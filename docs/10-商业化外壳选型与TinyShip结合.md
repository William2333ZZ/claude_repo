# 10 · 商业化外壳选型:TinyShip 结合分析(商业域)

> Owner 分析(2026-08-13):TinyShip 这类"SaaS 启动器"补的正是 DataFoundry 至今空白的
> **商业外壳**(官网/登录/付费/用户后台)。结论先行:**gate 前不买不建(纪律);gate 通过后
> 首选 TinyShip 搭私有外壳仓库,与开源内核走 API 对接**。定价叙事采用 Steph Ango 路线。

## 1. 对象是什么

**TinyShip**(tinyship.cn,作者 Viking/vikingmute,成熟独立开发者,有半年营收复盘):
中国市场适配的现代全栈 SaaS 启动器。monorepo,三框架可选(Next.js / Nuxt.js / TanStack Start),
TailwindCSS v4 + shadcn/ui + Zod。关键能力:

- **鉴权**:Better-Auth 底座——邮箱/手机号/OAuth/**微信登录**、2FA、会话管理
- **支付双栈**:国内(**微信支付、支付宝**)+ 国际(Stripe、PayPal、DodoPayments)同仓
- **基建**:PostgreSQL/SQLite(D1)、统一文件上传(OSS/S3/R2/COS)、Fumadocs 文档站、
  Cloudflare Workers 全托管部署
- 另带 AI 生图/生视频模块(对我们无直接用途,但其"AI 调用封装+额度计费"模式可参考)

**ShipFast**(shipfa.st,Marc Lou,$199–299):这个品类的开创者,8300+ 买家,卖点是
"两小时上线"与万人社区;弱点是无多租户/无 RBAC/无国内支付——**对我们不适用,仅作品类参照**。

**《Quality software deserves your hard-earned cash》**(Steph Ango,Obsidian CEO):
优质软件像农夫市集的手工食品,值得直接付费;要做有原则的软件就别变成 VCware,
**保持用户付费支撑**。GOG(DRM-free 游戏商店)即"尊重用户的付费软件"的活例。
——这是我们定价叙事的思想底座,与 docs/02 §6 的 open-core 模式契合。

## 2. 它补 DataFoundry 的什么

| DataFoundry 现状 | 缺口 | TinyShip 对应件 |
|---|---|---|
| FastAPI 平台(API/CLI/MCP) | 没有官网与产品门面 | Landing + 文档站(Fumadocs) |
| 账号口令 + 设备码登录 | 没有微信/手机号登录(国内客户期待) | Better-Auth 全家桶 |
| 无计费 | PoC 收款、订阅管理、发票 | 微信/支付宝 + Stripe 双栈 |
| 无 Web 界面(M2-G3 待做) | 数据集/运行/报告的可视化后台 | shadcn/ui 后台骨架 |
| 出海/国内二选一悬而未决(docs/03 开放问题④) | — | 双栈支付让该决策可以后置 |

## 3. 结合架构(关键设计:两仓分离)

```mermaid
flowchart LR
    subgraph 私有仓库[商业外壳(私有仓库,TinyShip 授权代码)]
        W[官网 Landing] --> C[用户后台<br/>Better-Auth:微信/手机号/2FA]
        C --> P[计费<br/>微信/支付宝/Stripe]
    end
    subgraph 开源仓库[DataFoundry 内核(Apache-2.0,本仓库)]
        A[平台 API<br/>RBAC/数据集/流水线/评测回流]
    end
    C -->|为登录用户签发平台 API Key<br/>(既有体系,后续 M2-G2 OIDC 打通)| A
    P -->|额度/订阅状态| A
```

**为什么必须两仓分离**:TinyShip 是买断制授权源码,license 不允许再分发——
它的代码**一行都不能进开源仓库**,否则毁掉 Apache-2.0 内核的合法性。
外壳私有 + 内核开源,恰好也是 open-core 的标准切分:社区用内核,付费买外壳体验与托管。

**依赖红线复核(docs/04)**:TinyShip 属"零件层"——买断源码在手、可改可替换、
无运行时闭源服务锁定,不触碰"闭源零依赖"红线(红线针对的是 NIM 式闭源运行时)。
风险:单人维护的模板项目 bus-factor;缓解:源码买断在手 + 外壳本身就是薄层。

## 4. Owner 决策

1. **现在(M1 期,gate 前):不买、不建**。商业外壳是 M3-H1 交付包工程,#7 关闭前不开工
   ——这是 docs/08 亲手立的纪律,Owner 带头遵守。设计合作客户访谈用的一页纸材料
   走纯静态页(现有 artifact/honkit 能力),不需要 boilerplate。
2. **gate 通过后:买 TinyShip**(几百元级 vs 自建外壳 2–4 周人力,ROI 明显),
   建私有外壳仓库,按 §3 架构对接。备选:shadcn/ui + Better-Auth 自建(若 license
   条款有意外);ShipFast 不选(无国内支付)。
3. **定价叙事采用 Steph Ango 路线**:用户付费支撑、不做广告与免费增值陷阱、
   像卖"农夫市集的手工食品"一样卖数据质量——对客户的一句话仍是 docs/03 的
   「同样的算力预算,更高的评测分数」,现在多了一层"为什么值得直接付钱"的思想背书。
4. **借鉴其增长打法**(独立开发者路线):公开营收复盘、build in public、
   文档站即营销——与我们"公开可复现实验报告"的可信度策略同构,M3 执行时采用。

## 5. 落到追踪器

- gate 通过后建事项:`[M3-H1a] 购买 TinyShip 并搭私有外壳仓库(登录+支付+后台骨架)`
  `[M3-H1b] 外壳↔内核对接(API Key 签发/额度同步,后续 OIDC)`——现在**不建 issue**,
  记录于此,防止追踪器被未开工计划污染(docs/08 规则)。
- 本文档同时兑现商业域欠账:设计合作客户材料的定价思想部分(§1、§4.3)。

## 7. 架构深读(2026-08-13 补)

TinyShip 2.0 的架构决策链(据作者公开的重构记录):

1. **PNPM monorepo + 框架无关 libs**:Auth/支付/ORM 做成抽象接口放 `libs/`,三个框架
   (Next.js/Nuxt/TanStack Start)只是薄适配——**与我们内核的 PaymentProvider/engines 模式
   同构**,互相印证了"抽象层+薄适配"是对的
2. **`libs/react-shared`**:Next.js 与 TanStack Start 共享 React 组件层——这让"先 Next.js、
   将来可换 TanStack"成为低成本选项,是我们选 Next.js 的兜底理由之一
3. **应用四区制**:Auth & Middleware / Public Pages / Protected Pages + Core API / Admin
   ——采纳为外壳信息架构(见 docs/13 §4.5)
4. **all-in-CF**(Workers/D1/R2/CF Email):工程上优雅,但 CF 大陆可达性差,
   我们的部署决策推迟到 M3-H1 按首发市场定
5. **开放式接口设计**:云商/数据库/支付商可自由替换——买它但不被它绑,符合 docs/04 零件层纪律

对应的正式前端选型(内核零框架/外壳 Next.js/PG/四区制)已登记 docs/13 §4.5。

### 来源

[TinyShip 官网](https://tinyship.cn/zh-CN) · [作者半年营收复盘](https://vikingz.me/tinyship-recap/) · [ShipFast](https://shipfa.st/) · [ShipFast 2026 测评](https://starterpick.com/blog/shipfast-review-2026) · [Boilerplate 横向对比](https://www.buildmvpfast.com/blog/best-nextjs-starter-kit-saas-boilerplate-2026) · [Quality software deserves your hard-earned cash](https://stephango.com/quality-software) · [Viking 推荐该文的推文](https://x.com/vikingmute/status/1778355062504640761) · [GOG](https://www.gog.com/)
