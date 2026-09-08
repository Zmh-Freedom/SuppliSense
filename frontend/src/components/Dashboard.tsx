import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts';
import SentimentPanel from './SentimentPanel';
import { SkeletonCard, SkeletonChart } from './Skeleton';
import { getRiskColor, getRiskBg } from '../riskColors';
import { useDashboard, useWatchlist } from '../hooks';
import { queryKeys } from '../query-keys';
import type { Prediction } from '../types';

export default function Dashboard() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const dashQuery = useDashboard();
  const { companies: watchlist } = useWatchlist();
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
  const isLoading = dashQuery.isLoading;
  const isRefreshing = dashQuery.isFetching && !dashQuery.isLoading;
  const error = dashQuery.error;

  // Watchlist management
  const [newName, setNewName] = useState('');
  const [toast, setToast] = useState('');
  const [watchlistOpen, setWatchlistOpen] = useState(false);

  const addMutation = useMutation({
    mutationFn: (name: string) => api.post('/alert/watch', { company_name: name }),
    onSuccess: () => {
      setNewName('');
      queryClient.invalidateQueries({ queryKey: queryKeys.watchlist });
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
    onError: () => setToast('添加失败，请重试'),
  });

  const removeMutation = useMutation({
    mutationFn: (name: string) => api.delete('/alert/watch', { company_name: name }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.watchlist });
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
    onError: () => setToast('移除失败，请重试'),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.upload('/alert/watch/upload', file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.watchlist });
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
    onError: () => setToast('导入失败，请重试'),
  });

  const checkAllMutation = useMutation({
    mutationFn: () => api.post('/alert/check-all'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.watchlist });
      queryClient.invalidateQueries({ queryKey: queryKeys.alertHistory });
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
    onError: () => setToast('巡检失败，请重试'),
  });

  const busy = checkAllMutation.isPending;

  const add = () => {
    const name = newName.trim();
    if (!name || addMutation.isPending) return;
    addMutation.mutate(name);
  };

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    uploadMutation.mutate(file);
  };

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
        <SummaryCard label="监控企业" value={data.total} color="#333" />
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
              {l.key} {data.distribution[l.key] || 0} 家
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

      {/* watchlist management */}
      <section className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl shadow-sm">
        <button type="button" onClick={() => setWatchlistOpen(open => !open)} aria-expanded={watchlistOpen} className="flex w-full items-center justify-between gap-3 p-5 text-left">
          <span><span className="text-sm font-medium text-[var(--color-text-secondary)]">监控清单管理</span><span className="ml-2 text-xs text-gray-400">{watchlist.length} 家企业</span></span>
          <span aria-hidden="true" className={`text-gray-400 transition-transform ${watchlistOpen ? 'rotate-180' : ''}`}>⌄</span>
        </button>
        {watchlistOpen && <div className="border-t border-[var(--color-border)] p-5">

        {/* add form */}
        <form onSubmit={e => { e.preventDefault(); add(); }} className="flex gap-2 mb-3">
          <input
            value={newName}
            onChange={e => setNewName(e.target.value)}
            placeholder="添加企业名称…"
            className="flex-1 border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-border-focus)] bg-[var(--color-input-bg)] placeholder-gray-300 min-h-[44px]"
          />
          <button
            type="submit"
            disabled={addMutation.isPending || !newName.trim()}
            className="bg-[var(--color-primary-bg)] text-white rounded-lg px-4 py-2 text-sm hover:bg-[var(--color-primary-hover)] disabled:opacity-30 transition-opacity shrink-0 min-h-[44px] inline-flex items-center"
          >
            {addMutation.isPending ? '...' : '添加'}
          </button>
        </form>

        {/* actions bar */}
        <div className="flex gap-2 mb-3">
          <label className="flex-1 flex items-center justify-center gap-1.5 text-xs text-gray-500 cursor-pointer hover:text-gray-600 transition-colors border border-dashed border-[var(--color-border)] rounded-lg py-2 min-h-[44px]">
            <span>Excel 批量导入</span>
            <input type="file" accept=".xlsx" onChange={handleFile} className="hidden" />
          </label>
          <button
            disabled={busy}
            onClick={() => checkAllMutation.mutate()}
            className="flex-1 border border-[var(--color-border)] bg-[var(--color-surface)] rounded-lg py-2 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] transition-colors disabled:opacity-50 min-h-[44px] inline-flex items-center justify-center"
          >
            {busy ? '巡检中…' : '免费巡检'}
          </button>
        </div>

        {/* company table */}
        {watchlist.length === 0 ? (
          <p className="text-sm text-gray-300 text-center py-4">暂无监控企业，在上方输入名称添加</p>
        ) : (
          <div className="space-y-1">
            {watchlist.map(name => {
              const company = data.companies.find(c => c.name === name);
              const score = company?.score;
              const level = company?.level || '未知';
              const levelColor = level === '未知' ? '#999' : getRiskColor(level === '高风险' ? 61 : level === '中风险' ? 31 : 0);
              return (
                <div key={name} className="flex items-center justify-between py-2.5 px-3 rounded-xl hover:bg-[var(--color-surface-hover)] transition-colors min-h-[44px]">
                  <button
                    className="text-sm text-[var(--color-text)] truncate flex-1 text-left hover:text-[var(--color-primary-bg)] transition-colors"
                    onClick={() => navigate(`/assess/${encodeURIComponent(name)}`)}
                  >
                    {name}
                  </button>
                  <div className="flex items-center gap-2 shrink-0">
                    {score != null ? (
                      <span className="text-xs font-semibold px-2 py-0.5 rounded-full" style={{ background: `${levelColor}15`, color: levelColor }}>
                        {level} {score}/100
                      </span>
                    ) : (
                      <span className="text-xs text-gray-400">未评估</span>
                    )}
                    <button
                      onClick={() => navigate(`/chat?q=${encodeURIComponent(`对${name}进行全面的风险评估`)}`)}
                      className="text-xs text-[var(--color-primary-bg)] hover:bg-[var(--color-primary-bg)]/10 rounded-md px-2 py-1 transition-colors"
                      title="Agent 分析"
                    >
                      分析
                    </button>
                    <button
                      onClick={() => removeMutation.mutate(name)}
                      className="text-gray-300 hover:text-red-400 text-sm transition-colors p-1"
                      title="移除"
                    >
                      ×
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
        </div>}
      </section>

      {toast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-red-50 border border-red-200 rounded-lg px-4 py-2 text-xs text-red-600 z-30 shadow-md">
          {toast}
          <button onClick={() => setToast('')} className="ml-2 text-red-400 hover:text-red-600">&times;</button>
        </div>
      )}

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
