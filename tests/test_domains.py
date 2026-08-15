"""域与依赖方向守护([docs/21 §7] 域图的可执行形式)。

三域:
  kernel    精炼内核——schema/registry/pipeline/runner/recipes/matheq/audit/ops/engines
  service   服务壳——server/store/security/billing/ratelimit
  interface 薄客户端——cli/mcp_server/credentials(经 HTTP 窄腰访问服务,不 import 服务内部)

方向规则(顶层 import;函数内懒加载是可选依赖 sympy/psycopg 的合法通道,不受此限):
  kernel    → 只许 stdlib + kernel。零第三方依赖是可嵌入/端侧(#27 载体①)的结构保证,
              这里把它从"今天的事实"升级为"被守护的不变量"。
  interface → 只许 stdlib + kernel + interface(本地证据组装可用内核;服务只能走 HTTP)。
  service   → 可用第三方与 kernel,但不得 import interface(方向必须单向)。
新增模块必须入册其一,否则 test_all_modules_classified 响亮失败——参照 dsh
"缺服务=组装错误 fail loud"的先例,归类是设计动作,不是事后整理。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "datafoundry"

KERNEL = {"schema", "registry", "pipeline", "runner", "recipes", "matheq", "audit", "ops", "engines"}
SERVICE = {"server", "store", "security", "billing", "ratelimit"}
INTERFACE = {"cli", "mcp_server", "credentials"}

_STD = set(sys.stdlib_module_names) | {"__future__"}


def _files(members: set[str]):
    for m in sorted(members):
        p = PKG / f"{m}.py"
        if p.exists():
            yield m, p
        d = PKG / m
        if d.is_dir():
            yield from ((m, f) for f in sorted(d.glob("*.py")))


def _top_level_imports(path: Path):
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module


def _violations(members: set[str], allowed_df: set[str], allow_third_party: bool):
    bad = []
    for member, f in _files(members):
        for name in _top_level_imports(f):
            root = name.split(".")[0]
            if root == "datafoundry":
                seg = name.split(".")[1] if "." in name else ""
                if seg and seg not in allowed_df:
                    bad.append(f"{f.relative_to(PKG)}: import {name}(越域)")
            elif not allow_third_party and root not in _STD:
                bad.append(f"{f.relative_to(PKG)}: 顶层第三方依赖 {name}(应函数内懒加载)")
    return bad


def test_kernel_pure_stdlib_and_self():
    bad = _violations(KERNEL, allowed_df=KERNEL, allow_third_party=False)
    assert not bad, "内核域破坏零依赖/单向规则:\n" + "\n".join(bad)


def test_kernel_package_init_stays_clean():
    # from datafoundry.X import 会执行包 __init__:它一旦引服务域,内核零依赖即被静默击穿
    init = PKG / "__init__.py"
    bad = [n for n in _top_level_imports(init)
           if n.split(".")[0] == "datafoundry"
           and (n.split(".")[1] if "." in n else "") not in KERNEL
           or n.split(".")[0] not in _STD | {"datafoundry"}]
    assert not bad, f"包 __init__ 引入了非内核依赖: {bad}"


def test_interface_talks_http_not_internals():
    bad = _violations(INTERFACE, allowed_df=KERNEL | INTERFACE, allow_third_party=False)
    assert not bad, "接口域越过 HTTP 窄腰直引服务内部:\n" + "\n".join(bad)


def test_service_never_imports_interface():
    bad = _violations(SERVICE, allowed_df=KERNEL | SERVICE, allow_third_party=True)
    assert not bad, "服务域反向依赖接口域:\n" + "\n".join(bad)


def test_all_modules_classified():
    known = KERNEL | SERVICE | INTERFACE
    unplaced = [p.name for p in PKG.iterdir()
                if p.suffix == ".py" and p.stem != "__init__" and p.stem not in known
                or p.is_dir() and p.name not in known and (p / "__init__.py").exists()]
    assert not unplaced, f"新模块未入域册(在 tests/test_domains.py 归类后再合入): {unplaced}"


# ---- 限界上下文(DDD 维度,与上面的技术分层正交;正典见 docs/22)----
CONTEXTS = {
    "精炼": {"schema", "registry", "pipeline", "runner", "ops", "engines", "matheq"},
    "配方": {"recipes"},
    "证据与合规": {"audit"},
    "账务": {"billing"},
    "身份与门禁": {"security", "ratelimit"},
    "接入": {"cli", "mcp_server", "credentials"},
    "组装与存储": {"server", "store"},
}


def test_bounded_contexts_partition_all_modules():
    # 每个模块归属且仅归属一个业务上下文——归类是设计动作,新模块入册后方可合入
    seen: dict[str, str] = {}
    for ctx, members in CONTEXTS.items():
        for m in members:
            assert m not in seen, f"{m} 同时归属「{seen[m]}」与「{ctx}」"
            seen[m] = ctx
    layers = KERNEL | SERVICE | INTERFACE
    assert set(seen) == layers, f"上下文划分与模块清单不一致: {sorted(set(seen) ^ layers)}"
