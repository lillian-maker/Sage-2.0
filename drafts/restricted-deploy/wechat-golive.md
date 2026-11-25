# 微信支付 Native 上线清单（C22 / C23）

范围：电脑网页扫码（Native）。可买 1 个月、1 年固定期限套餐和 Credits 点数包；不做自动续费、不做 H5。

## 1. 商户与密钥（服务器私有目录 `/opt/sage-docker-preview/shared/secrets`）

- 商户号 1004411391（普通商户，深圳市众妙之门科技有限公司）。
- 商户 API 私钥 `wechat_apiclient_key.pem` 已在服务器生成（PKCS8，2048，root 0400）；证书请求已提交，待管理员完成操作密码与短信验证后取回证书，读出证书序列号（`merchant_serial`）。
- APIv3 密钥：服务器生成 32 字节，直接填入商户平台，不经过聊天或仓库。
- 微信支付公钥与公钥 ID（`PUB_KEY_ID_...`）：商户平台申请后存服务器。
- AppID：公司名下认证服务号，在商户平台“AppID 账号管理”关联后填入 `app_id`。

## 2. `wechat_native.json`（属主 10001:10001，权限 0400）

字段：`enabled, app_id, mch_id, merchant_entity, merchant_serial, private_key, public_key, public_key_id, api_v3_key, notify_origin, payment_ttl_seconds`。
`merchant_entity` 与支付宝相同（`shenzhen-zhongmiao`），`notify_origin` 为 `https://sagegogo.com`，`payment_ttl_seconds` 900。

## 3. 部署

- `deployment.env` 的 `SAGE_COMPOSE_OVERLAYS` 加 `wechat`，挂载 `compose.wechat.yaml`（只读挂载上述文件，不开任何销售开关）。
- `compose.egress.yaml` 出站白名单已加入 `api.mch.weixin.qq.com`（下单、查单、关单、退款都经出站代理）。
- 开卖时在 `billing_sales.json` 增加：
  - `approved_channels.wechat`：与支付宝同格式，capabilities 取 `one_time`、`term_monthly`、`term_annual`；
  - `wechat_live_enabled`、`wechat_egress_proxy` 两个开关（必须成对出现）。
- 线上退款另有独立开关 `BILLING_WECHAT_LIVE_REFUND_ENABLED`（默认关）；和支付宝一样，目前没有对外的在线退款入口，测试款在商户平台手工退款并人工处理权益。

## 4. 行为

- 付款结果只认两种证据：验签解密后的支付通知，或服务器主动查单返回的 SUCCESS。
- 用户取消已出码的订单：先关单再查单，查到 CLOSED 才关闭订单；关单未确认时，用户再次取消或超时任务（`close_unpaid_orders`，每 5 分钟）会重发关单，普通查单从不重发。
- 退款：每笔退款只提交一次（持久化认领），之后只查询；只有退款查询返回 SUCCESS 且金额、单号全部吻合才确认退款。
