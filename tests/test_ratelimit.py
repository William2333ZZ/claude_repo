"""[M2-G1] 限流与登录锁定测试:单元(注入时钟)+ API 三态(锁定/限流/恢复)。"""
import pytest
from fastapi.testclient import TestClient

from datafoundry.service.ratelimit import LoginGuard, SlidingWindow


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---------- 单元:滑动窗口 ----------

def test_sliding_window_allows_then_blocks_then_slides():
    clk = Clock()
    win = SlidingWindow(limit=3, window=60.0, now=clk)
    assert [win.hit("k") for _ in range(3)] == [0.0, 0.0, 0.0]
    wait = win.hit("k")
    assert wait > 0  # 第 4 次被拒,给出等待秒数
    clk.t += 61  # 窗口滑过
    assert win.hit("k") == 0.0
    assert win.hit("other") == 0.0  # 分桶隔离


def test_sliding_window_disabled_when_zero():
    win = SlidingWindow(limit=0, now=Clock())
    assert all(win.hit("k") == 0.0 for _ in range(100))


# ---------- 单元:登录锁定 ----------

def test_login_guard_lock_and_expire():
    clk = Clock()
    g = LoginGuard(max_fails=3, cooldown=100.0, now=clk)
    assert g.fail("u") is False
    assert g.fail("u") is False
    assert g.fail("u") is True  # 第 3 次触发锁定
    assert g.locked_for("u") == pytest.approx(100.0)
    clk.t += 50
    assert g.locked_for("u") == pytest.approx(50.0)
    clk.t += 51  # 锁过期:清零重来
    assert g.locked_for("u") == 0.0
    assert g.fail("u") is False  # 计数已清


def test_login_guard_success_resets():
    g = LoginGuard(max_fails=3, cooldown=100.0, now=Clock())
    g.fail("u"), g.fail("u")
    g.ok("u")
    assert g.fail("u") is False  # 从头计数


# ---------- API 集成 ----------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFOUNDRY_HOME", str(tmp_path))
    monkeypatch.delenv("DATAFOUNDRY_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATAFOUNDRY_LOGIN_MAX_FAILS", "3")
    monkeypatch.setenv("DATAFOUNDRY_LOGIN_COOLDOWN", "60")
    from datafoundry.service.server import create_app

    app = create_app()
    with TestClient(app) as c:
        c.post("/auth/bootstrap", json={"username": "root", "password": "password8"})
        yield c


def test_lockout_rejects_even_correct_password(client):
    for _ in range(3):
        r = client.post("/auth/login", json={"username": "root", "password": "wrong-pass"})
        assert r.status_code == 401
    r = client.post("/auth/login", json={"username": "root", "password": "password8"})
    assert r.status_code == 423  # 锁定期内正确口令也拒绝
    assert "Retry-After" in r.headers


def test_lockout_scoped_per_username(client):
    for _ in range(3):
        client.post("/auth/login", json={"username": "root", "password": "wrong-pass"})
    # 另一个用户名不受影响(同 IP 只锁 (用户名, IP) 组合)
    r = client.post("/auth/login", json={"username": "someone", "password": "whatever8"})
    assert r.status_code == 401  # 正常的口令错误,而非 423


def test_rate_limit_429_with_retry_after(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFOUNDRY_HOME", str(tmp_path))
    monkeypatch.delenv("DATAFOUNDRY_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATAFOUNDRY_RATE_RPM", "5")
    from datafoundry.service.server import create_app

    with TestClient(create_app()) as c:
        codes = [c.get("/catalog").status_code for _ in range(6)]
        assert 429 in codes
        r = c.get("/catalog")
        assert r.status_code == 429
        assert int(r.headers["Retry-After"]) >= 1
        # 豁免路径不受限
        assert c.get("/health").status_code == 200
        assert c.post("/billing/webhook", content=b"x").status_code != 429
