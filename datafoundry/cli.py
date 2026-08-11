"""CLI 入口:serve / mcp / create-user / demo。"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import tempfile
from pathlib import Path

from datafoundry import __version__


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from datafoundry.server import create_app
    from datafoundry.store import Store

    app = create_app(Store(args.home))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def cmd_mcp(_: argparse.Namespace) -> int:
    from datafoundry.mcp_server import serve_stdio

    serve_stdio()
    return 0


def cmd_create_user(args: argparse.Namespace) -> int:
    from datafoundry.store import Store

    password = args.password or getpass.getpass("口令(≥8位): ")
    store = Store(args.home)
    user = store.create_user(args.username, password, args.role)
    store.audit("cli", "create_user", f"{args.username}:{args.role}")
    print(json.dumps(user, ensure_ascii=False))
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """离线演示:生成脏数据 → 漏斗流水线 → 打印 manifest 摘要。不起服务、不需要账号。"""
    import datafoundry.ops  # noqa: F401
    from datafoundry.pipeline import estimate
    from datafoundry.runner import run_pipeline

    demo_rows = [
        {"text": "机器学习中的过拟合指模型在训练集上表现好、在未见数据上表现差。常用对策包括正则化、早停与数据增广。" * 2},
        {"text": "机器学习中的过拟合指模型在训练集上表现好、在未见数据上表现差。常用对策包括正则化、早停与数据增广。" * 2},  # 精确重复
        {"text": "机器学习中的过拟合指模型在训练集上表现很好、在未见数据上表现差。常用对策包括正则化、早停与数据增广。" * 2},  # 近重复(单字差异)
        {"text": "买买买!!!点击 http://spam.example 立即抢购!!!$$$###@@@///\\\\|||~~~^^^&&&***((()))"},
        {"text": "太短"},
        {"text": "重复 重复 重复 重复 重复 重复 重复 重复 重复 重复 重复 重复 重复 重复 重复 重复 重复 重复"},
        {"text": "联系我: zhang.san@example.com 或 13812345678。这份规格说明覆盖了接口鉴权、错误码与重试策略,共三章。" * 2},
        {"text": "Transformer 的自注意力机制允许每个位置直接聚合序列中任意位置的信息,复杂度随序列长度平方增长。" * 3},
    ]
    base_steps = [
        {"op": "whitespace_normalize"},
        {"op": "length_filter", "params": {"min_len": 30}},
        {"op": "pii_redact"},
        {"op": "symbol_ratio_filter"},
        {"op": "repetition_filter"},
        {"op": "exact_dedup"},
        {"op": "minhash_dedup"},
        {"op": "quality_score"},
    ]
    # 预估始终带上 LLM 判审(只估成本不调用),展示漏斗编排把最贵的算子排到最后省了多少
    est_steps = [{"op": "llm_judge_filter"}] + base_steps
    steps = est_steps if args.with_llm else base_steps

    work = Path(args.out or tempfile.mkdtemp(prefix="datafoundry-demo-"))
    work.mkdir(parents=True, exist_ok=True)
    data = work / "demo_input.jsonl"
    data.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in demo_rows), encoding="utf-8")

    est = estimate(est_steps, 100_000)
    print("== 成本预估(按 10 万样本,含 llm_judge_filter,漏斗 vs 朴素) ==")
    print(json.dumps(
        {k: est[k] for k in ("funnel_order", "reorder_moves", "est_cost_naive", "est_cost_funnel", "est_savings")},
        ensure_ascii=False, indent=1))
    manifest = run_pipeline(data, steps, work / "run")
    print("\n== 运行结果 ==")
    for op in manifest["per_op"]:
        print(f"  {op['op']:<24} in={op['in']:>3} out={op['out']:>3} killed={op['killed']:>3} "
              f"retention={op['retention']:.2f}")
    print(f"\n总留存 {manifest['n_in']} -> {manifest['n_out']} ({manifest['retention']:.0%})")
    print(f"产物: {manifest['output']}\n死因: {manifest['rejects']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="datafoundry", description="DataFoundry 数据精炼平台")
    parser.add_argument("--version", action="version", version=f"datafoundry {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="启动 HTTP API 服务")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8321)
    p.add_argument("--home", default=None, help="数据目录(默认 $DATAFOUNDRY_HOME 或 ./.datafoundry)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("mcp", help="以 MCP stdio 服务运行(给 Claude Code 接入)")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("create-user", help="创建用户(直连本地库,用于引导)")
    p.add_argument("--username", required=True)
    p.add_argument("--password", default=None, help="不传则交互输入")
    p.add_argument("--role", default="engineer", choices=["viewer", "engineer", "admin"])
    p.add_argument("--home", default=None)
    p.set_defaults(func=cmd_create_user)

    p = sub.add_parser("demo", help="离线演示:脏数据 -> 漏斗流水线 -> 报告")
    p.add_argument("--out", default=None, help="输出目录(默认临时目录)")
    p.add_argument("--with-llm", action="store_true", help="包含 llm_judge_filter(需要 DATAFOUNDRY_LLM_* 配置)")
    p.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
