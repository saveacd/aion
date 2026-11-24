#!/usr/bin/env bash
# 从 scripts/backup.sh 产出的 dump 文件恢复 AION 数据库。
# 用法：scripts/restore.sh /var/backups/aion/aion-20260101-0330.dump
#
# 这是破坏性操作：会先删除并重建目标库。执行前请确认后端已停止。

set -euo pipefail

FILE="${1:?用法: restore.sh <dump 文件>}"
DB_NAME="${POSTGRES_DB:-aion}"
DB_USER="${POSTGRES_USER:-aion}"
CONTAINER="${DB_CONTAINER:-aion-db-1}"

[ -f "$FILE" ] || { echo "文件不存在: $FILE" >&2; exit 1; }

read -r -p "即将覆盖数据库 $DB_NAME，输入 RESTORE 确认: " CONFIRM
[ "$CONFIRM" = "RESTORE" ] || { echo "已取消"; exit 1; }

docker exec -i "$CONTAINER" pg_restore -l < "$FILE" > /dev/null

docker exec -i "$CONTAINER" psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $DB_NAME;"
docker exec -i "$CONTAINER" psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"

docker exec -i "$CONTAINER" pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner < "$FILE"
echo "恢复完成。请启动后端并检查 /readyz 与 show-status。"
