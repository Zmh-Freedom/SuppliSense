---
marp: true
theme: default
paginate: true
size: 16:9
style: |
  @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700;900&display=swap');
  section {
    font-family: 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
    color: #1e293b;
    padding: 40px 50px;
  }
  section::after {
    font-size: 12px;
    color: #94a3b8;
  }
  h1 {
    color: #0f172a;
    font-size: 1.6em;
    font-weight: 900;
    border-bottom: 3px solid #3b82f6;
    padding-bottom: 8px;
    margin-bottom: 14px;
  }
  h2 {
    color: #1e40af;
    font-size: 1.15em;
    font-weight: 700;
    margin-top: 0;
  }
  h3 {
    color: #334155;
    font-size: 1.0em;
    font-weight: 600;
  }
  strong { color: #1e40af; }
  table {
    font-size: 0.75em;
    border-collapse: collapse;
    width: 100%;
    margin: 8px 0;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
  }
  th {
    background: linear-gradient(135deg, #1e40af 0%, #3b82f6 100%);
    color: white;
    font-weight: 600;
    padding: 8px 12px;
    text-align: left;
  }
  td {
    padding: 7px 12px;
    border-bottom: 1px solid #e2e8f0;
  }
  tr:nth-child(even) td { background: #f8fafc; }
  tr:hover td { background: #eff6ff; }
  code {
    background: #e2e8f0;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 0.85em;
    color: #7c3aed;
  }
  ul { line-height: 1.5; padding-left: 20px; }
  li { margin-bottom: 3px; }
  blockquote {
    border-left: 4px solid #3b82f6;
    background: linear-gradient(90deg, #eff6ff 0%, #f8fafc 100%);
    padding: 12px 18px;
    margin: 10px 0;
    border-radius: 0 10px 10px 0;
    font-size: 0.88em;
    box-shadow: 0 1px 4px rgba(59,130,246,0.08);
  }
  section.cover {
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    text-align: center;
    background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 35%, #1e40af 70%, #3b82f6 100%);
    color: white;
  }
  section.cover h1 {
    color: white;
    border-bottom: 3px solid rgba(255,255,255,0.3);
    font-size: 2.2em;
  }
  section.cover h2 {
    color: #93c5fd;
    font-weight: 500;
    font-size: 1.2em;
  }
  section.cover p { color: #cbd5e1; }
  section.section-divider {
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    text-align: center;
    background: linear-gradient(135deg, #1e40af 0%, #3b82f6 40%, #7c3aed 100%);
    color: white;
  }
  section.section-divider h1 {
    color: white;
    border-bottom: 3px solid rgba(255,255,255,0.3);
    font-size: 2.0em;
  }
  section.section-divider p {
    color: #c7d2fe;
    font-size: 1.1em;
  }
  .grid-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  .card {
    background: white;
    border: 1px solid #e2e8f0;
    border-left: 4px solid #3b82f6;
    border-radius: 0 10px 10px 0;
    padding: 12px 16px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.06);
  }
  .card h3 {
    margin-top: 0;
    margin-bottom: 4px;
    color: #1e40af;
  }
  .stat-row {
    display: flex;
    justify-content: space-around;
    gap: 14px;
    margin: 14px 0;
  }
  .stat-box {
    background: white;
    border: 1px solid #e2e8f0;
    border-top: 3px solid #3b82f6;
    border-radius: 0 0 10px 10px;
    padding: 14px 20px;
    text-align: center;
    flex: 1;
    box-shadow: 0 2px 6px rgba(0,0,0,0.06);
  }
  .stat-box .number {
    font-size: 1.6em;
    font-weight: 900;
    color: #1e40af;
    line-height: 1;
  }
  .stat-box .label {
    font-size: 0.72em;
    color: #64748b;
    margin-top: 4px;
  }
  .badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 20px;
    font-size: 0.72em;
    font-weight: 600;
  }
  .badge-green { background: #dcfce7; color: #166534; }
  .badge-yellow { background: #fef9c3; color: #854d0e; }
  .badge-red { background: #fee2e2; color: #991b1b; }
  .badge-blue { background: #dbeafe; color: #1e40af; }
---

<!-- _class: cover -->

# 供应商风险分析智能体

## 项目进展汇报

**汇报日期：** 2026年6月16日 &nbsp;&nbsp;|&nbsp;&nbsp; **版本：** v0.3.0

---

# 项目概述

> 面向企业采购与供应链管理的 AI 驱动风险分析平台，提供从数据采集、风险评估到智能决策的全链路能力。

<div class="grid-2">
<div class="card">

### 核心功能
- 13 维度企业风险评分（财务/司法/经营/ESG）
- 实时舆情监控与情感分析
- 供应链风险传染图谱可视化
- AI 智能对话（ReAct / Plan-Execute / 多 Agent）

</div>
<div class="card">

### 扩展能力
- 风险预警与早期信号预测
- 宏观经济风险叠加（PMI、制裁筛查）
- 场景模拟（破产、供应中断）
- RAG 知识库检索增强

</div>
</div>

---

# 技术架构

<div class="grid-2">
<div class="card">

### 前端技术栈
| 技术 | 用途 |
|------|------|
| React 19 + TypeScript 6 | 核心框架 |
| Vite 8 | 构建工具 |
| React Query 5 | 数据获取与缓存 |
| React Router 7 | 路由与深链接 |
| Recharts 3 + ReactFlow 12 | 图表与图谱 |
| Tailwind CSS 4 | 样式系统 |

</div>
<div class="card">

### 后端技术栈
| 技术 | 用途 |
|------|------|
| FastAPI + Pydantic v2 | API 框架 |
| MongoDB 8 | 主数据库 |
| Redis 7 | 缓存与任务队列 |
| DeepSeek API | LLM 对话与分析 |
| ChromaDB | 向量知识库 |
| Docker Compose | 容器化部署 |

</div>
</div>

---

# 功能模块完成度

<div class="stat-row">
<div class="stat-box"><div class="number">12</div><div class="label">功能模块</div></div>
<div class="stat-box"><div class="number">~95%</div><div class="label">整体完成度</div></div>
<div class="stat-box"><div class="number">~70</div><div class="label">API 端点</div></div>
<div class="stat-box"><div class="number">25</div><div class="label">后端服务</div></div>
</div>

| 模块 | 状态 | 模块 | 状态 |
|------|------|------|------|
| 风险看板 | <span class="badge badge-green">100%</span> | 关系图谱 | <span class="badge badge-green">100%</span> |
| 企业风险评估 | <span class="badge badge-green">100%</span> | ESG 评估 | <span class="badge badge-green">100%</span> |
| 告警中心 | <span class="badge badge-green">100%</span> | 宏观风险分析 | <span class="badge badge-green">100%</span> |
| 舆情监控 | <span class="badge badge-green">100%</span> | 场景模拟 | <span class="badge badge-green">100%</span> |
| AI 智能对话 | <span class="badge badge-yellow">95%</span> | 用户认证与权限 | <span class="badge badge-green">100%</span> |

---

<!-- _class: section-divider -->

# 近一周优化升级

## React Query · React Router · Bug 修复

---

# 前端架构升级

<div class="grid-2">
<div class="card">

### React Query 数据层
**问题：** 13 个组件各自手写数据获取，无缓存、无去重

**改进：**
- React Query 统一数据获取
- 60s 缓存 + 自动去重 + 后台刷新
- 3 个共享 Hooks 集中管理
- WebSocket 驱动缓存失效

</div>
<div class="card">

### React Router 路由层
**问题：** 整数 tab + localStorage，无深链接

**改进：**
- React Router v7，8 个路由
- Layout + AuthGuard 组件
- URL 深链接（如 `/assess/海康威视`）
- 浏览器前进/后退正常工作

</div>
</div>

---

# Bug 修复 & 功能调整

| 问题 | 修复方案 | 状态 |
|------|---------|------|
| 风险矩阵点位置错误 | MongoDB 聚合查询返回告警数 | ✅ |
| 告警中心页面崩溃 | 后端返回包装对象，前端 select 提取 | ✅ |
| SSE 解析事件错位 | currentEvent 移到循环外 | ✅ |
| 风险矩阵 tooltip 体验差 | 固定底部改为鼠标跟随浮窗 | ✅ |
| 智能对话格式错误重试 | 正则 JSON 提取 + 调试日志 | 🔄 |
| 供应商对比功能 | 整体移除，后端 API 保留 | ✅ |

---

# 当前待解决问题

| 问题 | 严重度 | 状态 |
|------|--------|------|
| 智能对话 JSON 格式错误重试 | <span class="badge badge-yellow">中</span> | 已加调试日志 |
| 前端零测试覆盖 | <span class="badge badge-red">高</span> | 升级计划已制定 |
| 硬编码密码 123456（5 个文件） | <span class="badge badge-red">高</span> | 升级计划已制定 |
| 无 CSRF 防护 | <span class="badge badge-yellow">中</span> | SameSite=Lax 部分防护 |
| Docker 容器以 root 运行 | <span class="badge badge-yellow">中</span> | 升级计划已制定 |
| 无审计日志 | <span class="badge badge-blue">低</span> | 升级计划已制定 |

---

<!-- _class: section-divider -->

# 企业级升级计划

## 5 阶段 · 21 任务 · 13-18 次会话

---

# 升级计划：安全 + 测试

<div class="grid-2">
<div class="card">

### 阶段一：安全加固 🔴 最高优先级
- 外部化硬编码凭据（5 个文件）
- Cookie 安全 + CORS 收紧
- 密码验证 + 管理员加固
- Docker 非 root + 网络隔离

</div>
<div class="card">

### 阶段二：后端测试 🟠 高优先级
- Pytest 配置 + 测试 DB 隔离
- 认证/风险/告警服务测试
- 天眼查/LLM 外部服务 Mock

### 阶段三：前端测试 🟠 高优先级
- Vitest + Testing Library
- API 工具函数 + 组件冒烟测试

</div>
</div>

---

# 升级计划：质量 + 运维 + 目标

<div class="grid-2">
<div class="card">

### 阶段四：代码质量 🟡 中优先级
- 死代码清理 + TS 严格模式
- 设计令牌提取 + Toast 通知
- 移除 Celery/Flower 依赖

### 阶段五：运维改进 🟡 中优先级
- 结构化请求日志中间件
- .dockerignore + CI 流水线
- 审计日志基础

</div>
<div class="card">

### 整体目标
| 维度 | 当前 → 目标 |
|------|------------|
| 安全 | 🔴 硬编码 → 🟢 全外部化 |
| 测试 | 🔴 零覆盖 → 🟢 >60% |
| 质量 | 🟡 死代码 → 🟢 TS strict |
| 运维 | 🟡 无 CI → 🟢 自动化 |

</div>
</div>

---

<!-- _class: cover -->

# 总结

**已完成** — 核心功能全部上线 · 前端架构现代化 · 容器化部署 · 可观测性基础

**正在推进** — 智能对话 JSON 解析稳定性优化

**下一步** — 安全加固 → 测试建设 → 质量提升 → 运维完善

