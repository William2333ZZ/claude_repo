"""配方:注册表健康、组合执行正确性、CLI/API/MCP 四个面。"""
import json

import pytest
from fastapi.testclient import TestClient

import datafoundry.kernel.ops  # noqa: F401
from datafoundry.interface.cli import main as cli_main
from datafoundry.kernel.recipes import RECIPES, get_recipe, missing_requirements, recipe_steps, validate_all
from datafoundry.kernel.runner import run_pipeline
from datafoundry.service.server import create_app
from datafoundry.service.store import Store

MATH_ROWS = [
    # 干净样本:答案与 reference 一致,应存活
    {"text": "问题:小明有 3 个苹果,又买了 5 个,现在共有几个?解答:3+5=8,答案是 8。", "reference": "8"},
    # 错答样本:答案与 reference 不一致,应被 math_answer_verify 杀掉
    {"text": "问题:小红有 4 支笔,送出 1 支,还剩几支?解答:4-1=2,答案是 2。", "reference": "3"},
    # 精确重复:应被 exact_dedup 杀掉
    {"text": "问题:小明有 3 个苹果,又买了 5 个,现在共有几个?解答:3+5=8,答案是 8。", "reference": "8"},
    # 垃圾样本:应被启发式过滤杀掉
    {"text": "!!!买买买 $$$###@@@///\\\\|||~~~^^^&&&***((()))!!!", "reference": "1"},
]


def test_registry_all_valid(monkeypatch):
    # 前置未满足时:只允许 requires 非空的配方报"未配置"类错误
    monkeypatch.delenv("DATAFOUNDRY_LLM_BASE", raising=False)
    for name, errs in validate_all().items():
        if not RECIPES[name]["requires"]:
            assert not errs, (name, errs)
    # 前置满足后:注册表必须全绿
    monkeypatch.setenv("DATAFOUNDRY_LLM_BASE", "http://llm.example")
    monkeypatch.setenv("DATAFOUNDRY_LLM_KEY", "k")
    monkeypatch.setenv("DATAFOUNDRY_LLM_MODEL", "m")
    errors = validate_all()
    assert all(not v for v in errors.values()), errors


def test_registry_shape():
    for name, r in RECIPES.items():
        assert r["steps"], name
        assert r["provenance"], f"{name} 缺证据出处——配方必须可携证据"
        assert isinstance(r["requires"], list)


def test_get_recipe_is_copy():
    get_recipe("clean_basic_v1")["steps"].clear()
    assert RECIPES["clean_basic_v1"]["steps"], "get_recipe 必须深拷贝,不能污染注册表"


def test_unknown_recipe():
    with pytest.raises(KeyError):
        get_recipe("no_such_recipe")


def test_doc2sft_requires_llm(monkeypatch):
    monkeypatch.delenv("DATAFOUNDRY_LLM_BASE", raising=False)
    assert missing_requirements("doc2sft_v0") == ["llm"]
    monkeypatch.setenv("DATAFOUNDRY_LLM_BASE", "http://llm.example")
    assert missing_requirements("doc2sft_v0") == []


def test_math_recipe_composition_correctness(tmp_path):
    """组合的行为可判真伪:错答/重复/垃圾被杀,干净样本零误杀。"""
    src = tmp_path / "in.jsonl"
    src.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in MATH_ROWS), encoding="utf-8")
    manifest = run_pipeline(src, recipe_steps("math_zh_funnel_v1"), tmp_path / "run")
    assert manifest["n_in"] == 4
    assert manifest["n_out"] == 1
    survivor = json.loads((tmp_path / "run" / "output.jsonl").read_text(encoding="utf-8").strip())
    assert "8" in survivor["text"]
    causes = {json.loads(line)["trace"][-1]["op"]
              for line in (tmp_path / "run" / "rejects.jsonl").read_text(encoding="utf-8").splitlines()}
    assert "math_answer_verify" in causes and "exact_dedup" in causes


def test_cli_recipes_and_refine(tmp_path, capsys):
    assert cli_main(["recipes"]) == 0
    assert "math_zh_funnel_v1" in capsys.readouterr().out
    assert cli_main(["recipes", "math_zh_funnel_v1"]) == 0
    detail = json.loads(capsys.readouterr().out)
    assert detail["name"] == "math_zh_funnel_v1" and detail["missing_requirements"] == []
    assert cli_main(["recipes", "ghost"]) == 1
    capsys.readouterr()

    src = tmp_path / "in.jsonl"
    src.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in MATH_ROWS), encoding="utf-8")
    out = tmp_path / "out"
    assert cli_main(["refine", "--recipe", "math_zh_funnel_v1", "--in", str(src), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "总留存 4 -> 1" in text
    assert (out / "manifest.json").exists()


def test_api_recipes_and_run(tmp_path):
    client = TestClient(create_app(Store(tmp_path / "home")))
    client.post("/auth/bootstrap", json={"username": "root", "password": "rootpass123"})
    token = client.post("/auth/login", json={"username": "root", "password": "rootpass123"}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}

    names = {r["name"] for r in client.get("/recipes", headers=h).json()}
    assert {"clean_basic_v1", "math_zh_funnel_v1", "doc2sft_v0"} <= names
    assert client.get("/recipes/math_zh_funnel_v1", headers=h).json()["provenance"]
    assert client.get("/recipes/ghost", headers=h).status_code == 404

    jsonl = "\n".join(json.dumps(r, ensure_ascii=False) for r in MATH_ROWS)
    ds = client.post("/datasets/upload?name=math", content=jsonl,
                     headers={**h, "Content-Type": "text/plain"}).json()

    # steps 与 recipe_name 互斥;未知配方 404;llm 前置未满足 400
    bad = client.post("/runs", json={"dataset_id": ds["id"], "recipe_name": "math_zh_funnel_v1",
                                     "steps": [{"op": "exact_dedup"}]}, headers=h)
    assert bad.status_code == 400
    assert client.post("/runs", json={"dataset_id": ds["id"], "recipe_name": "ghost"}, headers=h).status_code == 404
    assert client.post("/runs", json={"dataset_id": ds["id"], "recipe_name": "doc2sft_v0"}, headers=h).status_code == 400

    run = client.post("/runs", json={"dataset_id": ds["id"], "recipe_name": "math_zh_funnel_v1"}, headers=h).json()
    import time
    for _ in range(50):
        info = client.get(f"/runs/{run['id']}", headers=h).json()
        if info["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    assert info["status"] == "succeeded"
    assert info["manifest"]["n_out"] == 1
    assert "math_answer_verify" in info["manifest"]["steps_executed"]


def test_mcp_list_recipes_tool():
    from datafoundry.interface.mcp_server import TOOLS, dispatch_tool, ApiClient

    assert any(t["name"] == "df_list_recipes" for t in TOOLS)

    calls = []

    class Fake(ApiClient):
        def __init__(self):
            self.base_url = "http://x"
            self.api_key = "k"

        def call(self, method, path, body=None, content_type="application/json"):
            calls.append((method, path))
            return {"ok": True}

    dispatch_tool(Fake(), "df_list_recipes", {})
    dispatch_tool(Fake(), "df_list_recipes", {"name": "math_zh_funnel_v1"})
    assert calls == [("GET", "/recipes"), ("GET", "/recipes/math_zh_funnel_v1")]
