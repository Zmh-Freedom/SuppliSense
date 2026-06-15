import { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { api } from '../api';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Area, ReferenceArea, ReferenceLine } from 'recharts';
import type {
  RiskResult, ESGResult, MacroRiskResult, AlternativeResult,
  ContagionResult, ScenarioResult, SanctionsResult,
} from '../types';
import { queryKeys } from '../query-keys';
import SentimentPanel from './SentimentPanel';

const LEVEL_COLOR: Record<string, string> = {
  '高风险': '#dc2626', '中风险': '#d97706', '低风险': '#16a34a',
  '严重': '#dc2626', '中等': '#d97706', '轻微': '#16a34a',
  'critical': '#dc2626', 'high': '#dc2626', 'medium': '#d97706', 'low': '#16a34a',
};
const LEVEL_BG: Record<string, string> = {
  '高风险': '#fef2f2', '中风险': '#fffbf0', '低风险': '#ecfdf5',
};

export default function AssessView() {
  const { companyName } = useParams<{ companyName?: string }>();
  const initialName = companyName ? decodeURIComponent(companyName) : '';
  const [name, setName] = useState(initialName);

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

  const assess = () => {
    if (!name.trim()) return;
    assessMutation.mutate({ target: name });
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

  return (
    <div className="max-w-2xl mx-auto py-6 px-4">
      <div className="flex gap-2 mb-6">
        <input
          value={name}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && assess()}
          placeholder="输入完整企业名称"
          className="flex-1 border border-[#e8e8e3] rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-[#bbb]"
        />
        <button onClick={assess} disabled={loading} className="bg-[#333] text-white rounded-xl px-6 py-2.5 text-sm hover:bg-[#555] disabled:opacity-50">
          {loading ? '评估中…' : '评估'}
        </button>
      </div>

      {error && <p className="text-red-400 text-sm mb-4">{error}</p>}

      {data && (
        <>
          {/* score */}
          <div className="bg-white border border-[#e8e8e3] rounded-2xl p-6 mb-4" style={{ background: bg }}>
            <div className="flex items-center gap-6">
              <div className="w-14 h-14 rounded-full flex items-center justify-center text-white font-bold text-lg" style={{ background: color }}>
                {data.risk_score}
              </div>
              <span className="text-lg font-semibold" style={{ color }}>{data.risk_level}</span>
              {data.is_listed && (
                <span className="text-[10px] px-1.5 py-0.5 rounded border border-[#e8e8e3] bg-white text-gray-500 shrink-0">上市</span>
              )}
              <div className="flex-1 bg-[#e5e5e0] h-2 rounded-full">
                <div className="h-full rounded-full transition-all duration-700" style={{ width: `${data.risk_score}%`, background: color }} />
              </div>
              <div className="flex items-center gap-2 shrink-0">
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
                    className="text-[10px] text-amber-600 hover:text-amber-800 border border-amber-200 rounded-md px-1.5 py-0.5 disabled:opacity-50 whitespace-nowrap">
                    {refreshing ? '刷新中…' : '刷新'}
                  </button>
                )}
                <a
                  href={`/api/v1/report/excel/${encodeURIComponent(name)}`}
                  className="text-xs bg-[#16a34a] text-white rounded-lg px-3 py-1.5 hover:bg-green-700 transition-colors no-underline"
                >
                  导出 Excel
                </a>
                <a
                  href={`/api/v1/report/html/${encodeURIComponent(name)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs bg-[#333] text-white rounded-lg px-3 py-1.5 hover:bg-[#555] transition-colors no-underline"
                >
                  导出报告
                </a>
              </div>
            </div>
          </div>

          {/* risk trend */}
          {trend.length > 1 && (
            <div className="bg-white border border-[#e8e8e3] rounded-2xl p-5 mb-6">
              <h3 className="text-sm font-semibold text-[#333] mb-1">近90天风险评分趋势</h3>
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
                  {/* Risk zone backgrounds */}
                  <ReferenceArea y1={60} y2={100} fill="#fef2f2" fillOpacity={0.6} />
                  <ReferenceArea y1={30} y2={60} fill="#fffbeb" fillOpacity={0.6} />
                  <ReferenceArea y1={0} y2={30} fill="#f0fdf4" fillOpacity={0.6} />
                  {/* Threshold lines */}
                  <ReferenceLine y={60} stroke="#fca5a5" strokeDasharray="4 4" strokeWidth={1} />
                  <ReferenceLine y={30} stroke="#86efac" strokeDasharray="4 4" strokeWidth={1} />
                  <CartesianGrid strokeDasharray="3 3" stroke="#f5f5f5" vertical={false} />
                  <XAxis dataKey="date" tick={{fontSize: 10, fill: '#999'}} axisLine={{stroke: '#eee'}} tickLine={false} />
                  <YAxis domain={[0, 100]} tick={{fontSize: 10, fill: '#999'}} axisLine={false} tickLine={false} width={24} />
                  <Tooltip
                    contentStyle={{
                      background: '#fff',
                      border: '1px solid #e8e8e3',
                      borderRadius: 12,
                      boxShadow: '0 4px 12px rgba(0,0,0,0.06)',
                      fontSize: 12,
                      padding: '8px 12px',
                    }}
                    labelStyle={{color: '#999', marginBottom: 2}}
                    formatter={(value) => {
                      const lvl = Number(value) >= 60 ? '高风险' : Number(value) >= 30 ? '中风险' : '低风险';
                      const clr = Number(value) >= 60 ? '#dc2626' : Number(value) >= 30 ? '#d97706' : '#16a34a';
                      return [<span key={0} style={{color: clr, fontWeight: 600}}>{value} 分 · {lvl}</span>, ''];
                    }}
                  />
                  <Area type="monotone" dataKey="risk_score" stroke="none" fill="url(#riskAreaGrad)" />
                  <Line
                    type="monotone" dataKey="risk_score"
                    stroke="#333" strokeWidth={2.5}
                    dot={{r: 3, fill: '#fff', stroke: '#333', strokeWidth: 2}}
                    activeDot={{r: 5, fill: '#333', stroke: '#fff', strokeWidth: 2}}
                  />
                </LineChart>
              </ResponsiveContainer>
              {/* Legend */}
              <div className="flex justify-center gap-4 mt-3 text-[10px] text-gray-400">
                <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-green-100 border border-green-200" />低风险 0-30</span>
                <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-amber-50 border border-amber-200" />中风险 30-60</span>
                <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-red-50 border border-red-200" />高风险 60-100</span>
              </div>
            </div>
          )}

          {/* metrics */}
          {fin && (
            <>
              <div className="grid grid-cols-4 gap-3 mb-3">
                <Metric label="营收增长" value={`${(fin.revenue_growth * 100).toFixed(1)}%`} />
                <Metric label="净利增长" value={`${(fin.net_profit_growth * 100).toFixed(1)}%`} />
                <Metric label="负债率" value={`${(fin.debt_ratio * 100).toFixed(1)}%`} />
                <Metric label="每股现金流" value={`¥${fin.cash_flow.toFixed(2)}`} />
              </div>
              <div className="grid grid-cols-4 gap-3 mb-3">
                <Metric label="ROE" value={`${((fin.roe ?? 0) * 100).toFixed(1)}%`} />
                <Metric label="净利率" value={`${((fin.net_profit_margin ?? 0) * 100).toFixed(1)}%`} />
                <Metric label="流动比率" value={`${(fin.current_ratio ?? 0).toFixed(2)}`} />
                <Metric label="速动比率" value={`${(fin.quick_ratio ?? 0).toFixed(2)}`} />
              </div>
              <div className="grid grid-cols-4 gap-3 mb-3">
                <Metric label="存货周转" value={`${(fin.inventory_turnover ?? 0).toFixed(1)}`} />
                <Metric label="应收款周转" value={`${(fin.ar_turnover_days ?? 0).toFixed(0)}天`} />
                <Metric label="扣非占比" value={`${((fin.recurring_profit_ratio ?? 0) * 100).toFixed(1)}%`} />
                <Metric label="产权比率" value={`${(fin.equity_ratio ?? 0).toFixed(2)}`} />
              </div>
              <div className="grid grid-cols-3 gap-3 mb-6">
                <Metric label="营收趋势" value={fin.revenue_trend && fin.revenue_trend < 0 ? `↓ ${Math.abs(fin.revenue_trend * 100).toFixed(1)}%` : fin.revenue_trend ? '→ 稳定' : '-'} />
                <Metric label="负债趋势" value={fin.debt_trend && fin.debt_trend > 0 ? `↑ +${(fin.debt_trend * 100).toFixed(1)}%` : fin.debt_trend ? '→ 稳定' : '-'} />
                <Metric label="净利趋势" value={fin.net_profit_trend && fin.net_profit_trend < 0 ? `↓ ${Math.abs(fin.net_profit_trend * 100).toFixed(1)}%` : fin.net_profit_trend ? '→ 稳定' : '-'} />
              </div>
            </>
          )}
          {!fin && (
            <div className="grid grid-cols-4 gap-3 mb-6">
              {Array(4).fill(null).map((_, i) => <Metric key={i} label="—" value="无数据" />)}
            </div>
          )}

          {/* risk detail */}
          <h3 className="text-sm font-semibold mb-3 text-[#555]">风险明细</h3>
          <div className="grid grid-cols-4 gap-4 text-sm">
            <div>
              <p className="font-medium mb-2 text-[#555]">司法</p>
              <Row label="诉讼" value={rd?.lawsuit_count ?? 0} />
              <Row label="被执行" value={rd?.executed_count ?? 0} />
              <Row label="失信" value={rd?.dishonesty_count ?? 0} />
              <Row label="重大诉讼" warn={rd?.major_lawsuit} />
            </div>
            <div>
              <p className="font-medium mb-2 text-[#555]">经营</p>
              <Row label="经营异常" value={rd?.abnormal_operation_count ?? 0} />
              <Row label="行政处罚" value={rd?.administrative_penalty_count ?? 0} />
              <Row label="法人频繁变更" warn={rd?.legal_person_change_frequent} />
              <Row label="环保处罚" value={rd?.env_penalty_count ?? 0} />
            </div>
            <div>
              <p className="font-medium mb-2 text-[#555]">资金链</p>
              <Row label="对外担保" value={rd?.guarantee_count ?? 0} />
              <Row label="股权质押" value={rd?.pledge_count ?? 0} />
              <Row label="破产/清算" value={rd?.bankruptcy_count ?? 0} />
            </div>
            <div>
              <p className="font-medium mb-2 text-[#555]">财务</p>
              {fin ? (
                <>
                  <Row label="负债率>70%" warn={fin.debt_ratio > 0.7} extra={`${(fin.debt_ratio * 100).toFixed(1)}%`} />
                  <Row label="现金为负" warn={fin.cash_flow < 0} extra={`¥${fin.cash_flow.toFixed(2)}`} />
                </>
              ) : <p className="text-gray-400 text-xs">无财报数据</p>}
            </div>
          </div>

          {/* ---- Additional analysis sections (collapsible) ---- */}

          {/* ESG */}
          <Expandable<ESGResult> key={`esg-${name}`} title="🌍 ESG 评分" endpoint={`/p2/esg/${encodeURIComponent(name)}`}
            render={(d) => (
              <div className="grid grid-cols-3 gap-3">
                {(['environmental', 'social', 'governance'] as const).map(dim => {
                  const dd = d[dim];
                  return (
                    <div key={dim} className="bg-white border border-[#e8e8e3] rounded-xl p-3">
                      <div className="text-xs text-gray-500 mb-1">{dim === 'environmental' ? 'E·环境' : dim === 'social' ? 'S·社会' : 'G·治理'}</div>
                      <div className="text-lg font-bold" style={{ color: LEVEL_COLOR[dd.level] }}>{dd.score.toFixed(0)}</div>
                      <div className="text-[11px]" style={{ color: LEVEL_COLOR[dd.level] }}>{dd.level}</div>
                      {dd.detail.map((item, i) => (
                        <div key={i} className="text-[10px] text-gray-500 mt-1 flex justify-between">
                          <span>{item.item}</span>
                          <span>{item.value}</span>
                        </div>
                      ))}
                    </div>
                  );
                })}
              </div>
            )}
          />

          <Expandable<MacroRiskResult> key={`macro-${name}`} title="🌐 宏观风险" endpoint={`/analysis/macro/${encodeURIComponent(name)}`}
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

          <Expandable<AlternativeResult> key={`alt-${name}`} title="🔀 替代建议" endpoint={`/analysis/alternatives/${encodeURIComponent(name)}`}
            render={(d) => (
              <div>
                {d.alternatives?.length === 0 ? (
                  <p className="text-xs text-gray-400">{d.source_risk_score != null && d.source_risk_score < 60 ? '风险较低，暂不需替代' : '暂未找到替代'}</p>
                ) : (
                  <div className="grid grid-cols-2 gap-2">
                    {d.alternatives?.slice(0, 4).map((a, i) => (
                      <div key={i} className="flex items-center gap-2 bg-white border border-[#e8e8e3] rounded-lg px-3 py-2">
                        <span className="text-xs font-bold text-green-600">#{i + 1}</span>
                        <span className="text-xs text-[#333] truncate flex-1">{a.company_name.slice(0, 12)}</span>
                        {a.risk_score !== null && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: LEVEL_BG[a.risk_level], color: LEVEL_COLOR[a.risk_level] }}>
                            {a.risk_level}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          />

          <Expandable<ContagionResult> key={`contagion-${name}`} title="🔗 风险传染" endpoint={`/p2/contagion/${encodeURIComponent(name)}`}
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

          <Expandable<ScenarioResult> key={`scenario-${name}`} title="🎯 情景模拟" endpoint={`/analysis/scenario/${encodeURIComponent(name)}?scenario=bankruptcy`}
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

          <Expandable<SanctionsResult> key={`sanc-${name}`} title="🛡️ 制裁筛查" endpoint={`/analysis/sanctions/${encodeURIComponent(name)}`}
            render={(d) => (
              <div>
                <span className={`text-sm font-bold ${d.clean ? 'text-green-600' : 'text-red-600'}`}>
                  {d.clean ? '✅ 未命中' : `⚠️ ${d.match_count}条命中`}
                </span>
                {d.matches?.length > 0 && (
                  <div className="mt-2 space-y-1">
                    {d.matches.map((m, i) => (
                      <div key={i} className="text-xs text-gray-600">
                        <span className={m.level === 'critical' ? 'text-red-500' : 'text-amber-600'}>
                          {m.name || m.detail || m.country}
                        </span>
                        {m.program && <span className="text-gray-400"> · {m.program}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          />

          {/* sentiment - inline collapsible */}
          <ExpandableSentiment key={`sentiment-${name}`} name={name} />
        </>
      )}
    </div>
  );
}

function ExpandableSentiment({ name }: { name: string }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-3">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between bg-white border border-[#e8e8e3] rounded-xl px-4 py-3 hover:border-[#ccc] transition-colors text-left"
      >
        <span className="text-sm font-medium text-[#555]">📰 舆情分析</span>
        <span className="text-gray-400 text-xs">{open ? '▲ 收起' : '▼ 展开'}</span>
      </button>
      {open && (
        <div className="bg-[#fafaf8] border border-[#e8e8e3] border-t-0 rounded-b-xl p-0">
          <SentimentPanel companyName={name} />
        </div>
      )}
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
        className="w-full flex items-center justify-between bg-white border border-[#e8e8e3] rounded-xl px-4 py-3 hover:border-[#ccc] transition-colors text-left"
      >
        <span className="text-sm font-medium text-[#555]">{title}</span>
        <span className="text-gray-400 text-xs">{open ? '▲ 收起' : '▼ 展开'}</span>
      </button>
      {open && (
        <div className="bg-[#fafaf8] border border-[#e8e8e3] border-t-0 rounded-b-xl px-4 py-3">
          {error ? (
            <p className="text-xs text-gray-400">暂无数据</p>
          ) : !data ? (
            <p className="text-xs text-gray-400">加载中…</p>
          ) : (
            render(data)
          )}
        </div>
      )}
    </div>
  );
}

// ---- helpers ----

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-white border border-[#e8e8e3] rounded-xl p-3 text-center">
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
