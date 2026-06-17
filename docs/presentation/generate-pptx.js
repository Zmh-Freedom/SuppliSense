const pptxgen = require("pptxgenjs");

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9";
pres.author = "周旻昊";
pres.title = "供应商风险分析智能体 - 项目进展汇报";

// ── Color palette ──
const C = {
  darkBg: "0F172A",
  darkBg2: "1E3A5F",
  primary: "1E40AF",
  blue: "3B82F6",
  purple: "7C3AED",
  lightBg: "F8FAFC",
  lightBg2: "F1F5F9",
  text: "1E293B",
  textDark: "0F172A",
  muted: "64748B",
  white: "FFFFFF",
  border: "E2E8F0",
  green: "166534",
  greenBg: "DCFCE7",
  yellow: "854D0E",
  yellowBg: "FEF9C3",
  red: "991B1B",
  redBg: "FEE2E2",
  blueLight: "DBEAFE",
  blueText: "1E40AF",
  cardBg: "FFFFFF",
};

const FONT = "Microsoft YaHei";
const FONT_EN = "Calibri";

// ── Helpers ──
const makeShadow = () => ({
  type: "outer",
  blur: 4,
  offset: 2,
  angle: 135,
  color: "000000",
  opacity: 0.08,
});

function addCardAccentBar(slide, x, y, h) {
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w: 0.06, h,
    fill: { color: C.blue },
  });
}

function addCard(slide, x, y, w, h) {
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w, h,
    fill: { color: C.cardBg },
    line: { color: C.border, width: 0.75 },
    shadow: makeShadow(),
  });
  addCardAccentBar(slide, x, y, h);
}

function addStatBox(slide, x, y, w, number, label) {
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w, h: 0.9,
    fill: { color: C.cardBg },
    line: { color: C.border, width: 0.75 },
    shadow: makeShadow(),
  });
  // top accent bar
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w, h: 0.04,
    fill: { color: C.blue },
  });
  slide.addText(number, {
    x, y: y + 0.1, w, h: 0.45,
    fontSize: 22, fontFace: FONT_EN, bold: true,
    color: C.primary, align: "center", valign: "middle",
  });
  slide.addText(label, {
    x, y: y + 0.55, w, h: 0.3,
    fontSize: 10, fontFace: FONT,
    color: C.muted, align: "center", valign: "top",
  });
}

function addBadge(slide, x, y, text, type) {
  const colors = {
    green: { bg: C.greenBg, fg: C.green },
    yellow: { bg: C.yellowBg, fg: C.yellow },
    red: { bg: C.redBg, fg: C.red },
    blue: { bg: C.blueLight, fg: C.blueText },
  };
  const c = colors[type] || colors.green;
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w: 0.6, h: 0.25,
    fill: { color: c.bg },
    rectRadius: 0.1,
  });
  slide.addText(text, {
    x, y, w: 0.6, h: 0.25,
    fontSize: 8, fontFace: FONT, bold: true,
    color: c.fg, align: "center", valign: "middle",
  });
}

// ══════════════════════════════════════════════════
// Slide 1: Cover
// ══════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.darkBg };

  // decorative accent line
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 0, w: 10, h: 0.06,
    fill: { color: C.blue },
  });

  s.addText("供应商风险分析智能体", {
    x: 1, y: 1.4, w: 8, h: 1.0,
    fontSize: 40, fontFace: FONT, bold: true,
    color: C.white, align: "center", valign: "middle",
  });

  // subtitle underline
  s.addShape(pres.shapes.RECTANGLE, {
    x: 3.5, y: 2.5, w: 3, h: 0.03,
    fill: { color: C.blue },
  });

  s.addText("项目进展汇报", {
    x: 1, y: 2.7, w: 8, h: 0.7,
    fontSize: 22, fontFace: FONT,
    color: "93C5FD", align: "center", valign: "middle",
  });

  s.addText("汇报日期：2026年6月16日    |    版本：v0.3.0", {
    x: 1, y: 4.2, w: 8, h: 0.5,
    fontSize: 14, fontFace: FONT,
    color: "94A3B8", align: "center", valign: "middle",
  });

  // bottom accent
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 5.565, w: 10, h: 0.06,
    fill: { color: C.blue },
  });
}

// ══════════════════════════════════════════════════
// Slide 2: 项目概述
// ══════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };

  s.addText("项目概述", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 28, fontFace: FONT, bold: true,
    color: C.textDark, margin: 0,
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 0.9, w: 2, h: 0.04,
    fill: { color: C.blue },
  });

  // blockquote
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 1.15, w: 9, h: 0.55,
    fill: { color: "EFF6FF" },
    line: { color: "EFF6FF", width: 0 },
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 1.15, w: 0.06, h: 0.55,
    fill: { color: C.blue },
  });
  s.addText("面向企业采购与供应链管理的 AI 驱动风险分析平台，提供从数据采集、风险评估到智能决策的全链路能力。", {
    x: 0.75, y: 1.15, w: 8.6, h: 0.55,
    fontSize: 13, fontFace: FONT,
    color: C.text, valign: "middle",
  });

  // Left card: 核心功能
  addCard(s, 0.5, 2.0, 4.3, 3.0);
  s.addText("核心功能", {
    x: 0.75, y: 2.1, w: 3.8, h: 0.4,
    fontSize: 15, fontFace: FONT, bold: true,
    color: C.primary,
  });
  s.addText([
    { text: "13 维度企业风险评分（财务/司法/经营/ESG）", options: { bullet: true, breakLine: true } },
    { text: "实时舆情监控与情感分析", options: { bullet: true, breakLine: true } },
    { text: "供应链风险传染图谱可视化", options: { bullet: true, breakLine: true } },
    { text: "AI 智能对话（ReAct / Plan-Execute / 多 Agent）", options: { bullet: true } },
  ], {
    x: 0.85, y: 2.55, w: 3.7, h: 2.3,
    fontSize: 12, fontFace: FONT, color: C.text,
    paraSpaceAfter: 6,
  });

  // Right card: 扩展能力
  addCard(s, 5.2, 2.0, 4.3, 3.0);
  s.addText("扩展能力", {
    x: 5.45, y: 2.1, w: 3.8, h: 0.4,
    fontSize: 15, fontFace: FONT, bold: true,
    color: C.primary,
  });
  s.addText([
    { text: "风险预警与早期信号预测", options: { bullet: true, breakLine: true } },
    { text: "宏观经济风险叠加（PMI、制裁筛查）", options: { bullet: true, breakLine: true } },
    { text: "场景模拟（破产、供应中断）", options: { bullet: true, breakLine: true } },
    { text: "RAG 知识库检索增强", options: { bullet: true } },
  ], {
    x: 5.55, y: 2.55, w: 3.7, h: 2.3,
    fontSize: 12, fontFace: FONT, color: C.text,
    paraSpaceAfter: 6,
  });
}

// ══════════════════════════════════════════════════
// Slide 3: 技术架构
// ══════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };

  s.addText("技术架构", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 28, fontFace: FONT, bold: true,
    color: C.textDark, margin: 0,
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 0.9, w: 2, h: 0.04,
    fill: { color: C.blue },
  });

  // Left card: 前端
  addCard(s, 0.5, 1.2, 4.3, 3.9);
  s.addText("前端技术栈", {
    x: 0.75, y: 1.3, w: 3.8, h: 0.35,
    fontSize: 15, fontFace: FONT, bold: true,
    color: C.primary,
  });

  const feData = [
    ["React 19 + TypeScript 6", "核心框架"],
    ["Vite 8", "构建工具"],
    ["React Query 5", "数据获取与缓存"],
    ["React Router 7", "路由与深链接"],
    ["Recharts 3 + ReactFlow 12", "图表与图谱"],
    ["Tailwind CSS 4", "样式系统"],
  ];
  const feHeader = [
    [
      { text: "技术", options: { bold: true, color: "FFFFFF", fill: { color: C.primary }, fontSize: 11, fontFace: FONT } },
      { text: "用途", options: { bold: true, color: "FFFFFF", fill: { color: C.primary }, fontSize: 11, fontFace: FONT } },
    ],
  ];
  const feRows = feData.map((r, i) => [
    { text: r[0], options: { fontSize: 11, fontFace: FONT_EN, fill: { color: i % 2 === 0 ? C.lightBg : C.white } } },
    { text: r[1], options: { fontSize: 11, fontFace: FONT, fill: { color: i % 2 === 0 ? C.lightBg : C.white } } },
  ]);
  s.addTable([...feHeader, ...feRows], {
    x: 0.7, y: 1.75, w: 3.9,
    colW: [2.1, 1.8],
    border: { pt: 0.5, color: C.border },
    rowH: 0.4,
  });

  // Right card: 后端
  addCard(s, 5.2, 1.2, 4.3, 3.9);
  s.addText("后端技术栈", {
    x: 5.45, y: 1.3, w: 3.8, h: 0.35,
    fontSize: 15, fontFace: FONT, bold: true,
    color: C.primary,
  });

  const beData = [
    ["FastAPI + Pydantic v2", "API 框架"],
    ["MongoDB 8", "主数据库"],
    ["Redis 7", "缓存与任务队列"],
    ["DeepSeek API", "LLM 对话与分析"],
    ["ChromaDB", "向量知识库"],
    ["Docker Compose", "容器化部署"],
  ];
  const beHeader = [
    [
      { text: "技术", options: { bold: true, color: "FFFFFF", fill: { color: C.primary }, fontSize: 11, fontFace: FONT } },
      { text: "用途", options: { bold: true, color: "FFFFFF", fill: { color: C.primary }, fontSize: 11, fontFace: FONT } },
    ],
  ];
  const beRows = beData.map((r, i) => [
    { text: r[0], options: { fontSize: 11, fontFace: FONT_EN, fill: { color: i % 2 === 0 ? C.lightBg : C.white } } },
    { text: r[1], options: { fontSize: 11, fontFace: FONT, fill: { color: i % 2 === 0 ? C.lightBg : C.white } } },
  ]);
  s.addTable([...beHeader, ...beRows], {
    x: 5.4, y: 1.75, w: 3.9,
    colW: [2.1, 1.8],
    border: { pt: 0.5, color: C.border },
    rowH: 0.4,
  });
}

// ══════════════════════════════════════════════════
// Slide 4: 功能模块完成度
// ══════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };

  s.addText("功能模块完成度", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 28, fontFace: FONT, bold: true,
    color: C.textDark, margin: 0,
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 0.9, w: 2, h: 0.04,
    fill: { color: C.blue },
  });

  // Stat boxes
  const stats = [
    ["12", "功能模块"],
    ["~95%", "整体完成度"],
    ["~70", "API 端点"],
    ["25", "后端服务"],
  ];
  stats.forEach((st, i) => {
    addStatBox(s, 0.5 + i * 2.3, 1.15, 2.1, st[0], st[1]);
  });

  // Table
  const header = [
    [
      { text: "模块", options: { bold: true, color: "FFFFFF", fill: { color: C.primary }, fontSize: 11, fontFace: FONT } },
      { text: "状态", options: { bold: true, color: "FFFFFF", fill: { color: C.primary }, fontSize: 11, fontFace: FONT } },
      { text: "模块", options: { bold: true, color: "FFFFFF", fill: { color: C.primary }, fontSize: 11, fontFace: FONT } },
      { text: "状态", options: { bold: true, color: "FFFFFF", fill: { color: C.primary }, fontSize: 11, fontFace: FONT } },
    ],
  ];
  const modules = [
    ["风险看板", "green", "100%", "关系图谱", "green", "100%"],
    ["企业风险评估", "green", "100%", "ESG 评估", "green", "100%"],
    ["告警中心", "green", "100%", "宏观风险分析", "green", "100%"],
    ["舆情监控", "green", "100%", "场景模拟", "green", "100%"],
    ["AI 智能对话", "yellow", "95%", "用户认证与权限", "green", "100%"],
  ];
  const rows = modules.map((r, i) => [
    { text: r[0], options: { fontSize: 11, fontFace: FONT, fill: { color: i % 2 === 0 ? C.lightBg : C.white } } },
    { text: r[2], options: { fontSize: 10, fontFace: FONT, bold: true, fill: { color: r[1] === "green" ? C.greenBg : C.yellowBg }, color: r[1] === "green" ? C.green : C.yellow } },
    { text: r[3], options: { fontSize: 11, fontFace: FONT, fill: { color: i % 2 === 0 ? C.lightBg : C.white } } },
    { text: r[5], options: { fontSize: 10, fontFace: FONT, bold: true, fill: { color: r[4] === "green" ? C.greenBg : C.yellowBg }, color: r[4] === "green" ? C.green : C.yellow } },
  ]);
  s.addTable([...header, ...rows], {
    x: 0.5, y: 2.3, w: 9,
    colW: [2.5, 1.2, 2.5, 1.2],
    border: { pt: 0.5, color: C.border },
    rowH: 0.42,
  });
}

// ══════════════════════════════════════════════════
// Slide 5: Section divider - 近一周优化升级
// ══════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.primary };

  s.addText("近一周优化升级", {
    x: 1, y: 1.6, w: 8, h: 1.0,
    fontSize: 36, fontFace: FONT, bold: true,
    color: C.white, align: "center", valign: "middle",
  });

  s.addShape(pres.shapes.RECTANGLE, {
    x: 3.5, y: 2.7, w: 3, h: 0.03,
    fill: { color: "93C5FD" },
  });

  s.addText("React Query  ·  React Router  ·  Bug 修复", {
    x: 1, y: 3.0, w: 8, h: 0.6,
    fontSize: 18, fontFace: FONT,
    color: "C7D2FE", align: "center", valign: "middle",
  });
}

// ══════════════════════════════════════════════════
// Slide 6: 前端架构升级
// ══════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };

  s.addText("前端架构升级", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 28, fontFace: FONT, bold: true,
    color: C.textDark, margin: 0,
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 0.9, w: 2, h: 0.04,
    fill: { color: C.blue },
  });

  // Left card
  addCard(s, 0.5, 1.2, 4.3, 3.8);
  s.addText("React Query 数据层", {
    x: 0.75, y: 1.3, w: 3.8, h: 0.35,
    fontSize: 15, fontFace: FONT, bold: true,
    color: C.primary,
  });
  s.addText([
    { text: "问题：", options: { bold: true, breakLine: false } },
    { text: "13 个组件各自手写数据获取，无缓存、无去重", options: { breakLine: true } },
  ], {
    x: 0.75, y: 1.75, w: 3.8, h: 0.5,
    fontSize: 11, fontFace: FONT, color: C.text,
  });
  s.addText("改进：", {
    x: 0.75, y: 2.3, w: 3.8, h: 0.3,
    fontSize: 11, fontFace: FONT, bold: true, color: C.text,
  });
  s.addText([
    { text: "React Query 统一数据获取", options: { bullet: true, breakLine: true } },
    { text: "60s 缓存 + 自动去重 + 后台刷新", options: { bullet: true, breakLine: true } },
    { text: "3 个共享 Hooks 集中管理", options: { bullet: true, breakLine: true } },
    { text: "WebSocket 驱动缓存失效", options: { bullet: true } },
  ], {
    x: 0.85, y: 2.6, w: 3.7, h: 2.0,
    fontSize: 11, fontFace: FONT, color: C.text,
    paraSpaceAfter: 5,
  });

  // Right card
  addCard(s, 5.2, 1.2, 4.3, 3.8);
  s.addText("React Router 路由层", {
    x: 5.45, y: 1.3, w: 3.8, h: 0.35,
    fontSize: 15, fontFace: FONT, bold: true,
    color: C.primary,
  });
  s.addText([
    { text: "问题：", options: { bold: true, breakLine: false } },
    { text: "整数 tab + localStorage，无深链接", options: { breakLine: true } },
  ], {
    x: 5.45, y: 1.75, w: 3.8, h: 0.5,
    fontSize: 11, fontFace: FONT, color: C.text,
  });
  s.addText("改进：", {
    x: 5.45, y: 2.3, w: 3.8, h: 0.3,
    fontSize: 11, fontFace: FONT, bold: true, color: C.text,
  });
  s.addText([
    { text: "React Router v7，8 个路由", options: { bullet: true, breakLine: true } },
    { text: "Layout + AuthGuard 组件", options: { bullet: true, breakLine: true } },
    { text: "URL 深链接（如 /assess/海康威视）", options: { bullet: true, breakLine: true } },
    { text: "浏览器前进/后退正常工作", options: { bullet: true } },
  ], {
    x: 5.55, y: 2.6, w: 3.7, h: 2.0,
    fontSize: 11, fontFace: FONT, color: C.text,
    paraSpaceAfter: 5,
  });
}

// ══════════════════════════════════════════════════
// Slide 7: Bug 修复 & 功能调整
// ══════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };

  s.addText("Bug 修复 & 功能调整", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 28, fontFace: FONT, bold: true,
    color: C.textDark, margin: 0,
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 0.9, w: 2, h: 0.04,
    fill: { color: C.blue },
  });

  const header = [
    [
      { text: "问题", options: { bold: true, color: "FFFFFF", fill: { color: C.primary }, fontSize: 12, fontFace: FONT } },
      { text: "修复方案", options: { bold: true, color: "FFFFFF", fill: { color: C.primary }, fontSize: 12, fontFace: FONT } },
      { text: "状态", options: { bold: true, color: "FFFFFF", fill: { color: C.primary }, fontSize: 12, fontFace: FONT } },
    ],
  ];
  const bugs = [
    ["风险矩阵点位置错误", "MongoDB 聚合查询返回告警数", "✅"],
    ["告警中心页面崩溃", "后端返回包装对象，前端 select 提取", "✅"],
    ["SSE 解析事件错位", "currentEvent 移到循环外", "✅"],
    ["风险矩阵 tooltip 体验差", "固定底部改为鼠标跟随浮窗", "✅"],
    ["智能对话格式错误重试", "正则 JSON 提取 + 调试日志", "🔄"],
    ["供应商对比功能", "整体移除，后端 API 保留", "✅"],
  ];
  const rows = bugs.map((r, i) => [
    { text: r[0], options: { fontSize: 11, fontFace: FONT, fill: { color: i % 2 === 0 ? C.lightBg : C.white } } },
    { text: r[1], options: { fontSize: 11, fontFace: FONT, fill: { color: i % 2 === 0 ? C.lightBg : C.white } } },
    { text: r[2], options: { fontSize: 12, fontFace: FONT, fill: { color: i % 2 === 0 ? C.lightBg : C.white }, align: "center" } },
  ]);
  s.addTable([...header, ...rows], {
    x: 0.5, y: 1.2, w: 9,
    colW: [2.8, 4.5, 1.2],
    border: { pt: 0.5, color: C.border },
    rowH: 0.5,
  });
}

// ══════════════════════════════════════════════════
// Slide 8: 当前待解决问题
// ══════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };

  s.addText("当前待解决问题", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 28, fontFace: FONT, bold: true,
    color: C.textDark, margin: 0,
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 0.9, w: 2, h: 0.04,
    fill: { color: C.blue },
  });

  const header = [
    [
      { text: "问题", options: { bold: true, color: "FFFFFF", fill: { color: C.primary }, fontSize: 12, fontFace: FONT } },
      { text: "严重度", options: { bold: true, color: "FFFFFF", fill: { color: C.primary }, fontSize: 12, fontFace: FONT } },
      { text: "状态", options: { bold: true, color: "FFFFFF", fill: { color: C.primary }, fontSize: 12, fontFace: FONT } },
    ],
  ];
  const issues = [
    ["智能对话 JSON 格式错误重试", "yellow", "中", "已加调试日志"],
    ["前端零测试覆盖", "red", "高", "升级计划已制定"],
    ["硬编码密码 123456（5 个文件）", "red", "高", "升级计划已制定"],
    ["无 CSRF 防护", "yellow", "中", "SameSite=Lax 部分防护"],
    ["Docker 容器以 root 运行", "yellow", "中", "升级计划已制定"],
    ["无审计日志", "blue", "低", "升级计划已制定"],
  ];
  const badgeColors = {
    green: { bg: C.greenBg, fg: C.green },
    yellow: { bg: C.yellowBg, fg: C.yellow },
    red: { bg: C.redBg, fg: C.red },
    blue: { bg: C.blueLight, fg: C.blueText },
  };
  const rows = issues.map((r, i) => {
    const bc = badgeColors[r[1]];
    return [
      { text: r[0], options: { fontSize: 11, fontFace: FONT, fill: { color: i % 2 === 0 ? C.lightBg : C.white } } },
      { text: r[2], options: { fontSize: 10, fontFace: FONT, bold: true, fill: { color: bc.bg }, color: bc.fg, align: "center" } },
      { text: r[3], options: { fontSize: 11, fontFace: FONT, fill: { color: i % 2 === 0 ? C.lightBg : C.white } } },
    ];
  });
  s.addTable([...header, ...rows], {
    x: 0.5, y: 1.2, w: 9,
    colW: [3.2, 1.3, 4.0],
    border: { pt: 0.5, color: C.border },
    rowH: 0.5,
  });
}

// ══════════════════════════════════════════════════
// Slide 9: Section divider - 企业级升级计划
// ══════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.primary };

  s.addText("企业级升级计划", {
    x: 1, y: 1.6, w: 8, h: 1.0,
    fontSize: 36, fontFace: FONT, bold: true,
    color: C.white, align: "center", valign: "middle",
  });

  s.addShape(pres.shapes.RECTANGLE, {
    x: 3.5, y: 2.7, w: 3, h: 0.03,
    fill: { color: "93C5FD" },
  });

  s.addText("5 阶段  ·  21 任务  ·  13-18 次会话", {
    x: 1, y: 3.0, w: 8, h: 0.6,
    fontSize: 18, fontFace: FONT,
    color: "C7D2FE", align: "center", valign: "middle",
  });
}

// ══════════════════════════════════════════════════
// Slide 10: 升级计划：安全 + 测试
// ══════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };

  s.addText("升级计划：安全 + 测试", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 28, fontFace: FONT, bold: true,
    color: C.textDark, margin: 0,
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 0.9, w: 2, h: 0.04,
    fill: { color: C.blue },
  });

  // Left card
  addCard(s, 0.5, 1.2, 4.3, 3.8);
  s.addText("阶段一：安全加固  🔴 最高优先级", {
    x: 0.75, y: 1.3, w: 3.8, h: 0.35,
    fontSize: 14, fontFace: FONT, bold: true,
    color: C.primary,
  });
  s.addText([
    { text: "外部化硬编码凭据（5 个文件）", options: { bullet: true, breakLine: true } },
    { text: "Cookie 安全 + CORS 收紧", options: { bullet: true, breakLine: true } },
    { text: "密码验证 + 管理员加固", options: { bullet: true, breakLine: true } },
    { text: "Docker 非 root + 网络隔离", options: { bullet: true } },
  ], {
    x: 0.85, y: 1.75, w: 3.7, h: 1.6,
    fontSize: 12, fontFace: FONT, color: C.text,
    paraSpaceAfter: 5,
  });

  // Right card
  addCard(s, 5.2, 1.2, 4.3, 3.8);
  s.addText("阶段二：后端测试  🟠 高优先级", {
    x: 5.45, y: 1.3, w: 3.8, h: 0.35,
    fontSize: 14, fontFace: FONT, bold: true,
    color: C.primary,
  });
  s.addText([
    { text: "Pytest 配置 + 测试 DB 隔离", options: { bullet: true, breakLine: true } },
    { text: "认证/风险/告警服务测试", options: { bullet: true, breakLine: true } },
    { text: "天眼查/LLM 外部服务 Mock", options: { bullet: true } },
  ], {
    x: 5.55, y: 1.75, w: 3.7, h: 1.2,
    fontSize: 12, fontFace: FONT, color: C.text,
    paraSpaceAfter: 5,
  });

  s.addText("阶段三：前端测试  🟠 高优先级", {
    x: 5.45, y: 3.1, w: 3.8, h: 0.35,
    fontSize: 14, fontFace: FONT, bold: true,
    color: C.primary,
  });
  s.addText([
    { text: "Vitest + Testing Library", options: { bullet: true, breakLine: true } },
    { text: "API 工具函数 + 组件冒烟测试", options: { bullet: true } },
  ], {
    x: 5.55, y: 3.5, w: 3.7, h: 0.9,
    fontSize: 12, fontFace: FONT, color: C.text,
    paraSpaceAfter: 5,
  });
}

// ══════════════════════════════════════════════════
// Slide 11: 升级计划：质量 + 运维 + 目标
// ══════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.lightBg };

  s.addText("升级计划：质量 + 运维 + 目标", {
    x: 0.5, y: 0.3, w: 9, h: 0.6,
    fontSize: 28, fontFace: FONT, bold: true,
    color: C.textDark, margin: 0,
  });
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 0.9, w: 2, h: 0.04,
    fill: { color: C.blue },
  });

  // Left card
  addCard(s, 0.5, 1.2, 4.3, 3.8);
  s.addText("阶段四：代码质量  🟡 中优先级", {
    x: 0.75, y: 1.3, w: 3.8, h: 0.35,
    fontSize: 14, fontFace: FONT, bold: true,
    color: C.primary,
  });
  s.addText([
    { text: "死代码清理 + TS 严格模式", options: { bullet: true, breakLine: true } },
    { text: "设计令牌提取 + Toast 通知", options: { bullet: true, breakLine: true } },
    { text: "移除 Celery/Flower 依赖", options: { bullet: true } },
  ], {
    x: 0.85, y: 1.75, w: 3.7, h: 1.2,
    fontSize: 12, fontFace: FONT, color: C.text,
    paraSpaceAfter: 5,
  });

  s.addText("阶段五：运维改进  🟡 中优先级", {
    x: 0.75, y: 3.1, w: 3.8, h: 0.35,
    fontSize: 14, fontFace: FONT, bold: true,
    color: C.primary,
  });
  s.addText([
    { text: "结构化请求日志中间件", options: { bullet: true, breakLine: true } },
    { text: ".dockerignore + CI 流水线", options: { bullet: true, breakLine: true } },
    { text: "审计日志基础", options: { bullet: true } },
  ], {
    x: 0.85, y: 3.5, w: 3.7, h: 1.2,
    fontSize: 12, fontFace: FONT, color: C.text,
    paraSpaceAfter: 5,
  });

  // Right card: 整体目标
  addCard(s, 5.2, 1.2, 4.3, 3.8);
  s.addText("整体目标", {
    x: 5.45, y: 1.3, w: 3.8, h: 0.35,
    fontSize: 15, fontFace: FONT, bold: true,
    color: C.primary,
  });

  const goalHeader = [
    [
      { text: "维度", options: { bold: true, color: "FFFFFF", fill: { color: C.primary }, fontSize: 11, fontFace: FONT } },
      { text: "当前 → 目标", options: { bold: true, color: "FFFFFF", fill: { color: C.primary }, fontSize: 11, fontFace: FONT } },
    ],
  ];
  const goals = [
    ["安全", "🔴 硬编码 → 🟢 全外部化"],
    ["测试", "🔴 零覆盖 → 🟢 >60%"],
    ["质量", "🟡 死代码 → 🟢 TS strict"],
    ["运维", "🟡 无 CI → 🟢 自动化"],
  ];
  const goalRows = goals.map((r, i) => [
    { text: r[0], options: { fontSize: 11, fontFace: FONT, bold: true, fill: { color: i % 2 === 0 ? C.lightBg : C.white } } },
    { text: r[1], options: { fontSize: 11, fontFace: FONT, fill: { color: i % 2 === 0 ? C.lightBg : C.white } } },
  ]);
  s.addTable([...goalHeader, ...goalRows], {
    x: 5.4, y: 1.8, w: 3.9,
    colW: [0.9, 3.0],
    border: { pt: 0.5, color: C.border },
    rowH: 0.5,
  });
}

// ══════════════════════════════════════════════════
// Slide 12: 总结
// ══════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.darkBg };

  s.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 0, w: 10, h: 0.06,
    fill: { color: C.blue },
  });

  s.addText("总结", {
    x: 1, y: 0.8, w: 8, h: 0.8,
    fontSize: 36, fontFace: FONT, bold: true,
    color: C.white, align: "center", valign: "middle",
  });

  s.addShape(pres.shapes.RECTANGLE, {
    x: 3.5, y: 1.7, w: 3, h: 0.03,
    fill: { color: C.blue },
  });

  // 已完成
  s.addText([
    { text: "已完成", options: { bold: true, fontSize: 16, color: "93C5FD" } },
    { text: "  —  核心功能全部上线 · 前端架构现代化 · 容器化部署 · 可观测性基础", options: { fontSize: 14, color: "CBD5E1" } },
  ], {
    x: 1, y: 2.2, w: 8, h: 0.6,
    fontFace: FONT, align: "center", valign: "middle",
  });

  // 正在推进
  s.addText([
    { text: "正在推进", options: { bold: true, fontSize: 16, color: "93C5FD" } },
    { text: "  —  智能对话 JSON 解析稳定性优化", options: { fontSize: 14, color: "CBD5E1" } },
  ], {
    x: 1, y: 3.0, w: 8, h: 0.6,
    fontFace: FONT, align: "center", valign: "middle",
  });

  // 下一步
  s.addText([
    { text: "下一步", options: { bold: true, fontSize: 16, color: "93C5FD" } },
    { text: "  —  安全加固 → 测试建设 → 质量提升 → 运维完善", options: { fontSize: 14, color: "CBD5E1" } },
  ], {
    x: 1, y: 3.8, w: 8, h: 0.6,
    fontFace: FONT, align: "center", valign: "middle",
  });

  s.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 5.565, w: 10, h: 0.06,
    fill: { color: C.blue },
  });
}

// ── Write file ──
const outPath = "/Users/zhouminhao/Projects/work/AI_team/SupplierRiskAnalysisAgent/docs/presentation/2026-06-16-project-progress-editable.pptx";
pres.writeFile({ fileName: outPath }).then(() => {
  console.log("PPTX written to:", outPath);
});
