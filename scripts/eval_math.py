"""[M1-A1] math_zh 评测判分器:全自动,精确匹配,零人工。

两种模式:
  --generations out.jsonl   离线判分(每行 {"id": ..., "output": ...})
  --endpoint URL --model M  对 OpenAI 兼容端点评测(贪心解码,协议见 evalsets/README.md)

输出:报告 JSON(accuracy、逐题结果),写到 --report(默认 eval_report.json)。
退出码 0=完成;判分本身不设及格线,分数解释归实验(M1-C1)。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.request

PROMPT = "请解答下面的数学题,一步步思考,最后一行只写「答案: <数字>」。\n题目:{question}"
_ANS_TAG = re.compile(r"答案[::]\s*(-?\d+(?:\.\d+)?)")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def extract_answer(output: str) -> str | None:
    m = _ANS_TAG.findall(output)
    raw = m[-1] if m else (_NUM.findall(output.replace(",", ""))[-1] if _NUM.findall(output.replace(",", "")) else None)
    if raw is None:
        return None
    v = float(raw)
    return str(int(v)) if v == int(v) else f"{v:g}"


def load_evalset(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def call_endpoint(base_url: str, api_key: str, model: str, question: str, timeout: float) -> str:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": PROMPT.format(question=question)}],
                "temperature": 0,
                "max_tokens": 512,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"]


def score(items: list[dict], outputs: dict[str, str]) -> dict:
    results = []
    for it in items:
        out = outputs.get(it["id"])
        got = extract_answer(out) if out is not None else None
        want = str(int(it["answer"]))
        ok = got is not None and got == want
        results.append({"id": it["id"], "want": want, "got": got, "correct": ok, "missing": out is None})
    n_ok = sum(r["correct"] for r in results)
    return {
        "evalset": "math_zh_v1",
        "n_items": len(items),
        "n_correct": n_ok,
        "n_missing": sum(r["missing"] for r in results),
        "accuracy": round(n_ok / len(items) * 100, 1) if items else 0.0,
        "results": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="math_zh_v1 评测判分")
    ap.add_argument("--evalset", default="evalsets/math_zh_v1.jsonl")
    ap.add_argument("--generations", help="离线模式:每行 {'id','output'} 的 JSONL")
    ap.add_argument("--endpoint", help="在线模式:OpenAI 兼容 base_url(如 http://127.0.0.1:8000/v1)")
    ap.add_argument("--model", default="", help="在线模式的模型名")
    ap.add_argument("--api-key", default="EMPTY")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--report", default="eval_report.json")
    args = ap.parse_args()

    items = load_evalset(pathlib.Path(args.evalset))
    if args.generations:
        outputs = {
            rec["id"]: rec.get("output", "")
            for rec in (json.loads(l) for l in pathlib.Path(args.generations).read_text(encoding="utf-8").splitlines() if l.strip())
        }
    elif args.endpoint:
        if not args.model:
            sys.exit("在线模式需要 --model")
        outputs = {}
        for i, it in enumerate(items, 1):
            outputs[it["id"]] = call_endpoint(args.endpoint, args.api_key, args.model, it["question"], args.timeout)
            print(f"\r[{i}/{len(items)}]", end="", flush=True)
        print()
    else:
        sys.exit("需要 --generations 或 --endpoint 之一")

    report = score(items, outputs)
    pathlib.Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"accuracy: {report['accuracy']}  ({report['n_correct']}/{report['n_items']},"
          f" missing {report['n_missing']})  -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
