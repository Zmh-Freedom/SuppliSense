# SuppliSense — AI-Powered Supplier Sourcing & Risk Intelligence

基于 LangGraph 的供应商智能寻源与风险预警平台。覆盖**风险评估 → 预警监控 → 智能寻源 → 供应商画像 → 舆情追踪 → ESG 评分 → 合规筛查 → 情景模拟**完整链路，支持自然语言交互。

---

## 系统架构

```
浏览器 ──→ Nginx (:80)
              ├── /            → React SPA (静态文件)
              ├── /api/v1/*    → FastAPI (:8000)
              ├── /ws          → WebSocket (实时推送)
              └── /health      → 健康检查

FastAPI ──→ LangGraph Agent 编排层
         │     ├── ReAct (单步查询，灵活调用 25 工具)
         │     ├── Plan-Execute (多步骤任务，动态重规划)
         │     ├── Multi-Agent (Supervisor + 3 专业 Agent 协作)
         │     ├── Parallel (Map-Reduce 并行分析)
         │     ├── ReAct+Reflection (自反思纠错)
         │     └── Sourcing (寻源专属子图)
         │
         ├── DeepSeek LLM (langchain-openai, 3 次重试, 60s 超时)
         ├── MongoDB (企业数据 / 快照 / 告警 / 对话历史)
         ├── PostgreSQL + pgvector (用户 / 知识库 / 向量检索 / 评估历史)
         ├── Redis (缓存 / 限流)
         ├── 天眼查 API (工商 / 司法 / 经营)
         ├── AkShare (A股 / 港股财报)
         ├── DuckDuckGo (新闻搜索)
         └── 飞书 Webhook (告警推送)
```

---

## 快速开始

### 生产模式（Docker Compose）

```bash
./start.sh    # docker compose up -d --build
```

打开 `http://localhost`，默认账号 `admin / 见启动日志中的随机密码`。

### 开发模式

```bash
docker compose --env-file backend/.env \
  -f docker-compose.dev.yml up -d mongo postgres redis  # 仅启动开发基础设施
cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000  # 本地后端热更新
cd frontend && npm run dev                                      # 前端热更新
```

后端访问 `http://localhost:8000`，前端访问 `http://localhost:5173`。开发环境后端使用 `backend/.env` 中的 `localhost` 数据库地址；完整后端容器仅用于生产模式。

### P1 企业身份与 Transactional Outbox

P1 在 PostgreSQL 中维护企业法定主体、别名、核验与逻辑合并，并将企业创建、更新、核验和合并事实与对应 Outbox 事件放在同一事务提交。它**尚未**切换现有风险评估或 MongoDB 供应商库的读写路径；评估不会隐式创建供应商。

Outbox worker 默认启用，可通过以下环境变量调整：

| 配置 | 默认值 | 说明 |
|------|------|------|
| `OUTBOX_WORKER_ENABLED` | `true` | 关闭时只停止消费，不影响企业事实和事件写入 |
| `OUTBOX_POLL_SECONDS` | `5` | 轮询间隔（秒） |
| `OUTBOX_BATCH_SIZE` | `50` | 每批领取事件数 |
| `OUTBOX_MAX_ATTEMPTS` | `8` | 自动重试上限，达到后进入死信 |
| `OUTBOX_LEASE_SECONDS` | `60` | 多 worker 领取事件的租约时长（秒） |

生产环境默认使用 4 个 Gunicorn worker。设置 `PROMETHEUS_MULTIPROC_DIR`（Compose 默认 `/tmp/prometheus` tmpfs）后，`/metrics` 使用 Prometheus multiprocess 聚合；Gunicorn master 会在 fork 前清理旧指标文件，并在 worker 退出后释放其 gauge 文件。每个 API worker 都会尝试取得 PostgreSQL advisory lock，但只有持锁进程启动 APScheduler；锁在进程退出时释放，替代 worker 可接管调度。Uvicorn 单进程开发模式不受影响。

管理员可查看积压、失败和死信事件，并仅对未发布的失败/死信事件回放：

```bash
# status 仅支持 pending、failed、dead_letter；limit 为 1..100
curl -H "Authorization: Bearer <access-token>" \
  "http://localhost:8000/api/v1/admin/outbox/events?status=dead_letter&limit=50"

# 回放会保留既有成功消费记录和尝试次数，并写入管理员与原因审计
curl -X POST -H "Authorization: Bearer <access-token>" \
  -H "Content-Type: application/json" \
  -d '{"reason":"确认下游故障已修复"}' \
  "http://localhost:8000/api/v1/admin/outbox/events/<event-id>/replay"
```

---

## 功能模块（7 个标签页）

| 标签 | 路由 | 功能 |
|------|------|------|
| **风险看板** | `/` | 统计卡片 + 风险分布 + 预警列表 + PMI 宏观指标 |
| **企业评估** | `/assess` | 13 维度评分 + 15 财务指标 + ESG + 制裁 + 传染图谱 + 情景模拟 + Excel/HTML 导出 |
| **智能寻源** | `/sourcing` | 采购需求 → 向量检索 → 并行风险评估 → Top-N 排序推荐 → 监控/准入 |
| **供应商库** | `/suppliers` | 供应商主数据管理 + `/suppliers/:id` 供应商画像（8 领域聚合） |
| **Agent** | `/chat` | 25 工具 AI 对话，6 种编排模式自动路由，SSE 流式 + Human-in-the-Loop 审批 |
| **关系图谱** | `/contagion` | 风险传染路径 + 股权穿透 + 供应链依赖 |
| **设置** | `/settings` | 用户偏好 + 监控清单管理 + 告警规则 + 知识库 |

---

## 技术栈

| 层 | 技术 |
|------|------|
| 后端框架 | FastAPI + Pydantic v2 |
| AI 编排 | LangGraph StateGraph（6 种图模式） |
| LLM | DeepSeek Chat（langchain-openai, OpenAI 兼容 API） |
| 数据库 | MongoDB (pymongo + motor) + PostgreSQL pgvector + Redis |
| 认证 | JWT (HttpOnly Cookie + Refresh Token) + BCrypt |
| 调度 | APScheduler（6 个定时任务） |
| 实时通信 | WebSocket（指数退避重连）+ SSE（对话流式） |
| 日志 | structlog（结构化日志）+ Sentry（异常监控） |
| 监控 | Prometheus metrics + 请求 ID 追踪 |
| 限流 | slowapi（60/min 全局 + 5/min 认证端点） |
| 容器化 | 生产环境 Docker Compose（mongo / postgres / redis / backend / frontend） |
| 前端 | React 19 + Vite 8 + TypeScript + Tailwind CSS 4 |
| 状态管理 | TanStack Query 5（服务端状态）+ localStorage（持久化） |
| 图表 | recharts（趋势图）+ @xyflow/react（关系图谱） |
| Markdown | react-markdown（聊天消息渲染） |
| 数据源 | 天眼查 API / AkShare / DuckDuckGo |
| 通知 | 飞书 Webhook（HMAC-SHA256） |
| CI/CD | GitHub Actions（pytest + tsc + vite build） |

---

## 后端架构

```
app/
├── api/           # 跨领域 API 路由（chat / upload / async_tasks）
├── tools/         # 25 个 LangGraph @tool 工具定义
├── graphs/        # LangGraph 编排层
│   ├── react_graph.py          # ReAct + ReAct-Reflection
│   ├── plan_execute_graph.py   # Plan-Execute
│   ├── supervisor_graph.py     # Multi-Agent Supervisor
│   ├── parallel_graph.py       # Map-Reduce 并行
│   ├── router.py               # IntentRouter 自动分流
│   ├── streaming.py            # SSE 流式适配
│   ├── approval.py             # Human-in-the-Loop 审批
│   ├── context.py              # 长对话摘要压缩
│   ├── reflection.py           # 自反思纠错
│   └── agents/                 # 专业子 Agent
│       ├── risk_agent.py       # 风险分析 Agent
│       ├── sentiment_agent.py  # 舆情分析 Agent
│       ├── compliance_agent.py # 合规筛查 Agent
│       └── sourcing.py         # 寻源子图
├── domains/       # 业务领域（repo → service → api 分层）
│   ├── risk/      # 风险评估 / 舆情 / ESG / 传染 / 制裁 / 宏观 / 替代
│   ├── sourcing/  # 智能寻源 / 准入审批
│   ├── supplier/  # 供应商主数据 / 供应商画像
│   ├── alert/     # 预警监控 / 通知
│   ├── auth/      # 用户认证 / 权限管理
│   └── knowledge/ # 知识库 RAG
├── services/      # 跨领域服务（agent / proactive_agent / scheduler / ws_manager）
├── repositories/  # 数据访问层（旧）
├── schemas/       # Pydantic 模型
├── db/            # MongoDB / PostgreSQL 连接管理
└── core/          # 配置 / 安全 / 缓存 / 日志 / 限流 / 错误处理
```

---

## Agent 工具清单（25 个）

### 数据查询
| 工具 | 说明 |
|------|------|
| `search_company` | 模糊搜索企业全称 |
| `query_financials` | 查询财报指标（15 项） |
| `knowledge_search` | 知识库 RAG 检索 |

### 风险评估
| 工具 | 说明 |
|------|------|
| `assess_risk` | 13 维度风险评估（财报 + 司法 + 经营） |
| `esg_assessment` | ESG 三维评分 (E/S/G) |
| `predict_risk` | 风险恶化预测 |
| `macro_risk` | 行业 + 地区宏观风险 |

### 舆情与合规
| 工具 | 说明 |
|------|------|
| `sentiment_analysis` | 舆情情感分析 + AI 摘要 |
| `contagion_analysis` | 风险传染路径 + 图谱 |
| `check_sanctions` | OFAC/SDN 制裁黑名单筛查 |
| `scenario_simulate` | 4 种情景影响模拟（倒闭/诉讼/中断/质量） |

### 替代与对比
| 工具 | 说明 |
|------|------|
| `find_alternatives` | 同行业低风险替代推荐 |
| `compare_companies` | 多企业横向对比 |
| `analyze_trend` | 风险评分趋势分析 |

### 预警监控
| 工具 | 说明 |
|------|------|
| `check_alert` | 风险预警变化检测 |
| `get_watchlist` | 查看监控清单 |
| `add_to_watchlist` | 加入监控 |
| `remove_from_watchlist` | 移除监控 |
| `analyze_watchlist_trend` | 监控清单趋势分析 |

### 报告
| 工具 | 说明 |
|------|------|
| `generate_report` | 生成 Excel/HTML 报告 |
| `manage_scheduled_report` | 管理定时报告 |

### 智能寻源
| 工具 | 说明 |
|------|------|
| `create_sourcing_request` | 创建采购寻源需求 |
| `search_suppliers` | 供应商搜索 + 排序 |
| `select_sourcing_result` | 选择寻源结果（加入监控/申请准入） |
| `expand_supplier_library` | 扩充供应商库 |

---

## Agent 编排模式（6 种）

| 模式 | 架构 | 适用场景 |
|------|------|----------|
| **ReAct** | LangGraph StateGraph | 单步查询，灵活调用 25 工具 |
| **ReAct+Reflection** | ReAct + Self-Reflection 节点 | 需要答案质量校验的复杂问题 |
| **Plan-Execute** | Planner → Executor → Replanner | 多步骤任务，支持动态重规划 |
| **Multi-Agent** | Supervisor + 3 专业 Agent（风险/舆情/合规） | 多角度协作分析 |
| **Parallel** | Map-Reduce 并行分析 | 多企业批量评估 |
| **Sourcing** | LangGraph 独立子图 | 采购寻源专属流程 |

- **IntentRouter 自动分流**：路由关键词优先（零延迟）→ LLM 分类兜底
- **SSE 流式输出**：支持 `thinking`, `tool_call`, `tool_result`, `answer_chunk`, `approval_required`, `done`, `error` 事件
- **Human-in-the-Loop**：高风险操作（移除监控/申请准入）触发审批中断，可恢复执行
- **长对话摘要**：超过 8 轮自动压缩上下文，避免 token 超限

---

## API 清单（/api/v1 前缀）

### 监控告警 `/api/v1/alert`
| Method | Path | 说明 |
|--------|------|------|
| GET | `/alert/dashboard` | 看板聚合数据 |
| GET | `/alert/watchlist` | 监控清单 |
| POST | `/alert/watch` | 加入监控 |
| POST | `/alert/watch/batch` | 批量加入 |
| POST | `/alert/watch/upload` | Excel 导入 |
| DELETE | `/alert/watch` | 移除监控 |
| POST | `/alert/check-all` | 免费巡检（AkShare） |
| POST | `/alert/refresh-all` | 付费刷新（天眼查） |
| GET | `/alert/history` | 告警历史 |
| GET/PUT | `/alert/rules` | 告警规则 |
| GET | `/alert/predict` | 风险预测 |

### 通知 `/api/v1/notifications`
| Method | Path | 说明 |
|--------|------|------|
| GET | `/notifications` | 通知列表（分页） |
| GET | `/notifications/unread-count` | 未读数量 |
| PUT | `/notifications/{id}/read` | 标记已读 |
| PUT | `/notifications/read-all` | 全部已读 |

### 风险评估 `/api/v1/risk`
| Method | Path | 说明 |
|--------|------|------|
| POST | `/risk/assess` | 企业风险评估 |
| POST | `/risk/calculate` | 原始数据计算 |

### 企业 & 财报 `/api/v1/company` `/api/v1/financial`
| Method | Path | 说明 |
|--------|------|------|
| GET | `/company/search` | 模糊搜索企业 |
| GET | `/company/profile` | 工商信息 |
| GET | `/financial/metrics` | 财务指标 |

### 企业身份 `/api/v1/companies`
| Method | Path | 说明 |
|--------|------|------|
| GET | `/companies/search` | 确定性解析企业身份 |
| GET | `/companies/{company_id}` | 获取规范企业主体（支持合并重定向） |
| POST | `/companies` | 创建待核验企业主体（管理员/分析师） |
| PATCH | `/companies/{company_id}` | 按身份版本更新主体（管理员/分析师） |
| POST | `/companies/{company_id}/verify` | 核验企业主体（管理员） |
| POST | `/companies/{company_id}/merge` | 逻辑合并企业主体（管理员，需确认） |

### Outbox 运维 `/api/v1/admin/outbox`
| Method | Path | 说明 |
|--------|------|------|
| GET | `/admin/outbox/events?status=pending\|failed\|dead_letter&limit=1..100` | 查看确定性排序的未发布事件（管理员） |
| POST | `/admin/outbox/events/{event_id}/replay` | 带原因回放未发布失败/死信事件（管理员） |

### 舆情 `/api/v1/sentiment`
| Method | Path | 说明 |
|--------|------|------|
| GET | `/sentiment/{company}` | 企业舆情分析 |
| GET | `/sentiment/dashboard/overview` | 舆情总览 |
| POST | `/sentiment/analyze` | 触发分析 |

### ESG & 传染 `/api/v1/p2`
| Method | Path | 说明 |
|--------|------|------|
| GET | `/p2/esg/{company}` | ESG 评分 |
| GET | `/p2/contagion/{company}` | 风险传染 |
| GET | `/p2/contagion/{company}/graph` | 传染图谱数据 |
| GET | `/p2/dependencies/{company}` | 供应链依赖 |

### 宏观分析 `/api/v1/analysis`
| Method | Path | 说明 |
|--------|------|------|
| GET | `/analysis/macro/{company}` | 宏观风险（PMI + 地区 + 政策） |
| GET | `/analysis/alternatives/{company}` | 替代企业推荐 |
| GET | `/analysis/scenario/{company}` | 情景模拟 |
| GET | `/analysis/sanctions/{company}` | 制裁筛查 |
| GET | `/analysis/trend/{company}` | 风险趋势 |
| GET | `/analysis/compare` | 多企业对比 |

### 供应商 `/api/v1/suppliers`
| Method | Path | 说明 |
|--------|------|------|
| GET | `/suppliers` | 供应商列表（分页） |
| GET | `/suppliers/{id}` | 主数据详情 |
| PUT | `/suppliers/{id}` | 更新主数据（自动审计） |
| GET | `/suppliers/{id}/profile` | **供应商画像**（8 领域聚合） |
| GET | `/suppliers/{id}/changelog` | 变更审计历史 |

### 智能寻源 `/api/v1/sourcing`
| Method | Path | 说明 |
|--------|------|------|
| POST | `/sourcing/requests` | 创建寻源请求 |
| GET | `/sourcing/requests` | 寻源请求列表 |
| GET | `/sourcing/requests/{id}` | 寻源结果详情 |

### 对话 `/api/v1/chat`
| Method | Path | 说明 |
|--------|------|------|
| POST | `/chat/` | 对话（自动选模式） |
| GET | `/chat/stream` | SSE 流式对话 |
| POST | `/chat/resume` | 恢复暂停的审批中断 |

### 报告 `/api/v1/report`
| Method | Path | 说明 |
|--------|------|------|
| GET | `/report/excel/{company}` | 下载 Excel 报告 |
| GET | `/report/html/{company}` | HTML 报告（可打印 PDF） |

### 知识库 `/api/v1/knowledge`
| Method | Path | 说明 |
|--------|------|------|
| GET | `/knowledge/stats` | 知识库统计 |
| GET | `/knowledge/search` | 向量检索 |
| POST | `/knowledge/upload` | 上传文档 |

### WebSocket `/ws`
服务端主动推送事件：

| 事件 | 触发时机 |
|------|---------|
| `alert` | 新告警产生 |
| `alert_update` | 告警状态变更 |
| `risk_update` | 风险评分更新 |
| `risk_alert` | 风险等级升级（含替代建议） |
| `sentiment_ready` | 舆情分析完成 |
| `task_complete` | 异步任务完成 |
| `sourcing_suggestion` | 高风险供应商替代推荐 |
| `notification` | 系统通知 |
| `proactive_analysis` | 主动监控分析报告 |

---

## 风险评分模型

### 评分维度（13 维，总分 0-100）

**财务风险（7 维，最高 35 分）**：资产负债率、每股现金流、营收/净利增长率、流动/速动比率、ROE、扣非利润占比

**趋势加分（3 维）**：营收 3 年趋势、负债 3 年趋势、应收款周转天数

**司法经营（8 维，最高 65 分）**：诉讼、被执行、失信、重大诉讼、经营异常、行政处罚、对外担保、股权质押、法人变更、破产/清算、环保处罚

### 评级

| 总分 | 等级 |
|------|------|
| 0-30 | 低风险 🟢 |
| 31-60 | 中风险 🟡 |
| 61-100 | 高风险 🔴 |

---

## 定时任务

| 任务 | 频率 | 数据源 |
|------|------|------|
| 免费巡检（财报） | 每日 9:00 | AkShare |
| 付费刷新（全量） | 每周一 9:00 | 天眼查 |
| 飞书日报 | 每日 9:00 | MongoDB 聚合 |
| 舆情巡检 | 每日 10:00 | DuckDuckGo + LLM |
| 告警通知 | 每 30 分钟 | 规则引擎检测 |
| 主动监控 Agent | 每 2 小时 | LLM 生成自然语言分析 |

---

## 目录结构

```
SuppliSense/
├── backend/
│   ├── app/
│   │   ├── api/              # 跨领域路由（chat, upload, async_tasks）
│   │   ├── tools/            # 25 个 LangGraph @tool 工具
│   │   ├── graphs/           # LangGraph 编排层（6 种图模式）
│   │   │   └── agents/       # 专业子 Agent（risk/sentiment/compliance/sourcing）
│   │   ├── domains/          # 业务领域聚合
│   │   │   ├── risk/         # 风险评估 / 舆情 / ESG / 传染 / 制裁 / 宏观
│   │   │   ├── sourcing/     # 智能寻源 / 准入审批
│   │   │   ├── supplier/     # 供应商主数据 / 供应商画像
│   │   │   ├── alert/        # 预警监控 / 通知
│   │   │   ├── auth/         # 认证 / 权限
│   │   │   └── knowledge/    # 知识库 RAG
│   │   ├── services/         # 跨领域服务（agent, scheduler, ws_manager）
│   │   ├── schemas/          # Pydantic 模型（按业务拆分）
│   │   ├── db/               # MongoDB / PostgreSQL 连接管理
│   │   ├── core/             # 配置 / 安全 / 缓存 / 日志 / 限流 / 错误处理
│   │   └── tasks/            # Celery 遗留（已废弃，保留兼容）
│   ├── tests/                # pytest 测试（48 条）
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── main.tsx          # 入口（QueryClient + Router）
│       ├── routes.tsx        # 路由定义（7 个标签页 + 2 个详情页）
│       ├── api.ts            # HTTP 客户端 + SSE 流式
│       ├── websocket.ts      # WebSocket 客户端（指数退避重连）
│       ├── types.ts          # TypeScript 类型定义
│       ├── query-keys.ts     # TanStack Query key factory
│       └── components/       # 20 个 React 组件（扁平结构，按页面划分）
├── docs/                     # 文档 / 设计 / 演示材料
├── nginx/nginx.conf          # 反向代理（gzip + 安全头部 + WebSocket）
├── docker-compose.yml        # 生产模式
├── docker-compose.dev.yml    # 开发模式（热重载）
├── Dockerfile.backend
├── Dockerfile.frontend
├── .github/workflows/ci.yml  # CI/CD 流水线
├── start.sh
└── stop.sh
```

---

## 代码规模

| 层 | 行数 | 文件数 |
|------|------|------|
| 后端 Python | ~16,300 | ~100 |
| 前端 TypeScript | ~5,800 | ~20 组件 |
| 测试 | 48 条 | pytest + Vitest |

---

## 文档索引

## Agent V2 离线评估与灰度发布

Task 14 提供不依赖 MongoDB、PostgreSQL、Redis、LLM 或外部 Provider 的固定评估集：

```bash
cd backend
pytest -q tests/test_sourcing_risk_evals.py
python -c 'from app.evals.sourcing_risk import run_sourcing_risk_evals; import json; print(json.dumps(run_sourcing_risk_evals("tests/evals/sourcing_risk_cases.json"), ensure_ascii=False, indent=2))'
```

评估报告包含需求解析质量、本地优先发现、身份/证据安全、决策/审批边界、恢复 fail-closed、候选 precision/recall、citation/evidence completeness、unsafe action rate、clarification rate 和 p50/p95/max latency。固定 12 个场景覆盖完整本地流、澄清、外部候选暂存/审批导入、身份歧义、制裁不可用、财务缺失、证据冲突、审批重放、重启恢复、未知 recovery 和越权授权。

### V2 feature flags

安全默认值为 `AGENT_RUN_V2_ENABLED=false`、`AGENT_RUN_V2_ROLLOUT=shadow`、`AGENT_RUN_V2_CANARY_PERCENT=0`。环境变量含义：

| Flag | 值 | 行为 |
|---|---|---|
| `AGENT_RUN_V2_ENABLED` | `true/false` | 总开关，关闭时始终走 legacy |
| `AGENT_RUN_V2_ROLLOUT` | `shadow/internal/canary/default` | 灰度阶段 |
| `AGENT_RUN_V2_CANARY_PERCENT` | `0..100` | Canary 按 user ID 的稳定 SHA-256 bucket 放量 |
| `AGENT_RUN_V2_ROLLOUT_STATE` | `active/rollback_frozen` | 回滚冻结时拒绝新 V2/Shadow 创建、恢复和审批；保留已有 Run/checkpoint/audit |

| 阶段 | 创建/恢复 API | 用户响应 | 领域动作 | 放行条件 |
|---|---|---|---|---|
| disabled | legacy/拒绝 V2 | legacy | legacy API 语义 | `AGENT_RUN_V2_ENABLED=false`，不创建/调度 V2 Run |
| shadow | 拒绝创建/恢复可执行 V2 | 409 read-only | 禁止领域写入、澄清、身份动作与审批 | 真实 trace 通过 `trace_adapter` 注入；无 adapter 时报告标记 `deterministic_fallback` |
| internal | `admin`/`analyst` 进入 V2 | V2 | 仍需人工审批 + approved-only Outbox | 角色 gate |
| canary | 稳定 hash 命中者进入 V2 | V2 | 仍需人工审批 + approved-only Outbox | `AGENT_RUN_V2_CANARY_PERCENT` |
| default | 全部用户进入 V2 | V2 | 仍需人工审批 + approved-only Outbox | promotion guard + 审批记录 |

Shadow 不创建或恢复可执行 V2 graph；create、澄清恢复、身份恢复和审批均 fail-closed，避免领域写入。Internal/Canary/Default 的 route decision 控制进入 V2 graph，任何业务写入仍必须经过人工审批和 approved-only Outbox。`rollback_frozen` 对所有新建、恢复和审批请求 fail-closed；在途 Run 保留 checkpoint 并暂停新步骤，pending proposal 冻结并人工复核，leased Outbox 停止新 lease，已有 lease 完成或过期后再验证恢复。当前进程通过 `rollback_rollout` latch 立即冻结，promotion 通过完整证据和审批后解除冻结。

### 灰度门槛、观测和回滚

Shadow → Internal → Canary → Default 逐阶段推进。Promotion 必须同时具备完整观测窗口、当前阶段最小样本（Shadow 12、Internal 50、Canary 100）、人工审批记录（approver/decision/record_id）、在途 Run/proposal/outbox 清零，以及离线 Eval 评分 100%、macro precision/recall ≥95%、citation/evidence completeness 100%、clarification accuracy 100%、关键缺证据误推荐 0%、身份唯一命中精确率 ≥99%、需求字段准确率 ≥95%、关键证据支持率 ≥98%、未审批写入 0%、重复动作 0%、恢复成功率 ≥99%、实际 P95 首事件 ≤2 秒、本地候选 P95 完成 ≤90 秒。latency 是 trace 的 `end - start`，实际值越低越好；缺失或负值直接拒绝。Prometheus 低基数承诺仅适用于 Task 14 新增 V2 metrics；历史 metrics（包括既有 company label）不在本任务修改范围。

发现主体错绑、制裁不可用仍推荐、未经审批写入、恢复重复执行、评分不可复现或证据不一致时立即停止推进。回滚入口必须先调用 `rollback_rollout(settings, stage, reason, in_flight)`，将 `AGENT_RUN_V2_ROLLOUT_STATE=rollback_frozen`，再由人工处理在途状态；不得删除已有 Run、checkpoint、审批或审计记录。恢复前需有新的人工审批记录、proposal/outbox 处置结果、V2 Eval/metrics 窗口验证和 `agent_run_v2_route()` 矩阵验证，之后才可调用 `promote_rollout(...)` 或重新设置 active。

| 文档 | 说明 |
|------|------|
| `CLAUDE.md` | 开发规范（AI 助手用） |
| `docs/project-status-report-2026Q2.md` | 2026 Q2 阶段报告 |
| `docs/manual-test-issues.md` | 手动测试问题汇总（20+ 问题） |
| `docs/superpowers/specs/2026-06-17-sourcing-and-risk-system-design.md` | 智能寻源 + 风险预警体系设计 |
| `docs/superpowers/plans/2026-06-16-enterprise-upgrade-zh.md` | 企业级升级实施计划 |
| `docs/SuppliSense_项目评审_v3.pdf` | 项目评审演示 |
