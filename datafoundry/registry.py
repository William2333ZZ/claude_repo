"""算子协议与注册表。

每个算子声明:
  kind          : filter(判死) | mapper(改写) | score(打分) | dedup(语料级去重) | verify(硬验证)
  cost_tier     : heuristic | model | llm  —— 漏斗编译器按此排序
  cost_per_1k   : 每千样本成本(抽象单位,默认按人民币元的量级标定)
  expected_retention : 预期留存率(0-1],成本预估用;mapper/score 恒为 1
"""
from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from typing import Iterable, Iterator

TIER_RANK = {"heuristic": 0, "model": 1, "llm": 2}


class Op(ABC):
    name: str = ""
    kind: str = "filter"
    cost_tier: str = "heuristic"
    cost_per_1k: float = 0.001
    expected_retention: float = 1.0
    description: str = ""

    def __init__(self, **params):
        self.params = params

    @abstractmethod
    def process(self, sample: dict) -> dict | None:
        """返回样本(可修改)或 None(kill)。kill 前必须写 trace。"""

    def process_batch(self, samples: Iterable[dict]) -> Iterator[dict | None]:
        """语料级算子(去重)覆写此方法;默认逐条。yield None 表示该样本被 kill。"""
        for s in samples:
            yield self.process(s)

    @classmethod
    def spec(cls) -> dict:
        sig = inspect.signature(cls.__init__)
        params = {
            n: (None if p.default is inspect.Parameter.empty else p.default)
            for n, p in sig.parameters.items()
            if n not in ("self", "params") and p.kind is not inspect.Parameter.VAR_KEYWORD
        }
        return {
            "name": cls.name,
            "kind": cls.kind,
            "cost_tier": cls.cost_tier,
            "cost_per_1k": cls.cost_per_1k,
            "expected_retention": cls.expected_retention,
            "description": cls.description,
            "params": params,
        }


OPS: dict[str, type[Op]] = {}


def register(cls: type[Op]) -> type[Op]:
    assert cls.name, f"{cls} 缺少 name"
    assert cls.cost_tier in TIER_RANK, f"{cls.name} 非法 cost_tier"
    OPS[cls.name] = cls
    return cls


def create_op(name: str, **params) -> Op:
    if name not in OPS:
        raise KeyError(f"未知算子: {name}(可用: {sorted(OPS)})")
    return OPS[name](**params)


def catalog() -> list[dict]:
    return [OPS[n].spec() for n in sorted(OPS)]
