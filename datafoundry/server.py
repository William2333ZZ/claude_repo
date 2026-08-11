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

import threading
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

import datafoundry.ops  # noqa: F401  导入即注册内置算子
from datafoundry import __version__
from datafoundry.engines import ENGINES, engine_status
from datafoundry.pipeline import estimate as pipeline_estimate
from datafoundry.pipeline import validate_steps
from datafoundry.registry import catalog
from datafoundry.runner import load_jsonl, run_pipeline
from datafoundry.security import create_token, parse_token, role_rank
from datafoundry.store import Store


class LoginBody(BaseModel):
    username: str
    password: str


class UserBody(BaseModel):
    username: str
    password: str = Field(min_length=8)
    role: str = "engineer"


class KeyBody(BaseModel):
    label: str = "default"


class EstimateBody(BaseModel):
    dataset_id: str
    steps: list[dict]


class RunBody(BaseModel):
    name: str = "run"
    dataset_id: str
    engine: str = "native"
    steps: list[dict] = []
    recipe: dict | None = None
    funnel: bool = True


def create_app(store: Store | None = None) -> FastAPI:
    store = store or Store()
    app = FastAPI(title="DataFoundry", version=__version__)
    app.state.store = store

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
    def login(body: LoginBody):
        user = store.authenticate(body.username, body.password)
        if not user:
            raise HTTPException(401, "用户名或口令错误")
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

    # ---------- 用户管理(admin) ----------

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

    @app.post("/pipelines/estimate")
    def estimate(body: EstimateBody, _: dict = Depends(require("viewer"))):
        ds = store.get_dataset(body.dataset_id)
        if not ds:
            raise HTTPException(404, "无此数据集")
        errors = validate_steps(body.steps)
        if errors:
            raise HTTPException(400, "; ".join(errors))
        return pipeline_estimate(body.steps, ds["n_samples"])

    # ---------- 运行 ----------

    def _execute(run_id: str, body: RunBody, ds: dict) -> None:
        out_dir = store.runs_dir / run_id
        try:
            if body.engine == "native":
                manifest = run_pipeline(ds["path"], body.steps, out_dir, funnel=body.funnel)
            else:
                manifest = ENGINES[body.engine].run(ds["path"], out_dir, body.recipe or {})
            store.set_run_status(run_id, "succeeded", manifest=manifest)
        except Exception as exc:  # 失败必须落库,不能让 run 卡在 running
            store.set_run_status(run_id, "failed", error=f"{type(exc).__name__}: {exc}")

    @app.post("/runs")
    def create_run(body: RunBody, user: dict = Depends(require("engineer"))):
        ds = store.get_dataset(body.dataset_id)
        if not ds:
            raise HTTPException(404, "无此数据集")
        if body.engine == "native":
            errors = validate_steps(body.steps)
            if errors:
                raise HTTPException(400, "; ".join(errors))
        elif body.engine in ENGINES:
            ok, reason = ENGINES[body.engine].check(body.recipe or {})
            if not ok:
                raise HTTPException(400, reason)
        else:
            raise HTTPException(400, f"未知引擎 {body.engine}(可用: native, {', '.join(ENGINES)})")
        run = store.create_run(body.name, body.dataset_id, body.steps, user["username"])
        store.audit(user["username"], "create_run", f"{run['id']} engine={body.engine} ds={body.dataset_id}")
        store.set_run_status(run["id"], "running")
        threading.Thread(target=_execute, args=(run["id"], body, ds), daemon=True).start()
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
