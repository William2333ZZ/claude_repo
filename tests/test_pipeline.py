import json

import datafoundry.ops  # noqa: F401
from datafoundry.pipeline import estimate, funnel_compile, validate_steps
from datafoundry.runner import run_pipeline


def test_validate_unknown_op_and_bad_params():
    assert validate_steps([]) != []
    assert any("未知算子" in e for e in validate_steps([{"op": "nope"}]))
    assert any("参数错误" in e for e in validate_steps([{"op": "length_filter", "params": {"bogus": 1}}]))
    # llm 算子未配置后端时,validate 阶段就报错
    assert any("llm_judge_filter" in e for e in validate_steps([{"op": "llm_judge_filter"}]))


def test_funnel_puts_llm_last_and_respects_pin():
    steps = [
        {"op": "llm_judge_filter", "params": {"base_url": "http://x/v1", "model": "m"}},
        {"op": "length_filter"},
        {"op": "exact_dedup"},
    ]
    ordered, moves = funnel_compile(steps)
    assert [s["op"] for s in ordered] == ["length_filter", "exact_dedup", "llm_judge_filter"]
    assert moves

    pinned = [
        {"op": "llm_judge_filter", "params": {"base_url": "http://x/v1", "model": "m"}, "pin": True},
        {"op": "length_filter"},
    ]
    ordered2, _ = funnel_compile(pinned)
    assert ordered2[0]["op"] == "llm_judge_filter"  # pin 保持原位


def test_estimate_savings_positive():
    steps = [
        {"op": "llm_judge_filter", "params": {"base_url": "http://x/v1", "model": "m"}},
        {"op": "length_filter"},
        {"op": "exact_dedup"},
    ]
    est = estimate(steps, 100_000)
    assert est["est_cost_funnel"] < est["est_cost_naive"]
    assert est["est_savings"] > 0
    assert est["funnel_order"][-1] == "llm_judge_filter"


def test_run_pipeline_end_to_end(tmp_path):
    data = tmp_path / "in.jsonl"
    rows = [
        {"text": "这是一条足够长的正常样本,用来验证端到端流水线的通过路径。" * 2},
        {"text": "这是一条足够长的正常样本,用来验证端到端流水线的通过路径。" * 2},
        {"text": "太短"},
    ]
    data.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    steps = [{"op": "length_filter", "params": {"min_len": 10}}, {"op": "exact_dedup"}, {"op": "quality_score"}]
    manifest = run_pipeline(data, steps, tmp_path / "out")

    assert manifest["n_in"] == 3 and manifest["n_out"] == 1
    out_lines = (tmp_path / "out" / "output.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(out_lines) == 1
    survivor = json.loads(out_lines[0])
    assert [t["op"] for t in survivor["trace"]] == ["length_filter", "exact_dedup", "quality_score"]
    rejects = [json.loads(x) for x in (tmp_path / "out" / "rejects.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rejects) == 2
    assert all(r["trace"][-1]["action"] == "kill" for r in rejects)
