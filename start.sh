#!/bin/bash
# 一键启动脚本
# 用法: ./start.sh [--dev]
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

require_env_values() {
    local env_file="$1"
    shift
    local key

    if [[ ! -f "$env_file" ]]; then
        echo "❌ 缺少环境文件: $env_file" >&2
        exit 1
    fi
    for key in "$@"; do
        if ! grep -Eq "^${key}=[^[:space:]]" "$env_file"; then
            echo "❌ 环境文件缺少必填配置: ${key}" >&2
            echo "请填写配置后再启动（不会输出密钥内容）" >&2
            exit 1
        fi
    done
}

MODE="production"
if [[ "${1:-}" == "--dev" ]]; then
    MODE="development"
fi

if [[ "$MODE" == "development" ]]; then
    ENV_FILE="$SCRIPT_DIR/backend/.env"
    COMPOSE_FILE="$SCRIPT_DIR/docker-compose.dev.yml"
    require_env_values "$ENV_FILE" PG_PASSWORD MONGO_PASSWORD
else
    ENV_FILE="$SCRIPT_DIR/.env.docker"
    COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
    require_env_values "$ENV_FILE" PG_PASSWORD MONGO_PASSWORD REDIS_PASSWORD
fi

COMPOSE_CMD=(docker compose --project-directory "$SCRIPT_DIR" -f "$COMPOSE_FILE" --env-file "$ENV_FILE")

if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker 未运行，请先启动 Docker"
    exit 1
fi

echo "🔍 验证 Compose 配置..."
"${COMPOSE_CMD[@]}" config --quiet

if [[ "$MODE" == "production" ]]; then
    echo "📦 检查是否需要备份..."
    if ! bash "$SCRIPT_DIR/backup.sh" --env-file "$ENV_FILE"; then
        echo "⚠️  备份失败，继续启动服务。请检查备份日志。" >&2
    fi
    echo "🚀 启动供应商风险分析系统..."
    "${COMPOSE_CMD[@]}" up -d --build
else
    echo "🚀 启动开发基础设施（MongoDB、PostgreSQL、Redis）..."
    "${COMPOSE_CMD[@]}" up -d mongo postgres redis
fi
echo ""
echo "✅ 服务已启动"
if [[ "$MODE" == "production" ]]; then
    echo "🌐 http://localhost"
else
    echo "🌐 后端: http://localhost:8000，前端: http://localhost:5173"
fi
echo "⏹️  停止: ./stop.sh"
