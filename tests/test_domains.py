"""域与依赖方向守护([docs/21 §7] 域图的可执行形式;v0.2 拆包后为物理目录)。

三域(物理目录 = 发行边界):
  datafoundry/kernel/     精炼内核——零第三方依赖,`pip install datafoundry` 即得,可嵌入
  datafoundry/service/    服务壳——HTTP/存储/认证/计费/限流,依赖走 [server] 等 extras
  datafoundry/interface/  薄客户端——CLI/MCP,经 HTTP 窄腰访问服务,不 import 服务内部

方向规则(顶层 import;函数内懒加载是可选依赖 sympy/psycopg/uvicorn 的合法通道):
  kernel    → 只许 stdlib + kernel(零第三方是可嵌入/端侧 #27 的结构保证)
  interface → 只许 stdlib + kernel + interface(serve 子命令内的服务导入必须懒加载)
  service   → 可用第三方与 kernel,不得 import interface
顶层不再有任何代码文件(shim 兼容期已于 2026-08-15 结束:零外部用户,内部消费者全量迁移)。
新模块必须入域册与上下文册,否则响亮失败——归类是设计动作,不是事后整理。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "datafoundry"

KERNEL = {"schema", "registry", "pipeline", "runner", "recipes", "matheq", "audit", "ops", "engines"}
SERVICE = {"server", "store", "security", "billing", "ratelimit"}
INTERFACE = {"cli", "mcp_server", "credentials"}
DOMAIN_DIR = {"kernel": KERNEL, "service": SERVICE, "interface": INTERFACE}

_STD = set(sys.stdlib_module_names) | {"__future__"}


def _domain_files(domain: str):
    yield from sorted((PKG / domain).rglob("*.py"))


def _top_level_imports(path: Path):
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module


def _df_member(name: str) -> str:
    """datafoundry.kernel.runner → runner;datafoundry.kernel.runner(旧路径)→ runner。"""
    parts = name.split(".")
    if len(parts) >= 3 and parts[1] in DOMAIN_DIR:
        return parts[2]
    return parts[1] if len(parts) >= 2 else ""


def _violations(domain: str, allowed: set[str], allow_third_party: bool):
    bad = []
    for f in _domain_files(domain):
        for name in _top_level_imports(f):
            root = name.split(".")[0]
            if root == "datafoundry":
                member = _df_member(name)
                if member and member not in allowed:
                    bad.append(f"{f.relative_to(PKG)}: import {name}(越域)")
            elif not allow_third_party and root not in _STD:
                bad.append(f"{f.relative_to(PKG)}: 顶层第三方依赖 {name}(应函数内懒加载)")
    return bad


def test_kernel_pure_stdlib_and_self():
    bad = _violations("kernel", allowed=KERNEL, allow_third_party=False)
    assert not bad, "内核域破坏零依赖/单向规则:\n" + "\n".join(bad)


def test_interface_talks_http_not_internals():
    bad = _violations("interface", allowed=KERNEL | INTERFACE, allow_third_party=False)
    assert not bad, "接口域越过 HTTP 窄腰直引服务内部:\n" + "\n".join(bad)


def test_service_never_imports_interface():
    bad = _violations("service", allowed=KERNEL | SERVICE, allow_third_party=True)
    assert not bad, "服务域反向依赖接口域:\n" + "\n".join(bad)


def test_package_init_stays_kernel_clean():
    # `import datafoundry` 是内核用户的第一步:__init__ 一旦引服务域,零依赖安装即被击穿
    bad = [n for n in _top_level_imports(PKG / "__init__.py")
           if n.split(".")[0] not in _STD and n.split(".")[0] != "datafoundry"
           or n.split(".")[0] == "datafoundry" and _df_member(n) not in KERNEL | {""}]
    assert not bad, f"包 __init__ 引入了非内核依赖: {bad}"


def test_package_top_level_is_pure():
    # 顶层只许 __init__.py + 三个域目录——代码一律住进域里(委托人 2026-08-15「目录还有代码」整改)
    entries = {p.name for p in PKG.iterdir() if p.name != "__pycache__"}
    assert entries == {"__init__.py", "kernel", "service", "interface"}, \
        f"包顶层出现未归域条目: {sorted(entries - {'__init__.py', 'kernel', 'service', 'interface'})}"


def test_all_modules_classified():
    for domain, members in DOMAIN_DIR.items():
        actual = {p.stem if p.suffix == ".py" else p.name
                  for p in (PKG / domain).iterdir()
                  if (p.suffix == ".py" and p.stem != "__init__")
                  or (p.is_dir() and (p / "__init__.py").exists())}
        assert actual == members, f"{domain}/ 实际成员与域册不符: {sorted(actual ^ members)}"


# ---- 限界上下文(DDD 维度,与技术分层正交;正典见 docs/22)----
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
    seen: dict[str, str] = {}
    for ctx, members in CONTEXTS.items():
        for m in members:
            assert m not in seen, f"{m} 同时归属「{seen[m]}」与「{ctx}」"
            seen[m] = ctx
    layers = KERNEL | SERVICE | INTERFACE
    assert set(seen) == layers, f"上下文划分与模块清单不一致: {sorted(set(seen) ^ layers)}"
