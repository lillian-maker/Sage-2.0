---
title: Sage 注册登录接通准备与协议审阅稿
date: 2026-10-11
status: draft — 未发布、未法律审核，不得开启正式协议就绪标记
scope: 第二项注册登录；不包含服务器、支付、短信开通或正式开放
---

# 先看结论

可以独立做的：修复配置异常、准备邮件模板、完成本地回归、起草正式协议和梳理平台填写内容。

腾讯云重新登录和Google Cloud启用后已成功读取控制台；五类邮件模板全部审核通过，控制台注册模板投递已获平台送达记录及用户收件箱确认。Sage Auth Test项目及应用配置均已创建，页面实测为外部/测试、尚无OAuth客户端，不再重复创建应用。验证码应用和最小权限服务端子用户均已创建并核验，用户已安全保存子用户密钥，助手未读取。仍需配合的：凭据安全注入、协议业务审核、真实挑战及密码输入；最终真实验收还需要服务器任务交付可用的受限HTTPS站点。用户已授权向服务器任务发送一次不含秘密的技术交接，已发送；不会为了Google验证擅自开放服务器。

已确认主体：深圳市众妙之门科技有限公司。对外客服及隐私联系邮箱：lillian.li@sagegogo.com。测试收件人已在当前授权对话指定，不在本文件记录个人邮箱。公司英文法定名称尚未提供，英文稿保留中文法定全称，不自行翻译成另一个主体。

## 一、平台操作准备

### 人机验证

本轮控制台实测：免费体验包已领取并生效，20,000次，2026-10-11至2026-10-18。没有购买正式套餐。平台提示测试包超量或过期会停服，不自动转后付费，不能依赖它正式上线。

最新接续：用户确认“已创建”后，按具体Chrome标签核实应用“Sage 官网注册登录”，公开AppID为`197617419`。首次读取已保存配置发现场景为空、风控为体验优先、域名为空，与批准方案不一致；按已批准范围修正后，重新载入页面并只读取非秘密字段，确认：账号场景、Web/App、始终验证、固定模式/滑动拼图、平衡风控、列表模式仅`sagegogo.com`、域名强校验开启。感知模式及CaptchaAppId强制校验保持关闭，未开启免验证或IP白名单。未读取、复制或重置密钥，未执行真实挑战或服务端票据校验。若后续测试站不使用该精确域名，先核对实际站点再更新配置，不关闭域名校验绕过。

保存经验：所属域名使用独立弹窗保存，会刷新详情并丢弃尚未保存的基础配置；先完成域名及强校验，再保存基础配置，重新载入核验实际值。不能将未提交表单、一次点击或成功提示替代持久化核验。截图仅截非秘密配置区域，排除上方密钥区域。

- 在腾讯云验证码控制台先检查是否已有 Sage 的应用，避免重复创建；名称可用“Sage 官网注册登录”，客户端选择 Web。
- 使用官方前端脚本和服务端 `DescribeCaptchaResult` 二次校验；不使用免验证、降级通过或 IP 白名单绕过测试。
- 绑定的站点按实际测试/正式环境分别填写。`sagegogo.com` 是已确认正式域名；受限测试站域名由服务器任务交付后核对，不先虚构子域名。
- AppID 可以进入页面；AppSecretKey 和腾讯云服务端凭据仅由安全渠道配置，不能截图、输出或写进仓库。若平台创建步骤展示新密钥，交由用户完成安全保存。
- 真人人机挑战在出现时请用户完成；后台校验成功、失败、取消、超时分别记录结果。

依据：[腾讯云验证码快速入门](https://cloud.tencent.com/document/product/1110/36839)。

配置前置检查：`CaptchaAppId强制校验`并不是服务端票据校验的同义项。腾讯云当前文档要求该可选功能开启后前端传入`aidEncrypted`；当前`src/pages/auth.js`和后端流程尚无该参数。不能仅因后台已传`CaptchaAppId`就开启该开关，否则可能阻断全部挑战。若决定接入此防盗刷能力，先确认范围并补服务端生成/前端传递及真实验证，不把密钥发往浏览器；本轮没有更改该开关或削弱已有保护。依据：[Tencent CaptchaAppid鉴权指引](https://cloud.tencent.com/document/product/1110/128489)。

### 邮箱

本轮 Chrome 实查：SES 中国香港区域，初始发信模板0条，随后已提交下面5类模板；独立Chrome窗口新开模板页后回读，5类全部审核通过。最新回读域名`notify.sagegogo.com`为“验证通过”、共享IP、单日最高发信量500封；发信地址页回读`noreply@notify.sagegogo.com`、别名Sage，创建时间2026/10/10 11:10:25。没有设置SMTP密码或读取密钥；后续已授权的控制台邮件实测见下文，网站后端API发信尚未验收。正式使用香港资源前，需完成下方跨境事项审核；不会自行切地域或迁移已有域名。

下列纯文本已填入 SES 模板编辑器，采用中英同封、验证码仅一个变量 `{{code}}`；通知无变量。它们不含测试邮箱、真实验证码或密钥，正文与现有后端发送参数一致。表中状态是本轮最后回读结果，不代表之后自动更新。

| 模板名称 | 文本来源 | 用途配置 | 模板ID / 审核状态 |
|---|---|---|---|
| Sage_Registration_20261011 | [ses-registration.txt](ses-registration.txt) | `SAGE_AUTH_REGISTRATION_SES_TEMPLATE_ID` | 224451 / 审核通过 |
| Sage_PasswordReset_20261011 | [ses-password-reset.txt](ses-password-reset.txt) | `SAGE_AUTH_PASSWORD_SES_TEMPLATE_ID` | 224453 / 审核通过 |
| Sage_GoogleBind_20261011 | [ses-google-bind.txt](ses-google-bind.txt) | `SAGE_AUTH_GOOGLE_BIND_SES_TEMPLATE_ID` | 224454 / 审核通过 |
| Sage_PasswordChanged_20261011 | [ses-password-changed.txt](ses-password-changed.txt) | `SAGE_AUTH_PASSWORD_CHANGED_SES_TEMPLATE_ID` | 224456 / 审核通过 |
| Sage_AccountClosure_20261011 | [ses-account-closure.txt](ses-account-closure.txt) | `SAGE_ACCOUNT_DELETION_SES_TEMPLATE_ID` | 224457 / 审核通过 |

逐个提交后回读模板 ID、名称、类型和审核状态。批准一个不代表其余已批准；账号注销模板仅准备，账号实际注销仍受独立政策开关限制。发送先单次测试，不自动重试；必须检查收件箱/垃圾箱，不能把发送返回成功等同送达。

依据：[腾讯云发信模板](https://cloud.tencent.com/document/product/1288/55193)。

### Google

用户明确批准在当前账号创建测试项目后，Sage Auth Test已创建，项目ID为`sage-auth-test-511306`，概览回读项目名称与ID一致；父级为无组织，没有启动付费试用。Google应用名称Sage；用户单独批准当前Google账号邮箱仅作测试支持邮箱，企业联系邮箱已填写。用户明确同意Google API服务用户数据政策后已完成表单四步。重新打开入口时实际显示“已创建OAuth配置”；本次再读取受众页面确认为“外部/测试”，概览提示尚无OAuth客户端。应用配置已完成，OAuth客户端、密钥和正式发布均未完成。

测试隔离注意：Google官方说明，仅请求`openid email profile`时，存在不受测试用户名单限制的例外。因此“Testing”不是Sage的访问隔离措施，不能据此向公网开放认证API；仍须保持站点受限并由服务器任务验证访问边界。这里记录的是官方规则与当前请求范围的比对，不是未列名账号的真实登录实测。依据：[Google应用受众规则](https://support.google.com/cloud/answer/15549945?hl=en)。

- 复用用户指定且归属公司的 Cloud 项目；开发/测试与生产项目分开配置，不在用户私人项目里随意创建正式凭据。
- 品牌名称 Sage，运营主体及联系地址使用上面的已确认信息。支持邮箱还需是当前 Google 项目可选择的联系人，不能保证自定义域名邮箱一定可选。
- 客户端类型为 Web，权限仅 `openid email profile`。不申请 Gmail、通讯录、Drive 或离线访问。
- 当前代码固定回调路径 `/api/auth/google/callback`。正式候选为 `https://sagegogo.com/api/auth/google/callback`；只有部署实际提供该 HTTPS 路由后，才登记为已可用。回调地址必须精确一致。
- 主页、条款、隐私政策及域名所有权验证按 Google 的要求准备。首页和协议公开可查阅是品牌审核的依赖，不等于现在可以向公众开放注册；公开页面与受限账户 API 的发布范围需由用户及服务器任务确认。
- 新建客户端会产生凭据，需要在该步骤交由用户安全接收；不会让用户把密钥粘贴到聊天。

依据：[Google OIDC](https://developers.google.com/identity/openid-connect/openid-connect)、[Google 品牌验证](https://developers.google.com/identity/verification/authentication-verification)。

### 接续执行清单：不要求用户整理技术参数

| 顺序 | 执行者与动作 | 放行依据 / 不能混淆的边界 |
|---|---|---|
| 1 | 已完成：用户创建验证码应用，助手按批准方案校正并重新载入核验 | AppID `197617419`；未读取密钥，应用配置通过不等于真实挑战或后端校验通过 |
| 2 | 服务器任务交付准确同源HTTPS地址、证书、前端/API路由与受限访问证据 | 2026-10-12开发记录已有固定候选的服务器本机HTTPS资源和安全检查通过；用户浏览器受限访问通道仍待交付。本任务未重新验服务器，不能使用静态分享站替代后端回调 |
| 3 | 助手按真实地址填写Google Web客户端；用户在产生凭据前确认并安全接收 | 精确路径`/api/auth/google/callback`，无query/fragment；只请求`openid email profile`。客户端secret仅创建时可保存，保存到仓库外，不能发聊天 |
| 4 | 已完成身份配置核验；用户已创建子用户并保存密钥，后续由安全入口注入 | 独立用户详情确认仅编程访问、控制台已禁止、组(0)、1条指定策略直接关联；CAPTCHA的AppSecretKey不等于云API的SecretId/SecretKey，不用主账号密钥；未读取凭据、不代表API已测通 |
| 5 | 在受限环境注入非秘密配置及凭据，运行就绪检查，再真人验收 | 配置齐全不等于收信成功、协议审核完成或可公开上线；不提前启用正式协议开关 |

已核实可交接的非秘密参数：`SAGE_CAPTCHA_APP_ID=197617419`、`SAGE_AUTH_EMAIL_TRANSPORT=ses`、`SAGE_SES_REGION=ap-hongkong`、`SAGE_EMAIL_FROM=noreply@notify.sagegogo.com`，五类模板ID见上表。尚需安全注入而不记录值：`SAGE_CAPTCHA_SECRET_ID`/`SAGE_CAPTCHA_SECRET_KEY`、`SAGE_CAPTCHA_APP_SECRET_KEY`、`SAGE_SES_SECRET_ID`/`SAGE_SES_SECRET_KEY`、`SAGE_GOOGLE_CLIENT_SECRET`；Google client ID在实际创建后核对。该清单没有读取部署环境，不代表这些变量在服务器已经缺失或已配置。

本次独立回归：验证码配置核验后，用隔离环境变量运行`backend.auth_readiness_tests`，8项／0.180秒／OK；无数据库、无供应商请求。只读代码复核确认Google回调必须HTTPS、精确路径且无查询或片段，前端/API/session需要同源；本轮没有降低校验或修改认证代码。

凭据依据：[Google Web客户端凭据及仅创建时显示的secret](https://developers.google.com/identity/protocols/oauth2/web-server)、[腾讯云服务端票据校验](https://cloud.tencent.com/document/product/1110/75489)、[腾讯云子账号密钥管理](https://cloud.tencent.com/document/product/598/37140)。最终CAM权限以实际支持的操作和资源粒度核对，不据通用示例直接授予云账户管理权限。

专用调用身份权限（用户已明确批准）：现有后端仅需`captcha:DescribeCaptchaResult`和`ses:SendEmail`。两者官方授权粒度均为操作级，资源必须使用`*`，不能虚构单个AppID或模板资源限制；这不等于允许全部云API，但凭据泄露仍可能被用于调用这两个接口并消耗额度。不开启控制台登录，不授予云主机、用户/密钥管理、模板管理、套餐购买或全量SES/CAPTCHA权限。来源IP限制受平台支持，但需先获得实际服务端出口证据，不填猜测IP。凭据通过安全渠道交接，不写入聊天、仓库或截图。依据：[SES接口授权粒度](https://cloud.tencent.com/document/product/598/70009)、[CAPTCHA接口授权粒度](https://intl.cloud.tencent.com/zh/document/product/598/57067)。

2026-10-11用户明确回复“同意，按上述最小权限配置”后，创建自定义策略`SageAuthRuntimeMinimalAccess`（ID `289325801`）。创建后打开详情回读JSON，确认仅一条allow语句、两项指定action、resource为`*`；版本1为当前版本，尚未关联任何用户、组或角色。该记录证明策略已保存，不代表API调用已通过。

用户完成iframe内图片人机验证及最终创建，并确认密钥已保存。创建结果页仅读取标题“成功新建用户”，未读取密钥。独立打开用户列表中的`sage-auth-runtime`详情，核验访问方式为编程访问；安全页“控制台访问：已禁止”、登录密码为空；组(0)；权限表仅1条`SageAuthRuntimeMinimalAccess`直接关联，时间2026-10-11 17:30:19。向导已提交且权限关联实际存在，不再要求用户重复创建。未查看API密钥页、未读取凭据、未做后端真实调用。

经用户单独授权，已向「Sage 服务器、个人设置和支付」交接公开AppID、模板ID、变量名和验收要求，不包含密钥或测试收件地址。要求该任务提供由用户本人安全注入的入口，以及受限HTTPS、路由与隔离证据；本任务不操作服务器，正式协议未审核前不启用就绪标志。

操作经验：CAM长时间“保存中”不一定是网络故障，主页面无dialog/alert也不能排除安全验证；应检查可见iframe中的提示并请求用户完成，不重复点击、刷新或绕过。新策略编辑器先全选替换，再确认旧模板未被追加，等待语法分析完成；保存后重新打开详情核对实际JSON，不只看策略摘要的服务名。

状态读取经验：CAM标题的原始textContent可能含隐藏的“已注销”文案；本次innerText及子元素可见性核对表明该文案未显示。账号结论应依据可见状态和安全设置，不用含隐藏文本的汇总直接推断注销。

控制台邮件单项检查（已通过）：首次使用注册模板224451、明确的联调测试主题及不可用于认证的测试变量。发信记录显示2026-10-11 20:44:53“请求成功”、20:44:59“退信”；Gmail返回`550 5.1.1 The email account that you tried to reach does not exist.`。用户核对并更正地址后，仅重发1封，主题为“Sage 邮件联调测试（地址更正·仅此1封，无需操作）”；同日20:57:52“请求成功”、20:57:56“送达”，用户随后确认“已收到，在收件箱”。两次发送均有明确授权，不再重发，不在文档保存真实收件地址。已完成的是控制台注册模板投递，不能代替子用户API、注册或找回密码验收，也未验证其余4类模板的真实投递。依据：[腾讯云普通发送](https://cloud.tencent.com/document/product/1288/74737)。此前无外部调用的配置检查测试8项／0.108秒／OK；本次仅更新记录，未改认证代码、未重复跑测试。

投递排查经验：发送按钮点击及统计页均不足以证明送达，统计有延迟、实时队列也有时间窗口和分页。结果不明确时优先按主题及时间查“发信记录”，区分请求成功、送达、退信，不盲目重发。真实收件地址只沿用用户授权值；收到账号不存在退信后，必须由用户核对更正。

## 二、正式协议审核与发布条件

### 已确认的业务规则（不等于法律审核通过）

2026-10-11，用户确认：首期仅面向年满18岁的业务用户；账户资料仅用于账号、安全和服务沟通，不用于广告营销或模型训练；未开放的AI任务和支付不承诺可用。正式服务条款、隐私政策由公司法务负责人最终审核。本稿按这些决定同步中英文，不把确认业务范围视为批准全部协议，也不启用正式协议就绪标记。

法务当前需要给出三组明确结论：各类数据的保存与删除规则；香港SES及Google的接收方/地域/告知和相关手续；适用法律、争议处理、版本与生效日期。工程随后按批准内容落地并验证，不由开发自行宣布合规。

| 待确认 | 背景与影响 | 我可以继续准备什么 |
|---|---|---|
| 数据保存与删除 | 会话、登录安全记录、注销后的必要记录、备份不能全部承诺立即删除；具体期限尚未批准 | 按实际数据库与清理命令列清单，给出最短必要期限的候选，审核后同步配置 |
| 香港邮件与 Google 的数据处理 | SES 当前选中国香港，Google 也涉及境外服务；不能承诺“所有数据仅在境内”，也不能只靠注册勾选替代应有流程 | 列出数据类别、接收方、用途及需审核的路径；不擅自改变地域或宣布合规完成 |
| 服务对象与使用边界 | 年满18岁的业务用户、账号资料用途与未开放能力的边界已由用户确认；未来任务数据不在本次授权范围 | 同步双语待审稿；实际AI任务接入前另行核对处理方式 |
| 审核及生效 | 公司法务负责人负责最终审核；具体正文、版本和正式生效日期尚未批准 | 准备双语发布页面、版本和验收要求；不会把“草案已写”标为“正式协议就绪” |

上述不是法律结论。正式文本须由公司安排法律审核，特别核对保存期限、境外处理、用户权利及未来业务范围。[个人信息保护法官方文本](https://www.cac.gov.cn/2021-08/20/c_1631050028355286.htm)是本稿的核对依据；不使用草案版本或将一般要求冒充已完成的合规手续。

### 数据保留：已经存在什么，还缺什么

| 数据 | 当前代码事实 | 发布前还需决定或验收 |
|---|---|---|
| 注册/找回的临时验证资料 | 验证码有效期300秒；`cleanup_auth_flows`支持清除过期或已消费流程中的联系方式、姓名、密码校验值、Google临时标识等，不删除账号或整条流程记录；会话摘要及防重放相关记录仍保留 | 决定清理频率并交服务器任务配置；另行审核残留记录期限。失效不等于物理删除，也不能为清理而破坏防重放保护 |
| 登录会话 | Cookie有效期7天；`AccountSession`另存客户端、IP、创建与撤销时间 | Cookie失效不等于这些记录删除；确定安全记录期限、清理方案及适用要求 |
| 咨询信息 | 已完成咨询设置90天后可清除个人信息；现有`purge_consultations`默认为dry-run | 确认运营处理方式，验收定时清理；未完成咨询不按该规则自动删除 |
| 注销相关记录 | 删除证明/防重复试用标识使用分别配置的期限；默认0，未确认期限不允许开启自助注销；有独立清理命令 | 审核两种期限，保留人工受理流程；不动账务历史，不把企业移除成员等同个人注销 |
| 日志、备份、Google/Tencent供应商副本 | 本轮未核实生产保存周期和供应商具体处理条款 | 由公司审核期限和境外处理安排；服务器任务提供实际配置与删除/恢复流程证据 |

这张表是代码盘点，不是已经批准的保存政策。本轮未执行任何真实数据清理，也没有修改保存期限。

### 第三方处理清单与页面缺口

| 服务 | 已核实的处理与用途 | 尚需补齐 |
|---|---|---|
| 腾讯天御验证码JS | 官方规则列明系统设置/属性、操作系统、验证交互与传感器、JS运行及IP等数据，用于安全识别；提供方为腾讯云计算（北京）有限责任公司，隐私联系 Dataprivacy@tencent.com。后端另传Ticket、Randstr、AppID及请求IP用于二次校验 | 按用户决定不增加验证码勾选框，沿用底部协议链接；隐私政策补齐验证服务披露。法务仍需确认初始化前的告知与同意方式或适用例外，不能因存在链接就视为已取得同意 |
| 腾讯云SES | 当前区域为中国香港；用于收件地址、验证邮件或通知内容的投递，已批准的模板不代表实际数据处理安排已获审核 | 法务核对所适用合同的接收方法定主体、联系与保存政策、跨境处理依据；不能套用验证码服务的地域声明 |
| Google登录 | 仅请求openid、email、profile；当前代码使用账号标识、已验证邮箱和姓名，不存Google头像，不访问邮箱正文或Drive | 核对适用接收方与地域、用户权利渠道及所需安排；发布准确隐私政策并在OAuth配置中登记。测试受众状态不能代替网站隔离 |

验证码依据：[官方JS个人信息保护规则](https://rule.tencent.com/rule/202402210001)（页面更新/生效日期2024-02-26）及[腾讯云文档入口](https://cloud.tencent.com/document/product/1110/104522)。规则未给出可直接用于Sage的统一数字保存期限。Google依据：[API服务用户数据政策](https://developers.google.com/terms/api-services-user-data-policy)。

2026-10-11展示方案修正：用户明确拒绝新增验证码勾选框，要求参考已打开的Qoder协议。已在Chrome读取[Qoder产品服务协议](https://qoder.com/zh/product-service)（更新2026-04-29）与[隐私政策](https://qoder.com/zh/privacy-policy)（更新2026-05-12），并核对现有登录页：底部两个链接，无勾选框。其隐私政策第10节列出阿里云验证码2.0安全Cookie；协议主体为新加坡公司，不能作为Sage接入腾讯验证码的同意或合规证明。两者可以借鉴的是“用网站隐私政策承载验证码说明，底部提供阅读入口”的信息组织，不是复制第三方、地域、免责或适用法律。

重新读取腾讯规则后确认：要求告知及依法征得同意后初始化，并列明法定例外，但没有指定必须使用勾选框。此前将独立勾选框视为必要交互过度约束，撤回该方案。Sage认证页已有居中的“服务条款 · 隐私政策”结构，无需新增同类组件；正式文本需包含实际验证码处理情况、官方规则链接及权利渠道。若法务判定需在点击时进一步明确表达，可采用按钮附近简短告知，不新增勾选或重复弹窗；具体文案尚未批准，不把普通按钮点击默认为对未展示内容的授权。本决定仅针对新增验证码勾选，不撤销此前确认的首次注册协议确认流程。当前不改供应商、不预加载SDK、不放宽后端验证、不启用正式注册。

2026-10-11用户再次确认后，已在本地认证页的注册、登录、找回密码共用验证码步骤实现按钮附近短说明，并以`aria-describedby`关联按钮。无新增勾选、弹窗或同意字段；保留既有底部链接和首次注册协议流程。中文文案为：“点击开始验证，即表示你同意腾讯云按《验证码个人信息保护规则》处理设备、IP 及验证操作等信息，用于安全验证。”英文含义一致，规则链接指向上述腾讯官方页面、新窗口打开。此为待法务确认的本地展示，不是对其法律充分性的判断；正式协议、版本及就绪开关未修改，未部署或触发真实验证。

跨境审核不能简化成一个普通注册勾选框；适用告知、单独同意、影响评估及其他路径由法务结合真实处理方式判定。[国家网信办数据跨境规则问答](https://www.cac.gov.cn/2025-10/31/c_1763633376984070.htm)说明，部分程序豁免不自动免除其余个人信息保护义务。本稿不认定Sage已满足任何豁免或手续。

### 法务交接包：请审核并回传什么 · 2026-10-12

复用本文件下方中英服务条款、隐私政策共四篇待审正文及上面的数据/第三方清单，不另起一套协议。下表是审核工作清单，不是法律结论，也不因开发完成而自动批准。已确认的公司主体、联系方式、18岁服务边界与账号资料用途不再重复询问。

| 审核项 | 已准备的材料与背景 | 需要法务回传的结果 | 批准后的工程动作 |
|---|---|---|---|
| 正文与业务范围 | 本文件中英服务条款、隐私政策；真实任务、收费及短信尚未全部开放 | 带修订的四篇正文，明确当前版本实际提供的功能和用户权利 | 按批准正文生成可阅读页面，检查中英、移动端和全站入口；不复制Qoder的主体或免责规则 |
| 数据保存和删除 | 上面的保留清单；验证码300秒/会话Cookie7天只是技术有效期，咨询90天是现有实现，不是完整保存政策 | 各类记录、日志、备份及注销标记的期限、起算点、处理责任人；哪些需要继续保留及如何说明 | 对照现有命令确定哪些可直接配置、哪些还缺实现；先预演，再经单独授权执行真实清理及调度 |
| 第三方及地域 | 腾讯验证码、香港SES、Google的实际用途和最小数据范围；不访问Gmail正文/Drive，不存Google头像 | 核实适用的接收方、处理地域、合同、告知及所需手续；指出还缺的材料 | 更新准确披露与链接；有额外要求先确认方案，不能改成免验证或擅自扩大权限 |
| 页面表达与通知 | 用户要求不新增验证码勾选框，已有底部协议链接及按钮旁短说明；首次注册协议确认流程保留 | 明确批准/修改按钮旁文案和初始化前安排；区分必要安全通知与可选产品更新 | 同步双语并测试点击前不加载验证码、未获批准不开放注册；不把通知偏好当营销或训练授权 |
| 发布与追溯 | 现有`agreement_version`与同意时间字段，可复用同一版本对应不可变双语发布包 | 审核负责人批准记录、最终版本、生效日期、争议条款和对外权利受理流程 | 核对正文、配置版本和记录一致后，另按发布授权配置就绪开关；此处不授权上线 |

审核材料不需要包含用户密码、验证码、密钥或真实客户样本。法务回传结论后，我负责把改动逐条映射到页面、配置和测试；发现尚缺工程实现会列为待办，不仅修改文字就宣称完成。

### 发布与工程验收清单

- [x] 公司主体、对外联系、服务人群、账号用途和法务审核责任已由用户确认；双语待审稿已同步。
- [ ] 法务逐项批准正文、第三方清单、保存期限、权利处理流程、争议条款、版本和生效日期；保留批准记录。未决项不能留在正式正文。
- [ ] 采用一个版本号对应不可变的“中英文服务条款＋隐私政策”发布包，保留正文、日期及内容摘要；现有单一`agreement_version`与时间字段可复用，未经确认不增加数据库结构。版本号不能不变而替换实质正文。
- [ ] 生成可公开阅读的四份协议页面，核对中英含义、导航和返回行为；注册页及全站页脚指向同一正式版本。当前`site-shell.js`仍打开“本地产品预览”说明，不是正式协议页面。
- [x] 本地注册、密码登录、找回共用步骤显示验证码短说明与官方规则链接，保留底部协议入口，不新增勾选；中英、移动端及200%字号夹具检查通过，点击前不加载SDK。
- [ ] 法务批准验证码短说明、初始化前告知/同意或适用例外及完整隐私披露，再验收受限站点；不得将仅有链接等同已同意，不通过供应商降级或免验证放行。
- [ ] 通知偏好与实际发送用途核对：用户此前确认的`product_updates`默认开启不在本轮擅改，但不得把该偏好视为广告营销或训练授权；必要安全通知与可选产品更新清楚区分。
- [ ] 受限HTTPS环境核验协议URL成功响应、正文/版本一致、勾选与后端同意记录；当前就绪检查只校验配置，不证明URL正文可读或法律审核完成。
- [ ] 只有审批、页面、记录和安全验收全部满足，才按发布授权设置正式协议版本与就绪开关；本稿不授权部署或公开注册。

本次代码核对：`src/pages/auth.js`的`loadCaptcha()`与按钮事件、`src/pages/site-shell.js`的法律说明、`backend/authentication.py`的`registration_terms_ready()`与`cleanup_auth_data()`、`backend/models.py`的协议与通知字段。未执行数据库写入、清理或真实供应商请求。

## 三、服务条款草案（中文，待审）

**主体与联系。** Sage 官网及本稿所述账户服务由深圳市众妙之门科技有限公司提供。咨询、账号及隐私相关问题可联系 lillian.li@sagegogo.com。正式版本号和生效日期待审核后补齐。

**服务对象。** 首期账户服务面向年满18岁的业务用户。你提交企业资料或代表企业使用服务时，应具有相应授权。

**适用范围。** 本条款适用于官网浏览、注册登录、个人设置和咨询预约。未开放的 AI 任务、真实支付和软件下载，不因页面展示而视为已经提供；后续新增服务适用页面明确说明及另行确认的规则，不默认从你的账户扣款。

**注册和账号。** 请使用你有权控制的有效邮箱或已支持范围内的手机号；你也可选择 Google 官方登录。邮箱、手机号新注册须设置密码并完成相应验证，验证完成后进入官网首页。Google 登录不要求向 Sage 提供 Google 密码。相同邮箱不代表允许自动合并账号，已有 Sage 账号需证明原身份并确认绑定。

**邀请码。** 邀请码可不填；填写后需要验证有效性，用于记录推荐来源。邀请码目前不向注册用户发放奖励，博主分佣由运营人工处理。

**账号安全。** 请保管好密码和验证码，不将账号交给未经授权的人。Sage 支持浏览器或密码管理器的填充、生成和保存能力，不自行保存你的明文密码。发现异常可通过原注册方式找回密码；成功重置会撤销已有登录会话。密码泄露检测与强制重置机制须在实际具备并完成验收后，才可作为已提供能力对外说明。

**个人与企业。** 个人账号注销与企业移除成员不同。企业管理员可撤销成员对企业空间的权限，但不应据此删除员工的其他个人权益。你提交企业资料前，应确认有相应权限；资料责任与实际任务处理规则将在对应服务启用前另行说明。

**停用与注销。** 你可按账户页面提供的入口提出注销请求。涉及剩余额度、退款、企业责任或其他未结事项时，先核对处理方案，不因提交注销请求自动清零购买权益。人工注销申请两个工作日内首次回复，原则上七个工作日内完成；需补充材料或处理未结事项时，由负责人说明原因和进度。依法需要保留的数据按经审核的隐私政策处理。

**服务变化与争议。** 如调整影响账号使用的重要规则，将以清楚的方式说明适用时间和影响；需要你再次确认的事项会单独处理。出现争议可先联系上述邮箱协商；适用法律、管辖及责任条款由公司法律审核后补齐，本稿不预设免责或排除用户法定权利。

## 四、隐私政策草案（中文，待审）

**谁处理信息。** 深圳市众妙之门科技有限公司是本稿所述 Sage 官网及账户功能的运营主体。联系邮箱为 lillian.li@sagegogo.com。本稿只描述已经核对的账户相关处理，不承诺未来 AI 执行器的数据处理方式。

| 功能 | 信息及用途 | 是否必须 |
|---|---|---|
| 邮箱或手机号注册登录 | 联系方式、名字、密码的单向校验值、身份验证记录，用于建立和保护账号 | 使用该方式注册所需；姓氏选填 |
| Google 登录 | Google 的账号标识、已验证邮箱、姓名等基本资料，用于识别账号和明确绑定 | 仅在你选择 Google 登录时；不请求 Gmail 或 Drive 权限；现实现不保存 Google 头像 |
| 协议与推荐来源 | 同意的协议版本和时间；填写且有效的邀请码及推荐来源 | 协议确认用于注册；邀请码选填 |
| 会话与安全 | 登录时间、客户端信息、IP、验证与安全记录，用于会话管理和防滥用 | 账号安全所需；无可靠位置来源时不推测具体城市 |
| 个人资料 | 自行设置的名称、头像、通知偏好 | 头像可不上传、可移除；偏好可调整 |
| 咨询预约 | 主动提交的公司/品牌、联系手机号、选填工作邮箱和问题 | 不影响普通浏览；仅在你提交预约时处理 |

**登录技术。** 登录会话和防请求伪造机制使用必要 Cookie。浏览器保存密码由你的浏览器或密码管理器决定；Sage 不自制“记住明文密码”的存储。选择开始腾讯天御人机验证时，相关服务处理系统与设备信息、验证交互、JS运行信息及IP等安全数据，用于识别自动化与滥用。正式发布前须按本稿第三方清单完成初始化前的告知与同意流程，并由法务审核完整披露。

**第三方与地域。** 账户后端拟使用腾讯云上海服务器；本轮看到的 SES 邮件区域为中国香港，Google 为可选登录服务。邮件发送涉及收件地址、验证码或通知内容，人机验证涉及验证票据与请求 IP；Google 仅用于认证。接收方法定名称、联系方式、处理地域、保存政策及所需告知/同意或其他手续，须审核后补齐。不能将本稿当作已经完成跨境手续的证明。

**保存与删除。** 账号信息在提供相关功能所需期间保存；失效验证码不能继续使用，但“不能使用”不等于记录立即物理删除。会话、安全审计、注销必要记录和备份分别确定期限、清理责任和恢复限制。咨询记录当前代码支持办结后九十天清理，生产调度尚未验收。正式发布前必须填写审核后的期限，不以“永久保存”代替规则，也不承诺无法落实的立即删除。

**你的选择和权利。** 你可以使用个人设置管理可编辑资料、头像、会话和通知偏好；涉及查阅、复制、更正、删除或撤回同意等请求，可通过上述邮箱联系，经必要身份核验后处理。原邮箱等无法自行修改的项目不会被描述成已有自助功能。退出 Google 授权不等于自动注销 Sage 账号，两者需分别处理。

**安全与用途限制。** 采用身份校验、权限限制和安全记录保护账户信息。账号密码、验证码、Google 令牌不写入公开页面或常规日志。账户资料仅用于账号、安全和服务沟通，不用于广告营销或模型训练；首期账户服务仅面向年满18岁的业务用户。后续AI任务的数据处理、对外提供及安全事件通知流程须在对应服务启用前明确并核对实现，本稿不授予未来任务数据处理权限。

## 五、English review copy — not published

### Terms of Service — account and website scope

Sage's website and the account functions described here are provided by 深圳市众妙之门科技有限公司. Contact lillian.li@sagegogo.com for account, service or privacy enquiries. The effective date and version remain subject to review. Initial account services are intended for business users aged 18 or older. You must have appropriate authority to submit company information or act on a company's behalf.

These terms cover website browsing, registration, sign-in, profile settings and consultation requests. Displaying a future feature does not make it available. AI execution, payments and downloads require their own actual availability and applicable notices; registration alone does not authorize a charge.

Use an email address or supported phone number that you control. New email and phone accounts require a password and verification. You may alternatively use Google's hosted sign-in. Sage does not request your Google password. Matching email addresses do not authorize automatic account merging: an existing Sage account requires proof of its original identity and explicit confirmation before connection.

An invitation code is optional, but a supplied code must be valid. It records a referral and does not currently grant a user reward. Referral commissions are handled manually by the operator.

Protect your credentials. Browser password generation, saving and filling are controlled by your browser or password manager; Sage does not store your plaintext password. Resetting a password through the original registration method revokes existing sessions. Automated breach detection and mandatory resets must not be advertised as available until implemented and verified.

Enterprise membership removal and personal account closure are separate. Removal revokes enterprise access, not unrelated personal entitlements. Account-closure requests involving balances, refunds or enterprise responsibilities require resolution rather than automatic forfeiture. Manual closure requests receive an initial response within two working days and are normally completed within seven working days; unresolved matters require an explanation and progress updates. Reviewed retention rules govern data that must remain.

Material service changes will be explained, with additional confirmation where required. Contact the address above about disputes. Applicable-law, jurisdiction and liability provisions await legal review; this draft does not waive statutory rights.

### Privacy Policy — account and website scope

深圳市众妙之门科技有限公司 operates the described Sage website and account functions. Privacy enquiries: lillian.li@sagegogo.com. This draft does not claim to describe future AI execution services.

Email/phone registration uses contact details, the required first name, an optional last name, a one-way password verifier and verification records. Optional Google sign-in uses the provider's account identifier, verified email and basic name information. The current implementation does not store the Google profile photo or request Gmail/Drive access. Agreement versions and acceptance times are recorded. Referral information is recorded only for a valid supplied code.

Session and security records include client information, IP addresses, timestamps and verification activity. A precise location is not inferred without a reliable source. Optional profile information includes an uploaded avatar and editable preferences. Consultation requests use the company/brand, contact phone, optional work email and question that the visitor submits.

Necessary cookies support sessions and request protection. Password-manager storage is controlled by the browser or password manager. When you choose to start Tencent CAPTCHA, the service processes system/device information, verification interactions, JavaScript runtime information and IP addresses for abuse prevention. Provider disclosures and consent before SDK initialization must be implemented and legally reviewed before publication, as recorded in the third-party checklist above.

The intended account server is in Tencent Cloud Shanghai. SES was observed in the Hong Kong region; Google is an optional external sign-in service. Email delivery processes recipient addresses and message contents, and server-side CAPTCHA verification sends the verification ticket and request IP. Recipient entities, contacts, processing locations and any required cross-border arrangements must be reviewed; this draft is not evidence of compliance approval.

Retention periods for account data, security logs, expired verification records, closure records and backups remain subject to review. Expiry does not mean immediate physical deletion. Code supports purging completed consultation information after ninety days, but production scheduling is not yet verified. Final text must state approved periods and actual handling procedures.

Profile settings support the implemented profile, avatar, session and notification controls. Requests for access, copies, corrections, deletion or consent withdrawal may be sent to the contact address, subject to necessary identity verification. Revoking Google access does not itself close a Sage account. Account information is used only for account operations, security and service communications, not for advertising or model training. Initial account services are for business users aged 18 or older. Data handling for future AI tasks, disclosures to others and incident-notification procedures require review before the relevant service is enabled; this draft does not authorize future task-data processing.

## 六、逐项验收记录（未测的保持空白）

| 路径 | 本地隔离测试 | 真实供应商/收件箱 | 最终验收 |
|---|---|---|---|
| 控制台注册模板单封投递（独立前置检查） | 不适用；非网站流程 | 2026-10-11 20:57:56平台送达，用户确认进入收件箱；更正地址后仅重发1封 | 此单项通过 |
| 人机验证通过、取消、失败、过期与重放 | 554项隔离PG回归通过；供应商替身 | 未测；测试包及应用已创建，保存配置已重新载入核验 | 未完成 |
| 邮箱注册、选填邀请码、必设密码、直接首页 | 同一回归通过，包含本地HTTP浏览器 | 网站API及验证码验证未测；上行控制台投递不代替本项 | 未完成 |
| 密码登录、错误提示、不存在账号不泄漏 | 同一回归通过 | 未测 | 未完成 |
| 找回密码、失效码、旧会话全部撤销、通知 | 同一回归通过；邮件替身 | 未测；找回和变更通知模板均已审核通过 | 未完成 |
| Google 新用户、已绑定、同邮箱证明及确认 | 同一回归通过；Google端点替身 | 未测；测试项目及应用配置已创建，客户端未建，受限HTTPS待交付 | 未完成 |
| Google 未登录、取消、回调错误与恢复 | 本地错误/取消和回调分支通过；Google官方未登录页未实测 | 未测 | 未完成 |
| 密码管理器生成、保存、再次选择 | 页面语义已有；原生未测 | 不适用 | 未完成 |
| 正式协议可读、双语版本一致、同意记录 | 草案准备 | 审核和发布未完成 | 未完成 |

2026-10-11：既有隔离PG脚本554项／183.302秒／OK，迁移往返及备份恢复通过，exit0，工具session55099。另有8项不访问数据库的就绪命令测试通过；与邮件测试合跑29项通过，邮件测试已包含在554项中，不重复相加。配置解析修复经独立只读复核无阻断发现。

本地检查没有发送邮件、调用真实 Google/Tencent、改变服务器或启用正式注册。另行授权的云端实际操作包括提交5类SES模板、领取验证码免费测试包、创建Google测试项目及应用配置、创建并核验最小权限策略和用户，以及2次控制台邮件提交（首次退信，更正后1次送达并确认进入收件箱）。验证码应用由用户创建，助手按已批准范围修正并核验保存配置；用户保存凭据，助手未读取密钥。真实验收记录只写用途、结果、时间和必要的脱敏追踪信息，不写密码、验证码、令牌、密钥或完整测试收件人。
