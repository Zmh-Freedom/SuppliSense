import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { chatRunEventStream, chatStream, dispatchStreamEvent, resumeChat } from '../api';
import type { ApprovalData, StreamCallbacks } from '../api';
import type { AgentAnswer, AgentEvidenceRecord, AgentWorkflowLifecycle, AgentWorkflowSnapshot, ChatMessage, SupplierReference } from '../types';
import AgentTrendCharts from './AgentTrendCharts';
import type { AgentStatus } from './AgentWorkflowPanel';
import { ConclusionSummary, EvidenceTable, ReviewChecklist } from './ChatResultSections';
import ChatMessageList, { type ChatStreamViewState } from './ChatMessageList';

const CAPABILITIES = [
  { label: '风险评估', desc: '全面分析企业风险状况', prompt: '对「公司名」进行全面的风险评估' },
  { label: '舆情分析', desc: '监测企业最新舆情动态', prompt: '分析「公司名」的最新舆情和新闻动态' },
  { label: '合规筛查', desc: '制裁名单与黑名单筛查', prompt: '对「公司名」进行制裁名单和合规筛查' },
  { label: '趋势预测', desc: '预测未来风险变化趋势', prompt: '预测「公司名」未来6-12个月的风险趋势' },
  { label: '关系图谱', desc: '供应链关系与传染风险', prompt: '分析「公司名」的供应链关系和传染风险' },
  { label: '报告生成', desc: '一键生成风险评估报告', prompt: '生成「公司名」的风险评估报告' },
];

const RECOMMENDED = [
  '复核青岛三祥科技股份有限公司',
  '复核上海汽车制动系统有限公司',
];

interface Session {
  sid: string;
  title: string;
  msgs: ChatMessage[];
  updatedAt: number;
}

const STORAGE_KEY = 'chat_sessions';

function createSessionId(): string {
  try {
    const randomUuid = globalThis.crypto?.randomUUID;
    if (typeof randomUuid === 'function') return randomUuid.call(globalThis.crypto);
  } catch {
    // LAN demos may run over plain HTTP where randomUUID is unavailable.
  }
  return `session-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function agentFromTool(tool: string): string | null {
  const match = tool.match(/^(sourcing|risk|compliance|sentiment)_agent$/);
  return match ? match[1] : null;
}

function loadSessions(): Session[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function saveSessions(sessions: Session[]) {
  // keep last 50 sessions max
  const trimmed = sessions.slice(-50);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
}

type StreamState = ChatStreamViewState;

function createWorkflowSnapshot(): AgentWorkflowSnapshot {
  return {
    status: 'running',
    stage: 'understand',
    message: '正在分析您的问题…',
    targetSuppliers: [],
    sources: [],
    toolCallCount: 0,
    completedToolCount: 0,
  };
}

function normalizeApprovalAnswer(answer: string, approved?: boolean): string {
  const waitingText = /已生成\s*\d+\s*项加入监控操作，等待人工确认后才会写入监控清单。?/;
  if (approved === true) {
    return waitingText.test(answer)
      ? answer.replace(waitingText, '加入监控操作已获批准，服务端已返回执行结果。')
      : answer;
  }
  if (approved === false) {
    return waitingText.test(answer)
      ? answer.replace(waitingText, '加入监控操作已拒绝，系统未写入监控清单。')
      : answer;
  }
  return waitingText.test(answer)
    ? answer.replace(waitingText, '分析已完成；如需执行加入监控，请在下方“执行详情”中确认。')
    : answer;
}

const ANSWER_STATUS_META: Record<string, { label: string; className: string; icon: string }> = {
  completed: { label: '分析完成', className: 'border-emerald-200 bg-emerald-50 text-emerald-700', icon: '✓' },
  partial: { label: '部分完成', className: 'border-amber-200 bg-amber-50 text-amber-700', icon: '!' },
  needs_review: { label: '需要人工确认', className: 'border-amber-200 bg-amber-50 text-amber-700', icon: '!' },
  failed: { label: '暂未完成', className: 'border-red-200 bg-red-50 text-red-700', icon: '×' },
};

const DIMENSION_LABELS: Record<string, string> = {
  risk: '综合风险',
  financial: '财务风险',
  business: '商务风险',
  business_risk: '商务风险',
  quality: '质量风险',
  delivery: '交付风险',
  compliance: '合规',
  esg: 'ESG',
  sentiment: '舆情',
  sourcing: '寻源',
};

const DIMENSION_BADGE_CLASSES: Record<string, string> = {
  risk: 'border-blue-200 bg-blue-50 text-blue-700',
  financial: 'border-indigo-200 bg-indigo-50 text-indigo-700',
  business: 'border-teal-200 bg-teal-50 text-teal-700',
  business_risk: 'border-teal-200 bg-teal-50 text-teal-700',
  quality: 'border-cyan-200 bg-cyan-50 text-cyan-700',
  delivery: 'border-sky-200 bg-sky-50 text-sky-700',
  compliance: 'border-rose-200 bg-rose-50 text-rose-700',
  esg: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  sentiment: 'border-amber-200 bg-amber-50 text-amber-700',
  sourcing: 'border-violet-200 bg-violet-50 text-violet-700',
};

function readableDimension(dimension: string): string {
  return DIMENSION_LABELS[dimension] || dimension;
}

function dimensionBadgeClass(dimension: string): string {
  return DIMENSION_BADGE_CLASSES[dimension] || 'border-stone-200 bg-stone-100 text-stone-700';
}

function readableLimitation(limitation: string): string {
  return limitation
    .replace(/缺少\s*([a-z_]+)\s*(维度的)?正式证据/gi, (_, dimension: string) => `${readableDimension(dimension)}数据覆盖不足`)
    .replace(/缺少\s*([a-z_]+)\s*证据/gi, (_, dimension: string) => `${readableDimension(dimension)}数据覆盖不足`)
    .replace(/缺少待评估供应商名称/g, '未识别到待评估供应商')
    .replace(/缺少待筛查供应商名称/g, '未识别到待筛查供应商')
    .replace(/缺少待分析供应商名称/g, '未识别到待分析供应商');
}

const FACT_LABELS: Record<string, string> = {
  risk_score: '综合风险评分',
  risk_level: '风险等级',
  revenue: '营业收入',
  net_profit: '净利润',
  revenue_growth: '营业收入同比增长率',
  net_profit_growth: '净利润同比增长率',
  debt_ratio: '资产负债率',
  cash_flow: '每股经营现金流',
  roe: '净资产收益率',
  net_profit_margin: '净利率',
  current_ratio: '流动比率',
  quick_ratio: '速动比率',
  total_score: 'ESG 综合评分',
  total_level: 'ESG 等级',
  calculated_level: 'ESG 等级',
  clean: '合规筛查结果',
  match_count: '风险命中记录数',
  supplier_spend_share: '采购依赖占比',
  active_supplier_count: '可替代供应商数量',
  exposure_level: '内部采购敞口等级',
  settlement_share: '实结算金额占比',
  latest_actual_settlement_amount: '最新月实结算金额',
  latest_received_record_count: '最新月收货记录数',
  comparison_supplier_count: '同月可比较供应商数量',
  missing_month_count: '最近 12 个月缺失交易月份数',
  settlement_change_ratio: '最新月实结算金额环比变化',
  receipt_record_change_ratio: '最新月收货记录数环比变化',
  settlement_without_receipts_month_count: '结算与收货记录不一致月份数',
  formal_business_score: '商务风险评分',
  coverage: '数据覆盖率',
  sentiment_score: '舆情倾向评分',
  negative_count: '负面信息数量',
};

const FACT_PATH_ALIASES: Record<string, string> = {
  'revenue_growth': 'revenue_growth',
  'net_profit_growth': 'net_profit_growth',
  'debt_ratio': 'debt_ratio',
  'cash_flow': 'cash_flow',
  'net_profit_margin': 'net_profit_margin',
  'current_ratio': 'current_ratio',
  'quick_ratio': 'quick_ratio',
  'return_on_equity': 'roe',
  'roe': 'roe',
  'revenue': 'revenue',
  'net_profit': 'net_profit',
};

function canonicalFactPath(path: string): string {
  const normalized = path.trim().toLowerCase().replace(/[\s-]+/g, '_');
  return FACT_PATH_ALIASES[normalized] || normalized;
}

const RISK_FACT_LABELS: Record<string, string> = {
  lawsuit_count: '司法诉讼',
  recent_lawsuits: '近三年诉讼',
  executed_count: '被执行记录',
  dishonesty_count: '失信记录',
  major_lawsuit: '重大诉讼',
  abnormal_operation_count: '经营异常',
  administrative_penalty_count: '行政处罚',
  guarantee_count: '对外担保',
  pledge_count: '股权质押',
  bankruptcy_count: '破产相关记录',
  env_penalty_count: '环保处罚',
  financial: '财务数据',
  judicial: '司法风险',
  operational: '经营风险',
};

function readableFactLabel(path: string): string {
  const canonicalPath = canonicalFactPath(path);
  return FACT_LABELS[canonicalPath] || RISK_FACT_LABELS[canonicalPath] || path
    .replaceAll('_', ' ')
    .replace(/\b\w/g, char => char.toUpperCase());
}

function readableFactValue(path: string, value: unknown): string {
  const canonicalPath = canonicalFactPath(path);
  if (canonicalPath === 'clean' && typeof value === 'boolean') return value ? '未命中风险记录' : '发现风险记录';
  if (canonicalPath === 'supplier_spend_share' && typeof value === 'number') return `${(value * 100).toFixed(1)}%`;
  if (canonicalPath === 'settlement_share' && typeof value === 'number') {
    return value > 0 && value < 0.001 ? '<0.1%' : `${(value * 100).toFixed(1)}%`;
  }
  if (canonicalPath === 'exposure_level' && typeof value === 'string') {
    return ({ high: '高敞口', medium: '中敞口', low: '低敞口', unknown: '暂无法判断' } as Record<string, string>)[value] ?? value;
  }
  if (canonicalPath === 'coverage' && typeof value === 'number') return `${(value * 100).toFixed(0)}%`;
  if (['revenue_growth', 'net_profit_growth', 'debt_ratio', 'roe', 'net_profit_margin', 'settlement_change_ratio', 'receipt_record_change_ratio'].includes(canonicalPath) && typeof value === 'number') return `${(value * 100).toFixed(1)}%`;
  if (['missing_month_count', 'settlement_without_receipts_month_count'].includes(canonicalPath) && typeof value === 'number') return `${value.toFixed(0)} 个月`;
  if (['current_ratio', 'quick_ratio'].includes(canonicalPath) && typeof value === 'number') return value.toFixed(2);
  if (canonicalPath === 'risk_score' && typeof value === 'number') return `${value}/100`;
  if (canonicalPath === 'cash_flow' && typeof value === 'number') return `${value.toFixed(2)} 元/股`;
  if (canonicalPath === 'latest_actual_settlement_amount' && typeof value === 'number') return `${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 元`;
  if (canonicalPath === 'latest_received_record_count' && typeof value === 'number') return `${value.toLocaleString('zh-CN')} 条`;
  if (canonicalPath === 'comparison_supplier_count' && typeof value === 'number') return `${value.toLocaleString('zh-CN')} 家`;
  if (typeof value === 'boolean') return value ? '有' : '无';
  return String(value);
}

function readableClaimStatement(claim: AgentAnswer['claims'][number]): string {
  let statement = claim.statement;
  if (claim.fact_path && claim.value !== undefined && claim.value !== null) {
    const label = readableFactLabel(claim.fact_path);
    const labelIndex = statement.indexOf(label);
    const rawPathIndex = statement.indexOf(claim.fact_path);
    const subjectEnd = labelIndex >= 0 ? labelIndex : rawPathIndex;
    const subject = subjectEnd >= 0 ? statement.slice(0, subjectEnd).trim() : '';
    statement = `${subject ? `${subject} ` : ''}${label}：${readableFactValue(claim.fact_path, claim.value)}`;
  } else if (claim.fact_path) {
    const label = readableFactLabel(claim.fact_path);
    statement = statement.replaceAll(claim.fact_path, label);
  }
  return statement
    .replace(/\s+为\s+/g, '：')
    .replaceAll('assess_risk', '风险评估')
    .replaceAll('assess_business_risk', '商务风险评估')
    .replaceAll('assess_operational_risk', '运营风险评估')
    .replaceAll('risk_service_result', '风险评估数据')
    .replaceAll('provider', '数据来源');
}

function readableClaimDetail(claim: AgentAnswer['claims'][number]): string {
  if (claim.fact_path && claim.value !== undefined && claim.value !== null) {
    return readableFactValue(claim.fact_path, claim.value);
  }
  return readableClaimStatement(claim)
    .replace(/^[^：:]{2,80}(?:有限公司|股份有限公司|有限责任公司|集团|公司)\s*/, '')
    .trim();
}

function readableProvider(provider: string, sourceType: string): string {
  const labels: Record<string, string> = {
    assess_risk: '综合风险评估',
    assess_business_risk: '商务风险评估',
    assess_operational_risk: '质量与交付评估',
    esg_assessment: 'ESG 评估',
    sentiment_analysis: '舆情分析',
    check_sanctions: '合规筛查',
    list_formal_suppliers: '正式供应商目录',
  };
  if (labels[provider]) return labels[provider];
  if (sourceType === 'risk_service_result') return '风险评估数据';
  if (sourceType === 'financial_provider') return '财务数据';
  if (sourceType === 'feishu_transaction_snapshot') return '采购交易数据';
  return '业务数据';
}

function readableEvidenceStatus(status: string): string {
  const labels: Record<string, string> = {
    available: '已获取',
    confirmed_empty: '已确认无记录',
    missing: '缺失',
    unavailable: '暂不可用',
    stale: '需要更新',
    conflicting: '存在冲突',
    synthetic: '演示数据',
  };
  return labels[status] || '已获取';
}

function readableValidationStatus(status: AgentAnswer['claims'][number]['validation_status']): string {
  const labels: Record<AgentAnswer['claims'][number]['validation_status'], string> = {
    supported: '证据支持',
    partial: '证据覆盖不足',
    conflicting: '证据存在冲突',
    unsupported: '暂无证据支持',
  };
  return labels[status];
}

function claimMetric(claim: AgentAnswer['claims'][number]): string {
  return claim.fact_path ? readableFactLabel(claim.fact_path) : '综合判断';
}

function claimAssessment(claim: AgentAnswer['claims'][number]): { label: string; className: string } {
  if (claim.validation_status === 'partial' || claim.validation_status === 'unsupported') {
    return { label: '当前未覆盖', className: 'border-amber-200 bg-amber-50 text-amber-700' };
  }
  if (claim.validation_status === 'conflicting') {
    return { label: '数据有冲突', className: 'border-red-200 bg-red-50 text-red-700' };
  }

  const value = claim.value;
  const path = canonicalFactPath(claim.fact_path || '');
  if (path === 'risk_level' && typeof value === 'string') {
    if (value.includes('低')) return { label: '风险较低', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' };
    if (value.includes('高')) return { label: '高风险信号', className: 'border-red-200 bg-red-50 text-red-700' };
    return { label: '需关注', className: 'border-amber-200 bg-amber-50 text-amber-700' };
  }
  if (path === 'clean' && typeof value === 'boolean') {
    return value
      ? { label: '未见风险信号', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' }
      : { label: '需关注', className: 'border-red-200 bg-red-50 text-red-700' };
  }
  if (typeof value === 'number') {
    if (['revenue_growth', 'net_profit_growth', 'cash_flow'].includes(path) && value < 0) {
      return { label: '需关注', className: 'border-amber-200 bg-amber-50 text-amber-700' };
    }
    if ((path === 'current_ratio' && value > 0 && value < 1) || (path === 'quick_ratio' && value > 0 && value < 0.8)) {
      return { label: '流动性需关注', className: 'border-amber-200 bg-amber-50 text-amber-700' };
    }
    if (['settlement_change_ratio', 'receipt_record_change_ratio'].includes(path) && Math.abs(value) >= 0.3) {
      return { label: '波动需复核', className: 'border-amber-200 bg-amber-50 text-amber-700' };
    }
    if (['missing_month_count', 'settlement_without_receipts_month_count'].includes(path) && value > 0) {
      return { label: '需核实', className: 'border-amber-200 bg-amber-50 text-amber-700' };
    }
    if (path === 'risk_score') {
      if (value >= 60) return { label: '高风险信号', className: 'border-red-200 bg-red-50 text-red-700' };
      if (value >= 30) return { label: '需关注', className: 'border-amber-200 bg-amber-50 text-amber-700' };
      return { label: '风险较低', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' };
    }
  }
  if (path === 'exposure_level' && typeof value === 'string') {
    if (value === 'high') return { label: '高敞口', className: 'border-red-200 bg-red-50 text-red-700' };
    if (value === 'medium') return { label: '需关注', className: 'border-amber-200 bg-amber-50 text-amber-700' };
    if (value === 'low') return { label: '低敞口', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' };
  }
  return { label: '已获取，需结合复核', className: 'border-stone-200 bg-stone-50 text-stone-700' };
}

function analysisOverview(answer: AgentAnswer, limitations: string[]): string {
  const dimensions = [...new Set(answer.claims.map(claim => readableDimension(claim.dimension)))];
  const supportedCount = answer.claims.filter(claim => claim.validation_status === 'supported').length;
  const scope = dimensions.length > 0 ? dimensions.join('、') : '现有数据';
  const evidenceSummary = answer.claims.length > 0
    ? `本轮围绕${scope}形成 ${answer.claims.length} 条判断，其中 ${supportedCount} 条已有证据支持。`
    : `本轮已核对${scope}，但尚未形成可展示的确定性判断。`;

  if (answer.status === 'needs_review' || limitations.length > 0) {
    return `${evidenceSummary} 当前存在数据覆盖或证据限制，以下内容用于安排复核，不应直接视为最终准入或风险结论。`;
  }
  return `${evidenceSummary} 以下按结论、证据支持度和可信度汇总，便于理解推荐依据或需要关注的风险信号。`;
}

function buildRiskItems(answer?: AgentAnswer, evidence: AgentEvidenceRecord[] = []): string[] {
  const items = new Set<string>();
  answer?.claims.forEach(claim => {
    if (claim.dimension) items.add(readableDimension(claim.dimension));
    if (claim.fact_path) items.add(readableFactLabel(claim.fact_path));
  });
  evidence.forEach(record => {
    if (record.dimension && record.dimension !== 'risk') items.add(readableDimension(record.dimension));
    const facts = record.facts || {};
    if (record.dimension === 'risk') {
      if ('risk_score' in facts) items.add('综合风险评分');
      if ('risk_level' in facts) items.add('风险等级');
      const riskDetail = facts.risk_detail;
      if (riskDetail && typeof riskDetail === 'object' && !Array.isArray(riskDetail)) {
        Object.keys(riskDetail).forEach(key => {
          if (key !== 'data_coverage' && key !== 'in_watchlist' && key in RISK_FACT_LABELS) items.add(RISK_FACT_LABELS[key]);
        });
      }
      const coverage = facts.data_coverage;
      if (coverage && typeof coverage === 'object' && !Array.isArray(coverage)) {
        const available = (coverage as { available_dimensions?: unknown }).available_dimensions;
        if (Array.isArray(available)) available.forEach(item => items.add(readableFactLabel(String(item))));
      }
    }
    if (record.dimension === 'business_risk') {
      if ('supplier_spend_share' in facts) items.add('采购依赖占比');
      if ('active_supplier_count' in facts) items.add('可替代供应商数量');
      if ('exposure_level' in facts) items.add('内部采购敞口');
      if ('settlement_share' in facts) items.add('实结算金额占比');
      if ('latest_received_record_count' in facts) items.add('收货记录数');
    }
  });
  return [...items];
}

function formatEvidenceDate(value: string): string {
  if (!value) return '时间未知';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function numericFact(facts: Record<string, unknown> | undefined, key: string): number | null {
  const value = facts?.[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function reviewClaims(answer: AgentAnswer, paths: string[]): string[] {
  return answer.claims
    .filter(claim => claim.fact_path !== null && claim.fact_path !== undefined && paths.includes(claim.fact_path))
    .map(readableClaimStatement);
}

function ReviewList({ title, items, className, ordered = false }: { title: string; items: string[]; className: string; ordered?: boolean }) {
  if (items.length === 0) return null;
  return <article className={`rounded-xl border p-3.5 ${className}`}>
    <h4 className="text-sm font-semibold">{title}</h4>
    {ordered ? <ol className="mt-2 space-y-2 text-sm leading-6">
      {items.map((item, index) => <li key={item} className="flex gap-2"><span aria-hidden="true" className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-white/70 text-[11px] font-semibold">{index + 1}</span><span>{item}</span></li>)}
    </ol> : <ul className="mt-2 space-y-1.5 text-sm leading-6">
      {items.map(item => <li key={item} className="flex gap-2"><span aria-hidden="true">•</span><span>{item}</span></li>)}
    </ul>}
  </article>;
}

function SupplierReviewConclusion({ answer, evidence, limitations }: { answer: AgentAnswer; evidence: AgentEvidenceRecord[]; limitations: string[] }) {
  const businessFacts = evidence.find(record => record.dimension === 'business_risk')?.facts;
  const netProfitGrowth = answer.claims.find(claim => claim.fact_path === 'net_profit_growth')?.value;
  const financeMissing = limitations.some(item => item.includes('财务'));
  const settlementChange = numericFact(businessFacts, 'settlement_change_ratio');
  const missingMonths = numericFact(businessFacts, 'missing_month_count');
  const settlementWithoutReceipts = numericFact(businessFacts, 'settlement_without_receipts_month_count');
  const findings: string[] = [];
  const basis: string[] = [];
  const checks: string[] = [];
  const boundaries: string[] = [];

  if (typeof netProfitGrowth === 'number' && netProfitGrowth < 0) {
    findings.push('盈利指标出现下滑，需要复核是否影响后续履约与合作稳定性。');
    basis.push(...reviewClaims(answer, ['revenue_growth', 'net_profit_growth', 'debt_ratio', 'cash_flow']));
    checks.push('核对最近一期财报的报告期、下滑原因、现金流变化，以及供应商对在手订单的履约说明。');
  }
  if (financeMissing) {
    findings.push('财务信息覆盖不足，当前无法判断其财务变化。');
    basis.push('本轮未取得可用的财务正式证据；这不是低风险或高风险判断。');
    checks.push('核对本轮已覆盖的数据范围；财务维度未形成结论，不据此判断供应商经营状况。');
    boundaries.push('未取得财务数据时，系统不形成财务风险结论，也不将数据缺失判定为低风险或高风险。');
  }
  if (settlementChange !== null && settlementChange <= -0.5) {
    findings.push('最新月实结算金额环比显著下降，需要核实交易变化原因。');
    basis.push(...reviewClaims(answer, ['settlement_change_ratio', 'latest_actual_settlement_amount', 'latest_received_record_count']));
    checks.push('向采购与财务核对该月是否存在结算跨月、退货冲销、暂停采购或订单调整，并保留对应单据。');
  }
  if ((missingMonths ?? 0) > 0 || (settlementWithoutReceipts ?? 0) > 0) {
    findings.push('采购交易连续性存在需要人工确认的信号。');
    basis.push(...reviewClaims(answer, ['missing_month_count', 'settlement_without_receipts_month_count']));
    checks.push('核对缺失月份是否为未导入、无交易或口径变更；对结算与收货记录不一致的月份逐笔查验。');
  }
  if (businessFacts) {
    boundaries.push('收货记录数仅表示源明细行数，不代表零件数量、送货批次或交付能力。');
    boundaries.push('结算变化只是复核信号，不能单独推断供应中断、付款逾期或供应商经营风险。');
  }
  if (findings.length === 0) return null;

  return <ReviewChecklist><section className="mt-4 border-t border-[var(--color-border)] px-4 pb-4 pt-4 sm:px-5" aria-label="采购复核结论">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div>
        <div className="flex items-center gap-1.5"><span aria-hidden="true" className="text-[var(--color-primary-bg)]">③</span><p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-secondary)]">采购复核结论</p></div>
        <h3 className="mt-1 text-base font-semibold text-[var(--color-text)]">发现问题、查看依据、明确下一步</h3>
      </div>
      <span className="rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-xs font-medium text-violet-700">需采购人员核实</span>
    </div>
    <div className="mt-3 grid gap-3 lg:grid-cols-3">
      <ReviewList title="发现了什么" items={[...new Set(findings)]} className="border-amber-200 bg-amber-50/70 text-amber-950" />
      <ReviewList title="依据是什么" items={[...new Set(basis)]} className="border-sky-200 bg-sky-50/70 text-sky-950" />
      <ReviewList title="采购人员需要核实什么" items={[...new Set(checks)]} className="border-violet-200 bg-violet-50/70 text-violet-950" ordered />
    </div>
    {boundaries.length > 0 && <div className="mt-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-code-bg)]/55 p-3 text-xs leading-5 text-[var(--color-text-secondary)]">
      <p className="font-medium text-[var(--color-text)]">数据边界</p>
      <ul className="mt-1.5 list-disc space-y-1 pl-4">{[...new Set(boundaries)].map(item => <li key={item}>{item}</li>)}</ul>
    </div>}
  </section></ReviewChecklist>;
}

function ActionSummary({ answer, limitations }: { answer: AgentAnswer; limitations: string[] }) {
  const supportedCount = answer.claims.filter(claim => claim.validation_status === 'supported').length;
  const needsReview = answer.status === 'needs_review' || limitations.length > 0;
  const hasClaims = answer.claims.length > 0;
  const conclusion = needsReview
    ? '建议复核'
    : hasClaims ? '已形成初步结论' : '暂未形成结论';
  const explanation = needsReview
    ? '发现了需要人工确认的信号或数据缺口，请先完成下方复核事项。'
    : hasClaims ? '当前判断已有可追溯证据支持，可结合明细安排后续动作。' : '当前证据不足以支持明确判断，已标注本轮未覆盖的数据范围。';
  const nextAction = needsReview
    ? '核对复核事项'
    : hasClaims ? '查看结论明细' : '查看数据范围';

  return <ConclusionSummary><section className="border-b border-[var(--color-border)] bg-[var(--color-code-bg)]/40 px-4 py-4 sm:px-5" aria-label="行动结论">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-secondary)]">① 结论摘要</p>
        <h3 className="mt-1 text-lg font-semibold text-[var(--color-text)]">{conclusion}</h3>
        <p className="mt-1 max-w-2xl text-sm leading-6 text-[var(--color-text-secondary)]">{explanation}</p>
      </div>
      <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${needsReview ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-emerald-200 bg-emerald-50 text-emerald-800'}`}>{needsReview ? '需采购人员处理' : '可继续推进'}</span>
    </div>
    <dl className="mt-4 grid divide-y divide-[var(--color-border)] overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] sm:grid-cols-3 sm:divide-x sm:divide-y-0">
      <div className="px-3 py-2.5"><dt className="text-[11px] text-[var(--color-text-secondary)]">处理状态</dt><dd className="mt-1 text-sm font-medium text-[var(--color-text)]">{conclusion}</dd></div>
      <div className="px-3 py-2.5"><dt className="text-[11px] text-[var(--color-text-secondary)]">证据覆盖</dt><dd className="mt-1 text-sm font-medium text-[var(--color-text)]">{hasClaims ? `${supportedCount} / ${answer.claims.length} 条已支持` : '暂无可用证据'}</dd></div>
      <div className="px-3 py-2.5"><dt className="text-[11px] text-[var(--color-text-secondary)]">下一步</dt><dd className="mt-1 text-sm font-medium text-[var(--color-text)]">{nextAction}</dd></div>
    </dl>
    {answer.action_proposals.length > 0 && <div className="mt-3 rounded-xl border border-indigo-200 bg-indigo-50/60 px-3 py-2.5 text-sm text-indigo-950">
      <p className="text-[11px] font-medium text-indigo-700">采购动作建议</p>
      {answer.action_proposals.map((proposal, index) => {
        const item = proposal as Record<string, unknown>;
        return <div key={`${String(item.action_type || 'action')}-${index}`} className="mt-1.5">
          <span className="font-medium">{String(item.label || '安排人工复核')}</span>
          {item.reason != null && <span className="ml-1 text-indigo-900/75">{String(item.reason)}</span>}
        </div>;
      })}
    </div>}
  </section></ConclusionSummary>;
}

function answerStatusMeta(answer: AgentAnswer, limitations: string[]) {
  const text = limitations.join(' ');
  if (text.includes('主体')) return { label: '主体待确认', className: 'border-violet-200 bg-violet-50 text-violet-700', icon: '?' };
  if (text.includes('权限')) return { label: '权限不足', className: 'border-red-200 bg-red-50 text-red-700', icon: '×' };
  if (text.includes('资料') || text.includes('财务')) return { label: '当前未覆盖', className: 'border-amber-200 bg-amber-50 text-amber-700', icon: '!' };
  if (answer.status === 'needs_review') return { label: '发现信号，需人工复核', className: 'border-amber-200 bg-amber-50 text-amber-700', icon: '!' };
  return ANSWER_STATUS_META[answer.status] || { label: answer.status, className: 'border-gray-200 bg-gray-50 text-gray-600', icon: '·' };
}

function StructuredAgentResult({ answer, evidence }: { answer?: AgentAnswer; evidence?: AgentEvidenceRecord[] }) {
  if (!answer && (!evidence || evidence.length === 0)) return null;
  const limitations = answer?.limitations.map(readableLimitation).filter(Boolean) || [];
  const status = answer ? answerStatusMeta(answer, limitations) : null;
  const riskItems = buildRiskItems(answer, evidence || []);
  const overview = answer ? analysisOverview(answer, limitations) : '';

  return <section className="mt-4 overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] shadow-sm" aria-label="Agent 分析结果">
    {answer && <ActionSummary answer={answer} limitations={limitations} />}
    {answer && <div className="bg-gradient-to-br from-[var(--color-surface)] to-[var(--color-code-bg)]/60 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-1.5"><span aria-hidden="true" className="text-[var(--color-primary-bg)]">②</span><p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-secondary)]">业务结论</p></div>
          <h3 className="mt-1 text-base font-semibold text-[var(--color-text)]">本轮分析结果</h3>
        </div>
        {status && <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${status.className}`}>
          <span aria-hidden="true">{status.icon}</span>{status.label}
        </span>}
      </div>
      <p className="mt-3 max-w-4xl text-sm leading-6 text-[var(--color-text-secondary)]">{overview}</p>
      {answer.claims.length > 0 && <EvidenceTable><div className="mt-4 w-full overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]">
        <table className="w-full table-fixed border-collapse text-left text-sm">
          <thead className="bg-[var(--color-code-bg)]/75 text-xs text-[var(--color-text-secondary)]">
            <tr>
              <th scope="col" className="w-[18%] px-2 py-2.5 font-medium sm:px-3">维度</th>
              <th scope="col" className="w-[23%] px-2 py-2.5 font-medium sm:px-3">指标/检查项</th>
              <th scope="col" className="w-[29%] px-2 py-2.5 font-medium sm:px-3">数据与说明</th>
              <th scope="col" className="w-[20%] px-2 py-2.5 font-medium sm:px-3">判断</th>
              <th scope="col" className="hidden w-[10%] px-3 py-2.5 font-medium md:table-cell">证据状态</th>
              <th scope="col" className="hidden w-[10%] px-3 py-2.5 text-right font-medium md:table-cell">可信度</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--color-border)]">
            {answer.claims.map(claim => {
              const assessment = claimAssessment(claim);
              return <tr key={claim.claim_id} className="align-top">
                <td className="break-words px-2 py-3 sm:px-3"><span className={`inline-flex max-w-full rounded-md border px-2 py-0.5 text-[11px] font-medium ${dimensionBadgeClass(claim.dimension)}`}>{readableDimension(claim.dimension)}</span></td>
                <td className="break-words px-2 py-3 font-medium leading-6 text-[var(--color-text)] sm:px-3">{claimMetric(claim)}</td>
                <td className="break-words px-2 py-3 leading-6 text-[var(--color-text)] sm:px-3">{readableClaimDetail(claim)}</td>
                <td className="break-words px-2 py-3 sm:px-3"><span className={`inline-flex max-w-full rounded-md border px-2 py-0.5 text-xs font-medium ${assessment.className}`}>{assessment.label}</span></td>
                <td className="hidden px-3 py-3 text-xs text-[var(--color-text-secondary)] md:table-cell">{readableValidationStatus(claim.validation_status)}</td>
                <td className="hidden px-3 py-3 text-right text-xs tabular-nums text-[var(--color-text-secondary)] md:table-cell">{claim.confidence > 0 ? `${Math.round(claim.confidence * 100)}%` : '—'}</td>
              </tr>;
            })}
          </tbody>
        </table>
      </div></EvidenceTable>}
    </div>}

    {answer && <SupplierReviewConclusion answer={answer} evidence={evidence || []} limitations={limitations} />}

    <AgentTrendCharts evidence={evidence || []} limitations={limitations} />

    <details className="group border-t border-[var(--color-border)]">
      <summary className="flex min-h-[48px] cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-left text-sm font-medium text-[var(--color-text)] [&::-webkit-details-marker]:hidden">
        <span className="flex items-center gap-2"><span aria-hidden="true" className="text-[var(--color-primary-bg)]">▤</span><span>数据说明</span><span className="text-[var(--color-text-secondary)]">（来源与边界）</span></span>
        <span className="flex items-center gap-2 text-xs font-normal text-[var(--color-text-secondary)]"><span>{evidence?.length || 0} 条数据记录</span><span aria-hidden="true" className="transition-transform group-open:rotate-180">⌄</span></span>
      </summary>
      <div className="space-y-3 border-t border-[var(--color-border)] bg-[var(--color-code-bg)]/45 px-4 py-4 text-xs">
        {riskItems.length > 0 && <div className="rounded-xl border border-sky-200 bg-sky-50/70 p-3 text-sky-950">
          <p className="font-medium">本次已评估的风险项</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {riskItems.map(item => <span key={item} className="rounded-full border border-sky-200 bg-white/80 px-2.5 py-1 text-[11px]">{item}</span>)}
          </div>
        </div>}
        {limitations.length > 0 && <div className="rounded-xl border border-amber-200 bg-amber-50/70 p-3 text-amber-900">
          <p className="font-medium">数据覆盖与限制</p>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 leading-5">{limitations.map(item => <li key={item}>{item}</li>)}</ul>
        </div>}
        {answer && <div className="grid gap-2 sm:grid-cols-3">
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3"><p className="text-[var(--color-text-secondary)]">确定性结论</p><p className="mt-1 font-semibold text-[var(--color-text)]">{answer.claims.length} 条</p></div>
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3"><p className="text-[var(--color-text-secondary)]">有效来源</p><p className="mt-1 font-semibold text-[var(--color-text)]">{answer.evidence_refs.length} 条引用</p></div>
          {answer.action_receipts.length > 0 && <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3"><p className="text-[var(--color-text-secondary)]">执行回执</p><p className="mt-1 font-semibold text-[var(--color-text)]">{answer.action_receipts.length} 条</p></div>}
        </div>}
        {evidence && evidence.length > 0 && <div className="space-y-2">
          <p className="font-medium text-[var(--color-text)]">数据来源</p>
          {evidence.map(record => <div key={record.evidence_id} className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
            <div className="flex flex-wrap items-center justify-between gap-2 text-[var(--color-text)]"><span className="font-medium">{readableProvider(record.provider, record.source_type)}</span><span className="text-[var(--color-text-secondary)]">{readableEvidenceStatus(record.status)}</span></div>
            <p className="mt-1 leading-5 text-[var(--color-text-secondary)]">{record.data_mode === 'synthetic' ? '演示数据' : '正式数据'} · 更新于 {formatEvidenceDate(record.collected_at)}</p>
            <details className="mt-2 rounded-lg bg-[var(--color-code-bg)] px-2.5 py-2">
              <summary className="cursor-pointer text-[11px] text-[var(--color-text-secondary)]">查看审计原始数据（含系统字段）</summary>
              <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-[var(--color-text-secondary)]">{JSON.stringify({ evidence_id: record.evidence_id, entity_id: record.entity_id, facts: record.facts || {} }, null, 2)}</pre>
            </details>
          </div>)}
        </div>}
        {!limitations.length && !evidence?.length && <p className="text-[var(--color-text-secondary)]">本轮没有可展示的数据说明。</p>}
      </div>
    </details>
  </section>;
}

export default function ChatView() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [sessions, setSessions] = useState<Session[]>(loadSessions);
  const [activeSid, setActiveSid] = useState<string>(() => {
    const list = loadSessions();
    return list.length > 0 ? list[list.length - 1].sid : '';
  });
  const [input, setInput] = useState<string>(() => {
    const queryInput = searchParams.get('q');
    if (queryInput) return queryInput;
    try { return localStorage.getItem('chat_input') || ''; } catch { return ''; }
  });

  useEffect(() => {
    if (!searchParams.get('q')) return;
    const next = new URLSearchParams(searchParams);
    next.delete('q');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const [loading, setLoading] = useState(false);
  const [streamState, setStreamState] = useState<StreamState | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const saveTimerRef = useRef<number | null>(null);
  const answerAccRef = useRef<string>('');  // 累积流式答案，用于 onDone 回退
  const approvalAccRef = useRef<ApprovalData | null>(null);
  const referencesAccRef = useRef<SupplierReference[]>([]);
  const agentAnswerAccRef = useRef<AgentAnswer | undefined>(undefined);
  const evidenceAccRef = useRef<AgentEvidenceRecord[]>([]);
  const workflowAccRef = useRef<AgentWorkflowSnapshot>(createWorkflowSnapshot());

  // Debounced localStorage save for chat input
  useEffect(() => {
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      try {
        localStorage.setItem('chat_input', input);
      } catch {
        return;
      }
    }, 500);
    return () => { if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current); };
  }, [input]);

  const active = sessions.find(s => s.sid === activeSid);
  const msgs = useMemo(() => active?.msgs ?? [], [active]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [msgs, streamState]);

  const persist = useCallback((sid: string, newMsgs: ChatMessage[], create = false) => {
    const list = loadSessions();
    const idx = list.findIndex(s => s.sid === sid);
    const title = newMsgs.find(m => m.role === 'user')?.content.slice(0, 40) || '新对话';
    const session: Session = { sid, title, msgs: newMsgs, updatedAt: Date.now() };

    if (idx >= 0) list[idx] = session;
    else if (create) list.push(session);
    else return;

    list.sort((a, b) => b.updatedAt - a.updatedAt);
    saveSessions(list);
    setSessions(list);
  }, []);

  const send = useCallback(async (msg?: string, forceNewSession = false) => {
    const text = (msg ?? inputRef.current?.value ?? input).trim();
    if (!text || loading) return;
    setInput('');

    const isNewSession = forceNewSession || !activeSid;
    const sid = isNewSession ? createSessionId() : activeSid;
    if (isNewSession) setActiveSid(sid);

    const newMsgs: ChatMessage[] = [...msgs, { role: 'user', content: text }];
    persist(sid, newMsgs, isNewSession);
    setLoading(true);
    const initialWorkflow = createWorkflowSnapshot();
    workflowAccRef.current = initialWorkflow;
    setStreamState({ thinking: '', plan: null, agents: null, toolCalls: [], answerChunks: [], answerStarted: false, approval: null, approvalSubmitting: false, charts: [], references: [], agentAnswer: null, evidence: [], workflowStatus: initialWorkflow });
    answerAccRef.current = '';
    approvalAccRef.current = null;
    referencesAccRef.current = [];
    agentAnswerAccRef.current = undefined;
    evidenceAccRef.current = [];

    let runId: string | null = null;
    let lastEventId: number | null = null;
    let receivedTerminalEvent = false;
    const streamCallbacks: StreamCallbacks = {
        onRun: (data) => {
          runId = data.run_id;
        },
        onEventId: (eventId) => {
          lastEventId = eventId;
        },
        onThinking: (data) => {
          setStreamState(prev => prev ? { ...prev, thinking: data.message } : null);
        },
        onWorkflowStatus: (data) => {
          const previous = workflowAccRef.current;
          const next: AgentWorkflowSnapshot = {
            ...previous,
            status: data.status as AgentWorkflowLifecycle | string,
            stage: data.stage ?? previous.stage,
            message: data.message,
            runId: data.run_id ?? previous.runId,
            targetSuppliers: data.target_suppliers ?? previous.targetSuppliers,
            sources: data.sources ?? previous.sources,
            evidenceStatus: data.evidence_status ?? previous.evidenceStatus,
            loopExitReason: data.loop_exit_reason ?? previous.loopExitReason,
            toolCallCount: data.tool_call_count ?? previous.toolCallCount,
            completedToolCount: data.completed_tool_count ?? previous.completedToolCount,
          };
          workflowAccRef.current = next;
          setStreamState(prev => prev ? { ...prev, workflowStatus: next } : null);
        },
        onPlan: (data) => {
          setStreamState(prev => prev ? { ...prev, plan: data.steps } : null);
        },
        onAgentSelection: (data) => {
          setStreamState(prev => prev ? {
            ...prev,
            agents: {
              selected: data.agents,
              reasoning: data.reasoning,
              status: Object.fromEntries(data.agents.map(a => [a, 'running' as AgentStatus]))
            }
          } : null);
        },
        onAgentStart: (data) => {
          setStreamState(prev => {
            if (!prev) return null;
            const agents = prev.agents || { selected: [], reasoning: '', status: {} };
            return {
              ...prev,
              agents: {
                ...agents,
                selected: agents.selected.includes(data.agent) ? agents.selected : [...agents.selected, data.agent],
                status: { ...agents.status, [data.agent]: 'running' }
              }
            };
          });
        },
        onAgentComplete: (data) => {
          setStreamState(prev => {
            if (!prev) return null;
            const agents = prev.agents || { selected: [], reasoning: '', status: {} };
            return {
              ...prev,
              agents: {
                ...agents,
                selected: agents.selected.includes(data.agent) ? agents.selected : [...agents.selected, data.agent],
                status: { ...agents.status, [data.agent]: 'complete' }
              }
            };
          });
        },
        onToolCall: (data) => {
          setStreamState(prev => {
            if (!prev) return null;
            const agent = agentFromTool(data.tool);
            const agents = agent ? (prev.agents || { selected: [], reasoning: '', status: {} }) : prev.agents;
            return {
              ...prev,
              agents: agent && agents ? {
                ...agents,
                selected: agents.selected.includes(agent) ? agents.selected : [...agents.selected, agent],
                status: { ...agents.status, [agent]: 'running' },
              } : agents,
              toolCalls: [...prev.toolCalls, { tool: data.tool, args: data.args, task_id: data.task_id }],
              workflowStatus: { ...prev.workflowStatus, toolCallCount: prev.toolCalls.length + 1 },
            };
          });
        },
        onToolResult: (data) => {
          setStreamState(prev => {
            if (!prev) return null;
            const toolCalls = [...prev.toolCalls];
            const target = [...toolCalls].reverse().find(call =>
              (data.task_id && call.task_id === data.task_id) || (!data.task_id && call.tool === data.tool && call.result === undefined),
            );
            if (target) {
              target.result = data.result;
            }
            const agent = agentFromTool(data.tool);
            const agents = agent && prev.agents ? {
              ...prev.agents,
              status: { ...prev.agents.status, [agent]: 'complete' as AgentStatus },
            } : prev.agents;
            return { ...prev, agents, toolCalls, workflowStatus: { ...prev.workflowStatus, completedToolCount: toolCalls.filter(call => call.result !== undefined).length } };
          });
        },
        onAnswerChunk: (data) => {
          answerAccRef.current += data.text;
          setStreamState(prev => prev ? {
            ...prev,
            answerChunks: [...prev.answerChunks, data.text],
            answerStarted: true,
          } : null);
        },
        onDone: (data) => {
          receivedTerminalEvent = true;
          const pendingApproval = approvalAccRef.current;
          const rawAnswer = answerAccRef.current || data.answer;
          const finalAnswer = normalizeApprovalAnswer(rawAnswer);
          const contractStatus = agentAnswerAccRef.current?.status;
          const serverStatus = data.status || contractStatus || workflowAccRef.current.status;
          const hasServerTerminalStatus = ['completed', 'partial', 'needs_review', 'failed'].includes(serverStatus);
          const finalWorkflow = {
            ...workflowAccRef.current,
            status: pendingApproval ? 'waiting_approval' : (hasServerTerminalStatus ? serverStatus : 'failed') as AgentWorkflowLifecycle | string,
            stage: pendingApproval ? 'approval' : hasServerTerminalStatus && ['completed', 'partial'].includes(serverStatus) ? 'completed' : 'decision',
            message: pendingApproval ? '分析已完成，等待人工确认写操作' : !hasServerTerminalStatus ? '服务端未返回有效终态，已停止显示为成功' : serverStatus === 'needs_review' ? '结果需要人工复核' : serverStatus === 'partial' ? '本轮 Agent 仅完成部分分析' : serverStatus === 'failed' ? '本轮 Agent 执行失败' : '本轮 Agent 工作流已完成',
          };
          workflowAccRef.current = finalWorkflow;
          const completedMsgs: ChatMessage[] = [...newMsgs, { role: 'assistant', content: finalAnswer, references: referencesAccRef.current, agentAnswer: agentAnswerAccRef.current, evidence: evidenceAccRef.current, workflow: finalWorkflow, approval: pendingApproval ? { ...pendingApproval, status: 'pending' } : undefined }];
          answerAccRef.current = '';
          persist(sid, completedMsgs);
          setStreamState(null);
          setLoading(false);
        },
        onAgentAnswer: (data) => {
          agentAnswerAccRef.current = data;
          setStreamState(prev => prev ? { ...prev, agentAnswer: data } : null);
        },
        onEvidence: (data) => {
          evidenceAccRef.current = data.records;
          setStreamState(prev => prev ? { ...prev, evidence: data.records } : null);
        },
        onError: (data) => {
          receivedTerminalEvent = true;
          console.error('Stream error:', data.message);
          const failedWorkflow = { ...workflowAccRef.current, status: 'failed' as const, message: data.message };
          workflowAccRef.current = failedWorkflow;
          const failedMsgs: ChatMessage[] = [...newMsgs, { role: 'assistant', content: `错误：${data.message}`, workflow: failedWorkflow }];
          persist(sid, failedMsgs);
          setStreamState(prev => prev ? {
            ...prev,
            error: data.message,
            workflowStatus: failedWorkflow,
            approvalSubmitting: false,
            agents: prev.agents ? {
              ...prev.agents,
              status: Object.fromEntries(Object.entries(prev.agents.status).map(([agent, status]) => [agent, status === 'running' ? 'error' : status])) as Record<string, AgentStatus>,
            } : null,
          } : null);
          setLoading(false);
        },
        onClarification: (data) => {
          receivedTerminalEvent = true;
          const clarificationWorkflow = { ...workflowAccRef.current, status: 'stopped' as const, stage: data.stage || 'understand', message: data.message };
          workflowAccRef.current = clarificationWorkflow;
          const clarifiedMsgs: ChatMessage[] = [...newMsgs, { role: 'assistant', content: data.message, workflow: clarificationWorkflow }];
          persist(sid, clarifiedMsgs);
          setStreamState(null);
          setLoading(false);
        },
        onApprovalRequired: (data) => {
          approvalAccRef.current = { ...data, status: 'pending' };
          const waiting = { ...workflowAccRef.current, status: 'waiting_approval' as const, stage: 'approval', message: '等待人工确认后继续执行' };
          workflowAccRef.current = waiting;
          setStreamState(prev => prev ? { ...prev, approval: data, workflowStatus: waiting } : null);
        },
        onChartData: (data) => {
          setStreamState(prev => prev ? {
            ...prev,
            charts: [...prev.charts, data]
          } : null);
        },
        onReferences: (data) => {
          referencesAccRef.current = data.items;
          const next = {
            ...workflowAccRef.current,
            targetSuppliers: data.items.map(item => item.name),
            sources: [...new Set(data.items.map(item => item.source || item.discovery_source).filter((item): item is string => Boolean(item)))],
          };
          workflowAccRef.current = next;
          setStreamState(prev => prev ? { ...prev, references: data.items, workflowStatus: next } : null);
        },
      };

    try {
      await chatStream(text, sid, streamCallbacks, 'auto');
    } catch (err) {
      if (runId && !receivedTerminalEvent) {
        try {
          await chatRunEventStream(runId, lastEventId, {
            onEvent: (event) => {
              dispatchStreamEvent(event.eventType, event.data, streamCallbacks);
              lastEventId = event.eventId;
            },
          });
          if (receivedTerminalEvent) return;
        } catch (replayError) {
          console.error('Chat event replay failed:', replayError);
        }
      }
      const isTimeout = err instanceof DOMException && err.name === 'AbortError';
      const failureMessage = isTimeout ? '请求超时（2分钟），请简化问题后重试' : '请求失败，请重试';
      const failedWorkflow = { ...workflowAccRef.current, status: 'failed' as const, message: isTimeout ? '工作流超时，尚未完成' : '工作流执行失败' };
      workflowAccRef.current = failedWorkflow;
      const failedMsgs: ChatMessage[] = [...newMsgs, { role: 'assistant', content: failureMessage, workflow: failedWorkflow }];
      persist(sid, failedMsgs);
      setStreamState(null);
      setLoading(false);
    }
  }, [input, loading, activeSid, msgs, persist]);

  const handleApproval = useCallback(async (approved: boolean) => {
    const persistedApprovalIndex = [...msgs].map((message, index) => ({ message, index })).reverse().find(
      item => item.message.role === 'assistant' && item.message.approval && !['approved', 'rejected'].includes(item.message.approval.status || ''),
    )?.index;
    const persistedApproval = persistedApprovalIndex === undefined ? null : msgs[persistedApprovalIndex].approval;
    const approval = streamState?.approval || persistedApproval;
    if (!approval || ['approved', 'rejected'].includes(approval.status || '')) return;
    const { session_id: approvalSid } = approval;

    // Persist the pending approval before resuming so a refresh does not lose
    // the only place where the user can confirm the write action.
    const submittingApproval: ApprovalData = { ...approval, status: 'submitting' };
    let resumeMsgs: ChatMessage[];
    if (persistedApprovalIndex !== undefined) {
      resumeMsgs = msgs.map((item, index) => index === persistedApprovalIndex ? { ...item, approval: submittingApproval } : item);
    } else {
      resumeMsgs = [...msgs, {
        role: 'assistant',
        content: approval.message,
        approval: submittingApproval,
        workflow: { ...workflowAccRef.current, status: 'waiting_approval', stage: 'approval', message: '等待人工确认后继续执行' },
      }];
    }
    persist(approvalSid, resumeMsgs);
    setLoading(true);

    // 保留审批内容并标记提交中，恢复 loading 状态继续流式输出
    setStreamState(prev => prev ? {
      ...prev,
      approval: submittingApproval,
      approvalSubmitting: true,
      thinking: '正在执行操作...',
      toolCalls: [],
      answerChunks: [],
      charts: [],
      references: [],
      agentAnswer: null,
      evidence: [],
    } : null);
    answerAccRef.current = '';
    agentAnswerAccRef.current = undefined;
    evidenceAccRef.current = [];

    try {
      await resumeChat(approvalSid, approved, {
        onWorkflowStatus: (data) => {
          const previous = workflowAccRef.current;
          const next = {
            ...previous,
            status: data.status as AgentWorkflowLifecycle | string,
            stage: data.stage ?? previous.stage,
            message: data.message,
            runId: data.run_id ?? previous.runId,
            targetSuppliers: data.target_suppliers ?? previous.targetSuppliers,
            sources: data.sources ?? previous.sources,
            evidenceStatus: data.evidence_status ?? previous.evidenceStatus,
            loopExitReason: data.loop_exit_reason ?? previous.loopExitReason,
            toolCallCount: data.tool_call_count ?? previous.toolCallCount,
            completedToolCount: data.completed_tool_count ?? previous.completedToolCount,
          };
          workflowAccRef.current = next;
          setStreamState(prev => prev ? { ...prev, workflowStatus: next, approvalSubmitting: true } : null);
        },
        onThinking: (data) => {
          setStreamState(prev => prev ? { ...prev, thinking: data.message, approvalSubmitting: true } : null);
        },
        onToolCall: (data) => {
          setStreamState(prev => prev ? {
            ...prev,
            toolCalls: [...prev.toolCalls, { tool: data.tool, args: data.args, task_id: data.task_id }],
            workflowStatus: { ...prev.workflowStatus, toolCallCount: prev.workflowStatus.toolCallCount + 1 },
          } : null);
        },
        onToolResult: (data) => {
          setStreamState(prev => {
            if (!prev) return null;
            const toolCalls = [...prev.toolCalls];
            const target = [...toolCalls].reverse().find(call =>
              (data.task_id && call.task_id === data.task_id) || (!data.task_id && call.tool === data.tool && call.result === undefined),
            );
            if (target) {
              target.result = data.result;
            }
            return { ...prev, toolCalls, workflowStatus: { ...prev.workflowStatus, completedToolCount: prev.workflowStatus.completedToolCount + 1 } };
          });
        },
        onAnswerChunk: (data) => {
          answerAccRef.current += data.text;
          setStreamState(prev => prev ? {
            ...prev,
            answerChunks: [...prev.answerChunks, data.text],
            answerStarted: true,
          } : null);
        },
        onChartData: (data) => {
          setStreamState(prev => prev ? {
            ...prev,
            charts: [...prev.charts, data]
          } : null);
        },
        onDone: (data) => {
          const rawAnswer = answerAccRef.current || data.answer;
          const finalAnswer = normalizeApprovalAnswer(rawAnswer, approved);
          const contractStatus = agentAnswerAccRef.current?.status;
          const serverStatus = data.status || contractStatus || workflowAccRef.current.status;
          const hasServerTerminalStatus = ['completed', 'partial', 'needs_review', 'failed'].includes(serverStatus);
          const finalWorkflow = {
            ...workflowAccRef.current,
            status: (hasServerTerminalStatus ? serverStatus : 'failed') as AgentWorkflowLifecycle | string,
            stage: hasServerTerminalStatus && ['completed', 'partial'].includes(serverStatus) ? 'completed' : 'decision',
            message: !hasServerTerminalStatus ? '服务端未返回有效终态，已停止显示为成功' : serverStatus === 'needs_review' ? '结果需要人工复核' : serverStatus === 'partial' ? '本轮 Agent 仅完成部分分析' : serverStatus === 'failed' ? '本轮 Agent 执行失败' : '本轮 Agent 工作流已完成',
          };
          workflowAccRef.current = finalWorkflow;
          const resolvedApproval: ApprovalData = { ...approval, status: approved ? 'approved' : 'rejected' };
          const completedMsgs: ChatMessage[] = resumeMsgs.map((item, index) => {
            if (index !== (persistedApprovalIndex ?? resumeMsgs.length - 1)) return item;
            return { ...item, content: finalAnswer, references: referencesAccRef.current, agentAnswer: agentAnswerAccRef.current, evidence: evidenceAccRef.current, workflow: finalWorkflow, approval: resolvedApproval };
          });
          answerAccRef.current = '';
          approvalAccRef.current = null;
          persist(approvalSid, completedMsgs);
          setStreamState(null);
          setLoading(false);
        },
        onAgentAnswer: (data) => {
          agentAnswerAccRef.current = data;
          setStreamState(prev => prev ? { ...prev, agentAnswer: data } : null);
        },
        onEvidence: (data) => {
          evidenceAccRef.current = data.records;
          setStreamState(prev => prev ? { ...prev, evidence: data.records } : null);
        },
        onError: (data) => {
          const failedWorkflow = { ...workflowAccRef.current, status: 'failed' as const, message: data.message };
          workflowAccRef.current = failedWorkflow;
          const failedApproval: ApprovalData = { ...approval, status: 'failed' };
          const failedMsgs: ChatMessage[] = resumeMsgs.map((item, index) => index === (persistedApprovalIndex ?? resumeMsgs.length - 1)
            ? { ...item, content: `错误：${data.message}`, workflow: failedWorkflow, approval: failedApproval }
            : item);
          persist(approvalSid, failedMsgs);
          setStreamState(prev => prev ? { ...prev, error: data.message, workflowStatus: failedWorkflow, approvalSubmitting: false } : null);
          setLoading(false);
        },
      });
    } catch (err) {
      const isTimeout = err instanceof DOMException && err.name === 'AbortError';
      const failedApproval: ApprovalData = { ...approval, status: 'failed' };
      const failedMsgs: ChatMessage[] = resumeMsgs.map((item, index) => index === (persistedApprovalIndex ?? resumeMsgs.length - 1)
        ? { ...item, content: isTimeout ? '请求超时，请重试' : '操作失败，请重试', approval: failedApproval }
        : item);
      persist(approvalSid, failedMsgs);
      setStreamState(prev => prev ? { ...prev, error: isTimeout ? '请求超时，请重试' : '操作失败，请重试', approvalSubmitting: false } : null);
      setLoading(false);
    }
  }, [streamState, msgs, persist]);

  const handleCapabilityClick = (prompt: string) => {
    const recentSupplier = [...msgs].reverse().flatMap(message => message.references || []).find(reference => reference.name)?.name;
    setInput(prompt.replace('「公司名」', recentSupplier || '青岛三祥科技股份有限公司'));
  };

  const handleRecommendedClick = (question: string) => {
    // Demo cases and Agent review entry points are standalone workflows. Do
    // not append them to whichever conversation happens to be selected.
    send(question, true);
  };

  const newChat = () => {
    setActiveSid('');
    setStreamState(null);
    setLoading(false);
  };

  const switchSession = (sid: string) => {
    setActiveSid(sid);
    setStreamState(null);
    setLoading(false);
  };

  const deleteSession = (sid: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const list = sessions.filter(s => s.sid !== sid);
    saveSessions(list);
    setSessions(list);
    if (activeSid === sid) {
      setActiveSid(list.length > 0 ? list[0].sid : '');
    }
  };

  return (
    <div className="flex h-full max-w-full">
      {/* History card — left column, persistent on lg+ screens */}
      <aside className="hidden lg:flex w-52 shrink-0 flex-col pt-6 pl-4 pr-2">
        <div className="flex-1 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl shadow-md overflow-hidden flex flex-col">
          <div className="flex items-center justify-between px-3 pt-3 pb-2">
            <h3 className="text-[11px] font-medium text-gray-400">会话历史</h3>
            <button onClick={newChat} className="text-[11px] text-gray-400 hover:text-gray-600 transition-colors">+ 新建</button>
          </div>
          {sessions.length === 0 ? (
            <div className="flex-1 flex items-center justify-center pb-6">
              <p className="text-[11px] text-gray-300 px-3">暂无会话</p>
            </div>
          ) : (
            <div className="flex-1 overflow-auto px-1.5 pb-3">
              <div className="space-y-0.5">
                {sessions.map(s => (
                  <div
                    key={s.sid}
                    onClick={() => switchSession(s.sid)}
                    className={`group flex items-center rounded-lg px-3 py-2 cursor-pointer transition-colors ${
                      s.sid === activeSid
                        ? 'bg-[var(--color-primary-bg)] text-white'
                        : 'text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]'
                    }`}
                  >
                    <div className="flex-1 min-w-0">
                      <p className="text-sm truncate">{s.title}</p>
                      <p className={`text-[11px] ${s.sid === activeSid ? 'text-white/60' : 'text-gray-400'}`}>
                        {new Date(s.updatedAt).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                      </p>
                    </div>
                    <button
                      onClick={(e) => deleteSession(s.sid, e)}
                      className={`shrink-0 text-xs opacity-0 group-hover:opacity-100 transition-opacity ml-1 ${
                        s.sid === activeSid ? 'text-white/60 hover:text-white' : 'text-gray-300 hover:text-red-400'
                      }`}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </aside>

      {/* Main chat */}
      <div className="flex-1 flex flex-col min-w-0 w-full max-w-6xl mx-auto h-full">
      {/* messages */}
      <div className="flex-1 overflow-auto px-4 space-y-6 py-6">
        {msgs.length === 0 && !loading && (
          <div className="flex-1 flex items-center justify-center px-4">
            <div className="w-full max-w-lg space-y-8 py-8">
              {/* Hero */}
              <div className="text-center space-y-2">
                <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-[var(--color-primary-bg)]/10 mb-2">
                  <svg className="w-7 h-7 text-[var(--color-primary-bg)]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M12 2l2.4 7.2h7.6l-6 4.8 2.4 7.2-6.4-4.8-6.4 4.8 2.4-7.2-6-4.8h7.6z"/>
                  </svg>
                </div>
                <h2 className="text-xl font-bold text-[var(--color-text)]">AI 工作台</h2>
                <p className="text-sm text-gray-500 leading-relaxed">
                  基于公开风险、上市公司财务和内部采购月度数据，生成可追溯的供应商复核任务
                </p>
              </div>

              {/* Capability cards */}
              <div>
                <p className="text-xs text-gray-400 mb-3 px-1">快速能力</p>
                <div className="grid grid-cols-3 gap-2">
                  {CAPABILITIES.map((cap) => (
                    <button
                      key={cap.label}
                      onClick={() => handleCapabilityClick(cap.prompt)}
                      className="text-left bg-white border border-slate-200 rounded-xl p-3 hover:border-[var(--color-primary-bg)]/30 hover:shadow-sm hover:-translate-y-0.5 transition-all duration-200 group"
                    >
                      <p className="text-sm font-medium text-[var(--color-text)] group-hover:text-[var(--color-primary-bg)] transition-colors">{cap.label}</p>
                      <p className="text-[11px] text-gray-400 mt-0.5 leading-tight">{cap.desc}</p>
                    </button>
                  ))}
                </div>
              </div>

              {/* Recommended questions */}
              <div>
                <p className="text-xs text-gray-400 mb-3 px-1">比赛演示案例</p>
                <div className="space-y-1.5">
                  {RECOMMENDED.map((q) => (
                    <button
                      key={q}
                      onClick={() => handleRecommendedClick(q)}
                      className="w-full text-left text-sm text-gray-600 bg-amber-50/50 border border-amber-100/50 rounded-lg px-4 py-2.5 hover:bg-amber-50 hover:border-amber-200 transition-colors duration-150"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>

            </div>
          </div>
        )}
        <ChatMessageList
          messages={msgs}
          streamState={streamState}
          loading={loading}
          onAnalyzeReference={name => setInput(`继续分析 ${name} 的风险`)}
          onApproval={handleApproval}
          StructuredAgentResult={StructuredAgentResult}
        />
        <div ref={bottomRef} />
      </div>

      {/* input */}
      <div className="px-4 pb-6 pt-2">
        <div className="flex items-center gap-2 bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl px-4 py-1 focus-within:border-[var(--color-border-focus)] focus-within:shadow-sm transition-shadow">
          <input
            ref={inputRef}
            value={input}
            onChange={e => { setInput(e.target.value); }}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="输入问题，如：对比海康威视和宝钢的风险"
            className="flex-1 border-none outline-none py-2.5 text-sm bg-transparent placeholder-gray-300"
          />
          <button
            onClick={() => send()}
            disabled={loading}
            title={loading ? '当前请求仍在处理中' : '发送问题'}
            className="bg-[var(--color-primary-bg)] text-white rounded-xl px-4 py-2 text-sm hover:bg-[var(--color-primary-hover)] disabled:opacity-40 shrink-0 transition-colors min-h-[44px] inline-flex items-center"
          >
            {loading ? '处理中…' : '发送'}
          </button>
        </div>
        <div className="flex justify-between mt-2 px-1">
          <div className="flex items-center gap-3">
            <button onClick={newChat} className="text-xs text-gray-400 hover:text-gray-600 transition-colors">
              + 新对话
            </button>
            {loading && <span role="status" aria-live="polite" className="text-xs text-[var(--color-text-secondary)]">正在提交并等待 Agent 响应…</span>}
          </div>
        </div>
      </div>
      </div>
    </div>
  );
}
