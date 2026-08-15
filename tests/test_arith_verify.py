"""解答自洽验证器测试:真实语料无参考答案时的确定性验证([M2-D 族,真实语料切入点])。"""
import datafoundry.ops  # noqa: F401  导入即注册
from datafoundry.registry import create_op


def _s(text):
    return {"id": "s", "text": text, "meta": {}, "stats": {}, "trace": []}


def _run(text, **params):
    return create_op("arithmetic_consistency_verify", **params).process(_s(text))


def test_correct_equations_pass():
    s = _run("先算 3+4=7,再算 7×2=14,答案是 14。")
    assert s is not None
    assert "复算 2 条断言" in s["trace"][-1]["detail"]


def test_false_equation_kills_and_names_it():
    s = _s("先算 3+4=8,所以答案是 8。")
    assert create_op("arithmetic_consistency_verify").process(s) is None
    assert s["stats"]["false_equation"] == "3+4=8"
    assert "假算式" in s["trace"][-1]["detail"]


def test_various_operators_and_decimals():
    assert _run("12÷4=3,0.5×4=2,10-2.5=7.5") is not None
    assert _run("1/2=0.5 且 2.5+2.5=5") is not None
    assert _run("12÷5=2.5") is None  # 12/5=2.4,假


def test_no_claims_passes_through():
    s = _run("这道题用代数方法,设 x 为未知数,推理后得到答案。")
    assert s is not None and "无算式断言" in s["trace"][-1]["detail"]


def test_division_by_zero_not_a_kill():
    assert _run("形式上写 5÷0=0 不可判,但 2+2=4 成立") is not None


def test_thousands_separator_normalized():
    assert _run("1,000+2,000=3,000") is not None
    assert _run("1,000+2,000=4,000") is None


def test_chained_equation_first_segment_checked():
    # 「3+4=7-2」左段 3+4=7 为真;链式书写不产生误杀
    assert _run("3+4=7-2=5,最后答案 5") is not None


# ---------- v2:带余数除法与边界护栏(真实语料首跑暴露的两类疑似误杀) ----------

def test_remainder_division_not_killed():
    assert _run("每人分到 10÷3=3……1(个),即 3 个余 1 个") is not None
    assert _run("5÷3=1……2,商 1 余 2") is not None
    assert _run("20÷3=6,余2,所以要 7 辆车") is not None


def test_remainder_division_wrong_still_killed():
    s = _s("10÷3=3……2,余数算错了")
    assert create_op("arithmetic_consistency_verify").process(s) is None
    assert "余" in s["stats"]["false_equation"]


def test_left_boundary_no_partial_match():
    assert _run("13+2=15,答案 15") is not None  # 不得截成 3+2=15


def test_fraction_continuation_not_killed():
    assert _run("化简得 1/4=5/20 两者相等") is not None  # 不得截成 1/4=5


def test_plain_false_division_still_killed():
    assert _run("12÷5=2.5") is None  # 无余数记法的假除法仍杀
