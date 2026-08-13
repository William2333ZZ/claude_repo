"""[M1-C1] 7B 复验:Qwen2.5-7B-Instruct QLoRA(4bit)训练 + math_zh 贪心评测。

协议与 CPU 代理(train_cpu_proxy.py)完全一致——同 QA 对构造、同 LoRA 超参
(r8/α16/lr2e-4/2epoch/cap1000/MAX_LEN320)、同 TRAIN_SEED 环境覆盖、同贪心评测判分;
唯一差异是底座换 7B + 4bit 量化(单卡 16GB 可训,Kaggle P100/T4 即够)。
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import pathlib
import random
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from eval_math import PROMPT, score  # noqa: E402  协议单一来源(含判分归一,勿手写比较)

MODEL = os.environ.get("TRAIN_MODEL", "Qwen/Qwen2.5-7B-Instruct")
SEED = int(os.environ.get("TRAIN_SEED", "42"))
TRAIN_CAP = 1000
MAX_LEN = 320
HPARAMS = dict(
    num_train_epochs=2, learning_rate=2e-4, per_device_train_batch_size=1,
    gradient_accumulation_steps=8, warmup_ratio=0.03, logging_steps=10,
    save_strategy="no", report_to=[],
)  # 精度(bf16/fp16)在 main 里按硬件探测后注入


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
    ap.add_argument("--skip-train", action="store_true", help="只评基座(7B 对照基线)")
    args = ap.parse_args()

    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (AutoModelForCausalLM, AutoTokenizer,
                              BitsAndBytesConfig, Trainer, TrainingArguments, set_seed)

    set_seed(SEED)
    bf16_ok = torch.cuda.is_available() and torch.cuda.get_device_capability() >= (8, 0)
    compute_dtype = torch.bfloat16 if bf16_ok else torch.float16
    HPARAMS["bf16" if bf16_ok else "fp16"] = True
    cap = torch.cuda.get_device_capability() if torch.cuda.is_available() else (0, 0)
    use_4bit = not cap or cap >= (7, 0)
    print(f"GPU capability={cap} bf16={bf16_ok} compute_dtype={compute_dtype} 4bit={use_4bit}")
    if not use_4bit:
        print(f"GPU sm_{cap[0]}{cap[1]} 不支持 bitsandbytes 4bit:走无量化 fp16 路径(建议 ≤3B 模型)")
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    tok = AutoTokenizer.from_pretrained(MODEL)
    if use_4bit:
        bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                 bnb_4bit_compute_dtype=compute_dtype, bnb_4bit_use_double_quant=True)
        model = AutoModelForCausalLM.from_pretrained(MODEL, quantization_config=bnb, device_map="auto")
    else:
        model = AutoModelForCausalLM.from_pretrained(MODEL, torch_dtype=compute_dtype, device_map="auto")

    t0 = time.time()
    if not args.skip_train:
        pairs = load_pairs(pathlib.Path(args.data))
        print(f"train pairs: {len(pairs)} (cap {TRAIN_CAP}) | seed={SEED} | model={MODEL}")
        if use_4bit:
            model = prepare_model_for_kbit_training(model)
        lora = LoraConfig(r=8, lora_alpha=16, lora_dropout=0.05, task_type="CAUSAL_LM",
                          target_modules=["q_proj", "k_proj", "v_proj", "o_proj"])
        model = get_peft_model(model, lora)

        def encode(p):
            msgs = [{"role": "user", "content": p["q"]}]
            prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            prompt_ids = tok(prompt, add_special_tokens=False)["input_ids"]
            ans_ids = tok(p["a"] + tok.eos_token, add_special_tokens=False)["input_ids"]
            ids = (prompt_ids + ans_ids)[:MAX_LEN]
            labels = ([-100] * len(prompt_ids) + ans_ids)[:MAX_LEN]
            pad = MAX_LEN - len(ids)
            return {"input_ids": ids + [tok.pad_token_id or tok.eos_token_id] * pad,
                    "labels": labels + [-100] * pad,
                    "attention_mask": [1] * len(ids) + [0] * pad}

        ds = [encode(p) for p in pairs]
        allowed = inspect.signature(TrainingArguments.__init__).parameters
        kw = {k: v for k, v in HPARAMS.items() if k in allowed}
        dropped = sorted(set(HPARAMS) - set(kw))
        if dropped:
            print(f"[compat] 当前 transformers 不支持并已丢弃: {dropped}")
        targs = TrainingArguments(output_dir=str(out / "ckpt"), **kw)

        import torch as _t

        class L(_t.utils.data.Dataset):
            def __len__(self):
                return len(ds)

            def __getitem__(self, i):
                return {k: _t.tensor(v) for k, v in ds[i].items()}

        Trainer(model=model, args=targs, train_dataset=L()).train()
        model.eval()
    train_seconds = round(time.time() - t0, 1)

    # ---- 评测(协议:evalsets/README.md;贪心,固定 prompt;判分调 eval_math.score,不手写)----
    items = [json.loads(l) for l in pathlib.Path(args.evalset).read_text(encoding="utf-8").splitlines() if l.strip()]
    outputs = {}
    for i, item in enumerate(items, 1):
        msgs = [{"role": "user", "content": PROMPT.format(question=item["question"])}]
        prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        enc = tok(prompt, return_tensors="pt").to(model.device)
        import torch as _t2
        with _t2.no_grad():
            out_ids = model.generate(**enc, max_new_tokens=256, do_sample=False,
                                     pad_token_id=tok.pad_token_id or tok.eos_token_id)
        outputs[item["id"]] = tok.decode(out_ids[0][enc["input_ids"].shape[1]:], skip_special_tokens=True)
        print(f"eval [{i}/{len(items)}]", flush=True)

    scored = score(items, outputs)
    acc, correct = scored["accuracy"], scored["n_correct"]
    import transformers as _tfm
    report = {"accuracy": acc, "correct": correct, "total": scored["n_items"], "model": MODEL,
              "seed": SEED, "train_seconds": train_seconds, "skip_train": args.skip_train,
              "torch": torch.__version__, "transformers": _tfm.__version__}
    (out / "eval_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(out / "generations.jsonl", "w", encoding="utf-8") as fh:
        for r in scored["results"]:
            fh.write(json.dumps({"id": r["id"], "output": outputs.get(r["id"], ""), "got": r["got"],
                                 "want": r["want"], "ok": r["correct"]}, ensure_ascii=False) + "\n")
    print(f"accuracy: {acc} ({correct}/{len(items)}) -> {out / 'eval_report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
