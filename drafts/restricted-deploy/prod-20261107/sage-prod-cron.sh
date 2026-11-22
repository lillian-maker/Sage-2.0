#!/bin/sh
# sage-preview 生产定时任务包装：日志落 /var/log/sage-cron.log，flock 防重入。
# 用法: sage-prod-cron.sh <manage.py command> [args...]
set -u
CMD="$1"; shift
LOCK="/tmp/sage-prod-cron-${CMD}.lock"
LOG=/var/log/sage-cron.log
exec 9>"$LOCK" 2>/dev/null || exit 1
flock -n 9 || { echo "$(date -Is) SKIP $CMD (still running)" >> "$LOG"; exit 0; }
REL=/opt/sage-docker-preview/releases/current
[ -d "$REL" ] || { echo "$(date -Is) ABORT $CMD (release/current missing)" >> "$LOG"; exit 1; }
cd "$REL/drafts/restricted-deploy" || exit 1
set -a; . /opt/sage-docker-preview/deployment.env; set +a
echo "$(date -Is) RUN $CMD $*" >> "$LOG"
# docker exec 不执行 ENTRYPOINT：须按部署 README 经 entrypoint 包装注入 SAGE_SECRET_KEY
docker compose --env-file /dev/null -p sage-preview -f compose.yaml -f compose.offline.yaml -f compose.egress.yaml \
    exec -T app python /app/deploy/docker-entrypoint.py python manage.py "$CMD" "$@" >> "$LOG" 2>&1
rc=$?
echo "$(date -Is) DONE $CMD rc=$rc" >> "$LOG"
exit $rc
