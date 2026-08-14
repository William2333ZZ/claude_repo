"""[M2-D2] 数学等价判定(#10):判分器(eval_math)与验证器(math_answer_verify)共用的
单源等价函数——判分口径 bug(str/int)之后的纪律:比较逻辑只许有一份。

分级策略(容差策略成文):
  L0 精确匹配:字符串相等——与历史行为逐字节一致,零回归护栏
  L1 有理数等价(stdlib,总是可用):`1/2` = `0.5` = `\\frac{1}{2}`;fractions.Fraction
     精确比较,无浮点近似(`0.33` ≠ `1/3`,这是特性不是缺陷:判分不做善意四舍五入)
  L2 表达式等价(可选依赖:pip install 'datafoundry[math]',sympy):`1+1`=`2`、
     `sqrt(4)`=`2` 等;无 sympy 时静默回落 L0+L1,行为恒为现状超集

安全:任何输入先过白名单字符集(数字/四则/括号/小数点/幂号/空白,及字面量 sqrt),
不合格直接判不等,原始字符串永不进入 sympify。
"""
from __future__ import annotations

import re
from fractions import Fraction

_FRAC_LATEX = re.compile(r"\\+frac\{([^{}]+)\}\{([^{}]+)\}")
_SAFE_NUM = re.compile(r"^[0-9+\-*/().\s]+$")
_SAFE_EXPR = re.compile(r"^[0-9+\-*/().^\s]*$")
_TRAIL = ",。.,;;:!?!?\u3000 "


def _plain(s) -> str:
    """latex 分数转斜杠形式,去千分位逗号与句尾标点。"""
    txt = _FRAC_LATEX.sub(r"(\1)/(\2)", str(s).strip())
    return txt.replace(",", "").strip(_TRAIL)


def to_fraction(s) -> Fraction | None:
    """解析为精确有理数;支持整数/小数/科学计数/a÷b 斜杠分数/latex 分数。失败返回 None。"""
    txt = _plain(s)
    if not txt or not _SAFE_NUM.match(txt):
        return None
    bare = txt.replace("(", "").replace(")", "").strip()
    try:
        return Fraction(bare)
    except (ValueError, ZeroDivisionError):
        pass
    if bare.count("/") == 1:  # Fraction 不吃 "0.5/2" 这类小数分子分母,手拆一次
        num, den = bare.split("/")
        try:
            return Fraction(num.strip()) / Fraction(den.strip())
        except (ValueError, ZeroDivisionError):
            return None
    return None


def equivalent(got, want) -> bool:
    """判分/验证共用的等价谓词。None 恒不等;策略见模块 docstring。"""
    if got is None or want is None:
        return False
    a, b = str(got).strip(), str(want).strip()
    if a == b:  # L0
        return True
    fa, fb = to_fraction(a), to_fraction(b)
    if fa is not None and fb is not None:  # L1:双方可精确解析→精确比较
        return fa == fb
    # L2:可选 sympy 表达式等价(白名单:允许 sqrt 字面量与幂号)
    sa, sb = _plain(a), _plain(b)
    if _SAFE_EXPR.match(sa.replace("sqrt", "")) and _SAFE_EXPR.match(sb.replace("sqrt", "")):
        try:
            import sympy  # noqa: PLC0415  可选依赖,仅此处触达

            diff = sympy.simplify(
                sympy.sympify(sa.replace("^", "**")) - sympy.sympify(sb.replace("^", "**"))
            )
            return bool(diff == 0)
        except Exception:  # sympy 缺失或表达式不可解析:回落"不等"
            return False
    return False
