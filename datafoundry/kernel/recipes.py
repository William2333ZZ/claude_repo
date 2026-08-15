"""配方(Recipe):简单算子的命名组合,平台的核心可售卖单元。

设计原则(2026-08-13 委托人指正落地):不造新概念——配方就是「简单东西的组合」。
工程化只做四件事:可发现(list/get)、可校验(validate_all)、可执行(steps 直接喂
run_pipeline)、可携证据(provenance 记录该组合在哪个实验里被验证过、结果如何)。

配方与算子的关系 = 菜谱与食材:算子保持简单(单一职责、三档成本),价值在组合与顺序;
漏斗编译器在执行时继续对组合做成本重排,配方作者只声明"放什么",不必操心"先放什么"。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os

from datafoundry.kernel.pipeline import validate_steps

# requires 声明配方运行的外部前置;目前只有 "llm"(需 DATAFOUNDRY_LLM_BASE/KEY/MODEL)
RECIPES: dict[str, dict] = {
    "clean_basic_v1": {
        "version": 1,
        "title": "通用清洗与去重",
        "scenario": "S1/S2 通用前置:任意中文语料的规整、脱敏、去噪、双重去重",
        "requires": [],
        "steps": [
            {"op": "whitespace_normalize"},
            {"op": "length_filter", "params": {"min_len": 30}},
            {"op": "pii_redact"},
            {"op": "symbol_ratio_filter"},
            {"op": "repetition_filter"},
            {"op": "exact_dedup"},
            {"op": "minhash_dedup"},
            {"op": "quality_score"},
        ],
        "provenance": "demo 场景验证:8 条脏样本正确杀 5 留 3;含 llm_judge 预估时漏斗编排省 46% 成本",
    },
    "math_zh_funnel_v1": {
        "version": 1,
        "title": "中文数学题漏斗+硬验证(零 LLM 成本)",
        "scenario": "S2 复现涨点:可验证域(数学)的脏语料精炼,首个配方包雏形",
        "requires": [],
        "steps": [
            {"op": "whitespace_normalize"},
            {"op": "length_filter", "params": {"min_len": 20}},
            {"op": "symbol_ratio_filter"},
            {"op": "repetition_filter"},
            {"op": "exact_dedup"},
            {"op": "minhash_dedup", "params": {"num_perm": 128, "bands": 8}},
            {"op": "math_answer_verify", "params": {"reference_key": "reference"}},
        ],
        "provenance": (
            "M1-C1 arm4a,两种子复验:唯一在两个种子下均优于不清洗基线的产线"
            "(seed42: 58.3 vs 基线 48.3;seed43: 63.3 vs 53.3);"
            "数据侧 wrong_answer/spam 击杀 100%、offtopic 96.6%、clean 零误杀。"
            "证据档案 experiments/m1_c1/RESULTS.md"
        ),
    },
    "doc2sft_v0": {
        "version": 0,
        "title": "私域文档 → SFT 问答数据(需 LLM 端点)",
        "scenario": "S1 锚点场景:文档分块 → QA 合成 → LLM 判审 → 双重去重",
        "requires": ["llm"],
        "steps": [
            {"op": "text_chunk_mapper", "params": {"max_chars": 800}},
            {"op": "qa_generate_mapper"},
            {"op": "llm_judge_filter"},
            {"op": "exact_dedup"},
            {"op": "minhash_dedup"},
        ],
        "provenance": "组件级验证(算子各自有测试);端到端待 LLM 端点到位后在脱敏样本上验证(docs/15 S1 升中保真)",
    },
}


def llm_configured() -> bool:
    return bool(os.environ.get("DATAFOUNDRY_LLM_BASE"))


def missing_requirements(name: str) -> list[str]:
    """返回未满足的前置(如 ["llm"]);空列表 = 可跑。"""
    return [r for r in RECIPES[name]["requires"] if r == "llm" and not llm_configured()]


def recipe_hash(name: str) -> str:
    """[M2-F2] 配方内容指纹:steps 的规范化 JSON(键排序)→ sha256 前 12 位。
    参数字典序不影响 hash;steps 内容一变 hash 即变——效果档案按它聚合,
    「所见即所签」的授权对象也是它(docs/21 主链③)。"""
    if name not in RECIPES:
        raise KeyError(f"未知配方 {name!r}(可用: {', '.join(RECIPES)})")
    canon = json.dumps(RECIPES[name]["steps"], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


def list_recipes() -> list[dict]:
    return [
        {
            "name": name,
            "version": r["version"],
            "hash": recipe_hash(name),
            "title": r["title"],
            "scenario": r["scenario"],
            "requires": r["requires"],
            "n_steps": len(r["steps"]),
            "ops": [s["op"] for s in r["steps"]],
        }
        for name, r in RECIPES.items()
    ]


def get_recipe(name: str) -> dict:
    if name not in RECIPES:
        raise KeyError(f"未知配方 {name!r}(可用: {', '.join(RECIPES)})")
    return {"name": name, "hash": recipe_hash(name), **copy.deepcopy(RECIPES[name])}


def recipe_steps(name: str) -> list[dict]:
    """深拷贝返回 steps,可直接交给 run_pipeline / POST /runs。"""
    return copy.deepcopy(get_recipe(name)["steps"])


def validate_all() -> dict[str, list[str]]:
    """校验全部配方的 steps 合法性,返回 {配方名: 错误列表};全空 = registry 健康。"""
    return {name: validate_steps(r["steps"]) for name, r in RECIPES.items()}
