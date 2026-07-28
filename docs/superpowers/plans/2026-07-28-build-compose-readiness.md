# 构建与 Compose 上线门禁修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让前端生产构建、代码检查和测试稳定通过，并让 Docker Compose 能从统一环境文件启动且正确判断后端就绪状态。

**Architecture:** 前端保留现有 TypeScript 与 ESLint 强度，在组件源头修正类型、派生状态和不可变性问题。部署侧由启动脚本统一传递 `.env.docker`，后端使用标准库健康检查，readiness 聚合 MongoDB、Redis、PostgreSQL并以 HTTP 状态反映结果。

**Tech Stack:** React 19、TypeScript 6、ESLint 10、Vitest、FastAPI、pytest、Docker Compose、Bash。

## Global Constraints

- 不扩展业务功能，不改变既有 API 正常响应字段。
- 不关闭 TypeScript `noUnusedLocals` / `noUnusedParameters`，不全局禁用 React Hooks 规则。
- 不新增第三方依赖。
- 前端测试、lint、build 必须全部退出码 0。
- readiness 三项依赖全部健康时返回 200，任一失败时返回 503。
- Compose 和后端使用同一组 Redis 密码配置。
- 保留供应商画像工作树及所有无关改动。

---

### Task 1: 修复 TypeScript 生产构建

**Files:**
- Modify: `frontend/tsconfig.app.json`
- Modify: `frontend/src/__tests__/AppErrorBoundary.test.tsx`
- Modify: `frontend/src/components/Dashboard.tsx`
- Modify: `frontend/src/components/SentimentPanel.tsx`
- Modify: `frontend/src/components/SupplierProfilePage.tsx`

**Interfaces:**
- Consumes: Vitest 全局类型、Recharts `Tooltip` formatter 类型。
- Produces: `npm run build` 可通过的 TypeScript 源码，不改变页面数据结构。

- [ ] **Step 1: 固定失败基线**

Run: `cd frontend && npm run build`

Expected: FAIL，包含 `Cannot find name 'vi'`、未使用变量和 Recharts formatter 类型错误。

- [ ] **Step 2: 修正测试类型**

在 `tsconfig.app.json` 中将 `types` 改为：

```json
"types": ["vite/client", "vitest/globals"]
```

在错误边界测试中明确抛错组件返回 JSX：

```tsx
function BrokenComponent(): never {
  throw new Error('test error')
}
```

- [ ] **Step 3: 清理构建级类型错误**

删除 `Dashboard`、`SentimentPanel`、`SupplierProfilePage` 中未使用的导入、变量和参数；不删除实际展示逻辑。将 tooltip formatter 改为接受 Recharts 可空值：

```tsx
formatter={(value) => [`${Number(value ?? 0)} 分`, '风险评分']}
```

- [ ] **Step 4: 验证构建和测试**

Run: `cd frontend && npm test && npm run build`

Expected: Vitest 2 files / 6 tests PASS；TypeScript 与 Vite build 退出码 0。

- [ ] **Step 5: 提交**

```bash
git add frontend/tsconfig.app.json frontend/src/__tests__/AppErrorBoundary.test.tsx frontend/src/components/Dashboard.tsx frontend/src/components/SentimentPanel.tsx frontend/src/components/SupplierProfilePage.tsx
git commit -m "fix: restore frontend production build"
```

### Task 2: 修复 React 与 ESLint 门禁

**Files:**
- Modify: `frontend/eslint.config.js`
- Modify: `frontend/src/components/AssessView.tsx`
- Modify: `frontend/src/components/ChartRenderer.tsx`
- Modify: `frontend/src/components/ChatView.tsx`
- Modify: `frontend/src/components/LoginPage.tsx`

**Interfaces:**
- Consumes: 现有 URL 参数、聊天 session localStorage、SSE callbacks。
- Produces: 不修改 state 引用、无同步 effect 派生状态的交互实现。

- [ ] **Step 1: 固定失败基线**

Run: `cd frontend && npm run lint`

Expected: FAIL，包含 `set-state-in-effect`、`immutability`、`no-empty`、`no-explicit-any` 和路由 Fast Refresh 报错。

- [ ] **Step 2: 修复 AssessView 派生状态**

初始化 `name` 时继续使用 URL 参数；用 mutation 的 `onSettled` 将 `querying` 设为 `idle`，删除仅用于同步 `isPending` 的 effect。URL 公司变化时仅触发评估，不重复同步同一 state；确保 mutation 依赖稳定。

- [ ] **Step 3: 修复图表纯渲染**

用 reducer 预计算饼图切片角度：

```tsx
const slices = items.map((item, index) => {
  const startRatio = items
    .slice(0, index)
    .reduce((sum, entry) => sum + Number(entry.value ?? 0) / total, 0);
  const startAngle = startRatio * Math.PI * 2 - Math.PI / 2;
  const angle = Number(item.value ?? 0) / total * Math.PI * 2;
  return { item, index, startAngle, endAngle: startAngle + angle, angle };
});
```

渲染阶段不再修改 `cumAngle`。

- [ ] **Step 4: 修复聊天不可变性和 effect**

将 URL 的 `q` 参数用于 `input` 的惰性初始值，并用 effect 只清理 URL 参数。把 `persist` 包装为稳定 callback；所有回调使用：

```tsx
const completedMessages = [...newMessages, assistantMessage];
persist(sessionId, completedMessages);
```

禁止对来自 state 派生的数组调用 `push`，并为 localStorage catch 提供显式忽略注释或日志，消除空 block。

- [ ] **Step 5: 修复剩余 lint**

将 `LoginPage` 的异常类型从 `any` 改为 `unknown` 并做类型收窄。针对 `routes.tsx` 同时导出路由数据和组件的既有职责，只在 ESLint flat config 中对该文件关闭 `react-refresh/only-export-components`，不全局关闭该规则。

- [ ] **Step 6: 验证所有前端门禁**

Run: `cd frontend && npm test && npm run lint && npm run build`

Expected: 三条命令全部退出码 0，ESLint 0 errors。

- [ ] **Step 7: 提交**

```bash
git add frontend/eslint.config.js frontend/src/components/AssessView.tsx frontend/src/components/ChartRenderer.tsx frontend/src/components/ChatView.tsx frontend/src/components/LoginPage.tsx
git commit -m "fix: satisfy React quality gates"
```

### Task 3: 修复 Compose 配置与后端就绪检查

**Files:**
- Create: `backend/tests/test_health.py`
- Modify: `backend/app/api/health.py`
- Modify: `backend/app/db/init_pg.py`
- Modify: `.env.docker.example`
- Modify: `docker-compose.yml`
- Modify: `start.sh`
- Modify: `stop.sh`

**Interfaces:**
- Produces: `readiness(response: Response) -> dict[str, object]`，健康时 200，不健康时 503。
- Consumes: `get_db().command("ping")`、`redis.from_url(...).ping()`、`get_cursor()` 执行 `SELECT 1`。

- [ ] **Step 1: 写 readiness 失败测试**

创建不启动应用 lifespan 的单元测试，直接调用异步端点并 monkeypatch 三项依赖：

```python
def test_readiness_returns_503_when_postgres_is_unavailable(monkeypatch):
    monkeypatch.setattr(health, "get_db", fake_healthy_mongo)
    monkeypatch.setattr(health.redis, "from_url", fake_healthy_redis)
    monkeypatch.setattr(health, "get_cursor", failing_cursor)
    response = Response()

    result = asyncio.run(health.readiness(response))

    assert response.status_code == 503
    assert result["checks"]["postgres"] == "unavailable"
```

再增加三项均健康时状态码 200、检查值均为 `ok` 的测试。

- [ ] **Step 2: 验证 RED**

Run: `cd backend && pytest tests/test_health.py -v`

Expected: FAIL，因为 readiness 没有 `Response` 参数和 PostgreSQL检查。

- [ ] **Step 3: 实现 readiness**

从 `app.db.postgres` 导入 `get_cursor`，使用同步 context manager 执行 `SELECT 1`。三项检查结束后：

```python
response.status_code = 200 if healthy else 503
return {
    "status": "ready" if healthy else "not_ready",
    "checks": checks,
}
```

确保 Redis client 在 `finally` 中关闭。

- [ ] **Step 4: 修复初始化错误日志**

将标准 logging 调用改为：

```python
logger.exception("PostgreSQL schema initialization failed: %s", e)
```

继续以 `RuntimeError` 包装原始异常。

- [ ] **Step 5: 修复 Compose 环境传递与健康检查**

在 `.env.docker.example` 增加 `REDIS_PASSWORD`，并使用：

```dotenv
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
PG_HOST=postgres
```

后端 healthcheck 改为 Python 标准库访问 `/health/ready`：

```yaml
test:
  - CMD
  - python
  - -c
  - "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/ready', timeout=3)"
```

`start.sh` 和 `stop.sh` 统一定义显式环境文件并执行：

```bash
docker compose --env-file "$ENV_FILE" config --quiet
docker compose --env-file "$ENV_FILE" up -d --build
```

环境文件不存在时退出 1；启动前备份失败继续保留警告但不吞掉输出。

- [ ] **Step 6: 验证后端与 Compose**

Run: `cd backend && pytest tests/test_health.py -v`

Expected: PASS。

Run: `cp .env.docker.example /tmp/supplisense-compose.env && docker compose --env-file /tmp/supplisense-compose.env config --quiet`

Expected: 退出码 0，且不输出密钥内容。

Run: `bash -n start.sh stop.sh backup.sh restore.sh`

Expected: 退出码 0。

- [ ] **Step 7: 提交**

```bash
git add backend/tests/test_health.py backend/app/api/health.py backend/app/db/init_pg.py .env.docker.example docker-compose.yml start.sh stop.sh
git commit -m "fix: make compose startup readiness reliable"
```

### Task 4: 全量回归与交付检查

**Files:**
- Modify: `docs/superpowers/plans/2026-07-28-build-compose-readiness.md`

**Interfaces:**
- Consumes: Tasks 1–3 的提交。
- Produces: 可审计的最终验证结果。

- [ ] **Step 1: 前端完整门禁**

Run: `cd frontend && npm test && npm run lint && npm run build`

Expected: 全部退出码 0。

- [ ] **Step 2: 后端可隔离测试**

Run: `cd backend && pytest tests/test_health.py tests/test_clarification.py tests/test_risk_service.py -v`

Expected: 全部退出码 0；不连接真实数据库。

- [ ] **Step 3: 部署静态验证**

Run: `docker compose --env-file /tmp/supplisense-compose.env config --quiet && bash -n start.sh stop.sh backup.sh restore.sh`

Expected: 全部退出码 0。

- [ ] **Step 4: 变更完整性检查**

Run: `git diff --check && git status --short`

Expected: 无空白错误，只包含计划内变更或干净工作区。

- [ ] **Step 5: 更新计划与提交**

将已验证步骤勾选，提交：

```bash
git add docs/superpowers/plans/2026-07-28-build-compose-readiness.md
git commit -m "docs: record build and compose verification"
```
