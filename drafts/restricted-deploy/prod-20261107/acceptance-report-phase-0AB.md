# Sage 官网上线验收报告 — 阶段 0/A/B（2026-11-07）

## 勘误与后续实态（2026-11-12，只读服务器核实后补记）

本节更正本报告与服务器实态不符的四点，原文保留不改：

1. 「cron wrapper 同步为 4 文件集」（原阶段 C 一节）——实际为 **3 文件集**
   （compose.yaml + compose.offline.yaml + compose.egress.yaml）。部署目录从未包含
   compose.auth.yaml / compose.logging.yaml（日志参数已并入基础 compose.yaml 与
   compose.egress.yaml）。仓库已按服务器现行版回写，逐字节一致（sha256 `f7b5fce4…`）。
2. 「cron 安装（… process_notifications@10min …）」——该行在 `/etc/cron.d/sage-prod`
   中以注释形式预留（注明「阶段E接通通知渠道后取消注释」），未按 10 分钟调度。
3. 「check_operations」cron 行未带 scope 参数（该命令要求 `--origin` / `--backup-manifest`
   / `--billing` 至少其一），即使执行位正常也恒以 rc=1 结束，巡检实际未生效。
4. （重大）三个调度脚本在 git 中为 100644；验收日的 95d2e788 release 靠手工 chmod 才能运行。
   2026-11-07 01:35 切换到本 release（2e91d5c）后执行位丢失，全部定时任务以
   Permission denied 静默失败，每日数据库备份自 11-06 后中断。2026-11-12 已在服务器
   chmod 恢复并补验（`BACKUP OK 20261112-044935 (333959 bytes)`）；仓库侧修复与防复发
   见体检提交 `bddf185`、`37ba7c5`。

## 版本锚点
- 构建锚点：`website-launch-20261106` = `2e91d5c980b75a64eb80c1e662f5eb276b644b41`（git tag，含部署架构修订）
- 镜像：`sage-app:2e91d5c…`（871MB）、`sage-web:2e91d5c…`（112MB），linux/amd64，经 digest 固定基础镜像构建
- 部署包：`sage-docker-2e91d5c…-deployment.tar.gz`（外层 SHA256 `d78748a3…`），服务器端内外层 sha256 校验全部 OK
- 首页 SHA256：`f86e056108b703fd1a265ba5289b2ce230bcc2c50517243f1e852d4be4a0a59c`
  （本地构建 = docker-audit = 服务器镜像内容 = 宿主边缘链路，四点一致）

## 本地审计证据（执行单阶段 A 前置）
- `tests/docker-audit.py`：**16/16 阶段通过**，open_findings=[]，cleanup_failures=[]
  （compose-config / linux-amd64-build / nginx 日志脱敏矩阵 19 项 / 迁移+健康 / 隔离与真实功能关闭 /
  缺输入 fail-closed / 运行时角色权限 / 静态资源缓存策略 / TLS-Basic-SNI-私有路径 / 会话-CSRF /
  代理拓扑 / flock 并发 / 头像故障矩阵 / 持久化+DB 故障恢复 / 独立卷恢复+反例控制 / OAuth 日志安全）
- `tests/gateway-redirect-audit.py`：**7/7 通过**（8444 发布口）
- 审计环境修复记录（均为机械性、已入 git）：
  1. Docker Desktop 4.74 在 macOS 26.6 崩溃循环（SIGBUS）→ 升级 4.98 解决
  2. Docker Hub 鉴权接口不可达 → daemon.json registry-mirrors（digest 校验保证内容一致）
  3. 镜像懒加载缺层 → 显式 `--platform linux/amd64` 重拉补齐
  4. OpenSSL 3.6 严格校验要求 AKI → 审计合成证书补 SKI/AKI 扩展（LibreSSL 与 OpenSSL 4.1 双环境验证）
- 部署架构修订（两次用户批准的变更）：
  - 公网入口 = 宿主 nginx 反代 `127.0.0.1:9444` → 容器 8443（compose.public.yaml 直绑与宿主 nginx 冲突，未采用）
  - 真实客户端 IP = 宿主覆写 `X-Sage-Client-IP` + 容器 realip 仅信任固定 edge 网关 172.20.0.1
    （TencentOS nginx http 模块不支持向上游发送 PROXY protocol，故改用头方案；两者安全等价：
    公网不可伪造，仅本机回环口 9443/9444 为本机信任域）
  - edge 网络 ipam 子网固定（SAGE_EDGE_SUBNET=172.20.0.0/16），与 set_real_ip_from 构成不变量对

## 阶段 A 服务器更新（2026-11-07 00:35–00:55）
- 前置：在线 pg_dump 备份（230KB，SHA256 `d9f892e7…`）→ 独立卷 `sage-preview-restore-verify-20261106`
  恢复核验：迁移数 50=50、表数 55=55、关键表校验和一致（GRANT 报错 81 项为验证容器无角色所致，预期无害）
- 迁移计划审阅：18 项（backend 0012-0015、commerce 0022-0035），**全部纯 DDL，无 RunPython/RunSQL**
- 维护窗口：stop app web → migrate（68 项落库）→ up app web；db 数据卷未触碰
- 回环验收：首页 200 + 哈希一致、health 200、/sales/ 401、www→主域 301、未知 SNI 拒绝、
  端口发布面 = 127.0.0.1:9443→8444 + 127.0.0.1:9444→8443
- 旧应用保护：shopifymas（up 6 days）、kb-local-preview(8090)、shopify.aibobod.com(443) 全部 200 无影响
- 收尾：日志轮转 max-size=10m max-file=36（≥180 天）、`systemctl enable docker docker.socket containerd`、
  cron 安装（每日备份+30 天保留 / purge_consultations / cleanup_account_deletions / cleanup_avatars /
  cleanup_auth_flows / process_notifications@10min / check_operations / 月度日志归档），
  备份脚本实测 BACKUP OK（325KB）；cron 经 `docker-entrypoint.py` 包装（exec 不执行 ENTRYPOINT 的 README 陷阱）

## 阶段 B 公网开放（2026-11-07 01:00–01:20，用户开放安全组 80/443）
| 验收项 | 结果 |
|---|---|
| 证书链外部严格校验（无 -k） | ✅ 200 / verify=0 |
| 首页内容外部哈希 | ✅ f86e0561… 四点一致 |
| http→https 301（路径+查询串保留） | ✅ |
| www→主域 301（HTTP 与 HTTPS） | ✅ |
| TLS1.2 / TLS1.3 协商 | ✅ |
| TLS1.0 / TLS1.1 拒绝 | ✅ |
| HSTS/nosniff/X-Frame-DENY/Referrer-Policy | ✅ |
| 备案号页脚展示 | ✅ 粤ICP备2026173751号 |
| 5432/8443/9443/9444/8099/8090 外部不可达 | ✅ ConnectionRefused |
| 22（SSH）可达 | ✅ 预期开放 |
| 真实 IP 传递（生产同款 conf 容器级证明） | ✅ 伪造无效 / 真实值传播 / 回退正确 |
| CSRF 拒绝手造请求 | ✅（CSRF_FAILED，防护生效） |

### 待用户确认项（非阻塞）
- 安全组疑似遗留 TCP 8000 允许规则（外部 SYN-ACK 但无应用响应；服务器无监听无 NAT）→
  建议入站仅保留 22/80/443，删除其余规则
- IPv6 未监听（vhost 无 [::]），执行单要求保持关闭 ✅

## 阶段 B 浏览器级验收（2026-11-07 01:40，真实 Chrome 1440×900 + 移动 390×844）
- 真实 Chrome 驱动（playwright-core + 本机 Chrome），全部 12 个收录页 + 移动端中英首页：
  **全部 HTTP 200，0 个 JS 控制台错误、0 个页面异常、0 个失败请求、0 个 4xx/5xx 响应**
- 懒加载滚动复核：首页 46/46、团队页 54/54 图片全部加载成功，真损坏 0
- 视觉检查（截图）：桌面首页（深色首屏/地球/品牌蓝/头像）、团队页（管理三位 CEO/CGO/CGOV、
  专家目录、UI 示例）、移动端（汉堡菜单/响应式首屏）渲染全部正确
- 交互验收：下载按钮弹出"Sage App 即将上线，敬请期待。"且 URL 不跳转（符合 2026-11-05 规格）、
  "产品"锚点滚动生效、页脚备案号链接指向 beian.miit.gov.cn、英文入口存在
- 演示视频：进视口自动加载（readyState=4，52s，缓冲 39.5s），真实播放（1440×900，currentTime 前进）

## 阶段 C 出口代理（2026-11-07 01:35，用户批准执行）
- 追加 `compose.egress.yaml`（4 文件集运行；`SAGE_EGRESS_UPSTREAM` 留空），app 重建中断约 1 分钟
- app 网络面：backend + egress_internal(内部) + frontend，**无直连外网**；egress_external 仅 egress 容器
- 验收矩阵：
  - `ses.tencentcloudapi.com` 经代理 → HTTP 200 ✅
  - `example.com` 非白名单 → Tunnel 403 Forbidden ✅
  - `oauth2.googleapis.com` → 403 拒绝（无香港上游，符合阶段 C 预期）✅
  - egress 日志仅三类腾讯云主机 + refused reason，无查询参数 ✅
- cron wrapper 同步为 4 文件集；公网站点复验 200
- 阶段 E 准备：私有目录 `/opt/sage-docker-preview/shared/auth`（0700）+ 8 键凭据模板
  （SES×2、CAPTCHA×3、Google×1、SMS×2，含填写说明与权限命令）；非秘密参数已入 deployment.env
  （SES 广州 / CAPTCHA 197617419 / Google 回调 / 条款 2026-11-05）

## 已知边界与建议
- 证书 2027-01-10 到期（TrustAsia DV），需在到期前 30 天内在腾讯云控制台重新申请并替换
  `/etc/sage/tls/` 与容器 secrets（运维手册待补）
- 建议配置腾讯云拨测告警（首页 + /health/ready）
- 阶段 C（出口代理）/ D（香港中转）/ E（凭据）/ F（登录开关）按执行单另行批准执行
