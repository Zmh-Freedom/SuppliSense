# 启动指南

## 开发模式（改代码自动生效）

```bash
docker compose -f docker-compose.dev.yml up -d   # 启动数据库+后端
cd frontend && npm run dev                        # 启动前端（另开终端）
```

后端通过 volume 挂载代码，`uvicorn --reload` 检测变更自动重启。前端 Vite HMR 热更新。

## 生产/演示模式

```bash
./start.sh    # docker compose up -d --build
./stop.sh     # docker compose down
```

## 访问地址

| 模式 | 地址 | API 文档 |
|------|------|----------|
| 开发 | http://localhost:5173 | http://localhost:8000/docs |
| 生产 | http://localhost | http://localhost/api/v1/docs |

## 默认账号

- 用户名: admin
- 密码: admin123

## 日志查看

```bash
docker compose logs -f backend
docker compose logs -f frontend
```
