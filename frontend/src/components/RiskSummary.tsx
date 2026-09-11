import type { ReactNode } from 'react';
import { getRiskColor, getRiskBg } from '../riskColors';

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
  const normalizedScore = score ?? 0;
  const color = getRiskColor(normalizedScore);
  const bg = getRiskBg(normalizedScore);
  if (compact) return <div className="flex flex-col items-center shrink-0"><div className="w-20 h-20 rounded-full flex items-center justify-center text-white text-2xl font-bold shadow-md" style={{ background: color }}>{score == null ? '—' : normalizedScore}</div><span className="text-xs mt-1 font-semibold" style={{ color }}>{level || '未知'}</span></div>;
  return (
    <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-sm" style={{ background: bg }} aria-label="风险摘要">
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full text-lg font-bold text-white" style={{ background: color }}>{score == null ? '—' : normalizedScore}</div>
        <div className="min-w-[110px]">
          <div className="text-lg font-semibold" style={{ color }}>{level || '未知'}</div>
          <div className="mt-0.5 text-[10px] text-gray-500">安全评分 · 分数越高风险越低</div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {isListed && <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-1.5 py-0.5 text-[10px] text-gray-500">上市</span>}
            {inWatchlist && <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] text-amber-600">监控中</span>}
          </div>
        </div>
        <div className="min-w-[160px] flex-1 bg-[var(--color-border)] h-2 rounded-full">
          <div className="h-full rounded-full transition-all duration-700" style={{ width: `${Math.max(0, Math.min(100, normalizedScore))}%`, background: color }} />
        </div>
        {meta}
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
    </section>
  );
}
