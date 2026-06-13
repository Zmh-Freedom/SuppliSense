#!/bin/bash
# 一键启动脚本（生产模式）
# 用法: ./start.sh
set -e

if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker 未运行，请先启动 Docker"
    exit 1
fi

echo "🚀 启动供应商风险分析系统..."
docker compose up -d --build
echo ""
echo "✅ 服务已启动"
echo "🌐 http://localhost"
echo "⏹️  停止: ./stop.sh"
