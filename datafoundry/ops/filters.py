"""启发式过滤与打分算子(heuristic 档:CPU、~分/GB 量级)。"""
from __future__ import annotations

import re

from datafoundry.registry import Op, register
from datafoundry.schema import add_trace


@register
class LengthFilter(Op):
    name = "length_filter"
    kind = "filter"
    cost_tier = "heuristic"
    cost_per_1k = 0.0002
    expected_retention = 0.9
    description = "按字符长度过滤,杀掉过短/过长样本"

    def __init__(self, min_len: int = 20, max_len: int = 100_000):
        super().__init__(min_len=min_len, max_len=max_len)
        self.min_len, self.max_len = min_len, max_len

    def process(self, sample):
        n = len(sample["text"])
        if n < self.min_len or n > self.max_len:
            add_trace(sample, self.name, "kill", f"len={n} 超出 [{self.min_len},{self.max_len}]")
            return None
        add_trace(sample, self.name, "pass")
        return sample


@register
class SymbolRatioFilter(Op):
    name = "symbol_ratio_filter"
    kind = "filter"
    cost_tier = "heuristic"
    cost_per_1k = 0.0002
    expected_retention = 0.95
    description = "非文字字符(符号/标点/数字之外的杂讯)占比过高则杀"

    _word = re.compile(r"[\w\u4e00-\u9fff]")

    def __init__(self, max_ratio: float = 0.4):
        super().__init__(max_ratio=max_ratio)
        self.max_ratio = max_ratio

    def process(self, sample):
        text = sample["text"]
        if not text:
            add_trace(sample, self.name, "kill", "空文本")
            return None
        wordy = len(self._word.findall(text))
        ratio = 1 - wordy / len(text)
        sample["stats"]["symbol_ratio"] = round(ratio, 4)
        if ratio > self.max_ratio:
            add_trace(sample, self.name, "kill", f"symbol_ratio={ratio:.2f} > {self.max_ratio}")
            return None
        add_trace(sample, self.name, "pass")
        return sample


@register
class RepetitionFilter(Op):
    name = "repetition_filter"
    kind = "filter"
    cost_tier = "heuristic"
    cost_per_1k = 0.0005
    expected_retention = 0.92
    description = "词级 n-gram 重复率过高(模板水文/爬虫噪声)则杀"

    def __init__(self, n: int = 3, max_dup_ratio: float = 0.3):
        super().__init__(n=n, max_dup_ratio=max_dup_ratio)
        self.n, self.max_dup_ratio = n, max_dup_ratio

    def process(self, sample):
        toks = sample["text"].split()
        if len(toks) < self.n * 2:
            add_trace(sample, self.name, "pass", "过短不判")
            return sample
        grams = [tuple(toks[i : i + self.n]) for i in range(len(toks) - self.n + 1)]
        dup = 1 - len(set(grams)) / len(grams)
        sample["stats"]["ngram_dup_ratio"] = round(dup, 4)
        if dup > self.max_dup_ratio:
            add_trace(sample, self.name, "kill", f"dup_ratio={dup:.2f} > {self.max_dup_ratio}")
            return None
        add_trace(sample, self.name, "pass")
        return sample


@register
class BlocklistFilter(Op):
    name = "blocklist_filter"
    kind = "filter"
    cost_tier = "heuristic"
    cost_per_1k = 0.0003
    expected_retention = 0.97
    description = "命中屏蔽词则杀(大小写不敏感,词表由参数传入)"

    def __init__(self, words: list[str] | None = None):
        super().__init__(words=words or [])
        self.words = [w.lower() for w in (words or []) if w]

    def process(self, sample):
        low = sample["text"].lower()
        hit = next((w for w in self.words if w in low), None)
        if hit:
            add_trace(sample, self.name, "kill", f"命中屏蔽词: {hit}")
            return None
        add_trace(sample, self.name, "pass")
        return sample


@register
class QualityScore(Op):
    name = "quality_score"
    kind = "score"
    cost_tier = "heuristic"
    cost_per_1k = 0.0005
    expected_retention = 1.0
    description = "启发式综合质量分(0-1)写入 stats.quality,供 selector/报告使用"

    def process(self, sample):
        text = sample["text"]
        length_term = min(len(text) / 500, 1.0)
        symbol = sample["stats"].get("symbol_ratio")
        if symbol is None:
            wordy = len(re.findall(r"[\w\u4e00-\u9fff]", text))
            symbol = 1 - wordy / max(len(text), 1)
        dup = sample["stats"].get("ngram_dup_ratio", 0.0)
        score = max(0.0, min(1.0, 0.4 * length_term + 0.3 * (1 - symbol) + 0.3 * (1 - dup)))
        sample["stats"]["quality"] = round(score, 4)
        add_trace(sample, self.name, "score", f"quality={score:.2f}")
        return sample


@register
class QualityThresholdFilter(Op):
    name = "quality_threshold_filter"
    kind = "filter"
    cost_tier = "heuristic"
    cost_per_1k = 0.0001
    expected_retention = 0.8
    description = "按 stats.quality 阈值过滤(需先跑 quality_score)"

    def __init__(self, min_quality: float = 0.5):
        super().__init__(min_quality=min_quality)
        self.min_quality = min_quality

    def process(self, sample):
        q = sample["stats"].get("quality")
        if q is None:
            add_trace(sample, self.name, "pass", "无 quality 分,放行")
            return sample
        if q < self.min_quality:
            add_trace(sample, self.name, "kill", f"quality={q} < {self.min_quality}")
            return None
        add_trace(sample, self.name, "pass")
        return sample


@register
class ArithmeticConsistencyVerify(Op):
    name = "arithmetic_consistency_verify"
    kind = "verify"
    cost_tier = "heuristic"
    cost_per_1k = 0.002
    expected_retention = 0.8
    description = "解答自洽验证:检出正文全部 a◦b=c 算式断言并复算,复算必错才杀;取整/四舍五入/分数惯用语按书写惯例记账放行——真实语料无参考答案时的确定性验证器"

    # 边界护栏(宁漏检不误杀):
    #   左侧不接数字/小数点/斜杠(防 13+2 被截成 3+2),也不接运算符
    #   (防 20×1/4=5、5+3×2=11 等复合表达式被截出假断言);
    #   右侧不接数字/斜杠(防 1/4=5/20 被截成 1/4=5),也不接运算符
    #   (防 2+2=2×2 被截成 2+2=2)。
    _eq = re.compile(
        r"(?<![\d./+\-×*xX÷])(\d+(?:\.\d+)?)\s*([+\-×*xX÷/])\s*(\d+(?:\.\d+)?)\s*=\s*(\d+(?:\.\d+)?)(?![\d/+\-×*xX÷])"
    )
    # 带余数除法记法:a÷b=q……r / a…r / 余 r —— 按 a=q*b+r 且 r<b 验证,先于普通算式消耗
    _rem = re.compile(
        r"(?<![\d./+\-×*xX÷])(\d+)\s*[÷/]\s*(\d+)\s*=\s*(\d+)\s*(?:[…]{1,2}|\.{2,6}|,?\s*余)\s*(\d+)(?![\d/])"
    )
    _thousands = re.compile(r"(?<=\d),(?=\d{3}\b)")

    def __init__(self, min_claims: int = 1, rel_tol: float = 1e-9, abs_tol: float = 1e-6):
        super().__init__(min_claims=min_claims, rel_tol=rel_tol, abs_tol=abs_tol)
        self.min_claims = min_claims
        self.rel_tol = rel_tol
        self.abs_tol = abs_tol

    def _check(self, a: float, op: str, b: float, c: float) -> bool | None:
        """返回 True=断言成立 / False=断言为假 / None=不可判(除零等),不可判不作死因。"""
        if op == "+":
            v = a + b
        elif op == "-":
            v = a - b
        elif op in "×*xX":
            v = a * b
        else:  # ÷ 或 /
            if b == 0:
                return None
            v = a / b
        return abs(v - c) <= max(self.abs_tol, self.rel_tol * max(abs(v), abs(c)))

    @staticmethod
    def _convention(sa: str, op: str, sb: str, sc: str) -> str | None:
        """复算不合时的书写惯例豁免(记账放行,不判死刑):
        fraction_idiom        「的1/4=5(千米)」惯用语——真分数=整数几乎必是「X 的 p/q」语义
        div_floor_convention  应用题取整惯例 10÷3=3(整数除法弃余)
        div_round_convention  按书写精度四舍五入 2÷3=0.67
        这些数学上不严格,但不是"算错";判死会污染假算式统计——宁漏检不误杀。"""
        if op not in "÷/":
            return None
        if op == "/" and "." not in sc and float(sa) < float(sb) and float(sc) >= 1:
            return "fraction_idiom"
        if "." in sa or "." in sb:
            return None
        ia, ib = int(sa), int(sb)
        if not ib:
            return None
        if "." not in sc:
            return "div_floor_convention" if int(sc) == ia // ib else None
        k = len(sc.split(".", 1)[1])
        if k <= 6 and abs(round(ia / ib, k) - float(sc)) < 1e-9:
            return "div_round_convention"
        return None

    def process(self, sample):
        text = self._thousands.sub("", sample["text"])
        checked = 0
        conventions = 0
        # 先处理带余数除法(并从文本消耗,避免被普通算式正则截断误杀)
        def _rem_check(m: re.Match) -> str:
            nonlocal checked
            a, b, q, r = (int(m.group(i)) for i in range(1, 5))
            if b == 0:
                return " "
            checked += 1
            if a == q * b + r and r < b:
                return " "  # 成立:消耗掉
            sample["stats"]["false_equation"] = f"{a}÷{b}={q}余{r}"
            return "\x00KILL\x00"

        text = self._rem.sub(_rem_check, text)
        if "\x00KILL\x00" in text:
            add_trace(sample, self.name, "kill", f"假算式: {sample['stats']['false_equation']}")
            return None
        for sa, op, sb, sc in self._eq.findall(text):
            ok = self._check(float(sa), op, float(sb), float(sc))
            if ok is None:
                continue
            if ok:
                checked += 1
                continue
            conv = self._convention(sa, op, sb, sc)  # 复算不合:先问惯例,再判死刑
            if conv:
                sample["stats"][conv] = sample["stats"].get(conv, 0) + 1
                conventions += 1
                continue
            bad = f"{sa}{op}{sb}={sc}"
            sample["stats"]["false_equation"] = bad
            add_trace(sample, self.name, "kill", f"假算式: {bad}")
            return None
        if checked == 0 and conventions == 0:
            add_trace(sample, self.name, "pass", "无算式断言,放行")
            return sample
        parts = ([f"复算 {checked} 条断言全部成立"] if checked else []) + (
            [f"惯例书写记账 {conventions} 处"] if conventions else [])
        add_trace(sample, self.name, "pass", ",".join(parts))
        return sample


@register
class MathAnswerVerify(Op):
    name = "math_answer_verify"
    kind = "verify"
    cost_tier = "heuristic"
    cost_per_1k = 0.001
    expected_retention = 0.7
    description = "硬验证示例:文本末尾/boxed 答案与 meta[reference] 数值一致才留(验证器族的第一块砖)"

    _boxed = re.compile(r"\\boxed\{((?:[^{}]|\{[^{}]*\})+)\}")  # 容一层嵌套:\boxed{\frac{1}{2}}
    _num = re.compile(r"-?\d+(?:\.\d+)?")

    def __init__(self, reference_key: str = "reference"):
        super().__init__(reference_key=reference_key)
        self.reference_key = reference_key

    @classmethod
    def _norm(cls, s: str) -> str | None:
        m = cls._num.findall(str(s).replace(",", ""))
        if not m:
            return None
        v = float(m[-1])
        return str(int(v)) if v == int(v) else f"{v:g}"

    def process(self, sample):
        from datafoundry.matheq import equivalent, to_fraction  # 等价判定单源(M2-D2)

        ref = sample["meta"].get(self.reference_key)
        if ref is None:
            add_trace(sample, self.name, "kill", f"meta 缺少 {self.reference_key}")
            return None
        boxed = self._boxed.findall(sample["text"])
        # boxed 内容先整体尝试(保留 1/2、\frac{1}{2} 等形式),失败再退历史的取末数字
        raw = boxed[-1] if boxed else None
        got = (raw if raw is not None and to_fraction(raw) is not None
               else (self._norm(raw) if raw is not None else self._norm(sample["text"][-80:])))
        want = ref if to_fraction(ref) is not None else self._norm(ref)
        if equivalent(got, want):
            add_trace(sample, self.name, "pass", f"answer={got}")
            return sample
        add_trace(sample, self.name, "kill", f"answer={got} != reference={want}")
        return None
