#!/bin/sh
# 每日访问日志归档快照（合规：访问日志保存不少于六个月）。
# 容器 json-file 轮转上限 10m x 36（约一年以上容量）；本脚本另按月快照到
# /var/backups/sage/logs/<YYYYMM>/，双保险满足《网络安全法》六个月留存。
set -eu
MONTH=/var/backups/sage/logs/$(date -u +%Y%m)
DEST=$MONTH/$(date -u +%Y%m%d-%H%M%S)
mkdir -p "$DEST"; chmod 700 "$DEST" 2>/dev/null || true
for c in sage-preview-web-1 sage-preview-app-1 sage-preview-db-1; do
    p=$(docker inspect "$c" --format '{{.LogPath}}')
    [ -n "$p" ] && [ -f "$p" ] || { echo "$(date -Is) LOG-ARCHIVE FAILED $c" >> /var/log/sage-cron.log; exit 1; }
    for log_file in "$p" "$p".*; do
        [ -f "$log_file" ] || continue
        cp "$log_file" "$DEST/$c-$(basename "$log_file")"
        chmod 600 "$DEST/$c-$(basename "$log_file")"
    done
done
host_log_found=0
for log_file in /var/log/nginx/sagegogo-access.log* /var/log/nginx/sagegogo-error.log*; do
    [ -f "$log_file" ] || continue
    cp "$log_file" "$DEST/$(basename "$log_file")"
    chmod 600 "$DEST/$(basename "$log_file")"
    host_log_found=1
done
[ "$host_log_found" -eq 1 ] || { echo "$(date -Is) LOG-ARCHIVE FAILED host nginx logs missing" >> /var/log/sage-cron.log; exit 1; }
# 保留 24 个月
find /var/backups/sage/logs -maxdepth 1 -type d -name '20*' -mtime +730 -exec rm -rf {} \; 2>/dev/null || true
echo "$(date -Is) LOG-ARCHIVE OK $DEST" >> /var/log/sage-cron.log
