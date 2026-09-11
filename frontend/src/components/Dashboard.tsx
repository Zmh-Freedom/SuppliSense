import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts';
import SentimentPanel from './SentimentPanel';
import { SkeletonCard, SkeletonChart } from './Skeleton';
import { getRiskColor, getRiskBg } from '../riskColors';
import { useDashboard } from '../hooks';
import { queryKeys } from '../query-keys';
import type { Prediction } from '../types';

export default function Dashboard() {
  const navigate = useNavigate();
  const dashQuery = useDashboard();
  const predQuery = useQuery({
    queryKey: queryKeys.predictions,
    queryFn: () => api.get<Prediction[]>('/alert/predict'),
  });
  const trendQuery = useQuery({
    queryKey: queryKeys.alertTrend(30),
    queryFn: () => api.get<{data: {date: string; count: number}[]}>('/trend/alert?days=30'),
  });

  const data = dashQuery.data;
  const predictions = predQuery.data ?? [];
  const alertTrend = trendQuery.data?.data ?? [];
  const rankedTargets = [...(data?.targets ?? [])]
    .filter(target => target.monitor_status !== 'removed')
    .sort((a, b) => {
      if (a.risk_score == null && b.risk_score == null) return 0;
      if (a.risk_score == null) return 1;
      if (b.risk_score == null) return -1;
      return b.risk_score - a.risk_score;
    })
    .slice(0, 5);
  const isLoading = dashQuery.isLoading;
  const isRefreshing = dashQuery.isFetching && !dashQuery.isLoading;
  const error = dashQuery.error;

  if (error) {
    return (
      <div className="max-w-2xl mx-auto py-20 text-center">
        <p className="text-gray-400 mb-4">加载失败，请检查后端服务</p>
        <button onClick={() => dashQuery.refetch()} disabled={isRefreshing} className="text-sm text-blue-500 hover:text-blue-600 disabled:opacity-50">重试</button>
      </div>
    );
  }

  if (isLoading || !data) return (
    <div className="max-w-4xl mx-auto py-6 px-4 space-y-6">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <SkeletonChart />
        <SkeletonChart />
      </div>
      <SkeletonChart />
    </div>
  );

  const levels = [
    { key: '高风险', color: getRiskColor(61), bg: getRiskBg(61) },
    { key: '中风险', color: getRiskColor(31), bg: getRiskBg(31) },
    { key: '低风险', color: getRiskColor(0), bg: getRiskBg(0) },
    { key: '未知', color: '#999', bg: '#f5f5f5' },
  ];

  return (
    <div className="max-w-4xl mx-auto py-6 px-4 space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-[var(--color-text)]">风险看板</h2>
        <button onClick={() => dashQuery.refetch()} disabled={isRefreshing} className="text-xs text-gray-400 hover:text-gray-600 disabled:opacity-50 min-h-[36px] px-2 inline-flex items-center">{isRefreshing ? '刷新中…' : '刷新'}</button>
      </div>

      {/* agent suggestions */}
      <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-4 shadow-sm">
        <div className="flex items-center gap-2 mb-3">
          <svg className="w-4 h-4 text-[var(--color-primary-bg)]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2l2.4 7.2h7.6l-6 4.8 2.4 7.2-6.4-4.8-6.4 4.8 2.4-7.2-6-4.8h7.6z"/></svg>
          <span className="text-sm font-medium text-[var(--color-text)]">Agent 建议</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {[
            { label: '复核上市供应商案例', q: '复核青岛三祥科技股份有限公司' },
            { label: '复核非上市供应商案例', q: '复核上海汽车制动系统有限公司' },
          ].map(item => (
            <button
              key={item.label}
              onClick={() => navigate(`/chat?q=${encodeURIComponent(item.q)}`)}
              className="text-left text-sm text-gray-600 bg-white border border-slate-200 rounded-xl px-4 py-2.5 hover:border-[var(--color-primary-bg)]/30 hover:shadow-sm hover:-translate-y-0.5 transition-all duration-200"
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {/* summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <SummaryCard label="监控对象" value={data.total} color="#333" />
        <SummaryCard label="告警" value={data.alert_count} color={getRiskColor(61)} />
        <SummaryCard label="高风险" value={data.distribution['高风险'] || 0} color={getRiskColor(61)} />
        <SummaryCard label="低风险" value={data.distribution['低风险'] || 0} color={getRiskColor(0)} />
      </div>

      {/* Trend Charts */}
      {alertTrend.length > 0 && (
        <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
          <h3 className="text-sm font-semibold text-[var(--color-text)] mb-1">近30天告警趋势</h3>
          <p className="text-[11px] text-gray-400 mb-4">
            共 {alertTrend.reduce((s, d) => s + d.count, 0)} 次告警 · 日均 {(alertTrend.reduce((s, d) => s + d.count, 0) / alertTrend.length).toFixed(1)} 次
          </p>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={alertTrend} barCategoryGap="30%">
              <defs>
                <linearGradient id="alertBarGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#ef4444" stopOpacity={0.9} />
                  <stop offset="100%" stopColor="#fca5a5" stopOpacity={0.3} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#f5f5f5" vertical={false} />
              <XAxis dataKey="date" tick={{fontSize: 10, fill: '#999'}} axisLine={{stroke: '#eee'}} tickLine={false} />
              <YAxis allowDecimals={false} tick={{fontSize: 10, fill: '#999'}} axisLine={false} tickLine={false} width={24} />
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
                formatter={(value) => [`${value} 次告警`, '']}
              />
              <ReferenceLine
                y={Math.ceil(alertTrend.reduce((s, d) => s + d.count, 0) / alertTrend.length)}
                stroke="#e5e7eb" strokeDasharray="4 4" strokeWidth={1}
                label={{value: '均值', position: 'right', fontSize: 10, fill: '#bbb'}}
              />
              <Bar dataKey="count" radius={[6, 6, 0, 0]} maxBarSize={32}>
                {alertTrend.map((entry, i) => (
                  <Cell key={i} fill={entry.count >= 3 ? '#ef4444' : entry.count >= 1 ? '#f59e0b' : '#d1d5db'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* risk distribution bar */}
      <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
        <h3 className="text-sm font-medium text-[var(--color-text-secondary)] mb-4">风险分布</h3>
        <div className="flex h-8 rounded-full overflow-hidden">
          {levels.filter(l => l.key !== '未知').map(l => {
            const count = data.distribution[l.key] || 0;
            const pct = data.total > 0 ? (count / data.total) * 100 : 0;
            return pct > 0 ? (
              <div
                key={l.key}
                style={{ width: `${pct}%`, background: l.color }}
                className="flex items-center justify-center text-xs text-white font-medium transition-all"
                title={`${l.key}: ${count}`}
              >
                {pct > 15 ? count : ''}
              </div>
            ) : null;
          })}
        </div>
        <div className="flex gap-4 mt-3 text-xs text-gray-500">
          {levels.filter(l => l.key !== '未知').map(l => (
            <span key={l.key} className="flex items-center gap-1">
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: l.color }} />
              {l.key} {data.distribution[l.key] || 0} 个
            </span>
          ))}
        </div>
      </div>

      {/* predictions */}
      {predictions.filter(p => p.probability !== 'low').length > 0 && (
        <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 mb-4 shadow-sm">
          <h3 className="text-sm font-medium text-[var(--color-text-secondary)] mb-3">
            ⚡ 早期预警信号
            <span className="text-xs text-gray-400 ml-2">基于趋势分析，预测未来风险恶化概率</span>
          </h3>
          <div className="space-y-2">
            {predictions.filter(p => p.probability !== 'low').slice(0, 6).map(p => {
              const color = p.probability === 'high' ? '#dc2626' : '#d97706';
              const bg = p.probability === 'high' ? '#fef2f2' : '#fffbf0';
              return (
                <div key={p.company_name} className="flex items-center gap-3 px-3 py-2 rounded-xl" style={{ background: bg }}>
                  <span className="text-sm font-semibold text-[var(--color-text)] flex-1 truncate">{p.company_name}</span>
                  <span className="text-xs font-medium px-2 py-0.5 rounded-full" style={{ color, background: `${color}15` }}>
                    {p.label}
                  </span>
                  <span className="text-[11px] text-gray-400">
                    {p.signals?.slice(0, 2).map(s => s.signal).join(' · ')}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* sentiment overview */}
      <SentimentPanel />

      <section className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h3 className="text-sm font-semibold text-[var(--color-text)]">风险排名</h3>
            <p className="mt-1 text-xs text-[var(--color-text-secondary)]">按当前风险分数从高到低，优先查看排名靠前的供应商。</p>
          </div>
          <button type="button" onClick={() => navigate('/assess')} className="shrink-0 rounded-lg bg-[var(--color-primary-bg)] px-3 py-2 text-xs text-white hover:bg-[var(--color-primary-hover)]">查看全部</button>
        </div>
        {rankedTargets.length === 0 ? (
          <div className="mt-4 rounded-xl border border-dashed border-[var(--color-border)] px-4 py-8 text-center text-xs text-[var(--color-text-secondary)]">
            暂无监控对象，风险排名会在建立监控对象后显示。
          </div>
        ) : (
          <div className="mt-4 overflow-x-auto rounded-xl border border-[var(--color-border)]">
            <table className="w-full min-w-[700px] border-collapse text-left text-xs">
              <thead className="bg-[var(--color-background)] text-[11px] text-[var(--color-text-secondary)]">
                <tr>
                  <th className="w-14 px-3 py-3 font-medium">排名</th>
                  <th className="px-3 py-3 font-medium">供应商</th>
                  <th className="px-3 py-3 font-medium">当前风险</th>
                  <th className="px-3 py-3 font-medium">风险变化</th>
                  <th className="px-3 py-3 font-medium">数据覆盖</th>
                  <th className="px-3 py-3 font-medium">下一步</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {rankedTargets.map((target, index) => {
                  const score = target.risk_score;
                  const riskColor = score == null ? '#737373' : getRiskColor(score);
                  const riskBg = score == null ? '#f5f5f4' : getRiskBg(score);
                  const trendDelta = target.risk_change?.delta;
                  const trendText = target.risk_change?.label || '暂无数据';
                  return (
                    <tr key={target.monitor_target_id} className="align-middle hover:bg-[var(--color-surface-hover)]">
                      <td className="px-3 py-3">
                        <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-[var(--color-background)] font-semibold text-[var(--color-text-secondary)]">{index + 1}</span>
                      </td>
                      <td className="max-w-[240px] px-3 py-3">
                        <button type="button" onClick={() => navigate(`/assess/${encodeURIComponent(target.monitor_target_id)}`)} className="block max-w-full truncate text-left font-medium text-[var(--color-text)] hover:text-[var(--color-primary-bg)]">
                          {target.display_name || target.company_name}
                        </button>
                        {target.supplier_code && <span className="mt-1 block text-[10px] text-gray-400">{target.supplier_code}</span>}
                      </td>
                      <td className="px-3 py-3">
                        <span className="inline-flex rounded-full px-2 py-1 text-[11px] font-medium" style={{ color: riskColor, background: riskBg }}>
                          {score == null ? '暂无快照' : `${target.risk_level || '未知'} ${score}/100`}
                        </span>
                      </td>
                      <td className="px-3 py-3 text-[var(--color-text-secondary)]">
                        {trendText}{trendDelta != null ? ` ${trendDelta > 0 ? '+' : ''}${trendDelta}` : ''}
                      </td>
                      <td className="max-w-[150px] px-3 py-3 text-[var(--color-text-secondary)]">
                        <span className="block truncate">{(target.data_coverage as { summary?: string } | undefined)?.summary || '覆盖情况未知'}</span>
                      </td>
                      <td className="max-w-[160px] px-3 py-3">
                        <span className={`block truncate font-medium ${target.next_action?.priority === 'high' ? 'text-red-700' : target.next_action?.priority === 'medium' ? 'text-amber-700' : 'text-emerald-700'}`}>
                          {target.next_action?.label || '继续观察'}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

    </div>
  );
}

function SummaryCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-4 shadow-sm">
      <div className="text-2xl font-bold" style={{ color }}>{value}</div>
      <div className="text-xs text-gray-400 mt-1">{label}</div>
    </div>
  );
}
