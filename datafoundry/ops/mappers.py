"""改写类算子(heuristic 档)。"""
from __future__ import annotations

import re

from datafoundry.registry import Op, register
from datafoundry.schema import add_trace


@register
class WhitespaceNormalize(Op):
    name = "whitespace_normalize"
    kind = "mapper"
    cost_tier = "heuristic"
    cost_per_1k = 0.0002
    expected_retention = 1.0
    description = "压缩连续空白、去首尾空白、统一换行"

    _ws = re.compile(r"[ \t\f\v]+")
    _nl = re.compile(r"\n{3,}")

    def process(self, sample):
        old = sample["text"]
        text = self._nl.sub("\n\n", self._ws.sub(" ", old.replace("\r\n", "\n"))).strip()
        if text != old:
            sample["text"] = text
            add_trace(sample, self.name, "edit", f"{len(old)}->{len(text)} chars")
        else:
            add_trace(sample, self.name, "pass")
        return sample


@register
class HtmlStrip(Op):
    name = "html_strip"
    kind = "mapper"
    cost_tier = "heuristic"
    cost_per_1k = 0.0005
    expected_retention = 1.0
    description = "剥掉 HTML 标签与转义实体(正则级,重解析场景请换专用零件)"

    _tag = re.compile(r"<[^>]{1,200}>")
    _ent = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&nbsp;": " ", "&quot;": '"', "&#39;": "'"}

    def process(self, sample):
        old = sample["text"]
        text = self._tag.sub(" ", old)
        for k, v in self._ent.items():
            text = text.replace(k, v)
        if text != old:
            sample["text"] = text
            add_trace(sample, self.name, "edit", "stripped html")
        else:
            add_trace(sample, self.name, "pass")
        return sample


@register
class PiiRedact(Op):
    name = "pii_redact"
    kind = "mapper"
    cost_tier = "heuristic"
    cost_per_1k = 0.001
    expected_retention = 1.0
    description = "脱敏:邮箱/手机号/身份证号替换为占位符,命中数写入 stats.pii_hits(合规与审计凭证)"

    _patterns = [
        ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
        ("ID", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
        ("PHONE", re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")),
    ]

    def process(self, sample):
        text, hits = sample["text"], 0
        for tag, pat in self._patterns:
            text, n = pat.subn(f"[{tag}]", text)
            hits += n
        sample["stats"]["pii_hits"] = hits
        if hits:
            sample["text"] = text
            add_trace(sample, self.name, "edit", f"redacted {hits} PII spans")
        else:
            add_trace(sample, self.name, "pass")
        return sample
