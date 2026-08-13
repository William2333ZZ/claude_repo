"""合成算子链 [M1-B2]:文本分块 → QA 生成(LLM)→ 交给 llm_judge_filter 过滤。

设计要点(docs/09 §2 的算子规格五元组):
- text_chunk_mapper : expand/heuristic;按段落聚合成 300–800 字块,子样本挂 parent_id 血缘
- qa_generate_mapper: expand/llm;每块生成 N 个 QA 对,q/a 同时写入 meta(供训练管线抽取)
  与 text(供后续过滤算子处理);LLM 失败时父样本进 rejects 并带原因,不静默丢
"""
from __future__ import annotations

import json
import os
import urllib.request

from datafoundry.registry import Op, register
from datafoundry.schema import add_trace, make_sample


@register
class TextChunkMapper(Op):
    name = "text_chunk_mapper"
    kind = "expand"
    cost_tier = "heuristic"
    cost_per_1k = 0.0005
    expected_retention = 4.0  # 一篇文档平均切出的块数(预估用)
    description = "按段落边界聚合分块(min_chars~max_chars),子样本带 parent_id/chunk_index 血缘"

    def __init__(self, min_chars: int = 300, max_chars: int = 800):
        super().__init__(min_chars=min_chars, max_chars=max_chars)
        self.min_chars, self.max_chars = min_chars, max_chars

    def process(self, sample):  # pragma: no cover - expand 算子不走 1:1 流
        return sample

    def expand(self, sample: dict) -> list[dict]:
        paragraphs = [p.strip() for p in sample["text"].split("\n") if p.strip()]
        chunks: list[str] = []
        buf = ""
        for p in paragraphs:
            if buf and len(buf) + len(p) > self.max_chars:
                chunks.append(buf)
                buf = p
            else:
                buf = f"{buf}\n{p}" if buf else p
            while len(buf) > self.max_chars:  # 超长段落硬切
                chunks.append(buf[: self.max_chars])
                buf = buf[self.max_chars :]
        if buf:
            if chunks and len(buf) < self.min_chars:
                chunks[-1] += "\n" + buf
            else:
                chunks.append(buf)
        chunks = [c for c in chunks if len(c) >= self.min_chars] or ([sample["text"]] if len(sample["text"]) >= self.min_chars else [])
        if not chunks:
            add_trace(sample, self.name, "kill", f"文本不足 {self.min_chars} 字,无块可出")
            return []
        children = []
        for i, chunk in enumerate(chunks):
            child = make_sample(chunk, meta={**sample["meta"], "parent_id": sample["id"], "chunk_index": i})
            child["trace"] = list(sample["trace"])
            add_trace(child, self.name, "edit", f"chunk {i + 1}/{len(chunks)} of {sample['id']}")
            children.append(child)
        return children


_QA_PROMPT = """你是数据标注员。基于下面这段材料,出 {n} 个高质量问答对:
- 问题必须能从材料中找到明确答案,不出主观题
- 答案完整、自洽,不写"如材料所述"
只输出 JSON 数组: [{{"q": "...", "a": "..."}}, ...]
材料:
{text}"""


@register
class QaGenerateMapper(Op):
    name = "qa_generate_mapper"
    kind = "expand"
    cost_tier = "llm"
    cost_per_1k = 20.0
    expected_retention = 3.0  # 每块产出的 QA 数(预估用)
    description = "每个文本块生成 N 个 QA 对(OpenAI 兼容后端);q/a 写入 meta 供训练管线抽取"

    def __init__(
        self,
        n_pairs: int = 3,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        timeout: float = 90.0,
        max_chars: int = 3000,
    ):
        super().__init__(n_pairs=n_pairs, model=model)
        self.n_pairs, self.timeout, self.max_chars = n_pairs, timeout, max_chars
        self.base_url = (base_url or os.environ.get("DATAFOUNDRY_LLM_BASE", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("DATAFOUNDRY_LLM_KEY", "")
        self.model = model or os.environ.get("DATAFOUNDRY_LLM_MODEL", "")

    def validate(self) -> None:
        missing = [k for k, v in {"base_url": self.base_url, "model": self.model}.items() if not v]
        if missing:
            raise ValueError(
                f"qa_generate_mapper 未配置 {missing}:传参或设置 DATAFOUNDRY_LLM_BASE / "
                "DATAFOUNDRY_LLM_KEY / DATAFOUNDRY_LLM_MODEL"
            )

    def _call(self, text: str) -> list[dict]:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(
                {
                    "model": self.model,
                    "messages": [
                        {"role": "user", "content": _QA_PROMPT.format(n=self.n_pairs, text=text)}
                    ],
                    "temperature": 0.7,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        start, end = content.find("["), content.rfind("]")
        pairs = json.loads(content[start : end + 1])
        return [p for p in pairs if isinstance(p, dict) and p.get("q") and p.get("a")]

    def process(self, sample):  # pragma: no cover - expand 算子不走 1:1 流
        return sample

    def expand(self, sample: dict) -> list[dict]:
        self.validate()
        try:
            pairs = self._call(sample["text"][: self.max_chars])
        except Exception as exc:
            add_trace(sample, self.name, "kill", f"QA 生成失败: {exc!s:.100}")
            return []
        if not pairs:
            add_trace(sample, self.name, "kill", "LLM 未产出合法 QA 对")
            return []
        children = []
        for i, p in enumerate(pairs):
            q, a = str(p["q"]).strip(), str(p["a"]).strip()
            child = make_sample(
                f"问:{q}\n答:{a}",
                meta={**sample["meta"], "parent_id": sample["id"], "qa_index": i, "question": q, "answer": a},
            )
            child["trace"] = list(sample["trace"])
            add_trace(child, self.name, "edit", f"qa {i + 1}/{len(pairs)} from {sample['id']}")
            children.append(child)
        return children