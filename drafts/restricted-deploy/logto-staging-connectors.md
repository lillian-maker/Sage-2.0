# G3 Logto staging 连接器 runbook

- 日期：2026-11-25
- 状态：completed（邮件路径）。SES SMTP 邮件连接器已配置并通过真实登录验证码发送；SMS 拆分为 Sage 票据 `G3-SMS` 待办；Google 按用户决定不启用（无中转）。
- 依赖：G1（`auth.staging.sagegogo.com` 已可用）
- 票据：Sage 仓 `docs/plans/2026-11-25-account-unification-logto-tickets.md` G3

## 1. 连通性实测

在 staging 主机 `81.68.192.59` 与 `sage-logto-staging-logto-1` 容器内：

```text
sms.tencentcloudapi.com     host 200 / container 200
ses.tencentcloudapi.com     host 200 / container 200
oauth2.googleapis.com       host ERR / container ERR
www.googleapis.com          host ERR / container ERR
smtp.qcloudmail.com:465     主机可达
smtp.qcloudmail.com:587     主机可达
```

生产 egress 现状：

```text
sage-preview-egress-1
SAGE_EGRESS_ALLOW=ses.tencentcloudapi.com,captcha.tencentcloudapi.com,sms.tencentcloudapi.com,openapi.alipay.com
SAGE_EGRESS_UPSTREAM_HOSTS=oauth2.googleapis.com,www.googleapis.com
SAGE_EGRESS_UPSTREAM=            # 空
egress-relay.service: inactive
```

结论：短信与 SES 可直接接。Google 没有可用出口；生产 egress 也没有配置中转。Google 登录必须等中转就绪。

主机凭据现状：`/opt/sage-docker-preview/shared/secrets` 只有 DB/TLS/支付材料；`shared/auth/auth_credentials.template.json` 只有键名模板；运行中的 `sage-preview-app-1` 没有 SMS/SES/Google 连接器环境；`deployment.env` 只有 `SAGE_SES_REGION` 与 `SAGE_GOOGLE_REDIRECT_URI`。因此没有可复用的生产连接器凭据，也没有已部署的 auth overlay。

## 2. 凭据放置

不要把凭据发到聊天或仓库。在 staging 主机建：

```text
/etc/sage/logto/connectors.env     root:root 0400
```

内容：

```text
# Tencent Cloud SMS
SMS_SECRET_ID=<SecretId>
SMS_SECRET_KEY=<SecretKey>
SMS_SDK_APP_ID=<SDKAppID>
SMS_SIGN=<签名>
SMS_REGION=ap-guangzhou
SMS_TEMPLATE_REGISTER=<模板ID>
SMS_TEMPLATE_SIGNIN=<模板ID>
SMS_TEMPLATE_FORGOT=<模板ID>
SMS_TEMPLATE_GENERIC=<模板ID>

# Tencent SES SMTP
SES_SMTP_HOST=smtp.qcloudmail.com
SES_SMTP_PORT=465
SES_SMTP_SECURE=ssl
SES_SMTP_USER=<SMTP 用户名>
SES_SMTP_PASSWORD=<SMTP 密码>
SES_FROM=<发件人地址>

# Google
GOOGLE_CLIENT_ID=<OAuth Client ID>
GOOGLE_CLIENT_SECRET=<OAuth Client Secret>
# 非大陆 HTTP CONNECT 代理，例如 hk-relay.example:3128
GOOGLE_EGRESS_RELAY=host:port
# 非空时用于测试 Google 登录；为空则跳过该连接器
GOOGLE_EGRESS_READY=0

# 测试收件人（只用于 staging 冒烟）
TEST_PHONE=+86...
TEST_EMAIL=...
```

规则：

- 实际 `/etc/sage/logto/connectors.env`（0400）已写入 SES SMTP host/port/secure/user/password/from；SMS 与 Google 项未写入。
- 短信模板只允许单占位符 `{1}`；Logto 四类用途各一个模板。
- 现有合作方模板若为多占位符或用途不匹配，需重新申请；不得用生产模板冒充 staging 验证。
- SES 只使用 SMTP 凭据；不得复用生产 API 密钥文件。
- Google 凭据只用于 staging client；回调地址以 Logto 连接器页面显示的 Callback URI 为准。

## 3. Tencent Cloud SMS connector（未执行，转 G3-SMS 待办）

本批不配置短信。Sage 票据 `G3-SMS` 记录输入、步骤、验收与回滚；凭据到位后按该票执行。

Logto Console → Connectors → SMS → Tencent Cloud SMS：

| 字段 | 值 |
| --- | --- |
| AccessKey ID | `SMS_SECRET_ID` |
| AccessKey Secret | `SMS_SECRET_KEY` |
| SDK App ID | `SMS_SDK_APP_ID` |
| Sign name | `SMS_SIGN` |
| Region | `SMS_REGION` |
| Templates | Register / SignIn / ForgotPassword / Generic 各一个 |

验收：

1. 注册用途：用 `TEST_PHONE` 收一次验证码。
2. 登录用途：退出后重新登录，收一次验证码。
3. 找回密码用途：发起一次找回，收一次验证码。
4. 错误模板/未配置用途返回具名错误，不静默回退。
5. 日志不含验证码、手机号或 Secret。

## 4. SES SMTP email connector（已配置并验证）

Logto Console → Connectors → Email → SMTP。

已创建 SMTP 连接器，实际配置：

| 字段 | 值 |
| --- | --- |
| Host | `smtp.qcloudmail.com` |
| Port | `465`，Secure=true |
| Auth | `login`，user=`noreply@notify.sagegogo.com`；`auth.pass` 已保存 |
| From Email | `noreply@notify.sagegogo.com` |
| Name | `sage-staging` |
| Timeouts | connection 120000、greeting 30000、socket 600000、dns 30000 |

注意：Logto 1.46 的 SMTP 连接器把 `auth` 作为 JSON 字段保存。控制台保存时会省略未填写的 `pass`；只带完整 config 调用 `/test` 返回 204 仅证明该次请求的凭据有效，不证明已保存配置。必须在控制台 Auth JSON 中写入 `pass` 并保存，数据库 `connectors.config.auth.pass` 才有值。

已启用登录方式：`email` + verification code（首位），保留 `username` + password。以下为真实登录路径的验收证据：

```text
POST /api/experience/verification/verification-code  200  433ms  42b
sign-in page: 验证码已经发送至你的邮箱 noreply@notify.sagegogo.com
```

- 验证码发送使用已保存的连接器配置；SMTP 拒绝时该接口会返回错误，不会返回 200。
- 测试收件地址使用发件地址 `noreply@notify.sagegogo.com`；未读取该邮箱收件箱，因此“SMTP 接受并完成发送”已证明，“收件箱可见”未证明。
- 日志未出现 SMTP 错误。

## 5. Google connector（不启用）

用户 2026-11-25 明确：没有可用中转时不开 Google 登录。当前不创建 Google 连接器，不改生产 egress，也不申请 staging Google OAuth client。

前置：一个可用的非大陆出口中转，或另一条能访问 `oauth2.googleapis.com` / `www.googleapis.com` 的路径。

Logto Console → Connectors → Social → Google：

| 字段 | 值 |
| --- | --- |
| Client ID | `GOOGLE_CLIENT_ID` |
| Client Secret | `GOOGLE_CLIENT_SECRET` |
| Callback URI | 使用 Logto 页面显示的值，加入 Google Console 的 Authorized redirect URIs |

出口选项（三选一，必须先验证）：

1. **复用生产 egress**：把 `logto` 接入 `sage-preview_egress_internal`，设置 `HTTPS_PROXY=http://egress:3128`；同时给生产 egress 配置可用的 `SAGE_EGRESS_UPSTREAM`。这会改生产网络/容器，需单独批准。
2. **独立 staging egress**：在 staging stack 增加一个 egress 容器，使用同一不允许私网、仅 443、host 白名单的代理，并配置非大陆中转。
3. **其他代理/relay**：由你提供 host:port 与访问边界；验证后再接。

注意：Logto 是 Node 服务，是否遵循 `HTTPS_PROXY` 需要实测；不能假设。若 Logto 不使用代理，方案 1/2 都需要网络层中转，而不是只设环境变量。

验收：

1. 浏览器打开 `https://auth.staging.sagegogo.com`，用 Google 登录完成一次回调。
2. token 端点与 JWKS 均可达；失败时具名报错，不静默降级。
3. 日志不含 client secret、code 或 token。

## 6. 失败与回退

- 任一连接器未验收：保持关闭；不得用用户名/密码或本地试用身份冒充。
- 短信模板未审批：关闭短信登录；邮件/Google 可独立验收。
- Google 出口未就绪：关闭 Google 登录；G4 官网只启用已验收的方式。
- 不在生产 compose、生产 egress 或生产凭据上做未批准的修改。

## 7. 未决输入

1. Tencent SMS SecretId/SecretKey、SDKAppID、签名、四类模板 ID。
2. 测试手机号；测试邮箱（用于收件箱可见性复验）。SES SMTP 用户名/密码与发件人已提供并验证。
3. Google OAuth client ID/secret 与可用出口中转；当前按用户决定不启用。
4. 是否允许 staging 复用生产 egress（默认不）。
5. CAPTCHA 方案：先用 Logto 自带限流/验证码，还是接腾讯验证码。
