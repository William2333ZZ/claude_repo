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
class MathAnswerVerify(Op):
    name = "math_answer_verify"
    kind = "verify"
    cost_tier = "heuristic"
    cost_per_1k = 0.001
    expected_retention = 0.7
    description = "硬验证示例:文本末尾/boxed 答案与 meta[reference] 数值一致才留(验证器族的第一块砖)"

    _boxed = re.compile(r"\\boxed\{([^{}]+)\}")
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
        ref = sample["meta"].get(self.reference_key)
        if ref is None:
            add_trace(sample, self.name, "kill", f"meta 缺少 {self.reference_key}")
            return None
        boxed = self._boxed.findall(sample["text"])
        got = self._norm(boxed[-1]) if boxed else self._norm(sample["text"][-80:])
        want = self._norm(ref)
        if got is not None and want is not None and got == want:
            add_trace(sample, self.name, "pass", f"answer={got}")
            return sample
        add_trace(sample, self.name, "kill", f"answer={got} != reference={want}")
        return None
