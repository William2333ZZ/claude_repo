# DataFoundry 看板外壳(web/)

Next.js 外壳,**独立部署物**(委托人 2026-08-15 裁定;正典 docs/28 §4、docs/29 §3.5,
选型出处 docs/13「外壳框架」ADR)。python 包对本目录零依赖,反之亦然。

## 架构约定(违反即打回)

- **只吃 REST 窄腰**:一切数据经 `next.config.mjs` 的 `/df/:path*` 反代到平台 API——
  浏览器全程同源,`df_session` cookie 落在看板域,API 的相对 303 自然落回看板;
  外壳没有第二条取数路径,更禁直连数据库
- **middleware 只查 cookie 在场性**,真伪由 API 端每次请求裁决(窄腰是唯一权威)
- GLOSS 人话与 `service/console.py` 同源同语,统一语言变更两处同步(docs/22 纪律)
- 免密直登:技能把一次性链接指向本站 `/df/auth/web?token=…` 即可(既有端点零改动)

## 本地跑

```bash
cd web && npm install && npm run dev   # DF_API_BASE 可指向本地 API,默认生产
```

建设总账:issue #30(30-A 骨架与会话 → 30-E 分享链)。
