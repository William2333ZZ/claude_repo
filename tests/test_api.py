import json
import time

import pytest
from fastapi.testclient import TestClient

from datafoundry.service.server import create_app
from datafoundry.service.store import Store

JSONL = "\n".join(
    json.dumps(r, ensure_ascii=False)
    for r in [
        {"text": "这是一条足够长的正常样本,用来验证接口端到端的通过路径。" * 2},
        {"text": "这是一条足够长的正常样本,用来验证接口端到端的通过路径。" * 2},
        {"text": "太短"},
    ]
)
STEPS = [{"op": "length_filter", "params": {"min_len": 10}}, {"op": "exact_dedup"}]


@pytest.fixture()
def client(tmp_path):
    app = create_app(Store(tmp_path / "home"))
    return TestClient(app)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def bootstrap_admin(client):
    r = client.post("/auth/bootstrap", json={"username": "root", "password": "rootpass123"})
    assert r.status_code == 200
    r = client.post("/auth/login", json={"username": "root", "password": "rootpass123"})
    assert r.status_code == 200
    return r.json()["token"]


def test_bootstrap_only_once(client):
    bootstrap_admin(client)
    r = client.post("/auth/bootstrap", json={"username": "evil", "password": "evilpass123"})
    assert r.status_code == 403


def test_login_failures(client):
    bootstrap_admin(client)
    assert client.post("/auth/login", json={"username": "root", "password": "wrongpass"}).status_code == 401
    assert client.post("/auth/login", json={"username": "ghost", "password": "whatever12"}).status_code == 401


def test_unauthenticated_and_rbac(client):
    admin = bootstrap_admin(client)
    assert client.get("/datasets").status_code == 401  # 未认证

    r = client.post("/users", json={"username": "bob", "password": "bobpass123", "role": "viewer"}, headers=auth(admin))
    assert r.status_code == 200
    viewer = client.post("/auth/login", json={"username": "bob", "password": "bobpass123"}).json()["token"]

    # viewer 只读:能看目录,不能上传/建用户
    assert client.get("/ops", headers=auth(viewer)).status_code == 200
    assert client.post("/datasets/upload?name=x", content=JSONL, headers=auth(viewer)).status_code == 403
    assert (
        client.post("/users", json={"username": "c", "password": "ccpass1234", "role": "viewer"}, headers=auth(viewer)).status_code
        == 403
    )


def test_api_key_flow(client):
    admin = bootstrap_admin(client)
    key = client.post("/auth/keys", json={"label": "mcp"}, headers=auth(admin)).json()
    assert key["key"].startswith("dfk_")

    me = client.get("/auth/me", headers={"X-API-Key": key["key"]})
    assert me.status_code == 200 and me.json()["username"] == "root"

    assert client.delete(f"/auth/keys/{key['id']}", headers=auth(admin)).status_code == 200
    assert client.get("/auth/me", headers={"X-API-Key": key["key"]}).status_code == 401  # 吊销后失效


def test_dataset_pipeline_run_end_to_end(client):
    admin = bootstrap_admin(client)
    ds = client.post("/datasets/upload?name=demo", content=JSONL, headers=auth(admin)).json()
    assert ds["n_samples"] == 3

    head = client.get(f"/datasets/{ds['id']}/head?n=2", headers=auth(admin)).json()
    assert len(head["head"]) == 2

    est = client.post(
        "/pipelines/estimate", json={"dataset_id": ds["id"], "steps": STEPS}, headers=auth(admin)
    ).json()
    assert est["n_samples"] == 3 and est["funnel_order"]

    run = client.post(
        "/runs", json={"name": "t", "dataset_id": ds["id"], "steps": STEPS}, headers=auth(admin)
    ).json()
    for _ in range(50):
        info = client.get(f"/runs/{run['id']}", headers=auth(admin)).json()
        if info["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    assert info["status"] == "succeeded", info.get("error")
    assert info["manifest"]["n_in"] == 3 and info["manifest"]["n_out"] == 1

    rejects = client.get(f"/runs/{run['id']}/rejects", headers=auth(admin)).json()["rejects"]
    assert len(rejects) == 2 and all(r["trace"][-1]["action"] == "kill" for r in rejects)


def test_run_validation_errors(client):
    admin = bootstrap_admin(client)
    ds = client.post("/datasets/upload?name=d2", content=JSONL, headers=auth(admin)).json()
    r = client.post("/runs", json={"dataset_id": ds["id"], "steps": [{"op": "nope"}]}, headers=auth(admin))
    assert r.status_code == 400
    r = client.post("/runs", json={"dataset_id": "ds_missing", "steps": STEPS}, headers=auth(admin))
    assert r.status_code == 404
    # datajuicer 引擎未安装时给出明确指引
    r = client.post(
        "/runs",
        json={"dataset_id": ds["id"], "engine": "datajuicer", "recipe": {"process": [{"language_id_score_filter": {}}]}},
        headers=auth(admin),
    )
    assert r.status_code == 400 and "py-data-juicer" in r.json()["detail"]


def test_health_engine_status(client):
    r = client.get("/health").json()
    assert r["ok"] and r["engines"]["native"]["available"]


def test_env_bootstrap_admin(tmp_path, monkeypatch):
    from datafoundry.service.server import create_app
    from datafoundry.service.store import Store

    monkeypatch.setenv("DATAFOUNDRY_BOOTSTRAP_ADMIN", "opsadmin:opspass123")
    c = TestClient(create_app(Store(tmp_path / "boot-home")))
    login = c.post("/auth/login", json={"username": "opsadmin", "password": "opspass123"})
    assert login.status_code == 200 and login.json()["role"] == "admin"
    # 已有用户后,公网 bootstrap 端点应已关闭
    assert c.post("/auth/bootstrap", json={"username": "evil", "password": "evilpass123"}).status_code == 403
    # 二次启动(库已有用户)不重复创建、不报错
    c2 = TestClient(create_app(Store(tmp_path / "boot-home")))
    assert c2.post("/auth/login", json={"username": "opsadmin", "password": "opspass123"}).status_code == 200
