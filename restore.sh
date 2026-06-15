#!/bin/bash
# MongoDB 数据恢复脚本
# 用法: ./restore.sh <archive_file>
set -euo pipefail

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
# 参数检查
# ============================================================
if [[ $# -lt 1 ]]; then
    echo -e "${RED}[ERROR]${RESET} 请指定备份文件路径" >&2
    echo "用法: $0 <archive_file>" >&2
    echo ""
    echo "示例: $0 backups/tianyancha_20260115.archive"
    exit 1
fi

ARCHIVE="$1"

if [[ ! -f "$ARCHIVE" ]]; then
    echo -e "${RED}[ERROR]${RESET} 备份文件不存在: ${ARCHIVE}" >&2
    exit 1
fi

# ============================================================
# 查找 MongoDB 容器
# ============================================================
CONTAINER=$(docker ps --format '{{.Names}}' | grep -E 'sra-mongo|mongodb' | head -1)
if [[ -z "$CONTAINER" ]]; then
    echo -e "${RED}[ERROR]${RESET} 未找到运行中的 MongoDB 容器" >&2
    exit 1
fi

# ============================================================
# 显示恢复信息并确认
# ============================================================
ARCHIVE_SIZE=$(stat -f%z "$ARCHIVE" 2>/dev/null || stat -c%s "$ARCHIVE" 2>/dev/null || echo "未知")

echo -e "${BOLD}${CYAN}============================================================${RESET}"
echo -e "${BOLD}${CYAN}  MongoDB 恢复工具${RESET}"
echo -e "${BOLD}${CYAN}============================================================${RESET}"
echo -e "备份文件:   ${BOLD}${ARCHIVE}${RESET}"
echo -e "文件大小:   ${ARCHIVE_SIZE} bytes"
echo -e "目标容器:   ${BOLD}${CONTAINER}${RESET}"
echo ""
echo -e "${RED}${BOLD}⚠  警告: 恢复操作将覆盖目标数据库中已存在的同名集合数据！${RESET}"
echo ""

read -r -p "$(echo -e "${YELLOW}确认恢复? 输入 YES 继续: ${RESET}")" CONFIRM

if [[ "$CONFIRM" != "YES" ]]; then
    echo -e "${YELLOW}[INFO]${RESET} 取消恢复操作"
    exit 0
fi

# ============================================================
# 执行恢复
# ============================================================
echo ""
echo -e "${YELLOW}[INFO]${RESET} 正在恢复数据 ..."

if docker exec -i "$CONTAINER" mongorestore \
    --username root \
    --password 123456 \
    --authenticationDatabase admin \
    --archive \
    --drop < "$ARCHIVE"; then
    echo ""
    echo -e "${BOLD}${GREEN}============================================================${RESET}"
    echo -e "${BOLD}${GREEN}  恢复成功${RESET}"
    echo -e "${BOLD}${GREEN}============================================================${RESET}"
else
    echo ""
    echo -e "${BOLD}${RED}============================================================${RESET}"
    echo -e "${BOLD}${RED}  恢复失败${RESET}"
    echo -e "${BOLD}${RED}============================================================${RESET}"
    exit 1
fi
