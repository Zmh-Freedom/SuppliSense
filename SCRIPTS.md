# 一键启动脚本

## 使用方法

### 启动项目
```bash
./start.sh
```

自动完成：
- 检查并启动 MongoDB 和 Redis（Docker 容器）
- 创建 Python 虚拟环境并安装依赖
- 启动后端服务（FastAPI）
- 启动前端服务（Vite）

### 停止项目
```bash
./stop.sh
```

自动完成：
- 停止后端和前端进程
- 询问是否停止 Docker 容器

## 脚本功能

### start.sh
- ✅ 检查 Docker 是否运行
- ✅ 自动创建和启动 MongoDB（端口 27017）
- ✅ 自动创建和启动 Redis（端口 6379）
- ✅ 创建 Python 虚拟环境（backend/venv）
- ✅ 自动安装后端依赖
- ✅ 启动后端（端口 8000）
- ✅ 自动安装前端依赖
- ✅ 启动前端（端口 5173）
- ✅ 保存进程 PID 以便停止

### stop.sh
- ✅ 停止后端和前端进程
- ✅ 可选停止 Docker 容器
- ✅ 清理 PID 文件

## 访问地址

启动后访问：
- 🌐 前端: http://localhost:5173
- 🔧 后端: http://localhost:8000
- 📊 API 文档: http://localhost:8000/docs
- 🔍 健康检查: http://localhost:8000/health

## 默认账号

- 用户名: admin
- 密码: admin123

## 日志文件

- 后端日志: logs/backend.log
- 前端日志: logs/frontend.log

## 注意事项

1. 确保 Docker 已安装并运行
2. 首次运行会自动安装所有依赖，可能需要几分钟
3. 如果端口被占用，请手动停止占用进程或修改脚本中的端口
4. 停止脚本会询问是否停止 Docker 容器，选择 N 可以保留数据

## 手动启动（可选）

如果脚本有问题，可以手动启动：

```bash
# 1. 启动 MongoDB
docker start mongodb

# 2. 启动 Redis
docker start redis

# 3. 启动后端
cd backend
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 4. 启动前端
cd frontend
npm run dev
```
