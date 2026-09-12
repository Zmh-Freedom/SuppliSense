import type { SourcingRiskCandidate, SourcingRiskEvidence } from '../types';

const IDENTITY_STATUS_LABELS: Record<string, string> = {
  exact: '主体已确认',
  candidates: '存在多个主体候选，待选择',
  pending_verification: '主体待人工确认',
  verified: '主体已确认',
};

function EvidenceLabels({ evidence }: { evidence: SourcingRiskEvidence[] }) {
  const labels = evidence.flatMap(item => {
    const values: string[] = [];
    if (item.freshness_status === 'stale') values.push('证据已过期');
    if (item.conflict_status === 'conflicting') values.push('证据存在冲突');
    return values;
  });
  return labels.length > 0 ? <div className="flex flex-wrap gap-1">{[...new Set(labels)].map(label => <span key={label} className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-700">{label}</span>)}</div> : null;
}

export default function SourcingRiskCandidateCard({ candidate, evidence, onVerify, onContinueRisk, onAddToWatchlist, verifying = false }: { candidate: SourcingRiskCandidate; evidence?: SourcingRiskEvidence[]; onVerify?: () => void; onContinueRisk?: () => void; onAddToWatchlist?: () => void; verifying?: boolean }) {
  const name = candidate.supplier_name ?? candidate.name ?? '未命名候选企业';
  const source = candidate.source === 'gasgoo_manual_export'
    ? '盖世人工候选'
    : candidate.source === 'staged_external' || candidate.status === 'staged_candidate'
      ? '外部待核验'
      : '本地库';
  const candidateEvidence = evidence ?? candidate.evidence ?? candidate.evidence_by_dimension ?? [];
  return (
    <article className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 space-y-2">
      <div className="flex justify-between gap-3">
        <h4 className="font-semibold text-sm text-[var(--color-text)]">{name}</h4>
        <span className="text-[10px] shrink-0 rounded-full px-2 py-0.5 bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]">{source}</span>
      </div>
      {candidate.identity_status && <p className="text-xs text-[var(--color-text-secondary)]">主体状态：{IDENTITY_STATUS_LABELS[candidate.identity_status] ?? '待确认'}</p>}
      {candidate.risk_score != null && <p className="text-xs text-[var(--color-text-secondary)]">天眼查风险复核：{candidate.risk_level || '未知'} {candidate.risk_score}/100</p>}
      {(candidate.industry || candidate.categories?.length || candidate.capabilities?.length) && (
        <div className="space-y-1 text-xs text-[var(--color-text-secondary)]">
          {candidate.industry && <p>行业：{candidate.industry}</p>}
          {candidate.categories?.length ? <p>主营品类：{candidate.categories.join('、')}</p> : null}
          {candidate.capabilities?.length ? (
            <p>供货能力：{candidate.capabilities.map(item => String(item.product_name || item.category || '')).filter(Boolean).join('、')}</p>
          ) : null}
        </div>
      )}
      {Array.isArray(candidate.match_reasons) && candidate.match_reasons.length > 0 && <p className="text-xs text-[var(--color-text-secondary)]">匹配依据：{candidate.match_reasons.map(item => String(item)).join('、')}</p>}
      <div className="grid gap-1 text-xs text-[var(--color-text-secondary)]">
        {candidate.website_url ? <a href={candidate.website_url} target="_blank" rel="noreferrer" className="w-fit text-[var(--color-primary-bg)] hover:underline">官网（待核验）</a> : <span>官网：未找到</span>}
        {candidate.contact_phone ? <span>电话（待核验）：{candidate.contact_phone}</span> : <span>电话：未找到</span>}
        {candidate.contact_email ? <a href={`mailto:${candidate.contact_email}`} className="w-fit text-[var(--color-primary-bg)] hover:underline">邮箱（待核验）：{candidate.contact_email}</a> : <span>邮箱：未找到</span>}
      </div>
      {Array.isArray(candidate.verification_reasons) && candidate.verification_reasons.length > 0 && <p className="text-xs text-amber-700">核验状态：{candidate.verification_reasons.map(item => String(item)).join('；')}</p>}
      {onVerify && candidate.identity_status !== 'exact' && (
        <button type="button" onClick={onVerify} disabled={verifying} className="w-fit rounded-lg border border-[var(--color-primary-bg)] px-2.5 py-1 text-xs font-medium text-[var(--color-primary-bg)] disabled:opacity-50">
          {verifying ? '天眼查核验中…' : '核验主体与风险'}
        </button>
      )}
      {(onContinueRisk || onAddToWatchlist) && (
        <div className="flex flex-wrap gap-2 pt-1">
          {onContinueRisk && <button type="button" onClick={onContinueRisk} className="rounded-lg border border-[var(--color-primary-bg)] px-2.5 py-1 text-xs font-medium text-[var(--color-primary-bg)]">继续风险核验</button>}
          {onAddToWatchlist && <button type="button" onClick={onAddToWatchlist} className="rounded-lg border border-amber-600 px-2.5 py-1 text-xs font-medium text-amber-700">申请加入监控</button>}
        </div>
      )}
      {candidate.source_updated_at && <p className="text-[10px] text-[var(--color-text-secondary)]">数据更新时间：{String(candidate.source_updated_at).slice(0, 10)}</p>}
      <EvidenceLabels evidence={candidateEvidence} />
    </article>
  );
}
