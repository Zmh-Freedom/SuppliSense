# 供应商风险分析 Agent

基于 ReAct 架构的企业供应商风险智能分析系统。覆盖**风险评估 → 预警监控 → 舆情追踪 → ESG 评分 → 替代建议 → 情景模拟**完整链路，支持自然语言交互。

---

## 系统架构

```
浏览器 (React) ──→ Vite Proxy ──→ FastAPI ──→ DeepSeek LLM (ReAct Agent)
                                        │
                                        ├── MongoDB (企业数据缓存)
                                        ├── 天眼查 API (工商 / 司法 / 经营)
                                        ├── AkShare (A股 / 港股财报)
                                        ├── DuckDuckGo (新闻搜索)
                                        └── 飞书 Webhook (告警推送)
```

## 功能模块（5 个标签页）

| 标签 | 功能 | 说明 |
|------|------|------|
| **风险看板** | 全局总览 | 统计卡片 + 风险分布条 + 预警信号 + 舆情概览 + 风险矩阵四象限图 |
| **企业评估** | 单企业深度分析 | 风险评分 + 15 财务指标 + 8 司法经营指标 + ESG/宏观/替代/传染/情景/制裁/舆情（可展开） |
| **告警中心** | 变更监控 | 快照对比 + 自定义规则触发 + 飞书推送 |
| **舆情监控** | 新闻追踪 | DDG 搜索 + LLM 情感分类 + AI 摘要 + 新闻原文链接 |
| **智能对话** | AI 助手 | 14 工具 ReAct Agent，支持自然语言查询/评估/预测/替代建议 |

### 企业评估页展开项

| 展开面板 | 内容 | 数据来源 |
|------|------|------|
| ESG 评分 | 环境(E) / 社会(S) / 治理(G) 三维评分 | 天眼查风险数据 |
| 宏观风险 | 行业 PMI 景气 + 地区信用 + 政策标签 | AkShare + 规则引擎 |
| 替代建议 | 同行业低风险企业 Top 5 | 监控清单 + 天眼查 |
| 风险传染 | 分支机构 + 供应链依赖 + 同行业关联 | 天眼查 branch API |
| 情景模拟 | 4 种情景（倒闭/诉讼/中断/质量）影响评估 | 依赖图 + 风险评分 |
| 制裁筛查 | OFAC 实体清单 + 敏感国家 + 失信被执行人 | 内置名单 + 天眼查 |
| 舆情分析 | 新闻情感 + 风险标签 + AI 摘要 | DDG 搜索 + LLM |

---

## 快速开始

### 1. 环境要求

- Python 3.11+
- Node.js 18+
- MongoDB 7.x（本地或 Docker）

### 2. 启动 MongoDB

```bash
docker run -d --name mongodb -p 27017:27017 \
  -e MONGO_INITDB_ROOT_USERNAME=root \
  -e MONGO_INITDB_ROOT_PASSWORD=123456 \
  mongo:7
```

### 3. 配置环境变量

```bash
cd backend
cp .env.example .env
```

必填项：

```bash
TIANYANCHA_TOKEN=your_token        # 天眼查开放平台 token
LLM_API_KEY=sk-xxx                 # DeepSeek API key
```

可选项：

```bash
FEISHU_WEBHOOK_URL=https://...     # 飞书机器人 webhook
FEISHU_SECRET=your_secret          # 飞书签名校验密钥
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
SENTIMENT_CHECK_CRON=0 10 * * *    # 舆情巡检 cron
```

### 4. 启动后端

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. 启动前端

```bash
cd frontend
npm install
npx vite --host 0.0.0.0 --port 5173
```

打开 `http://localhost:5173`

---

## API 清单

### 风险评估 `/risk`

| Method | Path | 说明 |
|--------|------|------|
| POST | `/risk/assess` | 评估企业风险（15 财务 + 8 司法经营 → 0-100 评分） |

### 企业信息 `/company`

| Method | Path | 说明 |
|--------|------|------|
| GET | `/company/search?keyword=` | 模糊搜索企业（支持非连续匹配） |
| GET | `/company/profile?company_name=` | 企业工商信息 |
| GET | `/company/risk?company_name=` | 风险统计概览 |

### 财报 `/financial`

| Method | Path | 说明 |
|--------|------|------|
| GET | `/financial/metrics?company_name=` | 15 项财务指标 + 3 年趋势 |

### 监控告警 `/alert`

| Method | Path | 说明 |
|--------|------|------|
| GET | `/alert/dashboard` | 看板聚合数据 |
| GET | `/alert/watchlist` | 监控清单 |
| POST | `/alert/watch` | 加入监控 |
| POST | `/alert/watch/batch` | 批量加入 |
| POST | `/alert/watch/upload` | Excel 导入 |
| DELETE | `/alert/watch?company_name=` | 移除监控 |
| POST | `/alert/check` | 单企业变更检测 |
| POST | `/alert/check-all` | 免费巡检（AkShare） |
| POST | `/alert/refresh` | 付费刷新（天眼查） |
| POST | `/alert/refresh-all` | 全量付费刷新 |
| GET | `/alert/history` | 告警历史 |
| GET | `/alert/rules` | 查看告警规则 |
| PUT | `/alert/rules` | 自定义告警规则 |
| GET | `/alert/predict` | 风险预测（所有企业） |
| GET | `/alert/predict/{company}` | 风险预测（单企业） |

### 舆情 `/sentiment`

| Method | Path | 说明 |
|--------|------|------|
| GET | `/sentiment/{company}` | 企业舆情分析 |
| GET | `/sentiment/{company}/trend` | 舆情趋势（7 次历史） |
| GET | `/sentiment/dashboard/overview` | 舆情总览 |
| POST | `/sentiment/analyze` | 触发单企业分析 |
| POST | `/sentiment/analyze-all` | 批量分析 |

### ESG & 传染 `/p2`

| Method | Path | 说明 |
|--------|------|------|
| GET | `/p2/esg` | ESG 总览 |
| GET | `/p2/esg/{company}` | ESG 三维评分 |
| GET | `/p2/contagion` | 传染总览 |
| GET | `/p2/contagion/{company}` | 风险传染分析 |
| GET | `/p2/dependencies/{company}` | 供应链依赖 |
| POST | `/p2/dependencies` | 添加依赖关系 |
| DELETE | `/p2/dependencies` | 删除依赖关系 |

### 宏观 & 替代 & 情景 & 制裁 `/analysis`

| Method | Path | 说明 |
|--------|------|------|
| GET | `/analysis/macro/pmi` | 最新 PMI |
| GET | `/analysis/macro/{company}` | 宏观风险评估 |
| GET | `/analysis/alternatives` | 替代总览 |
| GET | `/analysis/alternatives/{company}` | 替代建议 |
| GET | `/analysis/scenario/{company}` | 情景模拟 |
| POST | `/analysis/scenario` | 自定义情景 |
| GET | `/analysis/sanctions` | 制裁总览 |
| GET | `/analysis/sanctions/{company}` | 制裁筛查 |

### 对话 `/chat`

| Method | Path | 说明 |
|--------|------|------|
| POST | `/chat/chat` | 发送消息（ReAct Agent） |

---

## Agent 工具清单（14 个）

| 工具 | 说明 | 数据来源 |
|------|------|------|
| `search_company` | 搜索企业全称 | MongoDB + 天眼查 |
| `assess_risk` | 风险评估（含财报 + 风险明细） | 天眼查 + AkShare |
| `check_alert` | 预警变化检测 | MongoDB 快照对比 |
| `get_watchlist` | 查看监控清单 | MongoDB |
| `add_to_watchlist` | 加入监控 | MongoDB |
| `remove_from_watchlist` | 移除监控 | MongoDB |
| `esg_assessment` | ESG 三维评分 | 天眼查风险数据 |
| `contagion_analysis` | 风险传染路径 | 天眼查 branch API |
| `sentiment_analysis` | 舆情情感分析 | DDG 搜索 + LLM |
| `predict_risk` | 风险恶化预测 | 趋势模型 |
| `macro_risk` | 宏观风险评估 | AkShare PMI + 规则引擎 |
| `find_alternatives` | 替代供应商推荐 | 监控清单匹配 |
| `scenario_simulate` | 情景影响模拟 | 依赖图 + 风险评分 |
| `check_sanctions` | 制裁黑名单筛查 | 内置名单 + 天眼查 |

---

## 风险评分模型

### 评分维度（13 个，总分 100）

**财务风险（7 维，最高 35 分）**

| 指标 | 说明 |
|------|------|
| 资产负债率 | 连续从 40%→100% 线性增长 0→15 分 |
| 每股现金流 | 负值 +8 分 |
| 营收增长率 | 负增长按比例 0→7 分 |
| 净利增长率 | 负增长按比例 0→7 分 |
| 流动比率 | < 0.8 加 3 分，< 1.2 加 1 分 |
| ROE | < 5% 加 3 分 |
| 扣非利润占比 | < 70% 加 3 分 |

**趋势加分（3 维）**

| 指标 | 说明 |
|------|------|
| 营收趋势 | 3 年斜率 < -2% 加 3 分 |
| 负债趋势 | 3 年斜率 > 2% 加 3 分 |
| 应收周转 | > 180 天加 3 分 |

**司法经营风险（8 维，最高 65 分）**

| 指标 | 计算方式 |
|------|------|
| 诉讼数量 | 每件 +0.3（上限 10） |
| 被执行记录 | 每条 +5（上限 20） |
| 失信记录 | +25 |
| 重大诉讼 | +10 |
| 经营异常 | 每条 +3（上限 12） |
| 行政处罚 | 每条 +2（上限 10） |
| 对外担保 | 按次数线性（上限 5） |
| 股权质押 | 按次数线性（上限 4） |
| 法人变更 | +5 |
| 破产/清算 | 每条 +3（上限 9） |
| 环保处罚 | 每条 +2（上限 5） |

### 评级

| 总分 | 等级 | 颜色 |
|------|------|------|
| 0-30 | 低风险 | 🟢 |
| 31-60 | 中风险 | 🟡 |
| 61-100 | 高风险 | 🔴 |

---

## 定时任务

| 任务 | 频率 | 环境变量 | 花费 |
|------|------|------|------|
| 免费巡检 | 每日 9:00 | `ALERT_CHECK_CRON` | 免费（AkShare） |
| 付费刷新 | 每周一 9:00 | `ALERT_REFRESH_CRON` | 付费（天眼查 6 接口/公司） |
| 飞书日报 | 每日 9:00 | `FEISHU_DIGEST_CRON` | 免费 |
| 舆情巡检 | 每日 10:00 | `SENTIMENT_CHECK_CRON` | 免费（DDG 搜索） |

---

## 数据库

| MongoDB 集合 | 说明 |
|------|------|
| `baseinfo` | 企业工商信息（天眼查 ic/baseinfo） |
| `riskInfo` | 天眼风险数据（被执行/失信/裁判文书等） |
| `lawSuit` | 法律诉讼明细 |
| `punishmentInfo` | 行政处罚明细 |
| `abnormal` | 经营异常明细 |
| `illegalinfo` | 严重违法明细 |
| `branch` | 分支机构数据 |
| `news` | 新闻缓存 |
| `watchlist` | 监控清单 |
| `alert_snapshots` | 风险快照（定时对比用） |
| `alerts` | 告警记录 |
| `sentiment_results` | 舆情分析结果 |
| `financial_cache` | 财报缓存（AkShare） |
| `supply_deps` | 供应链依赖关系 |
| `conversations` | 对话历史 |

---

## ESG 评分模型

| 维度 | 指标 | 数据来源 |
|------|------|------|
| **E** 环境 | 环保处罚数量、高负债隐含环保压力 | 天眼查 riskInfo |
| **S** 社会 | 诉讼数量（劳动纠纷）、行政处罚 | 天眼查 lawSuit + punishmentInfo |
| **G** 治理 | 法人变更频率、失信/被执行、破产/清算、对外担保/股权质押 | 天眼查 riskInfo |

每个维度 0-100 分，综合总分 = E + S + G（上限 100）。

---

## 宏观风险模型

| 维度 | 内容 |
|------|------|
| 行业景气 | AkShare PMI 数据，按制造业/非制造业/综合匹配 |
| 地区风险 | 31 省信用评级（GDP + 债务率 + 信用事件） |
| 政策风险 | 7 标签自动匹配：环保限产/出口管制/产能过剩/房地产依赖/数据安全/双碳政策/地方债务 |

---

## 风险预测模型

基于趋势信号评分（0-14 分），预测未来 6-12 月风险恶化概率：

| 信号 | 分值 |
|------|------|
| 营收持续下滑 | 1-3 |
| 负债持续上升 | 1-3 |
| 净利持续恶化 | 1-2 |
| 流动性紧张 | 1-2 |
| 回款恶化 | 2 |
| 利润质量差 | 2 |
| 历史风险评分上升 | 1-2 |
| 诉讼快速增长 | 2 |

| 总分 | 预测 |
|------|------|
| 0-4 | 大概率稳定 |
| 5-9 | 可能恶化 |
| 10-14 | 高概率恶化 |

---

## 情景模拟

| 情景 | 说明 | 影响系数 |
|------|------|------|
| 供应商倒闭 | 供应完全中断 | ×1.5 |
| 重大诉讼 | 经营受限 | ×1.0 |
| 供应链中断 | 短期断供 | ×1.2 |
| 质量事故 | 产品召回 | ×0.8 |

影响评分 = (依赖广度 × 组织复杂度 × 当前风险) × 情景系数

---

## 技术栈

| 层 | 技术 |
|------|------|
| 后端框架 | FastAPI + Pydantic v2 |
| AI 引擎 | DeepSeek（OpenAI 兼容 SDK）+ ReAct 架构 |
| 数据库 | MongoDB（PyMongo） |
| 定时任务 | APScheduler |
| 财报数据 | AkShare（A 股 15 指标 + 3 年趋势） |
| 工商数据 | 天眼查 API（6 接口） |
| 新闻搜索 | DuckDuckGo HTML / Bing RSS |
| 通知推送 | 飞书 Webhook（HMAC-SHA256 签名） |
| 前端 | Vite + React 19 + TypeScript + Tailwind CSS |
| 测试 | pytest（16 个） |

---

## 目录结构

```
SupplierRiskAnalysisAgent/
├── backend/
│   ├── app/
│   │   ├── api/               # FastAPI 路由（8 个模块）
│   │   │   ├── alert.py       # 监控告警
│   │   │   ├── chat.py        # 智能对话
│   │   │   ├── company.py     # 企业信息
│   │   │   ├── financial.py   # 财报数据
│   │   │   ├── risk.py        # 风险评估
│   │   │   ├── sentiment.py   # 舆情监控
│   │   │   ├── p2.py          # ESG + 风险传染
│   │   │   ├── macro.py       # 宏观 + 替代
│   │   │   └── scenario.py    # 情景 + 制裁
│   │   ├── services/          # 业务逻辑（14 个服务）
│   │   │   ├── agent.py       # ReAct Agent（14 工具）
│   │   │   ├── risk_service.py
│   │   │   ├── alert_service.py / alert_rules.py
│   │   │   ├── scheduler.py
│   │   │   ├── feishu.py
│   │   │   ├── sentiment.py
│   │   │   ├── predictor.py
│   │   │   ├── esg_service.py
│   │   │   ├── contagion.py
│   │   │   ├── macro_service.py
│   │   │   ├── alternative_service.py
│   │   │   ├── scenario_service.py
│   │   │   ├── sanctions_service.py
│   │   │   └── tianyancha_client.py
│   │   ├── repositories/      # 数据访问层
│   │   │   ├── company_repo.py
│   │   │   └── financial_repo.py
│   │   ├── schemas/            # Pydantic 数据模型
│   │   └── db/                 # MongoDB 连接
│   ├── tests/                  # 16 个单元测试
│   ├── .env.example
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.tsx             # 主入口（5 标签页）
│   │   ├── api.ts              # HTTP 客户端
│   │   ├── types.ts            # TypeScript 类型
│   │   └── components/         # React 组件
│   │       ├── Dashboard.tsx   # 风险看板（含矩阵）
│   │       ├── AssessView.tsx  # 企业评估（含 7 个展开项）
│   │       ├── AlertCenter.tsx # 告警中心
│   │       ├── SentimentView.tsx  # 舆情监控
│   │       ├── ChatView.tsx    # 智能对话
│   │       ├── Sidebar.tsx     # 侧边栏
│   │       └── RiskMatrix.tsx  # 风险矩阵图
│   ├── vite.config.ts
│   └── package.json
├── 升级计划.md
└── 课题介绍-简略版.md
```
