"""合规证据包:把一次运行的 manifest / rejects / output 组装为监管口径的审计报告。

定位(docs/17 §4.1 评审裁定):**流程合规的证据组装工具**——处置全链可追溯、死因可举证、
抽样可复核,对应 TC260-003 的语料溯源/抽样核查思路与 EU AI Act 第 10 条的数据治理文档要求。
边界(必须如实):TC260 的"违法不良信息"判定需要专门的内容安全能力,本工具 v0 用平台
启发式过滤器做**代理核查**,报告中显式声明"非内容安全认证"。
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

import datafoundry.kernel.ops  # noqa: F401  导入即注册算子
from datafoundry.kernel.registry import create_op

# 代理核查器:抽样复核用的启发式判定(kill 或改写命中即计 flag)
PROXY_CHECKERS = ["symbol_ratio_filter", "repetition_filter", "pii_redact"]

STANDARDS: dict[str, dict] = {
    "tc260": {
        "title": "TC260-003《生成式人工智能服务安全基本要求》口径",
        "threshold": 0.05,  # 抽样不合格占比合格线(条款为 5%)
        "clauses": [
            ("语料来源记录与可溯源", "§5 血缘与处置链", "覆盖:每样本 trace 记录经过的算子/参数/判定"),
            ("语料内容抽样核查(4000 条 / 5% 判废)", "§4 抽样核查", "代理核查:启发式过滤器复核,非内容安全认证"),
            ("过滤不合格语料的手段与记录", "§2 处置链 / §3 死因分布", "覆盖:逐算子进出计数,被杀样本全量落盘并带死因"),
            ("语料标注质量抽检", "§4 抽样核查", "部分:judge_labels 落盘可供人工抽检(如有)"),
        ],
    },
    "euai10": {
        "title": "EU AI Act Article 10(数据与数据治理)口径",
        "threshold": 0.05,
        "clauses": [
            ("数据来源与处理链文档化(provenance)", "§1 概要 / §5 血缘与处置链", "覆盖:数据集指纹 + steps_executed + 逐样本 trace"),
            ("数据准备操作的记录(清洗/过滤/去重)", "§2 处置链", "覆盖:逐算子进出/留存/成本档"),
            ("数据缺陷的识别与处置", "§3 死因分布 / §4 抽样核查", "覆盖:死因分布;代理核查如实标注"),
            ("记录的可审计性(append-only/时间戳)", "§6 边界声明", "部分:产物落盘不可变性属部署责任(见 P0-1)"),
        ],
    },
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def death_cause_stats(rejects_path: Path) -> Counter:
    """死因分布:每条被杀样本按 trace 最后一个算子归因。"""
    causes: Counter = Counter()
    if not rejects_path.exists():
        return causes
    with open(rejects_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            trace = json.loads(line).get("trace", [])
            causes[trace[-1]["op"] if trace else "unknown"] += 1
    return causes


def sample_audit(output_path: Path, n: int = 400, seed: int = 20260813) -> dict:
    """从产出随机抽样 n 条,用代理核查器复核。固定 seed 保证报告可复现。"""
    rows = [json.loads(line) for line in open(output_path, encoding="utf-8") if line.strip()]
    picked = random.Random(seed).sample(rows, min(n, len(rows)))
    by_checker: dict[str, int] = {}
    flagged_ids: set[int] = set()
    for name in PROXY_CHECKERS:
        op = create_op(name)
        hits = 0
        for i, row in enumerate(picked):
            probe = copy.deepcopy(row)
            result = op.process(probe)
            if result is None or result["text"] != row["text"]:  # 判死或需改写都算命中
                hits += 1
                flagged_ids.add(i)
        by_checker[name] = hits
    return {
        "population": len(rows),
        "sampled": len(picked),
        "seed": seed,
        "flagged": len(flagged_ids),
        "rate": round(len(flagged_ids) / len(picked), 4) if picked else 0.0,
        "by_checker": by_checker,
    }


def build_report(run_dir: str | Path, standard: str = "tc260", sample_n: int = 400, seed: int = 20260813) -> str:
    run_dir = Path(run_dir)
    if standard not in STANDARDS:
        raise KeyError(f"未知标准 {standard!r}(可用: {', '.join(STANDARDS)})")
    spec = STANDARDS[standard]
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    output_path = run_dir / "output.jsonl"
    rejects_path = run_dir / "rejects.jsonl"
    audit = sample_audit(output_path, n=sample_n, seed=seed)
    passed = audit["rate"] <= spec["threshold"]
    causes = death_cause_stats(rejects_path)

    lines = [
        f"# 数据处置审计报告({spec['title']})",
        "",
        f"- 运行目录:`{run_dir}`",
        f"- 数据集:`{manifest['dataset']}`",
        f"- 产出指纹:`sha256:{_sha256(output_path)}`",
        "",
        "## 1. 概要",
        "",
        f"输入 {manifest['n_in']} 条 → 产出 {manifest['n_out']} 条(留存 {manifest['retention']:.1%}),"
        f"估算处置成本 {manifest['est_cost_total']}。",
        "",
        "## 2. 处置链(逐算子)",
        "",
        "| 算子 | 成本档 | 进 | 出 | 杀 | 留存 |",
        "|---|---|---|---|---|---|",
    ]
    for op in manifest["per_op"]:
        lines.append(
            f"| {op['op']} | {op['cost_tier']} | {op['in']} | {op['out']} | {op['killed']} | {op['retention']:.2%} |"
        )
    lines += ["", "## 3. 死因分布(被杀样本按终杀算子归因)", ""]
    if causes:
        lines += ["| 死因算子 | 条数 |", "|---|---|"]
        lines += [f"| {op} | {cnt} |" for op, cnt in causes.most_common()]
    else:
        lines.append("(无被杀样本)")
    lines += [
        "",
        "## 4. 抽样核查(代理)",
        "",
        f"抽样 {audit['sampled']} / {audit['population']} 条(seed={audit['seed']},可复现),"
        f"代理核查命中 {audit['flagged']} 条,占比 **{audit['rate']:.2%}**"
        f"(合格线 {spec['threshold']:.0%})→ **{'通过' if passed else '不通过'}**。",
        "",
        "| 代理核查器 | 命中 |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in audit["by_checker"].items()]
    lines += [
        "",
        "## 5. 血缘与处置链声明",
        "",
        f"- 执行序:{' → '.join(manifest['steps_executed'])}(漏斗重排 {len(manifest.get('funnel_moves', []))} 处)",
        "- 每条产出样本携带完整 `trace`(经过的算子/参数/判定);每条被杀样本连同死因全量落盘于 rejects",
        "- 本报告由固定 seed 生成,同一运行目录可逐字复现",
        "",
        "## 6. 边界声明(如实)",
        "",
        "- 本报告是**流程合规的证据组装**:证明数据处置全链可追溯、死因可举证、抽样可复核",
        "- 抽样核查为**启发式代理**,不构成内容安全认证;涉「违法不良信息」判定需接入专门内容安全能力",
        "- 落盘产物的不可变性(append-only)属部署责任,托管方案见平台文档 P0-1 事项",
        "",
        f"## 7. 条款映射({spec['title']})",
        "",
        "| 条款要点 | 报告小节 | 覆盖状态 |",
        "|---|---|---|",
    ]
    lines += [f"| {c} | {s} | {status} |" for c, s, status in spec["clauses"]]
    return "\n".join(lines) + "\n"


def write_report(run_dir: str | Path, standard: str = "tc260", sample_n: int = 400, seed: int = 20260813) -> Path:
    report = build_report(run_dir, standard=standard, sample_n=sample_n, seed=seed)
    path = Path(run_dir) / f"audit_{standard}.md"
    path.write_text(report, encoding="utf-8")
    return path
