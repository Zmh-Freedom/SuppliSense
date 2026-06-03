# 供应商风险分析 Agent

基于 ReAct 架构的企业供应商风险智能分析系统。输入企业名称，自动完成工商查询、风险扫描、财报分析和风险评分，支持自然语言交互和定时监控告警。

## 架构

```
用户 (React前端 / curl) 
  → FastAPI (ReAct Agent) 
    → DeepSeek LLM (决策 + 生成)
    → MongoDB (企业数据缓存)
    → 天眼查 API (付费刷新)
    → AkShare (免费财报)
```

## 快速开始

### 1. 环境要求

- Python 3.11+
- Node.js 18+
- MongoDB（本地或 Docker）

### 2. 启动 MongoDB

```bash
docker run -d --name mongodb -p 27017:27017 \
  -e MONGO_INITDB_ROOT_USERNAME=root \
  -e MONGO_INITDB_ROOT_PASSWORD=123456 \
  mongo:7
```

### 3. 配置 `.env`

```bash
cd backend
cp .env.example .env
# 编辑 .env，填入必要配置：
#   TIANYANCHA_TOKEN=xxx       # 天眼查 API token
#   LLM_API_KEY=sk-xxx          # DeepSeek API key
```

### 4. 启动后端

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 5. 启动前端

```bash
cd frontend
npm install
npx vite --host 0.0.0.0 --port 5173
```

打开 `http://localhost:5173`

## API 概览

| 接口 | 方法 | 说明 |
|------|------|------|
| `/chat/chat` | POST | 智能对话（ReAct Agent） |
| `/risk/assess` | POST | 一键风险评估 |
| `/company/search` | GET | 模糊搜索企业 |
| `/company/profile` | GET | 企业基本信息 |
| `/company/risk` | GET | 风险统计 |
| `/financial/metrics` | GET | 财报数据 |
| `/alert/watchlist` | GET | 监控清单 |
| `/alert/watch` | POST | 加入监控 |
| `/alert/watch/upload` | POST | Excel 批量导入 |
| `/alert/check` | POST | 单公司变更检测 |
| `/alert/check-all` | POST | 免费巡检（全部） |
| `/alert/refresh` | POST | 付费刷新（天眼查 API） |
| `/alert/dashboard` | GET | 看板聚合数据 |

## 数据库

| MongoDB 集合 | 说明 |
|-------------|------|
| `baseinfo` | 企业工商信息（6269 条） |
| `riskInfo` | 天眼风险数据 |
| `lawSuit` / `abnormal` / `punishmentInfo` | 司法/经营明细 |
| `watchlist` | 监控清单 |
| `alert_snapshots` | 风险快照（用于对比） |
| `alerts` | 告警记录 |
| `financial_cache` | 财报缓存 |
| `conversations` | 对话历史 |

## 定时任务

| 任务 | 频率 | 花费 |
|------|------|------|
| 免费巡检 | 每日 9:00 | AkShare 财报 + 快照对比 |
| 付费刷新 | 每周一 9:00 | 天眼查 6 接口/公司 |

可在 `.env` 中修改 cron 表达式：

```bash
ALERT_CHECK_CRON=0 9 * * *      # 免费巡检
ALERT_REFRESH_CRON=0 9 * * 1    # 付费刷新
```

## 风险评分公式

总分 0-100，分三类：

**财务风险（最高 35 分）**

| 指标 | 计算 |
|------|------|
| 资产负债率 | 40% 以下 0 分，40-90% 线性 0→15 |
| 现金流为负 | +8 分 |
| 营收下降 | 0→7 分（跌幅越大越高） |
| 净利下降 | 0→7 分（跌幅越大越高） |

**司法风险（最高 40 分）**

| 指标 | 计算 |
|------|------|
| 诉讼 | 每件 +0.3（上限 10） |
| 被执行 | 每条 +5（上限 20） |
| 失信 | +25 |
| 重大诉讼 | +10 |
| 对外担保 | 按次数线性（上限 5） |
| 股权质押 | 按次数线性（上限 4） |

**经营风险（最高 25 分）**

| 指标 | 计算 |
|------|------|
| 经营异常 | 每条 +3（上限 12） |
| 行政处罚 | 每条 +2（上限 10） |
| 法人变更 | +5 |
| 破产/清算 | 每条 +3（上限 9） |
| 环保处罚 | 每条 +2（上限 5） |

| 总分 | 等级 |
|------|------|
| 0-30 | 低风险 |
| 31-60 | 中风险 |
| 61-100 | 高风险 |

## 工具清单

Agent 可通过 ReAct 循环调用：

| 工具 | 说明 |
|------|------|
| `search_company` | 搜索企业全称 |
| `assess_risk` | 风险评估（含财报 + 风险明细） |
| `check_alert` | 预警变化检测 |
| `get_watchlist` | 查看监控清单 |
| `add_to_watchlist` | 加入监控 |
| `remove_from_watchlist` | 移除监控 |

## 技术栈

| 层 | 技术 |
|----|------|
| 后端框架 | FastAPI + Pydantic |
| AI 引擎 | DeepSeek（OpenAI 兼容）+ ReAct |
| 数据库 | MongoDB（PyMongo） |
| 定时任务 | APScheduler |
| 财报数据 | AkShare（A 股 + 港股） |
| 工商数据 | 天眼查 API |
| 前端 | Vite + React + TypeScript + Tailwind CSS |
| 测试 | pytest |

## 目录结构

```
SupplierRiskAnalysisAgent/
  backend/
    app/
      api/          # FastAPI 路由
      services/      # 业务逻辑（Agent / 评分 / 告警 / 调度）
      repositories/  # 数据访问（MongoDB / AkShare）
      schemas/       # Pydantic 模型
      db/            # MongoDB 连接
    tests/           # 16 个单元测试
  frontend/
    src/
      components/    # React 组件
      api.ts         # HTTP 客户端
      types.ts       # TypeScript 类型
```
