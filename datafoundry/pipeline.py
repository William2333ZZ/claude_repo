"""流水线表达 + 漏斗编译器 + 成本预估。

流水线是平台自有的表达(不写在任何第三方框架的语言里):
  steps = [{"op": "length_filter", "params": {"min_len": 30}, "pin": false}, ...]

漏斗编译(funnel compile):按成本档稳定排序 heuristic < model < llm,
让便宜算子先杀掉大部分样本,昂贵算子只看幸存者;pin=true 的步骤保持原位不动。
成本预估同时给出「朴素顺序 vs 漏斗顺序」的对比——省了多少钱是一等公民指标。
"""
from __future__ import annotations

from typing import Any

from datafoundry.registry import OPS, TIER_RANK, Op, create_op


def validate_steps(steps: list[dict]) -> list[str]:
    """返回错误列表;空列表 = 合法。"""
    errors: list[str] = []
    if not steps:
        return ["流水线为空"]
    for i, step in enumerate(steps):
        name = step.get("op")
        if not name or name not in OPS:
            errors.append(f"step[{i}]: 未知算子 {name!r}")
            continue
        try:
            op = create_op(name, **step.get("params", {}))
        except (TypeError, AssertionError) as exc:
            errors.append(f"step[{i}] {name}: 参数错误 {exc}")
            continue
        validate = getattr(op, "validate", None)
        if callable(validate):
            try:
                validate()
            except ValueError as exc:
                errors.append(f"step[{i}] {name}: {exc}")
    return errors


def funnel_compile(steps: list[dict]) -> tuple[list[dict], list[str]]:
    """稳定排序:同档保持相对顺序;pin 的步骤固定在原索引。返回(新顺序, 调整说明)。"""
    indexed = list(enumerate(steps))
    pinned = {i: s for i, s in indexed if s.get("pin")}
    movable = [(i, s) for i, s in indexed if not s.get("pin")]
    movable.sort(key=lambda t: (TIER_RANK[OPS[t[1]["op"]].cost_tier], t[0]))
    slots: list[tuple[int, dict] | None] = [None] * len(steps)
    for i, s in pinned.items():
        slots[i] = (i, s)
    it = iter(movable)
    for j in range(len(steps)):
        if slots[j] is None:
            slots[j] = next(it)
    result = [s for _, s in slots]  # type: ignore[misc]
    moves = [
        f"{s['op']}({OPS[s['op']].cost_tier}) step[{orig}] -> step[{j}]"
        for j, (orig, s) in enumerate(slots)  # type: ignore[misc]
        if orig != j
    ]
    return result, moves


def _walk_cost(steps: list[dict], n_samples: int) -> tuple[float, list[dict]]:
    total, n, detail = 0.0, float(n_samples), []
    for step in steps:
        cls = OPS[step["op"]]
        cost = n / 1000.0 * cls.cost_per_1k
        total += cost
        detail.append(
            {
                "op": step["op"],
                "cost_tier": cls.cost_tier,
                "est_samples_in": int(n),
                "est_cost": round(cost, 4),
            }
        )
        n *= cls.expected_retention
    return round(total, 4), detail


def estimate(steps: list[dict], n_samples: int) -> dict[str, Any]:
    """成本预估:漏斗顺序 vs 用户原始顺序,以及预期留存。"""
    ordered, moves = funnel_compile(steps)
    naive_cost, _ = _walk_cost(steps, n_samples)
    funnel_cost, detail = _walk_cost(ordered, n_samples)
    retention = 1.0
    for step in ordered:
        retention *= OPS[step["op"]].expected_retention
    return {
        "n_samples": n_samples,
        "funnel_order": [s["op"] for s in ordered],
        "reorder_moves": moves,
        "est_cost_naive": naive_cost,
        "est_cost_funnel": funnel_cost,
        "est_savings": round(naive_cost - funnel_cost, 4),
        "est_retention": round(retention, 4),
        "est_samples_out": int(n_samples * retention),
        "per_op": detail,
        "note": "成本为抽象单位(按元量级标定的算子默认值);留存率为算子声明的先验,跑过的真实留存见 run manifest",
    }


def build_ops(steps: list[dict]) -> list[tuple[dict, Op]]:
    return [(s, create_op(s["op"], **s.get("params", {}))) for s in steps]
