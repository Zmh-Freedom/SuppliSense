import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../api';
import type { RiskResult } from '../types';
import SentimentPanel from './SentimentPanel';

const LEVEL_COLOR: Record<string, string> = {
  '高风险': '#dc2626', '中风险': '#d97706', '低风险': '#16a34a',
  '严重': '#dc2626', '中等': '#d97706', '轻微': '#16a34a',
  'critical': '#dc2626', 'high': '#dc2626', 'medium': '#d97706', 'low': '#16a34a',
};
const LEVEL_BG: Record<string, string> = {
  '高风险': '#fef2f2', '中风险': '#fffbf0', '低风险': '#ecfdf5',
};

export default function AssessView({ initialName = '' }: { initialName?: string }) {
  const [name, setName] = useState(initialName);
  const [data, setData] = useState<RiskResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const prevName = useRef(initialName);

  useEffect(() => {
    if (initialName && initialName !== prevName.current) {
      prevName.current = initialName;
      setName(initialName);
      runAssess(initialName);
    }
  }, [initialName]);

  const runAssess = async (target: string) => {
    if (!target.trim()) return;
    setLoading(true); setError('');
    try {
      const res = await api.post<RiskResult>('/risk/assess', { company_name: target.trim() });
      setData(res);
    } catch {
      setError('评估失败');
      setData(null);
    }
    setLoading(false);
  };

  const assess = () => runAssess(name);

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
              <div className="flex-1 bg-[#e5e5e0] h-2 rounded-full">
                <div className="h-full rounded-full transition-all duration-700" style={{ width: `${data.risk_score}%`, background: color }} />
              </div>
            </div>
          </div>

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
          <Expandable title="🌍 ESG 评分" endpoint={`/p2/esg/${encodeURIComponent(name)}`}
            render={(d: any) => (
              <div className="grid grid-cols-3 gap-3">
                {['environmental', 'social', 'governance'].map(dim => {
                  const dd = d[dim];
                  return (
                    <div key={dim} className="bg-white border border-[#e8e8e3] rounded-xl p-3">
                      <div className="text-xs text-gray-500 mb-1">{dim === 'environmental' ? 'E·环境' : dim === 'social' ? 'S·社会' : 'G·治理'}</div>
                      <div className="text-lg font-bold" style={{ color: LEVEL_COLOR[dd.level] }}>{dd.score.toFixed(0)}</div>
                      <div className="text-[11px]" style={{ color: LEVEL_COLOR[dd.level] }}>{dd.level}</div>
                      {dd.detail.map((item: any, i: number) => (
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

          {/* Macro */}
          <Expandable title="🌐 宏观风险" endpoint={`/analysis/macro/${encodeURIComponent(name)}`}
            render={(d: any) => (
              <div>
                <div className="flex items-center gap-3 mb-3">
                  <span className="text-2xl font-bold" style={{ color: LEVEL_COLOR[d.total_level] }}>{d.total_score}</span>
                  <span className="text-sm" style={{ color: LEVEL_COLOR[d.total_level] }}>{d.total_level}</span>
                  <span className="text-xs text-gray-400">行业{d.industry_risk?.risk_score || 0} + 地区{d.regional_risk?.score || 0} + 政策{d.policy_risks?.score || 0}</span>
                </div>
                {d.policy_risks?.tags?.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {d.policy_risks.tags.map((t: any, i: number) => (
                      <span key={i} className="text-[10px] px-2 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-100">{t.tag}</span>
                    ))}
                  </div>
                )}
              </div>
            )}
          />

          {/* Alternatives */}
          <Expandable title="🔀 替代建议" endpoint={`/analysis/alternatives/${encodeURIComponent(name)}`}
            render={(d: any) => (
              <div>
                {d.alternatives?.length === 0 ? (
                  <p className="text-xs text-gray-400">{d.source_risk_score < 60 ? '风险较低，暂不需替代' : '暂未找到替代'}</p>
                ) : (
                  <div className="grid grid-cols-2 gap-2">
                    {d.alternatives?.slice(0, 4).map((a: any, i: number) => (
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

          {/* Contagion */}
          <Expandable title="🔗 风险传染" endpoint={`/p2/contagion/${encodeURIComponent(name)}`}
            render={(d: any) => (
              <div>
                <div className="flex gap-4 mb-2 text-xs text-gray-500">
                  <span>关联方 {d.related_count}</span>
                  <span>分支 {d.branch_count}</span>
                  <span>依赖 {d.dependency_count}</span>
                  <span className="text-red-500">高风险 {d.high_risk_related_count}</span>
                </div>
                {d.related_entities?.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {d.related_entities.slice(0, 8).map((e: any, i: number) => (
                      <span key={i} className="text-[10px] px-2 py-0.5 rounded bg-gray-50 border border-gray-100 text-gray-600">
                        {e.name.slice(0, 15)} <span className="text-gray-400">({e.relation_type})</span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
          />

          {/* Scenario */}
          <Expandable title="🎯 情景模拟" endpoint={`/analysis/scenario/${encodeURIComponent(name)}?scenario=bankruptcy`}
            render={(d: any) => (
              <div>
                <div className="flex items-center gap-3 mb-2">
                  <span className="text-lg font-bold" style={{ color: LEVEL_COLOR[d.impact_level] }}>{d.impact_score} · {d.impact_level}</span>
                  <span className="text-xs text-gray-400">{d.scenario_desc}</span>
                </div>
                <div className="space-y-1">
                  {d.suggested_actions?.slice(0, 3).map((a: string, i: number) => (
                    <div key={i} className="text-xs text-gray-600">{a}</div>
                  ))}
                </div>
              </div>
            )}
          />

          {/* Sanctions */}
          <Expandable title="🛡️ 制裁筛查" endpoint={`/analysis/sanctions/${encodeURIComponent(name)}`}
            render={(d: any) => (
              <div>
                <span className={`text-sm font-bold ${d.clean ? 'text-green-600' : 'text-red-600'}`}>
                  {d.clean ? '✅ 未命中' : `⚠️ ${d.match_count}条命中`}
                </span>
                {d.matches?.length > 0 && (
                  <div className="mt-2 space-y-1">
                    {d.matches.map((m: any, i: number) => (
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

          {/* sentiment */}
          {data && <div className="mt-4"><SentimentPanel companyName={name} /></div>}
        </>
      )}
    </div>
  );
}

// ---- Expandable section ----

function Expandable({ title, endpoint, render }: { title: string; endpoint: string; render: (d: any) => React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<any>(null);

  const toggle = async () => {
    if (open) { setOpen(false); return; }
    setOpen(true);
    if (!data) {
      try {
        const d = await api.get<any>(endpoint);
        setData(d);
      } catch { setData({ error: true }); }
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
          {!data ? (
            <p className="text-xs text-gray-400">加载中…</p>
          ) : data.error ? (
            <p className="text-xs text-gray-400">暂无数据</p>
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
