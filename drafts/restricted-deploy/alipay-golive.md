# 支付宝收款上线步骤（固定期限套餐 + Credits 点数包）

本文只描述支付宝电脑网站支付。微信支付暂缓；自动续费（连续包月/包年）未接入。
以下每一步涉及生产环境的操作（部署、迁移、发布商品、打开销售开关、真实付款与退款）都需要负责人单独批准后执行。
密钥、私钥不进入仓库、聊天或日志，只放在服务器私有目录。

## 1. 这次代码做了什么

- 套餐可按“固定期限、不自动续费”出售：渠道能力 `term_monthly` / `term_annual`。报价时把 `renewal: "none"` 冻结进订单；支付宝到账通知验签通过后，自动开通一个周期（付款时刻加一个自然月或一年后，顺延到次日北京时间零点），到期后用户可重新购买。
- Credits 点数包沿用原有一次性付款流程，只有套餐生效期内的账户可以购买；有效期为付款时刻加一个自然月后顺延到次日北京时间零点。
- 付款完成后支付宝把浏览器带回 `https://sagegogo.com/pages/billing.html?order=<订单号>`。页面只读取服务端订单状态，不把跳转参数当作收款凭证；最终结果以支付宝异步通知为准。
- 生产销售开关集中在一个经审阅的文件 `billing_sales.json`，不设置则全部保持关闭。
- 新增命令 `publish_billing_products`，用于把审核过的草稿商品发布上架。

## 2. 支付宝开放平台（负责人本人操作）

1. 应用 `Sagegogo`（AppID 2021007121629097）：把应用简介、网址改为真实内容（网址 `https://sagegogo.com`）。
2. 开发设置 → 接口加签方式：选择“密钥”模式、RSA2。用支付宝密钥工具生成密钥对，上传应用公钥，保存页面给出的“支付宝公钥”。
3. 确认应用已绑定“电脑网站支付”，提交审核并上线。应用处于“开发中”时不能在线上调用。
4. 记录商户 PID（2088 开头的 16 位数字），即 `seller_id`。
5. 应用网关、授权回调地址不需要填写；异步通知地址由每笔订单携带。

## 3. 服务器私有目录中的两个文件

目录即部署时的 `SAGE_DOCKER_SECRETS_DIR`。

### 3.1 `alipay_live.json`（含私钥；属主 10001:10001，权限 0400）

```json
{
  "enabled": true,
  "app_id": "2021007121629097",
  "seller_id": "<2088 开头的商户 PID>",
  "merchant_entity": "shenzhen-zhongmiao",
  "private_key": "-----BEGIN RSA PRIVATE KEY-----\n<应用私钥，PKCS1 格式>\n-----END RSA PRIVATE KEY-----\n",
  "public_key": "-----BEGIN PUBLIC KEY-----\n<支付宝公钥，不是应用公钥>\n-----END PUBLIC KEY-----\n",
  "payment_ttl_seconds": 900,
  "notify_origin": "https://sagegogo.com"
}
```

- 私钥必须是 `BEGIN RSA PRIVATE KEY`（PKCS1）格式，2048 位及以上。密钥工具导出的 PKCS8 需先转换：`openssl rsa -in pkcs8.pem -traditional -out pkcs1.pem`。
- `payment_ttl_seconds` 是下单后可付款的时长。用户离开支付宝页面未付款时，这笔订单要等到超时关闭后才能重新下单，建议 900。
- `merchant_entity` 是内部标识，必须与下面 `billing_sales.json` 中的值完全一致。

### 3.2 `billing_sales.json`（不含密钥；应用用户 10001 可读，不可被组或其他用户写入）

```json
{
  "sales_enabled": true,
  "alipay_live_enabled": true,
  "alipay_egress_proxy": true,
  "credits_purchase_enabled": true,
  "approved_channels": {
    "alipay": {
      "enabled": true,
      "currency": "CNY",
      "merchant_entity": "shenzhen-zhongmiao",
      "regions": ["CN"],
      "capabilities": ["one_time", "term_monthly", "term_annual"]
    }
  },
  "terms_documents": {
    "purchase-2026-11": {
      "approved": true,
      "zh": {"title": "<购买条款标题>", "paragraphs": ["<经批准的条款正文，逐段填写>"]},
      "en": {"title": "<Purchase terms title>", "paragraphs": ["<Approved terms, one paragraph per item>"]}
    }
  }
}
```

- 只卖点数包就去掉 `term_*`；只卖套餐就去掉 `one_time`。
- 条款文本必须是正式批准的中英文版本；商品的 `terms_version` 要与这里的键一致。
- 文件内容有任何多余或缺失字段，应用会拒绝启动，而不是部分打开销售。

## 4. 商品（正式价格与权益由负责人提供）

商品清单 JSON 示例（金额单位为分；数值仅示意格式，不是正式价格）：

```json
[
  {"code": "pro", "version": 1, "kind": "subscription", "audience": "personal", "billing_cycle": "monthly",
   "currency": "CNY", "unit_amount_minor": 0, "credits_quantity": "0", "minimum_seats": 1,
   "entitlements": {"benefits": {"zh": ["<权益说明>"], "en": ["<Benefit>"]}},
   "entitlement_version": "ent-2026-11", "credit_rule_version": "credit-2026-11", "terms_version": "purchase-2026-11"},
  {"code": "credits_pack", "version": 1, "kind": "credits", "audience": "personal", "billing_cycle": "",
   "currency": "CNY", "unit_amount_minor": 0, "credits_quantity": "0", "minimum_seats": 1,
   "entitlements": {"benefits": {"zh": ["<权益说明>"], "en": ["<Benefit>"]}},
   "entitlement_version": "ent-2026-11", "credit_rule_version": "credit-2026-11", "terms_version": "purchase-2026-11"}
]
```

同一 `code` + 周期只展示版本号最大的已发布版本；月付与年付要用不同的 `version`。价格发布后不可修改，调价需新增版本。

```sh
# 先校验，再导入为草稿
python manage.py import_billing_products products.json
python manage.py import_billing_products products.json --apply
# 先校验，再发布（立即生效）
python manage.py publish_billing_products pro:1 pro:2 credits_pack:1
python manage.py publish_billing_products pro:1 pro:2 credits_pack:1 --apply
```

在容器内执行时沿用 `README.md` 中的入口：`docker compose … exec -T app python /app/deploy/docker-entrypoint.py python manage.py …`。

## 5. 部署

1. 用本分支构建并审阅新镜像，按既有流程备份数据库；迁移以服务器上 `migrate --plan` 的结果为准（本仓库迁移不含 RunPython/RunSQL）。
2. 所有 compose 命令经 `prod-20261107/sage-compose.sh` 执行，它按固定顺序拼接：
   `compose.yaml → compose.offline.yaml → [compose.auth.yaml] → compose.egress.yaml → [compose.alipay.yaml] → [compose.sales.yaml]`
   可选文件在 `deployment.env` 中声明，例如 `SAGE_COMPOSE_OVERLAYS="auth alipay sales"`；对应私有文件放好后再加名称，否则 compose 会报缺文件。
   - `compose.egress.yaml` 的出站白名单已加入 `openapi.alipay.com`（查单、关单用；下单签名与异步通知不需要出站）。
   - `compose.alipay.yaml` 挂载密钥文件；`compose.sales.yaml` 挂载销售开关文件。
   - `compose.public.yaml` 已废弃（公网入口是宿主 nginx），不要叠加。
3. 重建 `egress` 与 `app` 容器；启动失败说明两个文件之一不合规。
4. 登录功能必须已上线，否则用户无法下单（独立于支付的前置条件）。

## 6. 上线验收（建议先把一个商品定为 0.01 元的内部版本）

1. `GET https://sagegogo.com/api/payments/webhooks/alipay` 返回 405（此前为同样结果，表示路由可达）。
2. 登录 → 价格页选择套餐 → 获取报价（摘要显示“一个月，到期不自动续费”）→ 同意条款并创建订单 → “支付宝付款” → 跳转支付宝收银台。
3. 付款后浏览器回到订单页，数秒内显示“支付成功，权益已开通”；订阅管理显示“固定期限，到期不自动续费”及服务期。
4. 点数包同样走一遍，核对 Credits 到账。
5. 支付宝商家平台核对该笔交易金额与订单一致。
6. 退款：目前没有接入在线退款入口，测试款请在支付宝商家平台手工退款，并人工处理对应权益。

## 7. 回退

把 `billing_sales.json` 中 `sales_enabled` 和 `alipay_live_enabled` 改为 `false` 并重启 `app`：停止新报价、新订单和新付款入口。若仍需接收在途订单的到账通知，请只关闭 `sales_enabled`，保留 `alipay_live_enabled: true`。

## 8. 已知限制

- 已有未到期套餐的账户可在订阅管理中手动续费同一档位（不限次数，从当前到期时间顺延）。
- 固定期限套餐暂不支持在线升级、在线退款。
- 生产环境同一订单只签发一次付款链接；用户中途关闭支付宝页面后，可在订单页取消这笔未付订单再重新下单，否则等订单超时关闭（`payment_ttl_seconds`）。
- 付款后的自动开通依赖支付宝异步通知到达 `https://sagegogo.com/api/payments/webhooks/alipay`；通知失败时支付宝会在约 25 小时内重试。主动查单不会单独生成首笔收款记录。
- 返回地址固定为中文订单页。
