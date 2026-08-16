# S1 对照训练:raw vs refined(#24)

同一 19,999 条 BELLE 抽样集(seed=42,sha256 `7fbe8eb51bdf…` 与 S0 逐字节一致,
见同目录 autopsy.json/AUTOPSY.md)。raw = 未过滤直接训练;refined = 过 v3.1
验证器链后的 survivors。其余全同:Qwen2.5-0.5B-Instruct LoRA、训练预算 1000
样本、2 epoch、math_zh_v1 60 题贪心评测(与 M1-C1 完全同协议,
`scripts/train_cpu_proxy.py` 零修改复用)。

## Round 1(2026-08-16,设计有confound,结论不采信——完整过程见下)

raw/refined 各自取自 19,999/18,920 两个不同大小的池,`train_cpu_proxy.py` 的
`load_pairs()` 各自独立 `random.Random(SEED).shuffle()` 后取前 1000。

| 组 | s42(run 31913092519) | s43(run 31918679550) | vs 基座 48.3 |
|---|---|---|---|
| 基座(不训练) | 48.3(29/60) | 48.3(29/60) | — |
| raw(未清洗直训) | 45.0(27/60) | 48.3(29/60) | s42 −3.3 / s43 ±0 |
| refined(v3.1 漏斗后) | 41.7(25/60) | 46.7(28/60) | s42 −6.7 / s43 −1.6 |

**两种子方向一致**(raw > refined,配对差 s42=+3.3、s43=+1.6),与"清洗提升下游
准确率"的主张相反。**但本项目自己的方法论纪律明确警告过这个陷阱**:M1-C1
"方法论教训"一节记录过"两种子期'4a>3'"的结论后来被四种子推翻——2 个种子不足以
下任何结论,尤其是在下面这个真实设计缺陷被发现之后:

**根因(已确认,非猜测)**:`random.Random(SEED).shuffle(list)` 对列表长度敏感——
同一 seed 在 19,999 长列表与 18,920 长列表上洗出的排列不同源,取前 1000 得到的是
两个几乎不重叠的子抽样,**不是**"同一批数据去掉被杀的那些"。真实语料本轮清洗仅
动 5.4%,这个抽样层面的残留偏差完全可能压过这么小的信号——与 M1-C1(清洗前后差
25+ 个百分点)不是同一量级的比较,设计阶段未充分权衡。refined.jsonl 本身确实是
raw.jsonl 的严格子集(过滤步骤没错),问题出在下游 `load_pairs()` 的独立洗牌上。

**判定**:Round 1 数字不采信、不对外引用,只留作方法论记录(仪器先怀疑自己,
训练侧的对应)。已修正设计,见 Round 2。

## 设计修正(v2,`experiments/s1_real_math/prepare_corpus.py`)

候选池大小直接等于训练预算(`CANDIDATE_POOL = 1000 = TRAIN_CAP`):raw 用满
这 1000 条;refined = 这 1000 条过滤后剩下的(自然更少,约 940-960 条量级——
这是清洗的诚实代价,不回填凑数)。`load_pairs()` 的洗牌此时只重排同一固定小
列表,refined 经洗牌+截断后仍是 raw 的严格子集(本地测试断言验证:
`refined_qs.issubset(raw_qs)` 恒成立)。唯一变量真正收窄为"这条数据有没有被
过滤",不再掺杂子抽样噪声。

## Round 2(v2 配对设计,进行中)

| 组 | s42 | s43 | 均值 | vs 基座 48.3 |
|---|---|---|---|---|
| 基座 | 48.3(29/60) | — | — | — |
| raw | 待跑 | — | — | — |
| refined | 待跑 | — | — | — |

## 复现命令

```bash
# 语料准备(抽样+漏斗,产物在 CI /tmp,不落仓库)
python experiments/s1_real_math/prepare_corpus.py --out /tmp/s1
# 训练+评测(每臂;TRAIN_SEED 复验用 42/43)
TRAIN_SEED=42 python scripts/train_cpu_proxy.py --data /tmp/s1/raw.jsonl --out results/raw_s42
TRAIN_SEED=42 python scripts/train_cpu_proxy.py --data /tmp/s1/refined.jsonl --out results/refined_s42
# 或直接:Actions → s1-real-corpus-training,train_seed=42|43
```

## 纪律

- Round 1 的教训:**"分数出来了"不等于"可以看数字讲故事"**——先问训练管线的每一步
  是否真的实现了"唯一变量=数据",这一步比多跑种子更优先
- 若 Round 2 两种子仍矛盾或幅度落在噪声范围(±2-3 分/60 题协议下 M1-C1 定的门槛),
  按 M1-C1 先例扩到四种子(42/43/44/45)才可下结论,不提前对外引用
