"""只读控制台(#19,docs/26 只读证据页):HTTP Basic + 死因首屏 + 老板版文案。"""
import base64
import time

from fastapi.testclient import TestClient

from datafoundry.service.server import create_app
from datafoundry.service.store import Store

JSONL = '{"text": "这是一条足够长的合格样本,应当活着走出漏斗。"}\n{"text": "短"}\n'


def _basic(u, p):
    return {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()}


def _setup(tmp_path):
    client = TestClient(create_app(Store(tmp_path / "home")))
    client.post("/auth/register", json={"username": "boss", "password": "bosspass123"})
    token = client.post("/auth/login", json={"username": "boss", "password": "bosspass123"}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    ds = client.post("/datasets/upload?name=c", content=JSONL, headers=h).json()
    run = client.post("/runs", json={"name": "c1", "dataset_id": ds["id"],
                                     "steps": [{"op": "length_filter", "params": {"min_len": 10}}]},
                      headers=h).json()
    for _ in range(50):
        if client.get(f"/runs/{run['id']}", headers=h).json()["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    return client, run["id"]


def test_console_requires_basic_auth(tmp_path):
    client, _ = _setup(tmp_path)
    r = client.get("/console")
    assert r.status_code == 401 and "Basic" in r.headers.get("WWW-Authenticate", "")


def test_console_index_lists_runs(tmp_path):
    client, run_id = _setup(tmp_path)
    r = client.get("/console", headers=_basic("boss", "bosspass123"))
    assert r.status_code == 200 and run_id in r.text and "运行与数据集" in r.text


def test_run_page_shows_death_causes_in_boss_language(tmp_path):
    client, run_id = _setup(tmp_path)
    r = client.get(f"/console/runs/{run_id}", headers=_basic("boss", "bosspass123"))
    assert r.status_code == 200
    assert "可辩护的死因" in r.text          # 老板版头条
    assert "length_filter" in r.text          # 死因分布
    assert "太短或太长" in r.text             # 人话对照
    assert "被杀样本抽检" in r.text           # 逐条可辩护
    assert "<script" not in r.text.lower()    # 零 JS 纪律


def test_recipe_archive_page(tmp_path):
    client, _ = _setup(tmp_path)
    r = client.get("/console/recipes/math_zh_funnel_v1", headers=_basic("boss", "bosspass123"))
    assert r.status_code == 200 and "配方档案" in r.text
