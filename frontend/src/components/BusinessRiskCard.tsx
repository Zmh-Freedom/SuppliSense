import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { api } from '../api';
import { queryKeys } from '../query-keys';
import type { BusinessRiskP0 } from '../types';

export default function BusinessRiskCard({ supplierId }: { supplierId: string }) {
  const { data, error, isLoading } = useQuery({
    queryKey: queryKeys.supplierBusinessRisk(supplierId),
    queryFn: () => api.get<BusinessRiskP0>(`/risk/business/${encodeURIComponent(supplierId)}`),
    enabled: !!supplierId,
  });

  if (isLoading) return <Card><p className="text-sm text-gray-400">正在加载商务风险 P0…</p></Card>;
  if (error || !data) return <Card><p className="text-sm text-gray-500">商务风险 P0 暂时无法加载，请稍后重试。</p></Card>;

  if (data.assessment_status !== 'partial' || !data.enabled_dimension) {
    return <Card>
      <Title />
      <p className="mt-2 text-sm text-gray-600">{data.reason ?? '当前没有足够的交易快照生成商务风险结论。'}</p>
      {data.assessment_status === 'needs_scope' && data.available_category_codes?.length ? <p className="mt-2 text-xs text-amber-700">请按品类查看：{data.available_category_codes.join('、')}</p> : null}
      <p className="mt-3 text-xs text-gray-400">P0 仅启用“供应依赖与可替代性”；尚未输出完整商务风险评分。</p>
    </Card>;
  }

  const { enabled_dimension: dimension, observed_signals: signals } = data;
  const isDemo = data.assessment_data_mode === 'demo';
  const isSupplierMonthSummary = data.scope?.data_granularity === 'supplier_month';
  return <Card>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <Title summary={isSupplierMonthSummary} />
      <span className={`rounded-full px-2 py-1 text-xs font-medium ${isDemo ? 'bg-amber-50 text-amber-700' : 'bg-emerald-50 text-emerald-700'}`}>
        {isDemo ? '演示数据 · 不可决策' : '正式数据 · 可用作决策证据'}
      </span>
    </div>
    <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Metric label={isSupplierMonthSummary ? '采购敞口' : '依赖风险'} value={isSupplierMonthSummary ? exposureLevelLabel(dimension.exposure_level ?? dimension.risk_level) : riskLevelLabel(dimension.risk_level)} warn={(dimension.exposure_level ?? dimension.risk_level) === 'high'} />
      <Metric label={isSupplierMonthSummary ? '结算金额占比' : '采购占比'} value={percent(dimension.settlement_share ?? dimension.supplier_spend_share)} />
      <Metric label={isSupplierMonthSummary ? '同月可比较供应商' : '同范围供应商'} value={`${dimension.active_supplier_count} 家`} warn={!isSupplierMonthSummary && dimension.single_source} />
      <Metric label="数据周期" value={data.period ?? '-'} />
    </div>
    <div className="mt-4 rounded-xl bg-gray-50 p-3 text-xs text-gray-600">
      {isSupplierMonthSummary ? <>
        <p>实结算金额：{money(dimension.latest_actual_settlement_amount ?? dimension.supplier_received_amount)} / 同月正向结算总额 {money(dimension.comparison_actual_settlement_amount ?? dimension.category_total_received_amount)}</p>
        <p className="mt-1">收货记录：{signals?.receipts?.received_record_count?.toLocaleString('zh-CN') ?? '-'} 条（源明细行数）</p>
      </> : <>
        <p>收货金额：{money(dimension.supplier_received_amount)} / {money(dimension.category_total_received_amount)}</p>
        <p className="mt-1">单一来源：{dimension.single_source ? '是，需要优先核验替代能力' : '否'}</p>
      </>}
    </div>
    {isSupplierMonthSummary ? <div className="mt-4 grid grid-cols-1 gap-2 text-xs text-gray-600 sm:grid-cols-3">
      <Signal label="结算环比" value={changeLabel(signals?.settlement?.change_ratio)} />
      <Signal label="收货记录环比" value={changeLabel(signals?.receipts?.change_ratio)} />
      <Signal label="近12月完整度" value={`${signals?.data_continuity?.present_months ?? 0}/${signals?.data_continuity?.expected_months ?? 12} 个月`} />
    </div> : <div className="mt-4 grid grid-cols-1 gap-2 text-xs text-gray-600 sm:grid-cols-3">
      <Signal label="合同" value={contractLabel(signals?.contract)} />
      <Signal label="结算" value={settlementLabel(signals?.settlement)} />
      <Signal label="价格" value={priceLabel(signals?.price)} />
    </div>}
    {data.limitations?.length ? <ul className="mt-4 space-y-1 text-xs text-amber-800">{data.limitations.map((limitation) => <li key={limitation}>• {limitation}</li>)}</ul> : null}
    {data.evidence?.length ? <p className="mt-3 text-xs text-gray-400">证据：{data.evidence.map((item) => `${item.source}${item.period ? ` · ${item.period}` : ''}`).join('；')}</p> : null}
  </Card>;
}

function Card({ children }: { children: ReactNode }) {
  return <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-sm">{children}</section>;
}

function Title({ summary = false }: { summary?: boolean }) {
  return summary
    ? <div><h3 className="text-sm font-semibold">内部采购证据</h3><p className="mt-1 text-xs text-gray-400">展示采购敞口与月度变化，不单独作为供应商风险结论</p></div>
    : <div><h3 className="text-sm font-semibold">商务风险 P0</h3><p className="mt-1 text-xs text-gray-400">当前仅评估供应依赖与可替代性（完整商务模型权重 30%）</p></div>;
}

function Metric({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return <div><p className="text-xs text-gray-400">{label}</p><p className={`mt-1 text-sm font-semibold ${warn ? 'text-red-600' : 'text-[#333]'}`}>{value}</p></div>;
}

function Signal({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg bg-gray-50 px-3 py-2"><span className="text-gray-400">{label}：</span>{value}</div>;
}

function riskLevelLabel(level: string): string {
  return ({ high: '高风险', medium: '中风险', low: '低风险' } as Record<string, string>)[level] ?? level;
}

function exposureLevelLabel(level: string): string {
  return ({ high: '高敞口', medium: '中敞口', low: '低敞口', unknown: '暂无法判断' } as Record<string, string>)[level] ?? level;
}

function percent(value: number): string {
  if (value > 0 && value < 0.001) return '<0.1%';
  return `${(value * 100).toFixed(1)}%`;
}

function money(value: number): string {
  return `${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 元`;
}

function contractLabel(signal?: NonNullable<BusinessRiskP0['observed_signals']>['contract']): string {
  if (!signal || signal.status !== 'observed') return '缺失';
  return `有效 ${signal.active_rows ?? 0}，临期 ${signal.expiring_rows ?? 0}`;
}

function settlementLabel(signal?: NonNullable<BusinessRiskP0['observed_signals']>['settlement']): string {
  if (!signal || signal.status !== 'observed') return '缺失';
  return `未结算 ${(signal.unsettled_ratio ?? 0) * 100}%`;
}

function priceLabel(signal?: NonNullable<BusinessRiskP0['observed_signals']>['price']): string {
  if (!signal || signal.status !== 'observed' || signal.change_ratio == null) return '缺失';
  const sign = signal.change_ratio > 0 ? '+' : '';
  return `环比 ${sign}${(signal.change_ratio * 100).toFixed(1)}%`;
}

function changeLabel(value?: number | null): string {
  if (value == null) return '相邻自然月数据不足';
  const sign = value > 0 ? '+' : '';
  return `${sign}${(value * 100).toFixed(1)}%`;
}
