# SuppliSense 项目阶段性报告

**日期：** 2026-06-22  
**版本：** v0.3.0  
**代码量：** 13,450 行 Python + 前端 React SPA  

---

## 一、项目概述

基于 AI 的供应商智能寻源与风险预警平台。采购分析师通过自然语言查询供应商风险数据，Agent 自动调用工具链完成分析。

**技术栈：** FastAPI + MongoDB + PostgreSQL(pgvector) + DeepSeek + LangGraph + React

## 二、已完成功能

### 2.1 核心能力（25 个 AI 工具）

| 类别 | 工具 | 说明 |
|------|------|------|
| 数据查询 | `search_company`, `tianyancha_query` | 企业搜索 + 天眼查全接口 |
| 风险评估 | `assess_risk`, `esg_assessment`, `predict_risk`, `macro_risk` | 13 维度评分体系 |
| 舆情分析 | `sentiment_analysis`, `contagion_analysis` | 舆情+传染分析 |
| 合规筛查 | `check_sanctions`, `scenario_simulate` | 制裁/情景模拟 |
| 替代推荐 | `find_alternatives` | 同行业低风险替代 |
| 分析报告 | `generate_report`, `analyze_trend`, `compare_companies`, `query_financials`, `manage_scheduled_report` | Excel/HTML/对比/趋势/定时 |
| 预警监控 | `check_alert`, `get_watchlist`, `add_to_watchlist`, `remove_from_watchlist` | 完整 CRUD |
| 智能寻源 | `create_sourcing_request`, `search_suppliers`, `select_sourcing_result`, `expand_supplier_library` | 搜索→评估→闭环 |
| 知识库 | `knowledge_search` | RAG 检索 |

### 2.2 Agent 编排（4 种模式）

| 模式 | 架构 | 适用场景 |
|------|------|----------|
| ReAct | LangGraph StateGraph | 单步查询，灵活调用 25 工具 |
| Plan-Execute | LangGraph planner→executor→replanner | 多步骤任务，支持动态重规划 |
| Multi-Agent | LangGraph Supervisor + 3 专业 Agent | 多角度分析（风险/舆情/合规协作） |
| Sourcing | LangGraph 独立子图 | 采购寻源专属流程 |

- IntentRouter 自动分流：关键词优先（零延迟）→ LLM 分类兜底
- 全 LangGraph 架构，旧 Agent 代码已清理（-710 行）
- SSE 流式输出，支持 Thinking/Tool Call/Answer Chunk 事件

### 2.3 智能寻源（P1-P2）

```
采购需求 → 向量检索 (PG cosine) → MongoDB 快照查分 → 加权排序 → Top-10 推荐
    ↓
  勾选操作：加入监控 / 申请准入
    ↓
  管理员审批 → 供应商状态联动更新
    ↓
  风险上升 → 自动推送替代建议（24h 限频）
```

- 供应商主库：19 家种子数据 + Excel 批量导入 + 天眼查搜索导入
- 准入审批：完整审批流程，通过/拒绝后自动更新供应商状态
- 预警联动：风险评分上升 ≥10 或等级升级 → 自动寻找 Top-3 替代

### 2.4 前端

- React 19 + Vite 8 + TypeScript + Tailwind CSS
- 10 个页面：Dashboard / 企业评估 / 智能寻源 / 供应商库 / Agent 聊天 / 关系图谱 / 设置 / 登录
- TanStack Query 状态管理 + SSE 流式接收 + WebSocket 实时推送
- 寻源页：三步进度指示器（检索→评估→排序）、空结果提示、勾选反馈

## 三、工程质量

### 3.1 数据库设计

```
suppliers (MongoDB)        supplier_profiles (PG)
  ├── _id (UUID, 主键)      └── embedding (384 维)
  ├── name (唯一索引)
  ├── unified_code (唯一索引)
  ├── legal_person / registered_capital / establish_time
  └── supplier_id → 统一外键

watchlist / access_applications / alert_snapshots / alerts
  └── supplier_id → 关联 suppliers
```

- Pydantic 文档校验层：Repository 写入前类型检查
- 供应商变更审计：`supplier_changelog` 集合
- 天眼查 API 调用计数：`api_call_logs` 集合

### 3.2 并发与性能

| 改动 | 效果 |
|------|------|
| Gunicorn 4 workers × 2 threads | 并发处理能力 ×8 |
| PG 连接池 4-20 per worker | 消除 DB 瓶颈 |
| AsyncMongoClient 原生异步 | 不再消耗线程池 |
| Redis 缓存 (assess_risk 2h TTL) | 热点数据毫秒级 |
| LLM 统一工厂 + 3 次重试 + 60s 超时 | DeepSeek API 稳定性 |
| 寻源改用 MongoDB 快照查分 | 避免批量 API 调用超时 |

### 3.3 代码架构

```
domains/          # 按业务领域聚合（61 files）
├── risk/        (30)  风险评估全栈
├── sourcing/    (7)   寻源全栈
├── alert/       (6)   预警全栈
├── auth/        (6)   认证全栈
└── knowledge/   (6)   知识库全栈

graphs/          (8)   LangGraph 编排层
tools/           (1)   25 工具聚合入口
api/             (6)   跨领域路由
services/        (7)   跨领域服务
core/            (9)   配置/安全/日志
db/              (3)   MongoDB/PG 连接
schemas/         (5)   Pydantic 模型
```

- 领域聚合完成：改一个功能只需操作一个目录
- Tools 拆分：528 行单体 → 7 个独立文件
- Old Agent 代码清理：752 → 42 行（-94%）
- 48 条测试（含业务逻辑测试）

### 3.4 安全与运维

- JWT 认证 + 3 级角色（admin/analyst/viewer）
- BCrypt 密码哈希
- 全局限流 60/min + 认证限流 5/min
- CORS 白名单 + Cookie Secure
- Prometheus 指标 + Sentry 异常监控
- 请求 ID 追踪 + 结构化日志
- Docker Compose 生产部署

## 四、数据现状

| 数据 | 数量 |
|------|------|
| 供应商主库 | 24 家（13 家已补全工商信息） |
| 监控企业 | 15 家 |
| 天眼查 API 调用 | 记录在 api_call_logs |
| PG 向量 | 24 条 supplier_profiles |
| MongoDB 集合 | 30+ 个业务集合 |

## 五、已知限制

| 限制 | 说明 |
|------|------|
| 天眼查免费套餐 | 搜索接口不可用，部分企业无数据 |
| 供应商规模 | 24 家，需持续扩充 |
| 测试覆盖率 | 48 条（偏少），缺少集成测试 |
| 前端测试 | 6 条，仅覆盖基础渲染 |
| 负载能力 | ~120 req/s（简单查询），需持续监控 |

## 六、后续方向

| 优先级 | 事项 |
|--------|------|
| 高 | 定时预评估（凌晨跑全库→寻源真实分） |
| 高 | 企查查/天眼查企业套餐升级 |
| 中 | 后端集成测试 + 前端 E2E |
| 中 | 告警通知多样化（邮件/企业微信） |
| 低 | 微服务拆分（团队 > 3 人时） |
| 低 | 多级审批工作流 |

---

**总结：** 项目已完成 P0-P3 全部工程目标。Agent 拥有 25 个工具、4 种编排模式，覆盖采购风险分析全链路。代码架构经过领域聚合和冗余清理，生产环境具备 Gunicorn 多 worker + Redis 缓存 + LLM 重试的基础设施。当前阶段的核心瓶颈是数据规模（供应商数量），而非工程能力。
