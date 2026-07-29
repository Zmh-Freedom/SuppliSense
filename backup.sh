#!/bin/bash
# MongoDB 数据备份脚本
# 用法: ./backup.sh [--db <database_name>]
set -euo pipefail

# ============================================================
# 配置
# ============================================================
DB=""
RETENTION_DAYS=7
ENV_FILE=""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="${SCRIPT_DIR}/backups"
LOG_FILE="${BACKUP_DIR}/backup.log"

# ============================================================
# ANSI 颜色
# ============================================================
if command -v tput &>/dev/null && tput setaf 1 &>/dev/null; then
    RED=$(tput setaf 1)
    GREEN=$(tput setaf 2)
    YELLOW=$(tput setaf 3)
    CYAN=$(tput setaf 6)
    BOLD=$(tput bold)
    RESET=$(tput sgr0)
else
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    RESET='\033[0m'
fi

# ============================================================
# 参数解析
# ============================================================
while [[ $# -gt 0 ]]; do
    case "$1" in
        --db)
            if [[ -z "${2:-}" ]]; then
                echo "[ERROR] --db 需要指定数据库名" >&2
                exit 1
            fi
            DB="$2"
            shift 2
            ;;
        --env-file)
            if [[ -z "${2:-}" ]]; then
                echo "[ERROR] --env-file 需要指定文件路径" >&2
                exit 1
            fi
            ENV_FILE="$2"
            shift 2
            ;;
        --help|-h)
            echo "用法: $0 [--db <database_name>] [--env-file <path>]"
            echo ""
            echo "选项:"
            echo "  --db <name>   指定要备份的数据库名称（默认: tianyancha）"
            echo "  --env-file <path>  从环境文件读取 MongoDB 连接配置"
            echo "  -h, --help    显示帮助信息"
            exit 0
            ;;
        *)
            echo "[ERROR] 未知参数: $1" >&2
            echo "用法: $0 [--db <database_name>]" >&2
            exit 1
            ;;
    esac
done

COMPOSE_CMD=(docker compose --project-directory "$SCRIPT_DIR" -f "$SCRIPT_DIR/docker-compose.yml")
if [[ -n "$ENV_FILE" ]]; then
    if [[ ! -f "$ENV_FILE" ]]; then
        echo "[ERROR] 环境文件不存在: $ENV_FILE" >&2
        exit 1
    fi
    COMPOSE_CMD+=(--env-file "$ENV_FILE")
fi

# ============================================================
# 日志函数
# ============================================================
log() {
    local level="$1"
    shift
    local msg="$*"
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[${timestamp}] [${level}] ${msg}" | tee -a "$LOG_FILE"
}

log_info()  { log "INFO" "$@"; }
log_warn()  { log "WARN" "$@"; }
log_error() { log "ERROR" "$@"; }

# ============================================================
# 初始化
# ============================================================
mkdir -p "$BACKUP_DIR"

BACKUP_DATE="$(date +%Y%m%d)"
if [[ -z "$DB" ]]; then
    DB="$("${COMPOSE_CMD[@]}" exec -T mongo sh -c 'printf "%s" "${MONGO_INITDB_DATABASE:-tianyancha}"')"
fi
ARCHIVE_NAME="${DB}_${BACKUP_DATE}.archive"
ARCHIVE_PATH="${BACKUP_DIR}/${ARCHIVE_NAME}"
DIAGNOSTIC_LOG="${BACKUP_DIR}/${DB}_${BACKUP_DATE}.mongodump-error.log"

echo -e "${BOLD}${CYAN}============================================================${RESET}"
echo -e "${BOLD}${CYAN}  MongoDB 备份工具${RESET}"
echo -e "${BOLD}${CYAN}============================================================${RESET}"
echo -e "数据库:     ${BOLD}${DB}${RESET}"
echo -e "备份目录:   ${BACKUP_DIR}"
echo -e "备份文件:   ${ARCHIVE_NAME}"
echo -e "保留天数:   ${RETENTION_DAYS}"
echo ""

log_info "开始备份数据库: ${DB}"

# ============================================================
# 执行备份
# ============================================================
echo -e "${YELLOW}[INFO]${RESET} 正在执行 mongodump ..."

TEMP_ARCHIVE="$(mktemp "${BACKUP_DIR}/.${ARCHIVE_NAME}.XXXXXX")"
DUMP_ERROR_LOG="$(mktemp "${BACKUP_DIR}/.mongodump.XXXXXX.log")"

if "${COMPOSE_CMD[@]}" exec -T mongo sh -s -- "$DB" > "$TEMP_ARCHIVE" 2> "$DUMP_ERROR_LOG" <<'EOF'
set -eu
umask 077
config_file="$(mktemp)"
cleanup() {
    rm -f "$config_file"
}
trap cleanup EXIT
escaped_password="$(printf '%s' "$MONGO_INITDB_ROOT_PASSWORD" | sed 's/\\/\\\\/g; s/"/\\"/g')"
printf 'password: "%s"\n' "$escaped_password" > "$config_file"
mongodump \
    --config "$config_file" \
    --username "${MONGO_INITDB_ROOT_USERNAME:-root}" \
    --authenticationDatabase admin \
    --db "$1" \
    --archive
EOF
then

    # ============================================================
    # 验证备份
    # ============================================================
    if [[ -f "$TEMP_ARCHIVE" ]]; then
        ARCHIVE_SIZE=$(stat -f%z "$TEMP_ARCHIVE" 2>/dev/null || stat -c%s "$TEMP_ARCHIVE" 2>/dev/null || echo 0)
        if [[ "$ARCHIVE_SIZE" -gt 0 ]]; then
            mv "$TEMP_ARCHIVE" "$ARCHIVE_PATH"
            rm -f "$DUMP_ERROR_LOG"
            echo -e "${GREEN}[SUCCESS]${RESET} 备份完成: ${BOLD}${ARCHIVE_PATH}${RESET} (${ARCHIVE_SIZE} bytes)"
            log_info "备份成功: ${ARCHIVE_PATH} (${ARCHIVE_SIZE} bytes)"
        else
            echo -e "${RED}[ERROR]${RESET} 备份文件为空，可能备份失败"
            log_error "备份文件为空: ${ARCHIVE_PATH}"
            rm -f "$TEMP_ARCHIVE" "$DUMP_ERROR_LOG"
            exit 1
        fi
    else
        echo -e "${RED}[ERROR]${RESET} 备份文件未生成"
        log_error "备份文件未生成: ${ARCHIVE_PATH}"
        rm -f "$DUMP_ERROR_LOG"
        exit 1
    fi
else
    echo -e "${RED}[ERROR]${RESET} mongodump 执行失败"
    rm -f "$TEMP_ARCHIVE"
    mv "$DUMP_ERROR_LOG" "$DIAGNOSTIC_LOG"
    chmod 600 "$DIAGNOSTIC_LOG"
    log_error "mongodump 执行失败；诊断日志: ${DIAGNOSTIC_LOG}"
    exit 1
fi

# ============================================================
# 保留策略：只保留最近 N 天的备份
# ============================================================
echo ""
echo -e "${YELLOW}[INFO]${RESET} 清理过期备份（保留最近 ${RETENTION_DAYS} 天）..."

CLEANED_COUNT=0
for archive in "$BACKUP_DIR"/*.archive; do
    # 跳过不存在的文件（glob 展开为空时）
    [[ -f "$archive" ]] || continue

    BASENAME=$(basename "$archive")
    # 提取文件名中的日期部分 (格式: DB_YYYYMMDD.archive)
    FILE_DATE=$(echo "$BASENAME" | grep -oE '[0-9]{8}' | head -1)
    if [[ -z "$FILE_DATE" ]]; then
        log_warn "无法解析文件日期，跳过: ${BASENAME}"
        continue
    fi

    # 计算文件年龄（天数）
    FILE_EPOCH=$(date -j -f "%Y%m%d" "$FILE_DATE" "+%s" 2>/dev/null || date -d "$FILE_DATE" "+%s" 2>/dev/null)
    NOW_EPOCH=$(date "+%s")
    if [[ -z "$FILE_EPOCH" ]]; then
        log_warn "无法计算文件时间，跳过: ${BASENAME}"
        continue
    fi

    AGE_DAYS=$(( (NOW_EPOCH - FILE_EPOCH) / 86400 ))

    if [[ "$AGE_DAYS" -gt "$RETENTION_DAYS" ]]; then
        echo -e "  ${RED}删除${RESET} ${BASENAME} (${AGE_DAYS} 天前)"
        rm -f "$archive"
        log_info "删除过期备份: ${BASENAME} (${AGE_DAYS} 天前)"
        ((CLEANED_COUNT++)) || true
    fi
done

if [[ "$CLEANED_COUNT" -eq 0 ]]; then
    echo -e "  ${GREEN}无需清理${RESET}"
fi

# ============================================================
# 完成
# ============================================================
echo ""
echo -e "${BOLD}${GREEN}============================================================${RESET}"
echo -e "${BOLD}${GREEN}  备份任务完成${RESET}"
echo -e "${BOLD}${GREEN}============================================================${RESET}"

# 列出当前所有备份
echo ""
echo -e "${CYAN}当前备份列表:${RESET}"
ls -lh "$BACKUP_DIR"/*.archive 2>/dev/null || echo "  (无备份文件)"

log_info "备份任务结束"
