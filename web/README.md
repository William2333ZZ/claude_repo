# DataFoundry 看板外壳(web/)

Next.js 外壳,**独立部署物**(委托人 2026-08-15 裁定;正典 docs/28 §4、docs/29 §3.5,
选型出处 docs/13「外壳框架」ADR)。python 包对本目录零依赖,反之亦然。

## 架构约定(违反即打回)

- **只吃 REST 窄腰**:数据经两条路径进——Client Component 用 `lib/api.ts`
  (浏览器 `fetch('/df/...')`,`next.config.mjs` 反代到平台 API,cookie 同源自动带);
  Server Component 用 `lib/api-server.ts`(读 `df_session` cookie,转发为
  `Authorization: Bearer` 直连后端——SSR 没有浏览器帮你带同源 cookie)。
  外壳没有第三条取数路径,更禁直连数据库
- **middleware 只查 cookie 在场性**,真伪由 API 端每次请求裁决(窄腰是唯一权威);
  API 端 `current_user` 现在同时认 header(X-API-Key/Bearer)与 `df_session` cookie
  (docs/30 §3.1)
- **登录用 Server Action**(`app/login/actions.ts`):口令走浏览器→Next 服务端→后端,
  从不落入任何客户端 JS 变量
- GLOSS 人话与 `service/console.py` 同源同语,统一语言变更两处同步(docs/22 纪律)
- 免密直登:技能把一次性链接指向本站 `/df/auth/web?token=…` 即可(既有端点零改动)
- 组件不拿 fetch(`components/` 只吃 props);类型手镜像后端形状(`lib/types.ts`,
  改字段两端同 PR)

## 目录

```
app/login/{page.tsx,actions.ts}   登录(Server Action)
app/console/page.tsx              总览(余额 + 最近运行 + 订单,Server Component)
components/{Card,StatChip}.tsx    展示层原件
lib/api.ts                        客户端类型化 REST 客户端
lib/api-server.ts                 服务端取数(cookie→Bearer 转发)
lib/types.ts                      契约层(镜像后端响应形状)
lib/gloss.ts                      死因人话对照
```

## 本地跑

```bash
cd web && npm install && npm run build   # 类型检查 + 生产构建(CI 用同一命令,web-build.yml)
cd web && npm install && npm run dev     # DF_API_BASE 可指向本地 API,默认生产
```

完整前后端契约(API 表、部署拓扑、已知债务):docs/30-前后端架构.md。
建设总账:issue #30(30-A 骨架与会话 → 30-E 分享链)。
