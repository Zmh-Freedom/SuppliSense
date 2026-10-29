#!/usr/bin/env bash
# SSH 隧道：本地开发机 -> ECS 上的 PostgreSQL / Redis / MongoDB
# 前置条件：~/.ssh/config 中已配置 Host supplisense-db
# 用法: ./scripts/db-tunnel.sh [start|stop|status|restart]
set -euo pipefail

HOST="supplisense-db"
LOCAL_PORTS=(15432 16379 27018)

is_running() {
    pgrep -f "ssh -f -N ${HOST}" >/dev/null 2>&1
}

case "${1:-start}" in
    start)
        if is_running; then
            echo "隧道已在运行"
            exit 0
        fi
        for port in "${LOCAL_PORTS[@]}"; do
            if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
                echo "错误: 本地端口 $port 已被占用，无法建立隧道" >&2
                exit 1
            fi
        done
        ssh -f -N "$HOST"
        echo "隧道已启动: pg=127.0.0.1:15432 redis=127.0.0.1:16379 mongo=127.0.0.1:27018"
        ;;
    stop)
        if is_running; then
            pkill -f "ssh -f -N ${HOST}"
            echo "隧道已停止"
        else
            echo "隧道未运行"
        fi
        ;;
    status)
        if is_running; then
            echo "隧道运行中"
            pgrep -af "ssh -f -N ${HOST}"
        else
            echo "隧道未运行"
            exit 1
        fi
        ;;
    restart)
        "$0" stop || true
        sleep 1
        "$0" start
        ;;
    *)
        echo "用法: $0 [start|stop|status|restart]" >&2
        exit 1
        ;;
esac