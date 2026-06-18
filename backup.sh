#!/bin/bash
# MongoDB 数据备份脚本
# 用法: ./backup.sh [--db <database_name>]
set -euo pipefail

# ============================================================
# 配置
# ============================================================
DB="tianyancha"
MONGO_USER="${MONGO_USER:-root}"
MONGO_PASS="${MONGO_PASSWORD:?请设置 MONGO_PASSWORD 环境变量}"
MONGO_AUTH_DB="${MONGO_AUTH_DB:-admin}"
RETENTION_DAYS=7

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
                echo -e "${RED}[ERROR]${RESET} --db 需要指定数据库名" >&2
                exit 1
            fi
            DB="$2"
            shift 2
            ;;
        --help|-h)
            echo "用法: $0 [--db <database_name>]"
            echo ""
            echo "选项:"
            echo "  --db <name>   指定要备份的数据库名称（默认: tianyancha）"
            echo "  -h, --help    显示帮助信息"
            exit 0
            ;;
        *)
            echo -e "${RED}[ERROR]${RESET} 未知参数: $1" >&2
            echo "用法: $0 [--db <database_name>]" >&2
            exit 1
            ;;
    esac
done

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
ARCHIVE_NAME="${DB}_${BACKUP_DATE}.archive"
ARCHIVE_PATH="${BACKUP_DIR}/${ARCHIVE_NAME}"

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
# 查找 MongoDB 容器
# ============================================================
CONTAINER=$(docker ps --format '{{.Names}}' | grep -E 'sra-mongo|mongodb' | head -1)
if [[ -z "$CONTAINER" ]]; then
    echo -e "${RED}[ERROR]${RESET} 未找到运行中的 MongoDB 容器"
    log_error "未找到运行中的 MongoDB 容器"
    exit 1
fi
echo -e "容器:       ${BOLD}${CONTAINER}${RESET}"
echo ""
log_info "使用容器: ${CONTAINER}"

# ============================================================
# 执行备份
# ============================================================
echo -e "${YELLOW}[INFO]${RESET} 正在执行 mongodump ..."

if docker exec "$CONTAINER" mongodump \
    --username "$MONGO_USER" \
    --password "$MONGO_PASS" \
    --authenticationDatabase "$MONGO_AUTH_DB" \
    --db "$DB" \
    --archive > "$ARCHIVE_PATH" 2>&1; then

    # ============================================================
    # 验证备份
    # ============================================================
    if [[ -f "$ARCHIVE_PATH" ]]; then
        ARCHIVE_SIZE=$(stat -f%z "$ARCHIVE_PATH" 2>/dev/null || stat -c%s "$ARCHIVE_PATH" 2>/dev/null || echo 0)
        if [[ "$ARCHIVE_SIZE" -gt 0 ]]; then
            echo -e "${GREEN}[SUCCESS]${RESET} 备份完成: ${BOLD}${ARCHIVE_PATH}${RESET} (${ARCHIVE_SIZE} bytes)"
            log_info "备份成功: ${ARCHIVE_PATH} (${ARCHIVE_SIZE} bytes)"
        else
            echo -e "${RED}[ERROR]${RESET} 备份文件为空，可能备份失败"
            log_error "备份文件为空: ${ARCHIVE_PATH}"
            rm -f "$ARCHIVE_PATH"
            exit 1
        fi
    else
        echo -e "${RED}[ERROR]${RESET} 备份文件未生成"
        log_error "备份文件未生成: ${ARCHIVE_PATH}"
        exit 1
    fi
else
    echo -e "${RED}[ERROR]${RESET} mongodump 执行失败"
    log_error "mongodump 执行失败"
    rm -f "$ARCHIVE_PATH"
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
