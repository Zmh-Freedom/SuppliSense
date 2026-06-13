#!/bin/bash
# 一键停止脚本
# 用法: ./stop.sh
set -e

echo "⏹️  停止供应商风险分析系统..."
docker compose down
echo "✅ 已停止"
