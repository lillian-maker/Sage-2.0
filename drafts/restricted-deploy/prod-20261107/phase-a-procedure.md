# 阶段 A 服务器更新操作单（2026-11-07，操作者按序执行）
# 锚点：website-launch-20261106 = 2e91d5c980b75a64eb80c1e662f5eb276b644b41
#   （勘误 2026-11-12：初稿误写 95d2e788/13 组——那是边缘方案修订前的锚点；
#    实际执行锚点 = 2e91d5c、docker-audit 16/16 全过，见同目录 acceptance-report-phase-0AB.md）
# 前置（已完成）：本地 docker-audit.py 16 组全过；pg_dump 备份 + 独立卷恢复核验通过
# 禁止动作：全局 prune、down -v、批量停容器；只操作 -p sage-preview 本项目

# 0. 上传部署包（本地完成后）
#    sage-docker-<SHA>-deployment.tar.gz → /opt/sage-docker-preview/incoming/

# 1. 建新 release 目录并解包（沿用 cfb411f 的 releases/<SHA>/ 结构）
#    RELEASE=/opt/sage-docker-preview/releases/<SHA>
#    mkdir -p $RELEASE && tar -xzf incoming/sage-docker-<SHA>-deployment.tar.gz -C $RELEASE
#    docker load -i $RELEASE/images-linux-amd64.tar.gz   （镜像 sage-app/sage-web:<SHA>）

# 2. 更新 /opt/sage-docker-preview/deployment-inputs.json 与 deployment.env：
#    SAGE_IMAGE_TAG=<SHA>；SAGE_TRUSTED_PROXY_IPS 保持 ${SAGE_GATEWAY_IP}
#    （app 只信任 web 容器 10.231.216.2；真实客户端 IP 由宿主 nginx 写入
#     X-Sage-Client-IP，web nginx 仅信任固定 edge 网关 172.20.0.1）
#    新增 SAGE_EDGE_SUBNET=172.20.0.0/16
#    SAGE_PUBLIC_HTTPS_PORT=9444（宿主 nginx 的 proxy_pass 端口，按需调整时同步更新 vhost）

# 3. 迁移计划审阅（不落库）：
#    docker compose --env-file /dev/null -p sage-preview \
#      -f $RELEASE/drafts/restricted-deploy/compose.yaml \
#      -f $RELEASE/drafts/restricted-deploy/compose.offline.yaml \
#      run --rm migrate python manage.py migrate --plan
#    记录到 precheck-20261106/migration-plan.txt；出现 RunPython/RunSQL 则暂停上报

# 4. 维护窗口（app/web 中断约 1-3 分钟；db 保持运行）：
#    4.1 docker compose ... stop app web          （只停本项目两个服务）
#    4.2 docker compose ... run --rm migrate python manage.py migrate --noinput
#    4.3 docker compose ... up -d app web         （--pull never --no-build 语义由
#        compose.offline.yaml + 本地镜像保证；不执行 build/pull）
#    4.4 ln -sfn <SHA> /opt/sage-docker-preview/releases/current

# 5. 回环验证（SNI=sagegogo.com 走 9443→8444 运维口）：
#    - 首页 200 且与新构建 dist 首页 SHA256 一致
#    - /health/ready 200；/api/ 咨询接口可达；/sales/ 仍 401（口令第二道门）
#    - 无口令不再拦截首页（公开化生效）
#    - 数据完整：django_migrations 数量 = 50 + 新增项；业务表行数与备份时一致
#    - shopifymas(:8099)、kb-local-preview(:8090)、shopify.aibobod.com 仍 200（旧应用保护）

# 6. 日志留存落地（收尾项）：
#    - compose 日志上限调整为 max-size=10m, max-file=36（override 文件随 release）
#    - 月度归档 + 每日备份 cron 安装（见 prod-20261107/*.sh）
#    - systemctl enable docker docker.socket containerd（重启自愈，生产必需）

# 回滚：按验收方案第 7 节；旧镜像 cfb411f 仍在本地（回退 = 换回 SAGE_IMAGE_TAG 重建容器）；
# 数据库出现新写入时不得直接用旧备份覆盖。
