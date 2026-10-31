import type { AgentEvidenceRecord } from '../types';
import ChartRenderer from './ChartRenderer';
import { TrendSection } from './ChatResultSections';

interface MonthlyTrendPoint {
  month: string;
  settlement: number;
  receipts: number;
}

interface MonthlyTrendRow extends Omit<MonthlyTrendPoint, 'settlement' | 'receipts'> {
  settlement: number | null;
  receipts: number | null;
}

interface FinancialHistoryPoint {
  period: string;
  revenue: number;
  netProfit: number;
}

interface ParsedFinancialPeriod {
  period: string;
  ordinal: number;
  granularity: 'year' | 'quarter' | 'month' | 'date';
  year: number;
  month?: number;
  day?: number;
}

function numericValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function parseMonth(value: string): { year: number; month: number } | null {
  const match = value.match(/^(\d{4})-(0[1-9]|1[0-2])$/);
  return match ? { year: Number(match[1]), month: Number(match[2]) } : null;
}

function parseFinancialPeriod(value: string): ParsedFinancialPeriod | null {
  const yearMatch = value.match(/^(\d{4})(?:年度|年)?$/);
  if (yearMatch) {
    const year = Number(yearMatch[1]);
    return { period: value, ordinal: year, granularity: 'year', year };
  }

  const quarterMatch = value.match(/^(\d{4})[- ]?[Qq]([1-4])$/) || value.match(/^(\d{4})年([1-4])季度$/);
  if (quarterMatch) {
    const year = Number(quarterMatch[1]);
    const quarter = Number(quarterMatch[2]);
    return { period: value, ordinal: year * 4 + quarter - 1, granularity: 'quarter', year };
  }

  const month = parseMonth(value);
  if (month) {
    return { period: value, ordinal: month.year * 12 + month.month - 1, granularity: 'month', year: month.year, month: month.month };
  }

  const dateMatch = value.match(/^(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/);
  if (!dateMatch) return null;
  const year = Number(dateMatch[1]);
  const monthNumber = Number(dateMatch[2]);
  const day = Number(dateMatch[3]);
  const date = new Date(Date.UTC(year, monthNumber - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() + 1 !== monthNumber || date.getUTCDate() !== day) return null;
  return {
    period: value,
    ordinal: year * 12 + monthNumber - 1,
    granularity: 'date',
    year,
    month: monthNumber,
    day,
  };
}

function orderAndVerifyFinancialHistory(history: FinancialHistoryPoint[]): { rows: FinancialHistoryPoint[]; continuous: boolean } {
  if (history.length < 2) return { rows: history, continuous: false };
  const parsed = history.map(point => parseFinancialPeriod(point.period));
  if (parsed.some(period => period === null)) return { rows: history, continuous: false };
  const periods = parsed as ParsedFinancialPeriod[];
  if (new Set(periods.map(period => period.period)).size !== periods.length) return { rows: history, continuous: false };

  const ordered = [...history].sort((left, right) => (
    parseFinancialPeriod(left.period)!.ordinal - parseFinancialPeriod(right.period)!.ordinal
  ));
  const orderedPeriods = [...periods].sort((left, right) => left.ordinal - right.ordinal);
  if (orderedPeriods.every(period => period.granularity === 'date')) {
    const sameAnnualDate = orderedPeriods.every(period => period.month === orderedPeriods[0].month && period.day === orderedPeriods[0].day);
    if (sameAnnualDate && orderedPeriods.slice(1).every((period, index) => period.year - orderedPeriods[index].year === 1)) {
      return { rows: ordered, continuous: true };
    }

    const monthOrdinals = orderedPeriods.map(period => period.year * 12 + (period.month || 1) - 1);
    const quarterOrdinals = orderedPeriods.map(period => period.year * 4 + Math.floor(((period.month || 1) - 1) / 3));
    const monthEnds = orderedPeriods.every(period => period.day === new Date(Date.UTC(period.year, (period.month || 1), 0)).getUTCDate());
    const quarterEnds = orderedPeriods.every(period => [3, 6, 9, 12].includes(period.month || 0));
    if (monthEnds && quarterEnds && quarterOrdinals.slice(1).every((ordinal, index) => ordinal - quarterOrdinals[index] === 1)) {
      return { rows: ordered, continuous: true };
    }
    if (monthEnds && monthOrdinals.slice(1).every((ordinal, index) => ordinal - monthOrdinals[index] === 1)) {
      return { rows: ordered, continuous: true };
    }
    return { rows: ordered, continuous: false };
  }

  const granularity = orderedPeriods[0].granularity;
  const sameGranularity = orderedPeriods.every(period => period.granularity === granularity);
  const continuous = sameGranularity && orderedPeriods.slice(1).every((period, index) => period.ordinal - orderedPeriods[index].ordinal === 1);
  return { rows: ordered, continuous };
}

function trailingMonths(latestMonth: string, count: number): string[] | null {
  const parsed = parseMonth(latestMonth);
  if (!parsed) return null;
  return Array.from({ length: count }, (_, index) => {
    const offset = count - index - 1;
    const date = new Date(Date.UTC(parsed.year, parsed.month - 1 - offset, 1));
    return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`;
  });
}

function statusMessage(record: AgentEvidenceRecord, label: string): string | null {
  const messages: Record<string, string> = {
    missing: `${label}当前未覆盖，无法判断是否存在相关记录。`,
    unavailable: `${label}查询未成功或暂不可用，本次未形成趋势判断。`,
    confirmed_empty: `本次查询范围内未发现${label.replace(/数据$/, '')}记录，不据此判断交付能力。`,
    stale: `${label}数据已过期，暂不用于趋势判断。`,
    conflicting: `${label}数据存在冲突，暂不绘制趋势。`,
    synthetic: `${label}为演示数据，不用于采购决策。`,
  };
  return messages[record.status] || (record.status === 'available' ? null : `${label}状态未知，暂不判断趋势。`);
}

function parseMonthlyTrend(facts: Record<string, unknown> | undefined): { rows: MonthlyTrendPoint[]; hasDuplicateMonths: boolean } {
  if (!Array.isArray(facts?.monthly_trend)) return { rows: [], hasDuplicateMonths: false };
  const rows: MonthlyTrendPoint[] = [];
  const seenMonths = new Set<string>();
  let hasDuplicateMonths = false;
  facts.monthly_trend.forEach(item => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return;
    const row = item as Record<string, unknown>;
    const month = typeof row.month === 'string' ? row.month : '';
    const settlement = numericValue(row.actual_settlement_amount);
    const receipts = numericValue(row.received_record_count);
    if (!parseMonth(month) || settlement === null || receipts === null) return;
    if (seenMonths.has(month)) hasDuplicateMonths = true;
    seenMonths.add(month);
    rows.push({ month, settlement, receipts });
  });
  return { rows: rows.sort((left, right) => left.month.localeCompare(right.month)), hasDuplicateMonths };
}

function alignMonthlyTrend(
  monthlyTrend: MonthlyTrendPoint[],
  missingMonthCount: number | null,
  hasDuplicateMonths: boolean,
): { rows: MonthlyTrendRow[]; missingMonths: string[]; aligned: boolean } {
  if (monthlyTrend.length === 0 || hasDuplicateMonths || missingMonthCount === null || !Number.isInteger(missingMonthCount) || missingMonthCount < 0) {
    return {
      rows: monthlyTrend.map(row => ({ ...row })),
      missingMonths: [],
      aligned: false,
    };
  }

  const monthWindow = trailingMonths(monthlyTrend[monthlyTrend.length - 1].month, 12);
  const byMonth = new Map(monthlyTrend.map(row => [row.month, row]));
  if (!monthWindow || byMonth.size + missingMonthCount !== monthWindow.length || [...byMonth.keys()].some(month => !monthWindow.includes(month))) {
    return {
      rows: monthlyTrend.map(row => ({ ...row })),
      missingMonths: [],
      aligned: false,
    };
  }

  const missingMonths = monthWindow.filter(month => !byMonth.has(month));
  if (missingMonths.length !== missingMonthCount) {
    return {
      rows: monthlyTrend.map(row => ({ ...row })),
      missingMonths: [],
      aligned: false,
    };
  }

  return {
    rows: monthWindow.map(month => {
      const point = byMonth.get(month);
      return { month, settlement: point?.settlement ?? null, receipts: point?.receipts ?? null };
    }),
    missingMonths,
    aligned: true,
  };
}

export default function AgentTrendCharts({ evidence, limitations }: { evidence: AgentEvidenceRecord[]; limitations: string[] }) {
  const businessEvidence = evidence.find(record => record.dimension === 'business_risk');
  const businessFacts = businessEvidence?.facts;
  const parsedMonthlyTrend = parseMonthlyTrend(businessFacts);
  const monthlyTrend = parsedMonthlyTrend.rows;
  const missingMonthCount = numericValue(businessFacts?.missing_month_count);
  const alignedTrend = alignMonthlyTrend(monthlyTrend, missingMonthCount, parsedMonthlyTrend.hasDuplicateMonths);
  const hasTransactionTrend = businessEvidence?.status === 'available' && monthlyTrend.length >= 2;
  const businessStatusMessage = businessEvidence
    ? statusMessage(businessEvidence, '内部交易数据')
      || (monthlyTrend.length === 0
        ? '本次商务资料没有可绘制的月度趋势数据。'
        : monthlyTrend.length === 1
          ? '仅取得 1 个有记录月份，尚不足以判断变化趋势。'
          : parsedMonthlyTrend.hasDuplicateMonths
            ? '月度数据包含重复月份，连续性无法核验；保留已有日期数值表，不绘制折线。'
          : !alignedTrend.aligned
            ? missingMonthCount === null
              ? '缺少可核验的最近 12 个月完整性信息；保留已有日期数值表，不绘制折线。'
              : '缺失月份数量与月度日期不一致，暂不推断缺口位置；保留已有日期数值表，不绘制折线。'
            : null)
    : null;

  const financialEvidence = evidence.find(record => record.dimension === 'financial')
    || evidence.find(record => record.dimension === 'risk' && record.facts?.financial_history !== undefined);
  const financialFacts = financialEvidence?.facts;
  const financialHistory = Array.isArray(financialFacts?.financial_history)
    ? financialFacts.financial_history.flatMap(item => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
      const row = item as Record<string, unknown>;
      const period = typeof row.period === 'string' ? row.period : '';
      const revenue = numericValue(row.revenue);
      const netProfit = numericValue(row.net_profit);
      return period && revenue !== null && netProfit !== null ? [{ period, revenue, netProfit }] : [];
    })
    : [];
  const orderedFinancialHistory = orderAndVerifyFinancialHistory(financialHistory);
  const hasFinancialTrend = financialHistory.length >= 2 && (!financialEvidence || financialEvidence.status === 'available');
  const financialMissing = limitations.some(item => item.includes('财务'));
  const hasFinancialSnapshot = evidence.some(record => {
    const facts = record.facts || {};
    return record.dimension === 'financial' || (record.dimension === 'risk' && facts.financial !== null && facts.financial !== undefined);
  });
  const financialStatusMessage = financialEvidence && financialEvidence.status !== 'available'
    ? statusMessage(financialEvidence, '财务数据')
    : null;
  const financialTrendUnavailable = !hasFinancialTrend && !financialStatusMessage && (financialMissing || hasFinancialSnapshot);

  if (!hasTransactionTrend && !businessStatusMessage && !hasFinancialTrend && !financialTrendUnavailable && !financialStatusMessage) return null;

  const transactionSeries = alignedTrend.rows.length > 0 ? alignedTrend.rows : monthlyTrend;
  return <TrendSection><section className="border-t border-[var(--color-border)] px-4 py-3 sm:px-5" aria-label="趋势与证据">
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
      <h3 className="text-sm font-semibold text-[var(--color-text)]">趋势与证据</h3>
      <p className="text-xs text-[var(--color-text-secondary)]">只展示已有日期和数值；缺失月份不补零、不连线。</p>
    </div>
    {businessStatusMessage && <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2 text-xs leading-5 text-amber-950">{businessStatusMessage}</p>}
    {hasTransactionTrend && <div className="mt-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="text-sm font-medium text-[var(--color-text)]">供应商交易连续性</p>
        {businessEvidence?.data_mode === 'synthetic' && <span className="text-xs font-medium text-amber-700">演示数据 · 不用于采购决策</span>}
      </div>
      <p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">收货记录数表示源明细行数，不代表零件数量、送货批次或交付能力。月份缺少快照只表示数据未覆盖，不代表无交易。</p>
      {alignedTrend.missingMonths.length > 0 && <p className="mt-1 text-xs text-amber-800">未取得月度快照：{alignedTrend.missingMonths.join('、')}</p>}
      <div className="mt-2 grid gap-3 lg:grid-cols-2">
        <ChartRenderer compact lineIsContinuous={alignedTrend.aligned} missingValueLabel="本月快照未取得" data={{ type: 'line', title: '实结算金额（元）', data: transactionSeries.map(item => ({ date: item.month, value: item.settlement })), source: 'tool', tool: 'assess_business_risk' }} />
        <ChartRenderer compact lineIsContinuous={alignedTrend.aligned} missingValueLabel="本月快照未取得" data={{ type: 'line', title: '收货记录数（条）', data: transactionSeries.map(item => ({ date: item.month, value: item.receipts })), source: 'tool', tool: 'assess_business_risk' }} />
      </div>
    </div>}
    {hasFinancialTrend && <div className="mt-3 border-t border-[var(--color-border)] pt-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="text-sm font-medium text-[var(--color-text)]">上市供应商财务趋势</p>
        {financialEvidence?.data_mode === 'synthetic' && <span className="text-xs font-medium text-amber-700">演示数据 · 不用于采购决策</span>}
      </div>
      <p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">仅展示已取得的报告期营收和净利润。</p>
      {!orderedFinancialHistory.continuous && <p className="mt-1 text-xs leading-5 text-amber-800">财务报告期不连续或无法识别，折线未绘制；仅显示已有报告期。</p>}
      <div className="mt-2 grid gap-3 lg:grid-cols-2">
        <ChartRenderer compact lineIsContinuous={orderedFinancialHistory.continuous} data={{ type: 'line', title: '营业收入', data: orderedFinancialHistory.rows.map(item => ({ date: item.period, value: item.revenue })), source: 'tool', tool: 'assess_risk' }} />
        <ChartRenderer compact lineIsContinuous={orderedFinancialHistory.continuous} data={{ type: 'line', title: '净利润', data: orderedFinancialHistory.rows.map(item => ({ date: item.period, value: item.netProfit })), source: 'tool', tool: 'assess_risk' }} />
      </div>
    </div>}
    {financialStatusMessage && <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2 text-xs leading-5 text-amber-950">{financialStatusMessage}</p>}
    {financialTrendUnavailable && <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2 text-xs leading-5 text-amber-950"><span className="font-medium">财务趋势当前未覆盖。</span> 本轮没有取得至少两个报告期的正式财务序列，因此不绘制趋势图。</p>}
  </section></TrendSection>;
}
