import type { AgentEvidenceRecord } from '../types';
import ChartRenderer from './ChartRenderer';
import { TrendSection } from './ChatResultSections';

function numericValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export default function AgentTrendCharts({ evidence, limitations }: { evidence: AgentEvidenceRecord[]; limitations: string[] }) {
  const businessFacts = evidence.find(record => record.dimension === 'business_risk')?.facts;
  const monthlyTrend = Array.isArray(businessFacts?.monthly_trend)
    ? businessFacts.monthly_trend.flatMap(item => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
      const row = item as Record<string, unknown>;
      const month = typeof row.month === 'string' ? row.month : '';
      const settlement = numericValue(row.actual_settlement_amount);
      const receipts = numericValue(row.received_record_count);
      return month && settlement !== null && receipts !== null ? [{ month, settlement, receipts }] : [];
    })
    : [];
  const financialFacts = evidence.find(record => record.dimension === 'financial')?.facts;
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
  const hasTransactionTrend = monthlyTrend.length >= 2;
  const hasFinancialTrend = financialHistory.length >= 2;
  const financialMissing = limitations.some(item => item.includes('财务'));
  const hasFinancialSnapshot = evidence.some(record => {
    const facts = record.facts || {};
    return record.dimension === 'financial' || (record.dimension === 'risk' && facts.financial !== null && facts.financial !== undefined);
  });
  const financialTrendUnavailable = !hasFinancialTrend && (financialMissing || hasFinancialSnapshot);

  if (!hasTransactionTrend && !hasFinancialTrend && !financialTrendUnavailable) return null;

  return <TrendSection><section className="border-t border-[var(--color-border)] px-4 py-4 sm:px-5" aria-label="趋势与证据">
    <div>
      <div className="flex items-center gap-1.5"><span aria-hidden="true" className="text-[var(--color-primary-bg)]">④</span><p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-secondary)]">趋势与证据</p></div>
      <h3 className="mt-1 text-base font-semibold text-[var(--color-text)]">用变化判断复核优先级</h3>
    </div>
    {hasTransactionTrend && <div className="mt-3 rounded-xl border border-sky-200 bg-sky-50/45 p-3.5">
      <p className="text-sm font-medium text-sky-950">供应商交易连续性趋势</p>
      <p className="mt-1 text-xs leading-5 text-sky-900/75">展示已导入月份的实结算金额与收货记录数；收货记录数仅代表源明细行数，不代表零件数量或交付能力。</p>
      <div className="mt-2 grid gap-2 lg:grid-cols-2">
        <ChartRenderer data={{ type: 'line', title: '实结算金额（元）', data: monthlyTrend.map(item => ({ date: item.month, value: item.settlement })), source: 'tool', tool: 'assess_business_risk' }} />
        <ChartRenderer data={{ type: 'line', title: '收货记录数（条）', data: monthlyTrend.map(item => ({ date: item.month, value: item.receipts })), source: 'tool', tool: 'assess_business_risk' }} />
      </div>
    </div>}
    {hasFinancialTrend && <div className="mt-3 rounded-xl border border-indigo-200 bg-indigo-50/45 p-3.5">
      <p className="text-sm font-medium text-indigo-950">上市供应商财务趋势</p>
      <p className="mt-1 text-xs leading-5 text-indigo-900/75">仅展示已取得的报告期营收和净利润，供复核盈利变化的来源与持续性。</p>
      <div className="mt-2 grid gap-2 lg:grid-cols-2">
        <ChartRenderer data={{ type: 'line', title: '营业收入', data: financialHistory.map(item => ({ date: item.period, value: item.revenue })), source: 'tool', tool: 'assess_risk' }} />
        <ChartRenderer data={{ type: 'line', title: '净利润', data: financialHistory.map(item => ({ date: item.period, value: item.netProfit })), source: 'tool', tool: 'assess_risk' }} />
      </div>
    </div>}
    {financialTrendUnavailable && <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50/65 px-3.5 py-3 text-sm leading-6 text-amber-950">
      <span className="font-medium">财务趋势暂未覆盖。</span> 当前仅有最新财务指标或没有财务资料，未取得至少两个报告期的正式财务序列，因此系统不绘制财务趋势图。
    </div>}
  </section></TrendSection>;
}
