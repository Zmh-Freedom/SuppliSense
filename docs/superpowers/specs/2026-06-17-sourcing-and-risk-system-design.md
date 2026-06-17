# 基于 AI 的供应商智能寻源与风险预警体系设计文档

**日期：** 2026-06-17
**状态：** 已确认
**范围：** 项目重新定位 — 从"供应商风险分析"升级为"智能寻源 + 风险预警体系"；智能寻源完整设计 + 风险预警增量增强 + 顶层升级路线图

---

## 1. 项目重新定位与目标

### 1.1 定位升级

项目从"供应商风险分析智能体"升级为"基于 AI 的供应商智能寻源与风险预警体系"，两条主线并行：

- **智能寻源主线（新增）：** 采购需求驱动 → 向量检索候选池 → 并行风险评估 → 排序推荐 → 入监控/准入
- **风险预警主线（原有，增强）：** 复用现有 15 个风险工具，增强主动预警与监控闭环，与寻源结果联动

### 1.2 核心目标

- **寻源：** 用户提采购需求（品类/规格/预算/地域/资质/数量），AI 在 30 秒内返回 Top-N 排序候选 + 风险理由 + 加入监控/准入入口
- **预警：** 监控列表企业风险变化自动推送，并与寻源结果联动（高风险供应商触发"建议替代"通知，24h 限频）
- **体系：** 本地供应商库 + 天眼查补充 + ChromaDB 向量检索 + LangGraph 子图编排

### 1.3 本次设计覆盖范围

- 智能寻源完整设计（数据模型 / 子图 / 工具 / API / 前端）
- 风险预警体系与寻源的联动增量改动
- 顶层升级路线图（替代原 agent-capability-enhancement 与 enterprise-upgrade 两份 plan 的拆解逻辑）
- 文档整理方案

### 1.4 非目标

- 不写详细任务级实施 plan（后续 writing-plans 产出）
- 不重写已有风险工具内部实现
- 不做多级审批工作流引擎（准入流程仅做最小闭环）
- 不做市场扫描式发现（P4 可选演进）

---

## 2. 数据模型

### 2.1 MongoDB 新增集合

#### suppliers — 本地供应商主库

```python
{
    "_id": ObjectId,
    "name": str,                    # 企业全称
    "unified_code": str,            # 统一社会信用代码（唯一业务键）
    "categories": list[str],        # 主营品类（如 ["光电器件", "视频监控"]）
    "regions": list[str],           # 经营地域
    "qualifications": [             # 资质证书
        {"name": str, "level": str, "expire_at": datetime}
    ],
    "scale": {                      # 规模
        "employees": int | None,
        "revenue_range": str | None,
        "registered_capital": str | None,
    },
    "contact": {                    # 联系方式
        "contact_person": str | None,
        "phone": str | None,
        "email": str | None,
    },
    "status": str,                  # prospective / approved / blocked / deprecated
    "rating": int | None,           # 内部评级 1-5（准入后维护）
    "source": str,                  # manual / tianyancha / sourcing_import
    "tianyancha_id": str | None,    # 天眼查企业 ID
    "last_enriched_at": datetime | None,
    "embedding_dirty": bool,        # 资料变更后需重新 embedding
    "created_at": datetime,
    "updated_at": datetime,
}
```

#### sourcing_requests — 采购寻源请求

```python
{
    "_id": ObjectId,
    "user_id": str,
    "title": str,                   # 需求标题
    "category": str,                # 采购品类
    "spec": str,                    # 规格/技术要求（自然语言）
    "budget_range": {               # 预算范围
        "min": float | None,
        "max": float | None,
        "currency": str,            # 默认 CNY
    },
    "quantity": int | None,
    "region_required": str | None,  # 期望地域
    "qualifications_required": list[str],  # 要求的资质
    "status": str,                  # draft / searching / done / cancelled
    "result_count": int,
    "created_at": datetime,
    "completed_at": datetime | None,
    "conversation_id": str | None,  # 从聊天发起时关联会话
}
```

#### sourcing_results — 寻源结果明细

一次寻源请求 → N 条结果，拆单独集合便于历史查询与对比。

```python
{
    "_id": ObjectId,
    "request_id": ObjectId,         # 关联 sourcing_requests
    "supplier_id": ObjectId,        # 关联 suppliers
    "match_score": float,           # 向量检索分 0-1
    "risk_score": float,            # 综合风险分 0-100
    "risk_level": str,              # low / medium / high / critical / unknown
    "final_rank": float,            # 综合排序分（加权）
    "match_reason": str,            # LLM 生成的匹配理由
    "risk_summary": str,            # LLM 生成的风险摘要（含子维度说明）
    "risk_breakdown": {             # 风险子维度评分
        "financial": float,
        "sentiment": float,
        "esg": float,
        "sanctions": float,
        "contagion": float,
        "macro": float,
    } | None,
    "selected": bool,               # 用户是否勾选
    "action": str | None,           # none / watchlist / apply_access
    "created_at": datetime,
}
```

#### access_applications — 准入申请

```python
{
    "_id": ObjectId,
    "supplier_id": ObjectId,
    "request_id": ObjectId | None,  # 关联寻源请求（可选）
    "applicant_id": str,            # 申请人
    "status": str,                  # pending / approved / rejected
    "reviewer_id": str | None,
    "reviewed_at": datetime | None,
    "created_at": datetime,
}
```

### 2.2 ChromaDB 新增 collection

```
supplier_profiles
├── id: supplier_id（与 suppliers._id 一致）
├── document: 拼接文本 = 名称 + 品类 + 资质 + 地域 + 规模 + 主营业务描述
├── embedding: 由现有 retriever/embedding 流程生成
└── metadata: {category, region, status}
```

### 2.3 索引与唯一键约定

- `suppliers.unified_code` — 唯一索引（业务去重键）
- `suppliers.categories` — 多键索引
- `suppliers.status` — 普通索引
- `sourcing_requests.user_id + created_at` — 复合索引
- `sourcing_results.request_id + final_rank` — 复合索引
- `access_applications.status + created_at` — 复合索引

天眼查补充时：先按 `unified_code` 查本地库，未命中才调天眼查并入库（`source=tianyancha`），避免重复调用。

### 2.4 Embedding 重建机制

- `suppliers.embedding_dirty=true` 的记录由 APScheduler 每天凌晨重建 embedding
- 重建后写入 ChromaDB `supplier_profiles`，标记 `embedding_dirty=false`
- 新增/编辑供应商时自动标记 `embedding_dirty=true`

---

## 3. 智能寻源主线设计

### 3.1 LangGraph Sourcing Subgraph 编排

新增 `backend/app/graphs/agents/sourcing.py`，由 `graphs/router.py` 根据意图路由分发。

```
                    ┌──────────────────────────┐
                    │  parse_requirement       │  把需求文本/表单 → SourcingRequestInput
                    │  (LLM 结构化抽取)        │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  retrieve_candidates     │  ChromaDB 向量检索 Top-K（K=20）
                    │  (并行分支起点)          │  本地库未覆盖时触发天眼查补充
                    └────────────┬─────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
   ┌──────────▼─────────┐  ┌────▼─────────┐  ┌─────▼──────────┐
   │ assess_risk_batch  │  │ enrich_profile│  │ check_sanctions│
   │ (并行调用 risk     │  │ (天眼查补全   │  │ (制裁名单快查) │
   │  service 异步并发) │  │  缺失字段)    │  │                │
   └──────────┬─────────┘  └────┬─────────┘  └─────┬──────────┘
              │                  │                  │
              └──────────────────┼──────────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  rank_and_explain        │  并行结果汇总
                    │  - 加权 final_rank        │  生成 match_reason / risk_summary
                    │  - LLM 生成自然语言解释   │  持久化 sourcing_results
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  respond                 │  SSE 推送 Top-N（默认 N=10）
                    │  (流式)                  │  返回结构化结果 + 可勾选 action
                    └──────────────────────────┘
```

### 3.2 节点职责与并行策略

| 节点 | 输入 | 输出 | 实现要点 |
|------|------|------|----------|
| `parse_requirement` | 需求文本/表单 JSON | `SourcingRequestInput` | 聊天入口走 LLM 抽取；表单入口直接构造。缺字段时返回 clarify 事件 |
| `retrieve_candidates` | `SourcingRequestInput` | `list[supplier_id]` (Top-20) | ChromaDB `supplier_profiles` 检索；本地库覆盖度 < 阈值（可配置，默认 50%）时同步触发天眼查补充入库 |
| `assess_risk_batch` | `list[supplier_id]` | `dict[supplier_id, risk_result]` | `asyncio.gather` 并发调用 `risk_service.assess_risk` + `esg_service` + `sentiment`；单家超时 30s 跳过，标记 unknown |
| `enrich_profile` | `list[supplier_id]` | `dict[supplier_id, enriched_profile]` | 对 `embedding_dirty=false` 跳过；其余调天眼查补全 |
| `check_sanctions` | `list[supplier_id]` | `dict[supplier_id, sanctions_hit]` | 复用现有 `sanctions_service` |
| `rank_and_explain` | 上述汇总 | `list[SourcingResult]` (Top-10) | 加权公式 + LLM 生成自然语言解释；持久化 sourcing_results |
| `respond` | 排序结果 | SSE 流 | 复用 `graphs/streaming.py` 事件格式；新增 `sourcing_result` 事件类型 |

**关键设计决策：**

1. **三个并行节点用 LangGraph 原生 fan-out** — `retrieve_candidates` 之后分三路并行，最后 join 到 `rank_and_explain`
2. **风险批量评估走 service 层而非 tool 层** — 直接 `asyncio.to_thread` + `asyncio.gather` 调 `risk_service`，不通过 LangGraph tool 调用。service 层框架无关（CLAUDE.md 约定），且批量调用避免 tool 调用开销
3. **超时与降级** — `assess_risk_batch` 单家 30s 超时；3 家以上超时时，已成功的返回，超时的标记 `risk_level=unknown` 但仍参与排序（用最低匹配分兜底）
4. **与风险主图解耦** — sourcing subgraph 不调用 risk 主图，只调用底层 service；用户问纯风险问题时仍走原 risk ReAct 图

### 3.3 加权排序公式与降级策略

**加权公式：**
```
final_rank = 0.6 × match_score + 0.4 × (1 - risk_score / 100)
```

- `match_score`: ChromaDB 向量检索余弦相似度，0-1
- `risk_score`: 风险综合评分，0-100（0 最安全，100 最高风险）
- 制裁命中（`check_sanctions` 有结果）直接降权到末位，`final_rank` 乘 0.1

**降级策略：**
- 风险评估超时的供应商：`risk_score` 按 50（中等风险）计算
- 天眼查补充失败的供应商：仅用本地已有信息参与排序，`match_reason` 中说明"资料不完整"
- ChromaDB 无匹配结果时：返回空列表，SSE 推送 `thinking` 事件说明"本地库未找到匹配供应商，建议补充供应商主库"

### 3.4 新增 Tool 注册

新增 tool 注册到 `app/tools/__init__.py`（供 LangGraph 使用）和 `agent.py` 的 `TOOLS`（供旧模式使用），双架构共存期两处注册。

| Tool | 参数 | 返回 | 说明 |
|------|------|------|------|
| `create_sourcing_request` | `title, category, spec, budget_range?, quantity?, region_required?, qualifications_required?` | `request_id` | 创建寻源请求 |
| `search_suppliers` | `request_id` | `list[SourcingResult]` | 同步阻塞版（给旧 ReAct 用） |
| `select_sourcing_result` | `result_id, action: "watchlist" \| "apply_access"` | `bool` | 勾选结果执行动作 |

**新增文件：**

```
backend/app/graphs/agents/sourcing.py      # 子图定义
backend/app/graphs/router.py               # 意图路由
backend/app/schemas/sourcing.py            # Pydantic 模型
backend/app/services/sourcing_service.py   # 寻源业务逻辑（被 tool 包装）
backend/app/repositories/supplier_repo.py  # 供应商数据访问层
backend/app/repositories/sourcing_repo.py  # 寻源数据访问层
```

---

## 4. 风险预警体系增强（增量）

### 4.1 监控列表风险变化 → 建议替代

`alert_notifier.py` 已有每小时预警检查。新增逻辑：

```
当 watchlist 企业 risk_score 上升 ≥10 分 或 升级到 high/critical：
  1. 推送原有 risk_alert WebSocket 通知
  2. 新增 sourcing_suggestion 通知：
     - 异步触发 sourcing subgraph（用该企业主营品类作为寻源需求）
     - 找出 Top-3 替代候选
     - 推送 {type: "sourcing_suggestion", company, alternatives: [...]}
  3. 通知存入 notifications 集合
  4. 前端可点击"查看替代方案"跳转寻源结果页
```

**24h 限频：** 同一企业 24 小时内最多推送一次 `sourcing_suggestion` 通知。已有 `notifications` 集合中新增 `last_sourcing_suggestion_at` 字段，每次推送前检查。

### 4.2 寻源结果 → 监控 / 准入闭环

`sourcing_results.action` 字段支持两种动作：

| 动作 | 触发 | 后续流程 |
|------|------|----------|
| `watchlist` | 用户勾选"加入监控" | 调 `alert_service.add_to_watchlist(supplier_name)`，纳入每小时预警检查 |
| `apply_access` | 用户勾选"申请准入" | 创建 `access_applications` 记录（状态 `pending`），推送审批通知给 admin；审批通过后 `suppliers.status` 改为 `approved` |

准入流程本期做"记录 + 通知 + 状态变更"的最小闭环，不做审批工作流引擎。

### 4.3 风险子维度透出

现有 `risk_service.assess_risk` 返回综合分。新增字段透出各子维度：

```python
risk_breakdown: {
    "financial": float,      # 财务风险
    "sentiment": float,      # 舆情风险
    "esg": float,            # ESG 风险
    "sanctions": float,      # 制裁风险
    "contagion": float,      # 传染风险
    "macro": float,          # 宏观风险
}
```

写入 `sourcing_results.risk_summary`，供 LLM 生成自然语言解释时引用。

### 4.4 与原 agent-capability-enhancement 计划的整合映射

| 原计划项 | 新顶层 spec 中的位置 |
|----------|---------------------|
| 阶段一 架构重构（function calling / 流式 / 并行 / 错误恢复 / 结果管理） | 合并到"工程质量底座"章节（§6） |
| 阶段二 新工具接入（报告/趋势/对比/财务/定时报告） | 合并到"风险工具补全"（§6.5） |
| 阶段三 4.2 主动预警 | 与本节 §4.1 合并，强化为"风险-寻源联动预警" |
| 阶段三 4.1/4.3/4.4/4.5（重规划/上下文/追问/偏好） | 合并到 §8 "Agent 智能增强"，优先级降低 |

---

## 5. API 与前端

### 5.1 新增 API 端点

`backend/app/api/sourcing.py`，挂载到 `/api/v1/sourcing`：

```
POST   /sourcing/requests                  # 创建寻源请求（表单入口）
       body: SourcingRequestInput
       returns: { request_id, status }

POST   /sourcing/requests/{id}/search      # 触发寻源（SSE 流式）
       returns: text/event-stream
       events: thinking | retrieving | assessing | ranking | sourcing_result | done | error

GET    /sourcing/requests                  # 当前用户寻源历史
       query: page, page_size, status
       returns: { items, total }

GET    /sourcing/requests/{id}             # 单次寻源详情（含结果列表）

POST   /sourcing/results/{id}/select       # 勾选结果
       body: { action: "watchlist" | "apply_access" }
       returns: { success, message }

POST   /sourcing/suppliers                 # 手动录入供应商到本地库
GET    /sourcing/suppliers                 # 供应商主库查询
PUT    /sourcing/suppliers/{id}            # 编辑供应商资料（触发 embedding_dirty）

POST   /sourcing/access-applications/{id}/approve   # 准入审批（admin）
POST   /sourcing/access-applications/{id}/reject
```

### 5.2 聊天入口意图路由

复用 `/api/v1/chat/stream`，`graphs/router.py` 在 chat 入口判断意图：

- 含"找供应商 / 寻源 / 替代 / 采购"等关键词或 LLM 分类为 sourcing → 路由到 sourcing subgraph
- 否则走原 risk ReAct 图

聊天内的寻源走相同 SSE 事件类型，前端 `ChatView` 监听 `sourcing_result` 事件渲染卡片（含勾选按钮），与表单页结果页共用 `SourcingResultCard` 组件。

### 5.3 前端新增组件

扁平结构，放 `frontend/src/components/`：

```
SourcingPage.tsx              # 寻源 Tab 主页：表单 + 提交 + 历史侧栏
SourcingForm.tsx              # 采购需求表单（品类/规格/预算/地域/资质/数量）
SourcingResultList.tsx        # 结果列表 + 排序 + 勾选
SourcingResultCard.tsx        # 单条结果卡片（匹配分/风险分/理由/勾选按钮）
SourcingHistory.tsx           # 历史寻源请求列表
SupplierLibraryPage.tsx       # 供应商主库管理页（增删改查）
AccessApplicationList.tsx     # 准入审批列表（admin 可见，折叠在 SupplierLibraryPage 子区）
```

Tab 路由调整（`routes.tsx`）：

```
TAB_ROUTES 新增：
  /sourcing       → SourcingPage
  /suppliers      → SupplierLibraryPage（含准入审批子区）
```

### 5.4 SSE / WebSocket 事件扩展

**SSE 新增事件类型（注册到 `graphs/streaming.py`）：**

| 事件 | 数据 | 说明 |
|------|------|------|
| `sourcing_result` | `{request_id, results: [{supplier_id, name, match_score, risk_score, final_rank, match_reason, risk_summary}]}` | 寻源结果推送 |
| `retrieving` | `{message}` | 检索阶段状态 |
| `assessing` | `{message}` | 风险评估阶段状态 |
| `ranking` | `{message}` | 排序阶段状态 |

**WebSocket 新增事件：**

| 事件 | 数据 | 说明 |
|------|------|------|
| `sourcing_suggestion` | `{company, alternatives: [{name, match_score, risk_level}], request_id}` | 风险变化触发的替代建议 |

前端：`sourcing_suggestion` 事件触发 `queryClient.invalidateQueries(['sourcing', 'history'])` + 顶部通知栏提示。

### 5.5 前端样式约定

- Tailwind utility classes only，复用现有色值（`#333` / `#555` / `#e8e8e3` / `#f5f5f0` / `#fafaf8`）
- 圆角统一 `rounded-xl` / `rounded-2xl`
- 风险等级颜色复用现有 Dashboard 的高/中/低风险配色
- 寻源结果卡片采用现有 ChatView 消息卡片风格，保持视觉一致

---

## 6. 工程质量底座

本章节引用 `docs/superpowers/plans/2026-06-16-enterprise-upgrade-zh.md`（保留为子文档，不删除），不重复展开。

### 6.1 安全加固

引用原计划阶段一（5 任务）：外部化凭据 / Cookie 安全与 CORS / 密码验证 / Docker 加固 / 天眼查 HTTPS。

### 6.2 测试基础设施

引用原计划阶段二 + 阶段三（6 任务）：Pytest 配置 / 认证测试 / Mock / Vitest / API 测试 / 组件冒烟测试。

### 6.3 代码质量与运维

引用原计划阶段四 + 阶段五（5 任务）：死代码清理 / 依赖清理 / SSE 日志 / 请求日志中间件 / .dockerignore。

### 6.4 与寻源/风险功能的依赖关系

安全加固（§6.1）必须在寻源功能开发前完成，否则新增 API 端点无基本安全保障。

### 6.5 风险工具补全（原 agent-enhancement 阶段二）

5 个新工具接入仍按原计划执行：报告生成 / 趋势分析 / 多企业对比 / 财务数据查询 / 定时报告。优先级在寻源 MVP 之后。

---

## 7. LangGraph 迁移衔接

### 7.1 Sourcing Subgraph 与现有迁移阶段的关系

当前 LangGraph 迁移进度（阶段一+二完成）：`graphs/` 下已有 ReAct 图定义 + tool 注册机制。Sourcing subgraph 依赖：

- LangGraph 迁移阶段三完成（路由机制就绪）— `graphs/router.py` 需要意图路由能力
- `app/tools/__init__.py` 的 `@tool` 注册机制已可用

### 7.2 双架构共存期处理

寻源相关 tool 注册到两处（`TOOLS` dict + `@tool` 装饰器），与 CLAUDE.md 约定一致。旧 ReAct / Plan-Execute 模式可通过 `search_suppliers` tool 使用寻源能力（同步阻塞版）。

### 7.3 迁移完成后旧编排代码清理时点

当所有模式验证通过后，删除 `agent.py` 中旧 `_register` / `_try_parse` / prompt-based 工具调用，统一走 LangGraph。此为 P3 后期工作。

---

## 8. Agent 智能增强（保留自原计划，优先级降低）

以下功能源自原 agent-capability-enhancement 阶段三，保留设计但排在寻源之后实施。

### 8.1 动态重规划（Plan-Execute 模式）

执行器根据中间结果动态调整计划。触发条件：工具返回列表结果需为每项执行后续步骤；工具返回空结果需调整后续步骤。最多 2 次重规划。

### 8.2 上下文摘要与多轮记忆

对话超过 8 轮时，用 LLM 对前 4 轮生成摘要，摘要作为 system message 注入。关键实体（公司名、风险指标）从摘要中提取。

### 8.3 自动追问澄清

用户意图模糊时返回澄清问题而非猜测。系统提示词增加澄清规则，`_process_response()` 处理 `clarify` 类型响应。

### 8.4 用户偏好学习

`user_preference.py` 存储用户关注行业、偏好指标、报告格式、最近查询企业。Agent 生成回答时注入偏好，推荐替代供应商时优先考虑用户关注行业。

---

## 9. 实施阶段排序

```
P0（前置，必须先做）
  ├─ 工程质量底座 · 安全加固（enterprise-upgrade 阶段一 5 任务）
  └─ LangGraph 迁移阶段三/四完成（router + 子图机制就绪）

P1（寻源主线 MVP）
  ├─ 数据模型：suppliers + sourcing_requests + sourcing_results 集合 + 索引
  ├─ ChromaDB supplier_profiles collection + embedding 重建调度
  ├─ sourcing_service.py + supplier_repo.py + sourcing_repo.py
  ├─ 3 个新 tool（create_sourcing_request / search_suppliers / select_sourcing_result）
  ├─ sourcing subgraph（parse → retrieve → 并行三路 → rank → respond）
  └─ /api/v1/sourcing 端点 + SourcingPage 表单入口

P2（寻源闭环 + 风险联动）
  ├─ access_applications 集合 + 准入审批端点
  ├─ SourcingResultCard 勾选 → watchlist / apply_access
  ├─ alert_notifier "建议替代" 通知（24h 限频）
  └─ 聊天入口意图路由

P3（Agent 智能增强 + 工程质量收尾）
  ├─ 动态重规划 / 上下文摘要 / 自动追问 / 偏好学习
  ├─ 风险工具补全（报告/趋势/对比/财务/定时报告）
  ├─ 工程质量底座 · 测试基础设施 + 代码质量 + 运维
  └─ PPT 同步更新

P4（可选，未来演进）
  ├─ 准入流程多级审批工作流
  ├─ sourcing subgraph 演化为 multi-agent
  └─ 市场扫描式发现（无明确需求的候选池扩充）
```

---

## 10. 验证方式

| 阶段 | 验证标准 |
|------|----------|
| P0 | 应用缺少必需环境变量时拒绝启动；LangGraph router 可根据意图分发到不同子图 |
| P1 | 用户在 SourcingPage 填写采购需求提交后，30 秒内返回 Top-10 排序候选 + 风险理由；本地供应商库无匹配时返回说明 |
| P2 | 监控列表企业风险上升时自动推送替代建议（24h 同企业限一次）；勾选"加入监控"后企业出现在 Watchlist |
| P3 | 长对话不丢失上下文；模糊查询时 Agent 追问；5 个新风险工具可通过对话调用 |
| P4 | 准入审批支持多级流程；市场扫描定期发现新候选 |

---

## 11. 风险与权衡

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| ChromaDB 冷启动无供应商数据 | P1 寻源返回空结果 | 提前批量导入供应商主库数据 + 初始 embedding |
| 风险批量评估耗时超标 | 寻源超 30s 目标 | 单家 30s 超时降级；评估节点并行；后期可引入缓存 |
| 天眼查 API 限流 | 补充资料失败 | 限流保护 + 降级为本地信息排序 |
| LLM 意图路由误判 | 寻源意图走错图 | 关键词匹配优先，LLM 兜底；用户可手动切换 |
| 24h 限频规则误判 | 真正需要替代通知时被跳过 | 限频仅针对 `sourcing_suggestion`，原有 `risk_alert` 不受限 |

---

## 附录：旧文档处理方案

| 文件 | 处理 |
|------|------|
| `specs/2026-06-16-agent-capability-enhancement-design.md` | 内容整合进本 spec §4/§6/§8，原文件归档到 `docs/archive/` |
| `plans/2026-06-16-agent-capability-enhancement.md` | 整合后删除（详细任务级 plan 由后续 writing-plans 重新生成） |
| `plans/2026-06-16-enterprise-upgrade-zh.md` | 保留为子文档，本 spec §6 引用；不删除 |
| `presentation/2026-06-16-project-progress.md` | 本期不改；后续 P3 阶段同步更新 |
