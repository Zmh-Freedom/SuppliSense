#!/bin/bash
# 一键停止脚本
# 用法: ./stop.sh [--dev]
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ "${1:-}" == "--dev" ]]; then
    ENV_FILE="$SCRIPT_DIR/backend/.env"
    COMPOSE_FILE="$SCRIPT_DIR/docker-compose.dev.yml"
else
    ENV_FILE="$SCRIPT_DIR/.env.docker"
    COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
fi
COMPOSE_CMD=(docker compose --project-directory "$SCRIPT_DIR" -f "$COMPOSE_FILE" --env-file "$ENV_FILE")

if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ 缺少环境文件: $ENV_FILE" >&2
    echo "请补充配置后再停止服务（不会输出密钥内容）" >&2
    exit 1
fi

if [[ "${1:-}" == "--dev" ]]; then
    echo "⏹️  停止开发基础设施..."
else
    echo "⏹️  停止供应商风险分析系统..."
fi
"${COMPOSE_CMD[@]}" down
echo "✅ 已停止"
