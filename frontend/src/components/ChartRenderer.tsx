import type { ChartData } from '../types';

const CHART_COLORS = ['#6366f1', '#f59e0b', '#10b981', '#ef4444', '#8b5cf6', '#ec4899'];

interface PieSlice {
  index: number;
  startAngle: number;
  endAngle: number;
}

function calculatePieSlices(items: Array<{ name: string; value: number }>, total: number): PieSlice[] {
  return items.reduce<{ angle: number; slices: PieSlice[] }>(
    (result, item, index) => {
      const angle = (Number(item.value ?? 0) / total) * Math.PI * 2;
      return {
        angle: result.angle + angle,
        slices: [
          ...result.slices,
          {
            index,
            startAngle: result.angle,
            endAngle: result.angle + angle,
          },
        ],
      };
    },
    { angle: -Math.PI / 2, slices: [] },
  ).slices;
}

function GaugeChart({ data }: { data: ChartData }) {
  const item = data.data[0] as Record<string, unknown> | undefined;
  if (!item) return null;
  const value = Number(item.value ?? 0);
  const max = Number(item.max ?? 100);
  const label = String(item.label ?? '');
  const pct = Math.min(value / max, 1);

  // Risk safety score colors: red (low safety) → amber → green.
  const color = value < 40 ? '#ef4444' : value < 70 ? '#f59e0b' : '#10b981';

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
              className="h-full rounded-full"
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

function LineChartSimple({ data, lineIsContinuous, missingValueLabel }: { data: ChartData; lineIsContinuous: boolean; missingValueLabel: string }) {
  const items = data.data.map(item => {
    const value = item.value;
    return {
      date: typeof item.date === 'string' ? item.date : '日期未知',
      value: typeof value === 'number' && Number.isFinite(value) ? value : null,
    };
  });
  if (!items.length) return null;

  const values = items.flatMap(item => item.value === null ? [] : [item.value]);

  const dateTable = <details className="mt-1 text-xs">
    <summary className="min-h-8 cursor-pointer py-1 text-[var(--color-primary-bg)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-focus-ring)]">
      查看日期与数值
    </summary>
    <div className="overflow-x-auto">
      <table aria-label={`${data.title}日期与数值`} className="w-full border-collapse text-left text-xs">
        <caption className="sr-only">{data.title}日期与数值；缺失值表示该期未取得数值，不表示数值为零。</caption>
        <thead><tr className="border-b border-[var(--color-border)] text-[var(--color-text-secondary)]"><th scope="col" className="py-1 pr-3 font-medium">日期/报告期</th><th scope="col" className="py-1 pr-3 font-medium">数值</th><th scope="col" className="py-1 font-medium">状态</th></tr></thead>
        <tbody>{items.map((item, index) => <tr key={`${item.date}-${index}`} className="border-b border-[var(--color-border)] last:border-0">
          <th scope="row" className="py-1 pr-3 font-normal text-[var(--color-text)]">{item.date}</th>
          <td className="py-1 pr-3 tabular-nums text-[var(--color-text)]">{item.value === null ? '—' : String(item.value)}</td>
          <td className="py-1 text-[var(--color-text-secondary)]">{item.value === null ? missingValueLabel : '已取得'}</td>
        </tr>)}</tbody>
      </table>
    </div>
  </details>;

  if (values.length === 0) {
    return <div className="py-2">
      <p className="text-xs text-[var(--color-text-secondary)]">没有可绘制数值；下表保留已有日期。</p>
      {dateTable}
    </div>;
  }

  if (!lineIsContinuous) {
    return <div className="py-2">
      <p className="text-xs leading-5 text-[var(--color-text-secondary)]">无法确认日期连续性，折线未绘制；下表仅列出已有日期与数值。</p>
      {dateTable}
    </div>;
  }

  const min = Math.min(0, ...values);
  let max = Math.max(0, ...values);
  if (min === max) max = min === 0 ? 1 : 0;
  const range = max - min;
  const left = 34;
  const right = 232;
  const top = 9;
  const bottom = 67;
  const yFor = (value: number) => top + ((max - value) / range) * (bottom - top);
  const zeroY = yFor(0);
  const xFor = (index: number) => items.length === 1
    ? (left + right) / 2
    : left + (index / (items.length - 1)) * (right - left);
  const segments: string[][] = [];
  let currentSegment: string[] = [];
  items.forEach((item, index) => {
    if (item.value === null) {
      if (currentSegment.length > 0) segments.push(currentSegment);
      currentSegment = [];
      return;
    }
    currentSegment.push(`${xFor(index)},${yFor(item.value)}`);
  });
  if (currentSegment.length > 0) segments.push(currentSegment);
  const formatAxisValue = (value: number) => new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value);

  return (
    <div className="py-2">
      <svg
        role="img"
        aria-label={`${data.title}趋势图；日期按已确认的等周期排列；纵轴含零基线；未取得数值的日期不补零，折线在缺口处断开。`}
        viewBox="0 0 240 91"
        className="w-full"
      >
        <line x1={left} y1={zeroY} x2={right} y2={zeroY} stroke="#9ca3af" strokeDasharray="3 2" strokeWidth="1" />
        <text x={left - 5} y={top + 3} textAnchor="end" fontSize="8" fill="#6b7280">{formatAxisValue(max)}</text>
        <text x={left - 5} y={zeroY + 3} textAnchor="end" fontSize="8" fill="#6b7280">0</text>
        {min < 0 && <text x={left - 5} y={bottom + 3} textAnchor="end" fontSize="8" fill="#6b7280">{formatAxisValue(min)}</text>}
        {items.map((item, index) => item.value === null ? (
          <line
            key={`missing-${item.date}-${index}`}
            x1={xFor(index)} y1={top} x2={xFor(index)} y2={bottom}
            stroke="#cbd5e1" strokeDasharray="2 3" strokeWidth="1"
          >
            <title>{item.date}：{missingValueLabel}，折线不跨越该日期</title>
          </line>
        ) : null)}
        {segments.filter(segment => segment.length > 1).map((segment, index) => (
          <polyline key={`segment-${index}`} fill="none" stroke="#6366f1" strokeWidth="2" points={segment.join(' ')} />
        ))}
        {items.map((item, index) => item.value === null ? null : (
          <circle key={`point-${item.date}-${index}`} cx={xFor(index)} cy={yFor(item.value)} r="3" fill="#6366f1" />
        ))}
        <text x={left} y="86" fontSize="8" fill="#6b7280">{items[0].date}</text>
        {items.length > 1 && <text x={right} y="86" textAnchor="end" fontSize="8" fill="#6b7280">{items[items.length - 1].date}</text>}
      </svg>
      {dateTable}
    </div>
  );
}

function PieChartSimple({ data }: { data: ChartData }) {
  const items = data.data as Array<{ name: string; value: number }>;
  if (!items.length) return null;
  const total = items.reduce((s, d) => s + Number(d.value ?? 0), 0) || 1;
  const slices = calculatePieSlices(items, total);

  const slicePaths = slices.map(({ index, startAngle, endAngle }) => {
    const x1 = 40 + 30 * Math.cos(startAngle);
    const y1 = 40 + 30 * Math.sin(startAngle);
    const x2 = 40 + 30 * Math.cos(endAngle);
    const y2 = 40 + 30 * Math.sin(endAngle);
    const angle = endAngle - startAngle;
    const largeArc = angle > Math.PI ? 1 : 0;

    return (
      <path
        key={index}
        d={`M 40 40 L ${x1} ${y1} A 30 30 0 ${largeArc} 1 ${x2} ${y2} Z`}
        fill={CHART_COLORS[index % CHART_COLORS.length]}
      />
    );
  });

  return (
    <div className="flex items-center gap-3 py-2">
      <svg viewBox="0 0 80 80" className="w-20 h-20 shrink-0">{slicePaths}</svg>
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

export default function ChartRenderer({ data, compact = false, lineIsContinuous = false, missingValueLabel = '未提供数值' }: { data: ChartData; compact?: boolean; lineIsContinuous?: boolean; missingValueLabel?: string }) {
  const renderChart = () => {
    switch (data.type) {
      case 'gauge': return <GaugeChart data={data} />;
      case 'bar': return <BarChartSimple data={data} />;
      case 'line': return <LineChartSimple data={data} lineIsContinuous={lineIsContinuous} missingValueLabel={missingValueLabel} />;
      case 'pie': return <PieChartSimple data={data} />;
      case 'radar': return <RadarChartSimple data={data} />;
      default: return null;
    }
  };

  return (
    <div className={compact ? 'min-w-0' : 'min-w-0 max-w-sm rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3 my-2'}>
      {data.title && <p className="text-xs text-gray-500 mb-1">{data.title}</p>}
      {renderChart()}
    </div>
  );
}
