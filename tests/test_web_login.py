"""看板免密直登(docs/28):harness 持存盘 key 换一次性链接,浏览器点开即入看板。

设备码流的镜像:设备码 = 浏览器给 agent 发 key;免密直登 = agent 给浏览器发会话。
安全线:裸 API key 永不进 URL;进 URL 的只是短 TTL、单次消费的一次性令牌。
"""
from fastapi.testclient import TestClient

from datafoundry.service.server import create_app
from datafoundry.service.store import Store


def _client_with_key(tmp_path):
    store = Store(tmp_path / "home")
    client = TestClient(create_app(store))
    client.post("/auth/register", json={"username": "walker", "password": "goodpass123"})
    token = client.post("/auth/login", json={"username": "walker", "password": "goodpass123"}).json()["token"]
    key = client.post("/auth/keys", json={"label": "harness"},
                      headers={"Authorization": f"Bearer {token}"}).json()["key"]
    return store, client, key


def test_handoff_key_to_console_session(tmp_path):
    store, client, key = _client_with_key(tmp_path)
    # harness 侧:用存盘 key 换一次性链接(key 走 header,不进 URL)
    r = client.post("/auth/web-login", headers={"X-API-Key": key})
    assert r.status_code == 200
    login_path = r.json()["login_path"]
    assert login_path.startswith("/auth/web?token=dfw_") and key not in login_path
    # 人侧:浏览器打开 → 303 到看板 + 会话 cookie
    r = client.get(login_path, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/console"
    assert "df_session" in r.cookies
    # 会话 cookie 直接可看看板(无 Basic)
    page = client.get("/console", cookies={"df_session": r.cookies["df_session"]})
    assert page.status_code == 200 and "运行与数据集" in page.text


def test_token_is_single_use(tmp_path):
    store, client, key = _client_with_key(tmp_path)
    login_path = client.post("/auth/web-login", headers={"X-API-Key": key}).json()["login_path"]
    assert client.get(login_path, follow_redirects=False).status_code == 303
    assert client.get(login_path, follow_redirects=False).status_code == 401  # 二次消费拒绝


def test_token_expiry_and_bad_token(tmp_path):
    store, client, key = _client_with_key(tmp_path)
    user = store.authenticate("walker", "goodpass123")
    expired = store.web_login_start(user["id"], ttl=-1)["token"]
    assert store.web_login_consume(expired) is None
    assert client.get("/auth/web?token=dfw_forged", follow_redirects=False).status_code == 401


def test_web_login_requires_auth(tmp_path):
    _, client, _ = _client_with_key(tmp_path)
    assert client.post("/auth/web-login").status_code == 401
