"""设备授权登录流(RFC 8628 风格)与凭据缓存测试。"""
import json

import pytest
from fastapi.testclient import TestClient

from datafoundry.interface.credentials import clear_credentials, load_credentials, save_credentials
from datafoundry.service.server import create_app
from datafoundry.service.store import Store


@pytest.fixture()
def env(tmp_path):
    store = Store(tmp_path / "home")
    client = TestClient(create_app(store))
    client.post("/auth/bootstrap", json={"username": "root", "password": "rootpass123"})
    token = client.post("/auth/login", json={"username": "root", "password": "rootpass123"}).json()["token"]
    return client, store, token


def test_device_flow_happy_path(env):
    client, store, token = env
    grant = client.post("/auth/device/start", json={"label": "test-cli"}).json()
    assert grant["user_code"].count("-") == 1 and len(grant["user_code"]) == 9
    assert grant["device_code"].startswith("dfd_")
    assert grant["verification_uri_complete"].endswith(f"?code={grant['user_code']}")

    # 未授权时轮询 -> pending;立刻再轮询 -> slow_down
    r1 = client.post("/auth/device/token", json={"device_code": grant["device_code"]}).json()
    assert r1["status"] == "authorization_pending"
    r2 = client.post("/auth/device/token", json={"device_code": grant["device_code"]}).json()
    assert r2["status"] == "slow_down"

    # 已登录用户批准(user_code 大小写/无连字符都接受)
    lax_code = grant["user_code"].replace("-", "").lower()
    ok = client.post(
        "/auth/device/decide",
        json={"user_code": lax_code, "action": "approve"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert ok["status"] == "approved"

    # 轮询拿到一次性 API Key,该 Key 可用
    store._exec("UPDATE device_codes SET last_poll=NULL")  # 跳过 slow_down 间隔,免 sleep
    r3 = client.post("/auth/device/token", json={"device_code": grant["device_code"]}).json()
    assert r3["status"] == "approved" and r3["api_key"].startswith("dfk_")
    me = client.get("/auth/me", headers={"X-API-Key": r3["api_key"]}).json()
    assert me["username"] == "root"

    # 已领取后再轮询 -> claimed(不能重复领取)
    r4 = client.post("/auth/device/token", json={"device_code": grant["device_code"]}).json()
    assert r4["status"] == "claimed"


def test_device_flow_deny_expired_invalid(env):
    client, store, token = env
    # 拒绝
    g1 = client.post("/auth/device/start", json={}).json()
    client.post("/auth/device/decide", json={"user_code": g1["user_code"], "action": "deny"},
                headers={"Authorization": f"Bearer {token}"})
    store._exec("UPDATE device_codes SET last_poll=NULL")
    assert client.post("/auth/device/token", json={"device_code": g1["device_code"]}).json()["status"] == "denied"

    # 过期
    g2 = client.post("/auth/device/start", json={}).json()
    store._exec("UPDATE device_codes SET expires_at=0 WHERE user_code=?", (g2["user_code"],))
    assert client.post("/auth/device/token", json={"device_code": g2["device_code"]}).json()["status"] == "expired"
    r = client.post("/auth/device/decide", json={"user_code": g2["user_code"], "action": "approve"},
                    headers={"Authorization": f"Bearer {token}"}).json()
    assert r["status"] == "expired"

    # 无效 device_code / user_code
    assert client.post("/auth/device/token", json={"device_code": "dfd_nope"}).json()["status"] == "invalid"
    assert client.post("/auth/device/decide", json={"user_code": "XXXX-XXXX"},
                       headers={"Authorization": f"Bearer {token}"}).status_code == 404
    # 未认证不能批准
    assert client.post("/auth/device/decide", json={"user_code": "XXXX-XXXX"}).status_code == 401


def test_device_web_page_and_form(env):
    client, store, _ = env
    grant = client.post("/auth/device/start", json={}).json()

    page = client.get(f"/device?code={grant['user_code']}")
    assert page.status_code == 200 and grant["user_code"] in page.text

    # 错误口令 -> 页面报错且不批准
    bad = client.post("/device/decide",
                      content=f"user_code={grant['user_code']}&username=root&password=wrongpass&action=approve",
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert "用户名或口令错误" in bad.text

    # 正确口令 -> 批准成功
    good = client.post("/device/decide",
                       content=f"user_code={grant['user_code']}&username=root&password=rootpass123&action=approve",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert "已授权" in good.text
    store._exec("UPDATE device_codes SET last_poll=NULL")
    r = client.post("/auth/device/token", json={"device_code": grant["device_code"]}).json()
    assert r["status"] == "approved"


def test_credentials_file_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFOUNDRY_CONFIG_DIR", str(tmp_path / "cfg"))
    path = save_credentials("http://127.0.0.1:8321", "dfk_test", "root", "admin", 7)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    creds = load_credentials()
    assert creds["api_key"] == "dfk_test" and creds["key_id"] == 7

    # ApiClient 回退顺序:无环境变量时读凭据文件
    monkeypatch.delenv("DATAFOUNDRY_URL", raising=False)
    monkeypatch.delenv("DATAFOUNDRY_API_KEY", raising=False)
    from datafoundry.interface.mcp_server import ApiClient

    c = ApiClient()
    assert c.api_key == "dfk_test" and c.base_url == "http://127.0.0.1:8321"
    # 环境变量优先于凭据文件
    monkeypatch.setenv("DATAFOUNDRY_API_KEY", "dfk_env")
    assert ApiClient().api_key == "dfk_env"

    assert clear_credentials() is True
    assert load_credentials() is None
