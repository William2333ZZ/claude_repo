# 26 · Skill 优先架构:harness 装技能,技能带登录(2026-08-15,委托人三点指令后的平台重设计)

委托人公理(逐字):**① 所有的架构都是 harness 安装 skill;② skill 里面需要有平台的登录;
③ 然后再设计我们这个平台。**

## 0. 前提倒置:harness 是浏览器,Skill 是我们的 App

旧序(docs/23):REST/CLI/MCP 三投影平列,网页/控制台在远期。
**新序**:用户住在 harness 里(Claude Code / dsh / Codex),他不"访问我们的产品",
他**安装我们的技能**。从此:

```
Skill(剧本层:怎么做、何时做、失败怎么办——含登录)
  └─直驱→ REST 窄腰(curl;MCP 为可选适配器,见 §7)
        └→ service(服务)→ kernel(裁决)→ 事实文件
```

- **Skill 是第一交付物**:纯文本、装进 `~/.agents/skills/` 即用、跨 harness 通用
  (Claude Code 与 dsh 吃同一种 SKILL.md)——分发摩擦≈零,这就是获客漏斗的第一级。
- **CLI** 降为本地离线特例(demo/refine 不触云);**网页 = 看板台**(2026-08-15
  委托人「toC 看板平台」指令,docs/28):原"两样"(设备码授权页 + 只读证据页)收编进
  toC 看板平台——看见/信任/批准/付钱/转发;仍**不做"网页操作台"**——
  操作台是用户自己的 harness,"做"的活永远在技能面。
- 旧"三投影矩阵"(docs/23 §4)升级为**剧本层 + 四投影**:skill 编排、REST 承载(主),
  CLI 离线、MCP 可选、控制台只读。

## 1. 登录即剧本第一幕(公理②的展开)

现状核查(2026-08-15):设备码流全套已在(`/auth/device/start → 人开授权页批准 →
/auth/device/token`,RFC 8628,为无浏览器客户端而生——正好是 agent);MCP 客户端可
无凭据启动;**但自助注册不存在**(POST /users 是 admin 专属)——陌生人旅程第一幕即断。

**登录剧本(datafoundry-onboard skill)规格**:
1. 探测凭据(`DATAFOUNDRY_API_KEY` env → `~/.datafoundry/credentials.json`)→ 有则跳过
2. 无账户 → 自助注册 `POST /auth/register`(**已实现,生产实测 200**)
3. 有账户 → 剧本直呼设备码端点:`/auth/device/start` 把**授权页 URL+用户码**交给人,
   人在浏览器点批准,轮询 `/auth/device/token` 拿 API key → 存盘(**端点原已在**)
4. 验证:`GET /auth/me` 打通 → 报"已就绪,余额 X"
5. 失败分支:服务不可达(冷启动 ~50s)/批准超时/额度为零,各给一句指路话术

安全线延伸:**密码永远不经过 agent**(设备码流的本意);API key 只落本机凭据文件。

**镜像一环(2026-08-15,已实现)**:登录剧本的反向——**免密开看板**。剧本持存盘 key
`POST /auth/web-login` 换 120 秒一次性链接交给人,浏览器点开即入看板(docs/28 §3);
设备码 = 浏览器给 agent 发 key,免密直登 = agent 给浏览器发会话,一对镜像,
密码与裸 key 都永不进 URL。

## 2. 技能包四件套(skills/,harness 无关资产核)

| Skill | 剧本 | 依赖端点(REST 直呼,§7 裁定后) | 状态 |
|---|---|---|---|
| **datafoundry-onboard** | §1 登录剧本 | /auth/register · /auth/device/* · /auth/me · /billing/me | ✅ 已落库 |
| **datafoundry-refine** | 上传→估算(免费)→人点头才扣费→跑→读尸检→下载交付 | /datasets/upload · /pipelines/estimate · /runs · /runs/{id}/rejects · /runs/{id}/output | ✅ 已落库 |
| **datafoundry-autopsy** | 公开数据集尸检(BELLE 战役方法学:抽样纪律/仪器自检/报告格式) | 本地内核为主(pip install datafoundry) | ✅ 已落库 |
| **datafoundry-billing** | 查余额;不足→建单→**pay_url 交人**扫码;到账核验 | /billing/plans · /billing/orders · /billing/me | ✅ 已落库 |

每篇 SKILL.md 必含:何时用我 / 前置检查 / 步骤 / 失败分支 / 统一语言(docs/22 §1 的词)。
旧 #26(dsh bundle 薄包装)并入本设计:bundle/插件配置只是技能包的每-harness 安装器。

## 3. 平台侧改动清单(#29;§7 裁定后 B-E 全部裁撤,终态见行内标注)

| # | 改动 | 为什么 | 大小 |
|---|---|---|---|
| A | **自助注册**:POST /auth/register(开放,engineer 角色,限流+密码策略沿用;体验额度暂 0) | 陌生人第一幕 | ✅ 已实现+生产实测 |
| B | ~~df_device_login~~ | 公理② | 裁撤(§7):剧本直呼既有设备码端点 |
| C | ~~df_get_rejects~~ | 尸检剧本 | 裁撤(§7):REST /runs/{id}/rejects 原已在 |
| D | ~~df_create_order~~(裁定保留:**支付授权不进智能体,建单与查余额可进**) | 计费剧本 | 裁撤(§7):REST /billing/orders 原已在 |
| E | ~~df_eval_run~~ | 旅程 B | 裁撤(§7):REST /runs/{id}/eval 原已在 |
| F | **产出下载**:GET /runs/{id}/output | 交付一幕此前是空的(走查发现) | ✅ 已实现 |

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

