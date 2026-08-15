"""[#24 S1] 语料准备:复现 S0 抽样集,派生 raw/refined 两个训练视图。

两段各司其职:
  1. 验证段——逐字复刻 real-corpus.yml 的下载/抽样/text 拼接,对结果 sha256 做硬断言
     (须以 7fbe8eb51bdf 开头,即 S0 报告(experiments/m2_real/…/autopsy.json)记录的
     抽样集指纹)。断言过了,才敢说这是"同一个 19,999 条"——不比对就写"同一语料"是自欺。
  2. 训练视图段——同一 rows/picked(内存复用,免二次网络请求),写成
     train_cpu_proxy.load_pairs() 认得的标准样本 schema(id/text/meta/stats/trace,
     meta={question,answer}):
     raw.jsonl     = 全部条目直接 make_sample(),未过滤(**不是**空 steps 跑管线——
                     run_pipeline([]) 会拒绝"流水线为空",本地先踩过这个坑)
     refined.jsonl = 同一批样本经 v3.1 验证器链(与 real-corpus.yml 完全同一 STEPS)
                     的 survivors——kernel 内部走 coerce_sample,顶层 question/answer
                     正确落进 meta(早前误用「meta 套 meta」写法测出会被吞,已改)

用法(CI 里跑,产物留在 /tmp,不进仓库——语料文本纪律不因训练用途松动):
  python experiments/s1_real_math/prepare_corpus.py --out /tmp/s1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
import sys
import urllib.request

import datafoundry.kernel.ops  # noqa: F401  导入即注册内置算子
from datafoundry.kernel.runner import run_pipeline
from datafoundry.kernel.schema import make_sample

REFINE_STEPS = [  # 与 real-corpus.yml 完全一致的 v3.1 验证器链(refined 视图的定义)
    {"op": "length_filter", "params": {"min_len": 10}},
    {"op": "symbol_ratio_filter"},
    {"op": "repetition_filter"},
    {"op": "exact_dedup"},
    {"op": "minhash_dedup"},
    {"op": "arithmetic_consistency_verify"},
]

DATASET = "BelleGroup/school_math_0.25M"
SAMPLE_N = 20000
SEED = 42  # 与 S0 一致:语料抽样种子是复现锚点,不是训练超参,不做成 CLI 参数
EXPECTED_SHA_PREFIX = "7fbe8eb51bdf"  # 出处:experiments/m2_real/BelleGroup__school_math_0.25M/autopsy.json


def fetch_rows() -> list[dict]:
    api = f"https://huggingface.co/api/datasets/{DATASET}"
    info = json.loads(urllib.request.urlopen(api, timeout=60).read())
    files = [s["rfilename"] for s in info.get("siblings", []) if s["rfilename"].endswith((".json", ".jsonl"))]
    if not files:
        sys.exit(f"数据集无 json 文件: {[s['rfilename'] for s in info.get('siblings', [])][:20]}")
    fname = sorted(files, key=len)[0]
    url = f"https://huggingface.co/datasets/{DATASET}/resolve/main/{fname}"
    print("downloading:", url)
    tmp = pathlib.Path("/tmp/s1_corpus_raw")
    urllib.request.urlretrieve(url, tmp)
    rows: list[dict] = []
    with open(tmp, encoding="utf-8") as fh:
        head = fh.read(1)
        fh.seek(0)
        if head == "[":
            rows = json.load(fh)
        else:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    print("total rows:", len(rows))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="输出目录(建议 /tmp 下,不落仓库)")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows = fetch_rows()
    rng = random.Random(SEED)
    n = min(SAMPLE_N, len(rows))
    picked = rng.sample(range(len(rows)), n)

    # ---- 段 1:逐字复刻 real-corpus.yml 的写法,验证同一抽样集 ----
    verify_path = out / "_verify_corpus.jsonl"
    with open(verify_path, "w", encoding="utf-8") as vf:
        for i in picked:
            r = rows[i]
            text = (str(r.get("instruction", "")) + "\n" + str(r.get("output", ""))).strip()
            if text:
                vf.write(json.dumps({"text": text, "meta": {"src_idx": i}}, ensure_ascii=False) + "\n")
    sha = hashlib.sha256(verify_path.read_bytes()).hexdigest()
    print("corpus sha256:", sha)
    if not sha.startswith(EXPECTED_SHA_PREFIX):
        sys.exit(
            f"抽样集指纹不符:got {sha[:12]}, want {EXPECTED_SHA_PREFIX}——"
            "S0 与 S1 用的不是同一批数据,先查 HF 源文件是否变化再继续"
        )
    print(f"✓ 抽样集指纹核对通过({sha[:12]}…),与 S0(experiments/m2_real)同一语料")
    verify_path.unlink()  # 验证用完即删,不留在产物里(避免与 raw.jsonl 混淆)

    # ---- 段 2:训练视图(标准样本 schema,question/answer 落 meta) ----
    raw_path = out / "raw.jsonl"
    n_written = 0
    with open(raw_path, "w", encoding="utf-8") as rf:
        for i in picked:
            r = rows[i]
            instruction, response = str(r.get("instruction", "")), str(r.get("output", ""))
            text = (instruction + "\n" + response).strip()
            if text and instruction.strip() and response.strip():
                sample = make_sample(text, meta={"question": instruction, "answer": response})
                rf.write(json.dumps(sample, ensure_ascii=False) + "\n")
                n_written += 1
    print(f"raw.jsonl: {n_written} 条(question/answer 均非空,已是标准样本 schema)")

    # ---- 段 3:refined 视图——raw 过 v3.1 验证器链后的 survivors ----
    refined_dir = out / "_refined_run"
    m = run_pipeline(str(raw_path), REFINE_STEPS, str(refined_dir))
    refined_path = out / "refined.jsonl"
    (refined_dir / "output.jsonl").rename(refined_path)
    print(f"refined.jsonl: {m['n_out']} 条(留存 {m['retention'] * 100:.1f}%,"
          f"S0 报告参照值 94.6%——数字不同就说明抽样或验证器版本对不上,先停下核查)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
