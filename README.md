# SnapGen OpenAI-Compatible Gateway

逆向 `GenV-SnapGen_3.7.2.apk`（Flutter AOT）+ 真机流量抓取后得到的上游接口封装。
**移动端通道已于 2026-11-04 动态调试中完整打通**（模拟器 + 自研 MITM + Pairip 许可证补丁）。

## 逆向结论（全部实测验证）

| 项 | 结果 |
|---|---|
| 移动端 API | `https://api.geminigen.ai/mobile/v*` — **生成端点不需要任何签名** |
| 平台校验真相 | 不在 `x-token`。`/mobile/v1/login` 的 body 字段 `platform:"GenV-APP"` 决定账号进入哪个凭证库；**web 池账号调 mobile 端点 → INVALID_PLATFORM**。用 `platform:"GenV-APP"` 注册的账号则畅行无阻 |
| `x-token`/`x-timestamp` | 仅出现在 `/mobile/v1/login`、`/mobile/v1/user/*`；服务端**不校验**（垃圾值/缺失均放行） |
| 移动端请求头 | `authorization: Bearer <jwt>` + `x-app-version: genv-android/3.7.2` + `User-Agent: Dart/3.12 (dart:io)` |
| Web API | `https://api.snapgen.ai/api/*`，`Authorization: Bearer` + `x-guard-id` 签名（`guard.py` 已复现） |
| 任务模型 | multipart POST → `{uuid}` → `GET /mobile/v1/history/{uuid}` 或 `/api/history/{uuid}`；status: 0排队 1处理 2完成 3失败 |
| 结果 | `generated_video[0].file_download_url` / `generated_image[0].file_download_url`（R2 CDN，签名 7 天） |
| 配额 | `GET /mobile/v1/user/quota/models` → 每模型 family limit/used/remaining |

## 已验证可用的模型（端到端出片）

| 网关 id | 上游 endpoint | 实测 |
|---|---|---|
| `muse-video` | `POST /mobile/v4/video-gen/muse` | ✅ 提交→完成（~30s 出 mp4，10s 时长，竖/横/方） |
| `grok-1.5-lite` | `POST /mobile/v4/video-gen/grok-1.5-lite` | ✅ 提交→完成（6s/10s，480p/720p） |
| `nano-banana-2` | `POST /mobile/v4/generate_image` | ✅ 提交→完成（~45s 出图，必填 `resolution` 1K/2K/4K、字面 `aspect_ratio` 1:1/16:9/9:16/4:3/3:4） |
| `nano-banana-pro` | 同上 | ✅ 已接受（生成耗时较长） |
| `veo-3.1-fast/lite/3.1` | `POST /mobile/v4/video-gen` | ⚠️ 提交可入队但当前 `SYSTEM_ERROR` 失败（上游侧）；`aspect_ratio` 要字面 `16:9` |

## 免费额度经济模型（关键）

- 每个**新注册 GenV-APP 账号**：~5 次**免广告 0 积分**生成（muse/grok-lite/nano 实测扣 0 credit）+ 10 plan credits + 各 quota ×100。
- 用尽后所有提交返回 `AD_REWARD_REQUIRED`（要求看激励广告，AdMob SSV 服务端校验，不可伪造）。
- **无限调用 = 账号轮换**：`python account_pool.py N --app` 批量建 app 账号；网关在 `AD_REWARD_REQUIRED`/`NOT_ENOUGH_CREDIT` 时自动轮换，池耗尽时**自动现场注册新号**续上。
- `POST /mobile/v1/user/reset-plan` 存在但返回 `reset:false`（不能绕过广告门）。
- Veo/Nano/vela-free 等在配额用尽的账号上同样报广告门；新账号上 veo 可入队。

## 其余通道状态

| 通道 | 状态 |
|---|---|
| web `vela-video`（0积分免turnstile） | 提交 200 但当日全 `HIGH_TRAFFIC_ERROR`（免费层拥堵） |
| web `sd-2-lower` | `MODEL_UNDER_MAINTENANCE` |
| web 图片模型 | 全要 Cloudflare Turnstile；自动化采集被 600010 拦截，需外部打码或人工 token 经 `X-Turnstile-Token` 传入 |
| grok-lower / grok-video / grok-image / omni-flash | `required_pro:true`，免费账号 quota=0 |

## 运行

```bash
# 账号池（app 通道必须 --app；web 通道用普通注册）
python account_pool.py 5 --app        # 批量建 GenV-APP 账号 -> accounts_pool.json
export GATEWAY_API_KEY=<可选>
python main.py                        # 127.0.0.1:8400
```

## OpenAI 兼容接口

```bash
curl localhost:8400/v1/models

curl -X POST localhost:8400/v1/videos -H 'Content-Type: application/json' -d '{
  "model":"muse-video", "prompt":"a red apple rotating", "size":"9:16", "seconds":10 }'

curl localhost:8400/v1/videos/video_xxx            # {"status":"completed","url":"...mp4"}
curl -L localhost:8400/v1/videos/video_xxx/content # 302 -> CDN

curl -X POST localhost:8400/v1/images/generations -H 'Content-Type: application/json' -d '{
  "model":"nano-banana-2", "prompt":"a fox", "size":"1024x1024" }'

curl -X POST localhost:8400/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model":"muse-video","messages":[{"role":"user","content":"a red apple"}]}'

curl -X POST localhost:8400/v1/videos -H 'X-Wait: 300' -d '{...}'  # 同步等待
curl localhost:8400/v1/quota                                      # 上游配额实况
```

## WebUI / 管理面

浏览器打开 `http://127.0.0.1:8400/`。**设了 `GATEWAY_API_KEY` 后 WebUI 有登录门禁**：未登录访问 `/` 得到登录页，输入管理密码（= master key）写 cookie 后进入。

创作台 + 号池 + API Key + QQ Bot + 任务五页：

- **创作台**：prompt / 模型（📱mobile 免 turnstile、🌐web）/ 比例 / 时长 / 分辨率 / 参考图 / turnstile token / 同步等待秒数，结果直接给下载链接。
- **号池**：可用/总数/剩余免费次数统计，账号表（荣誉/普通标记、已用免费额、广告门状态），「一键补充号池」现场走 mail.tm+OTP 注册；阈值策略（最低可用数、批次、自动开关）可视化配置；上游配额实况。
- **API Key**：签发（名称 / 允许模型列表 / 调用上限）/ 吊销 / 删除，key 以 `sgw-` 前缀，scope 越权返回 403。
- **任务**：全量 job 列表（持久化到 `gateway_jobs.json`）。

管理 API（需 `Authorization: Bearer <GATEWAY_API_KEY>`）：

```
GET  /admin/state                  池统计+策略+keys+配额
POST /admin/pool/refill  {"n":3}   一键建号
GET/PUT /admin/pool/policy         阈值策略 {min_usable, refill_batch, auto}
GET/POST/DELETE /admin/keys[/{id}] key 管理
GET  /v1/jobs                      任务列表
```

## 荣誉号池（自动冗余）

- `provision_app_account()` 现场建的 app 账号打 `"honor":true`，与普通手工账号分开统计。
- 轮换优先级：**可用荣誉号 → 可用普通号 → 现场注册**。
- 后台任务每 60s 检查：可用荣誉号 < `min_usable` → 自动按 `refill_batch` 注册补足（`GENV_MIN_USABLE=0` 关闭）。
- 每号 ~`GENV_FREE_GENS`（默认 5）次免广告 0 积分额度，由 `gateway_state.json` 本地账本追踪（上游不暴露剩余次数）；撞 `AD_REWARD_REQUIRED` 自动标记「广告门」不再选用。

## QQ 机器人（官方开放平台 AppID+AppSecret）

在 [q.qq.com](https://q.qq.com) 创建机器人 → WebUI **QQ Bot** 页填 `AppID` + `AppSecret`
（可选沙箱开关）→ 保存后 `bot_qq.py` 自动走 `getAppAccessToken → /gateway → wss` 接入官方网关，
**无需配置任何 QQ 号/群号，默认谁都能用**。状态页显示在线/离线、Bot 账号、最近事件；
「验证凭证」一键测 AppID/Secret 是否有效（token + gateway 探测）。

事件覆盖：群@(`GROUP_AT_MESSAGE_CREATE`) / 私聊(`C2C_MESSAGE_CREATE`) /
频道@ / 频道私信。回复走 `POST /v2/groups/{openid}/messages` 等 v2 接口。
开放平台侧需勾选对应消息事件订阅权限。

备用通道：`bot_onebot.py`（NapCat/Lagrange 反向 WS → `ws://<host>:8400/onebot`），
两套传输共用 `botcore.py` 指令层。

指令（群聊默认需 @bot，可关；私聊直接发）：

```
/help                  用法
/model                 可用模型列表
/<模型名> <提示词>      生成，如 /muse-video 一只猫在海边
/pool                  号池状态
/refill <n>            建号（bot 管理员限定）
```

提交后 bot 后台轮询，完成自动回报下载链接。bot 流量只走荣誉池。

管理 API：`GET/PUT /admin/bot/config`（appsecret 读取时脱敏）、`GET /admin/bot/status`、`POST /admin/bot/test`。

## 文件

- `webui.html` — WebUI（创作台/号池/密钥/任务）
- `usage.py` — 账号用量账本 + 荣誉池阈值策略
- `keystore.py` — `sgw-` API key 签发/吊销/模型范围/次数上限
- `bot_onebot.py` — OneBot v11 反向 WS QQ 机器人
- `provider.py` — 双通道上游客户端（web x-guard-id 签名 / mobile x-app-version；登录、轮询、配额、账号轮换、自动建号）
- `guard.py` — web `x-guard-id` 签名复现
- `models.py` — 模型注册表（channel/字段映射/turnstile/credit/quota 标注）
- `main.py` — FastAPI OpenAI 兼容层
- `genv_account.py` / `account_pool.py` — 邮箱+OTP 全自动建号（支持 `platform="GenV-APP"`）
- `mitm3.py`(VM) — 自研 TLS MITM：解 Flutter BoringSSL 流量 + login 请求改写
- `flutter_ssl_bypass.js` — Frida 证书校验绕过（备用）
- `remote_config_live.json` — Firebase RemoteConfig 实况
- 动态调试产出（VM `C:\Users\devin\`）：`GenV_lic_signed.apk`（pairip 补丁）、`flows.log`（全量抓包）

## 登录/凭证注意

- `platform:"GenV-APP"` 的账号只能在 app 凭证库登录（login body 带 `platform`）；web 池账号反之。
- access_token ~40 分钟过期；网关自动重新登录，无需管 refresh。
- 限流按 `X-Forwarded-For` + 账号计桶，网关客户端无需关心（服务端侧自动轮换）。
