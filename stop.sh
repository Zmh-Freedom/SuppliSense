#!/bin/bash
# 一键停止脚本
# 用法: ./stop.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env.docker"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ 缺少环境文件: $ENV_FILE"
    echo "请从 .env.docker.example 复制并填写配置后再停止服务"
    exit 1
fi

echo "⏹️  停止供应商风险分析系统..."
docker compose --env-file "$ENV_FILE" down
echo "✅ 已停止"
