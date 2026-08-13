# Codex 工作规则

你是本项目的资深企业级软件工程师。

## 项目概述

供应商风险分析平台，采购分析师通过自然语言查询供应商风险数据。后端 FastAPI + MongoDB + PostgreSQL(pgvector)，LLM 使用 DeepSeek（OpenAI 兼容 API），前端 React。

## 技术栈

- **Web:** FastAPI, Uvicorn, Pydantic v2
- **数据库:** MongoDB (PyMongo 同步驱动), PostgreSQL + pgvector (用户/知识库/审计), Redis (缓存)
- **LLM:** DeepSeek API (openai SDK), LangGraph + langchain-openai
- **调度:** APScheduler (定时任务)
- **实时:** WebSocket (预警推送), SSE (对话流式)

## 架构分层

```
app/
├── api/           # API 路由层（FastAPI routers）
├── tools/         # LangGraph @tool 工具定义（包装 service 层）
├── graphs/        # LangGraph 图定义（编排层）
│   └── agents/    # 子 agent 图（Multi-Agent 模式）
├── services/      # 业务逻辑层
├── repositories/  # 数据访问层
├── schemas/       # Pydantic 模型定义
├── db/            # 数据库连接管理
└── core/          # 配置、认证、缓存、依赖注入
```

## 当前状态：LangGraph 架构

编排层已全部迁移到 LangGraph，旧手写编排代码已清理。

| mode | 架构 | 状态 |
|------|------|------|
| `"react"` / `"langgraph-react"` | LangGraph ReAct 图 | 生产默认 |
| `"plan-execute"` / `"langgraph-plan-execute"` | LangGraph Plan-Execute 图 | 已迁移 |
| `"multi-agent"` / `"langgraph-multi-agent"` | LangGraph Supervisor 图 | 已迁移 |
| `"auto"` | IntentRouter → LangGraph | 自动选择 |

- 旧 `react`/`plan-execute`/`multi-agent` 模式名自动归一化到 LangGraph 对应图
- `agent.py` 中的 `chat()` 作为同步端点回退保留
- `agent.py` 中的 `_load_history`/`_save_turn`/`TOOLS` 由 LangGraph 图共享
- **新功能优先在 LangGraph 架构上开发**（`graphs/` + `tools/`）
- **service 层不改** — 保持框架无关

## 后端代码约定

### 命名规范

- **文件/函数/变量:** snake_case（`risk_service.py`, `assess_risk`, `company_name`）
- **类:** PascalCase（`RiskCalculateRequest`, `CompanyProfile`）
- **常量:** UPPER_SNAKE_CASE（`RISK_TIMEOUT_SECONDS`）
- **私有函数:** 前缀 `_`（`_calc_score`, `_score_to_level`）
- **MongoDB 字段:** 数据库字段名可能用 camelCase（如 `legalPersonName`），但 Python 代码中一律 snake_case

### 导入规范

- 导入顺序：标准库 → 第三方库 → `app.*`，组间空行
- 顶层导入优先；仅在以下情况使用延迟导入（函数内 `from app.xxx import`）：
  - 打破循环依赖
  - 跨 service 调用（如 `risk_service.py` 内 `from app.services.alert_service import get_watchlist`）
  - `main.py` 中避免模块级循环导入

### 错误处理

- **API 层** (`api/`): `raise HTTPException(status_code=xxx, detail="中文描述")`
- **Service 层** (`services/`): `raise ValueError("描述")` 用于业务逻辑错误，返回 `None` 表示未找到
- **Repository 层** (`repositories/`): 返回 `None` 表示未找到，不抛异常
- **全局错误格式:** `app/core/errors.py` 提供统一 JSON 信封 `{"error": {"code": "...", "message": "...", "detail": ...}}`
- **超时模式:** `asyncio.wait_for(..., timeout=60)`，捕获 `TimeoutError` 转为 HTTP 504

### 类型注解

- 公共函数必须有返回类型注解：`def assess_risk(request: RiskAssessRequest) -> RiskCalculateResponse`
- 使用现代联合语法：`str | None`（不用 `Optional[str]`），`list[str]`（不用 `List[str]`）
- Repository 函数返回 `dict`，不强制使用 TypedDict

### API 端点规范

- 使用 `APIRouter()`，挂载到 `/api/v1` 前缀
- 请求体用 Pydantic `BaseModel` 子类
- 同步 service 用 `asyncio.to_thread()` 包装
- 端点文档用中文：`summary`, `description`, `responses`
- 示例：
  ```python
  @router.post("/assess", summary="风险评估", responses={504: {"description": "超时"}})
  async def assess(req: RiskAssessRequest):
      return await asyncio.to_thread(assess_risk, req)
  ```

### 工具注册

新增工具在 `app/tools/__init__.py` 用 `@tool` 装饰器定义，加入 `TOOLS_LIST`。同时需要在 `app/services/agent.py` 中用 `@_register` 注册（供同步端点回退使用）。

### SSE 事件格式

流式输出统一使用 `_sse_event(event_type, data)` 格式，事件类型：`thinking`, `tool_call`, `tool_result`, `answer_chunk`, `done`, `error`。修改事件格式需同步 `graphs/streaming.py`。

### Service 函数约定

- 所有 service 函数返回 `dict`
- 14/15 个工具以 `company_name: str` 为主参数
- 使用 `from app.db.mongo import get_db` 获取同步数据库连接
- 需要异步调用时用 `asyncio.to_thread()` 包装
- 日志使用 `from app.core.logging import get_logger; logger = get_logger()`，用关键字参数：`logger.info("risk_assessed", company=company_name, score=score)`

### 测试规范

- 框架：pytest + FastAPI TestClient
- 命名：`test_<功能>_<场景>`（如 `test_risk_calculate_low_risk`）
- Mock 方式：`monkeypatch.setattr("app.services.xxx.func", mock_func)`
- 断言：直接 `assert resp.status_code == 200` + `assert resp.json()["field"] == value`
- 测试文件放在 `backend/tests/`，与被测模块对应

---

## 前端开发规范

### 技术栈

- **框架:** React 19 + Vite 8（SPA，非 SSR）
- **语言:** TypeScript（非 strict 模式，但启用 `noUnusedLocals` + `noUnusedParameters`）
- **状态管理:** TanStack Query 5（服务端状态）+ useState（本地状态）+ localStorage（持久化）
- **路由:** react-router-dom v7（`createBrowserRouter`）
- **样式:** Tailwind CSS v4（纯 utility classes，无 CSS modules / styled-components / 组件库）
- **图表:** recharts（柱状图/折线图）、@xyflow/react（关联图谱）
- **Markdown:** react-markdown（聊天消息渲染）
- **HTTP:** 原生 fetch 封装（`api.ts`），无 axios
- **实时:** SSE（ReadableStream 手动解析）、WebSocket（自定义 WSClient 指数退避重连）
- **Lint:** ESLint 10 + typescript-eslint + react-hooks，无 Prettier

### 目录结构

```
frontend/src/
  main.tsx          # 入口，QueryClient + Router 配置
  routes.tsx        # 路由定义（TAB_ROUTES 数组）
  api.ts            # fetch 封装 + SSE 流式
  types.ts          # 所有 TypeScript 接口（集中定义）
  query-keys.ts     # TanStack Query key factory
  websocket.ts      # WebSocket 客户端
  index.css         # 全局样式（Tailwind 引入）
  components/       # 页面级 + UI 组件（扁平结构）
  hooks/            # 自定义 hooks
```

不要创建 `services/`、`utils/`、`store/`、`pages/`、`features/` 目录，保持扁平。

### 命名规范

- **组件文件:** PascalCase（`Dashboard.tsx`, `ChatView.tsx`）
- **非组件文件:** camelCase（`api.ts`, `types.ts`, `websocket.ts`）
- **Hooks:** camelCase + `use` 前缀（`useWatchlist.ts`）
- **组件名:** PascalCase，default export（`export default function Dashboard()`）
- **变量/函数:** camelCase（`queryClient`, `assessMutation`）
- **类型/接口:** PascalCase（`RiskResult`, `ChatMessage`）
- **常量:** UPPER_SNAKE_CASE（`SESSION_KEY`, `API_BASE`）

### 组件规范

- 全部使用函数组件 + Hooks（唯一例外：`ErrorBoundary` 类组件）
- 每个组件一个文件，放在 `src/components/`
- 页面组件可内联 `useQuery`/`useMutation`，不必都抽到 hooks
- 小型辅助组件定义在父组件同文件内（如 `SummaryCard`, `Metric`）
- Props 用内联接口类型：`function Foo({ name, count }: { name: string; count: number })`

### 数据获取规范

- 服务端数据统一用 TanStack Query：`useQuery` + `useMutation`
- Query key 集中在 `query-keys.ts` 管理
- QueryClient 配置：`staleTime: 60_000`, `retry: 1`, `refetchOnWindowFocus: false`
- WebSocket 事件触发缓存失效：`wsClient.on('alert_update', () => queryClient.invalidateQueries(...))`
- 客户端持久化用 localStorage（chat_sessions, session, 选中企业等）

### 样式规范

- 只用 Tailwind utility classes，不用 CSS modules
- 颜色用已有设计系统色值：`#333`（主文字）、`#555`（次要文字）、`#e8e8e3`（边框）、`#f5f5f0`（背景）、`#fafaf8`（页面底色）
- 圆角统一 `rounded-xl` / `rounded-2xl`
- 动态值用 inline style（如风险分数颜色）
- Markdown 渲染用 `@tailwindcss/typography` 的 `prose` 类

### API 调用规范

- 使用 `src/api.ts` 的 `api.get<T>()` / `api.post<T>()` 等方法
- SSE 流式用 `chatStream()` 函数，通过 `StreamCallbacks` 回调处理事件
- 后端 API 前缀 `/api/v1`，开发代理配置在 `vite.config.ts`

---

## 开发原则

- 优先编写可运行代码
- 优先 FastAPI 最佳实践
- 优先类型注解
- 优先单元测试
- 每次修改先说明方案
- 多功能修改时边改边提交，相关修改合并为一个提交

## 禁止

- 修改已有 API 返回结构
- 删除测试代码
- 引入未使用依赖
- 在 service 层引入 LangGraph 依赖（service 层保持框架无关）
- 在前端使用 CSS modules / styled-components / 组件库（统一 Tailwind）
- 在前端引入 Redux / Zustand（统一 TanStack Query + useState）
- 在前端引入 axios（统一 fetch 封装）
