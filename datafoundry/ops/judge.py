"""LLM 判审算子(llm 档:最贵,漏斗编译器会把它排到最后)。

后端为任意 OpenAI 兼容 endpoint,配置来自参数或环境变量:
  DATAFOUNDRY_LLM_BASE  (如 https://api.example.com/v1)
  DATAFOUNDRY_LLM_KEY
  DATAFOUNDRY_LLM_MODEL
未配置时 validate() 会明确报错,而不是运行到一半才失败。
判审结果写入 stats.judge_score / stats.judge_reason —— 这些标注同时是未来蒸馏小打分器的训练数据。
"""
from __future__ import annotations

import json
import os
import urllib.request

from datafoundry.registry import Op, register
from datafoundry.schema import add_trace

_PROMPT = """你是严格的数据质检员。给下面这条训练样本按标准打 1-5 分(5 最好):
标准: {criteria}
只输出 JSON: {{"score": <1-5>, "reason": "<20字内>"}}
样本:
{text}"""


@register
class LlmJudgeFilter(Op):
    name = "llm_judge_filter"
    kind = "filter"
    cost_tier = "llm"
    cost_per_1k = 15.0
    expected_retention = 0.6
    description = "LLM 判审过滤(OpenAI 兼容后端);score<min_score 则杀,标注留作蒸馏语料"

    def __init__(
        self,
        criteria: str = "事实清晰、信息量足、无广告水文",
        min_score: int = 3,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        timeout: float = 60.0,
        max_chars: int = 4000,
    ):
        super().__init__(criteria=criteria, min_score=min_score, model=model)
        self.criteria, self.min_score, self.timeout, self.max_chars = criteria, min_score, timeout, max_chars
        self.base_url = (base_url or os.environ.get("DATAFOUNDRY_LLM_BASE", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("DATAFOUNDRY_LLM_KEY", "")
        self.model = model or os.environ.get("DATAFOUNDRY_LLM_MODEL", "")

    def validate(self) -> None:
        missing = [k for k, v in {"base_url": self.base_url, "model": self.model}.items() if not v]
        if missing:
            raise ValueError(
                f"llm_judge_filter 未配置 {missing}:传参或设置 DATAFOUNDRY_LLM_BASE / "
                "DATAFOUNDRY_LLM_KEY / DATAFOUNDRY_LLM_MODEL"
            )

    def _call(self, text: str) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(
                {
                    "model": self.model,
                    "messages": [{"role": "user", "content": _PROMPT.format(criteria=self.criteria, text=text)}],
                    "temperature": 0,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        start, end = content.find("{"), content.rfind("}")
        return json.loads(content[start : end + 1])

    def process(self, sample):
        self.validate()
        try:
            verdict = self._call(sample["text"][: self.max_chars])
            score = int(verdict.get("score", 0))
            reason = str(verdict.get("reason", ""))[:80]
        except Exception as exc:  # 网络/解析失败:保守放行并记录,绝不静默丢数据
            sample["stats"]["judge_error"] = str(exc)[:120]
            add_trace(sample, self.name, "pass", f"judge error, kept: {exc!s:.80}")
            return sample
        sample["stats"]["judge_score"] = score
        sample["stats"]["judge_reason"] = reason
        if score < self.min_score:
            add_trace(sample, self.name, "kill", f"judge {score}<{self.min_score}: {reason}")
            return None
        add_trace(sample, self.name, "pass", f"judge {score}")
        return sample
