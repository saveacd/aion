#!/usr/bin/env bash
# AION 数据库备份脚本。建议放进 cron：每天 03:30 执行。
#   30 3 * * * /opt/aion/scripts/backup.sh >> /var/log/aion-backup.log 2>&1
#
# 产物：$BACKUP_DIR/aion-YYYYmmdd-HHMM.dump（pg_dump 自定义格式，需 pg_restore 还原）
# 保留：默认 14 天，可用 KEEP_DAYS 覆盖。
# 恢复：scripts/restore.sh <文件>

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/aion}"
KEEP_DAYS="${KEEP_DAYS:-14}"
DB_NAME="${POSTGRES_DB:-aion}"
DB_USER="${POSTGRES_USER:-aion}"
CONTAINER="${DB_CONTAINER:-aion-db-1}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M)"
TARGET="$BACKUP_DIR/aion-$STAMP.dump"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "[$(date -Is)] 找不到容器 $CONTAINER，备份中止" >&2
    exit 1
fi

# -Fc：自定义格式，支持并行与选择性还原
docker exec -t "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$TARGET.tmp"
mv "$TARGET.tmp" "$TARGET"

SIZE="$(du -h "$TARGET" | cut -f1)"
echo "[$(date -Is)] 备份完成 $TARGET ($SIZE)"

# 清理过期备份
find "$BACKUP_DIR" -name 'aion-*.dump' -type f -mtime "+$KEEP_DAYS" -print -delete

# 校验：确认能读取头部
if ! docker exec -i "$CONTAINER" pg_restore -l < "$TARGET" > /dev/null; then
    echo "[$(date -Is)] 警告：备份文件 $TARGET 校验失败" >&2
    exit 2
fi
echo "[$(date -Is)] 校验通过"
