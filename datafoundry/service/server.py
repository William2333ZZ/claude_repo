"""HTTP API:认证(登录 + API Key)、RBAC、数据集、流水线、运行、审计。

角色:
  viewer   只读(看目录/数据集/运行结果)
  engineer 上传数据、发起运行、管理自己的 API Key
  admin    以上 + 用户管理

认证方式:
  Authorization: Bearer <登录令牌>   (交互)
  X-API-Key: dfk_...                (程序化/MCP harness)

首次部署:库里没有任何用户时,POST /auth/bootstrap 可创建第一个 admin(仅此一次)。
"""
from __future__ import annotations

import os

import html
import threading
import urllib.parse
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import datafoundry.kernel.ops  # noqa: F401  导入即注册内置算子
from datafoundry import __version__
from datafoundry.service.billing import PLANS, billing_enabled, get_provider
from datafoundry.kernel.engines import ENGINES, engine_status
from datafoundry.kernel.pipeline import estimate as pipeline_estimate
from datafoundry.kernel.pipeline import validate_steps
from datafoundry.service.ratelimit import LoginGuard, SlidingWindow, client_key
from datafoundry.kernel.recipes import get_recipe, list_recipes, missing_requirements, recipe_hash, recipe_steps
from datafoundry.kernel.registry import catalog
from datafoundry.kernel.runner import load_jsonl, run_pipeline
from datafoundry.service.security import create_token, parse_token, role_rank
from datafoundry.service.store import Store


class LoginBody(BaseModel):
    username: str
    password: str


class UserBody(BaseModel):
    username: str
    password: str = Field(min_length=8)
    role: str = "engineer"


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")  # 与 store.isidentifier 规则一致
    password: str = Field(min_length=8)


class KeyBody(BaseModel):
    label: str = "default"


class EstimateBody(BaseModel):
    dataset_id: str
    steps: list[dict]


class EvalBody(BaseModel):
    accuracy: float
    evalset: str
    train_fingerprint: str = ""
    details: dict = {}


class DeviceStartBody(BaseModel):
    label: str = "cli"


class DeviceTokenBody(BaseModel):
    device_code: str


class DeviceDecideBody(BaseModel):
    user_code: str
    action: str = "approve"  # approve | deny


class RunBody(BaseModel):
    name: str = "run"
    dataset_id: str
    engine: str = "native"
    steps: list[dict] = []
    recipe: dict | None = None  # engine=datajuicer 的引擎配方(DJ process 列表)
    recipe_name: str | None = None  # native 引擎的命名配方(平台 recipes registry)
    funnel: bool = True


def create_app(store: Store | None = None) -> FastAPI:
    store = store or Store()
    app = FastAPI(title="DataFoundry", version=__version__)
    app.state.store = store

    # ---------- [M2-G1] 速率限制与登录锁定(docs/12 P1-1;边界见 ratelimit 模块注释) ----------
    import os as _os0

    limiter = SlidingWindow(limit=int(_os0.environ.get("DATAFOUNDRY_RATE_RPM", "240")))
    login_guard = LoginGuard(
        max_fails=int(_os0.environ.get("DATAFOUNDRY_LOGIN_MAX_FAILS", "5")),
        cooldown=float(_os0.environ.get("DATAFOUNDRY_LOGIN_COOLDOWN", "300")),
    )
    app.state.limiter, app.state.login_guard = limiter, login_guard
    _RATE_EXEMPT = ("/health", "/billing/webhook")  # 健康检查与支付回调不限流

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.url.path not in _RATE_EXEMPT:
            wait = limiter.hit(client_key(request))
            if wait > 0:
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    {"detail": "请求过于频繁,请稍后再试"},
                    status_code=429,
                    headers={"Retry-After": str(int(wait) + 1)},
                )
        return await call_next(request)

    # 容器部署引导:库为空且设置了 DATAFOUNDRY_BOOTSTRAP_ADMIN="用户名:口令" 时自动建 admin,
    # 避免公网实例的 /auth/bootstrap 被抢注。口令经 Space/容器 secret 注入,不落仓库。
    import os as _os

    _boot = _os.environ.get("DATAFOUNDRY_BOOTSTRAP_ADMIN", "")
    if _boot and ":" in _boot and store.count_users() == 0:
        _user, _pw = _boot.split(":", 1)
        try:
            store.create_user(_user, _pw, "admin")
            store.audit(_user, "bootstrap_admin", "via env DATAFOUNDRY_BOOTSTRAP_ADMIN")
        except ValueError as _exc:  # 用户名/口令不合规:启动继续,日志可见
            print(f"[datafoundry] 引导管理员失败: {_exc}")

    # ---------- 认证 ----------

    def current_user(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> dict:
        if x_api_key:
            user = store.resolve_api_key(x_api_key)
            if user:
                return user
            raise HTTPException(401, "API Key 无效或已吊销")
        if authorization and authorization.startswith("Bearer "):
            payload = parse_token(store.secret, authorization[7:])
            if payload:
                return {"id": payload["uid"], "username": payload["sub"], "role": payload["role"]}
            raise HTTPException(401, "令牌无效或已过期,请重新登录")
        raise HTTPException(401, "未认证:提供 Authorization: Bearer <token> 或 X-API-Key")

    def require(min_role: str):
        def dep(user: dict = Depends(current_user)) -> dict:
            if role_rank(user["role"]) < role_rank(min_role):
                raise HTTPException(403, f"需要 {min_role} 及以上角色(当前 {user['role']})")
            return user

        return dep

    # ---------- 健康与引导 ----------

    _LANDING = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DataFoundry 数据精炼平台</title><style>
:root{--bg:#F6F7F9;--card:#fff;--ink:#1B2430;--muted:#5C6672;--line:#E2E6EB;--accent:#6741D9}
@media (prefers-color-scheme:dark){:root{--bg:#12161C;--card:#1A202A;--ink:#E8ECF1;--muted:#98A2B0;--line:#2A323E;--accent:#9775FA}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;line-height:1.8}
.wrap{max-width:44rem;margin:0 auto;padding:4rem 1.5rem 5rem;text-align:center}
.logo{width:96px;height:96px;border-radius:22px;background:#1B2430;display:inline-flex;align-items:center;justify-content:center}
h1{font-size:2rem;margin:1.2rem 0 .4rem;font-weight:800}
.tag{color:var(--muted);font-size:1.05rem;margin:0 0 2.2rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.8rem;margin:0 0 2.4rem;text-align:left}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1rem 1.1rem}
.card b{display:block;margin-bottom:.2rem}.card span{font-size:.88rem;color:var(--muted)}
.cta{display:inline-block;margin:.3rem .4rem;padding:.6rem 1.4rem;border-radius:8px;text-decoration:none;font-size:.95rem}
.p{background:var(--accent);color:#fff}.s{border:1px solid var(--line);color:var(--ink)}
footer{margin-top:3rem;font-size:.8rem;color:var(--muted)}
</style></head><body><div class="wrap">
<div class="logo"><svg width="64" height="64" viewBox="0 0 1024 1024">
<g fill="#8A94A6"><rect x="330" y="168" width="52" height="52" rx="10" transform="rotate(-14 356 194)"/>
<circle cx="516" cy="152" r="27"/><path d="M668 150 L700 206 L636 206 Z"/></g>
<path d="M268 316 L756 316 L546 574 L546 668 L478 668 L478 574 Z" fill="#fff" stroke="#fff" stroke-width="26" stroke-linejoin="round"/>
<path d="M512 726 C542 766 574 800 574 836 A62 62 0 1 1 450 836 C450 800 482 766 512 726 Z" fill="#9775FA"/></svg></div>
<h1>DataFoundry 数据精炼平台</h1>
<p class="tag">你有一批要拿去训模型的数据,但不确定它值不值得训。把它交给我们:跑之前告诉你花多少、能剩多少;跑之后每条数据的去留都有理由——比你自己想的更周全,拿去交差更有底气。</p>
<div class="grid">
<div class="card"><b>先算账,再干活</b><span>上传即出成本与预期留存,不花冤枉钱;便宜检查先做,贵的 AI 判审只看幸存者</span></div>
<div class="card"><b>连它自己写错的算式都点名</b><span>验证器逐条复算数据里的每个断言,错的直接出示证据——实测公开数据集也照抓</span></div>
<div class="card"><b>能交差的报告</b><span>每条被删数据都有死因;合规审计报告(TC260 / EU AI Act)拿去向老板和监管交待</span></div>
<div class="card"><b>越用越聪明</b><span>配方档案记住"什么数据用什么方、效果几分",下次一键复用,不必重新踩坑</span></div>
</div>
<a class="cta p" href="/device">登录 / 设备授权</a>
<a class="cta s" href="/docs">API 文档</a>
<footer>按数据处理量计费 · 开源内核(Apache-2.0)· © 2026 DataFoundry</footer>
</div></body></html>"""

    from datafoundry.service.console import build_router  # [#19] 只读控制台(第四投影)
    app.include_router(build_router(store))

    @app.get("/", response_class=HTMLResponse)
    def landing():
        return _LANDING

    @app.get("/health")
    def health():
        return {"ok": True, "version": __version__, "engines": engine_status()}

    @app.post("/auth/bootstrap")
    def bootstrap(body: LoginBody):
        if store.count_users() > 0:
            raise HTTPException(403, "已初始化:请用现有 admin 创建用户")
        user = store.create_user(body.username, body.password, "admin")
        store.audit(body.username, "bootstrap_admin")
        return {"created": user, "note": "第一个 admin 已创建,请立即登录"}

    @app.post("/auth/login")
    def login(body: LoginBody, request: Request):
        guard_key = f"{body.username}|{client_key(request)}"
        locked = login_guard.locked_for(guard_key)
        if locked > 0:  # 锁定期内正确口令也拒绝
            raise HTTPException(
                423, f"登录已锁定,请 {int(locked) + 1} 秒后再试",
                headers={"Retry-After": str(int(locked) + 1)},
            )
        user = store.authenticate(body.username, body.password)
        if not user:
            just_locked = login_guard.fail(guard_key)
            store.audit(body.username, "login_failed", "locked" if just_locked else "")
            raise HTTPException(401, "用户名或口令错误")
        login_guard.ok(guard_key)
        store.audit(user["username"], "login")
        return {
            "token": create_token(store.secret, user["id"], user["username"], user["role"]),
            "role": user["role"],
        }

    @app.get("/auth/me")
    def me(user: dict = Depends(current_user)):
        return user

    @app.post("/auth/keys")
    def create_key(body: KeyBody, user: dict = Depends(require("engineer"))):
        key = store.create_api_key(user["id"], body.label)
        store.audit(user["username"], "create_api_key", body.label)
        return key

    @app.get("/auth/keys")
    def list_keys(user: dict = Depends(require("engineer"))):
        return store.list_api_keys(user["id"])

    @app.delete("/auth/keys/{key_id}")
    def revoke_key(key_id: int, user: dict = Depends(require("engineer"))):
        if not store.revoke_api_key(user["id"], key_id):
            raise HTTPException(404, "无此 Key 或不属于你")
        store.audit(user["username"], "revoke_api_key", str(key_id))
        return {"revoked": key_id}

    # ---------- 设备授权登录(RFC 8628 风格,同 feishu-cli / TapTap 模式) ----------

    @app.post("/auth/device/start")
    def device_start(body: DeviceStartBody, request: Request):
        grant = store.device_start(body.label)
        base = str(request.base_url).rstrip("/")
        store.audit("anonymous", "device_start", f"{grant['user_code']} label={body.label}")
        return {
            **grant,
            "verification_uri": f"{base}/device",
            "verification_uri_complete": f"{base}/device?code={urllib.parse.quote(grant['user_code'])}",
        }

    @app.post("/auth/device/token")
    def device_token(body: DeviceTokenBody):
        result = store.device_poll(body.device_code)
        if result["status"] == "approved":
            store.audit(result["username"], "device_claim", f"key_id={result['key_id']}")
        return result

    @app.post("/auth/device/decide")
    def device_decide(body: DeviceDecideBody, user: dict = Depends(current_user)):
        status = store.device_decide(body.user_code, user["id"], body.action == "approve")
        if status == "not_found":
            raise HTTPException(404, "无此授权码(user_code),请核对后重试")
        store.audit(user["username"], "device_decide", f"{body.user_code} -> {status}")
        return {"user_code": store.normalize_user_code(body.user_code), "status": status}

    _PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DataFoundry 设备授权</title><style>
body{{font-family:system-ui,sans-serif;background:#f6f7f9;color:#1b2430;display:flex;justify-content:center;padding:3rem 1rem}}
.card{{background:#fff;border:1px solid #e2e6eb;border-radius:10px;padding:2rem;max-width:22rem;width:100%}}
h1{{font-size:1.1rem;margin:0 0 1rem}}label{{display:block;font-size:.85rem;margin:.8rem 0 .25rem;color:#5c6672}}
input{{width:100%;box-sizing:border-box;padding:.5rem .6rem;border:1px solid #cfd6dd;border-radius:6px;font-size:1rem}}
input[name=user_code]{{font-family:ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase}}
.row{{display:flex;gap:.6rem;margin-top:1.2rem}}button{{flex:1;padding:.55rem;border-radius:6px;border:1px solid transparent;font-size:.95rem;cursor:pointer}}
.ok{{background:#1b2430;color:#fff}}.no{{background:#fff;border-color:#cfd6dd;color:#5c6672}}
.msg{{margin-top:1rem;font-size:.9rem}}.err{{color:#c92a2a}}.good{{color:#2b8a3e}}
</style></head><body><div class="card"><h1>DataFoundry 设备授权</h1>
<form method="post" action="/device/decide">
<label>授权码(终端里显示的 user_code)</label><input name="user_code" value="{code}" required>
<label>用户名</label><input name="username" autocomplete="username" required>
<label>口令</label><input name="password" type="password" autocomplete="current-password" required>
<div class="row"><button class="ok" name="action" value="approve">授权</button>
<button class="no" name="action" value="deny">拒绝</button></div></form>
<p class="msg {cls}">{msg}</p></div></body></html>"""

    @app.get("/device", response_class=HTMLResponse)
    def device_page(code: str = Query(default="")):
        return _PAGE.format(code=html.escape(code)[:20], cls="", msg="确认后回到终端即可。")

    @app.post("/device/decide", response_class=HTMLResponse)
    async def device_decide_web(request: Request):
        raw = (await request.body()).decode("utf-8")
        form = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
        user_code = html.escape(form.get("user_code", ""))[:20]
        user = store.authenticate(form.get("username", ""), form.get("password", ""))
        if not user:
            store.audit(form.get("username", "?")[:40], "device_decide_web_fail", user_code)
            return _PAGE.format(code=user_code, cls="err", msg="用户名或口令错误,请重试。")
        status = store.device_decide(form.get("user_code", ""), user["id"], form.get("action") == "approve")
        store.audit(user["username"], "device_decide", f"{user_code} -> {status}")
        if status == "approved":
            return _PAGE.format(code="", cls="good", msg=f"已授权({user['username']}/{user['role']})。回到终端即可,本页可关闭。")
        if status == "denied":
            return _PAGE.format(code="", cls="good", msg="已拒绝该授权请求。本页可关闭。")
        hints = {"not_found": "授权码不存在,请核对。", "expired": "授权码已过期,请在终端重新发起登录。"}
        return _PAGE.format(code=user_code, cls="err", msg=hints.get(status, f"当前状态: {status}"))

    # ---------- 用户管理(admin) ----------

    @app.post("/auth/register")
    def register(body: RegisterBody):
        # [docs/26 §3-A] Skill 优先:陌生人旅程第一幕。开放注册(env 可关),固定 engineer 角色,
        # 全局限流中间件覆盖;体验额度为 0——estimate 本免费,付费前已可见价值
        if os.environ.get("DATAFOUNDRY_OPEN_SIGNUP", "1").lower() not in ("1", "true", "yes"):
            raise HTTPException(403, "注册未开放:请联系管理员开通账户")
        try:
            user = store.create_user(body.username, body.password, "engineer")
        except ValueError as exc:  # store 统一以 ValueError 报重复/不合规
            raise HTTPException(409 if "已存在" in str(exc) else 400, str(exc)) from None
        store.audit(body.username, "register", "self-serve signup(engineer)")
        return {"username": user["username"], "role": user["role"],
                "next": "设备码登录:POST /auth/device/start,或 CLI `datafoundry login`"}

    @app.post("/users")
    def create_user(body: UserBody, admin: dict = Depends(require("admin"))):
        if body.role not in ("viewer", "engineer", "admin"):
            raise HTTPException(400, "角色须为 viewer/engineer/admin")
        try:
            user = store.create_user(body.username, body.password, body.role)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        store.audit(admin["username"], "create_user", f"{body.username}:{body.role}")
        return user

    @app.get("/users")
    def list_users(_: dict = Depends(require("admin"))):
        return store.list_users()

    # ---------- 数据集 ----------

    @app.post("/datasets/upload")
    def upload_dataset(
        name: str = Query(min_length=1, max_length=120),
        body: str = Body(media_type="text/plain"),
        user: dict = Depends(require("engineer")),
    ):
        path = store.uploads_dir / f"{name.replace('/', '_')}.jsonl"
        path.write_text(body, encoding="utf-8")
        try:
            n = len(load_jsonl(path))
        except ValueError as exc:
            path.unlink(missing_ok=True)
            raise HTTPException(400, str(exc)) from None
        if n == 0:
            path.unlink(missing_ok=True)
            raise HTTPException(400, "没有解析出任何样本:每行一个 JSON 对象,文本字段名默认 text")
        ds = store.register_dataset(name, str(path), n, user["username"])
        store.audit(user["username"], "upload_dataset", f"{ds['id']} n={n}")
        return ds

    @app.post("/datasets/register")
    def register_dataset(
        name: str = Query(min_length=1, max_length=120),
        path: str = Query(),
        user: dict = Depends(require("engineer")),
    ):
        p = Path(path)
        if not p.is_file():
            raise HTTPException(400, f"服务器上不存在该文件: {path}")
        try:
            n = len(load_jsonl(p))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        ds = store.register_dataset(name, str(p.resolve()), n, user["username"])
        store.audit(user["username"], "register_dataset", f"{ds['id']} {path}")
        return ds

    @app.get("/datasets")
    def list_datasets(_: dict = Depends(require("viewer"))):
        return store.list_datasets()

    @app.get("/datasets/{ds_id}/head")
    def dataset_head(ds_id: str, n: int = Query(default=5, ge=1, le=50), _: dict = Depends(require("viewer"))):
        ds = store.get_dataset(ds_id)
        if not ds:
            raise HTTPException(404, "无此数据集")
        samples = load_jsonl(ds["path"])[:n]
        return {"dataset": ds, "head": samples}

    # ---------- 算子与流水线 ----------

    @app.get("/ops")
    def list_ops(_: dict = Depends(require("viewer"))):
        return catalog()

    @app.get("/engines")
    def list_engines(_: dict = Depends(require("viewer"))):
        return engine_status()

    @app.get("/recipes")
    def recipes_index(_: dict = Depends(require("viewer"))):
        return list_recipes()

    @app.get("/recipes/{name}")
    def recipes_show(name: str, _: dict = Depends(require("viewer"))):
        try:
            return get_recipe(name)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0]))

    @app.get("/recipes/{name}/history")
    def recipes_history(name: str, _: dict = Depends(require("viewer"))):
        """[M2-F2] 配方效果档案:跨数据集的 (run, 计数, 成本, 评测) 历史,含 hash 演化。"""
        try:
            current = recipe_hash(name)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0]))
        return {"recipe": name, "current_hash": current, "runs": store.recipe_history(name)}

    @app.post("/pipelines/estimate")
    def estimate(body: EstimateBody, _: dict = Depends(require("viewer"))):
        ds = store.get_dataset(body.dataset_id)
        if not ds:
            raise HTTPException(404, "无此数据集")
        errors = validate_steps(body.steps)
        if errors:
            raise HTTPException(400, "; ".join(errors))
        return pipeline_estimate(body.steps, ds["n_samples"])

    # ---------- 计费(默认关闭:不设 DATAFOUNDRY_BILLING_PROVIDER 则平台行为不变) ----------

    def require_billing():
        if not billing_enabled():
            raise HTTPException(404, "计费未启用:设 DATAFOUNDRY_BILLING_PROVIDER=mock|stripe|wechatpay|alipay")

    @app.get("/billing/plans")
    def billing_plans(_: dict = Depends(require("viewer"))):
        require_billing()
        return PLANS

    @app.get("/billing/me")
    def billing_me(user: dict = Depends(require("viewer"))):
        require_billing()
        return {"balance": store.credit_balance(user["id"]), "orders": store.list_orders(user["id"])}

    @app.post("/billing/orders")
    def billing_create_order(plan: str = Query(), user: dict = Depends(require("engineer"))):
        require_billing()
        if plan not in PLANS:
            raise HTTPException(400, f"未知套餐 {plan}(可用: {sorted(PLANS)})")
        try:
            provider = get_provider()
        except ValueError as exc:
            raise HTTPException(500, str(exc)) from None
        order = store.create_order(user["id"], plan, PLANS[plan], provider.name)
        try:
            payment = provider.create_payment(order)
        except Exception as exc:  # 网关侧失败必须把原因透出(如支付宝"应用未上线"/密钥错误)
            store.audit(user["username"], "billing_order_fail", f"{order['id']} {exc!s:.200}")
            raise HTTPException(502, f"支付网关下单失败: {exc}") from None
        if payment.get("provider_ref"):
            store.set_order_ref(order["id"], payment["provider_ref"])
        store.audit(user["username"], "billing_order", f"{order['id']} {plan}")
        return {"order": order, "payment": payment}

    @app.post("/billing/webhook")
    async def billing_webhook(request: Request):
        require_billing()
        try:
            provider = get_provider()
        except ValueError as exc:
            raise HTTPException(500, str(exc)) from None
        body = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}
        event = provider.parse_webhook(headers, body)
        if not event:
            raise HTTPException(400, "回调验签失败或事件不相关")
        order = store.mark_order_paid(event["order_id"])  # 幂等
        if order:
            store.audit("webhook", "billing_paid", f"{order['id']} +{order['credits']} credits")
        if provider.ack_response is not None:  # 支付宝等网关要求明文应答,否则会重试通知
            from fastapi.responses import PlainTextResponse

            return PlainTextResponse(provider.ack_response)
        if order:
            return {"credited": order["credits"], "order_id": order["id"]}
        return {"note": "订单不存在或已处理(幂等)"}

    @app.post("/billing/grant")
    def billing_grant(
        username: str = Query(),
        credits: int = Query(gt=0),
        reason: str = Query(default="manual"),
        admin: dict = Depends(require("admin")),
    ):
        """线下收款(对公转账/PoC 合同)后的手工上账通道——B2B 现实路径。"""
        require_billing()
        target = next((u for u in store.list_users() if u["username"] == username), None)
        if not target:
            raise HTTPException(404, f"无此用户: {username}")
        store.add_credits(target["id"], credits, f"grant:{reason}")
        store.audit(admin["username"], "billing_grant", f"{username} +{credits} ({reason})")
        return {"username": username, "balance": store.credit_balance(target["id"])}

    # ---------- 运行 ----------

    def _execute(run_id: str, body: RunBody, ds: dict, refund_user_id: int | None = None) -> None:
        out_dir = store.runs_dir / run_id
        try:
            if body.engine == "native":
                extra = None
                if body.recipe_name:  # [M2-F2] 配方标识入 manifest:效果档案按 hash 聚合
                    extra = {"recipe_name": body.recipe_name, "recipe_hash": recipe_hash(body.recipe_name)}
                manifest = run_pipeline(ds["path"], body.steps, out_dir, funnel=body.funnel,
                                        manifest_extra=extra)
            else:
                manifest = ENGINES[body.engine].run(ds["path"], out_dir, body.recipe or {})
            store.set_run_status(run_id, "succeeded", manifest=manifest)
        except Exception as exc:  # 失败必须落库,不能让 run 卡在 running
            store.set_run_status(run_id, "failed", error=f"{type(exc).__name__}: {exc}")
            if refund_user_id is not None:  # [docs/24 Q5] 失败的精炼不是交付,不收钱:同额退回,账本留双向痕迹
                store.add_credits(refund_user_id, ds["n_samples"], f"refund:{run_id}")
                store.audit("system", "run_refund", f"{run_id} +{ds['n_samples']} credits")

    @app.post("/runs")
    def create_run(body: RunBody, user: dict = Depends(require("engineer"))):
        ds = store.get_dataset(body.dataset_id)
        if not ds:
            raise HTTPException(404, "无此数据集")
        if body.engine == "native":
            if body.recipe_name:  # 命名配方展开为 steps,血缘/计费/漏斗与手写 steps 完全同路
                if body.steps:
                    raise HTTPException(400, "steps 与 recipe_name 二选一")
                try:
                    missing = missing_requirements(body.recipe_name)
                    body.steps = recipe_steps(body.recipe_name)
                except KeyError as exc:
                    raise HTTPException(404, str(exc.args[0]))
                if missing:
                    raise HTTPException(400, f"配方前置未满足: {missing}(llm 需配置 DATAFOUNDRY_LLM_BASE/KEY/MODEL)")
            errors = validate_steps(body.steps)
            if errors:
                raise HTTPException(400, "; ".join(errors))
        elif body.engine in ENGINES:
            ok, reason = ENGINES[body.engine].check(body.recipe or {})
            if not ok:
                raise HTTPException(400, reason)
        else:
            raise HTTPException(400, f"未知引擎 {body.engine}(可用: native, {', '.join(ENGINES)})")
        if billing_enabled() and role_rank(user["role"]) < role_rank("admin"):
            balance = store.credit_balance(user["id"])
            if balance < ds["n_samples"]:
                raise HTTPException(
                    402, f"额度不足:本次需 {ds['n_samples']},余额 {balance}。请购买套餐或联系管理员上账"
                )
        run = store.create_run(body.name, body.dataset_id, body.steps, user["username"])
        charged = billing_enabled() and role_rank(user["role"]) < role_rank("admin")
        if charged:
            store.add_credits(user["id"], -ds["n_samples"], f"run:{run['id']}")
        store.audit(user["username"], "create_run", f"{run['id']} engine={body.engine} ds={body.dataset_id}")
        store.set_run_status(run["id"], "running")
        threading.Thread(target=_execute, args=(run["id"], body, ds, user["id"] if charged else None),
                         daemon=True).start()
        return {"id": run["id"], "status": "running"}

    @app.get("/runs")
    def list_runs(_: dict = Depends(require("viewer"))):
        return store.list_runs()

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, _: dict = Depends(require("viewer"))):
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(404, "无此运行")
        return run

    @app.post("/runs/{run_id}/eval")
    def set_run_eval(run_id: str, body: EvalBody, user: dict = Depends(require("engineer"))):
        block = {
            "accuracy": body.accuracy,
            "evalset": body.evalset,
            "train_fingerprint": body.train_fingerprint,
            "details": body.details,
            "recorded_by": user["username"],
        }
        if not store.set_run_eval(run_id, block):
            raise HTTPException(404, "无此运行")
        store.audit(user["username"], "set_run_eval", f"{run_id} {body.evalset}={body.accuracy}")
        return {"run_id": run_id, "eval": block}

    @app.get("/runs/{run_id}/output")
    def run_output(run_id: str, user: dict = Depends(require("engineer"))):
        # [docs/26 Skill 走查] 交付一步:幸存集下载——没有它,旅程 A 在最后一幕断掉
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(404, "无此运行")
        path = store.runs_dir / run_id / "output.jsonl"
        if not path.exists():
            raise HTTPException(404, "该运行暂无产出(未完成或已失败)")
        from fastapi.responses import FileResponse
        return FileResponse(path, media_type="application/jsonl",
                            filename=f"{run_id}_output.jsonl")

    @app.get("/runs/{run_id}/rejects")
    def run_rejects(run_id: str, n: int = Query(default=10, ge=1, le=100), _: dict = Depends(require("viewer"))):
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(404, "无此运行")
        path = store.runs_dir / run_id / "rejects.jsonl"
        if not path.exists():
            return {"rejects": []}
        import json as _json

        out = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if len(out) >= n:
                    break
                out.append(_json.loads(line))
        return {"rejects": out}

    return app
