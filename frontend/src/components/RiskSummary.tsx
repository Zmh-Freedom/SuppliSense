import type { ReactNode } from 'react';
import { getRiskColor, getRiskBg, getRiskLevelLabel } from '../riskColors';

export default function RiskSummary({
  score,
  level,
  isListed,
  inWatchlist,
  actions,
  meta,
  compact = false,
}: {
  score?: number | null;
  level?: string | null;
  isListed?: boolean;
  inWatchlist?: boolean;
  actions?: ReactNode;
  meta?: ReactNode;
  compact?: boolean;
}) {
  const hasScore = typeof score === 'number' && Number.isFinite(score);
  const normalizedScore = hasScore ? score as number : 0;
  const color = hasScore ? getRiskColor(normalizedScore) : '#737373';
  const bg = hasScore ? getRiskBg(normalizedScore) : '#f5f5f4';
  const readableLevel = getRiskLevelLabel(level);
  if (compact) return <div className="flex shrink-0 flex-col items-center"><div className="flex h-20 w-20 items-center justify-center rounded-full text-2xl font-bold text-white shadow-md" style={{ background: color }}>{hasScore ? normalizedScore : '—'}</div><span className="mt-1 text-xs font-semibold" style={{ color }}>{readableLevel}</span>{!hasScore && <span className="mt-0.5 text-[10px] text-gray-400">暂无快照</span>}</div>;
  return (
    <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-sm" style={{ background: bg }} aria-label="风险摘要">
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full text-lg font-bold text-white" style={{ background: color }}>{hasScore ? normalizedScore : '—'}</div>
        <div className="min-w-[110px]">
          <div className="text-lg font-semibold" style={{ color }}>{readableLevel}</div>
          <div className="mt-0.5 text-[10px] text-gray-500">{hasScore ? '安全评分 · 分数越高风险越低' : '暂无风险快照，暂无法判断风险等级'}</div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {isListed && <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-1.5 py-0.5 text-[10px] text-gray-500">上市</span>}
            {inWatchlist && <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] text-amber-600">监控中</span>}
          </div>
        </div>
        <div className="min-w-[160px] flex-1 bg-[var(--color-border)] h-2 rounded-full">
          <div className="h-full rounded-full transition-all duration-700" style={{ width: `${hasScore ? Math.max(0, Math.min(100, normalizedScore)) : 0}%`, background: color }} />
        </div>
        {meta}
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
    </section>
  );
}
