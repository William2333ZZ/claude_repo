"""算子协议与注册表。

每个算子声明:
  kind          : filter(判死) | mapper(改写) | score(打分) | dedup(语料级去重) | verify(硬验证)
                  | expand(1→N 扩增:分块/合成,子样本挂父血缘)
  cost_tier     : heuristic | model | llm  —— 漏斗编译器按此排序
  cost_per_1k   : 每千样本成本(抽象单位,默认按人民币元的量级标定)
  expected_retention : 预期产出倍率;filter 为留存率(0-1],expand 可 >1(每样本产 N 条)
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
    # [DDD/coeffect] 算子间协作的显式契约:对上游 stats 的需求与自身供给。
    # 组装期校验,缺即拒编译(fail loud)——依赖是声明出来的,不是约定俗成的。
    requires_stats: tuple[str, ...] = ()
    provides_stats: tuple[str, ...] = ()

    def __init__(self, **params):
        self.params = params

    @abstractmethod
    def process(self, sample: dict) -> dict | None:
        """返回样本(可修改)或 None(kill)。kill 前必须写 trace。"""

    def process_batch(self, samples: Iterable[dict]) -> Iterator[dict | None]:
        """语料级算子(去重)覆写此方法;默认逐条。yield None 表示该样本被 kill。"""
        for s in samples:
            yield self.process(s)

    def expand(self, sample: dict) -> list[dict]:
        """kind=="expand" 的算子覆写:一条父样本产出 N 条子样本(可为空,空则父样本进 rejects)。
        子样本须带 meta.parent_id 与继承的 trace,保证血缘可回溯。"""
        raise NotImplementedError(f"{self.name} 未实现 expand")

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
            "requires_stats": list(cls.requires_stats),
            "provides_stats": list(cls.provides_stats),
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
