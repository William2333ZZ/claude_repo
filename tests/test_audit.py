"""合规证据包:抽样可复现、死因归因正确、双标准报告齐全、CLI 端到端。"""
import json

import datafoundry.kernel.ops  # noqa: F401
from datafoundry.kernel.audit import STANDARDS, build_report, death_cause_stats, sample_audit, write_report
from datafoundry.interface.cli import main as cli_main
from datafoundry.kernel.recipes import recipe_steps
from datafoundry.kernel.runner import run_pipeline

ROWS = [
    {"text": "问题:小明有 3 个苹果,又买了 5 个,现在共有几个?解答:3+5=8,答案是 8。", "reference": "8"},
    {"text": "问题:小红有 4 支笔,送出 1 支,还剩几支?解答:4-1=3,答案是 3。", "reference": "3"},
    {"text": "问题:小红有 4 支笔,送出 2 支,还剩几支?解答:4-2=1,答案是 1。", "reference": "2"},  # 错答
    {"text": "问题:小明有 3 个苹果,又买了 5 个,现在共有几个?解答:3+5=8,答案是 8。", "reference": "8"},  # 重复
    {"text": "!!!买买买 $$$###@@@///\\\\|||~~~^^^&&&***((()))!!!", "reference": "1"},  # 垃圾
]


def _make_run(tmp_path):
    src = tmp_path / "in.jsonl"
    src.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in ROWS), encoding="utf-8")
    run_dir = tmp_path / "run"
    run_pipeline(src, recipe_steps("math_zh_funnel_v1"), run_dir)
    return run_dir


def test_death_cause_stats(tmp_path):
    run_dir = _make_run(tmp_path)
    causes = death_cause_stats(run_dir / "rejects.jsonl")
    assert causes["math_answer_verify"] == 1 and causes["exact_dedup"] == 1
    assert sum(causes.values()) == 3


def test_sample_audit_reproducible_and_clean(tmp_path):
    run_dir = _make_run(tmp_path)
    a = sample_audit(run_dir / "output.jsonl", n=400, seed=7)
    b = sample_audit(run_dir / "output.jsonl", n=400, seed=7)
    assert a == b, "固定 seed 必须逐字段复现"
    assert a["sampled"] == a["population"] == 2  # 产出仅 2 条,抽样封顶
    assert a["rate"] == 0.0  # 漏斗产出应通过代理核查(垃圾已在产线被杀)


def test_reports_both_standards(tmp_path):
    run_dir = _make_run(tmp_path)
    for std in STANDARDS:
        path = write_report(run_dir, standard=std)
        text = path.read_text(encoding="utf-8")
        assert path.name == f"audit_{std}.md"
        assert "条款映射" in text and "边界声明" in text and "内容安全认证" in text
        assert "→ **通过**" in text
    # 同参数重建必须逐字一致(报告可复现)
    assert build_report(run_dir, "tc260") == build_report(run_dir, "tc260")


def test_unknown_standard(tmp_path):
    run_dir = _make_run(tmp_path)
    import pytest

    with pytest.raises(KeyError):
        build_report(run_dir, standard="iso42001")


def test_cli_audit(tmp_path, capsys):
    run_dir = _make_run(tmp_path)
    assert cli_main(["audit", "--run", str(run_dir), "--standard", "euai10"]) == 0
    out = capsys.readouterr().out
    assert "audit_euai10.md" in out and "通过" in out
    assert cli_main(["audit", "--run", str(tmp_path / "nowhere")]) == 1
