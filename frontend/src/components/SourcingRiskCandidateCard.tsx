import type { SourcingRiskCandidate, SourcingRiskEvidence } from '../types';

function EvidenceLabels({ evidence }: { evidence: SourcingRiskEvidence[] }) {
  const labels = evidence.flatMap(item => {
    const values: string[] = [];
    if (item.freshness_status === 'stale') values.push('证据已过期');
    if (item.conflict_status === 'conflicting') values.push('证据存在冲突');
    return values;
  });
  return labels.length > 0 ? <div className="flex flex-wrap gap-1">{[...new Set(labels)].map(label => <span key={label} className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-700">{label}</span>)}</div> : null;
}

export default function SourcingRiskCandidateCard({ candidate, evidence }: { candidate: SourcingRiskCandidate; evidence?: SourcingRiskEvidence[] }) {
  const name = candidate.supplier_name ?? candidate.name ?? '未命名候选企业';
  const source = candidate.source === 'staged_external' || candidate.status === 'staged_candidate'
    ? '外部暂存'
    : '本地库';
  const candidateEvidence = evidence ?? candidate.evidence ?? candidate.evidence_by_dimension ?? [];
  return (
    <article className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 space-y-2">
      <div className="flex justify-between gap-3">
        <h4 className="font-semibold text-sm text-[var(--color-text)]">{name}</h4>
        <span className="text-[10px] shrink-0 rounded-full px-2 py-0.5 bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]">{source}</span>
      </div>
      {candidate.identity_status && <p className="text-xs text-[var(--color-text-secondary)]">主体状态：{candidate.identity_status}</p>}
      {(candidate.industry || candidate.categories?.length || candidate.capabilities?.length) && (
        <div className="space-y-1 text-xs text-[var(--color-text-secondary)]">
          {candidate.industry && <p>行业：{candidate.industry}</p>}
          {candidate.categories?.length ? <p>主营品类：{candidate.categories.join('、')}</p> : null}
          {candidate.capabilities?.length ? (
            <p>供货能力：{candidate.capabilities.map(item => String(item.product_name || item.category || '')).filter(Boolean).join('、')}</p>
          ) : null}
        </div>
      )}
      <div className="grid gap-1 text-xs text-[var(--color-text-secondary)]">
        {candidate.website_url ? <a href={candidate.website_url} target="_blank" rel="noreferrer" className="w-fit text-[var(--color-primary-bg)] hover:underline">官网（待核验）</a> : <span>官网：未找到</span>}
        {candidate.contact_phone ? <span>电话（待核验）：{candidate.contact_phone}</span> : <span>电话：未找到</span>}
        {candidate.contact_email ? <a href={`mailto:${candidate.contact_email}`} className="w-fit text-[var(--color-primary-bg)] hover:underline">邮箱（待核验）：{candidate.contact_email}</a> : <span>邮箱：未找到</span>}
      </div>
      {candidate.source_updated_at && <p className="text-[10px] text-[var(--color-text-secondary)]">数据更新时间：{String(candidate.source_updated_at).slice(0, 10)}</p>}
      <EvidenceLabels evidence={candidateEvidence} />
    </article>
  );
}
