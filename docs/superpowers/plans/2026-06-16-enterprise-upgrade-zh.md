# 企业级升级实施计划

**目标：** 将供应商风险分析平台从原型级升级至企业级标准

**架构：** 5 阶段渐进式升级 — 安全加固优先（关闭真实漏洞），然后建立后端/前端测试基础设施，再做代码质量清理，最后改进运维。每阶段产出可部署的应用，每任务可独立提交。

**技术栈：** FastAPI, MongoDB, PostgreSQL+pgvector, Redis, React 19, TypeScript, Vitest, pytest, Docker Compose, structlog, Prometheus

**最后更新：** 2026-06-19（合并上线审计发现的新问题）

---

## 阶段一：安全加固（最高优先级）

### 已完成的任务

以下任务已在当前代码中实现，无需重复执行：

- [x] **配置启动校验** — `main.py:63-79` `_validate_config()` 启动时检查 SECRET_KEY / MONGO_PASSWORD
- [x] **密码复杂度验证** — `schemas/user.py` `@field_validator("password")` 强制 8 位 + 字母 + 数字
- [x] **默认管理员密码随机化** — `main.py:82-111` 无 INITIAL_ADMIN_PASSWORD 时随机生成 16 位密码
- [x] **前端移除默认凭据提示** — `LoginPage.tsx` 已改为"请联系管理员获取账号"
- [x] **CORS methods/headers 收紧** — `main.py:161-162` 已限定 methods 和 headers
- [x] **天眼查默认 HTTPS** — config.py `TIANYANCHA_BASE_URL` 默认 https

---

### 新任务 1：API 全量认证加固 【CRITICAL】

**背景：** 审计发现 19 个 API router 中仅 `auth`、`async_tasks`、`notifications` 有认证依赖，其余 16 个 router 的所有端点完全公开。攻击者无需登录即可调用风险评估、舆情分析（消耗 LLM 费用）、清空知识库/预警数据、上传/删除文件等。

**影响的 router：** `risk`, `company`, `financial`, `sentiment`, `alert`, `chat`, `knowledge`, `upload`, `p2`, `macro`, `scenario`, `report`, `trend`, `compare`

**修改文件：**
- `backend/app/api/risk.py` — 添加 `dependencies=[Depends(get_current_user)]`
- `backend/app/api/company.py` — 同上
- `backend/app/api/financial.py` — 同上
- `backend/app/api/sentiment.py` — 同上
- `backend/app/api/alert.py` — 同上
- `backend/app/api/chat.py` — 同上
- `backend/app/api/knowledge.py` — 同上
- `backend/app/api/upload.py` — 同上
- `backend/app/api/p2.py` — 同上
- `backend/app/api/macro.py` — 同上
- `backend/app/api/scenario.py` — 同上
- `backend/app/api/report.py` — 同上
- `backend/app/api/trend.py` — 同上
- `backend/app/api/compare.py` — 同上

**步骤：**

1. 在每个 router 文件中添加导入：
   ```python
   from fastapi import Depends
   from app.core.deps import get_current_user
   ```

2. 将每个 `APIRouter()` 改为带默认依赖：
   ```python
   router = APIRouter(dependencies=[Depends(get_current_user)])
   ```

3. 对需要匿名访问的端点（如有）单独标记排除。

4. 在 `main.py` 的 `api_v1.include_router` 调用中也支持传入 `dependencies`。

**验证：** 不带 token 调用各端点返回 401。带有效 token 的正常请求不受影响。

**提交：** `security: 所有 API 路由添加认证依赖，修复 16 个未授权端点`

---

### 新任务 2：Prometheus /metrics 端点保护

**背景：** `/metrics` 端点在 `main.py:231-235` 完全公开，泄露内部请求量、端点名称、LLM 调用次数等敏感指标。恶意用户可构造大量不同路径造成高基数问题。

**修改文件：** `backend/app/main.py`

**步骤：**

1. 在 `metrics_endpoint` 函数上添加认证依赖：
   ```python
   from app.core.deps import get_current_admin_user

   @app.get("/metrics", dependencies=[Depends(get_current_admin_user)])
   async def metrics_endpoint():
       ...
   ```
   或者改为从环境变量读取 bearer token 进行简单认证：
   ```python
   METRICS_TOKEN = os.getenv("METRICS_TOKEN", "")

   @app.get("/metrics")
   async def metrics_endpoint(request: Request):
       token = request.headers.get("Authorization", "").removeprefix("Bearer ")
       if METRICS_TOKEN and token != METRICS_TOKEN:
           raise HTTPException(status_code=401)
       ...
   ```

**验证：** 不带 token 访问 `/metrics` 返回 401。Prometheus 配置中加上 bearer token 后可正常抓取。

**提交：** `security: /metrics 端点添加认证保护`

---

### 新任务 3：WebSocket 认证与限流

**背景：** `/ws` 端点在 `main.py:238-259` 既没有 token 验证也没有频率限制（`@limiter.exempt`），单客户端可无限开连接。

**修改文件：** `backend/app/main.py`

**步骤：**

1. 在 WebSocket 连接时验证 token（通过 query param 或首次消息）：
   ```python
   @app.websocket("/ws")
   async def websocket_endpoint(websocket: WebSocket):
       token = websocket.query_params.get("token")
       if not token:
           await websocket.close(code=4001, reason="Missing token")
           return
       try:
           payload = decode_token(token)
           if payload.get("type") != "access":
               await websocket.close(code=4001, reason="Invalid token")
               return
       except Exception:
           await websocket.close(code=4001, reason="Invalid token")
           return
       # ... 正常连接逻辑
   ```

2. 移除 `@limiter.exempt`，改用手动限流或保留 excmpt 但限制连接数。

3. 添加最大连接数限制：`ws_manager` 中维护连接计数，超过上限时拒绝新连接。

**验证：** 不带 token 连接 WebSocket 被拒绝。带有效 token 正常连接。

**提交：** `security: WebSocket 添加 token 认证和连接数限制`

---

### 新任务 4：Redis 连接修复 + 缓存反序列化安全

**背景：** 
1. `cache.py:16` 使用 `settings.MONGO_HOST` 作为 Redis 主机名，Mongo/Redis 分离部署时缓存完全失效
2. `cache.py:67-73` 从 Redis 读取 `__type__` 后 `importlib.import_module` + `getattr` 还原模型，若 Redis 被入侵可执行任意代码

**修改文件：** `backend/app/core/cache.py`

**步骤：**

1. 修改 Redis 连接使用 `REDIS_URL`：
   ```python
   # 修改前
   cache_client = redis.Redis(
       host=settings.MONGO_HOST if hasattr(settings, "MONGO_HOST") else "localhost",
       port=6379,
       db=2,
       decode_responses=True,
   )

   # 修改后
   cache_client = redis.Redis.from_url(
       settings.REDIS_URL,
       db=2,
       decode_responses=True,
   )
   ```

2. 将 Pydantic 模型反序列化改为白名单方式：
   ```python
   # 定义允许反序列化的类型白名单
   _ALLOWED_MODELS = {
       "RiskCalculateResponse": "app.schemas.risk:RiskCalculateResponse",
       # ... 其他允许的类型
   }

   def _deserialize_value(value: dict):
       type_path = value.pop("__type__", None)
       if type_path and type_path in _ALLOWED_MODELS:
           module_path, class_name = _ALLOWED_MODELS[type_path].split(":")
           ...
       return value
   ```

**验证：** 缓存正常读写。`REDIS_URL` 指向不同主机时缓存正常工作。

**提交：** `fix: Redis 改用 REDIS_URL 连接，缓存反序列化加白名单`

---

### 新任务 5：CORS Origins 可配置化

**背景：** `config.py:72` CORS_ORIGINS 硬编码为 localhost，无环境变量覆盖。生产部署到真实域名时所有前端请求被 CORS 拦截。

**修改文件：** `backend/app/core/config.py`

**步骤：**

```python
# 修改前
CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

# 修改后
CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
```

**验证：** 设置 `CORS_ORIGINS=https://your-domain.com` 后，前端可从该域名正常访问。

**提交：** `fix: CORS_ORIGINS 支持环境变量覆盖`

---

### 新任务 6：访问令牌有效期缩短 + 令牌类型强制分离

**背景：**
1. 访问令牌 24 小时有效 (`ACCESS_TOKEN_EXPIRE_MINUTES=1440`)，被盗后窗口过大
2. `decode_access_token` 实际可解码 refresh token，依赖调用方手动检查 `type` claim

**修改文件：**
- `backend/app/core/config.py`
- `backend/app/core/security.py`

**步骤：**

1. 缩短 access token 有效期：
   ```python
   ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
   ```

2. 分离 access / refresh token 解码函数，`decode_access_token` 拒绝非 access 类型：
   ```python
   def decode_access_token(token: str) -> dict:
       payload = decode_token(token)
       if payload.get("type") != "access":
           raise InvalidTokenError("token 类型不匹配")
       return payload
   ```

**验证：** 用 refresh token 调用 `decode_access_token` 抛出异常。登录后 30 分钟 access token 过期。

**提交：** `security: 缩短 access token 有效期至 30 分钟，强制类型校验`

---

### 新任务 7：频率限制适配反向代理

**背景：** slowapi 默认取 `remote_addr` 作为限流 key，在 nginx 反向代理后所有请求 IP 相同，全局 `60/minute` 变成所有用户共享。

**修改文件：** `backend/app/core/rate_limit.py`

**步骤：**

```python
from slowapi.util import get_remote_address

def get_client_ip(request):
    """获取真实客户端 IP，适配反向代理。"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)

# 在 slowapi Limiter 初始化时使用
limiter = Limiter(key_func=get_client_ip)
```

**验证：** 在 nginx 后面，不同客户端的 IP 应被正确识别，限流计数器按客户端独立。

**提交：** `fix: 频率限制适配反向代理 X-Forwarded-For`

---

### 新任务 8：Docker 安全加固（含未完成部分）

**背景：** 原计划任务 4 尚未完成。需补充：默认数据库密码弱、容器日志无轮转。

**修改文件：**
- `Dockerfile.backend`
- `docker-compose.yml`
- `docker-compose.dev.yml`

**步骤：**

1. `Dockerfile.backend` 添加非 root 用户：
   ```dockerfile
   RUN useradd -r -s /bin/false appuser && chown -R appuser:appuser /app
   USER appuser
   ```

2. `docker-compose.yml` 中 MongoDB / Redis 密码改为强制环境变量：
   ```yaml
   MONGO_INITDB_ROOT_PASSWORD: ${MONGO_PASSWORD:?请设置 MONGO_PASSWORD}
   ```

3. Redis 添加密码认证：
   ```yaml
   command: redis-server --requirepass ${REDIS_PASSWORD:?请设置 REDIS_PASSWORD}
   ```

4. 添加容器日志轮转：
   ```yaml
   services:
     backend:
       logging:
         driver: "json-file"
         options:
           max-size: "10m"
           max-file: "3"
   ```

5. `docker-compose.dev.yml` 默认密码改为强制要求环境变量（移除 `123456` / `sra123` 默认值）.

**验证：** `docker exec sra-backend whoami` 返回 `appuser`。不设置密码时 compose 拒绝启动。

**提交：** `security: 非 root 容器运行，强制数据库密码，Redis 认证，日志轮转`

---

## 阶段二：后端测试基础设施

### 任务 9：Pytest 配置与测试数据库隔离

（原任务 6，保持不变）

**新建/修改文件：**
- 新建 `backend/pyproject.toml`
- 修改 `backend/requirements.txt`
- 重写 `backend/tests/conftest.py`

**步骤：**

1. 创建 `backend/pyproject.toml`：
   ```toml
   [tool.pytest.ini_options]
   testpaths = ["tests"]
   asyncio_mode = "auto"
   addopts = "-v --tb=short --cov=app --cov-report=term-missing"

   [tool.coverage.run]
   source = ["app"]
   omit = [
       "app/core/celery_app.py",
       "app/services/feishu.py",
   ]

   [tool.coverage.report]
   fail_under = 0
   show_missing = true
   ```

2. 在 `backend/requirements.txt` 末尾添加测试依赖。

3. 重写 `backend/tests/conftest.py`，使用测试数据库隔离。

4. 运行 `cd backend && python -m pytest -v` 确认现有测试通过。

**提交：** `test: 添加 pytest 配置、测试 DB fixtures、覆盖率设置`

---

### 任务 10：认证服务测试

（原任务 7，保持不变）

**新建文件：** `backend/tests/test_auth.py`

覆盖：正确登录、错误密码、不存在用户、密码复杂度、修改密码、未认证访问等场景。

**提交：** `test: 添加认证服务完整测试`

---

### 任务 11：外部服务 Mock

（原任务 8，保持不变）

**新建文件：** `backend/tests/fixtures/mock_tianyancha.py`、`mock_llm.py`

**提交：** `test: 添加天眼查/LLM 可复用 mock`

---

### 新任务 12：API 认证拦截测试

**背景：** 配合新任务 1，验证所有受保护端点的认证拦截有效。

**新建文件：** `backend/tests/test_auth_guard.py`

**步骤：**

1. 用 pytest 参数化测试，遍历所有非公开 endpoint，验证无 token 返回 401。
2. 用有效 token 验证正常返回非 401。

**提交：** `test: 添加全量 API 认证拦截测试`

---

## 阶段三：前端测试基础设施

### 任务 13：Vitest 安装配置

（原任务 9，保持不变）

**提交：** `test: 安装配置 Vitest + Testing Library`

---

### 任务 14：API 工具函数测试

（原任务 10，保持不变）

**提交：** `test: 添加 api 工具函数测试`

---

### 任务 15：组件冒烟测试

（原任务 11，保持不变）

**提交：** `test: 添加核心页面组件冒烟测试`

---

## 阶段四：代码质量与可靠性

### 新任务 16：LLM 配置统一从 settings 读取

**背景：** `agent.py`、`react_graph.py`、`supervisor_graph.py`、`plan_execute_graph.py`、`router.py`、`context.py`、`sentiment.py` 共 7 处直接使用 `os.getenv("LLM_API_KEY")` 而非 `settings.LLM_API_KEY`。配置路径不一致，`.env` 加载失败时这些组件拿到空值。

**修改文件：**
- `backend/app/services/agent.py`
- `backend/app/graphs/react_graph.py`
- `backend/app/graphs/supervisor_graph.py`
- `backend/app/graphs/plan_execute_graph.py`
- `backend/app/graphs/router.py`
- `backend/app/graphs/context.py`
- `backend/app/services/sentiment.py`

**步骤：**

1. 每个文件将 `os.getenv("LLM_API_KEY")` / `os.getenv("LLM_BASE_URL")` / `os.getenv("LLM_MODEL")` 替换为 `settings.LLM_API_KEY` / `settings.LLM_BASE_URL` / `settings.LLM_MODEL`。
2. 确保各文件顶部 `from app.core.config import settings`。

**验证：** 正常对话、流式输出、舆情分析均正常工作。

**提交：** `refactor: LLM 配置统一从 settings 读取，消除 os.getenv 散落`

---

### 新任务 17：日志格式改为 JSON + 请求 ID 中间件

**背景：**
1. `logging.py` 使用 `ConsoleRenderer()`，人类可读但不适合 ELK/Datadog 解析
2. 没有 `X-Request-ID` 机制，无法跨服务关联日志

**修改/新建文件：**
- `backend/app/core/logging.py`
- 新建 `backend/app/core/middleware.py`

**步骤：**

1. 在 `logging.py` 中根据环境变量切换 renderer：
   ```python
   import os
   renderer = JSONRenderer() if os.getenv("LOG_FORMAT") == "json" else ConsoleRenderer()
   ```

2. 新建 `middleware.py`，生成并注入 `X-Request-ID`，在日志中绑定 request_id。

3. 在 `main.py` 中注册请求 ID 中间件。

**验证：** 设置 `LOG_FORMAT=json` 后日志为 JSON 行格式。每个请求有唯一 request_id 并出现在相关日志中。

**提交：** `feat: 支持 JSON 格式日志，添加请求 ID 中间件`

---

### 新任务 18：Plan-Execute 真流式改造

**背景：** `plan_execute_graph.py:301-303` 是假流式——先生成完整回答再按 10 字符分块 + `asyncio.sleep(0.02)` 发送。应改为真正的 LLM token 级别流式。

**修改文件：** `backend/app/graphs/plan_execute_graph.py`

**步骤：**

1. 将 replanner node 的 LLM 调用改为 streaming 模式。
2. 逐 token yield 到 SSE，移除 `asyncio.sleep(0.02)` 模拟延迟。

**验证：** Plan-Execute 模式下对话有真正的逐 token 流式体验。

**提交：** `feat: Plan-Execute 模式改为真正的 LLM token 流式`

---

### 新任务 19：schema 初始化失败改为硬错误

**背景：** `init_pg.py:106` 中 `except Exception: logger.warning(...)` 静默忽略错误，应用正常启动后业务查询才报错。

**修改文件：** `backend/app/db/init_pg.py`

**步骤：**

1. 在 `ensure_pg_schema()` 失败时 raise 而非 log.warning，让应用在启动阶段就暴露问题。

**验证：** 模拟 PG 不可达时，应用拒绝启动并输出明确错误。

**提交：** `fix: PG schema 初始化失败改为硬错误，避免静默失效`

---

### 新任务 20：前端 Error Boundary + 路由懒加载

**背景：**
1. 无全局 ErrorBoundary，路由组件渲染异常导致白屏
2. `routes.tsx` 所有组件静态 import，首页加载全量 JS

**修改文件：**
- 新建 `frontend/src/components/AppErrorBoundary.tsx`
- `frontend/src/routes.tsx`

**步骤：**

1. 创建全局 ErrorBoundary，捕获渲染错误后显示降级 UI + 重试按钮。
2. 将路由组件改为 `React.lazy(() => import(...))`，用 `<Suspense>` 包裹。

**验证：** 模拟组件崩溃时显示降级 UI。Network 面板确认路由切换时按需加载 chunk。

**提交：** `feat: 全局 ErrorBoundary + 路由懒加载`

---

### 任务 21：前端死代码清理

（原任务 12，保持不变）

**提交：** `refactor: 清理死代码，统一 API 请求处理`

---

### 任务 22：后端无用依赖清理

（原任务 13，保持不变）

**提交：** `chore: 移除未使用的 celery/flower 依赖`

---

### 新任务 23：飞书通知 N+1 查询优化

**背景：** `feishu.py:103-105` 每日摘要为每个公司单独查询 MongoDB，100 个监控企业产生 200+ 次查询。

**修改文件：** `backend/app/services/feishu.py`

**步骤：**

1. 使用 `$in` 查询批量获取所有公司的最新快照，替代逐公司查询。

**验证：** 每日摘要推送正常，MongoDB 慢查询日志中不再出现大量单条查询。

**提交：** `perf: 飞书每日摘要改为批量查询，消除 N+1`

---

### 新任务 24：MongoDB 连接池参数调优

**背景：** `mongo.py` 未配置 `maxPoolSize`、`minPoolSize`、`maxIdleTimeMS`。

**修改文件：** `backend/app/db/mongo.py`

**步骤：**

```python
_client = MongoClient(
    host=settings.MONGO_HOST,
    ...
    maxPoolSize=50,
    minPoolSize=5,
    maxIdleTimeMS=30000,
    serverSelectionTimeoutMS=5000,
)
```

**验证：** 高并发时连接数稳定在合理范围。

**提交：** `perf: MongoDB 连接池参数调优`

---

## 阶段五：运维改进

### 任务 25：.dockerignore 优化

（原任务 16，保持不变）

**提交：** `chore: 添加 .dockerignore 减小构建上下文`

---

### 新任务 26：Docker Compose 健康检查 + 启动顺序

**背景：** `docker-compose.yml` 中 backend 无 healthcheck，frontend 的 `depends_on` 无 `condition: service_healthy`，可能导致前端在 backend 就绪前返回 502。

**修改文件：** `docker-compose.yml`

**步骤：**

1. backend 服务添加 healthcheck：
   ```yaml
   healthcheck:
     test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
     interval: 10s
     timeout: 5s
     retries: 3
   ```

2. frontend 的 depends_on 加上条件：
   ```yaml
   depends_on:
     backend:
       condition: service_healthy
   ```

**验证：** `docker compose up` 后 frontend 在 backend healthcheck 通过后才开始接受请求。

**提交：** `feat: Docker 健康检查 + 启动顺序保障`

---

### 新任务 27：舆情分析接入正规新闻 API

**背景：** `sentiment.py:103-191` 通过 HTML 爬取 Bing/DuckDuckGo 搜索结果，违反 ToS，生产环境 IP 很快会被封禁。

**修改文件：** `backend/app/services/sentiment.py`

**步骤：**

1. 调研并接入正规新闻 API（如 NewsAPI、天行数据新闻接口、聚合数据等）。
2. 保留现有爬取逻辑作为 fallback（通过 feature flag 控制是否启用）。
3. 添加搜索结果缓存（Redis，TTL 1 小时）。

**验证：** 舆情分析正常返回结果。不再有对搜索引擎的 HTML 爬取请求。

**提交：** `feat: 舆情分析接入正规新闻 API，添加搜索缓存`

---

## 执行顺序

```
阶段一（安全：任务 1→8）───┐
                          ├──> 阶段四（代码质量：任务 16→24）+ 阶段五（运维：任务 25→27）
阶段二（后端测试：9→12）──┤
                          │
阶段三（前端测试：13→15）──┘
```

阶段一必须最先完成，关闭所有安全漏洞。阶段二、三可并行进行。阶段四、五在所有测试通过后执行。

## 验证清单

- [ ] 阶段一完成后：所有 API 端点要求认证（除 auth/login），/metrics 受保护，WebSocket 需 token
- [ ] 阶段一完成后：不设置必需环境变量时应用拒绝启动
- [ ] 阶段一完成后：CORS 支持通过环境变量配置生产域名
- [ ] 阶段二完成后：`cd backend && python -m pytest -v --cov` 全部通过，含认证拦截测试
- [ ] 阶段三完成后：`cd frontend && npm test` 全部通过
- [ ] 阶段四完成后：`npm run typecheck && npm run build` 无错误
- [ ] 阶段四完成后：LLM 配置全部从 settings 读取，无 os.getenv 散落
- [ ] 阶段四完成后：日志支持 JSON 格式，每个请求有唯一 request_id
- [ ] 阶段五完成后：Docker 镜像构建更快，容器日志自动轮转，服务有健康检查
