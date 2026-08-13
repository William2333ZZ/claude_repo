# M1-C1 四路对比实验(证伪实验)

> 目标:同一语料、同一预算上限,四条产线各自 出数据 → 训练(scripts/train_proxy.py)
> → 评测(scripts/eval_math.py),对比 gain-per-dollar。结论进 M1-C2 报告(硬门禁)。

## C1a · 语料与预算(待产品确认后填死)

- **语料提案**:用 LLM 批量生成 5,000 条"带噪数学 QA 候选"(生成时故意混入:计算错误答案、
  跑题、重复、水文——模拟真实爬取语料的噪声分布),原始候选池一次生成、四组共享,存
  `experiments/m1_c1/corpus_raw.jsonl` 并记录生成参数与种子。`[产品确认点 1]`
- **预算口径提案**:以平台成本单位计,每组上限 = 候选池全量过一遍 llm 档算子的成本 × 0.5。
  四组实际成本从各自 run manifest 的 `est_cost_total` 读取,组间差异必须 < 5%。`[产品确认点 2]`
- **训练/评测恒定**:train_proxy.py 固定超参(seed 42)、math_zh_v1 贪心评测——组间唯一变量是数据。

## C1b · 四条流水线配置(已入库,本目录 *.json)

| 组 | 配置文件 | 机制 | 一句话假设 |
|---|---|---|---|
| ① 仅启发式 | `arm1_heuristic.json` | 规则清洗+去重,不做质量选择 | 便宜,但杀不掉"内容错误" |
| ② 仅 LLM 判审 | `arm2_llm_only.json` | 全量 LLM judge(min_score 4) | 语义强,但全量过贵、无验证 |
| ③ 打分器选择 | `arm3_scorer.json` | 质量分阈值选择(FineWeb-Edu 式路线的 v0 代理,后续换训练出的打分器) | 中间成本,选"像好数据的" |
| ④ 漏斗混合(本平台主张) | `arm4_funnel.json` | 启发式先杀 → **math_answer_verify 硬验证** → LLM judge 只看幸存者 | 同预算下验证器保真 + 漏斗省钱 |

预算对齐方法:执行前对四配方各调一次 `POST /pipelines/estimate`,微调 ②③ 的阈值参数使
四组 `est_cost_funnel` 差 < 5%,调整后的最终参数回写本目录并记入 C2 报告。

## C1c · 执行清单(需要的外部资源)

1. LLM API(生成候选池 + judge):任意 OpenAI 兼容端点,设 `DATAFOUNDRY_LLM_BASE/KEY/MODEL`;
   预估调用量:生成 5k 候选 + judge ≤ 7k 次判审
2. GPU:单卡(≥24GB)× 4 次 LoRA 训练(每次 1–2 小时)+ vLLM 评测
3. 执行顺序(每组):平台跑配方 → `train_proxy.py --data <run>/output.jsonl --out exp/armN`
   → vLLM 起服务 → `eval_math.py --endpoint` → `POST /runs/{id}/eval` 回流分数
4. 全部四组的 (成本, 分数) 与 run 血缘齐 → 关 #6,开写 #7 报告
