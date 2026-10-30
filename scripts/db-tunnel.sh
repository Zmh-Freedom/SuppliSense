#!/usr/bin/env bash
# SSH 隧道：本地开发机 -> ECS 上的 PostgreSQL / Redis / MongoDB
# 前置条件：~/.ssh/config 中已配置 Host supplisense-db
# 用法: ./scripts/db-tunnel.sh [start|stop|status|restart]
set -euo pipefail

HOST="supplisense-db"
LOCAL_PORTS=(15432 16379 27018)

listener_pid() {
    local port="$1"
    local output pid found_pid=""

    output=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null) || return 1
    while IFS= read -r pid; do
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        if [[ -z "$found_pid" ]]; then
            found_pid="$pid"
        elif [[ "$found_pid" != "$pid" ]]; then
            return 1
        fi
    done <<< "$output"

    [[ -n "$found_pid" ]] || return 1
    printf '%s\n' "$found_pid"
}

is_expected_tunnel() {
    local pid="$1"
    local process_name command arg has_no_command=0
    local -a argv=()

    process_name=$(ps -p "$pid" -o comm= 2>/dev/null) || return 1
    process_name="${process_name##*/}"
    [[ "$process_name" == "ssh" ]] || return 1

    command=$(ps -ww -p "$pid" -o command= 2>/dev/null) || return 1
    read -r -a argv <<< "$command"
    [[ ${#argv[@]} -ge 3 ]] || return 1
    [[ "${argv[${#argv[@]}-1]}" == "$HOST" ]] || return 1

    for arg in "${argv[@]}"; do
        if [[ "$arg" == "-N" ]]; then
            has_no_command=1
            break
        fi
    done
    [[ "$has_no_command" -eq 1 ]]
}

find_tunnel_pid() {
    local port pid tunnel_pid=""

    for port in "${LOCAL_PORTS[@]}"; do
        pid=$(listener_pid "$port") || return 1
        if [[ -z "$tunnel_pid" ]]; then
            tunnel_pid="$pid"
        elif [[ "$tunnel_pid" != "$pid" ]]; then
            return 1
        fi
    done

    [[ -n "$tunnel_pid" ]] || return 1
    is_expected_tunnel "$tunnel_pid" || return 1
    printf '%s\n' "$tunnel_pid"
}

case "${1:-start}" in
    start)
        if find_tunnel_pid >/dev/null; then
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
        if tunnel_pid=$(find_tunnel_pid); then
            env kill -TERM "$tunnel_pid"
            echo "隧道已停止"
        else
            echo "隧道未运行"
        fi
        ;;
    status)
        if tunnel_pid=$(find_tunnel_pid); then
            echo "隧道运行中 (PID $tunnel_pid)"
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
