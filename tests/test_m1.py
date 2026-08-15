"""M1 批次验收测试:#1 评测集与判分、#2 训练管线转换、#3 评测回流、#4 judge 落盘、#5 合成链。"""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import datafoundry.kernel.ops  # noqa: F401
from datafoundry.kernel.ops.judge import LlmJudgeFilter
from datafoundry.kernel.ops.synthesis import QaGenerateMapper
from datafoundry.kernel.registry import create_op
from datafoundry.kernel.runner import run_pipeline
from datafoundry.kernel.schema import make_sample
from datafoundry.service.server import create_app
from datafoundry.service.store import Store

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "evalsets"))
sys.path.insert(0, str(REPO / "scripts"))


# ---------- #4 judge 标注落盘 ----------

def test_judge_labels_persisted(tmp_path, monkeypatch):
    data = tmp_path / "in.jsonl"
    rows = [{"text": "高质量样本:内容翔实,信息密度高,值得保留。" * 3},
            {"text": "低质量样本:水文广告,快来买买买。" * 3}]
    data.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")

    scores = iter([{"score": 5, "reason": "翔实"}, {"score": 1, "reason": "广告"}])
    monkeypatch.setattr(LlmJudgeFilter, "_call", lambda self, text: next(scores))
    steps = [{"op": "llm_judge_filter", "params": {"base_url": "http://mock/v1", "model": "m", "min_score": 3}}]
    manifest = run_pipeline(data, steps, tmp_path / "out")

    labels_path = Path(manifest["judge_labels"])
    labels = [json.loads(l) for l in labels_path.read_text(encoding="utf-8").splitlines()]
    # 幸存(5分)与被杀(1分)都被收进蒸馏语料
    assert manifest["judge_labels_count"] == 2 and len(labels) == 2
    assert {l["score"] for l in labels} == {5, 1}
    assert all(l["text"] and "reason" in l for l in labels)


# ---------- #5 合成算子链 ----------

def test_chunk_mapper_lineage():
    op = create_op("text_chunk_mapper", min_chars=50, max_chars=120)
    doc = make_sample("\n".join(f"第{i}段:这是一段用于测试分块行为的说明性文字,长度适中。" for i in range(8)))
    children = op.expand(doc)
    assert len(children) >= 2
    assert all(c["meta"]["parent_id"] == doc["id"] for c in children)
    assert all(c["trace"][-1]["op"] == "text_chunk_mapper" for c in children)
    # 过短文本:无块可出,父样本可 kill(trace 带原因)
    short = make_sample("太短")
    assert op.expand(short) == [] and short["trace"][-1]["action"] == "kill"


def test_qa_generate_mocked(monkeypatch):
    monkeypatch.setattr(QaGenerateMapper, "_call",
                        lambda self, text: [{"q": "过拟合是什么?", "a": "训练集好、未见数据差。"},
                                            {"q": "对策有哪些?", "a": "正则化、早停、数据增广。"}])
    op = create_op("qa_generate_mapper", base_url="http://mock/v1", model="m")
    chunk = make_sample("过拟合指模型在训练集上表现好而在未见数据上表现差,常用对策包括正则化、早停与数据增广。")
    children = op.expand(chunk)
    assert len(children) == 2
    assert children[0]["meta"]["question"] and children[0]["meta"]["answer"]
    assert children[0]["text"].startswith("问:")
    assert children[0]["meta"]["parent_id"] == chunk["id"]


def test_full_doc_to_qa_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(QaGenerateMapper, "_call",
                        lambda self, text: [{"q": f"问题{i}", "a": f"答案{i}"} for i in range(3)])
    monkeypatch.setattr(LlmJudgeFilter, "_call", lambda self, text: {"score": 4, "reason": "ok"})
    doc_text = "\n".join(f"第{i}段:这是一段足够长的材料内容,覆盖了系统设计要点与验收标准的详细说明。" for i in range(10))
    data = tmp_path / "docs.jsonl"
    data.write_text(json.dumps({"text": doc_text}, ensure_ascii=False), encoding="utf-8")
    steps = [
        {"op": "text_chunk_mapper", "params": {"min_chars": 60, "max_chars": 150}},
        {"op": "qa_generate_mapper", "params": {"base_url": "http://mock/v1", "model": "m"}},
        {"op": "llm_judge_filter", "params": {"base_url": "http://mock/v1", "model": "m", "min_score": 3}},
    ]
    manifest = run_pipeline(data, steps, tmp_path / "out", funnel=False)
    assert manifest["n_out"] >= 6  # 1 篇文档 -> 多块 -> 每块 3 QA,全过 judge
    survivors = [json.loads(l) for l in Path(manifest["output"]).read_text(encoding="utf-8").splitlines()]
    s = survivors[0]
    assert s["meta"]["question"] and s["meta"]["answer"] and s["meta"]["parent_id"]
    assert [t["op"] for t in s["trace"][-2:]] == ["qa_generate_mapper", "llm_judge_filter"]
    assert manifest["judge_labels_count"] == manifest["n_out"]


# ---------- #1 评测集与判分 ----------

def test_evalset_deterministic_and_sane():
    import generate_math_zh as gen

    a, b = gen.generate(), gen.generate()
    assert a == b and len(a) == 60
    assert len({it["question"] for it in a}) == 60
    assert all(isinstance(it["answer"], int) for it in a)
    committed = [json.loads(l) for l in (REPO / "evalsets/math_zh_v1.jsonl").read_text(encoding="utf-8").splitlines()]
    assert committed == a  # 库中文件与生成器一致


def test_scorer_extract_and_score():
    import eval_math as em

    assert em.extract_answer("推理过程……\n答案: 42") == "42"
    assert em.extract_answer("先算 3*4=12,再加 5 得 17。答案:17") == "17"
    assert em.extract_answer("结果应该是 3,600 元") == "3600"
    assert em.extract_answer("没有数字") is None

    items = [{"id": "x1", "answer": 7}, {"id": "x2", "answer": 10}, {"id": "x3", "answer": 5}]
    report = em.score(items, {"x1": "答案: 7", "x2": "答案: 9"})
    assert report["n_correct"] == 1 and report["n_missing"] == 1
    assert report["accuracy"] == round(1 / 3 * 100, 1)


# ---------- #2 训练管线(转换 + dry-run) ----------

def test_train_proxy_convert_and_dryrun(tmp_path):
    data = tmp_path / "output.jsonl"
    rows = [{"id": "a", "text": "问:Q1\n答:A1", "meta": {"question": "Q1", "answer": "A1"}, "stats": {}, "trace": []},
            {"id": "b", "text": "无 QA 元数据的样本", "meta": {}, "stats": {}, "trace": []}]
    data.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    out = tmp_path / "exp"
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts/train_proxy.py"), "--data", str(data), "--out", str(out), "--dry-run"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    ds = json.loads((out / "dataset/data.json").read_text(encoding="utf-8"))
    assert ds == [{"instruction": "Q1", "input": "", "output": "A1"}]
    cfg = json.loads((out / "train_config.yaml").read_text(encoding="utf-8"))
    assert cfg["finetuning_type"] == "lora" and cfg["dataset"] == "proxy_sft" and cfg["seed"] == 42


# ---------- #3 评测回流 ----------

def test_eval_writeback_api(tmp_path):
    client = TestClient(create_app(Store(tmp_path / "home")))
    client.post("/auth/bootstrap", json={"username": "root", "password": "rootpass123"})
    token = client.post("/auth/login", json={"username": "root", "password": "rootpass123"}).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}

    jsonl = json.dumps({"text": "这是一条足够长的样本,用来打通评测回流的端到端链路。" * 2}, ensure_ascii=False)
    ds = client.post("/datasets/upload?name=e", content=jsonl, headers=auth).json()
    run = client.post("/runs", json={"dataset_id": ds["id"], "steps": [{"op": "quality_score"}]}, headers=auth).json()
    for _ in range(50):
        info = client.get(f"/runs/{run['id']}", headers=auth).json()
        if info["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    assert info["status"] == "succeeded"

    r = client.post(f"/runs/{run['id']}/eval", headers=auth,
                    json={"accuracy": 61.7, "evalset": "math_zh_v1", "train_fingerprint": "qwen7b-lora-seed42"})
    assert r.status_code == 200
    info = client.get(f"/runs/{run['id']}", headers=auth).json()
    assert info["manifest"]["eval"]["accuracy"] == 61.7
    assert info["manifest"]["eval"]["evalset"] == "math_zh_v1"
    assert client.post("/runs/run_missing/eval", headers=auth,
                       json={"accuracy": 1, "evalset": "x"}).status_code == 404