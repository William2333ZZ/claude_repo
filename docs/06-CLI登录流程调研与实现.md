# 06 · CLI 登录流程：feishu-cli / taptap 调研与 DataFoundry 实现

> 任务：调研 feishu-cli、taptap-cli 等工具的登录流程,并在 DataFoundry 上实践。
> 结论:两家收敛到同一个模式——**OAuth 2.0 设备授权流(RFC 8628)+ 本地凭据缓存 + 环境变量优先级**。
> 已在平台落地:服务端 3 个设备授权端点 + 浏览器授权页,CLI 新增 `login / whoami / logout`,
> 全链路真实烟测通过(含两段式 Agent 模式、凭据文件回退、logout 吊销)。

## 1. 调研结果

### feishu-cli([riba2534/feishu-cli](https://github.com/riba2534/feishu-cli))

明确采用 **OAuth 2.0 Device Flow(RFC 8628)**,好处是不需要在开放平台后台配置回调地址白名单:

- `feishu-cli auth login`:CLI 把**验证链接和 user_code 打到 stderr**,用户在任意设备(电脑/手机)的浏览器完成授权;桌面端尝试自动拉起浏览器,SSH 场景静默失败(链接已打印)
- **两段式 Agent 模式**(专为 AI Agent 工作流设计):
  `auth login --no-wait --json` 立即返回 device_code 不阻塞;
  `auth login --device-code <code> --json` 携带已有 device_code 续轮询;`--json` 输出事件流
- **凭据存储**:`~/.feishu-cli/token.json`(access + refresh token;access 约 2h,refresh 30 天自动续期)
- **令牌解析优先级**:`--user-access-token` 参数 > `FEISHU_USER_ACCESS_TOKEN` 环境变量 > token.json > config.yaml
- 配套命令:`auth status` / `auth check --scope` / `auth logout`(清 token 与缓存)/ `auth token --as user`(导出给其他工具)

### TapTap(开发者服务 OAuth)

TapTap 文档域名(developer.taptap.cn / .io)被本环境网络代理拦截,未能直接抓取原文;
从检索摘要与公开资料确认:TapTap OAuth 同样提供**标准设备授权流**——
`device/code` 端点返回 `device_code / user_code / verification_url / interval / expires_in`(另有二维码变体,
主机/PC 场景手机扫码即授权),客户端按 interval 轮询 token 端点,处理 `authorization_pending` / `slow_down`,
国内外域名 open.tapapis.cn / open.tapapis.com 互换。这与 GitHub CLI、Google TV 等的设备流同属 RFC 8628 家族。

### 模式提炼(为什么 CLI 都选设备流)

1. **无回调白名单**:CLI 没有固定回调地址,授权码模式难落地;设备流只需用户在任意浏览器打开链接
2. **天然支持 headless/SSH**:链接 + 短码可以在另一台设备完成
3. **Agent 友好**:两段式(先拿码、后轮询)让自动化流程不被阻塞——feishu-cli 已把这做成显式功能
4. **凭据卫生**:短时授权码换长期凭据,本地文件 0600,环境变量可覆盖(CI 场景)

## 2. DataFoundry 实现(本仓库已落地)

### 服务端(server.py + store.py)

| 端点 | 认证 | 行为 |
|---|---|---|
| `POST /auth/device/start` | 无 | 发 `device_code`(库中只存 SHA-256 哈希)+ `user_code`(XXXX-XXXX,无易混字符字母表)+ 验证链接,10 分钟过期 |
| `GET /device?code=` | 无 | 自包含 HTML 授权页(user_code 预填,输入并 html.escape 防注入) |
| `POST /device/decide` | 页面表单 | 表单(x-www-form-urlencoded,手工解析免 python-multipart 依赖)内用户名+口令经 argon2 校验后批准/拒绝 |
| `POST /auth/device/decide` | Bearer/Key | 程序化批准(已登录会话对 user_code 做决定),user_code 大小写/连字符宽容 |
| `POST /auth/device/token` | 无 | 轮询:`authorization_pending` / `slow_down`(按 interval 限速)/ `approved`(**一次性**签发 `dfk_` API Key,再查即 `claimed`)/ `denied` / `expired` / `invalid` |

安全性:device_code 只存哈希;user_code 短时效且一次性;批准动作必须携带有效用户凭据;全程审计
(device_start / device_decide / device_claim 均落 audit 表);签发的 Key 带 `device:<label>` 标签可单独吊销。

### CLI(cli.py + credentials.py)

```bash
datafoundry login                      # 设备流:打印链接+授权码,尝试拉起浏览器,轮询至授权
datafoundry login --no-wait --json     # 两段式第一步:立即返回 device_code(Agent 场景)
datafoundry login --device-code dfd_x  # 两段式第二步:续轮询
datafoundry login --password           # 口令直连(不走浏览器):login -> 签发 API Key
datafoundry whoami                     # 当前身份
datafoundry logout                     # 吊销服务端 Key + 清除本地凭据
```

- 凭据文件:`~/.datafoundry/credentials.json`(0600,含 url/api_key/username/role/key_id)
- 解析优先级(对齐 feishu-cli):显式参数 > `DATAFOUNDRY_URL` / `DATAFOUNDRY_API_KEY` 环境变量 > 凭据文件 > 默认值
- MCP harness 同享该优先级:登录一次,Claude Code 接入时可不再显式配 Key(仍推荐生产用独立 Key 控角色)

### 烟测记录(真实服务,非单测)

```
login --no-wait --json  -> device_code + user_code X6B7-KMKN + 验证链接
GET /device?code=...    -> 授权页渲染,user_code 预填
POST /device/decide     -> 表单口令校验 -> "已授权(alice/admin)"
login --device-code ... -> {"event":"approved"} -> 凭据落盘(-rw-------)
whoami(无环境变量)      -> {"username":"alice","role":"admin"}(走凭据文件)
MCP df_status(无环境变量)-> 同上(凭据回退生效)
logout                  -> 服务端 Key 吊销 + 本地清除;旧 Key 再访问 -> 401
```

单测新增 4 例(happy path、deny/expired/invalid、授权页表单、凭据文件与优先级),全套 30 例通过。

## 3. 与 feishu-cli 的差距(后续可补)

- **refresh token 双令牌**:我们签发的是长期 API Key(可吊销),feishu 是短 access + 30 天 refresh 自动续期。
  自托管平台风险模型不同,v0 取"可吊销长 Key";若对外多租户,应换双令牌
- `auth status` 细节(过期倒计时)、`auth check --scope` 式的能力预检(我们的对应物是角色预检)
- TapTap 式**二维码变体**(user_code 转二维码,手机扫码授权)——授权页已是独立 URL,补一步二维码渲染即可

### 来源

- [riba2534/feishu-cli](https://github.com/riba2534/feishu-cli)(README 认证章节,WebFetch 抓取)
- [TapTap OAuth 文档](https://developer.taptap.cn/docs/en/sdk/taptap-login/taptap-oauth/)(域名被本环境代理拦截,内容经检索摘要交叉确认)
- [TapTap 开发者文档(国际站)](https://developer.taptap.io/docs/v3/sdk/taptap-login/guide/taptap-oauth/)
- [OAuth 2.0 Device Flow 综述(oauth.com playground)](https://www.oauth.com/playground/device-code.html)
- [GitHub OAuth 设备流文档](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)
