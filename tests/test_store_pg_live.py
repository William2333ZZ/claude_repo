"""[P0-1] 账本外置:真实 PostgreSQL 全链路测试(资金路径优先)。

只在 DATAFOUNDRY_PG_TEST_URL 存在时运行(CI 的 postgres 服务容器,见
.github/workflows/tests.yml);本地无 PG 时整文件跳过。测试自带建/清表。
"""
import os

import pytest

PG_URL = os.environ.get("DATAFOUNDRY_PG_TEST_URL", "")
pytestmark = pytest.mark.skipif(not PG_URL, reason="需要 DATAFOUNDRY_PG_TEST_URL(CI postgres 容器)")

TABLES = ["credit_ledger", "orders", "device_codes", "api_keys", "runs", "datasets", "audit", "users"]


@pytest.fixture()
def store(tmp_path):
    from datafoundry.service.store import Store

    st = Store(home=tmp_path, db_url=PG_URL)
    # 清桌:保证每个测试从空库开始(顺序按外键依赖倒序)
    for t in TABLES:
        st._db.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    return Store(home=tmp_path, db_url=PG_URL)


def test_dialect_and_schema(store):
    assert store.dialect == "postgres"
    assert store.count_users() == 0


def test_user_roundtrip_and_duplicate(store):
    u = store.create_user("alice", "password8", "admin")
    assert u["id"] >= 1 and u["role"] == "admin"
    assert store.authenticate("alice", "password8")["username"] == "alice"
    assert store.authenticate("alice", "wrong-pass") is None
    with pytest.raises(ValueError):  # 唯一约束 → IntegrityError → ValueError(与 SQLite 行为一致)
        store.create_user("alice", "password8", "user")
    # IntegrityError 之后连接必须仍可用(pg 事务中断恢复)
    assert store.get_user(u["id"])["username"] == "alice"


def test_api_key_lifecycle(store):
    u = store.create_user("bob", "password8", "user")
    k = store.create_api_key(u["id"], "laptop")
    assert store.resolve_api_key(k["key"])["username"] == "bob"
    assert store.revoke_api_key(u["id"], k["id"]) is True
    assert store.resolve_api_key(k["key"]) is None


def test_ledger_money_path_idempotent(store):
    """资金路径核心:下单 → 标记支付(同事务记账)→ 重复回调不重复记账。"""
    u = store.create_user("carol", "password8", "user")
    plan = {"credits": 500, "amount_cents": 9900, "currency": "CNY"}
    order = store.create_order(u["id"], "basic", plan, "mock")
    assert order["status"] == "pending"
    paid = store.mark_order_paid(order["id"])
    assert paid is not None and paid["credits"] == 500
    assert store.mark_order_paid(order["id"]) is None  # 幂等:第二次回调无效
    assert store.credit_balance(u["id"]) == 500  # 只记了一次账
    store.add_credits(u["id"], -30, "run:consume")
    assert store.credit_balance(u["id"]) == 470
    assert store.list_orders(u["id"])[0]["status"] == "paid"


def test_device_flow_on_pg(store):
    u = store.create_user("dave", "password8", "user")
    dev = store.device_start("cli-test")
    assert store.device_decide(dev["user_code"], u["id"], approve=True) == "approved"
    poll = store.device_poll(dev["device_code"])
    assert poll["status"] == "approved" and poll["username"] == "dave"
    assert store.device_poll(dev["device_code"])["status"] == "claimed"  # 一次性发放


def test_runs_and_datasets_on_pg(store):
    ds = store.register_dataset("corpus", "/tmp/x.jsonl", 100, "tester")
    run = store.create_run("clean", ds["id"], [{"op": "exact_dedup"}], "tester")
    store.set_run_status(run["id"], "succeeded", manifest={"kept": 90})
    assert store.set_run_eval(run["id"], {"accuracy": 61.7}) is True
    got = store.get_run(run["id"])
    assert got["status"] == "succeeded"
    assert got["manifest"]["eval"]["accuracy"] == 61.7
    assert got["steps"][0]["op"] == "exact_dedup"
