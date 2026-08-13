"""[M1-C1c] 单组执行器:跑一条产线并按真值标签出杀伤分析表。

  python experiments/m1_c1/run_arm.py --arm arm1_heuristic [--corpus corpus_raw.jsonl]

产出 results/<arm>/:output.jsonl、rejects.jsonl、manifest.json、analysis.json。
analysis 的口径:对 clean 的留存率是"查准的底",对各噪声类的杀伤率是"查全";
算子不读 meta._gt(只有本分析器读),实验公平性由此保证。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import datafoundry.ops  # noqa: F401,E402
from datafoundry.runner import run_pipeline  # noqa: E402

HERE = pathlib.Path(__file__).parent


def analyze(manifest: dict) -> dict:
    def load(path):
        return [json.loads(l) for l in pathlib.Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]

    survivors, rejects = load(manifest["output"]), load(manifest["rejects"])
    total = Counter(s["meta"].get("_gt", "?") for s in survivors + rejects)
    kept = Counter(s["meta"].get("_gt", "?") for s in survivors)
    killer: dict[str, Counter] = defaultdict(Counter)
    for s in rejects:
        gt = s["meta"].get("_gt", "?")
        killer[gt][s["trace"][-1]["op"] if s["trace"] else "?"] += 1
    per_class = {
        gt: {
            "total": total[gt],
            "kept": kept.get(gt, 0),
            "retention": round(kept.get(gt, 0) / total[gt], 4) if total[gt] else None,
            "killed_by": dict(killer.get(gt, {})),
        }
        for gt in sorted(total)
    }
    return {
        "n_in": manifest["n_in"],
        "n_out": manifest["n_out"],
        "est_cost_total": manifest["est_cost_total"],
        "clean_retention": per_class.get("clean", {}).get("retention"),
        "noise_kill_rate": {
            gt: round(1 - v["retention"], 4) for gt, v in per_class.items() if gt != "clean" and v["retention"] is not None
        },
        "per_class": per_class,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, help="配置名(不含 .json),如 arm1_heuristic")
    ap.add_argument("--corpus", default=str(HERE / "corpus_raw.jsonl"))
    args = ap.parse_args()

    config = json.loads((HERE / f"{args.arm}.json").read_text(encoding="utf-8"))
    out_dir = HERE / "results" / config["name"]
    manifest = run_pipeline(args.corpus, config["steps"], out_dir)
    result = analyze(manifest)
    (out_dir / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"== {config['name']} ==  in={result['n_in']} out={result['n_out']} cost={result['est_cost_total']}")
    print(f"clean 留存: {result['clean_retention']}")
    for gt, rate in result["noise_kill_rate"].items():
        print(f"  {gt:<13} 杀伤率 {rate}")
    for op in manifest["per_op"]:
        print(f"  {op['op']:<24} in={op['in']:>5} killed={op['killed']:>5} retention={op['retention']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
