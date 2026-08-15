# 26 · Skill 优先架构:harness 装技能,技能带登录(2026-08-15,委托人三点指令后的平台重设计)

委托人公理(逐字):**① 所有的架构都是 harness 安装 skill;② skill 里面需要有平台的登录;
③ 然后再设计我们这个平台。**

## 0. 前提倒置:harness 是浏览器,Skill 是我们的 App

旧序(docs/23):REST/CLI/MCP 三投影平列,网页/控制台在远期。
**新序**:用户住在 harness 里(Claude Code / dsh / Codex),他不"访问我们的产品",
他**安装我们的技能**。从此:

```
Skill(剧本层:怎么做、何时做、失败怎么办——含登录)
  └─驱动→ MCP 工具(动作层:df_* 原子动作)
        └─经 HTTP 窄腰→ service(服务)→ kernel(裁决)→ 事实文件
```

- **Skill 是第一交付物**:纯文本、装进 `~/.agents/skills/` 即用、跨 harness 通用
  (Claude Code 与 dsh 吃同一种 SKILL.md)——分发摩擦≈零,这就是获客漏斗的第一级。
- **CLI** 降为本地离线特例(demo/refine 不触云);**网页**收缩为两样:
  设备码授权页(登录的人手一环)+ 只读证据页(#19,给"转发给老板"用);
  **不再有"网页操作台"这个建设项**——操作台就是用户自己的 harness。
- 旧"三投影矩阵"(docs/23 §4)升级为**剧本层 + 四投影**:skill 编排,MCP 执行,
  REST 承载,CLI 离线,控制台只读。

## 1. 登录即剧本第一幕(公理②的展开)

现状核查(2026-08-15):设备码流全套已在(`/auth/device/start → 人开授权页批准 →
/auth/device/token`,RFC 8628,为无浏览器客户端而生——正好是 agent);MCP 客户端可
无凭据启动;**但自助注册不存在**(POST /users 是 admin 专属)——陌生人旅程第一幕即断。

**登录剧本(datafoundry-onboard skill)规格**:
1. 探测凭据(`DATAFOUNDRY_API_KEY` env → `~/.datafoundry/credentials.json`)→ 有则跳过
2. 无账户 → 引导**自助注册**(平台缺口 A)
3. 有账户 → `df_device_login`(平台缺口 B):agent 起设备码流,把**授权页 URL+用户码**
   交给人,人在浏览器点批准,agent 轮询拿 token → 换长期 API key → 存盘
4. 验证:`df_status` 打通 → 报"已就绪,余额 ¥X"
5. 失败分支:服务不可达/批准超时/额度为零,各给一句指路话术

安全线延伸:**密码永远不经过 agent**(设备码流的本意);API key 只落本机凭据文件。

## 2. 技能包四件套(skills/,harness 无关资产核)

| Skill | 剧本 | 依赖工具 |
|---|---|---|
| **datafoundry-onboard** | §1 登录剧本 | df_device_login(新)· df_status |
| **datafoundry-refine** | 上传→估算(免费)→确认扣费→跑→读尸检→交幸存集+报告 | 既有 8 工具 + df_get_rejects(新) |
| **datafoundry-autopsy** | 公开数据集尸检(BELLE 战役剧本化:抽样纪律/仪器自检/报告格式) | df_run_pipeline · df_get_rejects |
| **datafoundry-billing** | 查余额;不足→建单→**把 pay_url 交给人**扫码;支付后核账 | df_create_order(新)· /billing/me 投影 |

每篇 SKILL.md 必含:何时用我 / 前置检查 / 步骤 / 失败分支 / 统一语言(docs/22 §1 的词)。
旧 #26(dsh bundle 薄包装)并入本设计:bundle/插件配置只是技能包的每-harness 安装器。

## 3. 平台侧改动清单(#29,按旅程断点排序)

| # | 改动 | 为什么 | 大小 |
|---|---|---|---|
| A | **自助注册**:POST /auth/register(开放,engineer 角色,限流+密码策略沿用;体验额度暂 0——estimate 本来免费,付费前已可见价值) | 陌生人第一幕 | 小 |
| B | **df_device_login**:MCP 匿名可用工具,内嵌设备码流+凭据落盘 | 公理② | 中 |
| C | **df_get_rejects**(#28 首位,原样) | 尸检剧本 | 小 |
| D | **df_create_order**:建单返回 pay_url;**裁定修订**——"账务不进 MCP"精确为
"**支付授权不进 MCP,建单与查余额可进**":建单是拟提案(不动钱),人扫码支付才是授权,
与安全线一致(docs/23 §4 账务行、§5-5 同步修订) | 计费剧本 | 小 |
| E | df_eval_run(#28 原样,复购剧本) | 旅程 B | 小 |

## 4. 验收方式升级:Skill 走查

从此平台功能的验收标准 = **拿一个干净 harness,装技能包,从零走完旅程 A**
(无账户 → 注册 → 设备码登录 → 上传 → 估算 → 人批准付费 → 跑 → 读尸检 → 交付)。
走不通的那一步,就是平台缺陷——不是文档缺陷,不是"用户不会用"。
gate 报告(docs/20 形态)增加"skill 走查记录"一节。

## 5. 不变的东西(重设计 ≠ 推翻)

安全线四条一字不动;五律一字不动;窄腰四条一字不动(Skill 不是第五条窄腰——它是
窄腰④之上的**剧本**,可随时重写,不承诺稳定);kernel/service/interface 三域不动;
管理面永不进 MCP、密码永不经 agent、支付授权永远人手。

## 6. 纪律

- 新用例先写 skill 剧本再写工具:**剧本写不顺的用例,工具设计一定有问题**(剧本即验收)
- 技能包与平台同仓演进,技能引用的工具名/统一语言变更必须同步技能文本
- 每次发布跑一遍 Skill 走查;走查记录入 gate 报告

## 7. 渠道裁定:Skill+HTTP 为主,MCP 降为可选适配器(2026-08-15 委托人「可以不用 MCP,打包成 skills 分发」)

技能剧本直接教 agent `curl` REST 窄腰——零配置、零常驻进程、任何能跑 bash 的 harness
通吃。MCP server 保留为**可选适配器**(已建成、零维护、bash 受限环境仍有用),但:
新能力先落 REST + Skill,MCP 不再同步扩建。此裁定当场把 #29 塌缩为两个 REST 端点
(自助注册 + 产出下载,均已当日实现)+ 技能包本身(skills/ 四件套,已落库)——
原计划的 df_device_login/df_get_rejects/df_create_order/df_eval_MCP 工具全部不需要:
对应 REST 端点早已存在,剧本直呼即可。**窄腰④相应重定义**:认知接入面 = REST API
(技能剧本直驱;MCP 为其可选投影)。

