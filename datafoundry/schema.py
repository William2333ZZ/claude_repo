"""统一样本模式与血缘。

样本是普通 dict(JSONL 友好),固定键:
  id     : str   样本唯一标识(内容指纹 + 随机后缀)
  text   : str   文本载荷(v0 只做文本;多模态载荷留 meta 扩展)
  meta   : dict  来源、参考答案等原始附带信息
  stats  : dict  算子写入的统计/打分(quality、pii_hits ...)
  trace  : list  逐样本血缘:每个算子对该样本做过什么

trace 事件: {"op": 名称, "action": pass|kill|edit|score, "detail": 说明}
被 kill 的样本不会消失,而是进 rejects 文件,trace 里带着死因——这就是审计凭证。
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Any

SAMPLE_KEYS = ("id", "text", "meta", "stats", "trace")


def content_fingerprint(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=12).hexdigest()


def make_sample(text: str, meta: dict[str, Any] | None = None, sample_id: str | None = None) -> dict:
    return {
        "id": sample_id or f"{content_fingerprint(text)}-{secrets.token_hex(3)}",
        "text": text,
        "meta": dict(meta or {}),
        "stats": {},
        "trace": [],
    }


def coerce_sample(obj: Any, text_key: str = "text") -> dict | None:
    """把任意 JSONL 行对象规整为标准样本;无法规整返回 None。"""
    if isinstance(obj, str):
        return make_sample(obj)
    if not isinstance(obj, dict):
        return None
    if all(k in obj for k in SAMPLE_KEYS):
        return obj
    text = obj.get(text_key)
    if not isinstance(text, str) or not text:
        return None
    meta = {k: v for k, v in obj.items() if k != text_key}
    return make_sample(text, meta=meta)


def add_trace(sample: dict, op: str, action: str, detail: str = "") -> None:
    sample.setdefault("trace", []).append({"op": op, "action": action, "detail": detail})
