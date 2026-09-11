import { Fragment } from 'react';
import type { AgentAnswer, AgentEvidenceRecord } from '../types';
import { EvidenceTable } from './ChatResultSections';
import AgentTrendCharts from './AgentTrendCharts';
import ActionSummary from './ActionSummary';
import SupplierReviewConclusion from './SupplierReviewConclusion';

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
  risk_monitoring: '风险监控',
  identity_review: '主体身份',
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
  identity_review: 'border-purple-200 bg-purple-50 text-purple-700',
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
  count: '可见监控对象数量',
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
  resolution: '主体检索结果',
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
  const normalized = path
    .trim()
    .replace(/([a-z])([A-Z])/g, '$1_$2')
    .toLowerCase()
    .replace(/[\s-]+/g, '_')
    .replace(/\.{2,}/g, '.')
    .replace(/^\.+|\.+$/g, '');
  if (FACT_PATH_ALIASES[normalized]) return FACT_PATH_ALIASES[normalized];
  const knownPaths = [...Object.keys(FACT_LABELS), ...Object.keys(RISK_FACT_LABELS)];
  const compact = normalized.replace(/[._]/g, '');
  const matched = knownPaths.find(item => item.replace(/[._]/g, '').toLowerCase() === compact);
  return matched || normalized;
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
  'risk_detail.lawsuit_count': '诉讼记录数',
  'risk_detail.executed_count': '被执行记录数',
  'risk_detail.dishonesty_count': '失信记录数',
  'risk_detail.major_lawsuit': '重大诉讼标记',
  'risk_detail.abnormal_operation_count': '经营异常记录数',
  'risk_detail.administrative_penalty_count': '行政处罚记录数',
  'risk_detail.legal_person_change_frequent': '法人频繁变更',
  'risk_detail.guarantee_count': '对外担保记录数',
  'risk_detail.pledge_count': '股权质押记录数',
  'risk_detail.bankruptcy_count': '破产相关记录数',
  'risk_detail.env_penalty_count': '环保处罚记录数',
  'risk_detail.data_coverage.coverage_ratio': '风险数据覆盖率',
  'risk_detail.data_coverage.assessment_status': '风险数据覆盖状态',
};

function readableFactLabel(path: string): string {
  const canonicalPath = canonicalFactPath(path);
  return FACT_LABELS[canonicalPath] || RISK_FACT_LABELS[canonicalPath] || FACT_LABELS[path] || RISK_FACT_LABELS[path] || path
    .replaceAll('_', ' ')
    .replace(/\b\w/g, char => char.toUpperCase());
}

function readableFactValue(path: string, value: unknown): string {
  const canonicalPath = canonicalFactPath(path);
  const leafPath = canonicalPath.split('.').pop() || canonicalPath;
  if (leafPath === 'clean' && typeof value === 'boolean') return value ? '未命中风险记录' : '发现风险记录';
  if (leafPath === 'supplier_spend_share' && typeof value === 'number') return `${(value * 100).toFixed(1)}%`;
  if (leafPath === 'settlement_share' && typeof value === 'number') {
    return value > 0 && value < 0.001 ? '<0.1%' : `${(value * 100).toFixed(1)}%`;
  }
  if (leafPath === 'exposure_level' && typeof value === 'string') {
    return ({ high: '高敞口', medium: '中敞口', low: '低敞口', unknown: '暂无法判断' } as Record<string, string>)[value] ?? value;
  }
  if ((leafPath === 'coverage' || leafPath === 'coverage_ratio') && typeof value === 'number') return `${(value * 100).toFixed(0)}%`;
  if (['revenue_growth', 'net_profit_growth', 'debt_ratio', 'roe', 'net_profit_margin', 'settlement_change_ratio', 'receipt_record_change_ratio'].includes(leafPath) && typeof value === 'number') return `${(value * 100).toFixed(1)}%`;
  if (['missing_month_count', 'settlement_without_receipts_month_count'].includes(leafPath) && typeof value === 'number') return `${value.toFixed(0)} 个月`;
  if (['current_ratio', 'quick_ratio'].includes(leafPath) && typeof value === 'number') return value.toFixed(2);
  if (leafPath === 'risk_score' && typeof value === 'number') return `${value}/100`;
  if (leafPath === 'cash_flow' && typeof value === 'number') return `${value.toFixed(2)} 元/股`;
  if (leafPath === 'latest_actual_settlement_amount' && typeof value === 'number') return `${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 元`;
  if (leafPath === 'latest_received_record_count' && typeof value === 'number') return `${value.toLocaleString('zh-CN')} 条`;
  if (leafPath === 'comparison_supplier_count' && typeof value === 'number') return `${value.toLocaleString('zh-CN')} 家`;
  if (leafPath === 'major_lawsuit' && typeof value === 'boolean') return value ? '有' : '无';
  if (leafPath === 'legal_person_change_frequent' && typeof value === 'boolean') return value ? '是' : '否';
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
  if (claim.dimension === 'risk_monitoring' && !claim.fact_path) {
    if (claim.statement.startsWith('监控对象：')) return String(claim.value ?? claim.statement.replace('监控对象：', ''));
    const trendDetail = claim.statement.replace(/^.*?最近\s*\d+\s*个月风险变化：/, '');
    if (trendDetail !== claim.statement) return trendDetail;
  }
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
  if (claim.dimension === 'risk_monitoring' && claim.statement.startsWith('监控对象：')) return '监控对象';
  if (claim.dimension === 'risk_monitoring' && claim.statement.includes('风险变化：')) return '本月风险变化';
  return claim.fact_path ? readableFactLabel(claim.fact_path) : '综合判断';
}

function claimAssessment(claim: AgentAnswer['claims'][number], coverageLimited = false): { label: string; className: string } {
  if (claim.validation_status === 'partial' || claim.validation_status === 'unsupported') {
    return { label: '当前未覆盖', className: 'border-amber-200 bg-amber-50 text-amber-700' };
  }
  if (claim.validation_status === 'conflicting') {
    return { label: '数据有冲突', className: 'border-red-200 bg-red-50 text-red-700' };
  }

  const value = claim.value;
  const path = canonicalFactPath(claim.fact_path || '');
  const leafPath = path.split('.').pop() || path;
  if (claim.dimension === 'risk_monitoring' && claim.statement.startsWith('监控对象：')) {
    return { label: '已纳入监控', className: 'border-blue-200 bg-blue-50 text-blue-700' };
  }
  if (claim.dimension === 'risk_monitoring' && claim.statement.includes('风险变化：')) {
    if (value === '恶化') return { label: '风险上升', className: 'border-red-200 bg-red-50 text-red-700' };
    if (value === '改善') return { label: '风险下降', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' };
    if (value === '稳定') return { label: '基本稳定', className: 'border-blue-200 bg-blue-50 text-blue-700' };
    return { label: '暂无足够数据', className: 'border-amber-200 bg-amber-50 text-amber-700' };
  }
  if (leafPath === 'risk_level' && typeof value === 'string') {
    if (value.includes('低')) return { label: coverageLimited ? '资料范围内风险较低' : '风险较低', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' };
    if (value.includes('高')) return { label: '高风险信号', className: 'border-red-200 bg-red-50 text-red-700' };
    return { label: '需关注', className: 'border-amber-200 bg-amber-50 text-amber-700' };
  }
  if (leafPath === 'clean' && typeof value === 'boolean') {
    return value
      ? { label: '未见风险信号', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' }
      : { label: '需关注', className: 'border-red-200 bg-red-50 text-red-700' };
  }
  if (leafPath === 'major_lawsuit' && typeof value === 'boolean') {
    return value
      ? { label: '需人工核实', className: 'border-red-200 bg-red-50 text-red-700' }
      : { label: '未见重大诉讼', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' };
  }
  if (typeof value === 'number') {
    if (['lawsuit_count', 'executed_count', 'dishonesty_count', 'abnormal_operation_count', 'administrative_penalty_count', 'guarantee_count', 'pledge_count', 'bankruptcy_count', 'env_penalty_count'].includes(leafPath)) {
      return value > 0
        ? { label: '需要关注', className: 'border-amber-200 bg-amber-50 text-amber-700' }
        : { label: '未见记录', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' };
    }
    if (['revenue_growth', 'net_profit_growth', 'cash_flow'].includes(leafPath) && value < 0) {
      return { label: '需关注', className: 'border-amber-200 bg-amber-50 text-amber-700' };
    }
    if ((leafPath === 'current_ratio' && value > 0 && value < 1) || (leafPath === 'quick_ratio' && value > 0 && value < 0.8)) {
      return { label: '流动性需关注', className: 'border-amber-200 bg-amber-50 text-amber-700' };
    }
    if (['settlement_change_ratio', 'receipt_record_change_ratio'].includes(leafPath) && Math.abs(value) >= 0.3) {
      return { label: '波动需复核', className: 'border-amber-200 bg-amber-50 text-amber-700' };
    }
    if (['missing_month_count', 'settlement_without_receipts_month_count'].includes(leafPath) && value > 0) {
      return { label: '需核实', className: 'border-amber-200 bg-amber-50 text-amber-700' };
    }
    if (leafPath === 'risk_score') {
      if (value >= 60) return { label: '高风险信号', className: 'border-red-200 bg-red-50 text-red-700' };
      if (value >= 30) return { label: '需关注', className: 'border-amber-200 bg-amber-50 text-amber-700' };
      return { label: coverageLimited ? '资料范围内风险较低' : '风险较低', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' };
    }
  }
  if (leafPath === 'exposure_level' && typeof value === 'string') {
    if (value === 'high') return { label: '高敞口', className: 'border-red-200 bg-red-50 text-red-700' };
    if (value === 'medium') return { label: '需关注', className: 'border-amber-200 bg-amber-50 text-amber-700' };
    if (value === 'low') return { label: '低敞口', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' };
  }
  return { label: '已获取，需结合复核', className: 'border-stone-200 bg-stone-50 text-stone-700' };
}

function analysisOverview(answer: AgentAnswer, limitations: string[]): string {
  if (answer.summary.includes('监控清单') && answer.claims.length === 0) return answer.summary;
  if (answer.summary.includes('风险变化') && answer.claims.length === 0) return answer.summary;
  if (answer.claims.some(claim => claim.dimension === 'risk_monitoring' && claim.statement.startsWith('监控对象：'))) {
    const count = answer.claims.filter(claim => claim.statement.startsWith('监控对象：')).length;
    return `当前责任范围内共有 ${count} 家供应商纳入监控，下面列出可直接打开详情的监控对象。`;
  }
  if (answer.claims.some(claim => claim.dimension === 'risk_monitoring' && claim.statement.includes('风险变化：'))) {
    return answer.summary || '已完成当前责任范围内供应商的风险变化检查，下面列出每家的变化状态。';
  }
  // Narrated summaries use a headline/body split. Prefer those summaries so
  // the legacy keyword gate cannot hide the procurement language generated by
  // the constrained LLM layer; keep the historical fallback for old sessions.
  if (answer.claims.length > 0 && answer.summary?.includes('\n\n')) {
    return answer.summary;
  }
  if (answer.claims.length > 0 && answer.summary && (
    answer.summary.includes('供应商复核') || answer.summary.includes('综合风险评分') || answer.summary.includes('寻源候选')
  )) {
    return answer.summary;
  }
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

function claimGroup(claim: AgentAnswer['claims'][number]): 'summary' | 'risk' | 'financial' | 'other' {
  const path = claim.fact_path || '';
  if (path === 'risk_score' || path === 'risk_level') return 'summary';
  if (claim.dimension === 'financial' || path.startsWith('financial.')) return 'financial';
  if (claim.dimension === 'risk' || path.startsWith('risk_detail.')) return 'risk';
  return 'other';
}

function claimGroupTitle(group: ReturnType<typeof claimGroup>): string {
  return ({ summary: '综合结论', risk: '风险信号', financial: '财务指标', other: '其他已核验信息' })[group];
}

function shouldShowClaim(claim: AgentAnswer['claims'][number]): boolean {
  const path = claim.fact_path || '';
  if (!path.startsWith('risk_detail.')) return true;
  const leafPath = path.split('.').pop() || path;
  if (leafPath === 'major_lawsuit' || leafPath === 'legal_person_change_frequent') {
    return claim.value === true;
  }
  if (leafPath.endsWith('_count')) return typeof claim.value !== 'number' || claim.value > 0;
  return true;
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

function answerStatusMeta(answer: AgentAnswer, limitations: string[]) {
  const text = limitations.join(' ');
  if (text.includes('主体')) return { label: '主体待确认', className: 'border-violet-200 bg-violet-50 text-violet-700', icon: '?' };
  if (text.includes('权限')) return { label: '权限不足', className: 'border-red-200 bg-red-50 text-red-700', icon: '×' };
  if (text.includes('资料') || text.includes('财务')) return { label: '当前未覆盖', className: 'border-amber-200 bg-amber-50 text-amber-700', icon: '!' };
  if (answer.status === 'needs_review') return { label: '发现信号，需人工复核', className: 'border-amber-200 bg-amber-50 text-amber-700', icon: '!' };
  return ANSWER_STATUS_META[answer.status] || { label: answer.status, className: 'border-gray-200 bg-gray-50 text-gray-600', icon: '·' };
}

export default function StructuredAgentResult({ answer, evidence }: { answer?: AgentAnswer; evidence?: AgentEvidenceRecord[] }) {
  if (!answer && (!evidence || evidence.length === 0)) return null;
  const limitations = answer?.limitations.map(readableLimitation).filter(Boolean) || [];
  const status = answer ? answerStatusMeta(answer, limitations) : null;
  const riskItems = buildRiskItems(answer, evidence || []);
  const overview = answer ? analysisOverview(answer, limitations) : '';
  const watchlistClaims = answer?.claims.filter(claim => claim.dimension === 'risk_monitoring' && (
    claim.statement.startsWith('监控对象：') || (typeof claim.value === 'string' && /(?:有限公司|股份有限公司|集团)/.test(claim.value))
  )) || [];
  const trendClaims = answer?.claims.filter(claim => claim.dimension === 'risk_monitoring' && claim.statement.includes('风险变化：')) || [];
  const isTrend = trendClaims.length > 0;
  const isWatchlist = Boolean(watchlistClaims.length > 0 || (answer?.summary.includes('监控清单') && !isTrend));
  const scopeQuery = isWatchlist || isTrend;
  const financialMissing = limitations.some(item => item.includes('财务'));
  const hasRiskSignals = Boolean(answer?.claims.some(claim => claimGroup(claim) === 'risk' && claim.fact_path?.startsWith('risk_detail.')));

  return <section className="mt-4 overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] shadow-sm" aria-label="Agent 分析结果">
    {answer && !scopeQuery && <ActionSummary answer={answer} limitations={limitations} />}
    {answer && <div className="bg-gradient-to-br from-[var(--color-surface)] to-[var(--color-code-bg)]/60 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-1.5"><span aria-hidden="true" className="text-[var(--color-primary-bg)]">{scopeQuery ? '▣' : '②'}</span><p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-secondary)]">{scopeQuery ? '采购视图' : '业务结论'}</p></div>
          <h3 className="mt-1 text-base font-semibold text-[var(--color-text)]">{isWatchlist && !isTrend ? '我的监控清单' : isTrend ? '我负责供应商的风险变化' : '本轮分析结果'}</h3>
        </div>
        {status && !scopeQuery && <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${status.className}`}>
          <span aria-hidden="true">{status.icon}</span>{status.label}
        </span>}
      </div>
      <p className="mt-3 max-w-4xl text-sm leading-6 text-[var(--color-text-secondary)]">{overview}</p>
      {watchlistClaims.length > 0 && <div className="mt-4 w-full overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]">
        <table className="w-full border-collapse text-left text-sm">
          <thead className="bg-[var(--color-code-bg)]/75 text-xs text-[var(--color-text-secondary)]"><tr><th className="px-3 py-2.5 font-medium">供应商</th><th className="px-3 py-2.5 font-medium">监控状态</th><th className="px-3 py-2.5 font-medium">当前说明</th></tr></thead>
          <tbody className="divide-y divide-[var(--color-border)]">{watchlistClaims.map(claim => <tr key={claim.claim_id}><td className="px-3 py-3 font-medium text-[var(--color-text)]">{String(claim.value || claim.statement.replace('监控对象：', ''))}</td><td className="px-3 py-3"><span className="inline-flex rounded-md border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">已纳入监控</span></td><td className="px-3 py-3 text-[var(--color-text-secondary)]">可打开监控对象详情查看风险快照和下一步动作</td></tr>)}</tbody>
        </table>
      </div>}
      {trendClaims.length > 0 && <div className="mt-4 w-full overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]">
        <table className="w-full border-collapse text-left text-sm">
          <thead className="bg-[var(--color-code-bg)]/75 text-xs text-[var(--color-text-secondary)]"><tr><th className="px-3 py-2.5 font-medium">供应商</th><th className="px-3 py-2.5 font-medium">本月变化</th><th className="px-3 py-2.5 font-medium">下一步</th></tr></thead>
          <tbody className="divide-y divide-[var(--color-border)]">{trendClaims.map(claim => { const assessment = claimAssessment(claim, answer?.status !== 'completed' || limitations.length > 0); const name = claim.statement.split(' 最近 ')[0]; const next = claim.value === '恶化' ? '安排采购复核' : claim.value === '改善' || claim.value === '稳定' ? '继续观察' : '等待后续快照'; return <tr key={claim.claim_id}><td className="px-3 py-3 font-medium text-[var(--color-text)]">{name}</td><td className="px-3 py-3"><span className={`inline-flex rounded-md border px-2 py-0.5 text-xs font-medium ${assessment.className}`}>{readableClaimDetail(claim)}</span></td><td className="px-3 py-3 text-[var(--color-text-secondary)]">{next}</td></tr>; })}</tbody>
        </table>
      </div>}
      {answer.claims.length > 0 && !scopeQuery && <EvidenceTable><div className="mt-4 w-full overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]">
        <table className="w-full table-fixed border-collapse text-left text-sm">
          <thead className="bg-[var(--color-code-bg)]/75 text-xs text-[var(--color-text-secondary)]"><tr><th scope="col" className="w-[15%] px-2 py-2.5 font-medium sm:px-3">风险维度</th><th scope="col" className="w-[20%] px-2 py-2.5 font-medium sm:px-3">指标/检查项</th><th scope="col" className="w-[32%] px-2 py-2.5 font-medium sm:px-3">当前数据</th><th scope="col" className="w-[18%] px-2 py-2.5 font-medium sm:px-3">判断</th><th scope="col" className="hidden w-[8%] px-3 py-2.5 font-medium md:table-cell">依据状态</th><th scope="col" className="hidden w-[7%] px-3 py-2.5 font-medium md:table-cell">可信度</th></tr></thead>
          <tbody className="divide-y divide-[var(--color-border)]">{(['summary', 'risk', 'financial', 'other'] as const).map(group => {
            const claims = answer.claims.filter(claim => claimGroup(claim) === group && shouldShowClaim(claim));
            if (claims.length === 0) return null;
            return <Fragment key={group}>{<tr className="bg-[var(--color-code-bg)]/35"><th colSpan={6} className="px-2 py-2 text-left text-xs font-semibold text-[var(--color-text-secondary)] sm:px-3">{claimGroupTitle(group)}</th></tr>}{claims.map(claim => { const assessment = claimAssessment(claim, answer.status !== 'completed' || limitations.length > 0); return <tr key={claim.claim_id} className="align-top"><td className="break-words px-2 py-3 sm:px-3"><span className={`inline-flex max-w-full rounded-md border px-2 py-0.5 text-[11px] font-medium ${dimensionBadgeClass(claim.dimension)}`}>{readableDimension(claim.dimension)}</span></td><td className="break-words px-2 py-3 font-medium leading-6 text-[var(--color-text)] sm:px-3">{claimMetric(claim)}</td><td className="break-words px-2 py-3 leading-6 text-[var(--color-text)] sm:px-3">{readableClaimDetail(claim)}</td><td className="break-words px-2 py-3 sm:px-3"><span className={`inline-flex max-w-full rounded-md border px-2 py-0.5 text-xs font-medium ${assessment.className}`}>{assessment.label}</span></td><td className="hidden px-3 py-3 text-xs text-[var(--color-text-secondary)] md:table-cell">{readableValidationStatus(claim.validation_status)}</td><td className="hidden px-3 py-3 text-xs tabular-nums text-[var(--color-text-secondary)] md:table-cell">{claim.confidence > 0 ? `${Math.round(claim.confidence * 100)}%` : '—'}</td></tr>; })}</Fragment>;
          })}</tbody>
        </table>
      </div></EvidenceTable>}
      {answer.claims.length > 0 && !scopeQuery && <section className="mt-4 rounded-xl border border-violet-200 bg-violet-50/60 p-3.5 text-sm leading-6 text-violet-950" aria-label="采购建议">
        <h4 className="font-semibold">采购建议</h4>
        <p className="mt-1">{answer.status === 'needs_review' || financialMissing
          ? `当前结论仅代表已取得资料范围${hasRiskSignals ? '，请先核实上方风险信号' : ''}；涉及关键零件或大额订单时，建议完成采购复核后再决定。`
          : '当前已取得的数据未见需要立即暂停采购的信号，可按正常流程推进并持续关注指标变化。'}</p>
      </section>}
    </div>}

    {answer && <SupplierReviewConclusion answer={answer} evidence={evidence || []} limitations={limitations} numericFact={numericFact} reviewClaims={reviewClaims} />}

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
