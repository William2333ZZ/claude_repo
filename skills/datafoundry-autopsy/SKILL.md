---
name: datafoundry-autopsy
description: 对公开/本地数据集做"尸检"——零 LLM 零成本复算文本里的算式断言,逐条点名假算式,产出可复现统计报告。当用户想评估某个数据集质量、怀疑语料有错、或要做数据质量对外报告时使用。可完全本地运行,不需要账号。
---

# 数据集尸检(本地内核,零依赖零成本)

DataFoundry 内核纯 Python 标准库,`pip install datafoundry` 即得,离线可跑。
方法学来自 BELLE school_math 战役:19,999 条中查出 4.04% 铁证假算式
(经四轮仪器自检 13.62%→4.04%,取整/四舍五入/分数惯用语按书写惯例记账不判死)。

## 步骤

**1. 抽样纪律**:固定 seed 抽样,记录抽样集 sha256——没有这两样,数字不可对外。

**2. 跑漏斗**(数据为 JSONL 含 text 字段):
```python
import datafoundry.kernel.ops  # 导入即注册
from datafoundry.kernel.runner import run_pipeline
m = run_pipeline("corpus.jsonl", [
    {"op": "length_filter", "params": {"min_len": 10}},
    {"op": "symbol_ratio_filter"},
    {"op": "repetition_filter"},
    {"op": "exact_dedup"},
    {"op": "minhash_dedup"},
    {"op": "arithmetic_consistency_verify"},   # 复算正文全部 a◦b=c 断言
], "out/")
```

**3. 读死因**:`out/rejects.jsonl` 每行一个被杀样本,`trace[-1]` 是死因,
`stats.false_equation` 是被点名的假算式;`out/output.jsonl` 幸存样本的
stats 里有惯例记账(div_floor_convention 等)。

**4. 报告格式**(照此写,别自由发挥):
- 头行:抽样 N 条(seed=S,sha256 前 12 位)
- 表:幸存率 / **铁证假算式**(条数+百分比)/ 三类惯例记账 / 重复 / 规则类
- 假算式样例 ≤15 条,**每条必须人眼一秒可判**(如 `4+5=12`);有一条存疑就别发
- 纪律声明:不含语料文本,只有统计与算式级引用

## 纪律(这部分是产品的魂,不可省)

- **首跑数字永远不信**:先查样例表里有没有可辩护的"冤案"(带余数除法、取整惯例、
  分数惯用语、复合表达式截断),有就说明仪器要修,别急着发数字
- 对外只引用可复现数字:seed + sha256 + 复现命令
- 语料文本不进任何仓库/报告,只留统计与算式级引用
