#!/bin/bash

# 一键停止脚本
# 用法: ./stop.sh

echo "⏹️  停止供应商风险分析系统..."
echo ""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 停止后端
if [ -f ".backend.pid" ]; then
    BACKEND_PID=$(cat .backend.pid)
    if kill -0 $BACKEND_PID 2>/dev/null; then
        kill $BACKEND_PID
        echo -e "${GREEN}✓ 后端已停止 (PID: $BACKEND_PID)${NC}"
    else
        echo -e "${YELLOW}⚠ 后端进程不存在 (PID: $BACKEND_PID)${NC}"
    fi
    rm .backend.pid
else
    echo -e "${YELLOW}⚠ 后端 PID 文件不存在${NC}"
fi

# 停止前端
if [ -f ".frontend.pid" ]; then
    FRONTEND_PID=$(cat .frontend.pid)
    if kill -0 $FRONTEND_PID 2>/dev/null; then
        kill $FRONTEND_PID
        echo -e "${GREEN}✓ 前端已停止 (PID: $FRONTEND_PID)${NC}"
    else
        echo -e "${YELLOW}⚠ 前端进程不存在 (PID: $FRONTEND_PID)${NC}"
    fi
    rm .frontend.pid
else
    echo -e "${YELLOW}⚠ 前端 PID 文件不存在${NC}"
fi

# 询问是否停止 Docker 容器
echo ""
read -p "是否停止 MongoDB 和 Redis 容器？(y/N) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    # 停止 MongoDB
    if docker ps | grep -q mongodb; then
        docker stop mongodb > /dev/null
        echo -e "${GREEN}✓ MongoDB 已停止${NC}"
    else
        echo -e "${YELLOW}⚠ MongoDB 未在运行${NC}"
    fi

    # 停止 Redis
    if docker ps | grep -q redis; then
        docker stop redis > /dev/null
        echo -e "${GREEN}✓ Redis 已停止${NC}"
    else
        echo -e "${YELLOW}⚠ Redis 未在运行${NC}"
    fi
else
    echo -e "${YELLOW}⚠ 保留 MongoDB 和 Redis 容器运行${NC}"
fi

echo ""
echo -e "${GREEN}✅ 所有服务已停止${NC}"
