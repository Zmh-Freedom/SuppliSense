import type { ChartData } from '../types';

const CHART_COLORS = ['#6366f1', '#f59e0b', '#10b981', '#ef4444', '#8b5cf6', '#ec4899'];

function GaugeChart({ data }: { data: ChartData }) {
  const item = data.data[0] as Record<string, unknown> | undefined;
  if (!item) return null;
  const value = Number(item.value ?? 0);
  const max = Number(item.max ?? 100);
  const label = String(item.label ?? '');
  const pct = Math.min(value / max, 1);

  // 颜色: 绿(0-30) → 黄(30-60) → 红(60-100)
  const color = value <= 30 ? '#10b981' : value <= 60 ? '#f59e0b' : '#ef4444';

  return (
    <div className="flex flex-col items-center py-2">
      <svg viewBox="0 0 120 70" className="w-32">
        {/* 背景弧 */}
        <path d="M 10 60 A 50 50 0 0 1 110 60" fill="none" stroke="#e5e7eb" strokeWidth="8" strokeLinecap="round" />
        {/* 值弧 */}
        <path
          d="M 10 60 A 50 50 0 0 1 110 60"
          fill="none"
          stroke={color}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={`${pct * 157} 157`}
        />
        {/* 数值 */}
        <text x="60" y="52" textAnchor="middle" className="text-lg" fill={color} fontSize="20" fontWeight="bold">
          {value}
        </text>
      </svg>
      {label && <span className="text-xs mt-1" style={{ color }}>{label}</span>}
    </div>
  );
}

function BarChartSimple({ data }: { data: ChartData }) {
  const items = data.data as Array<{ name: string; value: number }>;
  if (!items.length) return null;
  const maxVal = Math.max(...items.map(d => Math.abs(Number(d.value ?? 0))), 1);

  return (
    <div className="space-y-2 py-2">
      {items.map((item, i) => (
        <div key={i} className="flex items-center gap-2 text-xs">
          <span className="w-20 text-right text-gray-500 truncate shrink-0">{item.name}</span>
          <div className="flex-1 bg-gray-100 rounded-full h-5 overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                width: `${Math.abs(Number(item.value ?? 0)) / maxVal * 100}%`,
                backgroundColor: CHART_COLORS[i % CHART_COLORS.length],
              }}
            />
          </div>
          <span className="w-12 text-gray-600 shrink-0">{item.value}</span>
        </div>
      ))}
    </div>
  );
}

function LineChartSimple({ data }: { data: ChartData }) {
  const items = data.data as Array<{ date: string; value: number }>;
  if (!items.length) return null;
  const values = items.map(d => Number(d.value ?? 0));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const points = items.map((d, i) => {
    const x = (i / (items.length - 1 || 1)) * 200 + 10;
    const y = 60 - ((Number(d.value ?? 0) - min) / range) * 50;
    return `${x},${y}`;
  }).join(' ');

  return (
    <div className="py-2">
      <svg viewBox="0 0 220 75" className="w-full">
        <polyline fill="none" stroke="#6366f1" strokeWidth="2" points={points} />
        {items.map((d, i) => {
          const x = (i / (items.length - 1 || 1)) * 200 + 10;
          const y = 60 - ((Number(d.value ?? 0) - min) / range) * 50;
          return <circle key={i} cx={x} cy={y} r="3" fill="#6366f1" />;
        })}
        {/* X 轴标签（首尾） */}
        {items.length > 1 && (
          <>
            <text x="10" y="72" fontSize="7" fill="#9ca3af">{items[0].date}</text>
            <text x="180" y="72" fontSize="7" fill="#9ca3af">{items[items.length - 1].date}</text>
          </>
        )}
      </svg>
    </div>
  );
}

function PieChartSimple({ data }: { data: ChartData }) {
  const items = data.data as Array<{ name: string; value: number }>;
  if (!items.length) return null;
  const total = items.reduce((s, d) => s + Number(d.value ?? 0), 0) || 1;

  let cumAngle = -Math.PI / 2;
  const slices = items.map((item, i) => {
    const angle = (Number(item.value ?? 0) / total) * Math.PI * 2;
    const startAngle = cumAngle;
    cumAngle += angle;
    const endAngle = cumAngle;

    const x1 = 40 + 30 * Math.cos(startAngle);
    const y1 = 40 + 30 * Math.sin(startAngle);
    const x2 = 40 + 30 * Math.cos(endAngle);
    const y2 = 40 + 30 * Math.sin(endAngle);
    const largeArc = angle > Math.PI ? 1 : 0;

    return (
      <path
        key={i}
        d={`M 40 40 L ${x1} ${y1} A 30 30 0 ${largeArc} 1 ${x2} ${y2} Z`}
        fill={CHART_COLORS[i % CHART_COLORS.length]}
      />
    );
  });

  return (
    <div className="flex items-center gap-3 py-2">
      <svg viewBox="0 0 80 80" className="w-20 h-20 shrink-0">{slices}</svg>
      <div className="space-y-1">
        {items.map((item, i) => (
          <div key={i} className="flex items-center gap-1.5 text-xs">
            <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: CHART_COLORS[i % CHART_COLORS.length] }} />
            <span className="text-gray-600">{item.name}</span>
            <span className="text-gray-400">{item.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function RadarChartSimple({ data }: { data: ChartData }) {
  const items = data.data as Array<{ dim: string; score: number }>;
  if (!items.length) return null;
  const n = items.length;
  const maxScore = Math.max(...items.map(d => Number(d.score ?? 0)), 1);
  const cx = 50, cy = 50, r = 35;

  const points = items.map((item, i) => {
    const angle = (i / n) * Math.PI * 2 - Math.PI / 2;
    const score = Number(item.score ?? 0) / maxScore;
    const x = cx + r * score * Math.cos(angle);
    const y = cy + r * score * Math.sin(angle);
    return `${x},${y}`;
  }).join(' ');

  // 网格（外圈）
  const gridPoints = items.map((_, i) => {
    const angle = (i / n) * Math.PI * 2 - Math.PI / 2;
    return `${cx + r * Math.cos(angle)},${cy + r * Math.sin(angle)}`;
  }).join(' ');

  return (
    <div className="flex items-center gap-3 py-2">
      <svg viewBox="0 0 100 100" className="w-24 h-24 shrink-0">
        <polygon points={gridPoints} fill="none" stroke="#e5e7eb" strokeWidth="0.5" />
        <polygon points={points} fill="rgba(99,102,241,0.2)" stroke="#6366f1" strokeWidth="1.5" />
        {items.map((_, i) => {
          const angle = (i / n) * Math.PI * 2 - Math.PI / 2;
          const x = cx + r * Math.cos(angle);
          const y = cy + r * Math.sin(angle);
          return <circle key={i} cx={x} cy={y} r="1" fill="#e5e7eb" />;
        })}
      </svg>
      <div className="space-y-1">
        {items.map((item, i) => (
          <div key={i} className="flex items-center gap-1.5 text-xs">
            <span className="text-gray-500">{item.dim}</span>
            <span className="text-gray-400">{item.score}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ChartRenderer({ data }: { data: ChartData }) {
  const renderChart = () => {
    switch (data.type) {
      case 'gauge': return <GaugeChart data={data} />;
      case 'bar': return <BarChartSimple data={data} />;
      case 'line': return <LineChartSimple data={data} />;
      case 'pie': return <PieChartSimple data={data} />;
      case 'radar': return <RadarChartSimple data={data} />;
      default: return null;
    }
  };

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-3 my-2 max-w-sm">
      {data.title && <p className="text-xs text-gray-500 mb-1">{data.title}</p>}
      {renderChart()}
    </div>
  );
}
