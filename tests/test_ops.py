import datafoundry.kernel.ops  # noqa: F401
from datafoundry.kernel.registry import catalog, create_op
from datafoundry.kernel.schema import make_sample


def run_batch(op, samples):
    return list(op.process_batch(iter(samples)))


def test_catalog_has_core_ops():
    names = {spec["name"] for spec in catalog()}
    assert {"length_filter", "exact_dedup", "minhash_dedup", "pii_redact", "llm_judge_filter"} <= names


def test_length_filter_kills_and_traces():
    op = create_op("length_filter", min_len=10)
    dead = op.process(make_sample("短"))
    assert dead is None
    ok = op.process(make_sample("这句话足够长可以通过检查了"))
    assert ok is not None
    assert ok["trace"][-1]["action"] == "pass"


def test_pii_redact():
    op = create_op("pii_redact")
    s = op.process(make_sample("邮箱 a.b@example.com 手机 13812345678 均需脱敏"))
    assert "[EMAIL]" in s["text"] and "[PHONE]" in s["text"]
    assert s["stats"]["pii_hits"] == 2


def test_exact_dedup_keeps_first():
    op = create_op("exact_dedup")
    a, b = make_sample("同一句话  测试"), make_sample("同一句话 测试")  # 空白差异归一化后相同
    out = run_batch(op, [a, b])
    assert out[0] is not None and out[1] is None
    assert a["trace"][-1]["action"] == "pass" and b["trace"][-1]["action"] == "kill"


def test_minhash_dedup_near_duplicates():
    base = "机器学习中的过拟合指模型在训练集上表现好而在未见数据上表现差,常用对策包括正则化早停与数据增广。" * 3
    near = base.replace("表现好", "表现很好")
    other = "Transformer 的自注意力让每个位置直接聚合任意位置的信息,复杂度随序列长度平方增长,长序列场景需要稀疏化。" * 3
    op = create_op("minhash_dedup")
    out = run_batch(op, [make_sample(base), make_sample(near), make_sample(other)])
    assert out[0] is not None
    assert out[1] is None  # 近重复被杀
    assert out[2] is not None  # 无关文本保留


def test_repetition_filter():
    op = create_op("repetition_filter", max_dup_ratio=0.3)
    spam = " ".join(["刷屏 文本 重复"] * 20)
    assert op.process(make_sample(spam)) is None
    normal = "今天 讨论 三个 主题 分别 是 架构 评审 数据 质量 以及 上线 计划 大家 依次 发言 即可"
    assert op.process(make_sample(normal)) is not None


def test_math_answer_verify():
    op = create_op("math_answer_verify")
    good = make_sample("先算 3*4=12,再加 5,所以 \\boxed{17}", meta={"reference": "17"})
    bad = make_sample("所以答案是 \\boxed{16}", meta={"reference": "17"})
    assert op.process(good) is not None
    assert op.process(bad) is None


def test_quality_score_and_threshold():
    score = create_op("quality_score")
    keep = score.process(make_sample("信息密度正常的一段说明文字," * 10))
    assert 0 <= keep["stats"]["quality"] <= 1
    gate = create_op("quality_threshold_filter", min_quality=0.99)
    assert gate.process(keep) is None or keep["stats"]["quality"] >= 0.99
