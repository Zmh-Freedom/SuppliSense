#!/bin/bash
# 一键启动脚本（生产模式）
# 用法: ./start.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env.docker"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ 缺少环境文件: $ENV_FILE"
    echo "请从 .env.docker.example 复制并填写配置后再启动"
    exit 1
fi

if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker 未运行，请先启动 Docker"
    exit 1
fi

echo "🔍 验证 Compose 配置..."
docker compose --env-file "$ENV_FILE" config --quiet

echo "📦 检查是否需要备份..."
if ! bash "$SCRIPT_DIR/backup.sh" --env-file "$ENV_FILE"; then
    echo "⚠️  备份失败，继续启动服务。请检查备份日志。" >&2
fi

echo "🚀 启动供应商风险分析系统..."
docker compose --env-file "$ENV_FILE" up -d --build
echo ""
echo "✅ 服务已启动"
echo "🌐 http://localhost"
echo "⏹️  停止: ./stop.sh"
