import { useState, useEffect, useCallback } from 'react';
import { api } from '../api';

interface CompanySnap {
  name: string;
  score: number | null;
  level: string;
  last_checked: string | null;
}

interface DashboardData {
  total: number;
  distribution: Record<string, number>;
  companies: CompanySnap[];
  alert_count: number;
}

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [predictions, setPredictions] = useState<any[]>([]);

  const load = useCallback(() => {
    api.get<DashboardData>('/alert/dashboard').then(setData);
    api.get<any[]>('/alert/predict').then(p => setPredictions(p || []));
  }, []);

  useEffect(() => { load(); }, [load]);

  if (!data) return <div className="p-6 text-gray-300">加载中…</div>;

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
        <button onClick={load} className="text-xs text-gray-400 hover:text-gray-600">刷新</button>
      </div>

      {/* summary cards */}
      <div className="grid grid-cols-4 gap-3">
        <SummaryCard label="监控企业" value={data.total} color="#333" />
        <SummaryCard label="告警" value={data.alert_count} color="#e06060" />
        <SummaryCard label="高风险" value={data.distribution['高风险'] || 0} color="#e06060" />
        <SummaryCard label="低风险" value={data.distribution['低风险'] || 0} color="#2d8c63" />
      </div>

      {/* risk distribution bar */}
      <div className="bg-white border border-[#e8e8e3] rounded-2xl p-5">
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
        <div className="bg-white border border-[#e8e8e3] rounded-2xl p-5 mb-4">
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
                    {p.signals?.slice(0, 2).map((s: any) => s.signal).join(' · ')}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* company list */}
      <div className="bg-white border border-[#e8e8e3] rounded-2xl p-5">
        <h3 className="text-sm font-medium text-[#555] mb-3">企业详情</h3>
        {data.companies.length === 0 ? (
          <p className="text-sm text-gray-300 text-center py-6">暂无监控企业</p>
        ) : (
          <div className="space-y-1">
            {data.companies.map(c => {
              const levelInfo = levels.find(l => l.key === c.level) || levels[3];
              return (
                <div key={c.name} className="flex items-center justify-between py-2 px-3 rounded-xl hover:bg-[#f9f9f5] transition-colors">
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
    </div>
  );
}

function SummaryCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="bg-white border border-[#e8e8e3] rounded-2xl p-4">
      <div className="text-2xl font-bold" style={{ color }}>{value}</div>
      <div className="text-xs text-gray-400 mt-1">{label}</div>
    </div>
  );
}
