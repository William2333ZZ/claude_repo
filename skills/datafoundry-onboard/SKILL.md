---
name: datafoundry-onboard
description: DataFoundry 数据精炼平台的注册与登录剧本。当用户想使用 DataFoundry(清洗/精炼训练数据、跑数据尸检)但尚未配置凭据时使用。覆盖:探测凭据、自助注册、设备码授权登录、凭据落盘、就绪自检。
---

# DataFoundry 上手(注册 + 登录)

平台地址:`BASE=${DATAFOUNDRY_BASE:-https://datafoundry.onrender.com}`。
凭据文件:`~/.datafoundry/credentials.json`(键:base_url / api_key / username / role / key_id)。
所有已登录调用带头:`X-API-Key: <api_key>`。

## 步骤

**1. 探测**:若 `~/.datafoundry/credentials.json` 存在或 `DATAFOUNDRY_API_KEY` 已设,
`curl -s $BASE/auth/me -H "X-API-Key: $KEY"` 通即报"已就绪",跳到第 5 步。

**2. 注册(无账户时)**:生成强密码并**明示用户保管**(`openssl rand -base64 15`),然后:
```bash
curl -s -X POST $BASE/auth/register -H 'Content-Type: application/json' \
  -d '{"username": "<用户选定,3-32位字母数字下划线>", "password": "<生成的密码>"}'
```
409=已存在(直接登录);403=注册未开放(请用户联系管理员)。

**3. 设备码登录**(密码不再经过任何命令):
```bash
curl -s -X POST $BASE/auth/device/start -H 'Content-Type: application/json' -d '{"label": "agent"}'
```
把响应中的 `verification_uri_complete` 与 `user_code` **交给用户**:"请在浏览器打开并批准
(用刚才保管的密码登录)"。然后每 5 秒轮询:
```bash
curl -s -X POST $BASE/auth/device/token -H 'Content-Type: application/json' -d '{"device_code": "<上一步的>"}'
```
`authorization_pending` 继续等;`slow_down` 间隔加 2 秒;`approved` 时响应含 `api_key`。

**4. 落盘**:把 `{"base_url","api_key","username","role","key_id"}` 写入
`~/.datafoundry/credentials.json`(权限 600)。

**5. 就绪报告**:`/auth/me` 确认身份,`GET $BASE/billing/me` 读余额,
向用户报:"DataFoundry 已就绪:账号 X,余额 Y。估算免费,跑精炼按样本数计费。"

## 打开看板(免密直登——人想在浏览器看账时,任何时刻可用)

用存盘的 key 换**一次性登录链接**(120 秒、单次消费;裸 key 永不进 URL):
```bash
curl -s -X POST $BASE/auth/web-login -H "X-API-Key: $KEY"
```
把 `"$BASE"+login_path` **交给用户**(能开浏览器的 harness 可直接打开):
点开即进看板,不用再输密码。过期或已用就重新执行本步取新链接。

## 失败分支

- 服务不可达:报地址与错误,建议稍后再试(免费实例有冷启动,首次请求可能要等 ~50 秒)
- 授权超时(expires_in 到期):重新 device/start,旧码作废
- 余额为 0:不是障碍——估算(estimate)永远免费,先算账再决定充值

## 纪律

- 用户密码只在注册那一刻生成并交给用户,不写日志、不存对话
- 术语用平台统一语言:估算/配方/漏斗/死因/幸存集(见 datafoundry-refine)
