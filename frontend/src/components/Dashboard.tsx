import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts';
import SentimentPanel from './SentimentPanel';
import RiskMatrix from './RiskMatrix';
import { SkeletonCard, SkeletonChart } from './Skeleton';
import { useDashboard } from '../hooks';
import { queryKeys } from '../query-keys';
import type { Prediction } from '../types';

export default function Dashboard() {
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
    { key: '高风险', color: '#e06060', bg: '#fef5f5' },
    { key: '中风险', color: '#d4a040', bg: '#fffbf0' },
    { key: '低风险', color: '#2d8c63', bg: '#ecfdf5' },
    { key: '未知', color: '#999', bg: '#f5f5f5' },
  ];

  return (
    <div className="max-w-4xl mx-auto py-6 px-4 space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-[#333]">风险看板</h2>
        <button onClick={() => dashQuery.refetch()} disabled={isRefreshing} className="text-xs text-gray-400 hover:text-gray-600 disabled:opacity-50 min-h-[36px] px-2 inline-flex items-center">{isRefreshing ? '刷新中…' : '刷新'}</button>
      </div>

      {/* summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <SummaryCard label="监控企业" value={data.total} color="#333" />
        <SummaryCard label="告警" value={data.alert_count} color="#e06060" />
        <SummaryCard label="高风险" value={data.distribution['高风险'] || 0} color="#e06060" />
        <SummaryCard label="低风险" value={data.distribution['低风险'] || 0} color="#2d8c63" />
      </div>

      {/* Trend Charts */}
      {alertTrend.length > 0 && (
        <div className="bg-white border border-[#e8e8e3] rounded-2xl p-5 shadow-sm">
          <h3 className="text-sm font-semibold text-[#333] mb-1">近30天告警趋势</h3>
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
      <div className="bg-white border border-[#e8e8e3] rounded-2xl p-5 shadow-sm">
        <h3 className="text-sm font-medium text-[#555] mb-4">风险分布</h3>
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
        <div className="bg-white border border-[#e8e8e3] rounded-2xl p-5 mb-4 shadow-sm">
          <h3 className="text-sm font-medium text-[#555] mb-3">
            ⚡ 早期预警信号
            <span className="text-xs text-gray-400 ml-2">基于趋势分析，预测未来风险恶化概率</span>
          </h3>
          <div className="space-y-2">
            {predictions.filter(p => p.probability !== 'low').slice(0, 6).map(p => {
              const color = p.probability === 'high' ? '#dc2626' : '#d97706';
              const bg = p.probability === 'high' ? '#fef2f2' : '#fffbf0';
              return (
                <div key={p.company_name} className="flex items-center gap-3 px-3 py-2 rounded-xl" style={{ background: bg }}>
                  <span className="text-sm font-semibold text-[#333] flex-1 truncate">{p.company_name}</span>
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

      {/* company list */}
      <div className="bg-white border border-[#e8e8e3] rounded-2xl p-5 shadow-sm">
        <h3 className="text-sm font-medium text-[#555] mb-3">企业详情</h3>
        {data.companies.length === 0 ? (
          <p className="text-sm text-gray-300 text-center py-6">暂无监控企业</p>
        ) : (
          <div className="space-y-1">
            {data.companies.map(c => {
              const levelInfo = levels.find(l => l.key === c.level) || levels[3];
              return (
                <div key={c.name} className="flex items-center justify-between py-2.5 px-3 rounded-xl hover:bg-[#f9f9f5] transition-colors min-h-[44px]">
                  <span className="text-sm text-[#333] truncate flex-1">{c.name}</span>
                  {c.score !== null ? (
                    <div className="flex items-center gap-3 shrink-0">
                      <span className="text-xs font-semibold px-2 py-0.5 rounded-full" style={{ background: levelInfo.bg, color: levelInfo.color }}>
                        {c.level} {c.score}/100
                      </span>
                      <span className="text-[11px] text-gray-400">
                        {c.last_checked?.slice(0, 16).replace('T', ' ') || ''}
                      </span>
                    </div>
                  ) : (
                    <span className="text-xs text-gray-400">未评估</span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* risk matrix */}
      <RiskMatrix />
    </div>
  );
}

function SummaryCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="bg-white border border-[#e8e8e3] rounded-2xl p-4 shadow-sm">
      <div className="text-2xl font-bold" style={{ color }}>{value}</div>
      <div className="text-xs text-gray-400 mt-1">{label}</div>
    </div>
  );
}
