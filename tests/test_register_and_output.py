"""Skill 优先(docs/26)平台侧两端点:自助注册 + 产出下载——旅程 A 的第一幕与最后一幕。"""
import time

from fastapi.testclient import TestClient

from datafoundry.service.server import create_app
from datafoundry.service.store import Store

JSONL = '{"text": "这是一条足够长的合格样本,应当活着走出漏斗。"}\n{"text": "短"}\n'


def _app(tmp_path):
    return TestClient(create_app(Store(tmp_path / "home")))


def test_register_login_and_full_journey_to_delivery(tmp_path):
    client = _app(tmp_path)
    # 第一幕:陌生人自助注册(无任何凭据)
    r = client.post("/auth/register", json={"username": "stranger", "password": "goodpass123"})
    assert r.status_code == 200 and r.json()["role"] == "engineer"
    token = client.post("/auth/login", json={"username": "stranger", "password": "goodpass123"}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    # 中幕:上传 → 估算 → 跑(默认未开计费,不扣费)
    ds = client.post("/datasets/upload?name=j", content=JSONL, headers=h).json()
    est = client.post("/pipelines/estimate",
                      json={"dataset_id": ds["id"], "steps": [{"op": "length_filter", "params": {"min_len": 10}}]},
                      headers=h)
    assert est.status_code == 200 and "est_cost_funnel" in est.json()
    run = client.post("/runs", json={"name": "j1", "dataset_id": ds["id"],
                                     "steps": [{"op": "length_filter", "params": {"min_len": 10}}]},
                      headers=h).json()
    for _ in range(50):
        cur = client.get(f"/runs/{run['id']}", headers=h).json()
        if cur["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    assert cur["status"] == "succeeded"
    # 终幕:交付——幸存集可下载
    out = client.get(f"/runs/{run['id']}/output", headers=h)
    assert out.status_code == 200 and "合格样本" in out.text and "短" not in out.text


def test_register_duplicate_conflict_and_weak_password(tmp_path):
    client = _app(tmp_path)
    assert client.post("/auth/register", json={"username": "dup", "password": "goodpass123"}).status_code == 200
    assert client.post("/auth/register", json={"username": "dup", "password": "goodpass123"}).status_code == 409
    assert client.post("/auth/register", json={"username": "weak", "password": "short"}).status_code == 422


def test_register_can_be_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFOUNDRY_OPEN_SIGNUP", "0")
    client = _app(tmp_path)
    assert client.post("/auth/register", json={"username": "closeduser", "password": "goodpass123"}).status_code == 403


def test_output_missing_before_completion(tmp_path):
    client = _app(tmp_path)
    client.post("/auth/register", json={"username": "user2", "password": "goodpass123"})
    token = client.post("/auth/login", json={"username": "user2", "password": "goodpass123"}).json()["token"]
    assert client.get("/runs/run_none/output",
                      headers={"Authorization": f"Bearer {token}"}).status_code == 404
