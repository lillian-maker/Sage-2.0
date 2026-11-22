#!/bin/sh
# sage_preview 每日逻辑备份 + 30 天保留。恢复演练按验收方案第 7 节：先恢复到独立卷验证。
set -eu
P=/opt/sage-docker-preview
DEST=$P/backups/daily
STAMP=$(date -u +%Y%m%d-%H%M%S)
FINAL="$DEST/sage_preview_$STAMP.dump"
TMP="$DEST/.sage_preview_$STAMP.dump.tmp"
LOG=/var/log/sage-cron.log
umask 077
mkdir -p "$DEST"
trap 'rm -f "$TMP"' EXIT HUP INT TERM

if ! docker exec sage-preview-db-1 pg_dump -U postgres -d sage_preview -Fc > "$TMP"; then
    echo "$(date -Is) BACKUP EXPORT FAILED $STAMP" >> "$LOG"
    exit 1
fi
if ! pg_restore -l "$TMP" >/dev/null 2>&1; then
    echo "$(date -Is) BACKUP INVALID $STAMP" >> "$LOG"
    exit 1
fi

mv "$TMP" "$FINAL"
sha256sum "$FINAL" >> "$DEST/SHA256SUMS"
# 30 天保留（按 mtime），仅在新备份已经验证并发布后执行。
find "$DEST" -name 'sage_preview_*.dump' -mtime +30 -delete
echo "$(date -Is) BACKUP OK $STAMP ($(stat -c%s "$FINAL") bytes)" >> "$LOG"
