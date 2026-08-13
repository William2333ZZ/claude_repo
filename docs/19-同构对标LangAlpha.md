# 19 · 同构对标:LangAlpha(委托人指定参考,2026-08-13)

> LangAlpha(langalpha.ai,ginlix-ai)自称 **"a vibe investing agent harness"**,GitHub 口号
> **"Claude Code for Financial Market"**——与 DataFoundry(Claude Code harness for 训练数据)
> 是**同一品类的不同垂直**:agent harness × 开源内核 × 托管增值。它 1.6k stars、1670 commits、
> 获 LangChain 官方转发。本文提取其可借鉴处并落成采纳决策;对标不是抄——是用同类产品的
> 公开选择来校准我们的选择。

## 1. 它是什么(一手信息,源自其仓库)

- **形态**:LangGraph ReAct 主 agent + 并行异步 subagent(隔离上下文防漂移)+ 每 workspace
  一个沙箱(云 Daytona / 本地 Docker);Web UI + CLI/TUI + REST + **MCP server**;
  Slack/Discord/**飞书**/Telegram 渠道接入
- **两种模式**:PTC(Programmatic Tool Calling,agent 写 Python 做深度多步研究)/
  Flash(快速对话与编排)
- **workspace 复利**:`agent.md` 跨会话持久注入——"持久化工作区使研究自然复利,而非一次性问答"
- **开源边界**:**全栈 Apache-2.0**(后端/前端/CLI/MCP/技能库全部开源);商业独占的是
  **数据基础设施**(实时行情 WebSocket、价格触发自动化 "exclusively on the hosted platform")
- **BYOK**:用户自带 LLM key;数据源三层分级(托管独占 → 付费 key → Yahoo 免费降级),
  "add keys incrementally"
- **部署**:"You can start LangAlpha with nothing but Docker"——`make config`(交互向导)+
  `make up`(PG+Redis+前后端)

## 1.5 官网一手信息(2026-08-13 经 fetch-page 跳板抓取,public beta 期)

**定价表**(月付,年付 -15%):

| 档 | 价格 | credits/月 | 关键差异 |
|---|---|---|---|
| Free | $0 | 1,000 | 标准模型,5 工作区,1 个定时任务,社区支持 |
| Plus | $20 | 18,000 | 进阶模型,10 工作区,5 定时任务,充值包永不过期 |
| Pro(最热) | $40 | 38,000 | 全部前沿模型,深度多步研究,Slack/Email/Telegram/飞书,20 工作区/20 任务,完整 API(即将) |
| Max | $100/$200 | 100k/220k | 无限工作区与任务,credits 用完后标准模型不限量对话(5h 限) |

**计费机制(两轴设计,关键学习点)**:
- **订阅费 = 能力开关**:档位控制的是模型层级、研究深度(并行 subagent 数)、定时任务数、
  工作区数、渠道接入、API——不是用量
- **credits = 托管智能计量**:只在"agent 跑在**他们的**模型上"时消耗,按深度计
  ("简单查询耗得少,带代码/图表/检索的深度研究耗得多");工具调用计小额固定费,余额实时可见
- **BYOK 完全免计量**:自带 Claude/GPT/Gemini key 的运行**不消耗平台 credits**,直接付给模型商
- beta 期全档位开放完整数据(A股/美股/港股实时+基本面+文件),数据分层收费排在模型/深度分层之后
- 信任设计:beta 涨价保护承诺(方案价值缩水则 credits 补偿)

**官网叙事(与我们的死因/血缘定位互证)**:对"它和 ChatGPT/Claude 有什么不一样"的回答是——
"每个数字都查得到出处:往前一步是算出它的代码,再往前是数据的源头…可打开、可复现、**可审计**
的分析,而非一段看似合理的推测。**你验证它,而不是去信一个黑箱**";口号
"An AI analyst that does the work, not just the talking: filings read, models built, deliverables shipped";
页脚把 Self-host 直接链到 GitHub(自托管是一等 CTA)。

## 2. 同构验证(它替我们试过的路)

| 我们的既有选择 | LangAlpha 的对应 | 结论 |
|---|---|---|
| Claude Code harness / MCP 接入 | 自建 harness + MCP server + 多渠道 | "agent harness for X" 品类成立,不止我们一家在押 |
| 开源内核 + 托管/外壳收费 | 全栈开源 + 数据基础设施收费 | open-core 成立;切法有两种(见 §3 D3) |
| BYOK(DATAFOUNDRY_LLM_* 环境变量,已实现) | BYOK 明写在商业条款 | 我们已有此能力,**该把它升级为产品卖点**(D1) |
| 产物中心主义(死因/审计/评测报告) | memo/report 即交付物 | 一致 |
| run 档案 + judge_labels 复利 | workspace + agent.md 复利 | 「精炼复利」叙事成立(D4) |
| 无框架自研(stdlib MCP) | LangGraph 全家桶 | 各自成立;我们的零依赖裁定(docs/13)不变 |

## 3. 采纳决策(Owner 裁定,增量按 docs/17 象限纪律)

| # | 决策 | 落点 | 时点 |
|---|---|---|---|
| D1 | **BYOK 写进商业化方案**:客户自带 LLM key,判审成本透明,平台收软件与额度费、不赚 token 差价——对 P1 的信任卖点 | docs/14 §4 补记 | 本轮(文案级) |
| D2 | **自托管 Docker 一键化 = 一等交付形态**:P1 客户(金融/医疗,私域数据敏感)必然要求私有化;学其 `make config` 交互向导 + "nothing but Docker" 姿态;我们已有 Dockerfile,补向导与 compose | M3-H1 交付包验收标准 | gate 后(工程) |
| D3 | **开源切法对照入册**:他们"全栈开源、独占数据基建";我们"内核开源、外壳私有(TinyShip license 约束)+ 判断类资产(评测集口径/验证器/配方证据档案)走付费"。暂不改;**重估触发升级(2026-08-13):TinyShip 采购决策前必须先裁「自建开源外壳+档案收费」路线(docs/14 §10),不再是被动触发而是前置审查**(它的分发红利实证:LangChain 官推 → 1.6k stars) | docs/13 §4 商业外壳行关联 | 记录,不动手 |
| D4 | **「精炼复利」进产品叙事**:每次 run 沉淀配方证据、死因档案、judge_labels 蒸馏语料——数据产线不是一次性问答 | docs/14 叙事段 | 本轮(文案级) |
| D5 | **生态旗舰分发**:它靠 LangChain 官方放大;我们的对应生态位 = Claude Code/MCP 目录收录 + 向 DJ/DataFlow 上游回馈 PR,与 C2 报告发布同步执行 | M1-C2 发布清单 | gate 后随 C2 |
| D6 | 渠道接入(飞书/Slack bot)与图表可视化:诱人但当前无场景牵引,**列入不做清单**(Q3 陷阱) | docs/17 §4.3 追加 | 明确不做 |

## 4. 定位差异(为什么不是竞品)

它卖**分析结论**给投资者(数据是它的原料);我们卖**数据生产线**给模型团队(数据是我们的产品)。
无交集,但同为 "语言进、可验证工件出" 的 agent harness——它的今天(workspace 产品化、
VPC 交付、生态分发)是我们 gate 通过后 M3 阶段的有用地图。

## 5. 生态位修正:LangAlpha 是我们的下游客户原型(2026-08-13,委托人洞察)

委托人指出:LangAlpha 可以是本产品的**下游应用**。采纳,并精确化:

**今天它不买数据**——它是 BYOK harness,质量来自 prompt/技能/工具,不做微调,数据不是其
第一天的痛。但垂直 Agent 公司的成长曲线注定撞上四堵墙,每堵墙都是我们的入口楔子:

| 它的规模化之痛 | 我们的楔子 | 对应已有能力 |
|---|---|---|
| token 成本随用量爆炸 → 需要蒸馏专用小模型 | **轨迹/判审数据精炼 → 蒸馏语料** | judge_labels 回收循环已建成 |
| 「agent 答得对不对」无法回答 → 需要领域评测 | **验证器 + 评测集方法论** | math_answer_verify 起步,M2-D 扩族 |
| agentic RL 兴起 → 轨迹要变偏好/RL 数据 | **漏斗+验证精炼其 workspace 日志** | 配方引擎通用,换算子即可 |
| 技能库手写不可扩 → 需要从文档合成技能 | **doc2sft 链泛化为 doc2skill** | S1 场景已端到端走通 |

一句话:**Agent 公司的日志堆 → 我们的漏斗 → 它们的专用模型**——数据精炼是 agent 经济的
中间层。这与 docs/14 §9 的「应用是数据飞轮闭合器」互为镜像:我们不自建应用去产生反馈数据,
而是服务那些已经拥有反馈数据的应用公司。

**可达性红利**:这类公司开源、build in public、活在 GitHub/X 上——**AI Owner 可以自主完成
冷接触前的全部调研**(读它们的 issues 找数据/评测/成本之痛),部分缓解链六判定的
「接触真实客户」瓶颈。P-agent 证据扫描列入 gate 后低保真验证清单。

### 来源

[LangAlpha 官网](https://langalpha.ai/zh-CN/home)与[定价页](https://langalpha.ai/pricing)(2026-08-13 经 fetch-page 跳板一手抓取,run 31686720293)·
[GitHub: ginlix-ai/langalpha](https://github.com/ginlix-ai/langalpha)(一手:架构/开源边界/部署/BYOK)·
[LangChain 官方转发](https://x.com/LangChainAI/status/2002801246101807443) ·
[ginlix.ai](https://ginlix.ai/home)
