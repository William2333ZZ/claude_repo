---
name: datafoundry-refine
description: 用 DataFoundry 精炼(清洗)一批训练数据的完整剧本:上传 → 免费估算 → 人批准 → 执行漏斗 → 读死因尸检 → 下载幸存集交付。当用户说"帮我清洗/精炼/过滤这批数据""这数据能不能用来训练"时使用。需先完成 datafoundry-onboard。
---

# 用 DataFoundry 精炼一批数据

前置:凭据已就绪(否则先走 datafoundry-onboard)。`BASE` 与 `KEY` 从
`~/.datafoundry/credentials.json` 读。数据须为 JSONL,每行含 `text` 字段
(其他字段自动进 meta)。

## 步骤

**1. 上传**(文件名只作标识):
```bash
curl -s -X POST "$BASE/datasets/upload?name=<名字>" -H "X-API-Key: $KEY" \
  -H 'Content-Type: text/plain' --data-binary @<文件.jsonl>
```
记下响应 `id`(ds_xxx)与 `n_samples`。

**2. 选配方**:`GET $BASE/recipes`(每个带实验证据出处);或手写 steps(`GET $BASE/ops`
看算子清单,注意 requires_stats 依赖——缺上游会在组装期被拒并给指路错误)。

**3. 免费估算,先算账**:
```bash
curl -s -X POST $BASE/pipelines/estimate -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"dataset_id": "ds_xxx", "steps": [...]}'
```
**必须把这三个数报给用户并等确认**:预计成本(est_cost_funnel)、相比朴素顺序省多少
(est_savings)、预计留存(est_retention)。同时读 `GET /billing/me` 余额——
本次将扣 n_samples 个额度,不足则先走 datafoundry-billing。**用户没点头,不开跑。**

**4. 执行**:
```bash
curl -s -X POST $BASE/runs -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"name": "<名字>", "dataset_id": "ds_xxx", "steps": [...]}'   # 或 {"recipe_name": "..."}
```
每 5-10 秒轮询 `GET $BASE/runs/<run_id>` 至 succeeded/failed。**失败自动全额退款**,
读 error 字段报因即可。

**5. 尸检(交付的一半价值在这)**:`GET "$BASE/runs/<run_id>/rejects?n=20"`。
向用户汇报:总留存率、死因分布(哪个算子杀了多少、为什么)、抽 3-5 条被杀样本
逐条讲死因。语言纪律:死因是**可辩护的理由**,不说"质量低",要说"复算必错的算式
3+4=8"这种可复核的话。

**6. 交付**:`curl -s -o 幸存集.jsonl "$BASE/runs/<run_id>/output" -H "X-API-Key: $KEY"`,
连同 manifest(`GET /runs/<run_id>`,含配方 hash、平台版本、实际成本)一起交给用户——
这份 manifest 就是可复现凭证。想让用户在浏览器亲眼看尸检:
`POST $BASE/auth/web-login`(见 datafoundry-onboard「打开看板」)取免密链接交给人。

## 失败分支

- 402 额度不足:报差额,引导 datafoundry-billing
- 组装期报错(缺依赖/未知算子):错误信息自带指路,照做即可
- estimate 与实际成本差异大:如实报,manifest 里两个数都有
