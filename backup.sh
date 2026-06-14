#!/bin/bash
# MongoDB 数据备份脚本
set -e

BACKUP_DIR="./backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

CONTAINER=$(docker ps --format '{{.Names}}' | grep -E 'sra-mongo|mongodb' | head -1)
if [ -z "$CONTAINER" ]; then
    echo "❌ 未找到运行中的 MongoDB 容器"
    exit 1
fi

echo "📦 备份 MongoDB 到 $BACKUP_DIR ..."
docker exec "$CONTAINER" mongodump \
    --username root --password 123456 \
    --authenticationDatabase admin \
    --db tianyancha \
    --archive > "$BACKUP_DIR/tianyancha.archive"

echo "✅ 备份完成: $BACKUP_DIR/tianyancha.archive"
