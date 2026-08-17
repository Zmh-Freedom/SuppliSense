#!/bin/bash
# 一键停止脚本
# 用法: ./stop.sh [--dev]
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

require_env_values() {
    local env_file="$1"
    shift
    local key

    if [[ ! -f "$env_file" ]]; then
        echo "❌ 缺少环境文件: $env_file" >&2
        echo "请补充配置后再停止服务（不会输出密钥内容）" >&2
        exit 1
    fi
    for key in "$@"; do
        if ! grep -Eq "^${key}=[^[:space:]]" "$env_file"; then
            echo "❌ 环境文件缺少必填配置: ${key}" >&2
            echo "请填写配置后再停止服务（不会输出密钥内容）" >&2
            exit 1
        fi
    done
}

if [[ "${1:-}" == "--dev" ]]; then
    ENV_FILE="$SCRIPT_DIR/backend/.env"
    COMPOSE_FILE="$SCRIPT_DIR/docker-compose.dev.yml"
    require_env_values "$ENV_FILE" PG_PASSWORD MONGO_PASSWORD
else
    ENV_FILE="$SCRIPT_DIR/.env.docker"
    COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
    if [[ ! -f "$ENV_FILE" ]]; then
        echo "❌ 缺少环境文件: $ENV_FILE" >&2
        echo "请补充配置后再停止服务（不会输出密钥内容）" >&2
        exit 1
    fi
fi
COMPOSE_CMD=(docker compose --project-directory "$SCRIPT_DIR" -f "$COMPOSE_FILE" --env-file "$ENV_FILE")

if [[ "${1:-}" == "--dev" ]]; then
    echo "⏹️  停止开发基础设施..."
else
    echo "⏹️  停止供应商风险分析系统..."
fi
"${COMPOSE_CMD[@]}" down
echo "✅ 已停止"
