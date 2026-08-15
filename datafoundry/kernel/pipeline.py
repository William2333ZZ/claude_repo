"""流水线表达 + 漏斗编译器 + 成本预估。

流水线是平台自有的表达(不写在任何第三方框架的语言里):
  steps = [{"op": "length_filter", "params": {"min_len": 30}, "pin": false}, ...]

漏斗编译(funnel compile):按成本档稳定排序 heuristic < model < llm,
让便宜算子先杀掉大部分样本,昂贵算子只看幸存者;pin=true 的步骤保持原位不动。
成本预估同时给出「朴素顺序 vs 漏斗顺序」的对比——省了多少钱是一等公民指标。
"""
from __future__ import annotations

from typing import Any

from datafoundry.kernel.registry import OPS, TIER_RANK, Op, create_op


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
    if not errors:  # 算子都合法后,再按将要执行的顺序校验 stats 依赖(fail loud)
        errors.extend(stat_flow_errors(funnel_compile(steps)[0]))
    return errors


def stat_flow_errors(ordered: list[dict]) -> list[str]:
    """[DDD/coeffect] 组装期依赖校验:算子声明的 requires_stats 必须由排在其前的算子供给。
    三种失败各给指路的错误:顺序颠倒 / 链中缺提供者 / 注册表根本无提供者——缺即拒编译,
    不靠运行时"无分放行"的沉默宽容(dsh「缺服务=组装错误,加载即响亮失败」同款)。"""
    errors: list[str] = []
    provided: set[str] = set()
    for idx, step in enumerate(ordered):
        cls = OPS[step["op"]]
        for key in cls.requires_stats:
            if key in provided:
                continue
            if any(key in OPS[t["op"]].provides_stats for t in ordered[idx + 1:]):
                errors.append(
                    f"{step['op']} 需要上游 stats[{key!r}],但提供者排在它之后——调整顺序或检查 pin")
            else:
                names = sorted(n for n, c in OPS.items() if key in c.provides_stats)
                errors.append(
                    f"{step['op']} 需要上游 stats[{key!r}]:请先加入 {'/'.join(names)}" if names
                    else f"{step['op']} 声明依赖 stats[{key!r}],但注册表中无算子提供它")
        provided |= set(cls.provides_stats)
    return errors


def funnel_compile(steps: list[dict]) -> tuple[list[dict], list[str]]:
    """稳定排序:同档保持相对顺序;pin 的步骤固定在原索引。返回(新顺序, 调整说明)。

    依赖约束:声明 requires_stats 的步骤,其排序档位提升到链中提供者的档位——稳定排序随即
    保证"用户原序正确的链,编译后依然正确"(如 llm 档打分器 + heuristic 档阈值器不会被漏斗
    拆散)。用户原序本就颠倒或 pin 造成的冲突**不静默修**,由 stat_flow_errors 响亮失败。"""
    indexed = list(enumerate(steps))
    pinned = {i: s for i, s in indexed if s.get("pin")}
    movable = [(i, s) for i, s in indexed if not s.get("pin")]
    rank = {i: TIER_RANK[OPS[s["op"]].cost_tier] for i, s in indexed}
    for _ in range(len(steps)):  # 依赖档位提升至不动点(链极短,平方界足够)
        changed = False
        for i, s in indexed:
            for key in OPS[s["op"]].requires_stats:
                for i2, s2 in indexed:
                    if key in OPS[s2["op"]].provides_stats and rank[i] < rank[i2]:
                        rank[i] = rank[i2]
                        changed = True
        if not changed:
            break
    movable.sort(key=lambda t: (rank[t[0]], t[0]))
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
