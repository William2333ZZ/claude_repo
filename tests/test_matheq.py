"""[M2-D2] 数学等价判定测试:L0/L1/L2 三级、无依赖回落、零回归护栏、算子级行为。"""
import builtins

import pytest

from datafoundry.kernel import matheq
from datafoundry.kernel.matheq import equivalent, to_fraction


# ---------- L0/L1:stdlib 精确有理(总是可用) ----------

@pytest.mark.parametrize("a,b", [
    ("11", "11"),            # L0 精确
    ("1/2", "0.5"),          # 分数 vs 小数
    ("\\frac{1}{2}", "0.5"),  # latex 分数
    ("\\frac{3}{4}", "3/4"),
    ("3", "3.0"),
    ("-2", "-2.00"),
    ("1,000", "1000"),       # 千分位
    ("0.5/2", "1/4"),        # 小数分子
    ("12。", "12"),          # 句尾标点
])
def test_equivalent_true(a, b):
    assert equivalent(a, b) and equivalent(b, a)


@pytest.mark.parametrize("a,b", [
    ("0.33", "1/3"),   # 精确策略:不做善意近似(成文特性)
    ("11", "12"),
    ("1/2", "1/3"),
    (None, "1"), ("1", None),
    ("abc", "abc1"),
])
def test_equivalent_false(a, b):
    assert not equivalent(a, b)


def test_to_fraction_forms():
    from fractions import Fraction
    assert to_fraction("\\frac{7}{2}") == Fraction(7, 2)
    assert to_fraction("3.25") == Fraction(13, 4)
    assert to_fraction("x+1") is None
    assert to_fraction("1/0") is None  # 除零:解析失败而非崩溃


def test_charset_guard_blocks_injection():
    # 非白名单字符(字母/下划线/引号)不进任何解析路径,直接不等
    assert not equivalent("__import__('os')", "0")
    assert not equivalent("1;rm -rf", "1")


# ---------- L2:sympy 表达式(可选依赖)与回落 ----------

def test_expression_with_sympy_if_available():
    sympy = pytest.importorskip("sympy")  # noqa: F841  CI [math] 安装;本地缺则跳过
    assert equivalent("1+1", "2")
    assert equivalent("sqrt(4)", "2")
    assert not equivalent("sqrt(2)", "1.414")  # 精确策略延续到无理数


def test_fallback_without_sympy(monkeypatch):
    real_import = builtins.__import__

    def no_sympy(name, *a, **kw):
        if name == "sympy":
            raise ImportError("模拟未安装")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_sympy)
    assert not equivalent("1+1", "2")      # L2 缺席:回落不等,不崩溃
    assert equivalent("1/2", "0.5")        # L1 不受影响


# ---------- 零回归护栏:整数口径行为与历史逐条一致 ----------

def test_zero_regression_on_integer_protocol():
    # 历史比较 = 归一化字符串精确相等;整数域上 equivalent 必须给出完全相同的判定
    cases = [("11", "11", True), ("10", "10", True), ("19", "19", True),
             ("11", "12", False), (None, "11", False), ("3", "30", False)]
    for got, want, expect in cases:
        legacy = got is not None and got == want
        assert legacy == expect == equivalent(got, want)


def test_eval_math_score_unchanged_on_run5_fixture():
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path("scripts").resolve()))
    from eval_math import score

    items = [{"id": f"c{i}", "answer": a, "question": "x"} for i, a in enumerate([11, 10, 3, 4, 19])]
    outputs = {f"c{i}": f"先算……答案: {a}" for i, a in enumerate([11, 10, 3, 4, 19])}
    outputs["c4"] = "先算……答案: 18"  # 一条真错
    r = score(items, outputs)
    assert (r["n_correct"], r["accuracy"]) == (4, 80.0)


# ---------- 算子级:math_answer_verify 用等价判定后是历史行为的超集 ----------

def _sample(text, ref):
    return {"id": "s", "text": text, "meta": {"reference": ref}, "stats": {}, "trace": []}


def test_op_accepts_fraction_boxed_vs_decimal_reference():
    import datafoundry.kernel.ops  # noqa: F401  导入即注册
    from datafoundry.kernel.registry import create_op

    op = create_op("math_answer_verify")
    assert op.process(_sample("推导得 \\boxed{\\frac{1}{2}}", "0.5")) is not None  # 新能力
    assert op.process(_sample("所以 \\boxed{42}", 42)) is not None                 # 历史行为
    assert op.process(_sample("所以 \\boxed{41}", 42)) is None                     # 错答仍杀
