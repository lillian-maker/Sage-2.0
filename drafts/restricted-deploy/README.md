# 受限部署候选与迁移清单

## 独立Docker执行入口 · 2026-10-15

用户已批准Sage-2.0深色首屏、A独立三容器/新空库、保留旧宿主机Sage，首个目标为受控部署验收。先读本目录`docker-deployment-acceptance-plan-20261015.md`和`../../docs/BILLING_DEVELOPMENT.md`最新记录。下方systemd/Unix socket配置保留供旧环境使用，不与新容器混装。服务器安装、云端初始化和公网放行仍需具体批次确认。

根Dockerfile复用原静态构建、版本锁和collectstatic；`app`与`web`目标分别生成应用及只读站点镜像，基镜像固定官方digest。Compose的长期服务是`db`、`app`、`web`；`migrate`是tools profile的一次性任务，使用迁移身份，长期app不获得迁移口令。应用UID10001、网关101、PG999；应用和PG无宿主端口，网关仅映射`127.0.0.1:9443`。默认真实认证、发送、注销和交易关闭，头像verified=0；预约和OAuth回调在预览网关拒绝，Basic入口认证始终开启。

只有web加入用于端口发布的独立edge bridge；web→app的frontend、app→db的backend均为internal网络，app/db不加入edge。Docker29实测仅internal网络的容器不会落实宿主端口映射，因此不能用三个容器healthy替代实际HTTPS验证。edge默认绑定地址也限制为127.0.0.1，最终仍检查实际端口绑定及HTTPS可达性；不关闭内部网络隔离。

web的`app`主机名通过extra_hosts绑定同一个必需的SAGE_APP_IP，与app静态frontend地址一致。并行重启时，Docker服务别名短暂无注册可能回落到宿主DNS；本机代理实测给出198.18.*并被Nginx缓存，造成应用healthy而入口502。使用已固定的准确地址消除该启动依赖；最小对照中网关先启动、应用随后启动及再次重启均恢复200，不采用重试掩盖错误路由。

**执行前输入：**通过命令环境显式提供`SAGE_IMAGE_TAG`（审阅候选标识）、`SAGE_DOCKER_SECRETS_DIR`（绝对私有目录）、`SAGE_FRONTEND_SUBNET`、`SAGE_GATEWAY_IP`、`SAGE_APP_IP`（经路由和Docker网络盘点无冲突的子网及准确IP）；需要调整回环端口时提供`SAGE_HTTPS_PORT`。缺必需输入即失败，不提供示例生产凭据或默认网段。

私有目录含`django_key`、`db_admin_password`、`db_runtime_password`、`db_migrate_password`、`tls.crt`、`tls.key`、`preview_htpasswd`。真实凭据由用户安全配置，不发聊天、不读旧`.env`或私钥正文。Compose secrets是每服务只读文件挂载，不是加密保险箱；file来源不保证uid/gid/mode重映射，必须在目标Linux实测可读性。宿主目录应由管理员所有且0700，逐文件按目标容器身份设最小读权限；本地合成测试用0700父目录隔离、0444绑定文件供非root读取，不能把它当所有生产主机的权限验收。PG初始化容器本身处于数据库管理边界，需要读取两种初始角色口令；应用/迁移进程分别只获自身口令。

以下命令在已显式提供非敏感输入、准备私有文件并批准相应数据变更后执行；`--env-file /dev/null`防止Compose自动读取工作区`.env`：

```sh
docker compose --env-file /dev/null -p sage-preview -f drafts/restricted-deploy/compose.yaml config --quiet
docker compose --env-file /dev/null -p sage-preview -f drafts/restricted-deploy/compose.yaml build app web
docker compose --env-file /dev/null -p sage-preview -f drafts/restricted-deploy/compose.yaml up -d db
docker compose --env-file /dev/null -p sage-preview -f drafts/restricted-deploy/compose.yaml run --rm migrate python manage.py check --deploy
docker compose --env-file /dev/null -p sage-preview -f drafts/restricted-deploy/compose.yaml run --rm migrate python manage.py migrate --plan
```

确认准确目标新库、迁移计划与恢复证据后，一次性`run --rm migrate`应用迁移，再`up -d app web`。不能用启动失败自动迁移/清库；不能把app切换成owner绕过权限。`check --deploy`保留邮件/销售关闭等Warning，不虚报商业或供应商就绪。镜像为linux/amd64；本机arm64的仿真验证须与上海x86_64原生验收区分。

`docker exec`不会执行镜像ENTRYPOINT，也不会继承主进程启动后注入的环境。需要Django配置的运行身份管理检查应使用`docker compose --env-file /dev/null -p sage-preview -f drafts/restricted-deploy/compose.yaml exec -T app python /app/deploy/docker-entrypoint.py python manage.py check`；不要直接exec `manage.py`，不要读取`/proc`环境或把口令放进命令参数。迁移身份继续使用上方`run --rm migrate`，自然经过同一密钥加载入口。

本地合成验收入口为`.venv/bin/python -B tests/docker-audit.py`，要求TLS1.3客户端能力（本轮为Python3.12/OpenSSL3.5.8）；系统Python3.9/LibreSSL2.8.3不能执行这项协议矩阵，脚本会在创建资源前失败。脚本独占project、空卷、临时CA和随机测试凭据，明确不读实际部署凭据；测试新库和网关，输出脱敏摘要，结束仅清理本次资源。测试不连接云端或真实供应商，不代替独立公网来源/撤销或正式业务验收。初始CPU/内存/PID/日志上限是受控验收起点，不能当容量承诺。

停止新项目使用准确`-p sage-preview -f drafts/restricted-deploy/compose.yaml stop`，保留卷和失败现场；不执行全局prune、`down -v`或批量停止其他应用。正式升级先审阅候选、备份恢复、兼容性与回退；不能只凭镜像tag认定来源一致。

> 2026-10-13说明：本目录保留原配置准备清单，正文“当前没有上传/下一批待批准”等属于起草阶段，不是服务器实时状态；以`../../docs/BILLING_DEVELOPMENT.md`最新服务器记录为准。本次GitHub代码同步不执行这里的安装、迁移、配置或开放步骤，也不包含实际密钥。

本目录是配置准备稿，不代表云端已安装或网站已上线。接续先读 `../../docs/BILLING_DEVELOPMENT.md` 的最新部署记录。业务源码和既有未提交变更均保留；不以 Git HEAD 代替包含未跟踪文件的实际候选版本。

## 本轮交付边界

- 在独立副本构建官网和管理静态资源，固定源码与产物 SHA-256；不在原工作区重新生成页面。
- 运行包只包含后端、迁移、模板、依赖锁、`manage.py`、`dist/`、构建生成的 `tmp/admin-static/`、运行所需 `src/pages/billing-catalog.json` 及本目录配置草案。
- 不携带 `.env`、密钥、证书、现有数据库/备份/媒体/日志、`.git`、虚拟环境或浏览器资料；不上传服务器、执行业务迁移、启动网站、修改安全组或调用供应商。
- 完整源码副本另留在本地候选目录供审阅；候选包在 `tmp/deploy-candidates/`，不提交二进制包，不擅自 commit/push。候选归档哈希见最新开发记录。

## 配置与权限

| 项目 | 草案 | 启用前验证 |
|---|---|---|
| 代码 | `/opt/sage/releases/<候选摘要>/`，`/opt/sage/current` 指向审核版本；root所有，应用不可写 | 解包清单无越界或链接；逐文件摘要一致；只提升批准的候选 |
| Python | 已安装 `/opt/sage/venv`，依赖来自锁文件 | 比对锁文件摘要、Python和包版本；不把macOS虚拟环境上传Linux |
| 数据库 | `sage_staging`；`/var/run/postgresql` peer；应用 `sage`，迁移 `sage_migrate` | OS身份、PG身份和库名一致；运行账号无DDL/切换角色权限 |
| 进程 | `sage-staging.service`；2个sync worker仅作验收起点，不是容量结论 | 主机systemd兼容性、可写路径、重启和退出；无待迁移时才允许启动 |
| HTTPS | `nginx-staging.conf` 仅 `127.0.0.1:8443`，不开放80/443 | 正式域名证书、准确Host与Origin、受限访问通道另行批准；先检查全部Nginx配置，排除发行版默认公网监听 |
| 私有头像 | `/var/lib/sage/avatars`，sage: sage 0700，不经Nginx直接公开 | 真实文件锁/原子写/失败恢复实测后才设置flock已验证标志 |
| 密钥 | 样例不含 `SAGE_SECRET_KEY`；真实配置root:root 0600 | 管理员安全配置，不粘贴聊天，不读取/打印配置内容；缺少密钥必须拒绝启动 |
| 功能 | 邮件/短信/Google/注册/验证码/删除等开关均关，真实支付保持代码门禁 | 每项独立验收和批准后启用，不因基础部署完成自动放行 |

Gunicorn通过仅信任`127.0.0.1`的`X-Forwarded-Proto`设置WSGI协议，Nginx覆盖该头为实际`$scheme`；不用关闭SSL重定向来绕过HTTPS问题。后端需要真实socket对端IP，因此应用使用回环TCP而非Unix socket；PostgreSQL仍仅Unix socket。这遵循[Gunicorn 23配置定义](https://raw.githubusercontent.com/benoitc/gunicorn/23.0.0/gunicorn/config.py)、[部署说明](https://gunicorn.org/deploy/)和[Nginx代理头规则](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header)。不授权将这份回环模板直接改成公网监听。

`EnvironmentFile`由systemd管理器读取后传给服务进程，迁移时的账号覆盖必须放在最终命令`/usr/bin/env SAGE_DB_USER=sage_migrate …`中，不能假设`Environment=`优先于文件中的同名值；参见[systemd配置说明源文档](https://raw.githubusercontent.com/systemd/systemd/v252/man/systemd.exec.xml)。迁移身份不因此获得读取所有运行凭据的文件权限；后续受限一次性任务只注入必需变量，不复用供应商密钥。

访问日志不记查询串、IP、用户标识或Referer。Nginx错误日志仍可能带原始请求（包括OAuth查询串）；启用真实OAuth前须补齐错误日志脱敏和保留方案，不能把本模板宣称为全部日志无敏感信息。也不关闭错误日志来隐藏问题。

## 迁移盘点与下一批门禁

当前源码包含项目迁移32个：`backend` 0001–0011，`commerce` 0001–0021；加上Django内置迁移共50个。项目迁移为模型、字段与约束操作，无`RunPython`/`RunSQL`或自定义函数；不需要为迁移账号增加superuser/CREATEDB/CREATEROLE。正式顺序以锁定副本的迁移依赖图为准，不按文件名跨应用排序。

本地离线迁移图不是云端`sage_staging`的实际执行计划；只有对准确目标库执行`migrate --plan`才能确定当前待执行项。未来迁移的批准对象必须绑定候选包摘要、目标库、备份编号和当时的计划。

1. **版本入库与只读预检（下一批待批准）**：上传指定候选到独立release目录，逐文件校验；不覆盖current；安全提供仅迁移所需环境，用`sage_migrate`执行`check --deploy`、`migrate --plan`，核对库名/身份/迁移历史。当前没有上传。
2. **备份与恢复演练**：管理员在root专用备份目录保存目标库custom dump、全局角色/ACL的受限备份；后者按敏感文件管理，不输出聊天、不进入部署包。记录文件哈希。恢复到另一个已批准的临时库，核对schema/行数/角色/默认权限；不能用“导出成功”代替恢复验证。本轮没有创建备份或恢复库。
3. **单独确认迁移**：提交实际`migrate --plan`、备份恢复证据和维护窗口后，获准才以`sage_migrate`运行`migrate --noinput`。不创建管理员/真实用户，不发送邮件、不收款。
4. **验收权限与就绪**：同一角色运行`showmigrations --plan`、`migrate --check`；以`sage`测试必要DML和会话读写、DDL拒绝、非授权连接拒绝；交易性探针回滚。验证全部新表与序列的默认权限实际生效。依赖图通过不等于这些实测通过。
5. **另批启动内部站点**：确认密钥、证书和访问方式后才安装service/Nginx，`systemd-analyze verify`、`nginx -t`及完整`nginx -T`审阅（不把输出直接发聊天），按已核实配置启动。验证HTTPS无循环、Host拒绝、伪造代理头、Cookie/CSRF、私有媒体鉴权、健康检查、双语与移动端；不enable或开放入站。

失败时停止新应用并保持入站拒绝，保留候选、数据库与失败现场。首次迁移若失败，不通过清空数据目录或自动逆迁移重试；检查部分已应用迁移后决定续跑或恢复。恢复到不同批准库验证，再由人工确认切换；已有密码变更、注销和停权不能依赖简单逆迁移撤销。后续正式放行仍需备份调度、告警、真实供应商与完整业务验收。

部署检查遵循[Django 5.2上线检查清单](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)。`check --deploy`无Error不等于可公开上线；邮件/收款关闭、未覆盖全部子域HSTS等Warning须逐项记录，不删除或隐藏检查项。
