# 供应商风险分析 Agent

基于 ReAct 架构的企业供应商风险智能分析系统。覆盖**风险评估 → 预警监控 → 舆情追踪 → ESG 评分 → 替代建议 → 情景模拟**完整链路，支持自然语言交互。

---

## 系统架构

```
浏览器 ──→ Nginx (:80)
              ├── /            → React SPA (静态文件)
              ├── /api/v1/*    → FastAPI (:8000)
              ├── /ws          → WebSocket (实时推送)
              └── /health      → 健康检查
                  
FastAPI ──→ DeepSeek LLM (ReAct Agent)
         ├── MongoDB (企业数据 / 快照 / 告警)
         ├── Redis (缓存 / Celery broker)
         ├── 天眼查 API (工商 / 司法 / 经营)
         ├── AkShare (A股 / 港股财报)
         ├── DuckDuckGo (新闻搜索)
         └── 飞书 Webhook (告警推送)
```

## 快速开始

### 生产模式（Docker Compose）

```bash
./start.sh    # docker compose up -d --build
```

打开 `http://localhost`，默认账号 `admin / admin123`。

### 开发模式

```bash
docker compose -f docker-compose.dev.yml up -d   # 启动数据库 + 后端
cd frontend && npm run dev                        # 启动前端（热更新）
```

打开 `http://localhost:5173`。

---

## 功能模块（6 个标签页）

| 标签 | 功能 | 说明 |
|------|------|------|
| **风险看板** | 全局总览 | 统计卡片 + 风险分布 + 预警信号 + 风险矩阵 |
| **企业评估** | 单企业深度分析 | 评分 + 15 财务指标 + 司法经营指标 + 7 个展开分析 + 导出报告 |
| **告警中心** | 变更监控 | 快照对比 + 自定义规则触发 + 飞书推送 |
| **舆情监控** | 新闻追踪 | 搜索 + LLM 情感分类 + AI 摘要 + WebSocket 实时推送 |
| **智能对话** | AI 助手 | 14 工具 ReAct Agent，支持 SSE 流式响应 |
| **知识库** | 文档检索 | PDF/Word/Excel 上传 + ChromaDB 向量检索 + RAG |

### 企业评估页功能

| 功能 | 说明 |
|------|------|
| 风险评分 | 0-100 分 + 三级评级 + 评分明细 |
| 财务指标 | 营收/净利/负债率/ROE/现金流等 15 项 + 3 年趋势 |
| ESG 评分 | 环境(E) / 社会(S) / 治理(G) 三维 |
| 宏观风险 | 行业 PMI + 地区信用 + 政策标签 |
| 替代建议 | 同行业低风险企业推荐 |
| 风险传染 | 分支/子公司 + 供应链依赖 + **交互式图谱** |
| 情景模拟 | 4 种情景（倒闭/诉讼/中断/质量）影响评估 |
| 制裁筛查 | OFAC/SDN + 失信被执行人 |
| 舆情分析 | 新闻情感 + 风险标签 + AI 摘要 |
| **导出报告** | Excel 下载 + HTML 报告（可打印 PDF） |

---

## 技术栈

| 层 | 技术 |
|------|------|
| 后端框架 | FastAPI + Pydantic v2 |
| AI 引擎 | DeepSeek（OpenAI 兼容）+ ReAct / Plan-Execute / Multi-Agent |
| 数据库 | MongoDB + Redis |
| 向量检索 | ChromaDB + sentence-transformers |
| 容器化 | Docker Compose（mongo / redis / backend / nginx） |
| 反向代理 | Nginx（gzip + 安全头部 + WebSocket） |
| 实时通信 | WebSocket（替换轮询） |
| 认证 | JWT HttpOnly Cookie + Refresh Token |
| 定时任务 | APScheduler |
| 数据源 | 天眼查 API / AkShare / DuckDuckGo |
| 通知 | 飞书 Webhook（HMAC-SHA256） |
| 前端 | Vite + React 19 + TypeScript + Tailwind CSS 4 + React Flow |
| CI/CD | GitHub Actions（pytest + tsc + vite build） |
| 监控 | Prometheus + Sentry + structlog |

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

### 报告导出 `/api/v1/report`

| Method | Path | 说明 |
|--------|------|------|
| GET | `/report/excel/{company}` | 下载 Excel 报告 |
| GET | `/report/html/{company}` | HTML 报告（可打印 PDF） |

### 对话 `/api/v1/chat`

| Method | Path | 说明 |
|--------|------|------|
| POST | `/chat/` | ReAct 对话 |
| POST | `/chat/stream` | SSE 流式对话 |

### WebSocket `/ws`

服务端主动推送事件：

| 事件 | 触发时机 | 数据 |
|------|---------|------|
| `alert_update` | 监控清单/告警数量变更 | `{count}` |
| `sentiment_ready` | 舆情分析完成 | `{company_name}` |

---

## Agent 工具清单（14 个）

| 工具 | 说明 |
|------|------|
| `search_company` | 搜索企业全称 |
| `assess_risk` | 风险评估（财报 + 司法 + 经营） |
| `check_alert` | 预警变化检测 |
| `get_watchlist` | 查看监控清单 |
| `add_to_watchlist` | 加入监控 |
| `remove_from_watchlist` | 移除监控 |
| `esg_assessment` | ESG 三维评分 |
| `contagion_analysis` | 风险传染路径 |
| `sentiment_analysis` | 舆情情感分析 |
| `predict_risk` | 风险恶化预测 |
| `macro_risk` | 宏观风险评估 |
| `find_alternatives` | 替代供应商推荐 |
| `scenario_simulate` | 情景影响模拟 |
| `check_sanctions` | 制裁黑名单筛查 |

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
| 免费巡检 | 每日 9:00 | AkShare |
| 付费刷新 | 每周一 9:00 | 天眼查 |
| 飞书日报 | 每日 9:00 | MongoDB 聚合 |
| 舆情巡检 | 每日 10:00 | DDG + LLM |

---

## 目录结构

```
SupplierRiskAnalysisAgent/
├── backend/
│   ├── app/
│   │   ├── api/               # FastAPI 路由（13 个模块）
│   │   ├── services/          # 业务逻辑（21 个服务）
│   │   ├── agents/            # 多 Agent 系统
│   │   ├── repositories/      # 数据访问层
│   │   ├── schemas/           # Pydantic 模型
│   │   ├── db/                # MongoDB 连接
│   │   ├── core/              # 配置/安全/缓存/日志
│   │   └── tasks/             # Celery 异步任务
│   └── tests/                 # pytest 测试
├── frontend/
│   └── src/
│       ├── App.tsx            # 主入口（6 标签页）
│       ├── api.ts             # HTTP 客户端 + SSE
│       ├── websocket.ts       # WebSocket 客户端
│       └── components/        # 15 个 React 组件
├── nginx/nginx.conf           # 反向代理配置
├── docker-compose.yml         # 生产模式
├── docker-compose.dev.yml     # 开发模式（热重载）
├── Dockerfile.backend
├── Dockerfile.frontend
├── .github/workflows/ci.yml   # CI/CD 流水线
├── start.sh                   # 一键启动
└── stop.sh
```
