import { useState, useEffect, useCallback } from 'react';
import { api } from '../api';

interface CompanySnap {
  name: string;
  score: number;
  level: string;
}

export default function RiskMatrix() {
  const [companies, setCompanies] = useState<CompanySnap[]>([]);
  const [tooltip, setTooltip] = useState<{ name: string; score: number; level: string; x: number; y: number } | null>(null);

  const load = useCallback(() => {
    api.get<{ companies: CompanySnap[] }>('/alert/dashboard').then(d => {
      const filtered = d.companies.filter(c => c.score !== null);
      setCompanies(filtered);
    });
  }, []);

  useEffect(() => { load(); }, [load]);

  // Map score to y (0-100 → 100-0, so high risk is at top)
  const toY = (score: number) => 100 - score;
  // For impact, use random spread for now; later can use registered capital
  const toX = (name: string) => {
    let hash = 0;
    for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) % 100;
    return hash;
  };

  const quadrants = [
    { x: 50, y: 0, w: 50, h: 50, label: '重点监控', color: '#fef2f2', border: '#fca5a5', desc: '高风险 + 高影响' },
    { x: 0, y: 0, w: 50, h: 50, label: '定期评估', color: '#fffbeb', border: '#fcd34d', desc: '高风险 + 低影响' },
    { x: 50, y: 50, w: 50, h: 50, label: '持续跟踪', color: '#ecfdf5', border: '#6ee7b7', desc: '低风险 + 高影响' },
    { x: 0, y: 50, w: 50, h: 50, label: '低优先级', color: '#f8fafc', border: '#e2e8f0', desc: '低风险 + 低影响' },
  ];

  return (
    <div className="max-w-4xl mx-auto py-6 px-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-[#333]">风险矩阵</h2>
        <button onClick={load} className="text-xs text-gray-400 hover:text-gray-600">刷新</button>
      </div>

      <div className="bg-white border border-[#e8e8e3] rounded-2xl p-6">
        <div className="flex items-center justify-between mb-2 text-xs text-gray-400">
          <span>← 低</span>
          <span>影响程度 →</span>
          <span>高 →</span>
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
            <text x="2" y="20" fontSize="2.5" fill="#94a3b8" transform="rotate(-90, 2, 50)">高风险 ← 风险程度 → 低风险</text>
          </svg>

          {/* company dots */}
          {companies.map(c => {
            const x = toX(c.name);
            const y = toY(c.score);
            const color = c.score <= 30 ? '#059669' : c.score <= 60 ? '#d97706' : '#dc2626';
            return (
              <div
                key={c.name}
                className="absolute w-3 h-3 rounded-full border-2 border-white shadow-sm cursor-pointer hover:scale-150 transition-transform z-10"
                style={{
                  left: `${x}%`,
                  top: `${y}%`,
                  background: color,
                  transform: 'translate(-50%, -50%)',
                }}
                onMouseEnter={() => setTooltip({ name: c.name, score: c.score, level: c.level, x, y })}
                onMouseLeave={() => setTooltip(null)}
              />
            );
          })}
        </div>

        {/* tooltip */}
        {tooltip && (
          <div className="text-center mt-2 text-sm">
            <span className="font-medium">{tooltip.name}</span>
            <span className="text-gray-400 mx-2">|</span>
            <span className="font-semibold">{tooltip.score}/100</span>
            <span className="text-gray-400 ml-1">{tooltip.level}</span>
          </div>
        )}

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

      {companies.length === 0 && (
        <p className="text-gray-300 text-center mt-16">暂无评估数据，请先评估监控清单中的企业</p>
      )}
    </div>
  );
}
