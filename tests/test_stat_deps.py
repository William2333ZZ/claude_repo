"""算子 stats 依赖的组装期校验([DDD/coeffect],docs/22)。

依赖是声明出来的(requires_stats/provides_stats),不是约定俗成的:
缺提供者/顺序颠倒在组装期响亮失败;漏斗编译对"用户原序正确"的依赖链保序。
"""
import pytest

import datafoundry.kernel.ops  # noqa: F401  导入即注册
from datafoundry.kernel.pipeline import funnel_compile, stat_flow_errors, validate_steps
from datafoundry.kernel.registry import OPS, Op, register


def test_threshold_without_score_rejected_at_assembly():
    errs = validate_steps([{"op": "quality_threshold_filter"}])
    assert errs and "quality" in errs[0] and "quality_score" in errs[0]


def test_score_then_threshold_valid_and_order_kept():
    steps = [{"op": "quality_score"}, {"op": "quality_threshold_filter"}]
    assert validate_steps(steps) == []
    ordered, _ = funnel_compile(steps)
    names = [s["op"] for s in ordered]
    assert names.index("quality_score") < names.index("quality_threshold_filter")


# ---- 跨档依赖:若无档位提升,heuristic 阈值器会被漏斗排到 llm 打分器之前 ----

if "_t_llm_tagger" not in OPS:
    @register
    class _TLlmTagger(Op):
        name = "_t_llm_tagger"
        kind = "score"
        cost_tier = "llm"
        cost_per_1k = 1.0
        description = "测试用:llm 档打分器,供给 stats.t_tag"
        provides_stats = ("t_tag",)

        def process(self, sample):
            sample["stats"]["t_tag"] = 1
            return sample

    @register
    class _TTagGate(Op):
        name = "_t_tag_gate"
        kind = "filter"
        cost_tier = "heuristic"
        cost_per_1k = 0.0001
        description = "测试用:heuristic 档阈值器,依赖 stats.t_tag"
        requires_stats = ("t_tag",)

        def process(self, sample):
            return sample


def test_funnel_lifts_dependent_across_tiers():
    steps = [{"op": "_t_llm_tagger"}, {"op": "_t_tag_gate"}]
    ordered, _ = funnel_compile(steps)
    names = [s["op"] for s in ordered]
    assert names.index("_t_llm_tagger") < names.index("_t_tag_gate")
    assert stat_flow_errors(ordered) == []
    assert validate_steps(steps) == []


def test_user_reversed_order_fails_loud_not_silently_fixed():
    # 用户原序颠倒:编译器不静默代改意图,组装期报"提供者排在它之后"
    errs = validate_steps([{"op": "_t_tag_gate", "pin": True}, {"op": "_t_llm_tagger", "pin": True}])
    assert errs and "排在它之后" in errs[0]


def test_run_pipeline_funnel_off_still_enforced(tmp_path):
    from datafoundry.kernel.runner import run_pipeline
    src = tmp_path / "in.jsonl"
    src.write_text('{"text": "样本文本足够长通过检查"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="stats"):
        run_pipeline(src, [{"op": "quality_threshold_filter"}], tmp_path / "out", funnel=False)
