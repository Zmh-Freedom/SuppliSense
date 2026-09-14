import type { AgentAnswer } from '../types';
import { ConclusionSummary } from './ChatResultSections';

export default function ActionSummary({ answer, limitations }: { answer: AgentAnswer; limitations: string[] }) {
  const supportedCount = answer.claims.filter(claim => claim.validation_status === 'supported').length;
  const needsReview = answer.status === 'needs_review' || limitations.length > 0;
  const hasClaims = answer.claims.length > 0;
  const isWatchlist = answer.summary.includes('监控清单') || answer.claims.some(claim => claim.statement.startsWith('监控对象：'));
  const isTrend = answer.summary.includes('风险变化') || answer.claims.some(claim => claim.statement.includes('风险变化：'));
  const isNetwork = answer.summary.includes('供应链关系') || answer.summary.includes('传染风险') || answer.claims.some(claim => claim.dimension === 'risk_network');
  const conclusion = isWatchlist ? '监控清单' : isTrend ? '风险变化检查' : isNetwork ? '供应链关系与传染风险' : needsReview ? '建议复核' : hasClaims ? '已形成初步结论' : '暂未形成结论';
  const explanation = isWatchlist || isTrend || isNetwork
    ? answer.summary
    : hasClaims && answer.summary?.includes('\n\n')
    ? answer.summary
    : answer.summary && (answer.summary.includes('供应商复核') || answer.summary.includes('综合风险评分') || answer.summary.includes('寻源候选'))
    ? answer.summary
    : needsReview
    ? '发现了需要人工确认的信号或数据缺口，请先完成下方复核事项。'
    : hasClaims ? '当前判断已有可追溯证据支持，可结合明细安排后续动作。' : '当前证据不足以支持明确判断，已标注本轮未覆盖的数据范围。';
  const nextAction = isWatchlist ? '选择供应商查看详情' : isTrend ? '查看每家供应商的变化' : isNetwork ? '查看关联实体和关系图' : needsReview ? '核对复核事项' : hasClaims ? '查看结论明细' : '查看数据范围';

  return <ConclusionSummary><section className="border-b border-[var(--color-border)] bg-[var(--color-code-bg)]/40 px-4 py-4 sm:px-5" aria-label="行动结论">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-secondary)]">① 结论摘要</p><h3 className="mt-1 text-lg font-semibold text-[var(--color-text)]">{conclusion}</h3><p className="mt-1 max-w-2xl text-sm leading-6 text-[var(--color-text-secondary)]">{explanation}</p></div>
      <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${needsReview ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-emerald-200 bg-emerald-50 text-emerald-800'}`}>{needsReview ? '需采购人员处理' : '可继续推进'}</span>
    </div>
    <dl className="mt-4 grid divide-y divide-[var(--color-border)] overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] sm:grid-cols-3 sm:divide-x sm:divide-y-0">
      <div className="px-3 py-2.5"><dt className="text-[11px] text-[var(--color-text-secondary)]">处理状态</dt><dd className="mt-1 text-sm font-medium text-[var(--color-text)]">{conclusion}</dd></div>
      <div className="px-3 py-2.5"><dt className="text-[11px] text-[var(--color-text-secondary)]">证据覆盖</dt><dd className="mt-1 text-sm font-medium text-[var(--color-text)]">{hasClaims ? `${supportedCount} / ${answer.claims.length} 条已支持` : '暂无可用证据'}</dd></div>
      <div className="px-3 py-2.5"><dt className="text-[11px] text-[var(--color-text-secondary)]">下一步</dt><dd className="mt-1 text-sm font-medium text-[var(--color-text)]">{nextAction}</dd></div>
    </dl>
    {answer.action_proposals.length > 0 && <div className="mt-3 rounded-xl border border-indigo-200 bg-indigo-50/60 px-3 py-2.5 text-sm text-indigo-950"><p className="text-[11px] font-medium text-indigo-700">采购动作建议</p>{answer.action_proposals.map((proposal, index) => { const item = proposal as Record<string, unknown>; return <div key={`${String(item.action_type || 'action')}-${index}`} className="mt-1.5"><span className="font-medium">{String(item.label || '安排人工复核')}</span>{item.reason != null && <span className="ml-1 text-indigo-900/75">{String(item.reason)}</span>}</div>; })}</div>}
  </section></ConclusionSummary>;
}
