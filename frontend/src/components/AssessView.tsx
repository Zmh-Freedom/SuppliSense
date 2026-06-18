import { useState, useEffect, useRef, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import { api } from '../api';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Area, ReferenceArea, ReferenceLine } from 'recharts';
import type {
  RiskResult, ESGResult, MacroRiskResult, AlternativeResult,
  ContagionResult, ScenarioResult, SanctionsResult,
} from '../types';
import { queryKeys } from '../query-keys';
import { useWatchlist } from '../hooks';
import SentimentPanel from './SentimentPanel';
import Skeleton, { SkeletonChart } from './Skeleton';

const LEVEL_COLOR: Record<string, string> = {
  '高风险': '#dc2626', '中风险': '#d97706', '低风险': '#16a34a',
  '严重': '#dc2626', '中等': '#d97706', '轻微': '#16a34a',
  'critical': '#dc2626', 'high': '#dc2626', 'medium': '#d97706', 'low': '#16a34a',
};
const LEVEL_BG: Record<string, string> = {
  '高风险': '#fef2f2', '中风险': '#fffbf0', '低风险': '#ecfdf5',
};

type SubTab = 'overview' | 'financial' | 'risk' | 'relations';

export default function AssessView() {
  const { companyName } = useParams<{ companyName?: string }>();
  const navigate = useNavigate();
  const initialName = companyName ? decodeURIComponent(companyName) : '';
  const [name, setName] = useState(initialName);
  const [subTab, setSubTab] = useState<SubTab>('overview');
  const [showSuggestions, setShowSuggestions] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const { companies: watchlist } = useWatchlist();

  const filteredSuggestions = useMemo(() => {
    const q = name.trim().toLowerCase();
    if (!q) return watchlist;
    return watchlist.filter(c => c.toLowerCase().includes(q));
  }, [name, watchlist]);

  const assessMutation = useMutation({
    mutationFn: ({ target, force }: { target: string; force?: boolean }) =>
      api.post<RiskResult>('/risk/assess', { company_name: target.trim(), force_refresh: !!force }),
  });

  const trendQuery = useQuery({
    queryKey: queryKeys.riskTrend(name),
    queryFn: () => api.get<{ data: { date: string; risk_score: number }[] }>(
      `/trend/risk/${encodeURIComponent(name)}?days=90`
    ),
    enabled: !!assessMutation.data,
  });

  const data = assessMutation.data ?? null;
  const loading = assessMutation.isPending;
  const error = assessMutation.error ? '评估失败' : '';
  const refreshing = loading && !!data;
  const trend = trendQuery.data?.data ?? [];

  useEffect(() => {
    if (initialName) {
      setName(initialName);
      assessMutation.mutate({ target: initialName });
    }
  }, [initialName]);

  const assess = (target?: string) => {
    const t = (target || name).trim();
    if (!t) return;
    setSubTab('overview');
    assessMutation.mutate({ target: t });
  };

  const selectCompany = (company: string) => {
    setName(company);
    setShowSuggestions(false);
    assess(company);
  };

  const refresh = () => {
    if (!name.trim()) return;
    assessMutation.mutate({ target: name, force: true });
  };

  const score = data?.risk_score ?? 0;
  const color = score <= 30 ? '#2d8c63' : score <= 60 ? '#d4a040' : '#e06060';
  const bg = score <= 30 ? '#ecfdf5' : score <= 60 ? '#fffbf0' : '#fef5f5';
  const rd = data?.risk_detail;
  const fin = data?.financial;

  const subTabs: { key: SubTab; label: string }[] = [
    { key: 'overview', label: '概览' },
    { key: 'financial', label: '财务' },
    { key: 'risk', label: '风险' },
    { key: 'relations', label: '关联' },
  ];

  return (
    <div className="h-full py-6 px-6 overflow-auto">
      <div className="max-w-2xl mx-auto relative">
        {/* Floating watchlist card — fixed vertical center, to the left of content */}
        {watchlist.length > 0 && (
          <div className="hidden xl:block fixed top-1/2 -translate-y-1/2 z-10"
            style={{ left: `max(24px, calc((100vw - 672px) / 2 - 184px))` }}>
            <div className="w-40 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl shadow-md overflow-hidden">
              <h3 className="text-[11px] font-medium text-gray-400 px-3 pt-3 pb-2">监控清单</h3>
              <div className="max-h-[50vh] overflow-auto px-1.5 pb-1.5">
                {watchlist.map(c => (
                  <button
                    key={c}
                    onClick={() => selectCompany(c)}
                    className={`w-full text-left text-xs rounded-lg px-2.5 py-2 transition-colors truncate ${
                      name === c
                        ? 'bg-[var(--color-primary-bg)] text-white'
                        : 'text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]'
                    }`}
                  >
                    {c}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* search bar with autocomplete */}
        <div className="relative mb-6">
          <div className="flex gap-2">
            <div className="flex-1 relative">
              <input
                ref={inputRef}
                value={name}
                onChange={e => { setName(e.target.value); setShowSuggestions(true); }}
                onFocus={() => setShowSuggestions(true)}
                onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
                onKeyDown={e => e.key === 'Enter' && assess()}
                placeholder="输入企业名称搜索…"
                className="w-full border border-[var(--color-border)] rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-[var(--color-border-focus)] min-h-[44px]"
              />
              <AnimatePresence>
                {showSuggestions && name.trim() && filteredSuggestions.length > 0 && (
                  <motion.div
                    className="absolute left-0 right-0 top-full mt-1 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl shadow-lg z-10 overflow-hidden"
                    initial={{ opacity: 0, y: -4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    transition={{ duration: 0.15 }}
                  >
                    {filteredSuggestions.slice(0, 8).map(c => (
                      <button
                        key={c}
                        onMouseDown={() => selectCompany(c)}
                        className="w-full text-left px-4 py-2.5 text-sm text-[var(--color-text)] hover:bg-[var(--color-surface-hover)] transition-colors flex items-center gap-2"
                      >
                        <svg className="w-4 h-4 text-gray-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
                        <span>{c}</span>
                      </button>
                    ))}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
            <button onClick={() => assess()} disabled={loading} className="bg-[var(--color-primary-bg)] text-white rounded-xl px-6 py-2.5 text-sm hover:bg-[var(--color-primary-hover)] disabled:opacity-50 min-h-[44px] inline-flex items-center">
              {loading ? '评估中…' : '评估'}
            </button>
          </div>
        </div>

      {error && <p className="text-red-400 text-sm mb-4">{error}</p>}

      {loading && !data && (
        <div className="space-y-6">
          <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-6 shadow-sm">
            <div className="flex items-center gap-4">
              <Skeleton className="w-14 h-14 rounded-full" />
              <div className="flex-1">
                <Skeleton className="h-6 w-24 mb-2" />
                <Skeleton className="h-3 w-48" />
              </div>
            </div>
          </div>
          <SkeletonChart />
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-16 rounded-xl" />)}
          </div>
        </div>
      )}

      {data && (
        <>
          {/* Score header + Agent button */}
          <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 mb-4 shadow-sm" style={{ background: bg }}>
            <div className="flex items-center gap-4 flex-wrap">
              <div className="w-14 h-14 rounded-full flex items-center justify-center text-white font-bold text-lg shrink-0" style={{ background: color }}>
                {data.risk_score}
              </div>
              <span className="text-lg font-semibold" style={{ color }}>{data.risk_level}</span>
              {data.is_listed && (
                <span className="text-[10px] px-1.5 py-0.5 rounded border border-[var(--color-border)] bg-[var(--color-surface)] text-gray-500 shrink-0">上市</span>
              )}
              <div className="flex-1 min-w-[120px] bg-[var(--color-border)] h-2 rounded-full">
                <div className="h-full rounded-full transition-all duration-700" style={{ width: `${data.risk_score}%`, background: color }} />
              </div>

              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => navigate(`/chat?q=${encodeURIComponent(`请对${name}进行全面深度分析，包括风险评估、ESG、舆情、合规和传染风险`)}`)}
                  className="text-xs border border-[var(--color-primary-bg)]/30 text-[var(--color-primary-bg)] rounded-lg px-3 py-1.5 hover:bg-[var(--color-primary-bg)]/10 transition-colors inline-flex items-center gap-1 min-h-[36px]"
                >
                  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2l2.4 7.2h7.6l-6 4.8 2.4 7.2-6.4-4.8-6.4 4.8 2.4-7.2-6-4.8h7.6z"/></svg>
                  Agent 深度分析
                </button>
                {data.cache_age_hours != null && (
                  <span className={`text-[10px] whitespace-nowrap ${data.is_stale ? 'text-amber-500' : 'text-gray-400'}`}
                    title={data.cached_at?.slice(0, 19).replace('T', ' ') ?? ''}>
                    {data.cache_age_hours < 1
                      ? `${Math.round(data.cache_age_hours * 60)} 分钟前`
                      : `${data.cache_age_hours.toFixed(1)} 小时前`}
                  </span>
                )}
                {data.is_stale && (
                  <button onClick={refresh} disabled={refreshing}
                    className="text-xs text-amber-600 hover:text-amber-800 border border-amber-200 rounded-md px-2 py-1 disabled:opacity-50 whitespace-nowrap min-h-[36px] inline-flex items-center">
                    {refreshing ? '刷新中…' : '刷新'}
                  </button>
                )}
                <a href={`/api/v1/report/excel/${encodeURIComponent(name)}`}
                  className="text-xs bg-[#16a34a] text-white rounded-lg px-3 py-1.5 hover:bg-green-700 transition-colors no-underline inline-flex items-center min-h-[36px]">导出 Excel</a>
                <a href={`/api/v1/report/html/${encodeURIComponent(name)}`} target="_blank" rel="noopener noreferrer"
                  className="text-xs bg-[var(--color-primary-bg)] text-white rounded-lg px-3 py-1.5 hover:bg-[var(--color-primary-hover)] transition-colors no-underline inline-flex items-center min-h-[36px]">导出报告</a>
              </div>
            </div>
          </div>

          {/* Sub-tabs */}
          <div className="flex gap-1 mb-4 border-b border-[var(--color-border)]">
            {subTabs.map(t => (
              <button
                key={t.key}
                onClick={() => setSubTab(t.key)}
                className={`px-4 py-2 text-sm rounded-t-lg transition-colors min-h-[40px] -mb-[1px] ${
                  subTab === t.key
                    ? 'text-[var(--color-primary-bg)] border-b-2 border-[var(--color-primary-bg)] font-medium'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {/* ---- OVERVIEW TAB ---- */}
          {subTab === 'overview' && (
            <div className="space-y-4">
              {/* trend chart */}
              {trend.length > 1 && (
                <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
                  <h3 className="text-sm font-semibold text-[var(--color-text)] mb-1">近90天风险评分趋势</h3>
                  <p className="text-[11px] text-gray-400 mb-4">
                    最新 {trend[trend.length - 1]?.risk_score ?? '—'} 分 · 最高 {Math.max(...trend.map(d => d.risk_score))} · 最低 {Math.min(...trend.map(d => d.risk_score))}
                  </p>
                  <ResponsiveContainer width="100%" height={220}>
                    <LineChart data={trend} margin={{top: 5, right: 5, bottom: 5, left: 0}}>
                      <defs>
                        <linearGradient id="riskAreaGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#333" stopOpacity={0.12} />
                          <stop offset="100%" stopColor="#333" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <ReferenceArea y1={60} y2={100} fill="#fef2f2" fillOpacity={0.6} />
                      <ReferenceArea y1={30} y2={60} fill="#fffbeb" fillOpacity={0.6} />
                      <ReferenceArea y1={0} y2={30} fill="#f0fdf4" fillOpacity={0.6} />
                      <ReferenceLine y={60} stroke="#fca5a5" strokeDasharray="4 4" strokeWidth={1} />
                      <ReferenceLine y={30} stroke="#86efac" strokeDasharray="4 4" strokeWidth={1} />
                      <CartesianGrid strokeDasharray="3 3" stroke="#f5f5f5" vertical={false} />
                      <XAxis dataKey="date" tick={{fontSize: 10, fill: '#999'}} axisLine={{stroke: '#eee'}} tickLine={false} />
                      <YAxis domain={[0, 100]} tick={{fontSize: 10, fill: '#999'}} axisLine={false} tickLine={false} width={24} />
                      <Tooltip
                        contentStyle={{background: '#fff', border: '1px solid #e8e8e3', borderRadius: 12, boxShadow: '0 4px 12px rgba(0,0,0,0.06)', fontSize: 12, padding: '8px 12px'}}
                        labelStyle={{color: '#999', marginBottom: 2}}
                        formatter={(value) => {
                          const lvl = Number(value) >= 60 ? '高风险' : Number(value) >= 30 ? '中风险' : '低风险';
                          const clr = Number(value) >= 60 ? '#dc2626' : Number(value) >= 30 ? '#d97706' : '#16a34a';
                          return [<span key={0} style={{color: clr, fontWeight: 600}}>{value} 分 · {lvl}</span>, ''];
                        }}
                      />
                      <Area type="monotone" dataKey="risk_score" stroke="none" fill="url(#riskAreaGrad)" />
                      <Line type="monotone" dataKey="risk_score" stroke="#333" strokeWidth={2.5}
                        dot={{r: 3, fill: '#fff', stroke: '#333', strokeWidth: 2}}
                        activeDot={{r: 5, fill: '#333', stroke: '#fff', strokeWidth: 2}} />
                    </LineChart>
                  </ResponsiveContainer>
                  <div className="flex justify-center gap-4 mt-3 text-[10px] text-gray-400">
                    <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-green-100 border border-green-200" />低风险 0-30</span>
                    <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-amber-50 border border-amber-200" />中风险 30-60</span>
                    <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-red-50 border border-red-200" />高风险 60-100</span>
                  </div>
                </div>
              )}

              {/* key metrics summary */}
              {fin && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <Metric label="营收增长" value={`${(fin.revenue_growth * 100).toFixed(1)}%`} />
                  <Metric label="净利增长" value={`${(fin.net_profit_growth * 100).toFixed(1)}%`} />
                  <Metric label="负债率" value={`${(fin.debt_ratio * 100).toFixed(1)}%`} />
                  <Metric label="每股现金流" value={`¥${fin.cash_flow.toFixed(2)}`} />
                </div>
              )}

              {/* risk summary */}
              <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
                <h3 className="text-sm font-semibold mb-3 text-[var(--color-text-secondary)]">风险概况</h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                  <div>
                    <p className="font-medium mb-2 text-[var(--color-text-secondary)]">司法</p>
                    <Row label="诉讼" value={rd?.lawsuit_count ?? 0} />
                    <Row label="被执行" value={rd?.executed_count ?? 0} />
                    <Row label="失信" value={rd?.dishonesty_count ?? 0} />
                    <Row label="重大诉讼" warn={rd?.major_lawsuit} />
                  </div>
                  <div>
                    <p className="font-medium mb-2 text-[var(--color-text-secondary)]">经营</p>
                    <Row label="经营异常" value={rd?.abnormal_operation_count ?? 0} />
                    <Row label="行政处罚" value={rd?.administrative_penalty_count ?? 0} />
                    <Row label="法人变更频繁" warn={rd?.legal_person_change_frequent} />
                  </div>
                  <div>
                    <p className="font-medium mb-2 text-[var(--color-text-secondary)]">资金链</p>
                    <Row label="对外担保" value={rd?.guarantee_count ?? 0} />
                    <Row label="股权质押" value={rd?.pledge_count ?? 0} />
                    <Row label="破产/清算" value={rd?.bankruptcy_count ?? 0} />
                  </div>
                  <div>
                    <p className="font-medium mb-2 text-[var(--color-text-secondary)]">财务预警</p>
                    {fin ? (
                      <>
                        <Row label="负债率>70%" warn={fin.debt_ratio > 0.7} extra={`${(fin.debt_ratio * 100).toFixed(1)}%`} />
                        <Row label="现金为负" warn={fin.cash_flow < 0} extra={`¥${fin.cash_flow.toFixed(2)}`} />
                      </>
                    ) : <p className="text-gray-400 text-xs">无财报数据</p>}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* ---- FINANCIAL TAB ---- */}
          {subTab === 'financial' && (
            <div className="space-y-3">
              {fin ? (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <Metric label="营收增长" value={`${(fin.revenue_growth * 100).toFixed(1)}%`} />
                    <Metric label="净利增长" value={`${(fin.net_profit_growth * 100).toFixed(1)}%`} />
                    <Metric label="负债率" value={`${(fin.debt_ratio * 100).toFixed(1)}%`} />
                    <Metric label="每股现金流" value={`¥${fin.cash_flow.toFixed(2)}`} />
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <Metric label="ROE" value={`${((fin.roe ?? 0) * 100).toFixed(1)}%`} />
                    <Metric label="净利率" value={`${((fin.net_profit_margin ?? 0) * 100).toFixed(1)}%`} />
                    <Metric label="流动比率" value={`${(fin.current_ratio ?? 0).toFixed(2)}`} />
                    <Metric label="速动比率" value={`${(fin.quick_ratio ?? 0).toFixed(2)}`} />
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <Metric label="存货周转" value={`${(fin.inventory_turnover ?? 0).toFixed(1)}`} />
                    <Metric label="应收款周转" value={`${(fin.ar_turnover_days ?? 0).toFixed(0)}天`} />
                    <Metric label="扣非占比" value={`${((fin.recurring_profit_ratio ?? 0) * 100).toFixed(1)}%`} />
                    <Metric label="产权比率" value={`${(fin.equity_ratio ?? 0).toFixed(2)}`} />
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <Metric label="营收趋势" value={fin.revenue_trend && fin.revenue_trend < 0 ? `↓ ${Math.abs(fin.revenue_trend * 100).toFixed(1)}%` : fin.revenue_trend ? '→ 稳定' : '-'} />
                    <Metric label="负债趋势" value={fin.debt_trend && fin.debt_trend > 0 ? `↑ +${(fin.debt_trend * 100).toFixed(1)}%` : fin.debt_trend ? '→ 稳定' : '-'} />
                    <Metric label="净利趋势" value={fin.net_profit_trend && fin.net_profit_trend < 0 ? `↓ ${Math.abs(fin.net_profit_trend * 100).toFixed(1)}%` : fin.net_profit_trend ? '→ 稳定' : '-'} />
                  </div>
                </>
              ) : (
                <p className="text-sm text-gray-400 text-center py-12">该企业暂无财务数据（非上市或数据不可用）</p>
              )}
            </div>
          )}

          {/* ---- RISK TAB ---- */}
          {subTab === 'risk' && (
            <div className="space-y-4">
              {/* risk detail */}
              <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
                <h3 className="text-sm font-semibold mb-3 text-[var(--color-text-secondary)]">风险明细</h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                  <div>
                    <p className="font-medium mb-2 text-[var(--color-text-secondary)]">司法</p>
                    <Row label="诉讼" value={rd?.lawsuit_count ?? 0} />
                    <Row label="被执行" value={rd?.executed_count ?? 0} />
                    <Row label="失信" value={rd?.dishonesty_count ?? 0} />
                    <Row label="重大诉讼" warn={rd?.major_lawsuit} />
                  </div>
                  <div>
                    <p className="font-medium mb-2 text-[var(--color-text-secondary)]">经营</p>
                    <Row label="经营异常" value={rd?.abnormal_operation_count ?? 0} />
                    <Row label="行政处罚" value={rd?.administrative_penalty_count ?? 0} />
                    <Row label="法人频繁变更" warn={rd?.legal_person_change_frequent} />
                    <Row label="环保处罚" value={rd?.env_penalty_count ?? 0} />
                  </div>
                  <div>
                    <p className="font-medium mb-2 text-[var(--color-text-secondary)]">资金链</p>
                    <Row label="对外担保" value={rd?.guarantee_count ?? 0} />
                    <Row label="股权质押" value={rd?.pledge_count ?? 0} />
                    <Row label="破产/清算" value={rd?.bankruptcy_count ?? 0} />
                  </div>
                  <div>
                    <p className="font-medium mb-2 text-[var(--color-text-secondary)]">财务预警</p>
                    {fin ? (
                      <>
                        <Row label="负债率>70%" warn={fin.debt_ratio > 0.7} extra={`${(fin.debt_ratio * 100).toFixed(1)}%`} />
                        <Row label="现金为负" warn={fin.cash_flow < 0} extra={`¥${fin.cash_flow.toFixed(2)}`} />
                      </>
                    ) : <p className="text-gray-400 text-xs">无财报数据</p>}
                  </div>
                </div>
              </div>

              {/* Expandable sections */}
              <Expandable<ESGResult> key={`esg-${name}`} title="ESG 评分" endpoint={`/p2/esg/${encodeURIComponent(name)}`}
                render={(d) => (
                  <div className="grid grid-cols-3 gap-3">
                    {(['environmental', 'social', 'governance'] as const).map(dim => {
                      const dd = d[dim];
                      return (
                        <div key={dim} className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-3">
                          <div className="text-xs text-gray-500 mb-1">{dim === 'environmental' ? 'E·环境' : dim === 'social' ? 'S·社会' : 'G·治理'}</div>
                          <div className="text-lg font-bold" style={{ color: LEVEL_COLOR[dd.level] }}>{dd.score.toFixed(0)}</div>
                          <div className="text-[11px]" style={{ color: LEVEL_COLOR[dd.level] }}>{dd.level}</div>
                          {dd.detail.map((item, i) => (
                            <div key={i} className="text-[10px] text-gray-500 mt-1 flex justify-between"><span>{item.item}</span><span>{item.value}</span></div>
                          ))}
                        </div>
                      );
                    })}
                  </div>
                )}
              />

              <Expandable<MacroRiskResult> key={`macro-${name}`} title="宏观风险" endpoint={`/analysis/macro/${encodeURIComponent(name)}`}
                render={(d) => (
                  <div>
                    <div className="flex items-center gap-3 mb-3">
                      <span className="text-2xl font-bold" style={{ color: LEVEL_COLOR[d.total_level] }}>{d.total_score}</span>
                      <span className="text-sm" style={{ color: LEVEL_COLOR[d.total_level] }}>{d.total_level}</span>
                      <span className="text-xs text-gray-400">行业{d.industry_risk?.risk_score || 0} + 地区{d.regional_risk?.score || 0} + 政策{d.policy_risks?.score || 0}</span>
                    </div>
                    {d.policy_risks?.tags?.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {d.policy_risks.tags.map((t, i) => (
                          <span key={i} className="text-[10px] px-2 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-100">{t.tag}</span>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              />

              <Expandable<SanctionsResult> key={`sanc-${name}`} title="制裁筛查" endpoint={`/analysis/sanctions/${encodeURIComponent(name)}`}
                render={(d) => (
                  <div>
                    <span className={`text-sm font-bold ${d.clean ? 'text-green-600' : 'text-red-600'}`}>
                      {d.clean ? '✅ 未命中' : `⚠️ ${d.match_count}条命中`}
                    </span>
                    {d.matches?.length > 0 && (
                      <div className="mt-2 space-y-1">
                        {d.matches.map((m, i) => (
                          <div key={i} className="text-xs text-gray-600">
                            <span className={m.level === 'critical' ? 'text-red-500' : 'text-amber-600'}>{m.name || m.detail || m.country}</span>
                            {m.program && <span className="text-gray-400"> · {m.program}</span>}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              />

              <Expandable<ScenarioResult> key={`scenario-${name}`} title="情景模拟" endpoint={`/analysis/scenario/${encodeURIComponent(name)}?scenario=bankruptcy`}
                render={(d) => (
                  <div>
                    <div className="flex items-center gap-3 mb-2">
                      <span className="text-lg font-bold" style={{ color: LEVEL_COLOR[d.impact_level] }}>{d.impact_score} · {d.impact_level}</span>
                      <span className="text-xs text-gray-400">{d.scenario_desc}</span>
                    </div>
                    <div className="space-y-1">
                      {d.suggested_actions?.slice(0, 3).map((a, i) => (
                        <div key={i} className="text-xs text-gray-600">{a}</div>
                      ))}
                    </div>
                  </div>
                )}
              />

              {/* sentiment */}
              <ExpandableSentiment key={`sentiment-${name}`} name={name} />
            </div>
          )}

          {/* ---- RELATIONS TAB ---- */}
          {subTab === 'relations' && (
            <div className="space-y-4">
              <Expandable<ContagionResult> key={`contagion-${name}`} title="风险传染" endpoint={`/p2/contagion/${encodeURIComponent(name)}`}
                render={(d) => (
                  <div>
                    <div className="flex gap-4 mb-2 text-xs text-gray-500">
                      <span>关联方 {d.related_count}</span>
                      <span>分支 {d.branch_count}</span>
                      <span>依赖 {d.dependency_count}</span>
                      <span className="text-red-500">高风险 {d.high_risk_related_count}</span>
                    </div>
                    {d.related_entities?.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {d.related_entities.slice(0, 8).map((e, i) => (
                          <span key={i} className="text-[10px] px-2 py-0.5 rounded bg-gray-50 border border-gray-100 text-gray-600">
                            {e.name.slice(0, 15)} <span className="text-gray-400">({e.relation_type})</span>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              />

              <Expandable<AlternativeResult> key={`alt-${name}`} title="替代建议" endpoint={`/analysis/alternatives/${encodeURIComponent(name)}`}
                render={(d) => (
                  <div>
                    {d.alternatives?.length === 0 ? (
                      <p className="text-xs text-gray-400">{d.source_risk_score != null && d.source_risk_score < 60 ? '风险较低，暂不需替代' : '暂未找到替代'}</p>
                    ) : (
                      <div className="grid grid-cols-2 gap-2">
                        {d.alternatives?.slice(0, 4).map((a, i) => (
                          <div key={i} className="flex items-center gap-2 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg px-3 py-2">
                            <span className="text-xs font-bold text-green-600">#{i + 1}</span>
                            <span className="text-xs text-[var(--color-text)] truncate flex-1">{a.company_name.slice(0, 12)}</span>
                            {a.risk_score !== null && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: LEVEL_BG[a.risk_level], color: LEVEL_COLOR[a.risk_level] }}>{a.risk_level}</span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              />
            </div>
          )}
        </>
      )}
      </div>{/* max-w-2xl + relative */}
    </div>
  );
}

function ExpandableSentiment({ name }: { name: string }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-3">
      <button
        onClick={() => setOpen(!open)}
        className={`w-full flex items-center justify-between bg-[var(--color-surface)] border border-[var(--color-border)] px-4 py-3 hover:border-[var(--color-border-hover)] transition-colors text-left shadow-sm hover:shadow-md ${open ? 'rounded-t-xl' : 'rounded-xl'}`}
      >
        <span className="text-sm font-medium text-[var(--color-text-secondary)]">📰 舆情分析</span>
        <motion.span
          className="text-gray-400 text-xs inline-block"
          animate={{ rotate: open ? 180 : 0 }}
          transition={{ duration: 0.2 }}
        >▼</motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.4, 0, 0.2, 1] }}
            className="overflow-hidden"
          >
            <div className="bg-[var(--color-page-bg)] border border-[var(--color-border)] border-t-0 rounded-b-xl p-0">
              <SentimentPanel companyName={name} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ---- Expandable section ----

function Expandable<T>({ title, endpoint, render }: { title: string; endpoint: string; render: (d: T) => React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState(false);

  const toggle = async () => {
    if (open) { setOpen(false); return; }
    setOpen(true);
    if (!data && !error) {
      try {
        const d = await api.get<T>(endpoint);
        setData(d);
      } catch { setError(true); }
    }
  };

  return (
    <div className="mt-3">
      <button
        onClick={toggle}
        className={`w-full flex items-center justify-between bg-[var(--color-surface)] border border-[var(--color-border)] px-4 py-3 hover:border-[var(--color-border-hover)] transition-colors text-left shadow-sm hover:shadow-md ${open ? 'rounded-t-xl' : 'rounded-xl'}`}
      >
        <span className="text-sm font-medium text-[var(--color-text-secondary)]">{title}</span>
        <motion.span
          className="text-gray-400 text-xs inline-block"
          animate={{ rotate: open ? 180 : 0 }}
          transition={{ duration: 0.2 }}
        >▼</motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.4, 0, 0.2, 1] }}
            className="overflow-hidden"
          >
            <div className="bg-[var(--color-page-bg)] border border-[var(--color-border)] border-t-0 rounded-b-xl px-4 py-3">
              {error ? (
                <p className="text-xs text-gray-400">暂无数据</p>
              ) : !data ? (
                <p className="text-xs text-gray-400">加载中…</p>
              ) : (
                render(data)
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ---- helpers ----

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-3 text-center shadow-sm">
      <div className="text-base font-semibold">{value}</div>
      <div className="text-xs text-gray-400 mt-0.5">{label}</div>
    </div>
  );
}

function Row({ label, value, warn, extra }: { label: string; value?: number; warn?: boolean; extra?: string }) {
  const icon = warn === undefined ? '' : warn ? '⚠️' : '✓';
  const text = value !== undefined ? String(value) : '';
  return (
    <p className="text-xs text-gray-500 py-0.5">
      {label}  {icon && <span className="text-xs">{icon}</span>}  {text}{extra ? ` (${extra})` : ''}
    </p>
  );
}
