# SuppliSense — 采购助手与供应商风险监控平台

SuppliSense 面向采购团队，把外部企业信息、上市公司财报、内部交易月度快照和历史供应商关系，整理为可追溯的寻源建议与供应商风险监控结果。

它不是供应商准入或主数据维护系统。系统的目标是帮助采购人员回答三件事：

1. 某个物料或品类有哪些历史供应商和可验证的外部候选？
2. 我负责的供应商本轮发生了什么风险变化？
3. 依据是什么，还需要向谁补充或核实什么？

## 当前产品边界

| 能力 | 当前行为 |
| --- | --- |
| 采购助手 | 通过自然语言获取寻源建议、财务/舆情/合规/ESG/司法/趋势等专项分析；回答按主能力维度路由，并返回结论、证据、数据边界与下一步建议。 |
| 风险监控 | 面向采购复核的监控对象工作台，保存主体身份、数据覆盖、风险快照、待办动作、审批、执行结果和证据回写。 |
| 智能寻源 | 优先召回历史合作供应商和飞书正式主数据，再展示盖世等外部待验证候选；外部候选可结合天眼查进行主体与风险核验。 |
| 供应商库 | 飞书多维表格供应商主数据的只读快照与画像查看。 |
| 风险通知 | 为已确认的监控对象生成风险信号，按采购员/科室经理隔离站内通知、实时推送和飞书自建应用逐人消息，并记录投递重试结果。 |
| 不在范围内 | 自动供应商准入、自动写回飞书主数据、以收货记录数推断零件数量或交付能力、无证据的风险结论。 |

## 角色与数据隔离

供应商责任分配以飞书多维表格为来源，并同步到 PostgreSQL 快照。当前规则是：

| 角色 | 可见范围 |
| --- | --- |
| 采购员 | 仅本人负责的正式供应商、监控对象、风险快照和告警。 |
| 科室经理 | 所属科室采购员负责的供应商。 |
| 管理员 | 全量演示数据与运维功能。 |

每家正式供应商只分配给一名采购员。Agent 在调用正式供应商读取工具前也会校验同一责任范围；越权查询会被拒绝，不能借由对话获取其他采购员的数据。

## 核心流程

### 1. 风险监控与复核

```text
输入企业名称或从供应商库选择
  → AI 检索本地主数据与外部主体候选
  → 用户确认主体
  → 创建带稳定 ID 的监控对象
  → 汇集财报、天眼查、交易月度快照等证据
  → 输出：发现了什么 → 依据是什么 → 采购人员需要核实什么
  → 创建复核任务；需要写入的动作经过人工审批
  → 执行结果、证据与风险快照回写到该监控对象
```

监控对象没有主体候选时，页面会明确要求补充统一社会信用代码、天眼查链接或供应商代码；不会把名称相近的企业自动绑定。

### 2. 智能寻源

```text
输入物料号、物料名称或品类
  → 历史零件—供应商关系召回
  → 飞书正式供应商快照匹配
  → 外部候选库（例如盖世整理数据）补充
  → 天眼查主体与风险核验
  → 展示“历史合作供应商”与“外部待验证候选”及其来源和待核验项
```

外部候选只是寻源线索，不等于已准入供应商，也不会自动写入正式供应商主数据。

### 3. 对话可靠性

普通聊天统一进入 Harness Runtime。每一轮运行的计划、工具结果、证据和最终结论都持久化；浏览器 SSE 在最终结果前断开时，前端会按运行 ID 自动回放未消费事件，避免用户因网络波动重复提交。

## 数据来源与解释边界

| 来源 | 用途 | 不能据此直接得出的结论 |
| --- | --- | --- |
| 天眼查 API | 工商主体、司法、经营异常等风险核验 | 不能替代供应商现场审计或质量结论。 |
| 上市公司财报 | 盈利、偿债、现金流等财务变化 | 非上市主体没有财报时，应提示补充资料。 |
| 采购月度快照 | 交易连续性、结算金额或收货记录数变化 | 收货记录数不等于零件数量、采购金额或交付能力。 |
| 飞书供应商主数据 | 正式供应商、责任归属、科室与采购经理 | 本系统只同步读取，不维护主数据。 |
| 外部候选库 | 品类/零件的外部寻源线索 | 候选须经主体、资质和采购流程核验。 |
| 公开公告与信用来源 | 盖世汽车、中国汽车工业协会、巨潮资讯、沪深北港交易所公告、市场监管总局、中国政府采购网、第一财经汽车频道和财新汽车 | 各来源按 `ok`、`no_results`、`not_configured`、`fetch_failed` 区分覆盖状态；媒体报道只作为舆情信号，访问受限或未查询不等于无风险。信用中国和执行信息公开网适配器暂不进入活动采集链路。 |

舆情分析会在公开列表命中后继续抓取可访问的文章详情页，保留原文链接和正文摘录；LLM 逐条输出情感判断、摘要和判断依据。详情页不可访问时保留列表摘要，并在页面标明“列表摘要”，不伪装成完整原文。

结算或收货数据的环比波动只是一条复核信号。系统会同时显示数据覆盖和待补充资料，避免把信息缺口包装成低风险结论。

## 技术架构

```text
React 19 + Vite
  ├─ 采购助手（SSE + 断线回放）
  ├─ 风险监控对象工作台
  ├─ 智能寻源与供应商库
  └─ TanStack Query + HttpOnly Cookie 认证

FastAPI
  ├─ Harness Runtime：唯一的只读聊天执行入口
  ├─ Agent Supervisor：仅处理需人工确认的本地写操作
  ├─ ToolExecutor：工具输入、责任范围、预算、超时与证据契约
  ├─ 风险 / 寻源 / 监控 / 供应商 / 通知领域服务
  └─ 飞书同步与定时任务

PostgreSQL：用户、责任快照、Session / Turn / Run / Task / Proposal
MongoDB：业务数据、风险快照、告警、展示证据
Redis：缓存与限流
DeepSeek：意图与结论生成
```

历史 ReAct、Plan-Execute、Parallel 等图只保留给定向回归测试；活动聊天不会按 mode 切换到这些历史执行内核。

## 目录说明

```text
backend/app/
├── api/                 # 跨领域路由，聊天入口在 api/chat.py
├── domains/             # supplier / sourcing / alert / risk / auth 等领域
├── graphs/harness/      # 当前唯一的只读聊天运行时
├── graphs/agent_core/   # 会话、实体、计划、证据和答案契约
├── graphs/agent_supervisor/ # 有人工审批的写操作编排
├── tools/               # 统一 ToolRegistry / ToolExecutor
└── services/            # 框架无关的跨领域服务

frontend/src/
├── components/          # 页面与展示组件
├── hooks/               # 查询与实时状态 hooks
├── api.ts               # HTTP、SSE 与断线回放
├── routes.tsx           # SPA 路由
└── types.ts             # 集中类型定义
```

## 本地启动

### 前置条件

- Python 3.12
- Node.js 20+
- PostgreSQL、MongoDB、Redis
- DeepSeek API Key（需要真实 AI 问答时）
- 可选：天眼查 API、飞书自建应用和飞书多维表格

首次启动时，将 `backend/.env.example` 复制为 `backend/.env`，填写数据库连接与需要启用的数据源密钥。不要提交 `.env` 或任何真实密钥。

```bash
# 首次初始化后端独立 Python 环境
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cd ..

# 启动基础设施（开发模式）
./start.sh --dev

# 终端 1：后端
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 终端 2：前端
cd frontend
npm install
npm run dev -- --host 0.0.0.0
```

打开 `http://127.0.0.1:5174`。后端就绪检查：`http://127.0.0.1:8000/health/ready`。如本地后端使用其他端口，在 `frontend/.env.local` 设置相同的 `SUPPLISENSE_BACKEND_PORT` 后再启动 Vite。

局域网演示可使用 `http://<本机局域网 IP>:5174`。开发环境的 `backend/.env` 必须设置 `COOKIE_SECURE=false`，否则浏览器不会在 HTTP 地址保存登录 Cookie；生产环境使用 HTTPS 时必须恢复为 `true`。

### Docker Compose

```bash
./start.sh
./stop.sh
```

生产配置由根目录 `.env.docker` 管理，开发配置使用 `backend/.env`。两者均不应提交到 Git。

## 飞书数据同步

配置以下环境变量后开启 `FEISHU_BITABLE_ENABLED=true`：

```text
FEISHU_APP_ID
FEISHU_APP_SECRET
FEISHU_BITABLE_APP_TOKEN
FEISHU_SUPPLIER_MASTER_TABLE_ID
FEISHU_SUPPLIER_ASSIGNMENT_TABLE_ID
```

责任分配表至少应包含供应商、采购员、科室、采购经理和有效状态。人员字段或同步后的邮箱用于匹配系统账号；系统同步人员 `open_id`，通知按采购员身份投递，科室经理收到所属科室汇总。

风险通知使用飞书自建应用的用户消息接口。除多维表格配置外，还需确保应用已开通发送用户消息权限，并设置 `FEISHU_USER_MESSAGE_ENABLED=true`。每次发送会在 MongoDB `notification_deliveries` 中记录 `success` / `failed` 和尝试次数；没有可用 `open_id` 或应用凭据时不会退化为全局风险群发。

管理员可触发只读同步：

```bash
curl -X POST -H "Authorization: Bearer <access-token>" \
  http://localhost:8000/api/v1/sourcing/suppliers/sync
```

同步只读取飞书，写回正式供应商主数据不属于本项目范围。

## 常用页面

| 页面 | 路径 | 建议演示动作 |
| --- | --- | --- |
| 采购助手 | `/chat` | 获取寻源建议，或查询财务、舆情、合规、ESG、司法、趋势和供应链关系。 |
| 总览 | `/` | 查看责任范围内的风险摘要与告警。 |
| 智能寻源 | `/sourcing` | 对比历史供应商与外部待验证候选。 |
| 风险监控 | `/assess` | 调查主体、确认绑定、查看风险依据和处理复核任务。 |
| 供应商库 | `/suppliers` | 查看正式主数据和供应商画像。 |

## 验证

```bash
# 后端
cd backend
source .venv/bin/activate
python -m pytest -m agent_e2e -v
python -m pytest tests/test_agent_harness_p0.py tests/test_agent_supervisor_graph.py -q

# 前端
cd frontend
npm run lint
npm test -- --run
npm run build
```

真实浏览器验证至少覆盖：登录与退出、采购员责任范围、跨范围供应商拒绝、主体确认、风险监控任务审批、寻源结果来源标识，以及聊天 SSE 断线后的结果回放。

## 文档

- [使用说明书](docs/user-manual.md)
- [项目说明书](docs/project-description.md)
- [需求说明书](docs/requirements-specification.md)
- [UI 改造计划与验收记录](docs/ui-improvement-plan-2026-09-12.md)
- [Agent Harness 当前实施计划](docs/superpowers/plans/2026-09-03-agent-harness-hardening-plan.md)
- [问题与修复记录](docs/issue-log.md)
- [飞书供应商导入映射](docs/integration/feishu-import-converter.md)
- [Agent 验收与评估](docs/release/agent-acceptance-and-evaluation.md)
- [项目协作与代码规则](AGENTS.md)

当前活动 Agent 运行时以 `docs/superpowers/plans/2026-09-03-agent-harness-hardening-plan.md` 为准；历史 V2/Supervisor 设计不再作为新开发依据。
