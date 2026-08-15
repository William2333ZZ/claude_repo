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

    from datafoundry.service.server import create_app
    from datafoundry.service.store import Store

    app = create_app(Store(args.home))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def cmd_mcp(_: argparse.Namespace) -> int:
    from datafoundry.interface.mcp_server import serve_stdio

    serve_stdio()
    return 0


def cmd_create_user(args: argparse.Namespace) -> int:
    from datafoundry.service.store import Store

    password = args.password or getpass.getpass("口令(≥8位): ")
    store = Store(args.home)
    user = store.create_user(args.username, password, args.role)
    store.audit("cli", "create_user", f"{args.username}:{args.role}")
    print(json.dumps(user, ensure_ascii=False))
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    """设备授权登录(RFC 8628,同 feishu-cli / TapTap 模式);--password 为直连口令登录。"""
    import time
    import webbrowser

    from datafoundry.interface.credentials import credentials_path, save_credentials
    from datafoundry.interface.mcp_server import ApiClient

    client = ApiClient(base_url=args.url)

    def emit(event: dict) -> None:
        if args.json:
            print(json.dumps(event, ensure_ascii=False))

    if args.password:  # 直连口令登录 -> 签发 API Key
        username = args.username or input("用户名: ")
        password = getpass.getpass("口令: ")
        login = client.call("POST", "/auth/login", {"username": username, "password": password})
        authed = ApiClient(base_url=client.base_url)
        authed.api_key = ""  # 用 Bearer 走一次发 Key
        import urllib.request as _ur

        req = _ur.Request(
            f"{client.base_url}/auth/keys",
            data=json.dumps({"label": args.label}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {login['token']}"},
            method="POST",
        )
        with _ur.urlopen(req, timeout=30) as resp:
            key = json.loads(resp.read())
        path = save_credentials(client.base_url, key["key"], username, login["role"], key["id"])
        print(f"已登录 {username}({login['role']}),凭据保存至 {path}")
        return 0

    if args.device_code:  # 两段式第二步:携带已有 device_code 继续轮询
        grant = {"device_code": args.device_code, "interval": 3, "expires_in": 600}
    else:
        grant = client.call("POST", "/auth/device/start", {"label": args.label})
        emit({"event": "device_start", **{k: grant[k] for k in ("device_code", "user_code", "expires_in")},
              "verification_uri_complete": grant["verification_uri_complete"]})
        if not args.json:
            print("在任意设备的浏览器打开并完成授权:")
            print(f"  {grant['verification_uri_complete']}")
            print(f"  授权码: {grant['user_code']}(页面已预填,核对即可)")
        try:
            webbrowser.open(grant["verification_uri_complete"])
        except Exception:
            pass  # SSH/无图形环境静默失败,链接已打印
        if args.no_wait:
            if not args.json:
                print(f"\n--no-wait:稍后用以下命令续轮询\n  datafoundry login --device-code {grant['device_code']}")
            return 0

    interval = float(grant.get("interval", 3))
    deadline = time.time() + float(grant.get("expires_in", 600))
    if not args.json:
        print("等待授权中...(Ctrl-C 取消)")
    while time.time() < deadline:
        time.sleep(interval)
        result = client.call("POST", "/auth/device/token", {"device_code": grant["device_code"]})
        status = result["status"]
        emit({"event": status})
        if status == "authorization_pending":
            continue
        if status == "slow_down":
            interval += 2
            continue
        if status == "approved":
            path = save_credentials(client.base_url, result["api_key"], result["username"],
                                    result["role"], result.get("key_id"))
            emit({"event": "saved", "path": str(path)})
            if not args.json:
                print(f"已登录 {result['username']}({result['role']}),凭据保存至 {credentials_path()}")
            return 0
        print(f"登录失败: {status}", file=sys.stderr)
        return 1
    print("登录超时:授权码已过期,请重新 datafoundry login", file=sys.stderr)
    return 1


def cmd_whoami(_: argparse.Namespace) -> int:
    from datafoundry.interface.mcp_server import ApiClient

    me = ApiClient().call("GET", "/auth/me")
    print(json.dumps(me, ensure_ascii=False))
    return 0


def cmd_logout(_: argparse.Namespace) -> int:
    from datafoundry.interface.credentials import clear_credentials, load_credentials
    from datafoundry.interface.mcp_server import ApiClient

    creds = load_credentials()
    if creds and creds.get("key_id") is not None:
        try:  # 尽力吊销服务端 Key;失败不阻塞本地登出
            ApiClient().call("DELETE", f"/auth/keys/{creds['key_id']}")
            print(f"已吊销服务端 API Key(id={creds['key_id']})")
        except Exception as exc:
            print(f"服务端吊销失败(已忽略): {exc}", file=sys.stderr)
    print("已清除本地凭据" if clear_credentials() else "本地没有凭据")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """离线演示:生成脏数据 → 漏斗流水线 → 打印 manifest 摘要。不起服务、不需要账号。"""
    import datafoundry.kernel.ops  # noqa: F401
    from datafoundry.kernel.pipeline import estimate
    from datafoundry.kernel.runner import run_pipeline

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


def cmd_recipes(args: argparse.Namespace) -> int:
    """列出命名配方;带名字则显示完整定义(steps + 证据出处)。"""
    import datafoundry.kernel.ops  # noqa: F401
    from datafoundry.kernel.recipes import get_recipe, list_recipes, missing_requirements

    if args.name and getattr(args, "history", False):
        # [M2-F2] 效果档案在平台侧(runs 存在 store):经已登录凭据查 API
        from datafoundry.interface.credentials import load_credentials

        creds = load_credentials()
        if not creds:
            print("查看效果档案需先登录平台: datafoundry login <url>", file=sys.stderr)
            return 1
        import urllib.request

        req = urllib.request.Request(
            f"{creds['url'].rstrip('/')}/recipes/{args.name}/history",
            headers={"X-API-Key": creds["api_key"]},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except Exception as exc:
            print(f"查询失败: {exc}", file=sys.stderr)
            return 1
        print(f"== 配方 {data['recipe']} 效果档案(当前 hash {data['current_hash']})==")
        for r in data["runs"]:
            ev = f" 评测 {r['eval_accuracy']}({r['evalset']})" if r.get("eval_accuracy") is not None else ""
            print(f"{r['run_id']}  {r['status']:<9} 数据集 {r['dataset_id']}  "
                  f"{r['n_in']}→{r['n_out']} 成本 {r['est_cost_total']} hash {r['recipe_hash']}{ev}")
        if not data["runs"]:
            print("(暂无历史 run)")
        return 0
    if args.name:
        try:
            recipe = get_recipe(args.name)
        except KeyError as exc:
            print(exc.args[0], file=sys.stderr)
            return 1
        recipe["missing_requirements"] = missing_requirements(args.name)
        print(json.dumps(recipe, ensure_ascii=False, indent=2))
        return 0
    for r in list_recipes():
        req = f" [需 {','.join(r['requires'])}]" if r["requires"] else ""
        print(f"{r['name']:<20} v{r['version']}  {r['title']}{req}")
        print(f"{'':<20} 场景: {r['scenario']}")
        print(f"{'':<20} 组合: {' -> '.join(r['ops'])}")
    return 0


def cmd_refine(args: argparse.Namespace) -> int:
    """一条命令跑完一个配方:估成本 -> 漏斗执行 -> 报告。离线,不起服务不需账号。"""
    import datafoundry.kernel.ops  # noqa: F401
    from datafoundry.kernel.pipeline import estimate
    from datafoundry.kernel.recipes import get_recipe, missing_requirements, recipe_steps
    from datafoundry.kernel.runner import run_pipeline

    try:
        recipe = get_recipe(args.recipe)
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        return 1
    missing = missing_requirements(args.recipe)
    if missing:
        print(f"配方前置未满足: {missing}(llm 需配置 DATAFOUNDRY_LLM_BASE/KEY/MODEL)", file=sys.stderr)
        return 1
    src = Path(args.input)
    if not src.exists():
        print(f"输入不存在: {src}", file=sys.stderr)
        return 1
    steps = recipe_steps(args.recipe)
    n_lines = sum(1 for line in src.open(encoding="utf-8") if line.strip())
    est = estimate(steps, n_lines)
    print(f"== 配方 {args.recipe} v{recipe['version']}:{recipe['title']} ==")
    print(f"输入 {n_lines} 行;漏斗顺序: {' -> '.join(est['funnel_order'])}")
    print(f"成本预估: 朴素 {est['est_cost_naive']} vs 漏斗 {est['est_cost_funnel']}(省 {est['est_savings']})")

    out_dir = Path(args.out or tempfile.mkdtemp(prefix=f"datafoundry-{args.recipe}-"))
    manifest = run_pipeline(src, steps, out_dir, text_key=args.text_key, funnel=not args.no_funnel)
    print("\n== 运行结果 ==")
    for op in manifest["per_op"]:
        print(f"  {op['op']:<24} in={op['in']:>5} out={op['out']:>5} killed={op['killed']:>5} "
              f"retention={op['retention']:.2f}")
    print(f"\n总留存 {manifest['n_in']} -> {manifest['n_out']} ({manifest['retention']:.0%})")
    print(f"产物: {manifest['output']}\n死因: {manifest['rejects']}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    """把一次运行组装为监管口径审计报告(TC260 / EU AI Act Art.10)。"""
    from datafoundry.kernel.audit import STANDARDS, write_report

    run_dir = Path(args.run)
    if not (run_dir / "manifest.json").exists():
        print(f"{run_dir} 下没有 manifest.json(需要 refine/run 的输出目录)", file=sys.stderr)
        return 1
    try:
        path = write_report(run_dir, standard=args.standard, sample_n=args.sample_n, seed=args.seed)
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        return 1
    text = path.read_text(encoding="utf-8")
    verdict = "通过" if "→ **通过**" in text else "不通过"
    print(f"审计报告({STANDARDS[args.standard]['title']}):{path}")
    print(f"抽样核查判定:{verdict}(细节见报告 §4;代理核查,非内容安全认证)")
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

    p = sub.add_parser("login", help="登录:默认设备授权流(浏览器确认),--password 为口令直连")
    p.add_argument("--url", default=None, help="服务地址(默认 $DATAFOUNDRY_URL 或凭据文件或 127.0.0.1:8321)")
    p.add_argument("--label", default="cli", help="API Key 标签")
    p.add_argument("--no-wait", action="store_true", help="两段式:拿到 device_code 立即返回,不轮询(Agent 场景)")
    p.add_argument("--device-code", default=None, help="两段式:携带已有 device_code 续轮询")
    p.add_argument("--password", action="store_true", help="口令直连登录(不走浏览器)")
    p.add_argument("--username", default=None)
    p.add_argument("--json", action="store_true", help="事件流 JSON 输出(Agent 场景)")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("whoami", help="查看当前登录身份")
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser("logout", help="吊销服务端 Key 并清除本地凭据")
    p.set_defaults(func=cmd_logout)

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

    p = sub.add_parser("recipes", help="列出命名配方(简单算子的组合+实验证据);带名字看完整定义")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--history", action="store_true", help="查看该配方的跨数据集效果档案(需已登录平台)")
    p.set_defaults(func=cmd_recipes)

    p = sub.add_parser("refine", help="一条命令跑配方:JSONL 进 -> 估成本 -> 漏斗执行 -> 报告(离线)")
    p.add_argument("--recipe", required=True, help="配方名(见 datafoundry recipes)")
    p.add_argument("--in", dest="input", required=True, help="输入 JSONL")
    p.add_argument("--out", default=None, help="输出目录(默认临时目录)")
    p.add_argument("--text-key", default="text", help="文本字段名(默认 text)")
    p.add_argument("--no-funnel", action="store_true", help="按声明顺序执行,不做漏斗重排")
    p.set_defaults(func=cmd_refine)

    p = sub.add_parser("audit", help="合规证据包:运行目录 -> 监管口径审计报告(TC260/EU Art.10)")
    p.add_argument("--run", required=True, help="refine/run 的输出目录(含 manifest.json)")
    p.add_argument("--standard", default="tc260", choices=["tc260", "euai10"])
    p.add_argument("--sample-n", type=int, default=400, help="抽样条数(TC260 条款口径为 4000)")
    p.add_argument("--seed", type=int, default=20260813, help="抽样种子(固定可复现)")
    p.set_defaults(func=cmd_audit)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
