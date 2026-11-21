# 受限部署候选与迁移清单

## 2026-11-14：广州邮箱受限联调预检与回滚清单

用户选择广州邮件地域，备案通过及五个模板审核通过均为用户确认；用途、ID 和控制台收信证据见 `../auth-launch/review.md` 顶部。当前工作区有其他未提交改动，现有历史镜像、云端状态及迁移数量不能作为本次候选或目标的实时事实。本节是本地审阅准备，不授权或证明部署、迁移、开放公网或真实供应商调用。

本地 `compose.yaml` 中 app 仅连接 `frontend` 和 `backend` 两个 internal 网络；`compose.auth.yaml` 本身不提供供应商外联，也不启用认证。要从服务器调用 SES 和 CAPTCHA，需要先确认可审阅的受限出站方案与本人可访问的受限 HTTPS 入口，核对目的地和边界；不能为联通而取消数据库隔离或关闭校验。候选方案需在目标环境实测，当前尚无已批准的具体出站配置。

执行前按以下顺序形成绑定目标与证据的执行单：

1. **只读预检**：确认目标实例、预览项目、候选源码与镜像摘要；记录容器与监听、实际数据库迁移历史、网络和认证开关的非秘密状态。不读取或输出环境变量全集、凭据、用户数据或原始敏感日志。
2. **数据保护与迁移**：核对目标库与私有文件的备份，在独立获准环境恢复并验证 schema、数据完整性和角色；审阅针对实际目标的迁移计划及兼容性。历史记录称候选比旧预览多 18 项项目迁移，只能作为核对线索；实际计划以目标数据库为准。`commerce.0035` 存在保护性回退约束，不绕过。
3. **网络与配置**：经确认后配置受限 HTTPS 和 SES/CAPTCHA 所需出站；验证 app 实际出口、TLS、入口限制和数据库不对外暴露。本人通过安全入口提供 SES/CAPTCHA 凭据，核对容器可读性与最小权限，不输出值。按 `../auth-launch/review.md` 的广州 ID 配置各用途；Google、注销和手机不作为本轮邮箱前置。
4. **配置检查与真实验收**：批准的协议版本、内容和 URL 就绪后，运行 `check_auth_readiness --scope email`；结果只是配置证据。随后在受限网站逐次验证人机挑战、邮箱注册和收件、退出、密码登录、找回、旧会话撤销及密码变更通知，检查邮件显示和退信；区分平台受理与真实收件。
5. **失败与回滚**：停止新业务写入并保留现场；只有实测旧应用与现有 schema 兼容时才回退应用。数据库恢复须先验证备份可恢复且确认没有新增业务写入；有新增写入时不得用旧备份直接覆盖，先评估保留数据的方案。正式公开另行完成协议、安全与发布审批。

本地提供合成的必填镜像、网络及私有目录占位值后，执行 Compose 合并结构检查通过（`docker compose --env-file /dev/null -f drafts/restricted-deploy/compose.yaml -f drafts/restricted-deploy/compose.auth.yaml config --quiet`）；不证明凭据可读取、网络可达或服务可发送。重跑时须先提供 Compose 所需的非秘密占位值，不能把上面的命令当成无需输入即可复现。

本轮认证回归复现（在仓库根目录；测试进程覆盖数据库为内存并阻断 socket 连接，不读取真实凭据）：

```sh
env -i PATH=/usr/bin:/bin SAGE_ENV=test DJANGO_SETTINGS_MODULE=backend.settings PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B - <<'PY'
import django
from unittest.mock import patch
django.setup()
from django.db import connections
config = connections['default'].settings_dict
config['NAME'] = ':memory:'
config['TEST']['NAME'] = ':memory:'
from django.core.management import call_command
with patch('socket.socket.connect', side_effect=AssertionError('Network blocked for test')), patch('socket.create_connection', side_effect=AssertionError('Network blocked for test')):
    call_command('test', 'backend.authentication_tests.EmailAuthenticationTests', 'backend.email_tests', 'backend.auth_readiness_tests', verbosity=1)
PY
```

实际输出 `Ran 77 tests in 3.978s`、`OK`、`System check identified no issues (0 silenced).`；测试使用替身，不等于网站真实注册或收信。首次并行验证因覆盖 Django 测试数据库字典在运行前发生 `KeyError: 'MIRROR'`，改用上述方式后通过。

## 2026-10-28 既有内部预览恢复

用户授权启动 Docker 并核对内部预览。目标服务器 Docker 已启动，既有 web、app、db 三个容器均为 running/healthy；运行中的 3 个容器均属于 `sage-preview`。检查的端口中 9443 仅回环监听；未登录访问回环 HTTPS 首页通过正常证书校验并返回 401。首次其他容器计数因 Docker 过滤条件错误失败，已用全部运行容器和预览运行容器的独立计数重查，均为 3；不能将初次管道退出 0 当作计数通过。开机自启仍为 disabled，再次重启后不能依赖其自动恢复。没有运行迁移、重新部署或开放公网，认证后页面及真实服务未验证。完整记录见 `../../docs/BILLING_DEVELOPMENT.md`。

## 2026-10-27 官方摘要复核与服务器状态

Pillow本轮已从PyPI官方元数据独立复核，与两份本地wheel大小及SHA-256均一致；此前缓存来源限制已解决，证据见`../../tmp/sage-offline-build-9s1o319f/pillow-official-verification.json`。离线构建与原Dockerfile逐字复建的区别仍存在。

目标实例只读检查：实例运行，但Docker及PostgreSQL inactive/disabled，旧站服务inactive，旧Nginx服务not-found；Docker socket不可用，尚不能检查实际容器。当前旧站路径仍为历史版本。实际重启原因未确认；未经用户同意，不启动Docker（可能触发既有容器重启策略）、不迁移或公开服务。腾讯云保存了两条本轮只读检查命令及其执行记录，未修改目标服务器。


## 2026-10-27 固定依赖离线构建

已用固定基础镜像及30个本地依赖文件构建amd64应用/网页镜像。29个依赖的摘要来自本轮PyPI官方元信息；Pillow来自清华镜像，摘要匹配此前PyPI索引缓存，本轮未能实时复核官方摘要，正式部署前补核。Dockerfile只改变依赖下载为本地文件，不是原Dockerfile逐字复建。14项认证及28项凭据保护检查在本地无网络容器通过；网页镜像158文件与冻结静态构建一致。证据在`../../tmp/sage-offline-build-9s1o319f/offline-build-evidence.json`。未部署、连接真实服务或检查目标数据库。


## 2026-10-27 本地登录候选检查

当前工作区的345个白名单输入已冻结于`../../tmp/auth-candidate-20261027-y3h7bw54`并核对哈希。完整Dockerfile在下载固定依赖时长时间停滞而取消，未形成正式镜像。离线复用已有amd64测试镜像（先核对30个依赖版本与锁定清单一致），覆盖冻结backend和入口，建立只用于本地验证的镜像；只读、无网络下认证检查14项、凭据保护模拟28项通过。这个镜像没有经过完整Dockerfile复建，也没有云端部署、真实服务连接或数据库迁移，不可作为生产包。目标库迁移历史及恢复仍需实际只读核对与演练。


## 2026-10-27 邮箱优先配置检查（本地）

现有`check_auth_readiness`增加`--scope email`：先检查邮箱注册、密码登录与找回、验证码、密码变更通知和条款声明，不把Google、手机、注销或普通通知计入这一范围。默认完整检查行为不变。检查仅反映当前配置，不发送消息、不接数据库、不代表真实可达、模板获批或准许公开上线；`ready_for_public_launch`仍为false。严格模式按当前实际开关状态检查，开关仍关闭时会如实报告缺项，不能为了使检查通过而提前放行生产。

本地隔离测试：`env -i PATH=/usr/bin:/bin SAGE_ENV=test PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B manage.py test backend.auth_readiness_tests --verbosity 1`通过14项；`tests/auth-config-audit.py`合成权限与脱敏28项通过。无网络容器额外证明Google缺项时完整检查失败、邮箱范围可单独通过；仅使用合成配置。实际服务器仍待部署新版本、凭据安全配置及实测外联和受限HTTPS。

## 2026-10-27 认证安全配置入口（已本地验证，未部署）

本次增加可选的`compose.auth.yaml`，仅在明确准备好配置后追加到既有Compose命令；基础`compose.yaml`、网络、迁移服务和Google回调规则不变。原先完全关闭认证的部署不需要新增文件，也不会因此失效。

- 仅app只读挂载私有目录中的`auth_credentials.json`到`/run/secrets/auth_credentials.json`。文件由本人在安全终端编辑，不进聊天、参数、仓库或输出；私有父目录0700，文件按实际Linux容器身份设10001:10001、0400，并在目标主机核验。Compose文件挂载不是加密保险箱，也不自动修正宿主权限。
- 允许的秘密字段只有现有`SAGE_SES_SECRET_ID`、`SAGE_SES_SECRET_KEY`、`SAGE_CAPTCHA_SECRET_ID`、`SAGE_CAPTCHA_SECRET_KEY`、`SAGE_CAPTCHA_APP_SECRET_KEY`、`SAGE_GOOGLE_CLIENT_SECRET`。邮箱阶段可仅配SES；启用哪个用途就检查该用途所需字段，不强制先取得Google凭据。
- 非秘密参数沿用既有环境变量：地域、发信地址、模板编号、验证码应用编号、Google客户端编号/回调、已审条款版本/地址。通过操作者环境提供，继续`--env-file /dev/null`；不要输出完整Compose配置或进程环境。所有认证开关、条款就绪和Google品牌就绪默认仍为0。
- 入口拒绝未知字段、不可读/错误权限或owner、错误格式、空值、控制字符和超长输入；固定脱敏报错，不回显JSON解析内容。migrate/db/web不挂供应商凭据，迁移仍只获既有授权文件。

验证命令：`.venv/bin/python -B tests/auth-config-audit.py 5394ec2889b9`，复用本机已有镜像，仅挂载本次入口源码、无网络、无拉取、无真实凭据、无数据库。28项通过，涵盖兼容旧启动、按用途缺项失败、真实Linux UID与文件权限、不回显，以及注入后既有`check_auth_readiness --strict`。检查输出`configuration_ready=true`仍不等于可发布，`ready_for_public_launch=false`；外部请求0。Compose合并证明migrate/db/web和网络不变、全部门禁关闭。主会话独立重跑同一命令也通过。

后续仍需：把新入口编入经审阅候选，目标主机挂载验证和实际版本更新；在明确方案下接通供应商外联、本人可用受限HTTPS；Google回调和已完成的日志保护一起部署；本人安全填写后再跑只读就绪检查及逐项真实发送/登录。不能仅复制覆盖文件就启用真实业务，也不把本地合成通过记为生产验收。

测试维护经验：合并Compose检查迁移服务要带`--profile tools`；Django联通用的合成密钥也须满足现有至少50字符约束，不放宽应用规则来迁就测试。

## 2026-10-22 候选更新预检

本轮固定候选为 `facc7d06c911bf5b7a8968dbfb9415bdce5c7c8d`。以下10月15日云端记录为历史证据，不能代替当前服务器只读检查，也不能把历史镜像 `cfb411f21edc787f7dab1e75c6d3251287d001ed` 当作最新版本。

源码预检：当前项目迁移为50个（backend 0001–0015，commerce 0001–0035），较上述历史镜像新增18个。`commerce.0035_protect_enterprise_change_history` 含 `RunPython`：正向不改数据，反向在存在企业角色变更、名额减少/退款或下一期补买记录时拒绝回退，以保留历史。下面旧清单的“无RunPython”已不适用，也不能假设任何状态均可直接逆向迁移；实际待执行数量以目标数据库的 `migrate --plan` 为准。

服务器更新前必须形成一份绑定候选、目标库及时间的执行单：①只读核对镜像、已有迁移和服务状态；②备份目标库与私有文件，恢复到独立测试库核验；③审阅实际迁移计划和影响，再执行获批更新；④核对登录保护、业务表权限及旧站。失败时先停止新版本写入；有新业务记录时不能直接回放旧备份覆盖它们，也不能强行删除0035保护。回退采用经核实兼容的旧应用保留新表结构，或在无新增业务写入且备份恢复已通过的范围内恢复，具体路径由实际预检确定后批准。本轮只做本地合成演练，不执行云端变更。

## 独立Docker执行入口 · 2026-10-15

用户已批准Sage-2.0深色首屏、A独立三容器/新空库、保留旧宿主机Sage，首个目标为受控部署验收。先读本目录`docker-deployment-acceptance-plan-20261015.md`和`../../docs/BILLING_DEVELOPMENT.md`最新记录。Moby/Compose安装启动已获单独批准并完成保护验收；Task5的新库50项迁移、三容器、运行权限、真实回环HTTPS、重启/DB故障、独立卷恢复及入口安全轮换均通过。轮换后新TAT复验旧服务正常、仅回环9443、迁移50/Session1及其余51张业务表零行。用户确认备案尚未完成，下一步先读执行方案Task6的备案办理清单；公网继续关闭，另批确认。下方systemd/Unix socket配置保留供旧环境使用，不与新容器混装。

根Dockerfile复用原静态构建、版本锁和collectstatic；`app`与`web`目标分别生成应用及只读站点镜像，基镜像固定官方digest。Compose的长期服务是`db`、`app`、`web`；`migrate`是tools profile的一次性任务，使用迁移身份，长期app不获得迁移口令。应用UID10001、网关101、PG999；应用和PG无宿主端口，网关仅映射`127.0.0.1:9443`。默认真实认证、发送、注销和交易关闭，头像verified=0；预约和OAuth回调在预览网关拒绝，Basic入口认证始终开启。

只有web加入用于端口发布的独立edge bridge；web→app的frontend、app→db的backend均为internal网络，app/db不加入edge。Docker29实测仅internal网络的容器不会落实宿主端口映射，因此不能用三个容器healthy替代实际HTTPS验证。edge默认绑定地址也限制为127.0.0.1，最终仍检查实际端口绑定及HTTPS可达性；不关闭内部网络隔离。

web的`app`主机名通过extra_hosts绑定同一个必需的SAGE_APP_IP，与app静态frontend地址一致。并行重启时，Docker服务别名短暂无注册可能回落到宿主DNS；本机代理实测给出198.18.*并被Nginx缓存，造成应用healthy而入口502。使用已固定的准确地址消除该启动依赖；最小对照中网关先启动、应用随后启动及再次重启均恢复200，不采用重试掩盖错误路由。

### Nginx错误日志保护（2026-10-16，本地新候选）

web默认入口为`/usr/local/bin/sage-nginx-entrypoint`。原始错误输出只进入私有`/tmp`内存FIFO，固定类别过滤后才交给Docker日志保存；保留错误等级、故障原因及限定的Linux errno，不输出请求、路径、Host、Referer或上游URL。过滤器异常退出、暂停或成为僵尸时停止Nginx，不回退明文；启动及收尾异常给固定错误并非零退出。Compose健康检查也通过相同入口。没有增加运行依赖，真实OAuth回调仍403；云端旧镜像尚未更新，不能因为本地修复便启用真实Google。

容器的配置检查与重载分别使用`docker compose … exec -T web /usr/local/bin/sage-nginx-entrypoint -t`、`docker compose … exec -T web /usr/local/bin/sage-nginx-entrypoint -s reload`，省略部分沿用本项目实际已核对的Compose参数。不要在真实操作中用`--entrypoint nginx`、`exec web nginx -t`或直接重载绕过保护；测试旧版本泄漏的隔离反例另行标记。Docker自身保留接收时间，排错使用`docker logs --timestamps`；未知诊断保留“unrecognized message”提示，不以输出原文诊断。未来增加错误类别须用合成反例验证，没有“未知就直接放行”的兜底。

**执行前输入：**通过命令环境显式提供`SAGE_IMAGE_TAG`（审阅候选标识）、`SAGE_DOCKER_SECRETS_DIR`（绝对私有目录）、`SAGE_FRONTEND_SUBNET`、`SAGE_GATEWAY_IP`、`SAGE_APP_IP`（经路由和Docker网络盘点无冲突的子网及准确IP）；需要调整回环端口时提供`SAGE_HTTPS_PORT`。缺必需输入即失败，不提供示例生产凭据或默认网段。

私有目录含`django_key`、`db_admin_password`、`db_runtime_password`、`db_migrate_password`、`tls.crt`、`tls.key`、`preview_htpasswd`。真实凭据由用户安全配置，不发聊天、不读旧`.env`或私钥正文。Compose secrets是每服务只读文件挂载，不是加密保险箱；file来源不保证uid/gid/mode重映射，必须在目标Linux实测可读性。宿主目录应由管理员所有且0700，逐文件按目标容器身份设最小读权限；本地合成测试用0700父目录隔离、0444绑定文件供非root读取，不能把它当所有生产主机的权限验收。PG初始化容器本身处于数据库管理边界，需要读取两种初始角色口令；应用/迁移进程分别只获自身口令。

当前云引擎无userns remap，SELinux原先为Disabled且未更改。云端已核root:root/0700父目录：django_key为10001:10001/0400，db_admin_password为999:999/0400，两个角色口令为10001:999/0440，TLS与preview_htpasswd为101:101/0400。角色口令的owner供应用/迁移读取、group供PG初始化读取；不用0444或新增ACL依赖，每个容器仍只挂载获准文件。实际初始化、迁移及长期服务已验证对应读取能力；后续映射或安全状态漂移时停止，不能关闭校验或安全机制。

离线候选使用冻结应用源码`cfb411f21edc787f7dab1e75c6d3251287d001ed`的app/web镜像及同一官方PG16.15 amd64镜像，文件清单/摘要在`tmp/deploy-candidates/sage-docker-<源码SHA>/`。包内不含私钥、入口口令、数据库卷、媒体或备份。当前约250MB镜像包SHA256为`63cc5dfaeb03b7809a0cbc303a4b32757e143f000961d90ebfe39c22942a5a81`；外层部署包以最终manifest核验。不要从任意同名tag推定内容相同。

Docker按digest导出可能不保留可解析的registry名称。本轮已为PG创建专属离线别名，并用`compose.offline.yaml`仅把db.image绑定到同一amd64 manifest ID；本地与当前云端均实测为containerd image store。先校验候选SHA/镜像内容/平台，再load，子命令前同时指定`-f drafts/restricted-deploy/compose.yaml -f drafts/restricted-deploy/compose.offline.yaml`；启动明确`up -d --no-build --pull never`，一次性任务明确`run --rm --pull never`。不覆盖基配置、不改registry mirror、不自动回退拉取或云端构建；加载后身份不匹配则停止。本地已验证别名移除→离线load恢复、组合config及无pull的postgres --version=16.15；这不是云端加载/新库验收。

凭据初始化已由用户在终端执行：应用/DB密钥在服务器随机生成，入口口令由用户亲设，使用已实测的SHA-512 crypt；不粘贴聊天、不进入argv或日志，仅输出文件元数据和就绪布尔。用户已批准现有Sage TLS证书/私钥的受限副本，新文件101:101/0400；旧文件权限、大小及修改时间保留，不读取/打印私钥正文。

以下命令在已显式提供非敏感输入、准备私有文件并批准相应数据变更后执行；`--env-file /dev/null`防止Compose自动读取工作区`.env`：

```sh
docker compose --env-file /dev/null -p sage-preview -f drafts/restricted-deploy/compose.yaml config --quiet
docker compose --env-file /dev/null -p sage-preview -f drafts/restricted-deploy/compose.yaml build app web
```

上方build只用于本地从确定源码复建，云端离线候选禁止build/pull。首次新库初始化先用一次性db-bootstrap，不能直接让展开角色口令的失败SQL进入默认错误语句日志；以下离线路径同时指定两份配置：

```sh
docker compose --env-file /dev/null -p sage-preview -f drafts/restricted-deploy/compose.yaml -f drafts/restricted-deploy/compose.offline.yaml run -d --no-deps --pull never --name sage-preview-db-bootstrap -e 'PGOPTIONS=-c log_min_error_statement=panic -c log_statement=none -c log_duration=off -c log_min_duration_statement=-1' db
```

先确认初始化成功、最终TCP ready、目标库/两角色及日志保护生效；失败保留该容器/卷与私有日志并停止，不重试清空目录。只在成功后停止并移除准确的db-bootstrap容器（保留命名卷），再以原Compose启动常驻db，不给它或应用继承PGOPTIONS；验证常驻新会话log_min_error_statement恢复ERROR。该流程已在同镜像合成新卷及云端实际通过；云端为便于启动前核验，用等价docker create/start一次性容器、restart=no及同一批准镜像/卷/SQL/文件，上方Composerun为操作示意，不能混同已执行命令。详见最新开发记录。

以下为常驻db已就绪后的迁移检查示意；云端离线候选同样加入上述第二份配置及`--pull never`：

```sh
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


## 2026-10-16 第7项执行：国内异地备份采购准备稿

状态：官方资料与示例算账已准备，尚未采购、配置云端或执行异地恢复。前面旧部署步骤是历史阶段记录，实际部署以BILLING_DEVELOPMENT最新记录为准。现有部署记录为上海，本次未重新连接云端；因此暂拟广州作为另一地区，正式创建前核对实际主站地域。

### 建议给用户确认的方向

继续现有数据库，在广州使用COS多可用区标准存储、按量计费；普通标准存储是更便宜的备选。不为备份先迁移到托管数据库，也不预购容量包或默认开启版本控制、归档、对象锁、全球加速。新地域/存储类型/预算仍待用户确认，未改变已确认的15分钟内部回退目标、30天保存及全天严重提醒。

[PG16官方持续备份说明](https://www.postgresql.org/docs/16/continuous-archiving.html)要求可恢复的基础副本及连续变化记录，普通pg_dump不是该恢复链的基础。拟先在隔离本机验证完整副本＋持续变化记录及缺段/上传失败；目标是异地已经保存并能够恢复的数据落后不超过15分钟，不是每15分钟运行一次脚本就算通过。5分钟切段仅供本地测量，尚未配置，不承诺生产能力。

### 公开价格与预算算式

[官方定价页](https://buy.cloud.tencent.com.cn/price/cos)当前展示：标准0.118元/GB/月，多可用区标准0.15元/GB/月；标准读写各0.01元/万次，公网下载及桶间跨地域复制0.5元/GB。页面抓取未保留地域选择状态，广州正式采购时须再次核对选区价格；不是广州已取得的公司报价。[容量计费规则](https://cloud.tencent.com/document/product/436/53482)规定1024进制、按日结算日单价为月价/30、按月结算则用当月日均量计算。

下面是整个备份库的平均总存量（含全部保留副本、变化记录和上传文件），不是现有数据库大小；假设按日结算持续30天、不含促销。

| 平均总存量 | 普通标准30天 | 多可用区标准30天 | 一次经公网下载全部数据 |
|---:|---:|---:|---:|
| 10GB | 1.18元 | 1.50元 | 5元 |
| 50GB | 5.90元 | 7.50元 | 25元 |
| 100GB | 11.80元 | 15元 | 50元 |
| 435GB（压力算例） | 51.33元 | 65.25元 | 217.50元 |

100GB按日结算存满31天，多可用区空间费为15.50元；月结/实际变化量按实际账单规则。读写合计100万次约1元请求费。上述均非总账单：还需来源服务器出网、实际下载/恢复路径及额外服务费用。[COS流量说明](https://cloud.tencent.com/document/product/436/53863)区分直接异地上传与桶间跨地域复制；初期不默认先在上海存一份再复制，以免多一份空间和复制费。

PG默认每段16MB，短间隔提前切段仍占整段，不压缩且持续有写入时，5分钟一段的30天变化记录约135GB。压力算例是每天10GB完整副本留30份（300GB）再加135GB日志，共435GB；不是对Sage实际数据量或账单的预测。压缩率、实际数据/文件量、每日写入和出网收费尚未测量，不能把100GB/15元当费用上限。算账JSON和7项算术校验见`tmp/enterprise-procurement-20261016/backup-budget.json`，无需访问真实数据。

### 恢复与清理边界

数据库、头像/上传文件、批准应用版本、非敏感运行配置均要覆盖；密钥及解密/恢复材料走独立安全保管，不写入项目、聊天或普通清单。恢复后先复核最新注销、删除、停权、会话撤销和真实收退款，再恢复业务，不能由旧备份复活访问权限。

保留30天不等于对每个文件盲目设置30天删除：不能删掉仍被保留恢复点依赖的基础副本。按已批准资料期限设计并验证清理链，不静默延长保存期限；如要承诺过去精确30天任意时刻可恢复，须先确认可能需要更早基础副本的边界。当前没有采用该额外承诺或真实删除。

[版本控制](https://cloud.tencent.com/document/product/436/19884)开启后无法回到未开启状态且旧版本计费；归档/深度归档有最低保存期和恢复等待，不作为本轮默认。连续上传/缺段/冲突/磁盘积压/恢复点变旧要显式失败与报警，上传元数据不是异地恢复证据。

下一步先完成本地合成数据恢复链和容量报告，再提交具体地域、类型、费用预警及30天清理方案供用户决定；批准后才创建空间、安全配置凭据和定时备份，之后做真实异地恢复及提醒实收。B17仍2/4，50%，不因本轮纸面准备而上调。公开时间仍受真实业务/安全/公司手续验收，不在本轮填写日期。

### 本地恢复链验收补充 · 2026-10-16

已在全新、本机无网络的PostgreSQL 16.15 arm64容器完成物理基础备份、连续WAL及指定时间恢复的合成演练，15条断言通过。基础副本完成后的记录经WAL恢复，目标时间之后的记录被排除；缺少必需WAL时实际非零退出并报告`recovery ended before configured recovery target was reached`。相同归档重试成功，同名不同内容被拒绝。注入归档失败后，仅靠已经保存的副本恢复，恰好缺少那条未归档合成记录。4个测试容器和5个测试卷均清理。

本次使用手动切段，未等待5分钟自动触发；未测试长期定时运行、云端上传或报警实收。23,482,165字节基础副本和每段16MiB仅为小型合成数据库量测，不能当真实容量或套用其压缩率。完整报告、命令、脚本摘要与容量缺项在`tmp/enterprise-backup-20261016/report.md`、`run-20261016T133553Z/result.json`和`capacity-model.json`。

30天依赖检查说明：例如目标在窗口首日01:00，前一份副本完成于前日18:00、下一份在当日18:00；直接删除超过30天的文件会删掉唯一能恢复该目标的基础副本。此项是日期和依赖关系的合成检查，不是已运行30天。本期沿用用户已确认的30天保存上限，并显示完整依赖链实际可达的最早恢复时间；不默认延长副本保存，也不额外承诺精确过去30天任意时刻可恢复，不因此重问已批准的保存期限。自动清理及其恢复验证仍待实现。后续测真实容量并给具体预算，再执行获准的异地接入与恢复。B17仍2/4、50%，15分钟内部目标尚未实测验收。
