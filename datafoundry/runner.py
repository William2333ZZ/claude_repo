"""执行器:JSONL 进 → 分块流式经全部算子 → output/rejects/manifest 出。

产物(run 目录下):
  output.jsonl        幸存样本(含 stats 与完整 trace 血缘)
  rejects.jsonl       被杀样本 + 死因(审计凭证,也是回收再利用的原料)
  judge_labels.jsonl  LLM 判审标注(text/score/reason)——蒸馏小打分器的训练原料[M1-B1]
  manifest.json       每算子进出计数/耗时/留存率、总留存曲线、实际成本估计

算子形态:
  1:1(filter/mapper/score/verify/dedup) 走配对流;kind=="expand" 的扩增算子
  (分块、QA 生成)走 1→N 流,子样本经 meta.parent_id 挂血缘。

[M2-E2] 流式 I/O:输入按 chunk(默认 2,000,DATAFOUNDRY_CHUNK_SIZE 可调)读入,
每块过完整算子链后立即落盘——峰值内存 = O(chunk + 去重状态),与数据集大小解耦。
等价性依据:所有算子都是"逐样本、按序、内部状态"的流式转换器,算子实例跨块存活,
分块与全量结果一致(tests/test_streaming.py 实证)。契约:output.jsonl **保序**;
rejects.jsonl 是**无序审计袋**(写入交错随分块变化,内容多重集不变);样本 id 含随机
后缀,任意两次运行必不同(与分块无关)。minhash 去重为增量语义(见 dedup.py 注释)。
50 万单机硬上限随之取消(DATAFOUNDRY_MAX_SAMPLES 可选恢复护栏);
API 层(server 上传计数/预览)仍用全量 load_jsonl,属已知边界,随 #14 分片执行器统一。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from datafoundry.pipeline import build_ops, funnel_compile, validate_steps
from datafoundry.registry import OPS
from datafoundry.schema import coerce_sample

MAX_SAMPLES = 500_000  # 仅供 load_jsonl(API 层)沿用;流式主链不再受此限
DEFAULT_CHUNK = 2_000


def load_jsonl(path: str | Path, text_key: str = "text", max_samples: int = MAX_SAMPLES) -> list[dict]:
    """全量载入(API 层计数/预览与训练脚本用;精炼主链走 run_pipeline 的流式读)。"""
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
                raise ValueError(f"数据集超过单机上限 {max_samples} 样本;请拆分或等待分布式执行器")
    if skipped:
        samples_meta = {"skipped_lines": skipped}
        if samples:
            samples[0].setdefault("meta", {}).setdefault("_load", samples_meta)
    return samples


def _iter_chunks(path: str | Path, text_key: str, chunk_size: int, stats: dict):
    """流式读:产出样本块;坏行/不合规行计入 stats['skipped'],超硬护栏即报错。"""
    cap = int(os.environ.get("DATAFOUNDRY_MAX_SAMPLES", "0"))
    chunk: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                stats["skipped"] += 1
                continue
            s = coerce_sample(obj, text_key=text_key)
            if s is None:
                stats["skipped"] += 1
                continue
            stats["n_in"] += 1
            if cap and stats["n_in"] > cap:
                raise ValueError(f"数据集超过 DATAFOUNDRY_MAX_SAMPLES={cap} 护栏")
            chunk.append(s)
            if len(chunk) >= chunk_size:
                yield chunk
                chunk = []
    if chunk:
        yield chunk


def run_pipeline(
    dataset_path: str | Path,
    steps: list[dict],
    out_dir: str | Path,
    text_key: str = "text",
    funnel: bool = True,
    chunk_size: int | None = None,
) -> dict:
    errors = validate_steps(steps)
    if errors:
        raise ValueError("流水线不合法: " + "; ".join(errors))

    ordered, moves = funnel_compile(steps) if funnel else (steps, [])
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    chunk_size = chunk_size or int(os.environ.get("DATAFOUNDRY_CHUNK_SIZE", str(DEFAULT_CHUNK)))

    ops = build_ops(ordered)  # 算子实例只建一次:去重等内部状态跨块存活(等价性关键)
    acc = [
        {"op": step["op"], "params": step.get("params", {}), "cost_tier": OPS[step["op"]].cost_tier,
         "in": 0, "out": 0, "killed": 0, "seconds": 0.0}
        for step, _ in ops
    ]
    read_stats = {"n_in": 0, "skipped": 0}
    n_out = 0
    judge_count = 0
    rejects_path = out / "rejects.jsonl"
    out_path = out / "output.jsonl"
    labels_path = out / "judge_labels.jsonl"
    labels_fh = None

    def harvest_judge(sample: dict) -> None:
        # [M1-B1] judge 打分自动积累为蒸馏训练集,幸存与被杀样本都收(文件懒开:无判审则不建)
        nonlocal labels_fh, judge_count
        if "judge_score" in sample.get("stats", {}):
            if labels_fh is None:
                labels_fh = open(labels_path, "w", encoding="utf-8")
            labels_fh.write(json.dumps(
                {"text": sample["text"], "score": sample["stats"]["judge_score"],
                 "reason": sample["stats"].get("judge_reason", "")}, ensure_ascii=False) + "\n")
            judge_count += 1

    try:
        with open(rejects_path, "w", encoding="utf-8") as rej, open(out_path, "w", encoding="utf-8") as out_fh:

            def reject(sample: dict) -> None:
                harvest_judge(sample)
                rej.write(json.dumps(sample, ensure_ascii=False) + "\n")

            for samples in _iter_chunks(dataset_path, text_key, chunk_size, read_stats):
                for idx, (step, op) in enumerate(ops):
                    t0 = time.time()
                    survivors: list[dict] = []
                    killed = 0
                    batch_in = len(samples)
                    if op.kind == "expand":  # 1→N 扩增:分块/合成,子样本挂父血缘
                        for s in samples:
                            children = op.expand(s)
                            if children:
                                survivors.extend(children)
                            else:
                                killed += 1
                                reject(s)
                    else:  # 1:1 配对流
                        it = iter(samples)
                        for original, result in zip(list(samples), op.process_batch(it)):
                            if result is None:
                                killed += 1
                                reject(original)
                            else:
                                survivors.append(result)
                    a = acc[idx]
                    a["in"] += batch_in
                    a["out"] += len(survivors)
                    a["killed"] += killed
                    a["seconds"] += time.time() - t0
                    samples = survivors
                for s in samples:  # 本块幸存者立即落盘,不跨块滞留
                    harvest_judge(s)
                    out_fh.write(json.dumps(s, ensure_ascii=False) + "\n")
                    n_out += 1
    finally:
        if labels_fh is not None:
            labels_fh.close()

    n_in = read_stats["n_in"]
    per_op = [
        {"op": a["op"], "params": a["params"], "cost_tier": a["cost_tier"],
         "in": a["in"], "out": a["out"], "killed": a["killed"],
         "retention": round(a["out"] / a["in"], 4) if a["in"] else 1.0,
         "seconds": round(a["seconds"], 3),
         "est_cost": round(a["in"] / 1000.0 * OPS[a["op"]].cost_per_1k, 4)}
        for a in acc
    ]
    manifest = {
        "dataset": str(dataset_path),
        "steps_requested": steps,
        "steps_executed": [s["op"] for s in ordered],
        "funnel_moves": moves,
        "n_in": n_in,
        "n_out": n_out,
        "retention": round(n_out / n_in, 4) if n_in else 1.0,
        "est_cost_total": round(sum(o["est_cost"] for o in per_op), 4),
        "per_op": per_op,
        "output": str(out_path),
        "rejects": str(rejects_path),
        "chunk_size": chunk_size,
    }
    if read_stats["skipped"]:
        manifest["skipped_lines"] = read_stats["skipped"]
    if judge_count:
        manifest["judge_labels"] = str(labels_path)
        manifest["judge_labels_count"] = judge_count
    with open(out / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return manifest
