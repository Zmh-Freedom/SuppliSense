import { screeningEvidenceReview } from '../evidencePresentation';
import type { AgentAnswer, AgentEvidenceRecord } from '../types';
import { ConclusionSummary } from './ChatResultSections';

export default function ActionSummary({ answer, evidence = [] }: { answer: AgentAnswer; evidence?: AgentEvidenceRecord[] }) {
  const screeningReview = screeningEvidenceReview(evidence, answer.claims);
  const proposals = answer.action_proposals;
  if (!screeningReview && proposals.length === 0) return null;

  return <ConclusionSummary><section className="border-b border-[var(--color-border)] bg-[var(--color-code-bg)]/40 px-4 py-4 sm:px-5" aria-label="行动结论">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-secondary)]">本轮处理建议</p><h3 className="mt-1 text-lg font-semibold text-[var(--color-text)]">{screeningReview ? screeningReview.label : '结构化动作提案'}</h3></div>
      {screeningReview && <span className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-800">需采购人员处理</span>}
    </div>
    {screeningReview
      ? <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm text-amber-950"><p className="text-[11px] font-medium text-amber-800">名单核验后再确定采购动作</p><p className="mt-1 text-xs leading-5 text-amber-900/75">{screeningReview.summary}</p></div>
      : <div className="mt-3 space-y-2">{proposals.map((item, index) => <article key={`${String(item.action_type || 'action')}-${index}`} className="rounded-xl border border-indigo-200 bg-indigo-50/60 px-3 py-2.5 text-sm text-indigo-950"><p className="font-medium">{String(item.label || '查看结构化提案')}</p>{item.reason != null && <p className="mt-1 text-xs leading-5 text-indigo-900/75">{String(item.reason)}</p>}</article>)}</div>}
  </section></ConclusionSummary>;
}
