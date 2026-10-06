# Sage 支付开发记录与执行边界

## 官网与账户接续 · 2026-10-06

执行基线先读 `WEBSITE_IMPLEMENTATION_AUDIT.md`。保留现有未提交变更，在 `feat/billing-accounts` 增量开发，不覆盖旧账务实现。新增官网账户、咨询表及迁移0003–0005；迁移仅在隔离测试库执行。正式迁移前必须确认目标库与备份，涉及已产生资料的回退不能直接逆迁移删表。

邮箱保留SMTP模式，新增官方腾讯云SES SDK模板模式。配置名：`SAGE_AUTH_EMAIL_ENABLED`、`SAGE_AUTH_EMAIL_TRANSPORT`（smtp/ses）、`SAGE_EMAIL_FROM`；SES使用 `SAGE_SES_SECRET_ID`、`SAGE_SES_SECRET_KEY`、`SAGE_SES_REGION`、`SAGE_SES_TEMPLATE_ID`。批准模板应包含 `code` 参数。密钥只通过部署环境注入，不写仓库、不读取或打印配置文件。发送超时10秒、不自动重试或跨区故障重发；接口仅确认供应商接收，真实送达单独验收。

手机号验证最后接入，默认关闭。需同时配置 `SAGE_AUTH_PHONE_ENABLED=1`、`SAGE_SMS_TEMPLATE_APPROVED=1`、`SAGE_SMS_ALLOWED_COUNTRY_CODES`（逗号分隔数字国码）及 `SAGE_SMS_SECRET_ID/SECRET_KEY/REGION/SDK_APP_ID/SIGN/TEMPLATE_ID`。批准模板参数按验证码、有效分钟数顺序；不开放未批准国家，不凭同名手机号/邮箱合并身份。SDK合同与登录接口测试均使用fixture，未真实发送。

客户端IP默认来自连接端。只有连接IP精确匹配 `SAGE_TRUSTED_PROXY_IPS` 时才读取 `X-Real-IP`；正式代理必须覆盖此头并阻止直连后端，不能盲信用户提供的转发头。活跃会话不暴露session_key，撤销删除实际Django session并保留撤销标记；旧会话保存失败不能恢复身份。只读账务GET不创建账户会话元数据。

私有头像经真实解码、尺寸限制、重编码和元数据清除，使用 `SAGE_MEDIA_ROOT` 持久目录，不公开整目录。旧文件清理失败写入同事务持久队列，`cleanup_avatars` 默认只读，确认后追加 `--apply` 重试。咨询 `/sales/` 仅对受限销售权限开放，用户和权限管理不在该站点注册；正式销售账号权限需部署时单独授权。`purge_consultations` 默认dry-run，`--apply` 清理已完成满90天的联系信息与文本，保留不含原文的审计；定时执行需部署方配置，不宣称已经运行。部署还需 `collectstatic` 并由同源代理提供管理界面静态资源，不能公开runserver。

账号删除保留规则尚未确认，真实删除及其验证码流程未开发；通知偏好默认关闭。免费体验真实执行器、支付、录屏、安装包及生产部署不在本轮验收内。没有外部服务材料不阻塞可测试接入代码，但不等于生产就绪。存储已写入且数据库持续不可用的双故障可能留下未登记头像文件；目前队列覆盖旧文件删除失败，不宣称所有存储孤儿均可自动恢复。

最终隔离PostgreSQL回归：`PLAYWRIGHT_MODULE=<现有Playwright模块> node tests/postgres-audit.cjs`，324项／68.732秒／OK，包含真实Chrome＋Django HTTP的4条浏览器链路（内存邮箱，不真实发信）和真实PG会话/咨询/账务撤销锁并发；迁移往返、无漂移、独立pg_dump/pg_restore全行/序列/结构/重放不变量通过。证据 `tmp/website-postgres-audit.log`。SQLite邮箱/账户/咨询/SMS45项通过（4项PG并发跳过，已在最终PG运行），另SMS新增3条接口测试与账务撤销测试通过；不将SQLite跳过算验收。`manage.py check`、迁移漂移检查和`git diff --check`通过。

静态构建与内容合同通过（7类页面、21共享路由、50岗位、40文档），英文正文Chrome40个直接地址、320/768/1440px及无JS完整正文通过。账户/咨询Chrome接口fixture通过；双语首页release audit通过，包含6视口、45批场景/头像、地球不重叠和语言菜单键盘。完整七类页面200%文字及正式公网账户链路尚未全面验收。文档无JS可展开全部正文，但查询参数不会自动选篇。

购买页原隔离浏览器回归6组通过／pageErrors=0，含中英320/390/768/1440px及200%文字；英文账户选择框迁至正文后失去宽度约束的问题已复现并用单条CSS修复，未改或跳过断言。证据 `tmp/website-purchase-ui.log`。纯计价规则21项通过。独立空目录复制明确构建输入后复建，dist逐文件diff无差异；这是输入副本复建，不冒充Git干净checkout验证。

最终回归揭示报价201后购买页仍写旧 `.nav-actions > a[lang]`，空节点抛错被误显示为接口失败。已删除三处旧节点写入，URL变化通知既有语言菜单syncLinks，不新增另一套导航；真实购买Chrome HTTP测试在最终PG已通过。敏感账务POST同事务持会话行锁，撤销先提交时等待请求401零写；GET仍只读。

返工规则：Django测试客户端的流式响应必须消费 `streaming_content`，不能直接 `response.close()` 触发测试事务中的连接关闭；计费GET的会话检查只能查询，不能为方便展示产生数据库写入。相关反例已纳入测试。

## 价格页视觉修正 · 2026-10-04

用户确认取消Pro固定突出边框：`src/pages/billing.js`不再生成featured类，删除只服务该类的边框/阴影样式；保留“日常经营”用途标签及各方案独立订阅入口，不设默认购买。build --release、JS语法、git diff --check通过。iab本地8920实际页面检查四卡border均rgb(230,230,227)、box-shadow均none、featured均false；点击Pro订阅进入plan=pro确认页，收费仍关闭。未更新公网或GitHub。三页内容与排版设计见WEBSITE_PAGE_DESIGN；AI团队已选方案1，头像复用现有catalog素材；企业服务原三稿拒绝，按标准产品＋四类定制重写；文档补充八主题和八篇正文初稿及官方研究依据。当前仅设计文档更新，不代表正式三页实现或发布。

## 顺序执行 · 2026-10-04 · 商品参数草稿导入

第一项实际渠道接入尚依赖D15：支付宝已批准产品/SDK与商户归属、银行卡收单方及托管收银台/周期扣款能力。当前无实际网关、签名验签或公共收款回调，不能把内部可信资金记录接口当渠道接通。本轮官方搜索找到[签约接口](https://opendocs.alipay.com/open/02fkal)、[查询状态说明](https://opendocs.alipay.com/support/01rayb)，仅取到搜索摘要、正文抓取超时；不据此确认准入、年扣能力或费率。

顺序推进第二项的可独立部分：新增内部命令`import_billing_products`，复用现有ProductVersion、Credits精确解析与双语权益校验，无schema/依赖变化。默认验证零写入，只有`--apply`才原子插入草稿；重复版本相同为no-op，任何价格/权益冲突均整批回滚，不覆盖已有草稿或已发布版本、不发布、不设置生效时间、不改变售卖配置。新增草稿不是正式参数已经确认。

参数文件为UTF-8 JSON数组，每条必须完整包含：`code`、`version`、`kind`、`audience`、`billing_cycle`、`currency`、`unit_amount_minor`、`credits_quantity`、`minimum_seats`、`entitlements`、`entitlement_version`、`credit_rule_version`、`terms_version`。`unit_amount_minor`为正整数人民币分；`credits_quantity`为普通十进制字符串，不代替待决的业务精度；权益须包含非空`benefits.zh/en`。企业订阅明确`resource_mode=seat_included/shared_only`，至少2席；shared_only内含额度为0。拒绝缺项、未知字段、重复JSON键、NaN、重复版本、其他币种、布尔金额/席位、科学计数Credits等。免费体验不经此付费商品导入。

用法：在受控目标环境运行`python manage.py import_billing_products <已确认参数.json>`；校验结果经核对后，同一命令追加`--apply`。未调用真实/生产数据库，未读取商业参数或凭证文件；本轮只有隔离测试生成fixture草稿。失败自动回滚，成功后也只能形成草稿，发布仍需单独审批。生产执行前确认数据库目标及备份；不要把测试数据作为实际商品参数上传。

验证：先红（命令不存在），初次实现因模型`effective_at`默认非blank导致草稿校验失败；仅对有意留空的草稿日期排除full_clean，保留所有其他字段校验及售卖门禁，未改测试掩盖。最终5项导入测试＋5项现有公开购买门禁测试，SQLite **10项/0.208秒/OK**，日志`tmp/product-import-targeted.log`；涵盖默认零写、幂等、整批冲突回滚、数据库失败回滚、已发布版本不变和企业资源模式。py_compile及git diff --check通过。不声称PG并发/真实渠道联调；按最新要求未重跑全站或整套测试。

后续顺序：等待第一项D15材料接真实渠道；第二项等待D02具体金额/额度后导入批准版本；第三项真实任务仍缺执行端和D11消耗规则；第四项D03–D12管理/变更政策；第五项D12/D14财务角色和票据/核销字段；第六项D01生产环境和OTP服务。已有对应内核不重复开发，不用未经确认默认值勾完包。静态页面未变，分享预览沿用版本24；无新发布、GitHub提交/推送或实际资金副作用。

## 接续实现与验证策略 · 2026-10-04 · 未派发续费授权迁移

R23/W09：已有created、未派发续费意图允许采用新确认授权。原attempt/order/quote、金额、条款digest、有效期及唯一意图均不改；派发原子冻结最新授权版本并向可信调用方明确返回channel。收款、通用交付、订阅交付、退款只读资金核对使用被冻结且有合法授权历史的运输信息，不读取当前授权验证旧交易。实际收款在授权记录损坏时仍保存identity_mismatch_review，不开权益、不吞款。补缴新单复制失败原交易冻结运输和原商业字段，保留旧版本已替换／原款未知／有收款时不得新建补缴的约束。无新schema或依赖。

TDD先复现RENEWAL_INTENT_PENDING拒绝迁移；最终隔离PostgreSQL268项／70.956秒／OK／0跳过、迁移往返／无漂移、3条本地HTTP浏览器及独立pg_dump/pg_restore全行／序列／结构／重放不变量通过（tmp/postgres-audit-latest.log，工具session96613）。覆盖A→B→C、同意图重放、并发更换／派发、异常与过期意图拒绝、授权／派发事务回滚、冻结历史伪造仍保存实收事实、新渠道失败补缴。后加旧工具撤销／移除后旧款交付反例单独SQLite通过1项／0.137秒；不冒充这一步已在上述PG全量重新执行。纯计价规则针对性21项通过，证据tmp/billing-rules-targeted.log。

返工：首轮Playwright路径错误导致3条浏览器测试失败，改用现有bundled安装而非跳过；新增事件夹具补齐必填applied=False。独立审查发现expired未派发原单补缴claim错误调用要求已派发的helper，以及忽略原channel会接受未知／未来授权渠道；两个新增反例先红后绿，补缴选择冻结或未派发原单快照，原渠道按意图创建时历史核验，不允许未来记录解释旧意图。边界测试首次误在外层atomic调用强制autocommit的claim，调整夹具操作位置，不改业务guard。旧HTTP参考测试因sandbox回环绑定PermissionError未完成，本轮不记为36项通过；当前PG里的3条真实本地HTTP浏览器通过不替代该旧参考测试。

用户最新验证策略：后续仅跑改动直接相关的单元／事务／安全反例，真实支付接通后再做整站与渠道全量验收；不反复发布未变化静态产物或跑全站布局，不能省略资金守恒／权限／幂等针对性测试。前端产物未变，cmp和版本24目录逐文件diff通过，分享仍https://sage-billing-preview.lillian-lige.chatgpt.site ，没有本轮重复发布、公网后端更新、真实资金或GitHub提交推送。

W11审查：当前没有Invoice／Contract／银行流水模型，账户发票能力false。R28可申请金额依赖已开与待开事实，不能用缺失数据假定已开为零；D14票据字段／税务／隐私及D12内部角色未确认，不先造发票申请／保留表或额外只读零值接口。当前记录已知付款／退款事实不等于完整开票能力。下一轮按当前TODO缺口表实施不依赖未决条件的部分，外部条件集中记录，不用重复微调替代核心交易闭环。

## 当前前端冻结与分享 · 2026-10-04 · 版本24

R31/R32支付方式记录页的分页、刷新和错误重试原先替换DOM后丢失键盘焦点。先增加Enter分页反例，RED退出1（tmp/account-keyboard-red.log）；复用既有records-title样式，显式操作成功后聚焦结果标题，失败后聚焦role=status错误说明，Tab进入重试，Space重试后返回结果标题。初次读取不抢焦点；旧请求隔离仍保持。未新增按钮、抽象、依赖或CSS。

最终账户42组／43次隔离fixture写／新增财务写0／页面错误0，新增双语320px失败与重试键盘反例，支付方式8布局分页／刷新焦点检查通过，320px英文截图复核。购买6组、订单17组／561 GET／0写、三页24布局／12个200%文字组合、首页90头像批次／12语言视口／11导航及实际3002ms／2998ms轮播通过。对应tmp/*-audit-latest.log为本轮证据。billing-audit第一次误指向纯静态8914，缺少参考API导致seat_quantity读取失败；不改断言，改用脚本既定本地参考服务8900后全量通过。不是生产付款，也不宣称完整WCAG审计或真实BFCache验证。

release构建、JS语法、git diff --check、源与dist中文JS cmp和发布目录逐文件diff通过。本轮后端未变，下方PG262项／67.761秒是上一后端冻结证据，没有本轮重跑。静态版本24 succeeded：https://sage-billing-preview.lillian-lige.chatgpt.site 。version=`appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_9410c21c0b50819199a5f10fedb02c5e`；deployment=`appgdep_6ac12d2a120c81919ec1ff4fb50c53ae`；独立Sites源`83ae5dc44607642f9680c74aae402b1fefa7aaac`。115文件／10,014,720字节；原生hash=`sha256:8faa47989732f94adce84fe7f885bb9c2a0d9b6ffecf974d5a4131f4030bad59`；archive SHA256=`c72e07fbd4803b9faaf9c247e69589b40799b1012bd1954f29446ea047bd6821`；billing.js SHA256=`47738006788f621b239d23a965aa203cb46c2805a62ad8dd431f9ba5b1660ae9`；CSS未变。

下一可执行切片：W09已有created但未派发续费意图在合法授权跨渠道替换后，保留同一attempt/order/quote及不可变商业快照，派发时冻结最新授权。必须同步收款、交付、续期及主动补缴的实际渠道校验，不能只删除RENEWAL_INTENT_PENDING；派发后的历史收款不得读取当前授权。初步只读审查支持复用dispatch_authorization和AuthorizationChange，无需schema，尚未实现／测试。先写完整反例再改现有函数；真实渠道与provider调度仍D15，公开敏感写仍D12。已经产生此类交易后的回滚必须保留冻结渠道兼容读取，不能直接退回只读原订单channel的旧代码。

仅静态公开预览，无公网账户API、真实收费／退款／邮件、生产迁移或GitHub提交推送。W01–W14／T01–T70／D01–D16未全部完成。以下版本23及更早为历史。

## 接续冻结验证 · 2026-10-04 · 续费额外收款核查

R16/R18/R30/R32：可信收款路径保存重复、金额不符或身份不符款项后，原续费及主动补缴的订阅读取投影检查非accepted收款事实，显示既有review/query_original_payment；不靠PaymentAttempt.needs_reconciliation猜测完整状态。accepted交付优先级和资金记账保持，原续费幂等发放一次，补缴交付后原订阅仍可看到其额外收款待核对。不启用退款或新扣款。

长期修正规则：_renewal_state同时被订阅GET和close_expired_renewals调用，不能把展示review直接传给授权决策。首轮审查发现截止日后accepted待交付会因此误触关闭授权；反例先红后绿，policy_state保留原业务态，额外review只改变展示与禁止重复付款。原有异常与关闭逻辑不改。实际运行关闭命令验证零关闭、原有效授权保留，交付仍幂等。重复／金额／身份三种异常用独立savepoint逐项验证，写前与回滚后均为succeeded，避免第一类异常遮蔽后两类；不删除实收事实。savepoint内使用现有收款核心，真实wrapper另单独验证重复实付与交付。

最终冻结全量隔离PostgreSQL262项／67.761秒／OK／0跳过，含3条本地HTTP浏览器、迁移往返／无漂移、独立pg_dump/pg_restore全行／序列／结构及重放不变量。证据tmp/postgres-audit-latest.log及工具session5400；此前262项70.518秒和73.297秒是最终授权修复前结果，不冒充最终冻结。账户UI原40组保留、新增双语核查1组，41组／43次fixture写／新增财务写0／页面错误0，证据tmp/account-ui-audit-latest.log；购买6组通过，旧HTTP与规则36项／0.054秒通过。浏览器首轮sandbox启动拒绝、默认headless-shell缺失不是产品失败；购买测试统一使用其他现有测试采用的本机Chrome，经授权重跑通过，不安装依赖、不跳过断言。回环HTTP首次绑定受sandbox限制，经授权重跑通过。

本轮只改订阅后端、既有测试及记录，无schema、依赖、前端源码或生成页面变化。源与dist中文JS/CSS cmp、发布目录与版本23逐文件diff、JS语法及git diff --check通过；静态预览继续使用已成功版本23：https://sage-billing-preview.lillian-lige.chatgpt.site 。没有重复发布相同静态产物，也没有宣称公网后端更新。真实资金、邮件、生产迁移与GitHub提交推送均无；完整PRD／W01–W14与D01–D16仍未全部完成。下一步继续审查可独立实现的规则缺口，正式政策与渠道依赖留在第二轮清单。

## 当前冻结验证与分享 · 2026-10-04 · 版本23

R11/R31：Enterprise零内含额度的历史服务期不再安排或展示下一发放日；Teams正额度保持月末原锚点、取消后已付期发放和到期边界。读取历史快照，缺失／损坏数量拒绝，不默认零。新增3个测试先红后绿；原取消事务fixture补齐必需的零额度快照，保留回滚断言。无schema或依赖变化。

账户与财务页生命周期：离页同步清除敏感内容并使旧请求失效，恢复重新GET会话、重置busy；旧会话401不能清除新会话。退出结果未知时不冒充已退出；显式重试先重验身份和新CSRF，身份变化需再次点击，不自动POST或重放付款。原36组保留、新增4组；最终工具session93401退出0，40组／43次隔离fixture写（新增仅账户选择及显式退出）／新增财务写0／页面错误0。生命周期用合成事件，不代表真实Chrome BFCache命中或跨设备退出。

返工与长期规则：首次测试误把loading文案当恢复完成，改为等待重验身份并增加fresh-CSRF断言，未弱化验收。实际主内容200%测试发现320px价格周期标签越界（右缘325.39px）；只允许金额与周期标签自然分行，金额不拆、不缩字号、不隐藏溢出。移动端未确认退出提示改纵向排列，重试按钮不挤成逐字竖排。中英三页6个320px主内容200%实际视口截图复核通过，不宣称全浏览器缩放或导航200%。

最终冻结：隔离PostgreSQL260项／74.547秒／OK／0跳过，含3条本地HTTP浏览器、迁移往返／无漂移、独立pg_dump/pg_restore全行／序列／结构及重放不变量；容器已清理。旧规则36项／0.551秒通过；订单17组／562 GET／0写／0页面错误，购买6组／0页面错误，三页24布局／12文字放大组合通过。首页90头像批次／12语言视口／11导航及3002ms／3000ms真实轮播通过。release构建、JS语法、diff及源与dist中文JS/CSS cmp通过。原旧账户日志不当作本轮40组证据，40组以工具session93401原输出为准。

静态版本23 succeeded：https://sage-billing-preview.lillian-lige.chatgpt.site 。version=`appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_9492a24704308191a43ef83d577df561`；deployment=`appgdep_6ac1288e23b081919bb87b9a3c9104cb`；独立Sites源`dc564ecab6fa215c5b112f24878042455ad3d24f`。dist逐文件一致，115文件／10,014,720字节；原生hash=`sha256:9c0004b63caae6c32e539c856194ed15a5965a1211f00452a8061d7c793be11b`；archive SHA256=`8f3e663af95135798a74502d83bbfeb9418736cf6e5fe7018b19ef00f6664074`；billing.js SHA256=`dc5133f9b18ba0f5b4a2b1b8167cbf6552ca61f2d5a6bd7fbbac1e10d79df83f`；billing.css SHA256=`d8b9e10915d01be45751267243a749cf5e92f4612d5a121346be65ffecab4fe4`。

仅静态公开预览，没有后端或公网账户API，没有真实收费／退款／邮件、生产数据库变更、GitHub提交或推送。完整PRD和W01–W14未完成；商业参数、商户审核、执行器、敏感写规则、生产部署等D01–D16仍保留第二轮边界，不将自动执行开发授权解释为新政策批准。下方版本22及更早为历史。

## 当前冻结验证与分享 · 2026-10-03 · 版本22

R25复用`credit_refund_assessment`及既有订单详情：原始、未变更、单周期年度订单返回`scope_ready / annual_future_service_months`，包含原服务期、当前已开始服务月、未来未开始完整月及数量。根据持久化原锚点计算半开月界，月末恢复原锚点；不根据额度发放计数猜日期。来源、商品版本、条款快照、付款异常、多周期、抵扣或未知费用字段均拒绝。所有结果`can_submit=false`；年度不返回金额，不申请、冻结、取消或调用渠道。原Credits精确评估保持。

后端4个新增测试方法先红后绿；本轮完整隔离PostgreSQL **257项／80.143秒／OK／0跳过**，含3条本地HTTP浏览器、迁移往返／无漂移、独立pg_dump/pg_restore全行／序列／结构及重放不变量；临时容器名称过滤为空。年度GET两次查询无INSERT/UPDATE/DELETE，401/403/404/405边界通过。旧规则36项／0.065秒通过。没有schema或依赖变化。

前端原13组保留，年度新增4组，最终 **17组／561 GET／0写／0页面错误**；年度双语4视口的8布局、8个实际200%组合及44个异常响应通过。日期精确到微秒校验，连续性／数量／未知字段／账户与迟到响应拒绝。标签缩为“当前服务月”，保留不退规则移至说明。账户36组／既有16次隔离fixture写、购买6组通过；最终生成三页24布局／12个200%组合、首页90头像批次／12语言视口／11导航及2998ms/3000ms实际轮播通过。源码与dist中文脚本cmp、release构建、JS语法、diff检查通过。

返工记录：第一次年度UI重跑超时是新增夹具等待错误空态文案，不是业务响应错误；改为实际“当前账户还没有订单”及跨账户原单404后完整复测，未弱化断言。长元素截图的skip-link残片不是实际视口遮挡：普通viewport中top=-70px、bottom=-25.25、无焦点；显式聚焦才top=12px。未隐藏控件或改CSS掩盖，增加实际视口截图及几何断言。文案缩短另先红后绿复测。

静态版本22 **succeeded**：https://sage-billing-preview.lillian-lige.chatgpt.site 。version=`appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_d9cf566dc3308191997262fd503045d1`；deployment=`appgdep_6ac1239759a481919219ba1605361c63`；独立Sites源`3a945c721cf12c9918347cd5b2367e8ed3135b13`。dist逐文件一致，115文件／10,014,720字节；原生hash `sha256:f99c502812377b4ff9d4a45e56a976d54c87a8bad07d21df2a51cbf5b993901f`；archive SHA256=`e19cd57a9cd54fe8494673ed7d2f6ddfa55eed4cfdcd9385fefdbd2e33e2ee28`；billing.js SHA256=`18ce5d708ad9291b8b4d11f921ad864102ce9aed518cb53abbcd737391b698db`。同轮版本21为文案微调前历史；短期源授权过期后只刷新同一Site授权并重试。

公开站仅静态，不含后端、文档、测试数据或公网账户API；无真实退款／扣款／消息、生产迁移、GitHub提交或推送。R25只读切片完成，年度退款金额、服务冻结、申请审核、解约联动和真实渠道仍待D03/D06/D12/D14/D15。完整PRD及W01–W14未完成，继续按规则审查尚可独立执行的切片，不把全部缺口归因于商户。下文版本20及更早记录为历史。

## 当前前端冻结与分享 · 2026-10-03 · 版本20

本轮仅修改experience源脚本与既有账户UI测试，另更新PRD配套TODO／覆盖记录；未改后端／schema／依赖／正式规则。账户UI原34组保留，新增2组先红后绿：RED session60924退出1，example上下文缺失；GREEN session50598退出0，**36组／既有16次隔离fixture写／0页面错误，新增试用段0写**。中英320px实际200%文字截图已复核。页面恢复用合成PageTransitionEvent验证，不宣称实际Chrome BFCache命中或真实跨设备退出。

离页同步清报告／下载按钮、撤销Blob句柄并使旧请求失效；persisted pageshow重新GET资格／报告，迟到200、401、失败不能覆盖新结果。读取失败为未知、不重新发资格。登录return取当前页最新路径／场景／示例开合／hash，经auth既有白名单与语言转换；拒绝6类恶意返回地址，旧result/step不冒充服务端任务。start-trial仍disabled。

release构建成功，中文脚本与源cmp一致，英文脚本与既有english_links(translate(source))完整比较一致（它不是源文件字节相同，属于现有本地化转换）。生成版三页24布局／12个200%文字组合、首页90头像批次／12语言视口／11导航与实际3秒轮播通过；node --check／git diff --check通过。后端未变，本轮没有重跑253项；下方版本19的253项66.824秒／0跳过是上一后端冻结证据，不计为本轮新测试。

静态版本20 succeeded：https://sage-billing-preview.lillian-lige.chatgpt.site 。version=appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_994d124e7ab88191b98675b63a62245a；deployment=appgdep_6ac11f4b4d888191a605c8a426ce5acd；独立Sites源b04a5a35293585a507a4744667b6e5ddf6de66a0。dist逐文件一致；115文件／10,004,480字节，原生hash sha256:1d16d62b02a49d9be365ec13a91de188518756ff3a6396a3d2b54f43e6a90407；archive SHA256=f6411f0b076c3464d223af66a4ce370aa2fe0af9b3b3320c337086cf528b2024；experience.js SHA256=642b5e1fb5764bf914c3dfb767d5de63e390873b897a850296ab9ed175b46845。

无公开账户API、真实分析／扣款／外发消息／生产迁移或GitHub推送；全部PRD未完成。下一可开发切片是未变更年度退款服务范围评估；D01–D16详细决策材料已交付但未获新批准。下方版本19及更早发布保留为历史。

## 接续审查与决策交付 · 2026-10-03

第二轮详细选项已集中到主项目`docs/Sage_支付开发计划_TODO_2026-10-02.md` §C.1，D01–D16均有推荐／所需材料／验收边界。明确为未批准提案，不把自动执行开发授权解释为新的收费、MFA、退款或任务政策批准。未新增独立文档或重复PRD；TODO原先“输出选项及推荐”现在仅按该文档交付标完成，不标业务实现。

客户流程只读复现：experience页缺少持久化pageshow重新读取，本次401后仍留旧报告；未登录链接硬编码return=trial丢场景/示例/hash。两个问题均不依赖执行器或商业参数，按既有auth白名单返回、页面恢复与请求版本方式修复并验证，不开放任务启动或模拟分析。

独立财务审查另定位一个可推进切片：未变更年度订单未来完整服务月的只读范围评估。当前credit_refund_assessment只支持Credits，年度申请／冻结尚缺；范围评估不自动涉及D03金额舍入或D06解约，但必须排除变更／抵扣／提前续费复杂单。该项已加入TODO，尚未实现，不能以已有简单annual_refund计算或本次审查替代交付。公开退款／内部财务权限／发票／生产渠道仍受明确未决项约束。

## 当前冻结验证与分享 · 2026-10-03 · 版本19

完整隔离PostgreSQL **253项，66.824秒，OK，0跳过**，含3条本地HTTP浏览器、迁移往返／无漂移与独立pg_dump/pg_restore全行／序列／结构及重放不变量。新增5项先红后绿；原断言保留，临时容器名称过滤为空。旧规则36项0.586秒通过；release构建、JS语法和diff检查通过。三页24布局／12个实际200%文字组合、首页90头像批次／12语言视口／11导航与实际约3秒轮播通过。账户UI34组／既有16次隔离fixture写入，新增支付方式8布局与200%检查；订单通知13组／163 GET／0写／0页面错误、8布局／2个200%组合通过。

R24/R35：支付方式投影增加真实关联的自动充值授权ID、独立状态、版本和日期，按账户／支付方式隔离；UI严格校验，缺失或未知不冒充空记录。旧授权被替代不等于撤销。automatic_topup_supported=false、can_edit=false保留，既有删除影响摘要仍由保存事实生成。数据库日期回读统一UTC；测试比较规范化同一时刻，不修改业务日期语义。

R22/R37：付款通知依据PaymentAttempt.subscription和TopupBudget的持久化关联，区分普通／续费／自动充值付款确认；自动充值失败仅记录已应用且未收款的失败。不开扣款，不解析意图名称，不回填历史通知；付款确认在交付失败后仍独立留存，不宣称额度到账。重复收据／重复失败／回滚／未知结果／已收款后的迟到失败及故意topup前缀的手动购买均有反例。19种已定义类型与未知展示保持双语，原单链接优先；不增加schema或外部消息。

静态版本19 succeeded：https://sage-billing-preview.lillian-lige.chatgpt.site 。version=appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_b37bcddad0d0819187b6d8ac05e0147e；deployment=appgdep_6ac11c1c06d881919b1e7f7b1de4405c；独立Sites源a940f6183960e214d37f9b101e4abe86b52e2723。dist逐文件一致，115文件／10,004,480字节；原生hash sha256:1cd7f2c4d7479facaf360ef7804f9813ac444984edbde26b906525a48cfe7b19；archive SHA256=8e589c546c68172e7f1085e8474e90e0ab187d330deb4ff3044722ea1b43c2b2；billing.js SHA256=391076a18deac938a81b450f97d546f6dbea57bca23c13030ca431b121fa4e46。

仅静态预览，不含后端／真实账户API；没有真实扣款、外部通知、生产迁移或GitHub提交推送。完整PRD／W01–W14未标全部完成，D01–D16及真实渠道／执行端／生产验收保持待办。独立审查未发现缺口不等于覆盖证明，本轮仍查出上述两处；后续继续按规则和调用链复核。下文版本18及更早证据均为历史。

## 当前冻结验证与分享 · 2026-10-03 · 版本18

完整隔离PostgreSQL **248项，64.971秒，OK，0跳过**；含3条实际本地HTTP浏览器，迁移往返／无漂移、独立pg_dump/pg_restore全行／序列／结构及重放不变量通过。原测试保留，本轮增加17项；首次并发fixture装饰器返工见下节。实际名称`sage-billing-pg-test-`过滤确认无残留容器。没有生产库或支付渠道调用。

浏览器：额度流水50 GET／企业历史54 GET，均0写入、0页面错误；各中英4视口及8个实际200%文字组合通过。通知13组／163 GET／0写，8布局和2个200%组合通过，覆盖16种已定义事件及未知类型。账户原33组／既有16次隔离夹具写入、购买6组均通过。生成版三页24布局／12个200%组合、28个参考报价POST通过；首页90头像批次／12语言视口／11导航及真实2999ms／3000ms轮播暂停恢复通过。旧规则36项0.559秒通过；JS语法、git diff --check通过。新增两界面及通知截图已人工复核。

静态分享**版本18，succeeded**：https://sage-billing-preview.lillian-lige.chatgpt.site 。version=appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_903e48adef1481918e9746d97be6e4f7；deployment=appgdep_6ac118a590388191b98ca118c90d10b4；独立Sites源f47c934b93e332632fb233a1313e8ce1dc83ea36。static.directory=dist，逐文件与本地release一致；115文件／10,004,480字节；原生内容hash sha256:52dcd301c5cf9164b9b1e6ddae9ecad3d3700b3ba5031c83c7eb286fcd629a55；archive文件SHA256=1fd30403f855f85303a47b9f12338ae928e523c1baf7e1f67630118b61435cf1；billing.js SHA256=fab4784d99b6a3011376988804d2efbcdbf44bea47a4c8d4c594f3c9cbd7b876。

仅静态站，不包含后端／测试数据／真实账户API，无GitHub提交或推送。正式商户与部署、商业参数、执行端、敏感写验证及D01–D16对应分支仍待；没有将全部PRD或W工作包标完成。后文版本17／231项与更早结果均为历史。

## 接续实现 · 2026-10-03 · 流水、企业历史与状态通知

R10/R35新增`GET /api/billing/credits/transactions?offset=N`：既有财务账户权限，50条分页，按不可变账本ID倒序；数量为精确十进制字符串，预占、结算、释放、冻结、解除和退款分别标记。不输出内部幂等键、成本、凭证或其他成员身份，不从流水推导实时余额。

R12/R13/R35新增`GET /api/billing/organization-history?offset=N`：仅企业财务成员读取，普通成员与个人账户拒绝；分页展示席位／共享上限版本、前后关系、时间及脱敏操作人。缺失历史版本标未知，不将未知null当作无限。不开放配置写入。

R37新增支付／续费失败、企业充值授权／暂停／撤销处理与确认、席位／共享上限配置版本通知。同事务保存、重放不重复、回滚不留下提示；失败仅接受实际应用的失败结果，未知或已确认收款后的迟到失败不产生失败通知。授权记录不是充值成功，配置记录不是已送达成员通知。

`record_expired_notifications`只在可信内部调用中按已保存终点记录批次到期和某一已付款服务期结束，唯一来源保证重放幂等；不修改账本，不扣款，不发送外部消息，不在GET中写入。既有`grant_subscription_credits`命令接入扫描，并覆盖只有加购批次的账户；生产调度尚未配置。文案不推断账户全部停服或其他批次到期，提前提醒时间／外部渠道仍待D14。扩展通知枚举未改变schema。

返工经验：在测试类之间插入新类必须让原类装饰器与原class保持相邻。本轮第一次完整PG248项有10个CONFIG_INCOMPLETE，实际根因为新历史测试类截走原并发类fixture装饰器；已移回原类，未改业务门禁或跳过测试。英文320px实际200%文字复现原页面标题长单词溢出，采用`.page-heading h1{overflow-wrap:anywhere}`最小修复，保持字体／配色与所有布局断言。未构建时的浏览器夹具必须同时route源JS／CSS，避免旧生成样式被误判为当前实现。新鲜全量和发布结果见顶部。

## 当前冻结回归 · 2026-10-03 · 成本、授权、财务与站内通知

完整隔离PostgreSQL **231项，69.152秒，OK，0跳过**，含3条实际本地HTTP浏览器；0020/0021迁移往返、无漂移及独立pg_dump/pg_restore门禁通过。原测试未删除或跳过。旧规则36项0.564秒通过；账户UI33组/既有16次隔离夹具写入、购买UI6组、订单与通知13组/162次GET/0写入，均0页面错误。通知另覆盖中英320/390/768/1440共8布局、2个实际200%文字组合及截图人工复核；三页24布局/12文字放大、首页90头像批次/12语言视口/11导航及3001ms/3000ms真实轮播均通过。node --check/git diff --check通过，隔离容器已清理。

本轮实现及边界：

- R10：`complete_task_cost`追加未知成本补充事实，原来源/账户/证据/幂等校验，原事件、usage和Credits不变；汇总按币种精确累计。0020仅新增TaskCostCompletion，无历史回填；已知成本纠错与实际执行端接入未做。
- R36：`BILLING_RENEWAL_CHARGING_ENABLED=False`独立停止新续费派发，售卖总门禁仍保留；停止不改写未派发意图、不影响原交易查单/确认/交付，也不阻独立新购。自动充值仍硬关闭，不新增可绕过D09/D15的开关。续费测试显式批准fixture开关不代表生产启用。
- R23：可信内部跨渠道切换限定同收款商户/CNY/经审批渠道/Sage执行/无自动重试，版本和账户锁保护；未来周期只改运输channel，原商业字段、条款摘要、初单及旧在途全部不改。旧在途按派发时授权交付。未派发旧渠道意图阻止切换，不擅自重写、删除或再扣；provider调度迁移、公开签约/撤销仍未接。
- 续费根因：公开初单terms_document_digest原先在续费重建时丢失，已先复现再改为可信续费保留整份商业快照；不放宽公开新购门禁。跨渠道失败后的补缴同样保留失败原单；原交易未知或授权版本/mandate已变时不得另建补缴。并发与篡改反例通过。
- R30/R36：既有`report_billing_facts`扩展账户内`--order`/`--transaction-reference`/`--refund`互斥只读查单；refund是现有内部UUID，不冒充PSP退款号。歧义、跨账户和缺记录不任取；金额整分字符串，无证据/授权/联系方式。统计模式`--include-age`观测当前全账户unknown/已付未交付/冻结/未结异常时长，非window历史重建；缺起点未知，未来时间明确异常，不自设阈值。unknown按串行已应用事件ID顺序，不用创建时间或会时钟倒退的created_at排序代替因果顺序。重复零写SQL测试通过。
- R37：0021新增不可变站内BillingNotification，无历史回填。accepted收款、取消处理中/确认、Credits退款预留/成功/未退款终止在原业务事务留存，幂等回放及回滚保护。GET `/api/billing/notifications?offset=N`财务成员权限、account_id/items/offset/next_offset/total、50条分页；固定六类型+unknown，金额为CNY整分字符串，不泄露授权/证据/联系人。中英账单`#notifications`读取、分页、焦点、原单链接、未知/失败重试和迟到响应保护已接，不写已读状态。记录只代表该事件，不代表最新权益状态或银行实际到账。

迁移风险/回滚：0020/0021只建新表，未改变旧数据；空表可回退，已有补充成本/通知事实时不可直接rollback删表，应先备份/导出并向前修复。这里只在唯一隔离库验证，未对任何生产库执行迁移。

最新静态分享**版本17，succeeded**：https://sage-billing-preview.lillian-lige.chatgpt.site 。version=appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_3d74da23d9948191bfc220dc203f4954；deployment=appgdep_6ac11340248881918d9d96d51c77213f；独立Sites源7ba24ecefb27ebdb31535253196ec651f52cb1c2；托管static.directory=dist与本地release逐文件一致。115文件/9,984,000字节，原生内容hash sha256:1771c1c9a01577b2df1c9093bdebc400ceaa410414843f33cb4bf11c68471689；archive SHA256=0b1a870e4f100bf50789a4a365daf20f09dd0f710a964cf2e8c29076694cf029；billing.js SHA256=863906d6e2e3ca05ae55402f9e4c2c6aaa76530a99d6fa753de528e73ddfff0f。仅官网/客户静态页面，不含后端、fixture或真实账户服务，不能真实登录/收款。没有GitHub提交或推送，下方版本16/201项为历史。

下一顺序：继续核对R37其余已有可信状态事件的站内记录（续费失败、充值授权暂停/撤销、席位变化等），不因D14未定邮件时间一概阻断；再完整剩余规格审计。低余额阈值/提前提醒、外部邮件短信、发票、真实渠道、执行器、高风险管理和缺失正式参数仍按D01–D16处理，不标整包完成。

## 当前验证增量 · 2026-10-03 · 隔离备份恢复

tests/postgres-audit.cjs 在本次唯一临时PG16容器完成迁移及完整回归后，创建明确隔离账务夹具：已付Credits订单/重复实收待核查、未知在途付款、已结算任务、预占、冻结及真实成员关系。使用官方 [pg_dump custom archive](https://www.postgresql.org/docs/16/app-pgdump.html) 和 [pg_restore](https://www.postgresql.org/docs/16/app-pgrestore.html)，仅恢复到同一临时容器内新建空 sage_restore，不覆盖源库；备份文件只在容器内，finally清理整容器，不读取生产凭证。

验收：完整public表行/序列值与is_called精确一致；schema/列/约束/索引DDL保留比较；修改恢复后订单状态的负例必须被发现，复原后再次一致。恢复后重放原幂等订单、两条实收事实及Credits交付，无任何行/序列变化；未知付款意图不能重新claim派发。付款是可信内部测试事实，无PSP SDK/网络、邮件或真实收款。

首次夹具在存在任务预占时冻结失败TASK_SETTLEMENT_REQUIRED；修正准备顺序，未绕过或修改业务保护。schema原始文本比较随后因PG重解析CHECK约束的varchar字面量数组整数组text[]转换为逐元素text转换而失败；实际DDL诊断已确认，不是猜测OID排序。只对未加引号约束名的CHECK行、完整一维varchar字面量数组→text[]做窄规范化，其余DDL仍逐字节比较；不排序SQL行、忽略约束或移除类型。不同值/顺序/cast/缺约束/引号内同形字符串反例保留，实际PG检验值/类型及NULL/合法/非法值ANY三值语义相同。任何其他差异继续失败。失败命令保留stderr，隐藏可能含完整dump/身份的stdout。

最终冻结完整回归 **201项，51.513秒，OK，0跳过**；新增恢复门禁在201项之后独立执行并成功，不将其伪计为新Django测试数。迁移往返/无漂移及3条实际本地HTTP浏览器仍通过。node --check/git diff --check通过。UI/静态发布内容未变，保持下方版本16；README仅校正旧版本号，未推送GitHub。

范围限制：只验证当前PG16 public结构及显式Credits/付款夹具，不验证生产owner/ACL（恢复使用no-owner/no-acl）、PITR、跨版本、RPO/RTO或所有订阅/企业业务状态。生产恢复前须停写/停所有新扣款与自动任务，记录备份时间与外部渠道流水边界；备份后可能发生的真实付款不能凭旧库状态重新扣，必须查询/对账补齐事实、核对未决交易并人工批准恢复调度。仅通过此门禁不能开放收费或勾完W03/W14生产恢复验收，D01/D15仍在。

## 当前冻结回归 · 2026-10-03 · 订单历史与当前方案

最新静态分享**版本16，succeeded**：https://sage-billing-preview.lillian-lige.chatgpt.site 。version=appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_c5dbe7e3c1cc8191afd0f3582f9ee997；deployment=appgdep_6ac10ac987e88191a966e630d65ddf82；独立Sites源622c40ca709c7524192b2fb59ddaa9cf5e9bc08f。托管static.directory=dist，逐文件与本地release一致；115文件/9,973,760字节，原生包内容hash sha256:a3c537ef41d693459608d4fe291abf08d7c5fcd27abfe6dfaa6f607418ab34c7，archive文件SHA256=e2aeaf51336855505ec1c0548580ac40c61f15048ecd976c9cedf2dc73900287；billing.js SHA256=d63732eb20e2079310527b187a3b2d308689ae6a871f75cb2d0f0b058d9db1bd。仅网站静态内容，无后端/测试数据/真实账户API；没有GitHub提交推送。后文版本15及较小测试数保留为历史。

订单列表使用独立GET分页而非账户摘要，50条/页，created_at/id稳定倒序，返回account_id/items/next_offset；摘要只读取首50条。详情可按本人原订单UUID直接恢复，不要求订单位于第一页。请求序号、账户版本及DOM绑定拒绝旧页面/旧账户迟到响应；失败不当空记录，只读重试。分页前进/后退移焦至标题，中英320px无横向溢出。

同报价不同幂等键先识别原单再校验新购门禁，返回409 QUOTE_ALREADY_ORDERED；同键同请求在过期或停售后仍恢复原单，不放松同键冲突/权限。4项后端用例先红后绿；未改schema、依赖或正式价格规则。

价格页GET真实subscriptions，显示已付、待生效、仍续费及取消处理中状态；当前档位指向订阅管理，其他档位为查看方案，不冒充已实现升级。读取失败/非法响应为未知，拒绝以此开放新购。席位输入不得重写管理链接，账户/周期切换不得回填旧订阅。英文320px、真实200%文字曾复现按钮组溢出，修复受限宽度/允许换行后保持全部断言通过，字体和白底银灰主题不变。

冻结后全量：隔离PostgreSQL **201项，54.004秒，OK，0跳过**，含迁移往返/无漂移及3条真实本地HTTP浏览器链路；订单审计11组/118次只读请求/0写入，购买6组，账户33组/既有16次夹具写入，均0页面错误。三页24布局/12文字放大组合，旧规则36项0.059秒通过；首页90头像批次/12语言视口/11导航，通过真实3002ms及2998ms轮播/暂停恢复。发布ID另记；真实收费关闭，不提交或推送GitHub。

测试经验：改变响应夹具后同URL page.goto不保证重建页面状态，必须明确reload再断言，不能删除失败断言。三页billing-audit依赖8900参考报价HTTP服务；静态8914不提供API，错误指向静态服务会因缺参考POST记录失败，不能据此改业务代码或跳过测试。新增订单UI审计可在静态8914使用明确隔离响应。

## 当前公共接口契约 · 2026-10-03 · W01/W04

以下为**当前实现**，不是拟议支付平台能力。前端必须从服务端读取真实状态；启用正式收费、部署和渠道准入仍分别验收。所有客户API为同源session，响应禁止缓存；写入使用JSON及X-CSRFToken。账务写入还须X-Billing-Account与当前会话账户一致，owner/billing_admin可办理；普通企业成员仅本人my-usage。内部财务权限未开放，不复用企业管理员身份代替。

| 路由及方法 | 请求与实际返回 | 约束与下一步 |
|---|---|---|
| GET /api/auth/session | authenticated/user/脱敏身份/accounts/active_account_id/csrf_token/验证能力 | 未登录也可读取验证码页面会话；不能将验证能力false画成可发验证码 |
| POST /api/auth/challenges | method/identifier；challenge_id、时效与重发等待 | 邮箱已配置才发送；手机号尚未接通；验证码不返回客户端或日志 |
| POST /api/auth/verify | challenge_id/code；成功后会话投影 | 浏览器绑定、限时、单次核销、失败次数与限流；不合并未验证身份 |
| POST /api/auth/logout | 空对象；ok | 退出撤销会话，前端立即清数据；失败明确未知，不冒充退出成功 |
| POST /api/billing/accounts/select | account_id；当前会话投影 | 必须本人有效成员；前端清旧账户并重读，未知不自动重发写入 |
| GET /api/billing/catalog | 参考计划目录 | 不持久化报价、不代表可售，不能据参考金额创建订单 |
| POST /api/billing/quotes | plan_code/billing_cycle/seat_quantity；参考计算 | 无身份账务副作用；同源Origin检查；没有持久化quote_id |
| GET /api/billing/purchase-catalog | account_id/currency/items，商品UUID、精确金额字符串、purchase_options及quote_available | 未批准商品、协议、期限、渠道、当前账户资格不得开放报价 |
| POST /api/billing/purchase-quotes | product_id/seat_quantity/channel/buyer_region；持久化报价详情 | 禁客户端金额/期限；TTL由已确认配置提供；公共金额与总价须浏览器安全整数 |
| GET /api/billing/purchase-quotes/{UUID} | account_id、快照、amount_minor、expires_at、terms、can_order、unavailable_reason、order_id | 查询恢复不重新报价；过期/停售/已建单仍可读取本人原记录，内部merchant_entity不输出 |
| POST /api/billing/orders | quote_id/agreement_acceptance＋Idempotency-Key；订单快照 | 同键同请求回原单；同键不同请求409；同报价不同键409并通过报价GET恢复原单。停售/过期不阻断已建单的同键恢复 |
| GET /api/billing/orders?offset=N | account_id/items/next_offset，最多50条 | 仅单次offset、1–9位非负整数；未知/重复参数422；稳定-created_at/-id。账户摘要内orders仅首50，不作为完整记录 |
| GET /api/billing/orders/{UUID} | order/payments/payment_attempt/delivery/refunds/核查状态 | 按ID直接恢复本人历史单，不要求出现在第一页；查询不扣款或发权益 |
| GET /api/billing/orders/{UUID}/refund-assessment | 原单Credits精确未用量/参考金额；原始单周期年度服务月范围，can_submit=false | 年度scope_ready无金额；不是申请/审核批准，不舍入、冻结、取消或退款；复杂费用来源及多周期拒绝 |
| GET /api/billing/subscriptions | 当前账户items | 显示当前及已付未来服务、续费/取消状态；不能仅凭第一条或active排除未来已付款权益 |
| POST /api/billing/subscriptions/{UUID}/cancel | 空对象；subscription当前状态 | 本地阻新续费与真实渠道解约分开，未知仅刷新，不自动重发；当前已付款服务保留 |
| GET /api/billing/account | 账户、六类Credits、首50订单及能力 | 未知数据不补零，真实可售目录/历史详情另读对应接口 |
| GET /api/billing/organization、payment-methods | 当前账户分页投影，offset | 仅只读；具体可见字段/权限由服务端决定，不开放管理写入 |
| GET /api/billing/my-usage、automatic-topup | 本人限制／企业独立授权和已记录预算 | my-usage不泄露其他成员及企业资金；自动充值配置不是已开通扣款能力 |
| GET/POST /api/billing/purchase-inquiries | GET账户50条分页；POST公司/联系人/需求等，返回真实UUID回执 | 已保存不等于已签合同或到账；重复表单同窗口返回原回执 |
| GET /api/trial | 本人唯一体验资格、原任务及已存报告 | 只读恢复，无新任务POST、客户端完成或消费回调；真实executor待接 |

金额契约：公共报价及订单金额为整数分JSON number（新公共报价限制安全整数）；目录、余额、退款和统计精确字段为十进制字符串，前端不得统一转Number或浮点计算。CNY-only。报价/订单/协议快照不得因改价或新条款覆盖；售卖开关关闭不等于停止读取原交易。浏览器支付返回、人工回单和测试付款都不是可信实收证据。

错误契约：401 AUTH_REQUIRED；403 FORBIDDEN/CSRF_FAILED；409 ACCOUNT_CHANGED/IDEMPOTENCY_CONFLICT/QUOTE_ALREADY_ORDERED；404 NOT_FOUND；405 METHOD_NOT_ALLOWED（附Allow）；422 非法请求、席位、协议及报价期限校验失败；503 CONFIG_INCOMPLETE/TERMS_UNAVAILABLE/CHANNEL_UNAVAILABLE或未开放功能；未枚举异常500 REQUEST_FAILED，日志仅request_id，不输出内部异常或资料。客户端超时是结果未知，不等于写入失败，先读取原quote/order。错误响应遵循backend/http.py真实结构；业务界面用稳定错误码本地化，不展示原始内部日志。

尚未提供：真实支付尝试发起/支付跳转、签约查单/关单/撤销、退款申请与审核、发票和财务管理写入、公共任务执行、敏感MFA与身份合并。相关旧TODO保持局部未完成，不能因本表存在宣告所有API开发完成。

## 当前基线 · 2026-10-03 · Credits入口与账务事实

Sage Credits账单区GET账户审批目录，只展示可报价的UUID额度包，金额和数量使用精确字符串；选包进入既有确认页，标题“购买 Sage Credits”，明确一次性、到账起12个月，不开启自动充值。空包/不可报价包/读取失败分别展示；不可报价的未定价商品允许null，不伪造金额。账号和请求序号阻止迟到响应回填。中英4组购买UI夹具通过，含320px无溢出、精确金额、未知状态恢复；账户33组通过，0页面错误。三页24布局与12个200%文字组合、旧HTTP/规则36项通过。真实收费、商户与公开API仍关闭，未推送GitHub。

公共报价的JSON number金额需符合浏览器安全整数范围：单位金额及席位总价超过9007199254740991分则CONFIG_INCOMPLETE，拒绝建报价；等于边界可精确读取。内部账务保留PostgreSQL bigint，没有修改价格政策或数据库。失败用例先稳定复现目录错误显示可报价，再修复；个人边界及企业乘席超限均通过。仅公共报价有此技术限制，不宣称支持全范围bigint浏览器订单。

`report_billing_facts --account <UUID> --start <UTC_ISO> --end <UTC_ISO>` 为内部只读命令，需要已有授权环境，不能指向未授权业务库。期间资金流按实收paid_at/已退款resolved_at的UTC `[start,end)` 统计；unknown交易/待交付/未解决异常是指定创建或首次发现cohort的**当前状态**，不是历史时点重建。金额十进制整数字符串；排除待审核重复款及未确认退款，不生成收入确认或转化/续费率。未记录测试/生产账户分类，不能拿本命令结果宣称真实商业收入。无schema/依赖/公共API变更、外呼或资金写入。

隔离PostgreSQL首轮196项仅只读SQL断言失败：Django iterator在PG使用DECLARE命名游标，而SQLite为SELECT。必须验证实际游标只读形态，不能移除iterator或屏蔽写入断言以制造通过；最终回归记录在下方更新。

最终冻结代码回归：**197 tests / OK，54.807秒，0跳过**；包含三条真实本地HTTP浏览器路径，全部迁移往返及makemigrations无漂移通过。游标断言严格识别已声明的Django只读DECLARE/FETCH/CLOSE并拒绝写入/混合语句/未绑定游标，追加反例后不跳过测试。订单核查9组／0页面错误／58次只读请求／0写入；账户33组／0页面错误，保留既有16次夹具写请求；购买确认4组／0页面错误。旧HTTP/规则36项0.559秒通过（首次沙箱禁止loopback bind，获准重跑；不是产品失败）。release构建、JS语法与git diff --check通过。

首页最终复验通过：90个头像协作批次、12个中英文视口、11个导航链接；中英文真实轮播间隔实测2999ms，两次自动切换及暂停/恢复均通过。三页24布局、12个200%文字放大组合通过。日志位于项目tmp，仅作为测试产物，不进入静态发布包。Django system check无问题；本轮无schema变化、迁移业务库、真实外部发送或付款。

静态分享版本15已succeeded：https://sage-billing-preview.lillian-lige.chatgpt.site 。deployment=appgdep_6ac0f940a73c81918bc970ae5dbc3d50；version=appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_dec118f3984c8191bfd7e093c5404e92；独立Sites源1369f4768f9ea9ea389fd79b5f9f6094320bb04f。托管dist与本地release逐文件一致，billing.js SHA256=65bf105d223edc532ff61f7df2db6f080ca96858febb315bc008b74f4fa5786f。该源码不在GitHub提交；静态包不含后端或测试数据。

后续仍按PRD D01–D16集中确认；正式参数/条款、云环境/邮件短信、真实任务接口、MFA/内部财务权限、支付平台订阅准入和商户配置缺失不自行补值。退款申请/年度退款/升档/签约/开票/对账/任务不足入口不能因内部账本或静态预览存在而宣告完成。

## 客户购买报价接口 · 2026-10-03 · P0-2

复用既有ProductVersion/Quote/Order，无新schema或依赖。POST `/api/billing/purchase-quotes` 使用当前会话账户、财务角色、CSRF和X-Billing-Account，严格接收product_id/seat_quantity/channel/buyer_region；不接收客户端金额或期限。销售、已发布完整商品、渠道CNY/地区/周期能力全部通过才持久化。BILLING_QUOTE_TTL_SECONDS默认None，D03未确认前拒绝；测试300秒仅隔离夹具，不是正式政策。

GET `/api/billing/purchase-quotes/{id}` 输出当前账户快照、到期、可建单状态及关联订单，排除merchant_entity；其他账户404，查询参数和写方法拒绝。过期、已建单、商品/渠道关闭不可新建单；读取不重新报价或扣款。既有POST orders继续验证协议版本及幂等键；相同请求返回原单，同键不同参数及重复报价建单返回409。不建议换键解决未知状态。

新增HTTP覆盖正确报价→读取→协议版本拒绝→单次建单→重放→冲突→恢复原单；验证跨账户/CSRF/账户切换/成员角色/客户端金额/缺失及异常TTL/未批准商品/非法席位/过期/销售关闭。正式商品目录、协议文档、收银台提交、签约/支付/实际交付仍待接，不是客户购买全链路验收。无生产商品播种、真实支付或GitHub推送；前端未变，分享版本11不变。

验证：先以503/缺少报价编号失败，再接接口；账户26项本地集成通过。全量隔离PostgreSQL175项通过（47.857秒，含既有两条本地HTTP浏览器链路），迁移往返及无漂移通过；Django check及diff检查通过。新报价链路由Django HTTP集成测试覆盖，未声称新增浏览器购买测试或真实渠道测试。

## 部署基础 · 2026-10-03 · P0-1

新增 `GET/HEAD /health/live`（不访问数据库）及 `/health/ready`（只读检查数据库连接及全部迁移是否已应用）。其他方法405；失败503，只返回通用状态，日志仅记录错误类型与request_id。两个端点禁止缓存，不返回商户、数据库、用户或配置详情。ready不等于邮件、任务、支付渠道已可用，不触发自动迁移或业务写入。

验证：新增9项健康/部署检查测试；健康路由先404失败，实现后通过。新增测试首次插入位置错误导致NameError，已修正测试分组并保留全部断言。隔离PostgreSQL全量170项通过（44.632秒，含两条本地HTTP浏览器路径），迁移往返及makemigrations无漂移通过；git diff --check通过。SAGE_ENV=test下check --deploy正确非零退出，拒绝本地/SQLite并提示邮箱及收费关闭；该结果不是生产配置通过。未真实发信、收款、改业务数据库、发布后端或推送GitHub。前端未变，公开静态预览仍版本11。

`manage.py check --deploy` 增加 sage.E001–E004：拒绝本地环境、非PostgreSQL、通配或URL形式Host、已开启但配置不完整的SMTP。邮箱未开启与真实收费关闭分别提示sage.W001/W002；不得静默忽略商业上线门禁。配置通过不证明实际邮件送达。当前无schema、依赖或前端变化。

### 联调环境部署顺序（尚未执行公网部署）

1. 确定D01域名、运行平台、数据库与备份负责人。静态 `dist/` 与 `/api/` 必须由同一个HTTPS站点提供；公开静态预览不提供后端。生产 `public_file` 不负责静态文件服务，不能仅部署Django后声称官网已打通。
2. 安装已锁定的 `backend/requirements.txt`；平台安全配置注入 SAGE_ENV=production、SAGE_SECRET_KEY（至少50字符且随机）、SAGE_ALLOWED_HOSTS（准确域名，不带协议/端口）、SAGE_DB_NAME/USER/PASSWORD/HOST/PORT。不得把值写入源码、日志或聊天。数据库仅向应用私网开放，DB TLS/证书与备份策略按选定平台落地；尚未选定，不能假定已经开启。
3. 先执行 `.venv/bin/python manage.py check --deploy`，审核所有Django及Sage错误/警告。HSTS子域与preload不默认开启，须核实域名范围；当前内置安全响应头仍有Django标准中间件检查提示，应在实际部署验收时核实，不能直接全局屏蔽检查。
4. 在隔离联调库执行 `migrate --plan`，确认影响、备份、恢复演练与维护窗口后再执行 `migrate --noinput`。已有客户库未经授权禁止迁移；禁止以health请求自动迁移。失败时保持未就绪，不放流量；跨版本回滚必须先验证旧代码/schema兼容性，不能直接执行反向迁移删除数据。
5. 用已安装Gunicorn启动 `backend.wsgi:application`，监听私网或loopback而非公网；生产不使用runserver。具体worker/timeout由负载测试确定。HTTPS入口转发 `/api/` 和 `/health/`，静态入口服务 `dist/`。需逐项验证应用识别HTTPS、secure cookie、Host及CSRF，不为修复重定向循环关闭SSL检查或信任任意客户端转发头。
6. 反向代理必须覆盖而非透传客户端伪造的来源/协议头；当前代码OTP限流读取REMOTE_ADDR，代理环境须补经验证的可信来源方案，否则所有用户可能共用代理IP限额。平台及代理未选择前不硬编码可信代理，更不直接开启任意X-Forwarded-For信任。
7. 仅在邮件服务和发送域验证后配置SAGE_AUTH_EMAIL_ENABLED=1、SAGE_EMAIL_HOST/PORT/USER/PASSWORD/FROM。当前支持SMTP STARTTLS；需要SSL直连的服务不可直接套用。验证码不记录/展示，短信仍未接通。真实发送另行授权，验收送达、失败、限流与重复验证。
8. 探针使用正确Host和HTTPS访问：live用于进程故障；ready用于DB/schema流量门禁。数据库暂不可用仅撤流量，不因此循环重启健康进程。建议探针间隔不少于30秒，避免频繁迁移图查询；禁止缓存。监控按request_id追踪，日志/告警不包含身份、验证码或密钥。
9. 实测中英官网→登录→同源会话→只读账户页面、退出、跨账户拒绝、静态资源与重启持久性。关闭调试与所有测试数据入口；真实收费继续关闭。部署完成后还须备份恢复、并发、故障和授权渠道联调，才可进入商业上线验收。

依据：[Django 5.2 deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)。上述为待执行操作步骤，不是已经部署或生产验收的记录。

## 最新增量 · 2026-10-03 · 降档与年转月指引

断点恢复完成：32组UI复验通过／0页面错误；release、JS语法及diff检查通过。第一次上传Connection reset by peer，同站点重试成功。版本11部署appgdep_6ac06559025081919095043278e6eb23为succeeded，源34453d7c14c15f01bdac7a788ea144979f7da186；https://sage-billing-preview.lillian-lige.chatgpt.site 。仅静态预览，未部署客户API或支付，未提交GitHub。后端161项为上一阶段证据，本轮未重复后端全量测试。

个人Pro／Pro+／Ultra订阅卡片按当前档位和年付状态显示可降档目标及年转月说明，引用prepaid_through优先的已付款截止日，不把当前周期末误当全部服务结束。区分开启／取消中／已关闭／结果未知；取消能力关闭时提示核实，不指向不存在的按钮。只读指引不预约降档、不计价、不退差价、不新建订单；企业转换和升档D03/D04仍未开放。

首轮32组UI夹具通过／0错误；另补取消能力关闭分支后复跑。无后端规则或schema变更，未调用真实支付。覆盖审查继续检查已确认可开发项与第二轮门禁，不能因指引完成宣告套餐变更引擎完成。

断点恢复核查：线上仅版本10，上次Sites准备进程已不存在、无新版本，故重新打开同一站点而非新建。本地8914服务器已停止导致ERR_CONNECTION_REFUSED，重新绑定127.0.0.1后复验；不是功能断言失败。剩余审查重点：真实报价/订单公共入口、敏感操作MFA、任务报告阅读版、退款申请/财务审核、发票/核销和实际部署；未定项按PRD D01–D16，不虚构可用渠道。上次并行覆盖审查因代理额度限制没有产出，不能算已审查完成。

## 最新增量 · 2026-10-03 · 本人成员限额

公开静态预览版本10已succeeded，部署appgdep_6abffec4e3a48191bd9d9313864463d0；地址https://sage-billing-preview.lillian-lige.chatgpt.site 。仅静态文件，未部署后端／真实登录／支付，未推送GitHub。

GET /api/billing/my-usage 使用当前session账户及登录用户，普通有效企业成员可读；拒绝查询参数、错误账户header和写方法。复用organization_summary的服务月、席位和共享额度计算，viewer分支在返回前只输出本人，不输出成员列表／人数／企业资金。无生效期间时用量null，不替D08决定重置规则。

中英账户页增加“我的使用限制”入口、本人席位／共享上限／已用／预占／周期及只读刷新，按账户版本和用户编号校验响应。修改限额仍未开放；其他财务路由仍403。无schema、依赖或真实外部调用。

测试先403失败后实现。完整隔离PG回归最初发现上轮六类余额遗漏了account-live旧三值断言，两条真实HTTP浏览器路径因此失败；更新为六个精确零值，未删断言／跳过测试，最终161项全部通过（34.673秒），迁移往返／无漂移通过。后续只补viewer非零offset拒绝及docstring，两个相关接口测试复跑通过。UI夹具31组、0页面错误、16个既有写请求，新增本人读取不增加写请求。JS／release／diff通过。经验：公共页面字段扩充须同步审查真实HTTP浏览器断言，不能只跑响应夹具。

## 最新增量 · 2026-10-03 · 六类 Credits 余额展示

公开静态预览已更新为版本9，部署appgdep_6abffd8b539081918e6f9b342a55c782 succeeded；https://sage-billing-preview.lillian-lige.chatgpt.site 。仅静态文件，无线上账户API、真实扣款或GitHub推送。

账户Credits页复用既有credits_summary，完整显示可用、任务预占、退款冻结、已消耗、已过期、已退款核销的总计及逐批次数量。保持精确字符串，不转Number或推算金额；明确额度退款核销不等于资金到账。六类任一字段缺失或格式不合法则显示读取失败，不能补零。无后端/schema/收费规则变更。

验证：release构建、JS语法、diff通过；UI夹具30组／0页面错误、16个既有夹具写请求，新增显示路径没有写请求；中英320px无横向溢出。首次测试误将自动充值预算的独立metrics同时计入，限定余额区后保留六类精确值断言，复跑通过。共享样式class不能充当唯一业务区域选择器。此前160项后端回归为上阶段证据，本轮仅前端变更未重复跑后端。下一项为普通企业成员本人限额只读接口。

## 最新增量 · 2026-10-03 · 免费体验本人记录恢复

`GET /api/trial` 复用 read_trial，只从当前登录用户读取资格、原任务及已存报告，不接受指定用户或任务查询参数，不要求财务角色。POST/PUT/PATCH/DELETE均拒绝；无新schema、任务执行或外部调用。体验页中英双语增加登录、刷新、原任务状态及已存JSON报告下载，所有外部内容以textContent展示；异常显示资格未知，不当作available。页面仍不接收新分析或客户端完成回调。完整报告的业务阅读版排版尚未实现，当前仅原始报告下载，不能称完整报告体验已完成。

验证：HTTP测试先403失败，接通后通过；隔离PostgreSQL **160 tests / OK，33.270秒**，迁移往返及无漂移通过；浏览器夹具29组／0页面错误，保留16个既有夹具写请求，新增体验路径全GET，验证登录、跨用户、重复读取、禁止写入、异常恢复、HTML不执行及JSON下载内容一致。无真实分析、邮箱发送、付款或业务数据迁移；release及diff检查通过。

覆盖审查同步纠正旧矩阵中“未实现”但已有内部实现的项目。接下来安全可开发项：Credits六类余额完整展示、普通企业成员本人限额只读投影、已确认降档／年转月指引；它们不依赖真实商户或待定定价。D01–D16仍按原门禁执行。

发布：同一公开静态预览版本8已succeeded，https://sage-billing-preview.lillian-lige.chatgpt.site 。部署编号appgdep_6abffc632d6c819185bec15bd7f60d2c；隔离Sites源提交01ca8c5a426dbabf2b85c2aa288ae52dac5ac260。115个静态文件，不含后端、测试报告或用户数据；尚无线上账户API，因此公开预览显示无法读取而不是模拟记录。最终UI夹具复跑29组／0错误，320px中英无溢出，中文截图已视觉检查。未提交或推送GitHub。

## 最新增量 · 2026-10-03 · 内部异常队列持久化

新增BillingIssue／0019，将检测器产生的账户、稳定错误码、对象类型／编号唯一关联，保留首次与最近发现时间。record_billing_findings复用账户锁，重复／并发扫描不重复建单；时间不倒退，未再检测到异常不自动结案。没有前端传入异常正文、客户联系资料或任意修改账务入口。当前只有open状态，财务指派／人工结案及处理审计仍待实现，D12权限未确认不能开放操作。

check_billing_integrity命令默认仍只读，显式 `--record` 才保存检测结果；发现异常仍以BILLING_REVIEW_REQUIRED非零退出，不因记录已保存就报告账务正常。不迁移业务库，不改付款、退款或余额。

验证：新增入口先失败再实现；首次PostgreSQL回归的损坏夹具超过kind字段12字符，数据库先拒绝。改用长度合法的invalid值触发真实检测，不放宽字段或删断言；最终 **159 tests / OK，28.716秒**，0019迁移往返／无漂移及diff检查通过。包含并发仅一条记录、重复时间、命令显式落库、问题消失不自动关单与跨账户隔离。测试坏数据须遵守非目标字段约束，否则未覆盖目标路径。带数据回退须保全异常记录；本轮无UI／静态发布变化，版本7保持，无GitHub提交。下一阶段需审计剩余已确认开发项与D01–D16门禁，不能把队列表视为完整运营后台。

## 最新增量 · 2026-10-03 · 内部账务一致性只读检查

核对PRD后确认D12内部财务角色／增强验证未定，不开放人工退款审核按钮，企业billing_admin不等同Sage财务。继续实现W11独立安全检查：check_billing_integrity按指定账户锁读取订单、付款事实、额度及退款记录，输出待交付、异常收款／待核对意图、超实付退款保留、账本不守恒和退款冻结／核销流水不一致。报告只含稳定代码与对象编号，无联系方式／证据，不写数据、不调用渠道、不自动修账。不是渠道账单导入、结算手续费对账或已指派的异常工单。

命令：在已配置且获授权的内部环境运行 `.venv/bin/python manage.py check_billing_integrity --account <UUID>`。必须显式指定账户；正常输出JSON，发现待处理记录则同时以BILLING_REVIEW_REQUIRED失败退出；账户不存在返回ACCOUNT_NOT_FOUND。未设置自动调度或访问真实业务库。

验证：缺少函数先失败，最终隔离PostgreSQL **157 tests / OK，27.467秒**，迁移往返／无漂移及diff检查通过。夹具人为损坏退款状态和交付状态后可识别，命令错误退出、账户隔离及无余额修复有断言。没有新schema／UI／依赖，公开静态版本7不变，未提交GitHub。待续：异常队列／指派／结案、内部财务权限与MFA、开票和合同核销；不得把本只读检查称作完整W11验收。

## 最新增量 · 2026-10-03 · 客户订单退款处理记录

订单详情API新增refunds投影，只含客户可见编号、金额整数字符串、状态、创建和终态确认时间，不返回审核／渠道证据hash。中英文订单详情增加记录与只读刷新，区分金额已保留（不等于到账）、可信结果已确认、确认未退后终止（原额度有效期不延长）。沿用现有财务成员及账户隔离，写入口继续关闭。没有新增迁移或依赖。

验证：新增投影断言先以KeyError失败后实现，最终隔离PostgreSQL **156 tests / OK，35.463秒**；0018往返及无漂移通过。UI夹具 **28组／0错误**，320px中英文截图检查及三态刷新通过，仍为16个既有夹具写请求。发布不含截图／测试订单。release构建、JS语法及diff检查通过；申请审批、渠道退款意图／验签、年度退款仍未完成。

静态分享版本7发布succeeded：https://sage-billing-preview.lillian-lige.chatgpt.site 。version_id=appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_796b7659601c8191a83e72864ae99241；deployment_id=appgdep_6abff86c5c288191b5d9fa9120ffec3a；独立Sites源码0aa51487b73e3b00e5a4c91d692b40ad3a884682。首次打包因凭证过期退出，续领后同站点成功；没有重复建站或GitHub推送。公网无账户后端，不能把本地夹具视为公开退款可用。

## 最新增量 · 2026-10-03 · 退款资金保留与额度原子核销

新增CreditRefund及0018迁移，不可变绑定原PaymentFact、CreditFreeze、原单计算金额、请求键和内部审核证据引用。reserve_credit_refund仅供已完成审核的可信内部调用，重新读取原单全未用余额；只接受精确整分结果，D03未定舍入继续拒绝。不接收客户端退款金额。账户锁内累计成功及在途金额不超过实付，与额度冻结同事务，重复请求返回原状态；退款解除后旧请求重放不重新冻结。

resolve_credit_refund仅接受可信已退款或已确认未退且终止的结果，金额记录和额度核销／释放同事务。失败、未知不解冻；证据冲突拒绝，写失败整体回滚。通用额度解冻入口先验证账户归属，再拒绝绕过资金记录。内部证据hash并不执行财务审批或渠道验签，不将此台账当完整退款处理系统；尚无公众写入口、退款渠道调用、年度退款或申请审批界面。

验证：缺少函数的失败测试先复现，再实现；最终隔离PostgreSQL **156 tests / OK，28.076秒**，0018迁移往返／无漂移及diff检查通过。覆盖两个并发申请仅一方保留、重放、全部未用、释放后重算、非整分拒绝、事务失败回滚、跨账户和绕过解冻拒绝。仅隔离库迁移，有业务数据时回退前须导出资金／审核证据；未迁移业务库、真实退款或推送GitHub。前端无变化，静态分享版本6保持；W10审核工作流／退款意图及真实渠道仍待实现。

## 最新增量 · 2026-10-03 · 订单退款范围只读页面

订单详情接入退款评估GET，复用白底银灰信息面板及中英文文案。可评估订单展示原实付、全部未用付费额度和参考金额，明确不是批准退款；非整分显示待确认，不前端舍入。过期／预占／异常等原因分别说明；失败为未知，不误报不可退，可只读重试。请求序号、账户和DOM检查阻止旧响应回填；不存在退款提交、金额输入或资金写入。

验证：release构建、JS语法与diff检查通过；UI响应夹具 **27组通过／0页面错误**，包括320px中英文截图、非整分、失败重试、错误账户和离开页面后迟到响应。既有16个夹具写请求数量不变。后端未改动，153项PostgreSQL是上一后端增量的验证，不宣称本轮重跑。测试截图与夹具未发布。

Sites静态版本6已成功发布：https://sage-billing-preview.lillian-lige.chatgpt.site 。version_id=appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_3a6e421884348191aff81c51ab155dcd，deployment_id=appgdep_6abff61608f48191bbcaa9bf236e6697，独立Sites源码ed28974f59933535dc828621b2e711458f68923a。未提交GitHub；静态预览不含真实账户、API服务或退款能力。W10审批／资金保留／执行及W11财务仍需继续开发。

## 最新增量 · 2026-10-03 · 原订单付费 Credits 退款评估

新增 credit_refund_assessment 与 GET /api/billing/orders/{id}/refund-assessment。沿用财务成员权限、当前账户门禁和账户行锁，从唯一正常收款事实与原订单付费批次计算全部未用数量；不接受客户端金额或数量。过期、预占任务、冻结／历史核销、重复或待核对收款均停止评估。精确比例使用 Fraction；不足整分时返回未舍入分数及 rounding_policy_pending，不自行决定舍入规则。所有结果 can_submit=false，整分参考值也不是已批准报价。

尚未实现退款申请、资金保留、审批、渠道退款、发票／合同审核及退款页面，因此W10不整体勾选。未更改schema或前端，公开静态版本5保持不变。没有真实退款、邮件或GitHub提交。

验证：隔离PostgreSQL **153 tests / OK，26.862秒**，迁移往返、无漂移和diff检查通过，含既有两条本地HTTP浏览器链路。新增回归覆盖原单比例、跨账户、重复收款、任务预占、过期、冻结、用尽及GET-only。HTTP测试首次因force_login未初始化当前账户会话而403；通过既有auth/session建立上下文修正测试夹具，没有放宽生产权限。今后强制登录后的账务测试须显式建立账户上下文。

## 最新增量 · 2026-10-03 · 企业充值与预算只读页面

GET /api/billing/automatic-topup仅企业财务角色可读，复用会话／当前账户门禁，POST拒绝。返回最近授权的非敏感配置与当前已记录预算窗口，协议和证据不外露；缺窗口返回null而非0，待核对交易令余量为null。金额用整数字符串输出，前端使用BigInt分位格式化，避免超过安全整数时舍入丢分。

企业Sage Credits页新增只读授权／预算卡，个人不加载此企业接口；中英文显示固定包、阈值、上限、窗口及已用／在途／余量，明确自动收费未启用。错误重试、刷新保持只读，旧账户／已断开DOM响应不回填。手机截图发现标签金额并排挤压导致7.31断行，改为顶部标签和下方数值，复测并检查截图；沿用白底银灰。

验证：隔离PostgreSQL **150 tests / OK，34.320秒**，迁移往返／无漂移通过；UI夹具26组、0页面错误，包含320／1440中英文、缺预算非零、待查余量未知、错误重试及9007199254740993分精确展示。release构建／语法／diff检查通过，无新schema。测试夹具没有发布。

Sites静态版本5发布succeeded：https://sage-billing-preview.lillian-lige.chatgpt.site 。version_id=appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_49f843cddb3c8191980bc4f37d26581b，deployment_id=appgdep_6abff305b7808191ae05b177c6ceff8a，隔离源码e2be1af71eb58444d00ec42c1e8868b2f093814e。仅115文件静态包，不含Django后台、账户或密钥，不等同公开登录／收款已上线；未推送GitHub。敏感管理写入、D09策略与真实渠道仍待完成。

## 最新增量 · 2026-10-03 · 充值预算与付款意图绑定

新增TopupBudget及0017迁移，将账户／授权／显式预算窗口／政策版本／触发键不可变绑定到唯一PaymentAttempt。可信内部prepare_topup_attempt在同一账户锁和事务下校验有效授权、已付期间、无退款冻结、固定商品／渠道，建立报价、订单、付款意图和预算预留；失败整笔回滚。浏览器订单与通用付款意图入口拒绝topup命名空间。claim_payment_dispatch对预算绑定意图明确TOPUP_CHARGING_DISABLED；未获D09/D15批准前不允许借内部建单发起充值。

预算由原付款事实计算，不维护可漂移的第二套收支金额：确认成功计已用，明确失败／取消／到期不占预留，其他状态保持预留；异常收款或冲突列review并阻止新意图，不把待核对金额当已完成对账。所有未明在途（含旧窗口）串行阻断新意图；同窗口累计跨授权版本保留，改重叠窗口／政策不能重置上限。窗口与触发来自可信调用方，不包含阈值判断、自然月／服务月选择、连触发或自动重试策略；这些仍待D09确认。移除工具影响查询纳入该预算所绑定的交易。

验证：缺少预算入口先失败，随后定向及隔离PostgreSQL **149 tests / OK，26.917秒**；0017迁移往返／无漂移及diff检查通过。含两并发触发仅一笔订单、重复键、未知保留、失败释放、成功累计、换授权不重置、上限拒绝和重叠窗口拒绝。未知派发状态由明确隔离夹具模拟，不是真实PSP调用。无业务库迁移、真实收费、GitHub提交或页面变动；分享版本4保持。下一步：预算及充值授权只读投影／UI、敏感操作门禁与真实渠道恢复；W08仍未完整验收。有数据回退前必须导出预算绑定与付款证据，不直接反向删表。

## 最新增量 · 2026-10-03 · 充值到期暂停与独立撤销

close_expired_renewals在无有效已付期间时暂停充值授权，不等待三天续费补缴窗口；现有grant_subscription_credits命令按有历史期间的账户遍历，已取消续费的企业也覆盖。重复调度不重复审计，原授权记录请求重放不重新开启。退款冻结期间拒绝新的充值授权，防止以重新配置绕过暂停；冻结解除没有恢复副作用。

TopupAuthorization新增0016撤销时间／证据及revoking／revoked状态。内部财务角色请求立即停止本地使用，可信渠道确认只处理对应充值协议；未知／失败不调用确认，不能自动推断成功。支付方式移除请求关联全部未撤销充值授权；全部确认后才解除这一项阻塞，仍需通过订阅撤销与待查交易校验。充值授权撤销不误取消未绑定该工具的订阅。已确认撤销不可save覆盖，重放证据冲突拒绝；新配置不覆盖旧记录的revoking／revoked状态。

验证：到期状态测试先失败、缺失撤销入口先报错，退款中重新配置测试先失败，再完成修复。隔离PostgreSQL **147 tests / OK，30.716秒**，0016迁移往返／无漂移及diff检查通过；新增并发授权版本仅一方生效。无真实撤销请求、渠道验证、自动充值调度或预算台账，不将内部状态机宣称为真实渠道闭环。仅隔离库迁移，带数据回退需保全撤销证据。前端／分享版本4未变，无GitHub提交。下一步实现受显式预算窗口约束的在途预算记录及结算，D09日历和连续触发策略不默认取值。

## 最新增量 · 2026-10-03 · 企业独立充值授权记录

新增TopupAuthorization与0015迁移，仅记录可信渠道确认后的独立授权事实、操作者、版本、请求键及阈值／固定商品版本／金额／月上限／条款和同意证据快照。只允许企业财务角色、有效付费期间、已发布企业Credits商品、有效支付方式和明确automatic_topup渠道能力；不能根据one_time能力推定代扣。订阅现授权与历史授权均禁止复用为充值授权。相同请求重放不建第二记录，参数变更冲突，版本不符拒绝；重新配置必须提供新的独立确认。

记录返回charging_enabled=false。authorized表示已记录授权，不代表开启收费；D09预算周期、阈值口径、连续触发及D15渠道规则没有默认取值，暂无触发调度或扣款执行。superseded仅表示配置被替代，绝不表示旧渠道授权已撤销。已记录充值授权纳入支付方式移除影响，确认撤销功能尚未接入前不允许完成移除，不能因订阅撤销完成就误报全部移除。现有公开列表仍说明充值状态尚未接入，管理写入口继续关闭。

企业购买额度进入退款冻结事务时暂停当前充值授权，解除冻结不自动恢复；暂停与冻结共用账户锁及事务，重复暂停不重复审计。到期暂停调度、手动重新开启、旧授权撤销／部分更换及预算预留仍待实现，不能声明完整R21/R22完成。

验证：隔离PostgreSQL **144 tests / OK，24.429秒**，0015迁移往返及无漂移通过。新增测试覆盖独立授权拒绝复用、未获渠道能力拒绝、记录不建订单、重放／版本冲突、退款暂停与不自动恢复；未测试真实渠道，也未新增并发充值预算能力。仅隔离库迁移，历史无回填；有业务授权数据时反向撤表必须先保全审计，不以空库回退替代备份。无前端改动，分享版本4保持不变，未推送GitHub。

## 最新增量 · 2026-10-03 · 旧授权撤销只读展示

payment_methods_summary新增retired_authorizations脱敏投影，仅含内部记录ID、订阅ID和撤销状态，不返回协议引用或证据。中英文账单页在支付方式卡片下区分“更换前的授权”，说明新方式生效不代表旧授权已撤销；缺失或非法状态拒绝当作空记录展示，刷新／分页仍只读。

验证：投影测试先复现KeyError再补实现；隔离PostgreSQL143项通过、26.767秒，迁移往返及无漂移通过；UI夹具25组、0页面错误，新增中英文320／1440旧授权文案断言，手机英文截图已检查；release构建与diff检查通过。测试数据不发布。

通过Sites正常打包发布静态版本4并返回succeeded：https://sage-billing-preview.lillian-lige.chatgpt.site 。version_id=appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_9ebf991868408191b4ea372f3354e0e9，deployment_id=appgdep_6abfee0829ec8191ada220e8d36984d7，隔离Sites源码59cb0f26971ed5d132f477fd7aa88141078f22ae。115文件静态包不含后台／账户／密钥／夹具，不等同公开登录和支付上线，未推送GitHub。跨渠道订单衔接、provider调度、自动充值授权及管理写操作仍待完成。

## 最新增量 · 2026-10-03 · 同渠道授权版本切换

新增AuthorizationChange与0014迁移，记录旧／新授权、支付方式、操作人、请求键和旧授权撤销证据；Subscription新增authorization_version。可信内部入口在账户锁下校验财务角色、有效已付期间、版本及新授权／工具后，历史和绑定同事务切换。模型直接save仍拒绝修改绑定，仅该显式转换使用条件update。重复请求不重复切换；原激活重放以首条更换记录中的原始授权核对，不能重置当前方式。新发起快照携带授权版本，已发起不重新扣款或覆盖。切换不建订单、不发额度、不改变服务周期。

旧授权撤销独立确认，只标旧记录，不取消新订阅。移除影响清单包含旧授权，未撤销不完成移除。未验证／失败签约不能调用该可信函数，哈希不是渠道校验本身。

范围：仅同渠道、同商户、Sage发起续费；跨渠道涉及不可变订单快照，provider自动调度涉及旧计划停扣，均不能靠换字段宣称完成，仍待开发。没有公开更换写API、MFA或真实签约。只读页面尚需增加旧授权撤销展示；现有公开更换按钮仍关闭。自动充值独立授权未实现。历史授权不回填；0014仅隔离测试库迁移，带数据回退必须保全审计，不直接反向删表。

验证：定向27项通过；隔离PostgreSQL **143 tests / OK，23.784秒**，迁移往返／无漂移通过，含两个并发更换仅一个版本成功、审计失败回滚、旧在途保持、原激活幂等、旧方式移除不误取消新授权。无真实收费／业务库迁移／GitHub提交，前端及静态分享版本3未变。下一步：旧授权只读展示、跨渠道订单绑定与调度转换，然后独立自动充值授权；完整W09仍未完成。

## 最新增量 · 2026-10-03 · 重复收款不能被成功状态掩盖

授权切换前置审查复现：同一续费已有accepted付款后收到duplicate_payment_review，订单有效状态仍为succeeded，原支付方式影响清单错误返回空。移除检查现直接检查原始非accepted收款事实，将关联尝试列为review并阻止完成移除，不依赖是否收到另一个状态事件。有效付款仍正常交付，重复收款不重发期间。不能通过取消授权把待核对资金当作已解决；后续必须实现带证据的对账处置，不能直接清除事实。

验证：新增测试先失败（预期review、实际空列表）再通过；隔离PostgreSQL **140 tests / OK，22.912秒**，迁移往返及无漂移通过，包含本地HTTP浏览器链路。无新增schema、外部支付或GitHub提交。静态前端未改，分享版本3不变。同周期授权更换与旧授权撤销记录仍待实现；本次仅完成其前置安全修复，不计为完整W09。

## 最新增量 · 2026-10-03 · W09续费发起授权快照

PaymentAttempt新增dispatch_authorization及0013迁移。续费首次发起在同一账户锁／事务下保存订阅、支付方式ID及授权快照，与dispatched_at一起提交；未到发起时间、不符合门禁或已取消不产生快照。已发起的时间和快照不可通过模型save覆盖；公开订单投影不包含授权引用。支付方式移除影响查询同时查快照，因此以后订阅绑定变化也不会遗漏旧在途；本轮测试通过显式夹具模拟后来绑定变化，不能据此宣称更换功能已实现。

activate_recovery_subscription增加显式payment_method_id传递；新授权激活仍依赖旧授权撤销确认、已付补缴及有效新工具，重复激活不重发额度。没有默认沿用旧工具，也不自动签约。未绑定的历史续费不补写当前授权；0013旧数据为null，未来渠道查单须核验历史证据，不可凭空补全。

验证：django-tdd先复现缺失字段后实现；隔离PostgreSQL **139 tests / OK，0跳过，22.857秒**，0013迁移正反往返、无漂移及diff检查通过，含本地HTTP浏览器链路。无真实渠道调用或业务库迁移；若带数据回退需先导出快照保留审计。无前端变化，静态预览版本3不变，未提交GitHub。后续仍需同周期更换授权的版本／旧授权撤销记录及自动充值独立授权；R23整体未完成。

## 最新增量 · 2026-10-03 · W09支付方式只读页

静态分享版本3已发布成功：https://sage-billing-preview.lillian-lige.chatgpt.site 。version_id=appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_927000144e80819198a3e29c21b3adeb，deployment_id=appgdep_6abfe98f04fc8191a43e648ea693f069；隔离Sites源码bcc7a976d2b1749e61e4084f379a059fb4b7c4c0。该发布不包含Django后台，不表示真实登录或支付上线，未推送GitHub。

GET /api/billing/payment-methods复用财务角色／当前账户门禁，按50条分页，返回支付方式状态、已绑定订阅的撤销状态、待查交易及移除时间；不返回渠道指纹、商户引用、证据或确认摘要。未绑定且未确认撤销的历史授权单独标记列表不完整；自动充值仍标未接入，不能把空数据解释成无风险。接口POST拒绝。

中英文账单支付方式页接真实只读API，已记录不表示允许扣款；处理中说明保留已付服务，未知交易提示只查原单。分页、刷新、错误重试不发写请求，错误不显示为空，跨账户及过期请求不回填。沿用银灰白底与既有彩色支付图标；本地320／1440截图检查及复测。无新增schema或依赖。

验证：测试先复现接口503后实现；隔离PostgreSQL **138 tests / OK，0跳过，23.636秒**，迁移往返／无漂移通过；账户UI夹具 **25组、0页面错误**，新增中英文移动桌面、分页、失败重试及错账户响应拒绝；构建和diff检查通过。页面样例来自仅本地fixture，不发布测试数据。真实身份投递／渠道／管理写操作仍未开放；替换、自动充值、补缴新工具绑定等继续按未完成项推进。

## 最新增量 · 2026-10-03 · W09订阅支付方式移除内部状态

PaymentMethod及0012迁移保存账户／渠道／商户范围内不可扣款的指纹、验证证据摘要与active→removing→removed状态；订阅在可信激活时可显式绑定。指纹不是卡号、token或验证本身，真实渠道验证器仍未接。已注册渠道的激活必须指定绑定，不能以省略参数绕过已移除记录。历史未绑定订阅不猜测归属，不进行自动回填；再次绑定已移除工具、替换方式及补缴新方式传递仍需后续实现。

payment_method_impact返回本账户关联订阅与未明在途交易、确认摘要；不暴露渠道指纹和授权引用。request_payment_method_removal复用账户锁、财务角色和取消续费函数，摘要变化要求重新确认；审计与状态一起提交，重复提交不重复审计。未发起交易在后续dispatch门禁被取消，已发起交易不能重新发起。finish_payment_method_removal只供可信渠道处理方确认工具失效后调用；全部关联授权已撤销、没有待查在途才标移除；失败／未知不调用完成入口，不删除历史与额度，已确认在途成功仍正常交付。

边界：本切片仅已绑定订阅；自动充值及一次性支付工具关联、更换及版本切换、真实撤销调用、网页管理和D12高风险验证未实现。automatic_topup_supported=false不等同“自动充值列表为空且已确认”。不能宣称R23/R24整体完成或向浏览器开放这些内部函数。

验证：django-tdd先验证缺失入口报错，再实现并回归；隔离PostgreSQL **137 tests / OK，0跳过，23.205秒**，0012迁移往返／无漂移通过。覆盖移除与调度竞态、确认清单失效、未知交易不完成、在途成功仍交付、跨账户／角色拒绝、重复与证据冲突、审计失败回滚、已移除绑定拒绝。沿用[Django事务文档](https://docs.djangoproject.com/en/5.2/topics/db/transactions/)及现有账户锁，不在事务内调用外部支付。仅隔离库迁移；带数据回退须先导出绑定／审计，不能以空库往返代替数据保全。无页面变更、真实支付、邮件或GitHub提交；公开静态版本2保持不变。

## 最新增量 · 2026-10-03 · 企业商品准入前移

检查发现_paid_order已验证企业resource_mode，但_sale_gate未验证，可能让不可交付配置进入报价。新增回归复现缺失／未知模式及shared_only带非零内含额度均未被拦截；随后在既有_sale_gate复用数量解析提前拒绝，报价、建单及调用该门禁的支付路径一致，不新增抽象或配置。交付侧校验保留，旧测试改为显式构造损坏历史快照，仍验证不能交付；未删除断言。测试夹具应新建商品版本而不是修改已发布不可变商品，本轮首次测试因此报错后已改正，不削弱不可变规则。

验证：隔离PostgreSQL **133 tests / OK，0跳过，25.279秒**，迁移往返及无漂移通过，含两条本地HTTP浏览器链路；git diff --check通过。前置SQLite定向51项／5项跳过仅作快速验证，不替代PostgreSQL证据。本轮无schema或页面改动、无业务库访问、真实收费仍关、无GitHub提交；静态预览版本2无需重发。

管理写入的具体增强验证仍为D12，不能擅自挑选MFA方式开放敏感操作。下一可独立推进范围为W09已确认的授权停止／移除内部状态与影响清单；公开写入口继续受D12约束。仍不得据此标完整支付产品完成。

## 最新增量 · 2026-10-03 · W07组织只读页面

公开静态预览已更新并返回succeeded：https://sage-billing-preview.lillian-lige.chatgpt.site 。Sites版本2：appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_89770dc4b7308191aef23450be9356a8；部署appgdep_6abfe545efec8191ae42c2eb7157e6c8。仅隔离Sites源码推送，未提交或推送GitHub；后台API不在此静态发布中。源码快照8344cdaf0c341970d9bb1c5d4ab3c04c100b26f7对应发布归档，非主项目Git提交。

新增 GET /api/billing/organization，复用当前账户、财务角色校验和账户锁。返回付费／已分配席位、分页成员、脱敏标识、共享上限及当期已用／预占；无有效期间用量为null而非0。仅owner／billing_admin可读，跨账户409、普通成员403，POST不开放。每页50条，不返回原始登录标识。中英文账单企业管理接真实读接口，支持分页、失败重试与额度批次跳转，旧账户迟到响应不能回填。

验证：隔离PostgreSQL **132 tests / OK，22.229秒，0跳过**，迁移往返和无漂移通过，含两条本地真实HTTP浏览器链路。账户UI夹具 **24组、0页面错误**，本次覆盖320／1440中英文企业成员分页；截图检查后修正分页按钮原生样式并复跑通过。release构建通过。没有真实收费、真实投递或GitHub提交；公共预览仅静态页面，不等同后端公网联通。高风险管理写入、邀请、增减席费用和D08／D11未完成，W07不能整包勾选。

## 最新增量 · 2026-10-03 · W07共享上限与成员消费归属

SharedLimitChange以成员／版本及请求键追加保存上限变更与操作人：null不限、0禁止、正值限额；账户锁保护版本校验与任务预占。Reservation新增成员和服务月起止关联，0010／0011增加模型及完整性约束。内部企业预占要求有效成员、本人席位和生效中的已付期间，只选择本人席位批次及本组织无席位关联的购买池，按原到期顺序分配。共享限额计入当期已结消费＋未结预占；额度不足或超限不部分建任务。结算差额释放后才恢复余量；达到共享cap仍可用本人内含，不会读到另一席位额度。

上限降低不追扣历史消费，服务月切换只重算用量不清配置。跨服务月尚未结算的预占保持阻断，不能将D11未知规则默认为可结算。D08到期组织的月上限锚点未确认，因此此内部消费切片限有效订阅；到期旧成员使用未过期池的完整规则仍未实现。到期管理员配置只允许收紧，未开放管理HTTP写接口或公共任务执行；公开能力仍关闭。

验证：隔离PostgreSQL **130 tests / OK，0跳过，25.958秒，exit 0**；0010／0011迁移往返／无漂移通过，含共享＋本人独立额度排序、结算释放、降低上限不重置、跨成员拒绝、服务月用量重置和并发计入预占。没有真实任务或收费调用。下一步补组织设置只读数据与UI，再落实高风险写操作门禁，不把内部函数等同可用产品页。

## 最新增量 · 2026-10-03 · W07席位成员分配与换人审计

PaidSeat增加既有Membership关联及版本号；SeatChange保存操作人、前后成员、请求键与前后版本。内部assign_seat复用账户锁和owner／billing_admin校验，只能操作本企业已存在且有效的成员；不创建邀请或成员。重复请求返回原操作结果，不覆盖较新修改；过期版本拒绝，同成员不可重复占同一订阅多席。到期禁止分配／换人，允许解绑；解绑不减少购买席位，也不等同从组织移除成员。

换人不发新额度、不动批次／锚点／到期日，审计失败与成员更新一起回滚。有未结任务占用时先拒绝交接，D11实际任务终止／移交流程仍待任务端契约，不假装已处理运行任务。公共操作仍关闭，尚缺高风险验证、管理页、共享限额及邀请占席D05策略。下一步实现成员共享额度上限和消费归属后再接管理UI。

验证：隔离PostgreSQL **125 tests / OK，0跳过，21.140秒，exit 0**；0009迁移往返／无漂移通过，含换人批次不变、重复／陈旧请求、越权／跨企业、同成员重复占席、到期解绑、并发唯一赢家及审计失败回滚。仅隔离库迁移；无真实收费、GitHub推送或公开页面改动。

## 最新增量 · 2026-10-03 · W07付费席位与独立月额度

企业已确认付款／授权的内部订阅激活复用现有Subscription，创建PaidSeat固定位置；最低2席，未知资源配置拒绝。已发布商品权益必须明确resource_mode：seat_included（Teams，每席月度额度）或shared_only（Enterprise，额度必须为0）。尚无正式企业商品，测试数值不是定价批准。

Teams逐月额度关联席位、期间和服务月，个人继续原单批次逻辑；年付不一次发全年。Enterprise创建席位但不发内含额度。续费建单不再硬编码1席，保留购买数量、原席位及锚点；同单激活与月发放幂等。尚未实现成员分配／换人、增减席费用、共享限额及企业消费，企业任务预占仍拒绝，不能把本节当企业功能已可用。

0008新增PaidSeat及批次关联，个人服务月唯一约束改为seat为空条件，企业使用期间＋月＋席位唯一。仅隔离库验证，未回填业务数据。带企业多席位批次的数据若需回退旧唯一约束，必须先备份并制定保留／迁移方案，不能以空库往返成功宣称带数据可直接回退。

验证：2026-10-03隔离PostgreSQL **120 tests / OK，0跳过，19.468秒，exit 0**；0008迁移往返／无漂移、Teams两席逐月发放、Enterprise零内含、未知配置拒绝和年度续费保留席位通过；原个人版及本地HTTP浏览器测试保留通过。diff检查通过。无真实收费、无GitHub提交；未改页面／静态预览。下一步成员分配审计与换人不重发，再共享限额及管理页。

## 最新增量 · 2026-10-03 · W06可信任务成本事件

新增TaskCostEvent及0007迁移，复用Reservation关联和账户锁；每账户／来源／事件号唯一，记录只允许数值用量白名单、资源标识、重试次数、终态、成本金额／币种、发生时间及证据摘要。输入必须是可信任务端的单次操作最终计量，不是累计快照；事件号必须覆盖独立重试，禁止提示词／模型输出进入计量payload。相同事实重放幂等，改任务或改证据拒绝。迟到成本可在任务已结算后留存，不重扣用户Credits。

成本未知保持null与未知数量，已知成本用现有精确数值表示，按币种分别汇总、不自动换汇、不折算Sage Credits。无公共成本写接口，证据摘要不替代上游真实性校验。未完成项：真实任务调用及验真、成本更正/补全的审查流程、任务预算暂停恢复、经批准的成本到Credits规则、跨期／失败计费；这些不能由新增成本表推断已完成。W06后续仍须与任务执行方契约对齐，D11集中第二轮确认，企业席位可独立继续。

验证：隔离PostgreSQL **116 tests / OK，0跳过，18.322秒，exit 0**；0007迁移→撤回→重迁及无漂移通过，含成本并发去重、跨账户隔离、不可篡改、未知成本与不同币种、迟到重试不收费。django-tdd先用缺失入口复现失败，再实现；无新增依赖。未改UI或静态站，无真实调用／收费／GitHub推送。迁移没有应用到现存业务库。

## 最新增量 · 2026-10-03 · W06额度冻结

本节优先于下方历史进度。新增内部CreditFreeze记录及0006迁移，复用账户行锁与CreditEntry流水；指定批次／申请幂等冻结，只从可用数量扣转冻结。有任务预占时须先结算，不能同时花和退；失败／未知无释放入口。只有可信已退款或已确认未退款且申请终止的调用者可提交终态证据，证据哈希本身不是渠道验签。终态重放不重复核销，冲突拒绝，写入中断整体回滚；过期后释放回到过期而非可用。

账单汇总及批次现在返回真实frozen／refunded，不再硬编码冻结为0；原账单冻结总数读取复用，未修改页面。退款报价、金额保留、资金退款事实、年度未来服务冻结、申请操作UI及渠道调用尚未实现，`refunds_available`仍为false。不得直接将内部数量函数开放给浏览器。任务成本事件、D11跨期计量与企业共享权限仍待实现，没有把W06整包标完成。

验证：2026-10-03隔离PostgreSQL **111 tests / OK，0跳过，17.034秒，exit 0**；含0006迁移正反往返／makemigrations无漂移、并发冻结防超支／同申请单次核销、跨账户拒绝及异常写入回滚。diff空白检查通过。仅隔离测试库迁移，不触碰业务库；无真实收费／退款／邮件／GitHub提交。采用django-tdd先复现缺失能力再实现，没有新增依赖。公开静态版本未变更，因为本轮仅后端增量。

## 当前开发基线 · 2026-10-03 · 续费与主动补缴内部闭环

最新增量（优先于下方历史记录）：主动补缴的唯一订单、条款确认、创建／派发双重资格检查和新周期激活已实现内部函数；没有开放公共付款入口。migration0005增加可空的原尝试关联与唯一约束，仅在隔离库验证往返，没有回填或改现存业务数据。

- 补缴只接受已明确失败或从未派发的过期原尝试；未知／已派发过期／原款晚到均不新扣。并发建单和派发只认领一次，保留`recovery:`内部命名空间。
- 激活目前实现“旧授权确认撤销＋不同的新授权确认”分支；保留旧账期，新周期从可信实际开通时刻起算，首月额度只发一次。尚未实现复用旧授权调整扣款锚点的渠道分支，不把该实现限制当作新商业政策。授权未就绪保留付款事实，不假开通。
- 两项审查缺陷已由失败测试复现并修复：补缴资格读写不一致；补缴完成后原交易晚到账提示被隐藏。相关状态继续待核查、禁止重复付款、不重复发额度。后续交付调用方必须使用专用补缴激活入口，不能直接绕过到通用激活。
- 新鲜证据：隔离PostgreSQL **105 tests / OK，0跳过，16.520s，exit 0**，包含0005迁移往返／无漂移和两条本地HTTP浏览器链路。此前23组UI检查为上一增量证据，本增量没有修改UI。没有真实支付、授权撤销、邮件或退款调用。
- 公开静态预览已发布：[Sage 官网与订阅页面](https://sage-billing-preview.lillian-lige.chatgpt.site)。新项目`appgprj_6abfd98511a88191b89aa25c2887ba75`，版本1，部署`appgdep_6abfdb196a10819184bc78502490aa55`为succeeded。仅独立预览目录的dist进入Sites；主仓库原托管配置未改、没有GitHub提交或推送，原官网未覆盖。公开站没有Django／数据库／内部PRD／密钥，不能真实登录或收款。
- 下一顺序仍为W06任务成本事件及冻结→W07企业席位／共享上限→W09授权生命周期→W08自动充值→W10退款→W11财务。W05真实渠道补缴、生产调度和提醒仍待接入，D03／D13未决部分不擅自定值。

后续任务先读本节、根目录 README，以及 Sage 资料目录的 `docs/Sage_支付PRD_2026-10-02.md`、`Sage_支付开发计划_TODO_2026-10-02.md`（§10 为当前进度）和覆盖审查。下方 M0 与 2026-10-01 段落是历史证据，不覆盖本节。用户已确认 Django + PostgreSQL 和首页测试时钟修复；不要再次询问是否允许新建后端。

### 实际实现与边界

- Python 3.12、Django 5.2 LTS、PostgreSQL 单体，复用既有页面与规则函数，没有新造多渠道框架。依赖锁定在 `backend/requirements.txt`，由 `requirements.in` 生成，不手改锁定清单。
- 数据模型：唯一登录身份／计费账户／成员关系、浏览器绑定验证码挑战与限流、采购申请与审计；商品版本、报价、订单、已确认付款事实、Credits 来源批次、预占与账目。迁移已生成，仅在隔离测试 PostgreSQL 和专用本地开发库执行，没有连接或迁移现存业务数据库。
- 邮箱验证码后端不回传明文、不写日志、不提供固定验证码；过期、错误次数、浏览器绑定、重放和重发限制均在服务端验证。成功登录轮换会话，退出撤销会话。SMTP 未配置时拒绝发送；手机号渠道未选定，能力返回不可用，不假装送达。
- 每次请求检查成员身份与 owner／billing_admin 权限。前端读取真实账户、Credits、订单和交付状态；空数据与服务不可用明确分开。账户切换、迟到响应、401 和跨标签页账户冲突均清理或隔离旧数据。没有账号资料时显示脱敏标识，不编造姓名。
- 企业采购申请真实写入账户归属记录，重复提交去重；响应仅表示已接收，不宣称邮件已发、合同已签或权益已开通。旧 profile 入口转到账单中心；trial 不再使用本地演示钱包或浏览器自报扣费／完成。
- 商品完整性、CNY、地区与渠道单次／月续费／年续费能力在服务端检查。内部报价与订单有快照、有效期、条款版本和幂等约束。对外报价仍为不可付款参考，真实渠道开关仍关闭。
- 内部 `record_confirmed_payment` 只接受可信服务端调用，未暴露为可伪造付款的 HTTP 接口。资金事实先独立持久化，再尝试交付；交付失败不抹去已收款记录。除金额／币种外，还必须匹配订单快照的渠道／商户；不匹配留审查记录、不发额度。重放、第二笔实付和晚到账同样保留。内部付款事实及证据哈希不等于已执行验签或渠道查单。
- `PaymentAttempt`／事件记录持久化同一订单与业务意图；派发认领先独立提交，重复认领不再次派发。派发后超时为未知，不推断未付款；成功必须另有已确认资金事实。冲突终态标为待核查，账单显示真实状态并只读刷新。内部续费生成器已用“订阅＋计费期”唯一约束，客户端不能占用 `renewal:` 幂等命名空间；派发事务复查授权、唯一执行方、期间、快照和显式扣款窗口。当前没有 PSP 网络调用、公共支付尝试创建接口或渠道查询任务。
- 已实现购买 Credits 的幂等交付、精确数值批次、到期顺序、预占／结算／释放和防并发超支。个人订阅可由内部已确认付款＋授权证据激活；保留原始月锚点，年付逐月幂等发放，提前已付续费不提前发额度。`grant_subscription_credits` 命令可重入，未配置正式调度；时区／计费规则由可信调用者显式传入，不把测试选择当 D03 决策。
- 取消续费先关闭本地自动续费并保存“处理中”；渠道确认记录才变为已取消，不收回已付期间与额度。取消与派发共用账户锁；取消先发生则关闭尚未派发订单，已认领则视作在途，只查原交易并保留已确认款项交付，不承诺网络绝对只发送一次。
- 续费失败窗口由持久化期间、授权和交易事件推导；依据订阅显式保存的账期时区计算三个日历日，未据此批准 D03 全部政策。到期不送权益／额度；窗口后管理命令幂等关闭未来扣款、等待渠道解约。漏调度而无订单也会关闭，但不推断未扣款、不开放新付款。中英账单提示失败／未知／窗口关闭／缺失记录，`manual_payment` 能力保持关闭。补缴内部建单及新授权激活见顶部；真实渠道撤销／查询、升级、企业席位仍未完成。
- 一次体验的场景／输入锁、资格状态和幂等事件已持久化，按统一用户而非账户限制；恢复必须复用原对象，完整报告持久化并核验哈希后才核销，可信不可恢复故障才释放。没有公开任务执行／完成接口，不抓取 Shopify，不将报告哈希当作内容质量验证。真实分析任务、成本事件和端到端交付仍待接入。
- 批次退款冻结／恢复／核销内部台账见最新增量；退款金额及资金闭环、企业共享权限与上限尚未实现，对应入口关闭。

### 接口与运行约束

| 路径 | 当前行为 |
|---|---|
| GET `/api/auth/session` | 会话、脱敏身份、计费账户、当前账户、真实认证能力与 CSRF token |
| POST `/api/auth/challenges`、`/api/auth/verify` | 创建／验证浏览器绑定验证码；真实 SMTP 配置前不发送 |
| POST `/api/auth/logout` | 撤销当前会话，不承诺撤销所有设备 |
| GET `/api/billing/account`、`/api/billing/orders` | 当前账户、Credits汇总与批次、订单真实读取；未登录401、成员无财务权限403 |
| POST `/api/billing/accounts/select` | 选择当前计费账户，必须为有效成员 |
| GET／POST `/api/billing/purchase-inquiries` | 账户隔离的采购申请；POST 需 CSRF、角色和账户上下文 |
| POST `/api/billing/quotes` | 无数据库写入的不可付款参考；不生成可支付报价 |
| POST `/api/billing/orders` | 严格持久化订单契约；仅可信已发布商品／有效报价且销售与渠道获准才允许 |
| GET `/api/billing/subscriptions`；POST `/api/billing/subscriptions/{id}/cancel` | 真实期间／取消状态读取；取消需角色、CSRF与账户上下文，先本地阻断，再等待渠道确认 |
| 退款／发票／支付方式及支付回调 | 尚未实现或渠道未接通，明确拒绝，不返回伪成功 |

保护写请求须携带 `X-Billing-Account` 和会话 CSRF token；请求账户与服务端当前账户不同返回 `409 ACCOUNT_CHANGED`，前端不得自动把旧提交重放到新账户。读取指定账户上下文时同样检查。`accounts/select` 切换例外用于显式切换，但依然检查身份与成员关系。错误响应含 `request_id`，所有 API 响应 `Cache-Control: no-store`。

生产默认 `DEBUG=False`、安全 Cookie、HTTPS 重定向；缺 `SAGE_SECRET_KEY`、明确 `SAGE_ALLOWED_HOSTS` 或 PostgreSQL 配置直接启动失败。数据库变量为 `SAGE_DB_NAME/USER/PASSWORD/HOST/PORT`。真实邮件需显式启用 `SAGE_AUTH_EMAIL_ENABLED=1` 并在运行环境提供 `SAGE_EMAIL_HOST/PORT/USER/PASSWORD/FROM`。不读取或提交 `.env`，不把密钥放在文档或前端。

当前 OTP 5 分钟有效、最多5次验证、60秒重发间隔、15分钟每身份5次／每IP20次，是工程安全默认值，不是待确认商业计费 D03。生产代理需明确可信代理链和 HTTPS／来源IP传递；不能直接信任任意 `X-Forwarded-For`。目前以 `REMOTE_ADDR` 限流，未经部署适配时代理后所有用户可能共用一个IP配额，不能直接上线。

本地运行（新建独立 `.venv`；已有虚拟环境可跳过前两步）：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
python3 build.py --release
mkdir -p tmp
SAGE_ENV=development .venv/bin/python manage.py migrate
SAGE_ENV=development .venv/bin/python manage.py runserver 127.0.0.1:8915
```

仅 development／test 且无 PostgreSQL 配置时使用 `tmp/sage-local.sqlite3`；启动前目录须存在。本地随机密钥只适用于开发，重启可使已有会话／验证码失效。公网需正式进程服务、同源反向代理、静态资源服务与持久化数据库；不得把 runserver 或旧 WSGI 开发服务器公开暴露。

### 本轮新鲜验证

| 验证 | 最新实测结果 | 证据边界 |
|---|---|---|
| `node tests/postgres-audit.cjs`（设置 `PLAYWRIGHT_MODULE`） | **2026-10-03：95 tests / OK、0跳过，exit 0，14.218s**；含 migration0004 的迁移→反向撤回→重新迁移及makemigrations无漂移通过 | 原83项全部保留，新增续费并发、取消、托管执行方拒绝、保留编号、失败窗口／漏调度等12项；只在隔离PostgreSQL运行，无真实支付 |
| 上述浏览器链路 | **2条通过**：原账户／采购链路；新增真实API读取订阅→取消→刷新仍为处理中→已付额度保留→退出 | 邮件用内存后端，订阅由明确标记的内部付款／授权夹具建立；浏览器HTTP不拦截。不是真实邮件／收款／解约 |
| 旧 Python 规则与HTTP测试 | **36 tests / OK** | 本地报价／纯计算，不是渠道支付 |
| `tests/account-ui-audit.cjs` | **23组通过，0 pageerror**；含续费失败／未知／窗口结束提示、取消确认／焦点／防重、账户竞态及8组中英宽度200%文字检查 | 16次写请求全部由显式fixture拦截，非交易证据；390英文与1440中文截图已检查 |
| `tests/billing-audit.cjs` | 24中英布局、12文字放大组合、方案／席位／返回／语言／错误／竞态通过 | 28次参考报价，无账户／交易写入；异常响应fixture单列 |
| `tests/release-audit.cjs` | 90批（中英各45）、12视口、11导航；两语言各实测2次自动推进、减少动态效果暂停并恢复 | 实测间隔约3000ms；没有降低45批断言 |
| 构建及语法 | release 构建、auth／billing／experience JS、`git diff --check`通过 | `src`为源头，`pages`与`dist`重新生成 |

测试命令使用既有 Playwright，通过 `PLAYWRIGHT_MODULE` 指定安装位置，通过 `SAGE_URL` 指定本地站点；没有新增浏览器运行依赖。PostgreSQL 脚本使用随机密码、随机回环端口，只清理本次创建的唯一测试容器，不读取生产连接信息。反向撤表仅验证空测试库的可逆性；有业务数据后必须备份和向前修复，不能照搬测试回滚。

本轮复盘：续费测试先因缺函数失败，再暴露 `Order.created_by` 字段引用和时区序列化问题；已修根因，未放松原断言。新建与数据库重读的 PaymentAttempt 到期时间统一序列化 UTC。内部命名空间必须在公开入口拒绝，不能只靠下游函数；到期清理不能依赖调度曾成功建单。迁移0004新增可空关联与约束，不回填既有数据，尚未应用到现有业务库。当前只实现可信调用者提供窗口的到期前派发，尚未实现到期即时扣款与主动补缴收费闭环。

### 根因修正与后续顺序

- 首页曾44／45失败，根因为真实interval与手动回调两套时钟共同推进。覆盖遍历改为单一受控时钟，真实3秒／暂停／恢复单独验收，不改变产品轮播也不降低覆盖。
- 并发验证统一先锁身份限流记录再锁挑战，避免互锁；过期时刻在获得锁后读取，避免等待期间过期仍通过。
- 超长 JSON 整数应为422输入错误，不暴露为500；敏感认证／申请变量从框架异常报告隐藏，不能打印OTP、邮箱或采购信息排错。
- 登录页宽度必须考虑公共 `.page` 选择器优先级；财务账户改变或会话过期时先清旧DOM再请求，防止旧余额／订单闪现。
- 回调身份必须核对商户和渠道，不止金额／币种。先用错误身份回归复现旧函数错误交付，再修严格校验，暴露5个旧测试的商户前提不一致（其中1项测试错误金额，不应统称成功用例）。首次83项为78通过／5失败；用户随后授权自主处理，只将这5处merchant_reference修正为订单的fixture-shenzhen，保留全部断言及错误身份用例，重新83项全通过。不放松生产校验，也不以缺真实支付账户为由跳过本地资金安全测试。
- 新界面验证必须先 `node --check` 和重新构建，运行旧生成包不能证明新源码有效。全页截图的屏外固定 skip 链接疑似异常，应先查实际 DOM 边界，不能仅凭截图改 CSS。
- 新增迁移只在隔离 PostgreSQL 及核实为0账户／0订单的专用本地 SQLite 执行。反向迁移是空库验证，不是生产账务回滚方案。事务语义依据 [Django 5.2 transactions](https://docs.djangoproject.com/en/5.2/topics/db/transactions/) 与 [select_for_update](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update)，SQLite 不替代行锁测试。

5项测试前提及全量回归已闭环，不再是开发阻塞。后续补 W04可信意图／查单与授权撤销执行器、W05续费失败持久化、W06真实任务／冻结、W07席位与共享上限、W08/W09授权充值、W10/W11售后财务。D02/D03等只阻塞对应商业分支；其余明确代码缺口仍需继续开发。生产部署、真实通知、商户和参数未就绪；本轮没有真实收款、退款、发票发送或GitHub推送。商户相关联调留至接入条件就绪，不把本地全绿等同完整PRD验收。

**本轮未更新公开链接。** 历史 [Sage 2.0](https://sage-2-0.liliang2022712.chatgpt.site) 版本4于2026-10-02 17:20部署成功，部署 `appgdep_6abf7759a4008191a61eb56295b899e0`；不含本轮新增界面。当前连接读取原项目 `appgprj_6abc75a1b6048191b98185eca60ea25b` 返回 `Sites project not found`（NOT_FOUND），可编辑站点列表仅有另一个旧 Sage v0.6.1。已请求用户选择恢复原发布账户或在当前账户新建独立预览；未覆盖旧官网、未擅建替代站。静态公开包不包含Django、数据库、内部PRD或密钥，不能据静态发布声称认证／收款已上线。

用户补充官网源码位于 [lillian-maker/Sage-2.0](https://github.com/lillian-maker/Sage-2.0)。已只读核对公开仓库的首页／src／pages，以及本地 `origin`、`.openai/hosting.json` 和提交1422cbd中的托管配置：均对应此源码仓库和上述原项目ID。源码未丢失，不需重新克隆覆盖本地增量。源码存在不等于当前Sites连接具备发布访问能力；NOT_FOUND也不能直接推定项目已删除或确切权限原因。保持不commit／push，不覆盖另一个旧站。

## 历史基线 · 2026-10-02 · M0合回

后续维护先读本记录，以及 Sage 资料目录中的 `docs/Sage_支付PRD_2026-10-02.md`、`Sage_支付开发计划_TODO_2026-10-02.md`、`Sage_支付覆盖审查_2026-10-02.md`。下文 2026-10-01 是历史记录，不能覆盖最新规则。最新 PRD 不含商业参数猜测，待确认项为 D01–D16。

此前在 `Sage/tmp/payment-integration-20261002` 隔离副本实施并验证；2026-10-02 用户明确授权合回，11 文件增量已应用到 Sage-2.0，保留无关改动，不 commit / push。后续开发以本仓库为源头。用户同时确认没有通用后端，授权新建；Python / Django + PostgreSQL 是待确认的候选技术方案，尚未安装依赖或执行迁移。不得直接把临时 WSGI 服务开放到公网。

实际新增：严格商品配置校验；运行时强制不可收款；公共目录包含已确认规则；试用锁定/核销、Credits 到期排序、年度升级月额度差额、续费失败及支付方式移除的纯决策函数。后三类仍未接数据库、任务服务与授权渠道；测试通过不等于业务闭环。

前端继续使用原三页：补企业席位与 URL 状态恢复、错误重试和响应竞态保护，CNY 金额保留分；新账单视图覆盖支付方式、企业设置与合同采购说明。未知账号/余额/订单明确不可读取，不显示虚假 0 或演示账户。首页账户及页脚进入账单，英文链接正确；不修改首页地球、星空、头像或主视觉。静态参考价与真实可支付报价必须区分。

本轮新鲜证据：

- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_billing*.py' -v`：36 tests / OK。包含 21 纯规则及 15 本地 HTTP 用例。初次沙箱不允许监听，获批准后在回环地址重跑通过。
- `node --check src/pages/billing.js`、`node --check tests/billing-audit.cjs` 和 `python3 build.py --release`：通过。
- `tests/billing-audit.cjs` 对本地 8912：24 三页布局、28 账单锚点、12 文字放大组合、13 非法 URL，以及返回/语言/席位/重试/响应竞态。28 次参考报价，0 账号/订单/支付写入。异常响应为显式 fixture，正常报价是真实本地 HTTP，不是 PSP。
- `tests/release-audit.cjs`：90 批头像、12 首页语言/尺寸组合、11 导航链接通过；另实点 4 条中英/桌面/移动首页账户→账单→换语言链路，无 pageerror。
- 三页桌面截图和移动 Credits 页面人工复核。IAB截图曾露出屏外 skip 文字，DOM位置及独立Chrome三页均未复现，不据截图异常盲目修改 CSS。

长期维护经验：入口查询参数仅在初始化时验证，不在每次切换方案时拿旧 URL 重验；显式空值不自动改为另一套餐；不要让企业席位泄漏到个人报价；英文周期按钮必须测 390px 与 200% 文字。异步响应需和当前选择绑定，过时响应不能更新金额。

账务经验：Decimal 默认 28 位上下文不保证任意输入精确，补差按输入精度隔离计算并显式捕获精度异常；服务端任务完成核销要核对任务身份，不忽略显式错误的场景或输入。纯函数布尔值不能替代真实任务完成证据、账户权限、事务和 CAS。

生产未完成：D01 已关闭“是否已有后端”的信息缺口，新建授权成立，但技术栈、部署和真实验证码送达仍待落实；D02 商品价格权益未齐；D15 商户未认证/签约。不得将商品 `purchasable` 或渠道 `enabled` 改为 true 以绕过这些前置条件。真实首付、自动续费、退款、发票、对账必须在后续实际服务中实现并受控验收。分享链接仅是公开页面预览，不托管此 Python 后端。

合回后验证：在实际 Sage-2.0 目录重新执行 release 构建及 36 项 Python 测试，通过。第一次 HTTP 测试因沙箱禁止绑定端口失败；仅在批准本机监听后重跑，未修改或跳过断言。浏览器回归证据另在计划文件 §8 追加。

合回后支付 UI 回归通过：24 三页布局、28 账单视图、12 文字放大组合及响应竞态、错误恢复，28 次参考报价、0 交易写操作。首页 `release-audit.cjs` 首次 44/45 失败已定位为检查脚本的双时钟竞态：真实 3000ms interval 与手动 callback 同时推进。6 个中英文独立诊断样本证实，跨时钟边界为 44 批、受控单时钟为完整 45 批。测试尚未修改，完整首页回归不标通过。后续获准修复时保留 45 批断言，分离批次遍历与真实时间行为测试；不得通过放宽覆盖数量掩盖竞态。

日期：2026-10-01。范围：Sage-2.0，不修改 VOA/BOS。维护前读取根目录 README 和本文件。

## 本轮实际实现

- 白底价格页：个人／企业、月付／年付、已确认个人月价 199／499／1990 元；未定价不制造折扣或可付款入口。
- 方案确认页：服务端价格核验、五种支付方式的未接通状态；不采集卡号、协议签约或支付授权。
- 账单中心：订阅、Sage Credits、订单、发票的未认证状态，不编造余额和历史订单。
- 中英独立文案及响应式布局。沿用首页字体和标识，银灰及深色主按钮；只改变支付相关页面，不改首页背景。
- 本地 Python WSGI 报价服务：复用现有 Python 环境，无第三方依赖。标准库服务器只允许回环访问，不可生产部署。
- 账期锚点、年度剩余整月退款、购买批次比例退款的纯计算函数。

## 视觉设计维护规则（2026-10-01）

支付三页白底、近黑主文字与按钮，浅中性色仅用于分组。标题和套餐名称统一使用既有无衬线字体，不为装饰单独换宋体或衬线字体；通过字号、字重、留白及对齐建立层级。价格页居中，方案确认与管理页左对齐。维护视觉实现时一并读取根目录 design-qa.md。

图标来自 Bootstrap Icons v1.11.3，保留 src/pages/icons/LICENSE；使用本地资源，不新增运行依赖。视觉改造不改变支付能力，未接通渠道保持禁用。

字重修正：统一无衬线字体，主标题 500、套餐名称 400/28px、价格与按钮 500；标题色 #303030、正文 #595959、周期说明 #767676，按钮背景独立为 #242424，避免柔化文字时把按钮一起淡化。价格页标题上限从 50px 调整为 46px，账单标题 37px，确认页标题 32px。用户不希望标题、价格和按钮同时粗黑。

本次样式微调验证：release 构建与 diff 检查通过；三页桌面截图已逐页检查，中英文三页在实测 390 CSS px 宽度均无横向文档溢出，标题计算字重均为 500。确认页报价保持 ¥199。未改支付逻辑，本次未重跑后端测试。

本次新鲜验证：release 构建、JS 语法、diff 检查及 16 个后端测试通过；Codex 内置浏览器检查三页、中英文切换、套餐周期与类型、账单导航、移动端无横向溢出、图标加载和未开放支付状态。完整记录见 design-qa.md。此前 24 组及首页回归数据属于前一轮验证，不能作为本轮重新执行的声明。

## 接口与安全边界

2026-10-01 银灰与入口修正：支付页 surface 改为冷银灰 #edf1f5，分段选择与账单选中态以首页 #c4d0dc 的浅色层级呼应。金额用独立 currency-symbol 展示左上角 ¥，保留可访问的完整金额标签；订阅按钮进入确认页，不直接扣费。移除确认页重复 logo 区块。支付宝与微信采用既有 Bootstrap 标识的品牌色，Google 彩色标识下载自 https://www.gstatic.com/images/branding/googleg/1x/googleg_standard_color_128dp.png 并本地托管；Apple 保留黑色，银行卡不冒用未签约卡组织标识。

顶部账号菜单目前只展示真实未登录/账号未接通状态，不伪造已登录用户；显示真实头像、名称、登出等仍依赖认证实现。价格页和账号菜单的加购入口跳转 billing.html#credits，展示有效期与一次性购买规则；额度包价格未配置，实际购买保持禁用，不创建虚拟余额。相关英文入口一致。

该次修正已在内置浏览器验证订阅跳转、报价 ¥199、彩色资源加载、移除重复标识、账号菜单与加购跳转；中英文三页在 390 CSS px 宽度无溢出，展开菜单也位于视口内。构建、语法、diff 与 16 个后端测试通过。真实账号、加购交易与收款仍未实现。

| 接口 | 当前行为 |
|---|---|
| GET /api/billing/catalog | 读取 src/pages/billing-catalog.json；所有商品 purchasable=false |
| POST /api/billing/quotes | 严格校验方案、周期、席位；返回 preview_only=true；非可支付报价，不创建 quote_id 或订单 |
| /api/billing/orders、subscriptions、credits、refunds、refund-quotes、invoice-requests、payment-method-sessions | 401 AUTH_REQUIRED，未实现身份与写操作 |
| /api/payments/webhooks/* | 503 CHANNEL_UNAVAILABLE，不能确认任何付款 |

报价请求只接收 plan_code、billing_cycle、seat_quantity。多余金额或身份字段被拒绝。年价为空时返回 PRICE_NOT_CONFIGURED；不使用默认年折扣。金额为整数分。报价校验仅用于开发，尚无持久化报价、实际税额或服务合同。

本地服务限制 Host、跨来源写请求、请求体大小；静态路径只允许首页、pages、assets，拒绝源码和路径越界。没有支付密钥、用户账户或数据库迁移。不能把本地 Host 检查当生产认证方案。

浏览器先请求同源 API，无法连接时读取同源静态商品目录；收银台必须明确显示静态预览且不显示已核验价格。任何前端参数都不能开启渠道或模拟支付成功。

退款函数仅适用于单个未经变更的费用明细。升档、既往部分退款、到期争议、税务和多个服务区间尚需结算实现。测试通过不代表可以执行退款。

## 构建与测试

```sh
python3 build.py --release
python3 -m unittest discover -s tests -p 'test_billing*.py'
python3 -m backend.billing --port 8900
# 另一个终端，使用既有 Playwright；路径由环境指定，不新增运行依赖。
SAGE_URL=http://127.0.0.1:8900 node tests/billing-audit.cjs
SAGE_URL=http://127.0.0.1:8900 node tests/release-audit.cjs
```

如 Playwright 不在项目模块路径，设置 PLAYWRIGHT_MODULE 为已有安装路径。可通过 SAGE_SCREENSHOT 设置视觉截图输出位置。

本轮验证：16 个 unittest 通过（含非法 Host、路径越界、请求体大小和非对象 JSON）；支付页中英 3 路由 × 4 宽度共 24 组检查通过；真实本地 HTTP 报价、未知套餐、静态降级用例通过。原站回归：90 批头像切换、12 个首页语言／视口组合、11 个导航链接通过。dist 中三个支付页面的资源加载及静态报价不可用状态通过。未执行任何生产交易。

修正经验：新页面引用公共资源时必须检查 dist 发布包，不能只验证仓库根目录服务器。本轮已将 favicon.svg 纳入发布复制；计费页使用单独的双语脚本，避免旧的子串翻译破坏新接口键名。

本地 HTTP 测试显式关闭客户端代理发现，避免伪造 Host 用例被机器代理转发。原用例曾超时；直连验证服务返回 403，测试客户端改为直连后同一断言通过，没有删除或放宽断言。

## PRD 差距复盘与有序执行清单（2026-10-01）

### 依据与结论

本次以当前源码、目录结构、商品配置、测试以及用户在本对话确认的规则为依据。当前仓库和已知可视化工作目录未找到独立支付 PRD 文件；本节是执行追踪，不冒充已签字的完整 PRD。收到原文后逐项补齐映射。下列“推荐”不是已经确认的架构或商业规则。

已实现：三个中英文页面、订阅跳转、账号未接通菜单、加购说明入口、本地非可支付报价、年度/加购退款纯计算、部分请求边界测试。
尚未实现：认证、计费账户、数据库、订单、支付、签约、自动续费、权益与 Credits 台账、真实退款、发票、对账、企业合同核销。
当前完成度不能用页面数折算。真实收款与自动续费尚无成功证据。开发服务器不能作为生产服务部署。

本轮重新执行 16 个后端测试通过；这些只验证本地报价、拒绝访问边界和计算函数，不验证交易、并发事务或生产安全。未改 UI，未新增数据库或支付调用。

### 已确认产品边界

- 大陆主体、人民币优先；个人/企业两类，企业下含 Teams、Enterprise 和合同报价，2 个真人席位起购。
- 个人体验 0 元；Pro 199、Pro+ 499、Ultra 1990 元/月。个人年价、企业席位价、套餐 Credits 和加购包金额仍未配置。
- 上线即须支持连续包月、连续包年；不能用手动续费替代自动续费验收。
- 先支付宝与银行卡；微信、Google Pay、Apple Pay 纳入设计，是否首期接入另评估。展示 logo 不代表渠道或代扣权限已开通。
- 免费资格仅一次，在 VOC 机会洞察与 Shopify 店铺诊断中二选一；输入品牌名或店铺链接。任务失败后的资格恢复与滥用防护须明确。
- 统一叫 Sage Credits；不足时主动加购或升级，不默认自动充值、透支或未经授权执行。
- 当前记录：订阅额度逐月发放、当月有效；加购有效期 12 个月。最终规则须与正式 PRD/服务协议核对。
- 月度已开始周期原则不退；年付仅未开始完整订阅月；加购按原批次未用比例。现有函数只覆盖未变更的简单费用明细，不覆盖升级、历史退款、税费及争议处理。

### 顺序与验收

| 顺序 | TODO / 交付 | 前置条件 | 完成验收 |
|---|---|---|---|
| 0 | 核对实际实现，冻结当前 UI，建立本清单 | 当前代码 | 本轮已完成；未声称完成真实支付 |
| 1 | 确认产品后端、认证、数据库与部署归属，补齐 PRD 来源 | 用户提供现有仓库/路径；若无则先确认新建方案 | 有经确认的账号来源、计费账户归属、技术栈与环境方案；不重复建设账号 |
| 2 | 接入个人/组织身份与权限，真实顶部账户状态；数据库迁移方案 | 1 | 未登录拒绝；跨账号和跨组织访问被拒绝；管理员/成员权限明确；退出后会话失效；迁移/回滚在空测试库验证 |
| 3 | 商品与服务端报价：月/年、个人/企业、席位、加购包、价格版本、税费与续费文案 | 2；商业参数确认 | 未配置不可卖；不接收客户端金额；2 席位与角色校验；报价持久化、绑定账户、到期后重算 |
| 4 | 持久化订单与支付尝试：幂等、超时、取消、状态查询；补订单详情/结果/失败页面 | 3 | 重复提交不重复建单；并发、过期和乱序有测试；页面只依赖服务端归属校验后的状态 |
| 5 | 首次签约与支付：支付宝、银行卡的实际可用产品 | 4；商户签约与周期扣款能力批准 | 验签、金额/币种/商户/订单匹配；回调去重；主动查单；异常回调不发权益；敏感卡数据不经过 Sage |
| 6 | 支付确认到订阅、席位、Credits 权益交付；补偿与重试 | 4、5 | 支付确认和交付有事务/可靠重试；重放不重复发放；支付成功权益失败可恢复；重复实付进入退款/人工队列而非重复开通 |
| 7 | 连续包月/包年：授权记录、扣款调度、到期前通知、取消、失败处理 | 5、6；重试/宽限/通知规则确认 | 唯一扣款调度方；重复任务只扣一次；撤销授权后不再发起扣款；失败不误延长权益；端到端周期验证 |
| 8 | Sage Credits 与免费体验接入真实任务：批次、预占、结算、释放、到期、年付逐月发放 | 2、6；任务端接口与额度定义 | 并发不负余额；失败可释放；回调重放不重复扣；加购余额隔离；免费仅一次成功任务；客户端不能篡改成本 |
| 9 | 升级/降级、增减席位、退款：差额报价、原路退款、权益调整 | 3、6、7、8；业务规则确认 | 原订单累计退款不超实付；退款并发受控；未结任务先结算；回调失败可恢复；金额与权益一致 |
| 10 | 企业采购、对公转账、发票、日对账与异常处置 | 4、6、9；财务资料/流程确认 | 上传回单不能直接开通；财务审核有权限/审计；银行流水唯一核销；发票关联实付及退款；差异有负责人和处理记录 |
| 11 | 全链路回归、安全、备份恢复、上线配置与监控 | 前述必须项 | 未授权访问、验签失败、重复支付/回调、超时后到账、交付失败、取消续费与扣款竞态、退款/对账异常全部验收 |
| 12 | 受控上线与真实付款/取消续费/退款验证 | 11；用户另行批准真实交易与发布 | 经授权的小额真实收款、到账、权益、退款、对账及周期扣款证据；不以沙箱结果代替生产证据 |

步骤 1 是当前开发入口；不得在归属未定时新增第二套用户库或临时模拟登录。步骤 5 的申请可与 1–4 并行，但等待审批不等于代码已完成。步骤 7 是首发硬门槛，不后移到“后续优化”。

### 开发之外可并行准备的材料

以下为准备清单，不代表服务商最终资质要求；正式申请时查所选产品官方材料：
- 大陆公司主体、对公结算信息、商户申请负责人、产品域名与服务说明。密钥或完整银行资料通过批准的安全配置渠道提供，不写入聊天/源码。
- 支付宝具体签约产品及周期扣款权限；银行卡实际收单方、托管收银台/支付令牌、周期扣款支持范围。普通银行卡支付不等于支持自动续费。
- 商户号/应用号的非敏感标识、沙箱与生产环境区分、回调域名、证书管理方式、签约生效确认。
- 个人年价、企业席位价、各套餐 Sage Credits、加购档位/价格、税费/发票口径。
- 自动续费协议、提醒方式、取消入口、扣款失败与宽限规则、退款例外、升级差价、席位调整生效点、合同采购流程。

### 当前需确认的关键问题

优先确认：Sage 产品是否已有后端、登录账号和数据库？若已有，提供仓库或本地路径及 PRD 文件位置；优先复用。若没有，先形成简洁的新后端技术方案并确认，再实施数据库/认证；不把本地标准库 WSGI 服务直接扩成生产支付系统。

数据风险与回滚门禁：任何 schema/migration 开始前说明影响范围，先用隔离测试库验证，保留回滚与备份恢复步骤；未经确认不触碰已有产品数据。当前未做 schema 变更。

不自动 commit、push、发布或触发真实收款。保持现有 UI；下一阶段围绕身份与交易闭环，不继续堆装饰或把占位页算成能力完成。
## 最新增量 · 2026-10-03 · 正式报价确认与原订单恢复

公共购买目录仅提供已发布、已生效且当前账户可用的商品版本。真实双语服务条款须通过服务端审批配置，报价绑定条款内容 SHA256；缺少权益、条款、报价有效期或渠道准入时关闭入口。不预设尚未确认的价格、额度或有效期。

价格页已批准商品进入确认页，用户主动获取报价并逐项同意后才能创建订单。幂等键按账户和报价保存在 sessionStorage；提交结果未知只查询原报价／订单，不重报价或换键。创建订单不等于付款、签约或权益开通。页面支持中英文、320px及账户切换隔离；退出清除本地恢复标识。金额字符串复用 BigInt 精确格式化。

公开新购门禁在账户锁内阻已有自动续费、未结束或未来已付期间、未结订阅订单和待核查收款，不能绕过升档／转年流程。无有效付费期间的 Credits 新购保持关闭，D10 未决；已有未到期余额使用不受影响。内部续费／补缴路径不应用新购限制，同幂等键仍返回原订单。

已存体验报告新增原字段阅读版与完整 JSON 下载，不补写业务结论；文本安全展示，深层及宽字段回退原 JSON，限制 DOM 数量。订单详情独立显示重复收款／待核查，即使首笔已交付或未创建 PaymentAttempt；刷新只读，不把重复收款当成第二次权益。

测试经验：真实 HTTP 新购测试最初把 URL 对象和 `/pages/...` 字符串拼接成双斜杠路径，history.replaceState 抛 SecurityError。使用 new URL(path, base) 修复测试地址，同路径验证通过；未改业务代码掩盖错误。临时 JS 观测和调试器已移除，真实浏览器测试不拦截 API 响应。

最终证据：隔离PostgreSQL **188 tests / OK，0跳过，56.183秒**，包括迁移往返／无漂移、不同报价并发建单以及三条真实本地HTTP浏览器链路；旧账单HTTP与规则36项通过（0.554秒）。确认页双语隔离响应2组通过、0页面错误；账户UI32组通过、0页面错误，包含1200顶层报告字段完整保留与有界DOM；订单待核查9组通过、0页面错误、58次GET／0写。release构建、JS语法和diff检查通过。一次全量运行仍加载修改前Credits成功夹具，出现3失败2错误；改用真实首次订阅夹具并冻结测试文件后完整188项重跑通过，未放宽安全门禁或删断言。

Sites静态版本12发布succeeded：https://sage-billing-preview.lillian-lige.chatgpt.site 。version_id=appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_0f7e0a3772bc819195e63f83f23ba09c；deployment_id=appgdep_6ac0ee1195808191b298e7eabd233944；独立Sites源码349cb1a64e4538a92a09ad012fc4c6f2defc3c05，115文件／9,932,800字节。公开包不含后端、资料或测试数据，不能登录／收款；未推送GitHub。Sites发布按sites-hosting执行，同站点同公开范围，没有新建重复站点。

仍未完成：支付渠道签约／验签／实际扣款，D01–D16相关未决规则，公网后端认证与通知、任务执行、开票和财务高风险写流程。真实收费关闭；未提交／推送 GitHub。
## 最新补充 · 2026-10-03 · 采购回执与完整历史

R29采购申请成功和记录中展示真实UUID回执编号，展开查看原需求；公司名160字/需求2000字与后端一致。GET复用账户权限，按created_at/id稳定排序50条分页，严格校验offset；前端只接收最新读取，迟到响应不覆盖翻页或提交后列表。未知提交结果只引导核对历史，不自动重发。无新schema、依赖或抽象。

新增分页测试先因缺account_id失败，再修复通过。最终隔离PostgreSQL189项全过（49.666秒，0跳过），迁移往返及无漂移通过；账户UI33组、0页面错误，覆盖采购回执、特殊字符、51条历史、迟到响应、未知提交、中英320px。三页专项24中英布局／12文字放大组合、键盘/返回/语言和28次只读参考报价通过，0账户或付款写入。

专项测试复现匿名价格页报错：purchaseCatalog和session均缺失时，两个undefined被相等比较误判为目录已加载。改为先检查purchaseCatalog存在，原路径由红转绿。加载占位含main，测试现等待实际h1再执行原断言；不删测试、不放宽结果。兼容只读服务必须用python3 -m backend.billing运行，直接脚本路径会令backend/http.py遮蔽标准库http。
### 发布核对修正

版本12的native部署确为succeeded，但后续包检查发现只同步了Sites根目录，而hosting.json实际static.directory为dist，archive内容仍旧。因此“版本12包含最新页面”不成立；版本13仅保存未部署。同站点重新同步dist，必须以diff -qr确认本地release与托管dist完全一致，再打包保存部署。不得用source commit变化代替实际发布产物一致性。此经验与源码测试分开记录，后续发布按hosting.json目录执行。
### 最终静态发布证据 · 2026-10-03

版本14部署succeeded：https://sage-billing-preview.lillian-lige.chatgpt.site 。version_id=appgprj_6abfd98511a88191b89aa25c2887ba75~appgver_0703f91662cc8191a1e7dc3ca3311e5b；deployment_id=appgdep_6ac0f2757b408191a0e30fa33cc62b11；独立Sites源码0641369edc63d95c2958d99e6bc9d001a7b8188c；archive 115文件／9,963,520字节，sha256:6e7ff0eeabcbee6ef15778b383d1216edbafb0033afc63606f11efa66761479a。diff -qr本地release与实际托管dist返回0；archive内billing.js与本地SHA256均为9f2610924523647fba65df2e0a3afba4046b642d82ec58487b3c3db7e4026b5e。该版本取代先前发布目录错误的版本12说明，版本13未部署。

最后前端保护补丁后：正式报价确认页中英×320/390/768/1440及200%文字通过，原同意／未知恢复／跨账户／过期断言全部保留，0页面错误；三页专项24布局、12文字放大组合与键盘路径通过。后端源码未变，189项PG是最后后端增量完整回归。所有证据仍属于隔离本地／响应夹具层，不代表真实付款。公共静态包不含后端或账户资料，真实销售关闭，未提交／推送GitHub。
