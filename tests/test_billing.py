"""计费模块测试:mock 网关全流程、幂等回调、额度扣减与拦截、Stripe 验签、默认关闭。"""
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from datafoundry.billing import MockProvider, StripeProvider
from datafoundry.server import create_app
from datafoundry.store import Store

JSONL = "\n".join(
    json.dumps({"text": f"这是第 {i} 条足够长的样本,用于计费额度扣减的端到端验证。"}, ensure_ascii=False)
    for i in range(3)
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFOUNDRY_BILLING_PROVIDER", "mock")
    monkeypatch.setenv("DATAFOUNDRY_BILLING_SECRET", "test-secret")
    client = TestClient(create_app(Store(tmp_path / "home")))
    client.post("/auth/bootstrap", json={"username": "root", "password": "rootpass123"})
    admin = client.post("/auth/login", json={"username": "root", "password": "rootpass123"}).json()["token"]
    client.post("/users", json={"username": "eng", "password": "engpass1234", "role": "engineer"},
                headers={"Authorization": f"Bearer {admin}"})
    eng = client.post("/auth/login", json={"username": "eng", "password": "engpass1234"}).json()["token"]
    return client, {"Authorization": f"Bearer {admin}"}, {"Authorization": f"Bearer {eng}"}


def mock_webhook(client, order_id):
    body = json.dumps({"order_id": order_id, "paid": True}).encode()
    sig = MockProvider().sign(body)
    return client.post("/billing/webhook", content=body, headers={"X-Mock-Signature": sig})


def test_billing_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("DATAFOUNDRY_BILLING_PROVIDER", raising=False)
    client = TestClient(create_app(Store(tmp_path / "home")))
    client.post("/auth/bootstrap", json={"username": "root", "password": "rootpass123"})
    token = client.post("/auth/login", json={"username": "root", "password": "rootpass123"}).json()["token"]
    r = client.get("/billing/plans", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404 and "计费未启用" in r.json()["detail"]


def test_order_webhook_credit_flow(env):
    client, admin, eng = env
    plans = client.get("/billing/plans", headers=eng).json()
    assert "starter" in plans

    order = client.post("/billing/orders?plan=starter", headers=eng).json()
    assert order["payment"]["pay_url"].startswith("mock://pay/")
    oid = order["order"]["id"]

    # 假签名拒收
    bad = client.post("/billing/webhook", content=b'{"order_id":"x","paid":true}',
                      headers={"X-Mock-Signature": "deadbeef"})
    assert bad.status_code == 400

    # 真回调入账;重复回调幂等
    assert mock_webhook(client, oid).json()["credited"] == 100_000
    assert "幂等" in mock_webhook(client, oid).json()["note"]

    me = client.get("/billing/me", headers=eng).json()
    assert me["balance"] == 100_000
    assert me["orders"][0]["status"] == "paid"


def test_run_deducts_and_blocks(env):
    client, admin, eng = env
    ds = client.post("/datasets/upload?name=b", content=JSONL, headers=eng).json()

    # 余额 0:非 admin 发起 run 被 402 拦截
    r = client.post("/runs", json={"dataset_id": ds["id"], "steps": [{"op": "quality_score"}]}, headers=eng)
    assert r.status_code == 402

    # admin 手工上账(线下收款路径)后可跑,且按样本数扣减
    client.post("/billing/grant?username=eng&credits=10&reason=poc", headers=admin)
    r = client.post("/runs", json={"dataset_id": ds["id"], "steps": [{"op": "quality_score"}]}, headers=eng)
    assert r.status_code == 200
    for _ in range(50):
        if client.get(f"/runs/{r.json()['id']}", headers=eng).json()["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    assert client.get("/billing/me", headers=eng).json()["balance"] == 10 - ds["n_samples"]

    # admin 自己不受额度约束
    r = client.post("/runs", json={"dataset_id": ds["id"], "steps": [{"op": "quality_score"}]}, headers=admin)
    assert r.status_code == 200


def test_stripe_signature_scheme(monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    p = StripeProvider()
    body = json.dumps({"type": "checkout.session.completed",
                       "data": {"object": {"metadata": {"order_id": "ord_1"}}}}).encode()
    t = str(int(time.time()))
    good = hmac.new(b"whsec_test", f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    assert p.parse_webhook({"stripe-signature": f"t={t},v1={good}"}, body) == {"order_id": "ord_1"}
    assert p.parse_webhook({"stripe-signature": f"t={t},v1={'0' * 64}"}, body) is None
    stale = str(int(time.time()) - 9999)
    stale_sig = hmac.new(b"whsec_test", f"{stale}.".encode() + body, hashlib.sha256).hexdigest()
    assert p.parse_webhook({"stripe-signature": f"t={stale},v1={stale_sig}"}, body) is None
