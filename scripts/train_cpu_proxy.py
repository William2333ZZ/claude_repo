"""[M1-C1c/CPU 降规] 免费算力代理实验:Qwen2.5-0.5B LoRA CPU 训练 + 贪心评测,一条命令。

Owner 决策(2026-08-11):无 GPU 资源时以 0.5B 在 GitHub Actions 免费 CPU 跑器上做
四组相对比较;组间唯一变量仍是数据。公平性:所有组同一采样上限(种子洗牌取前 N)、
同一超参、同一评测协议。7B GPU 版(scripts/train_proxy.py)在资源到位后复跑校验。

  python scripts/train_cpu_proxy.py --data <run>/output.jsonl --out results/<arm>_cpu
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
import time

MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
TRAIN_CAP = 1000       # 组间一致的采样上限(CPU 时间预算)
MAX_LEN = 320
EPOCHS = 2
LR = 2e-4
SEED = 42
EVAL_MAX_NEW = 220


def load_pairs(path: pathlib.Path) -> list[dict]:
    pairs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        meta = json.loads(line).get("meta", {})
        if meta.get("question") and meta.get("answer"):
            pairs.append({"q": str(meta["question"]), "a": str(meta["answer"])})
    if not pairs:
        sys.exit("没有可训练的 QA 对(需要 meta.question/answer)")
    random.Random(SEED).shuffle(pairs)
    return pairs[:TRAIN_CAP]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--evalset", default="evalsets/math_zh_v1.jsonl")
    ap.add_argument("--skip-train", action="store_true", help="只评基座(对照基线)")
    args = ap.parse_args()

    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import (AutoModelForCausalLM, AutoTokenizer, Trainer,
                              TrainingArguments, set_seed)

    torch.set_num_threads(4)
    set_seed(SEED)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    tok = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModelForCausalLM.from_pretrained(MODEL, torch_dtype=torch.float32)

    t_train0 = time.time()
    if not args.skip_train:
        pairs = load_pairs(pathlib.Path(args.data))
        print(f"train pairs: {len(pairs)} (cap {TRAIN_CAP})")

        def encode(p):
            msgs = [{"role": "user", "content": p["q"]}]
            prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            prompt_ids = tok(prompt, add_special_tokens=False)["input_ids"]
            ans_ids = tok(p["a"] + tok.eos_token, add_special_tokens=False)["input_ids"]
            ids = (prompt_ids + ans_ids)[:MAX_LEN]
            labels = ([-100] * len(prompt_ids) + ans_ids)[:MAX_LEN]
            return {"input_ids": ids, "labels": labels}

        features = [encode(p) for p in pairs]

        def collate(batch):
            width = max(len(b["input_ids"]) for b in batch)
            pad = tok.pad_token_id or tok.eos_token_id
            return {
                "input_ids": torch.tensor([b["input_ids"] + [pad] * (width - len(b["input_ids"])) for b in batch]),
                "labels": torch.tensor([b["labels"] + [-100] * (width - len(b["labels"])) for b in batch]),
                "attention_mask": torch.tensor(
                    [[1] * len(b["input_ids"]) + [0] * (width - len(b["input_ids"])) for b in batch]
                ),
            }

        model = get_peft_model(
            model,
            LoraConfig(r=8, lora_alpha=16, lora_dropout=0.05, task_type="CAUSAL_LM",
                       target_modules=["q_proj", "k_proj", "v_proj", "o_proj"]),
        )
        trainer = Trainer(
            model=model,
            args=TrainingArguments(
                output_dir=str(out / "trainer"), num_train_epochs=EPOCHS, learning_rate=LR,
                per_device_train_batch_size=2, gradient_accumulation_steps=8,
                logging_steps=10, save_strategy="no", report_to=[], seed=SEED,
                lr_scheduler_type="cosine", warmup_ratio=0.03, use_cpu=True,
            ),
            train_dataset=features,
            data_collator=collate,
        )
        trainer.train()
        model = model.merge_and_unload()
    train_seconds = round(time.time() - t_train0)

    # ---- 评测(协议:evalsets/README.md;贪心,固定 prompt) ----
    model.eval()
    items = [json.loads(l) for l in pathlib.Path(args.evalset).read_text(encoding="utf-8").splitlines() if l.strip()]
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from eval_math import PROMPT, score

    outputs = {}
    t_eval0 = time.time()
    with torch.no_grad():
        for i, it in enumerate(items, 1):
            msgs = [{"role": "user", "content": PROMPT.format(question=it["question"])}]
            ids = tok.apply_chat_template(msgs, return_tensors="pt", add_generation_prompt=True)
            gen = model.generate(ids, max_new_tokens=EVAL_MAX_NEW, do_sample=False,
                                 pad_token_id=tok.pad_token_id or tok.eos_token_id)
            outputs[it["id"]] = tok.decode(gen[0][ids.shape[1]:], skip_special_tokens=True)
            print(f"\reval [{i}/{len(items)}]", end="", flush=True)
    print()
    report = score(items, outputs)
    report["train_meta"] = {
        "model": MODEL, "trained": not args.skip_train, "train_cap": TRAIN_CAP,
        "epochs": EPOCHS, "lr": LR, "max_len": MAX_LEN, "seed": SEED,
        "train_seconds": train_seconds, "eval_seconds": round(time.time() - t_eval0),
        "data": args.data,
    }
    (out / "eval_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "generations.jsonl").write_text(
        "\n".join(json.dumps({"id": k, "output": v}, ensure_ascii=False) for k, v in outputs.items()), encoding="utf-8")
    print(f"accuracy: {report['accuracy']} ({report['n_correct']}/{report['n_items']}) -> {out}/eval_report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
