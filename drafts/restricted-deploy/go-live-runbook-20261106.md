---
title: Sage 官网上线与三种登录接通执行单
date: 2026-11-06
status: 待批准，未执行。每个阶段单独批准，批准一个阶段不代表批准下一个
scope: ins-5ag0h225 上的 sage-preview 项目更新、公网开放、邮箱/Google/手机登录接通
---

# Sage 官网上线与三种登录接通执行单

先读 `docker-deployment-acceptance-plan-20261015.md` 第 6、7 节（验收矩阵、回滚与禁止动作）。本执行单只补充这次更新的具体步骤，不替代其中的规则：只停止本项目、不执行全局 prune 或 `down -v`、数据库恢复先到独立卷验证、凭据不进聊天、仓库和日志。

## 0. 已确认的决定（2026-11-06）

- 邮箱、Google、手机三种登录同时上线；人机验证正式套餐在真人验收前购买。
- 邮件改用腾讯云 SES 广州地域（境内，与隐私政策一致）；香港资源在广州验收通过后再删除，删除前单独确认。
- Google 服务端请求经香港中转（`egress-relay.service`），中转服务器由用户购买。

## 1. 候选版本

| 项目 | 内容 |
|---|---|
| 代码 | `feat/website-launch-polish`（官网）与 `feat/auth-launch`（部署配置）合并后的固定提交；执行前在此记录完整 SHA |
| 新增部署文件 | `compose.auth.yaml`（凭据与短信参数）、`compose.egress.yaml`（出口代理）、`compose.public.yaml`（公网 80/443）、`egress-relay.service`（香港中转） |
| 本地证据 | PostgreSQL 全量 1102 项；`auth-config-audit.py` 31 项；`egress-proxy-test.py` 6 项；`egress-config-audit.py`；`gateway-redirect-audit.py` 7 项 |
| 未完成的本地证据 | `docker-audit.py` 完整拓扑：2026-11-06 镜像构建通过，但 Docker Hub 认证接口超时，拉取数据库镜像失败，没有跑完。**执行阶段 A 前必须补跑通过** |

## 2. 阶段与批准点

| 阶段 | 内容 | 需要谁批准或操作 | 通过条件 |
|---|---|---|---|
| A 服务器更新（仍不公开） | 只读预检 → 备份并在独立卷恢复核验 → 迁移计划审阅 → 迁移 → 更新 app/web 镜像。所有登录开关保持关闭，只用 `compose.yaml` | 用户批准维护窗口 | 回环 9443 首页与新构建一致，健康检查 200，旧数据完整，原有宿主机服务不受影响 |
| B 公网开放 | 追加 `compose.public.yaml`；安全组新增入站 TCP 80/443（IPv4），IPv6 保持拒绝；DNS 核对 `@`、`www` 指向 81.68.192.59，保留全部 MX/TXT | 用户批准安全组与 DNS 变更 | 外部网络访问 `https://sagegogo.com` 证书校验通过；`http://` 与 `www` 都 301 到主域名；22/5432/8000/9443 从公网不可达 |
| C 出口代理 | 追加 `compose.egress.yaml`；先不配置 `SAGE_EGRESS_UPSTREAM` | 用户批准 | egress 日志只出现三类腾讯云主机；其他主机被拒绝；app 仍无直连外网 |
| D 香港中转 | 用户购买香港实例，安全组只放行来自 81.68.192.59 的 TCP 3128；安装 `egress_proxy.py` 与 `egress-relay.service`；把 `SAGE_EGRESS_UPSTREAM` 设为 `<香港内网或公网 IP>:3128` | 用户购买并批准 | 从 egress 容器经中转访问 `www.googleapis.com/oauth2/v1/certs` 返回 200；中转日志只出现两个 Google 主机；其他来源连接 3128 被安全组拒绝 |
| E 凭据与参数 | 用户在服务器私有目录编辑 `auth_credentials.json`（SES、CAPTCHA、Google、SMS 密钥，属主 10001:10001、权限 0400）；操作者环境填写非秘密参数（见第 3 节）；运行 `check_auth_readiness` | 用户本人填写密钥 | 只读就绪检查按用途报告齐全；输出不含任何密钥 |
| F 开关与真人验收 | 法务确认后打开条款就绪；按邮箱 → Google → 手机顺序打开开关，每打开一项立即真人验收；Google 回调在网关上从 403 改为放行，作为单独一步 | 用户逐项批准并完成人机验证、收码、密码输入 | 验收矩阵逐项通过（见第 4 节） |

## 3. 非秘密参数（阶段 E 由操作者环境提供）

| 变量 | 值 | 来源 |
|---|---|---|
| `SAGE_SES_REGION` | `ap-guangzhou` | 已确认 |
| `SAGE_EMAIL_FROM` | `Sage <noreply@notify.sagegogo.com>`（若广州改用新子域名则相应调整） | 广州发信地址创建后回读 |
| `SAGE_AUTH_REGISTRATION_SES_TEMPLATE_ID` 等 5 个邮件模板 ID | 待广州模板审核通过后填写 | 用户提供 |
| `SAGE_CAPTCHA_APP_ID` | `197617419` | 已核验的应用 |
| `SAGE_GOOGLE_CLIENT_ID` / `SAGE_GOOGLE_REDIRECT_URI` | 正式客户端 ID / `https://sagegogo.com/api/auth/google/callback` | 公网开放后创建正式客户端 |
| `SAGE_SMS_REGION` / `SAGE_SMS_SDK_APP_ID` / `SAGE_SMS_SIGN` / `SAGE_SMS_ALLOWED_COUNTRY_CODES` / 各用途模板 ID | 待短信资质、签名和模板审核通过 | 用户提供 |
| `SAGE_AUTH_REGISTRATION_TERMS_*` | 默认指向已发布的条款与隐私政策，版本 `2026-11-05` | 法务审阅后如有修改，更新版本号 |
| `SAGE_EGRESS_UPSTREAM` | `<香港中转地址>:3128` | 阶段 D |

## 4. 验收矩阵（阶段 F，真人执行）

| 用途 | 正向 | 反例 |
|---|---|---|
| 邮箱注册 | 人机验证 → 收到广州发出的验证码 → 设密码 → 进入账户 | 错码、过期码、重复发送限流、未同意条款不能注册 |
| 密码登录 | 正确密码 + 人机验证成功 | 错误密码不泄露账号是否存在；连续失败限流 |
| 找回密码 | 收码 → 重设 → 全部旧会话失效 → 收到密码已修改通知 | 旧验证码不能复用 |
| Google | 新用户注册、已绑定账号登录、同邮箱账号确认绑定 | 用户取消、回调错误、state 不匹配均不建号 |
| 手机 | +86 注册、登录、找回、密码修改通知、敏感操作验证 | 非允许国家码拒绝；签名与模板与审核一致 |
| 日志 | 网关与应用日志不含查询参数、验证码、密钥 | Google 回调的 code/state 不出现在任何日志 |

## 5. 回滚

- 阶段 B 出问题：先在安全组撤销 80/443 入站，再去掉 `compose.public.yaml` 重新启动 web；DNS 回滚不能代替立即隔离。
- 阶段 C/D 出问题：去掉 `compose.egress.yaml`，app 回到无出口状态，登录开关保持关闭；香港中转可直接停止服务。
- 阶段 F 出问题：关闭对应开关即可停止该登录方式，已注册账号与会话保留；不回退数据库。
- 阶段 A 出问题：按验收方案第 7 节，用记录的旧镜像与配置恢复；存在新写入时不能直接用旧备份覆盖。
