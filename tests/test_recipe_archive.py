"""[M2-F2] 配方版本化与效果档案测试(#16):hash 稳定性、档案查询、API/MCP 面。"""
import json

import pytest
from fastapi.testclient import TestClient

import datafoundry.ops  # noqa: F401
from datafoundry.recipes import RECIPES, get_recipe, list_recipes, recipe_hash


def test_hash_param_order_insensitive(monkeypatch):
    a = {"version": 1, "title": "t", "scenario": "s", "requires": [],
         "steps": [{"op": "length_filter", "params": {"min_len": 5, "max_len": 100}}], "provenance": "p"}
    b = json.loads(json.dumps(a))
    b["steps"][0]["params"] = {"max_len": 100, "min_len": 5}  # 参数序不同
    monkeypatch.setitem(RECIPES, "_ha", a)
    monkeypatch.setitem(RECIPES, "_hb", b)
    assert recipe_hash("_ha") == recipe_hash("_hb")
    b2 = json.loads(json.dumps(a))
    b2["steps"][0]["params"]["min_len"] = 6  # 内容变了
    monkeypatch.setitem(RECIPES, "_hc", b2)
    assert recipe_hash("_ha") != recipe_hash("_hc")


def test_hash_exposed_in_listing_and_detail():
    for r in list_recipes():
        assert len(r["hash"]) == 12
    name = list_recipes()[0]["name"]
    assert get_recipe(name)["hash"] == recipe_hash(name)


def test_hash_unknown_recipe_raises():
    with pytest.raises(KeyError):
        recipe_hash("no_such_recipe")


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFOUNDRY_HOME", str(tmp_path))
    monkeypatch.delenv("DATAFOUNDRY_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from datafoundry.server import create_app

    app = create_app()
    with TestClient(app) as c:
        c.post("/auth/bootstrap", json={"username": "root", "password": "password8"})
        token = c.post("/auth/login", json={"username": "root", "password": "password8"}).json()["token"]
        c.headers["Authorization"] = f"Bearer {token}"
        yield c, app.state.store


def test_history_across_datasets_with_eval(api):
    c, store = api
    name = "clean_basic_v1"
    h = recipe_hash(name)
    ds1 = store.register_dataset("d1", "/tmp/d1.jsonl", 10, "root")
    ds2 = store.register_dataset("d2", "/tmp/d2.jsonl", 10, "root")
    r1 = store.create_run("r1", ds1["id"], [], "root")
    r2 = store.create_run("r2", ds2["id"], [], "root")
    store.set_run_status(r1["id"], "succeeded", manifest={
        "recipe_name": name, "recipe_hash": h, "n_in": 10, "n_out": 8,
        "retention": 0.8, "est_cost_total": 0.01})
    store.set_run_status(r2["id"], "succeeded", manifest={
        "recipe_name": name, "recipe_hash": "oldhash00001", "n_in": 10, "n_out": 7,
        "retention": 0.7, "est_cost_total": 0.02})
    store.set_run_eval(r1["id"], {"accuracy": 61.7, "evalset": "math_zh_v1"})

    rows = store.recipe_history(name)
    assert len(rows) == 2
    assert {r["dataset_id"] for r in rows} == {ds1["id"], ds2["id"]}  # 跨数据集
    by_run = {r["run_id"]: r for r in rows}
    assert by_run[r1["id"]]["eval_accuracy"] == 61.7  # 评测已并入档案
    assert by_run[r2["id"]]["recipe_hash"] == "oldhash00001"  # 历史 hash 保留(版本演化可见)

    resp = c.get(f"/recipes/{name}/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_hash"] == h and len(body["runs"]) == 2

    assert c.get("/recipes/no_such/history").status_code == 404


def test_mcp_has_recipe_history_tool():
    from datafoundry.mcp_server import TOOLS

    names = {t["name"] for t in TOOLS}
    assert "df_recipe_history" in names and len(names) == 10
