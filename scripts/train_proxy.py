"""[M1-A2] 代理模型训练管线:run 产物 -> LLaMA-Factory LoRA 适配器,单命令。

  python scripts/train_proxy.py --data <run>/output.jsonl --out runs_proxy/exp1 [--dry-run]

职责:
1. 抽取 SFT 对:样本 meta.question/meta.answer(qa_generate_mapper 的产物即此格式;
   可用 --q-key/--a-key 改映射),转为 LLaMA-Factory alpaca 数据集
2. 生成固定超参的训练配置(实验组间唯一变量必须是数据 —— 超参写死在本文件顶部)
3. 调用 `llamafactory-cli train`;--dry-run 只做 1、2 并校验环境,无 GPU 也能验证

训练结束后用 vLLM 起服务 + scripts/eval_math.py --endpoint 评测(见 --help 尾注)。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import time

# ---- 固定超参 [M1-A2 验收:实验组间唯一变量是数据] ----
HPARAMS = {
    "model_name_or_path": "Qwen/Qwen2.5-7B-Instruct",
    "template": "qwen",
    "finetuning_type": "lora",
    "lora_target": "all",
    "lora_rank": 16,
    "lora_alpha": 32,
    "learning_rate": 1.0e-4,
    "num_train_epochs": 3.0,
    "cutoff_len": 2048,
    "per_device_train_batch_size": 2,
    "gradient_accumulation_steps": 8,
    "lr_scheduler_type": "cosine",
    "warmup_ratio": 0.03,
    "bf16": True,
    "logging_steps": 10,
    "save_strategy": "epoch",
    "plot_loss": True,
    "seed": 42,
}


def convert(data_path: pathlib.Path, q_key: str, a_key: str, out_dir: pathlib.Path) -> tuple[int, int]:
    rows, skipped = [], 0
    for line in data_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        s = json.loads(line)
        meta = s.get("meta", {})
        q, a = meta.get(q_key), meta.get(a_key)
        if not q or not a:
            skipped += 1
            continue
        rows.append({"instruction": str(q), "input": "", "output": str(a)})
    if not rows:
        sys.exit(f"没有可用 SFT 对:样本 meta 缺少 {q_key}/{a_key}(qa_generate_mapper 产物自带;"
                 f"其他来源用 --q-key/--a-key 指定)")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "data.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "dataset_info.json").write_text(
        json.dumps({"proxy_sft": {"file_name": "data.json"}}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return len(rows), skipped


def main() -> int:
    ap = argparse.ArgumentParser(
        description="run 产物 -> LoRA 适配器(固定超参)",
        epilog="评测:llamafactory-cli export 合并权重后 `vllm serve <merged>`,"
               "再 `python scripts/eval_math.py --endpoint http://127.0.0.1:8000/v1 --model <merged>`",
    )
    ap.add_argument("--data", required=True, help="run 的 output.jsonl")
    ap.add_argument("--out", required=True, help="输出目录(数据集/配置/适配器)")
    ap.add_argument("--q-key", default="question")
    ap.add_argument("--a-key", default="answer")
    ap.add_argument("--base-model", default=None, help="覆盖基座(默认 Qwen2.5-7B-Instruct)")
    ap.add_argument("--dry-run", action="store_true", help="只转换数据+生成配置+环境校验,不训练")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    ds_dir = out / "dataset"
    n_rows, n_skipped = convert(pathlib.Path(args.data), args.q_key, args.a_key, ds_dir)
    print(f"SFT 对: {n_rows}(跳过无 q/a 样本 {n_skipped})-> {ds_dir}/data.json")

    config = dict(HPARAMS)
    if args.base_model:
        config["model_name_or_path"] = args.base_model
    config.update(
        {
            "stage": "sft",
            "do_train": True,
            "dataset": "proxy_sft",
            "dataset_dir": str(ds_dir.resolve()),
            "output_dir": str((out / "adapter").resolve()),
        }
    )
    config_path = out / "train_config.yaml"  # JSON 是 YAML 子集,零依赖写出
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"训练配置(固定超参): {config_path}")

    cli = shutil.which("llamafactory-cli")
    if args.dry_run:
        print(f"[dry-run] llamafactory-cli: {cli or '未安装(pip install llamafactory)'}")
        print(f"[dry-run] 正式执行命令: llamafactory-cli train {config_path}")
        return 0
    if not cli:
        sys.exit("未找到 llamafactory-cli:pip install llamafactory(需要 GPU 环境)")
    t0 = time.time()
    ret = subprocess.run([cli, "train", str(config_path)]).returncode
    print(f"训练退出码 {ret},耗时 {time.time() - t0:.0f}s,适配器: {out / 'adapter'}")
    return ret


if __name__ == "__main__":
    sys.exit(main())
