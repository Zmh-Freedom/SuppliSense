#!/bin/bash

# 一键启动脚本
# 用法: ./start.sh

set -e

echo "🚀 启动供应商风险分析系统..."
echo ""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 检查 Docker 是否运行
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}❌ Docker 未运行，请先启动 Docker${NC}"
    exit 1
fi

# 启动 MongoDB
echo -e "${YELLOW}📦 检查 MongoDB...${NC}"
if docker ps | grep -q mongodb; then
    echo -e "${GREEN}✓ MongoDB 已在运行${NC}"
else
    # 检查是否存在容器（包括停止的）
    if docker ps -a --format '{{.Names}}' | grep -q '^mongodb$'; then
        # 容器存在，尝试启动
        if docker start mongodb > /dev/null 2>&1; then
            echo -e "${GREEN}✓ MongoDB 已启动${NC}"
        else
            # 启动失败，删除并重建
            echo -e "${YELLOW}⚠ MongoDB 容器异常，正在重建...${NC}"
            docker rm -f mongodb > /dev/null 2>&1
            docker run -d --name mongodb \
                -p 27017:27017 \
                -e MONGO_INITDB_ROOT_USERNAME=root \
                -e MONGO_INITDB_ROOT_PASSWORD=123456 \
                mongo:7 > /dev/null
            echo -e "${GREEN}✓ MongoDB 已重建并启动${NC}"
        fi
    else
        # 容器不存在，创建新的
        docker run -d --name mongodb \
            -p 27017:27017 \
            -e MONGO_INITDB_ROOT_USERNAME=root \
            -e MONGO_INITDB_ROOT_PASSWORD=123456 \
            mongo:7 > /dev/null
        echo -e "${GREEN}✓ MongoDB 已创建并启动${NC}"
    fi
fi

# 启动 Redis
echo -e "${YELLOW}📦 检查 Redis...${NC}"
if docker ps | grep -q redis; then
    echo -e "${GREEN}✓ Redis 已在运行${NC}"
else
    # 检查是否存在容器（包括停止的）
    if docker ps -a --format '{{.Names}}' | grep -q '^redis$'; then
        # 容器存在，尝试启动
        if docker start redis > /dev/null 2>&1; then
            echo -e "${GREEN}✓ Redis 已启动${NC}"
        else
            # 启动失败，删除并重建
            echo -e "${YELLOW}⚠ Redis 容器异常，正在重建...${NC}"
            docker rm -f redis > /dev/null 2>&1
            docker run -d --name redis \
                -p 6379:6379 \
                redis:7 > /dev/null
            echo -e "${GREEN}✓ Redis 已重建并启动${NC}"
        fi
    else
        # 容器不存在，创建新的
        docker run -d --name redis \
            -p 6379:6379 \
            redis:7 > /dev/null
        echo -e "${GREEN}✓ Redis 已创建并启动${NC}"
    fi
fi

echo ""
echo -e "${YELLOW}⏳ 等待数据库就绪...${NC}"
sleep 2

# 启动后端
echo -e "${YELLOW}🔧 启动后端服务...${NC}"
cd backend

# 检查是否有虚拟环境
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}📦 创建虚拟环境...${NC}"
    python3 -m venv venv
fi

# 激活虚拟环境
source venv/bin/activate

# 安装依赖（如果需要）
if [ ! -f ".deps_installed" ]; then
    echo -e "${YELLOW}📦 安装后端依赖...${NC}"
    pip install -r requirements.txt -q
    touch .deps_installed
fi

# 在后台启动后端
uvicorn app.main:app --host 0.0.0.0 --port 8000 > ../logs/backend.log 2>&1 &
BACKEND_PID=$!
echo -e "${GREEN}✓ 后端已启动 (PID: $BACKEND_PID)${NC}"

cd ..

# 创建日志目录
mkdir -p logs

# 启动前端
echo -e "${YELLOW}🎨 启动前端服务...${NC}"
cd frontend

# 安装依赖（如果需要）
if [ ! -d "node_modules" ]; then
    echo -e "${YELLOW}📦 安装前端依赖...${NC}"
    npm install > /dev/null 2>&1
fi

# 在后台启动前端
npm run dev > ../logs/frontend.log 2>&1 &
FRONTEND_PID=$!
echo -e "${GREEN}✓ 前端已启动 (PID: $FRONTEND_PID)${NC}"

cd ..

# 保存 PID 以便停止
echo "$BACKEND_PID" > .backend.pid
echo "$FRONTEND_PID" > .frontend.pid

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✅ 所有服务已启动！${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "🌐 前端地址: ${GREEN}http://localhost:5173${NC}"
echo -e "🔧 后端地址: ${GREEN}http://localhost:8000${NC}"
echo -e "📊 API 文档: ${GREEN}http://localhost:8000/docs${NC}"
echo -e "🔍 健康检查: ${GREEN}http://localhost:8000/health${NC}"
echo ""
echo -e "👤 默认管理员: ${YELLOW}admin${NC}"
echo -e "🔑 默认密码: ${YELLOW}admin123${NC}"
echo ""
echo -e "📋 日志文件:"
echo -e "   后端: logs/backend.log"
echo -e "   前端: logs/frontend.log"
echo ""
echo -e "⏹️  停止服务: ${YELLOW}./stop.sh${NC}"
echo ""
