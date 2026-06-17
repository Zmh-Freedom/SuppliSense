import { useState } from 'react';
import { useDashboard } from '../hooks';
import Skeleton from './Skeleton';

interface CompanySnap {
  name: string;
  score: number;
  level: string;
  alert_count: number;
}

export default function RiskMatrix() {
  const { data, isLoading, error, refetch } = useDashboard();
  const [tooltip, setTooltip] = useState<{ name: string; score: number; level: string; x: number; y: number; mouseX: number; mouseY: number } | null>(null);

  const companies = (data?.companies ?? []).filter(c => c.score !== null) as CompanySnap[];

  // Compute max alert count for scaling
  const maxAlerts = companies.length > 0 ? Math.max(...companies.map(c => c.alert_count ?? 0), 1) : 1;
  // x = risk_score (0-100)
  const toX = (score: number) => score;
  // y = alert_count mapped to 100-0 (high alerts at top)
  const toY = (count: number) => 100 - (count / maxAlerts) * 100;

  const quadrants = [
    { x: 50, y: 0, w: 50, h: 50, label: '高风险 高告警', color: '#fef2f2', border: '#fca5a5' },
    { x: 0, y: 0, w: 50, h: 50, label: '低风险 高告警', color: '#fffbeb', border: '#fcd34d' },
    { x: 50, y: 50, w: 50, h: 50, label: '高风险 低告警', color: '#ecfdf5', border: '#6ee7b7' },
    { x: 0, y: 50, w: 50, h: 50, label: '低风险 低告警', color: '#f8fafc', border: '#e2e8f0' },
  ];

  return (
    <div className="max-w-4xl mx-auto py-6 px-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-[var(--color-text)]">风险矩阵</h2>
        <button onClick={() => refetch()} disabled={isLoading} className="text-xs text-gray-400 hover:text-gray-600 disabled:opacity-50">{isLoading ? '刷新中…' : '刷新'}</button>
      </div>

      {error ? (
        <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-10 text-center">
          <p className="text-gray-400 mb-3">加载失败</p>
          <button onClick={() => refetch()} className="text-sm text-blue-500 hover:text-blue-600">重试</button>
        </div>
      ) : isLoading ? (
        <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-6 shadow-sm">
          <Skeleton className="h-4 w-1/3 mb-4" />
          <Skeleton className="aspect-square w-full rounded-xl" />
        </div>
      ) : (
        <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-6 shadow-sm">
          <div className="flex items-center justify-between mb-2 text-xs text-gray-400">
            <span>← 低风险</span>
            <span>风险评分 →</span>
            <span>高风险 →</span>
          </div>

          {/* matrix grid */}
          <div className="relative w-full aspect-square max-w-lg mx-auto" style={{ minHeight: 360 }}>
            {/* quadrants */}
            <svg className="absolute inset-0 w-full h-full" viewBox="0 0 100 100" preserveAspectRatio="none">
              {quadrants.map(q => (
                <g key={q.label}>
                  <rect x={q.x} y={q.y} width={q.w} height={q.h} fill={q.color} stroke={q.border} strokeWidth="0.5" />
                  <text x={q.x + q.w / 2} y={q.y + q.h / 2 + 1} textAnchor="middle" fontSize="3" fill="#94a3b8">
                    {q.label}
                  </text>
                </g>
              ))}
              {/* risk axis labels */}
              <text x="2" y="20" fontSize="2.5" fill="#94a3b8" transform="rotate(-90, 2, 50)">高告警 ← 告警次数 → 低告警</text>
            </svg>

            {/* company dots */}
            {companies.map(c => {
              const x = toX(c.score);
              const y = toY(c.alert_count ?? 0);
              const color = c.score <= 30 ? '#059669' : c.score <= 60 ? '#d97706' : '#dc2626';
              const sizeScale = maxAlerts > 0 ? (c.alert_count ?? 0) / maxAlerts : 0;
              const size = Math.max(8, Math.min(20, 6 + sizeScale * 14));
              return (
                <div
                  key={c.name}
                  className="absolute rounded-full border-2 border-white shadow-sm cursor-pointer hover:scale-150 transition-transform z-10"
                  style={{
                    left: `${x}%`,
                    top: `${y}%`,
                    background: color,
                    transform: 'translate(-50%, -50%)',
                    width: `${size}px`,
                    height: `${size}px`,
                  }}
                  onMouseEnter={(e) => {
                    const rect = e.currentTarget.parentElement!.getBoundingClientRect();
                    setTooltip({
                      name: c.name,
                      score: c.score,
                      level: c.level,
                      x, y,
                      mouseX: e.clientX - rect.left,
                      mouseY: e.clientY - rect.top,
                    });
                  }}
                  onMouseLeave={() => setTooltip(null)}
                />
              );
            })}

            {/* tooltip */}
            {tooltip && (
              <div
                className="absolute z-20 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg shadow-lg px-3 py-2 pointer-events-none"
                style={{
                  left: `${tooltip.mouseX}px`,
                  top: `${tooltip.mouseY - 8}px`,
                  transform: 'translate(-50%, -100%)',
                }}
              >
                <div className="text-sm font-medium text-[var(--color-text)]">{tooltip.name}</div>
                <div className="text-xs text-gray-500">
                  <span className="font-semibold">{tooltip.score}/100</span>
                  <span className="ml-1">{tooltip.level}</span>
                </div>
              </div>
            )}
          </div>

          {/* legend */}
          <div className="flex justify-center gap-4 mt-4 text-xs text-gray-500">
            {quadrants.map(q => (
              <span key={q.label} className="flex items-center gap-1">
                <span className="w-3 h-3 rounded-sm" style={{ background: q.color, border: `1px solid ${q.border}` }} />
                {q.label}
              </span>
            ))}
          </div>
        </div>
      )}

      {!isLoading && !error && companies.length === 0 && (
        <p className="text-gray-300 text-center mt-16">暂无评估数据，请先评估监控清单中的企业</p>
      )}
    </div>
  );
}
