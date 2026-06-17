# 企业级升级实施计划

**目标：** 将供应商风险分析系统从原型级升级至企业级标准

**架构：** 5 阶段渐进式升级 — 安全加固优先（关闭真实漏洞），然后建立后端/前端测试基础设施，再做代码质量清理，最后改进运维。每阶段产出可部署的应用，每任务可独立提交。

**技术栈：** FastAPI, MongoDB, Redis, React 19, TypeScript, Vitest, pytest, Docker Compose, structlog, Prometheus

---

## 阶段一：安全加固

### 任务 1：外部化硬编码凭据

**修改文件：**
- `backend/app/core/config.py` — 移除 `MONGO_PASSWORD` 和 `SECRET_KEY` 的硬编码默认值
- `backend/app/db/mongo.py` — 统一使用 `settings` 而非直接读取环境变量
- `docker-compose.yml` — 密码改为 `${MONGO_PASSWORD:?未设置}`
- `docker-compose.dev.yml` — 同上
- `backup.sh` — 从环境变量读取密码
- `restore.sh` — 从环境变量读取密码
- `.env.docker` — 将真实密钥替换为占位符
- `backend/app/main.py` — 添加启动配置校验

**步骤：**

1. 修改 `backend/app/core/config.py`，将两个关键配置的默认值改为空字符串：
   ```python
   # 修改前
   SECRET_KEY: str = os.getenv("SECRET_KEY", "your-secret-key-change-in-production-1234567890")
   MONGO_PASSWORD: str = os.getenv("MONGO_PASSWORD", "123456")

   # 修改后
   SECRET_KEY: str = os.getenv("SECRET_KEY", "")
   MONGO_PASSWORD: str = os.getenv("MONGO_PASSWORD", "")
   ```

2. 修改 `backend/app/db/mongo.py`，使用 `settings` 对象替代直接的 `os.getenv` 调用：
   ```python
   # 修改前（第 15-23 行）
   _client = MongoClient(
       host=os.getenv("MONGO_HOST", "localhost"),
       port=int(os.getenv("MONGO_PORT", "27017")),
       username=os.getenv("MONGO_USER", "root"),
       password=os.getenv("MONGO_PASSWORD", "123456"),
       authSource=os.getenv("MONGO_AUTH_SOURCE", "admin"),
       serverSelectionTimeoutMS=5000,
   )
   return _client[os.getenv("MONGO_DB", "tianyancha")]

   # 修改后
   from app.core.config import settings

   _client = MongoClient(
       host=settings.MONGO_HOST,
       port=settings.MONGO_PORT,
       username=settings.MONGO_USER,
       password=settings.MONGO_PASSWORD,
       authSource=settings.MONGO_AUTH_SOURCE,
       serverSelectionTimeoutMS=5000,
   )
   return _client[settings.MONGO_DB]
   ```
   删除不再需要的 `import os`（如果文件中没有其他地方使用）。

3. 修改 `docker-compose.yml` 和 `docker-compose.dev.yml`，将硬编码密码替换为环境变量引用：
   ```yaml
   # 修改前
   environment:
     MONGO_INITDB_ROOT_USERNAME: root
     MONGO_INITDB_ROOT_PASSWORD: 123456

   # 修改后
   environment:
     MONGO_INITDB_ROOT_USERNAME: ${MONGO_USER:-root}
     MONGO_INITDB_ROOT_PASSWORD: ${MONGO_PASSWORD:?请设置 MONGO_PASSWORD 环境变量}
   ```

4. 修改 `backup.sh`，将第 10-11 行改为从环境变量读取：
   ```bash
   # 修改前
   MONGO_USER="root"
   MONGO_PASS="123456"

   # 修改后
   MONGO_USER="${MONGO_USER:-root}"
   MONGO_PASS="${MONGO_PASSWORD:?请设置 MONGO_PASSWORD 环境变量}"
   ```

5. 修改 `restore.sh`，找到硬编码的凭据（约第 81-82 行），同样改为环境变量引用。

6. 修改 `.env.docker`，将所有真实密钥替换为占位符：
   ```bash
   # 修改前
   MONGO_PASSWORD=123456
   SECRET_KEY=change-this-to-a-random-secret-in-production
   TIANYANCHA_TOKEN=87081b56-c7ff-4177-bea0-94be05a415ce
   LLM_API_KEY=sk-bbf1ac59afe647aaa0a154e7c9bc972c

   # 修改后
   # 重要：启动前必须替换以下所有占位符
   MONGO_PASSWORD=CHANGE_ME_设置一个强密码
   SECRET_KEY=CHANGE_ME_生成一个64位随机字符串
   TIANYANCHA_TOKEN=CHANGE_ME_你的天眼查API令牌
   LLM_API_KEY=CHANGE_ME_你的LLM_API密钥
   ```

7. 在 `backend/app/main.py` 中添加启动配置校验函数，在 `lifespan` 函数开头调用：
   ```python
   def _validate_config():
       """启动前校验关键配置。"""
       errors = []
       if not settings.SECRET_KEY:
           errors.append("SECRET_KEY 未设置。生成命令：python -c \"import secrets; print(secrets.token_urlsafe(48))\"")
       if not settings.MONGO_PASSWORD:
           errors.append("MONGO_PASSWORD 未设置。")
       if settings.SECRET_KEY in (
           "your-secret-key-change-in-production-1234567890",
           "change-this-to-a-random-secret-in-production",
           "CHANGE_ME_生成一个64位随机字符串",
       ):
           errors.append("SECRET_KEY 仍为占位符，请设置真实的随机密钥。")
       if errors:
           for e in errors:
               logger.error("config_validation_failed", error=e)
           raise SystemExit(1)

   @asynccontextmanager
   async def lifespan(app: FastAPI):
       _validate_config()
       # ... 后续不变
   ```

**验证：** 不设置 `SECRET_KEY` 和 `MONGO_PASSWORD` 时，应用拒绝启动并输出明确错误信息。

**提交：** `security: 外部化所有硬编码凭据，要求通过环境变量配置`

---

### 任务 2：Cookie 安全与 CORS 收紧

**修改文件：**
- `backend/app/core/config.py` — 新增 `COOKIE_SECURE` 配置项
- `backend/app/api/auth.py` — cookie 添加 `secure` 标志
- `backend/app/main.py` — 收紧 CORS 策略

**步骤：**

1. 在 `backend/app/core/config.py` 的 `CORS_ORIGINS` 行之后添加：
   ```python
   COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "true").lower() == "true"
   ```

2. 在 `backend/app/api/auth.py` 中找到所有 `response.set_cookie()` 调用（`login_json` 和 `refresh_token` 端点），添加 `secure` 参数：
   ```python
   response.set_cookie(
       key="access_token",
       value=access_token,
       httponly=True,
       samesite="lax",
       secure=settings.COOKIE_SECURE,  # 新增
       max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
   )
   ```
   确保文件顶部已导入 `from app.core.config import settings`。

3. 修改 `backend/app/main.py` 中的 CORS 配置：
   ```python
   # 修改前
   allow_methods=["*"],
   allow_headers=["*"],

   # 修改后
   allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
   allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
   ```

**验证：** 登录后在浏览器 DevTools 中检查 `access_token` cookie，确认 `Secure` 和 `HttpOnly` 标志已设置。开发模式下 `secure=False` 以支持 localhost HTTP。

**提交：** `security: cookie 添加 secure 标志，收紧 CORS 策略`

---

### 任务 3：密码验证与默认管理员加固

**修改文件：**
- `backend/app/schemas/user.py` — 添加密码复杂度验证
- `backend/app/main.py` — 默认管理员密码随机化
- `frontend/src/components/LoginPage.tsx` — 移除硬编码凭据提示

**步骤：**

1. 在 `backend/app/schemas/user.py` 的 `UserCreate` 类中添加密码验证器：
   ```python
   from pydantic import field_validator

   class UserCreate(UserBase):
       password: str

       @field_validator("password")
       @classmethod
       def validate_password(cls, v: str) -> str:
           if len(v) < 8:
               raise ValueError("密码长度至少为8个字符")
           if not any(c.isalpha() for c in v):
               raise ValueError("密码必须包含至少一个字母")
           if not any(c.isdigit() for c in v):
               raise ValueError("密码必须包含至少一个数字")
           return v
   ```

2. 修改 `backend/app/main.py` 中的 `create_default_admin` 函数，从环境变量读取密码或随机生成：
   ```python
   import secrets
   import string

   def create_default_admin():
       from app.services.auth import create_user, list_users
       from app.schemas.user import UserCreate, UserRole

       users = list_users()
       if not users:
           password = os.getenv("INITIAL_ADMIN_PASSWORD")
           if not password:
               alphabet = string.ascii_letters + string.digits + "!@#$%&*"
               password = "".join(secrets.choice(alphabet) for _ in range(16))
               logger.warning(
                   "default_admin_created_with_random_password",
                   username="admin",
                   password=password,
                   hint="请保存此密码！设置 INITIAL_ADMIN_PASSWORD 环境变量可自定义。",
               )
           try:
               create_user(UserCreate(
                   username="admin",
                   email="admin@example.com",
                   password=password,
                   role=UserRole.ADMIN,
               ))
               logger.info("default_admin_created", username="admin")
           except ValueError:
               pass
   ```

3. 修改 `frontend/src/components/LoginPage.tsx`，移除硬编码凭据提示：
   ```tsx
   // 修改前
   <p className="text-xs text-gray-400 text-center">
     默认管理员：admin / admin123
   </p>

   // 修改后
   <p className="text-xs text-gray-400 text-center">
     请联系管理员获取账号
   </p>
   ```

**验证：** 尝试用短密码注册用户，应返回 422 验证错误。首次启动时检查日志中输出的随机管理员密码。

**提交：** `security: 强制密码复杂度，随机化默认管理员密码`

---

### 任务 4：Docker 安全加固

**修改文件：**
- `Dockerfile.backend` — 非 root 用户运行
- `docker-compose.yml` — 网络隔离、移除端口暴露、Redis 认证
- `backend/app/core/config.py` — 新增 `REDIS_PASSWORD` 配置

**步骤：**

1. 修改 `Dockerfile.backend`，在 CMD 之前添加非 root 用户：
   ```dockerfile
   RUN useradd -r -s /bin/false appuser && \
       chown -R appuser:appuser /app
   USER appuser
   EXPOSE 8000
   CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
   ```

2. 修改 `docker-compose.yml`，添加网络隔离并移除内部服务端口暴露：
   ```yaml
   # 在文件末尾添加
   networks:
     internal:
       driver: bridge
     external:
       driver: bridge

   # mongo 服务添加
   mongo:
     networks:
       - internal
     # 删除 ports: ["27017:27017"]

   # redis 服务添加
   redis:
     networks:
       - internal
     command: redis-server --requirepass ${REDIS_PASSWORD:-}
     # 删除 ports: ["6379:6379"]

   # backend 服务添加
   backend:
     networks:
       - internal
       - external

   # frontend 服务添加
   frontend:
     networks:
       - external
   ```

3. 在 `backend/app/core/config.py` 中添加 Redis 密码配置：
   ```python
   REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
   ```

4. 在 `.env.docker` 中添加：
   ```bash
   REDIS_PASSWORD=CHANGE_ME_redis密码
   REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379
   ```

**验证：** 运行 `docker exec sra-backend whoami` 应返回 `appuser`。Redis 无密码时 `redis-cli ping` 应失败。

**提交：** `security: 非 root 容器，网络隔离，Redis 认证`

---

### 任务 5：天眼查 HTTPS

**修改文件：**
- `backend/app/core/config.py` — 默认 URL 改为 HTTPS
- `backend/app/services/tianyancha_client.py` — 同步修改

**步骤：**

1. 修改 `backend/app/core/config.py`：
   ```python
   # 修改前
   TIANYANCHA_BASE_URL: str = os.getenv("TIANYANCHA_BASE_URL", "http://open.api.tianyancha.com")

   # 修改后
   TIANYANCHA_BASE_URL: str = os.getenv("TIANYANCHA_BASE_URL", "https://open.api.tianyancha.com")
   ```

2. 修改 `backend/app/services/tianyancha_client.py`，将模块级 `BASE_URL` 改为使用 settings：
   ```python
   # 修改前
   BASE_URL = os.getenv("TIANYANCHA_BASE_URL", "http://open.api.tianyancha.com")

   # 修改后
   from app.core.config import settings
   BASE_URL = settings.TIANYANCHA_BASE_URL
   ```

**验证：** 天眼查 API 正常工作（天眼查支持 HTTPS）。

**提交：** `security: 天眼查 API 改用 HTTPS`

---

## 阶段二：后端测试基础设施

### 任务 6：Pytest 配置与测试数据库隔离

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

2. 在 `backend/requirements.txt` 末尾添加：
   ```
   # 测试依赖
   pytest-cov>=4.0.0
   pytest-asyncio>=0.23.0
   respx>=0.21.0
   ```

3. 重写 `backend/tests/conftest.py`：
   ```python
   """共享测试 fixtures。"""

   import os
   import pytest

   # 使用测试数据库，避免污染开发数据
   os.environ.setdefault("MONGO_DB", "tianyancha_test")
   os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32chars!")
   os.environ.setdefault("MONGO_PASSWORD", "test_password")

   from fastapi.testclient import TestClient


   @pytest.fixture(scope="session")
   def app():
       from app.main import app as fastapi_app
       return fastapi_app


   @pytest.fixture
   def client(app):
       with TestClient(app) as c:
           yield c


   @pytest.fixture
   def db():
       from app.db.mongo import get_db
       database = get_db()
       yield database
       for collection_name in database.list_collection_names():
           database[collection_name].drop()
   ```

4. 运行 `cd backend && python -m pytest -v` 确认现有 9 个测试仍然通过。

**提交：** `test: 添加 pytest 配置、测试 DB fixtures、覆盖率设置`

---

### 任务 7：认证服务测试

**新建文件：** `backend/tests/test_auth.py`

**步骤：**

1. 创建测试文件，覆盖以下场景：
   - 正确凭据登录成功
   - 错误密码登录返回 401
   - 不存在的用户登录返回 401
   - 短密码被拒绝（422）
   - 无字母密码被拒绝（422）
   - 无数字密码被拒绝（422）
   - 修改密码成功后新密码可登录
   - 未认证访问 `/auth/me` 返回 401

2. 运行 `cd backend && python -m pytest tests/test_auth.py -v` 确认全部通过。

**提交：** `test: 添加认证服务完整测试`

---

### 任务 8：外部服务 Mock

**新建文件：**
- `backend/tests/fixtures/__init__.py`
- `backend/tests/fixtures/mock_tianyancha.py`
- `backend/tests/fixtures/mock_llm.py`

**步骤：**

1. 创建 mock 数据文件，包含天眼查企业信息、司法风险、LLM 响应的模拟数据。

2. 运行 `cd backend && python -m pytest -v` 确认全部通过。

**提交：** `test: 添加天眼查/LLM 可复用 mock`

---

## 阶段三：前端测试基础设施

### 任务 9：Vitest 安装配置

**修改/新建文件：**
- `frontend/package.json` — 添加测试依赖和脚本
- 新建 `frontend/vitest.config.ts`
- 新建 `frontend/src/__tests__/setup.ts`

**步骤：**

1. 安装依赖：`cd frontend && npm install -D vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom`

2. 在 `package.json` 的 scripts 中添加：
   ```json
   "test": "vitest run",
   "test:watch": "vitest",
   "test:coverage": "vitest run --coverage"
   ```

3. 创建 `frontend/vitest.config.ts`。

4. 创建 `frontend/src/__tests__/setup.ts`。

5. 运行 `npm test` 确认测试运行器正常工作。

**提交：** `test: 安装配置 Vitest + Testing Library`

---

### 任务 10：API 工具函数测试

**新建文件：** `frontend/src/__tests__/api.test.ts`

**步骤：**

1. 测试 `getStoredUser`、`setStoredUser`、`clearStoredUser`、`isAuthenticated` 等 localStorage 工具函数。

2. 运行 `npm test` 确认全部通过。

**提交：** `test: 添加 api 工具函数测试`

---

### 任务 11：组件冒烟测试

**新建文件：**
- `frontend/src/__tests__/LoginPage.test.tsx`
- `frontend/src/__tests__/ErrorBoundary.test.tsx`

**步骤：**

1. 测试 LoginPage 渲染登录表单、按钮禁用状态。
2. 测试 ErrorBoundary 正常渲染子组件、捕获错误后显示降级 UI。

**提交：** `test: 添加核心页面组件冒烟测试`

---

## 阶段四：代码质量与清理

### 任务 12：前端死代码清理

**修改文件：**
- `frontend/src/components/ErrorBoundary.tsx` — 移除无用的 `localStorage.removeItem('active_tab')`
- `frontend/src/components/Layout.tsx` — 移除空操作的 `onRefresh` prop
- `frontend/src/components/Sidebar.tsx` — `onRefresh` 改为可选
- `frontend/src/api.ts` — `api.upload` 重构使用共享的 `request<T>()`

**步骤：**

1. 在 ErrorBoundary 中删除 `localStorage.removeItem('active_tab')`。
2. 在 Layout 中移除 `onRefresh={() => {}}`。
3. 在 Sidebar 的 Props 接口中将 `onRefresh` 改为可选。
4. 重构 `api.upload` 使用共享的 `request<T>()` 函数，需要先更新 `request` 函数以支持 FormData。

**验证：** `npm run typecheck` 无错误，`npm run build` 成功。

**提交：** `refactor: 清理死代码，统一 API 请求处理`

---

### 任务 13：后端无用依赖清理

**修改文件：**
- `backend/requirements.txt` — 移除 celery、flower，去重 python-multipart
- 删除 `backend/app/core/celery_app.py`

**步骤：**

1. 从 requirements.txt 中删除 `celery>=5.3.0`、`flower>=2.0.0`、重复的 `python-multipart`。
2. 删除 `celery_app.py` 文件。
3. 确认无其他文件引用 `celery_app`。

**验证：** `pip install -r requirements.txt` 成功，应用正常启动。

**提交：** `chore: 移除未使用的 celery/flower 依赖`

---

### 任务 14：SSE 错误日志改进

**修改文件：**
- `frontend/src/api.ts` — SSE 解析错误改为 `console.warn`
- `backend/app/core/errors.py` — 错误日志添加请求上下文

**步骤：**

1. 在 `api.ts` 的 SSE 解析 catch 块中，将静默忽略改为警告输出。
2. 在 `errors.py` 的未处理异常处理器中，添加请求路径和方法到日志。

**提交：** `fix: 改进 SSE 错误日志，错误处理添加请求上下文`

---

## 阶段五：运维改进

### 任务 15：请求日志中间件

**新建文件：** `backend/app/core/middleware.py`
**修改文件：** `backend/app/main.py`

**步骤：**

1. 创建中间件，使用 structlog 记录请求方法、路径、状态码、耗时。
2. 在 main.py 中注册中间件。

**提交：** `feat: 添加结构化请求/响应日志中间件`

---

### 任务 16：.dockerignore 优化

**新建/修改文件：**
- `.dockerignore`
- `frontend/.dockerignore`

**步骤：**

1. 创建 `.dockerignore`，排除 tests、venv、.env.docker、backups、*.md、*.sh。
2. 创建 `frontend/.dockerignore`，排除 node_modules、dist。

**提交：** `chore: 添加 .dockerignore 减小构建上下文`

---

## 执行顺序

```
阶段一（安全）→ 阶段二（后端测试）+ 阶段三（前端测试）→ 阶段四（代码质量）+ 阶段五（运维）
```

## 验证清单

- [ ] 阶段一完成后：应用缺少必需环境变量时拒绝启动
- [ ] 阶段二完成后：`cd backend && python -m pytest -v --cov` 全部通过
- [ ] 阶段三完成后：`cd frontend && npm test` 全部通过
- [ ] 阶段四完成后：`npm run typecheck && npm run build` 无错误，UI 外观不变
- [ ] 阶段五完成后：Docker 镜像构建更快，日志包含请求上下文
