"""执行器:JSONL 进 → 逐算子全量遍历 → output/rejects/manifest 出。

产物(run 目录下):
  output.jsonl   幸存样本(含 stats 与完整 trace 血缘)
  rejects.jsonl  被杀样本 + 死因(审计凭证,也是回收再利用的原料)
  manifest.json  每算子进出计数/耗时/留存率、总留存曲线、实际成本估计

v0 为单机内存实现(默认上限 50 万样本,超限明确报错);
执行接口与算子协议不含单机假设,Ray 分片执行器是后续里程碑。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from datafoundry.pipeline import build_ops, funnel_compile, validate_steps
from datafoundry.registry import OPS
from datafoundry.schema import coerce_sample

MAX_SAMPLES = 500_000


def load_jsonl(path: str | Path, text_key: str = "text", max_samples: int = MAX_SAMPLES) -> list[dict]:
    samples: list[dict] = []
    skipped = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            s = coerce_sample(obj, text_key=text_key)
            if s is None:
                skipped += 1
                continue
            samples.append(s)
            if len(samples) > max_samples:
                raise ValueError(f"数据集超过 v0 单机上限 {max_samples} 样本;请拆分或等待分布式执行器")
    if skipped:
        samples_meta = {"skipped_lines": skipped}
        if samples:
            samples[0].setdefault("meta", {}).setdefault("_load", samples_meta)
    return samples


def run_pipeline(
    dataset_path: str | Path,
    steps: list[dict],
    out_dir: str | Path,
    text_key: str = "text",
    funnel: bool = True,
) -> dict:
    errors = validate_steps(steps)
    if errors:
        raise ValueError("流水线不合法: " + "; ".join(errors))

    ordered, moves = funnel_compile(steps) if funnel else (steps, [])
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    samples = load_jsonl(dataset_path, text_key=text_key)
    n_in = len(samples)
    per_op: list[dict] = []
    rejects_path = out / "rejects.jsonl"

    with open(rejects_path, "w", encoding="utf-8") as rej:
        for step, op in build_ops(ordered):
            t0 = time.time()
            survivors: list[dict] = []
            killed = 0
            batch_in = len(samples)
            it = iter(samples)
            for original, result in zip(list(samples), op.process_batch(it)):
                if result is None:
                    killed += 1
                    rej.write(json.dumps(original, ensure_ascii=False) + "\n")
                else:
                    survivors.append(result)
            samples = survivors
            per_op.append(
                {
                    "op": step["op"],
                    "params": step.get("params", {}),
                    "cost_tier": OPS[step["op"]].cost_tier,
                    "in": batch_in,
                    "out": len(samples),
                    "killed": killed,
                    "retention": round(len(samples) / batch_in, 4) if batch_in else 1.0,
                    "seconds": round(time.time() - t0, 3),
                    "est_cost": round(batch_in / 1000.0 * OPS[step["op"]].cost_per_1k, 4),
                }
            )

    out_path = out / "output.jsonl"
    with open(out_path, "w", encoding="utf-8") as fh:
        for s in samples:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")

    manifest = {
        "dataset": str(dataset_path),
        "steps_requested": steps,
        "steps_executed": [s["op"] for s in ordered],
        "funnel_moves": moves,
        "n_in": n_in,
        "n_out": len(samples),
        "retention": round(len(samples) / n_in, 4) if n_in else 1.0,
        "est_cost_total": round(sum(o["est_cost"] for o in per_op), 4),
        "per_op": per_op,
        "output": str(out_path),
        "rejects": str(rejects_path),
    }
    with open(out / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return manifest
