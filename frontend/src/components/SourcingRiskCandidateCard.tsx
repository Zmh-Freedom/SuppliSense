import type { SourcingRiskCandidate, SourcingRiskDecision, SourcingRiskEvidence } from '../types';
import { Link } from 'react-router-dom';
import { getRiskLevelLabel } from '../riskColors';

const IDENTITY_STATUS_LABELS: Record<string, string> = {
  exact: '主体已确认',
  candidates: '存在多个主体候选，待选择',
  pending_verification: '主体待人工确认',
  verified: '主体已确认',
};

const GROUP_LABELS: Record<string, string> = {
  recommended: '推荐',
  alternative: '备选',
  needs_review: '待复核',
  rejected: '不建议采用',
};

const REASON_CODE_LABELS: Record<string, string> = {
  capability_match: '供货能力匹配',
  category_match: '采购品类匹配',
  historical_supplier: '有历史合作记录',
  risk_evidence: '风险证据可用',
  identity_verified: '主体已确认',
  identity_pending: '主体待确认',
  insufficient_evidence: '证据不足',
  SANCTIONS_HIT: '存在制裁或处罚命中',
  IDENTITY_UNVERIFIED: '主体尚未确认',
  UNMATCHED_CATEGORY: '品类匹配不足',
  SUPPLIER_NOT_ACTIVE: '供应商状态待确认',
  MANDATORY_QUALIFICATION_MISSING: '必需资质待确认',
  KEY_EVIDENCE_CONFLICT: '关键证据存在冲突',
};

const DIMENSION_LABELS: Record<string, string> = {
  financial: '财务',
  judicial: '司法',
  sentiment: '舆情',
  sanctions: '制裁与处罚',
  esg: '可持续性',
  continuity: '交易连续性',
  identity: '主体身份',
  sourcing: '寻源来源',
};

const SOURCE_LABELS: Record<string, string> = {
  local: '本地供应商库',
  feishu_bitable: '供应商主数据',
  staged_external: '外部候选',
  tianyancha: '天眼查',
  web_search: '公开网页',
  public_web_search: '公开网页',
  duckduckgo_html: '公开网页',
  gasgoo_manual_export: '盖世人工候选',
};

function unique(values: string[]): string[] {
  return [...new Set(values.map(value => value.trim()).filter(Boolean))];
}

function stringList(value: unknown): string[] {
  if (typeof value === 'string') return [value];
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function readableReason(code: string): string {
  const direct = REASON_CODE_LABELS[code] ?? REASON_CODE_LABELS[code.toUpperCase()];
  if (direct) return direct;
  const missing = code.match(/^MISSING_(.+)_DATA$/i);
  if (missing) return `${DIMENSION_LABELS[missing[1].toLowerCase()] ?? missing[1]}数据缺失`;
  const stale = code.match(/^STALE_(.+)_DATA$/i);
  if (stale) return `${DIMENSION_LABELS[stale[1].toLowerCase()] ?? stale[1]}数据已过期`;
  if (code.startsWith('web_search:') || code.startsWith('tianyancha:')) return '外部检索命中采购条件';
  if (code.startsWith('industry_code:')) return '匹配目标行业范围';
  return code;
}

function formatDate(value: unknown): string | null {
  if (typeof value !== 'string' || !value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value.slice(0, 10);
  return parsed.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

function sourceLabel(candidate: SourcingRiskCandidate): string {
  if (candidate.source === 'gasgoo_manual_export') return '盖世人工候选';
  if (candidate.source === 'staged_external' || candidate.status === 'staged_candidate') return '外部候选';
  return SOURCE_LABELS[candidate.source ?? ''] ?? '本地供应商库';
}

function evidenceSourceLabel(source: string | undefined): string {
  return SOURCE_LABELS[source ?? ''] ?? DIMENSION_LABELS[source ?? ''] ?? source ?? '未标明来源';
}

function evidenceTitle(item: SourcingRiskEvidence): string {
  if (item.claim) return item.claim;
  const dimension = DIMENSION_LABELS[item.dimension ?? ''] ?? item.dimension ?? '供应商';
  return `${dimension}核验记录`;
}

function EvidenceDetails({ evidence }: { evidence: SourcingRiskEvidence[] }) {
  const visibleEvidence = evidence.filter((item, index, all) => {
    const key = item.evidence_id ?? `${item.dimension}-${item.source_reference ?? index}`;
    return all.findIndex(candidate => (candidate.evidence_id ?? `${candidate.dimension}-${candidate.source_reference ?? all.indexOf(candidate)}`) === key) === index;
  });
  if (visibleEvidence.length === 0) return <p className="text-xs text-[var(--color-text-secondary)]">暂无可展示的证据明细。</p>;

  return (
    <details className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-3 py-2">
      <summary className="cursor-pointer text-xs font-medium text-[var(--color-text)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]">查看证据明细（{visibleEvidence.length}）</summary>
      <ul className="mt-3 space-y-2" aria-label="证据明细">
        {visibleEvidence.map((item, index) => {
          const sourceReference = item.source_reference;
          const isLink = typeof sourceReference === 'string' && /^https?:\/\//.test(sourceReference);
          return <li key={item.evidence_id ?? `${item.dimension}-${index}`} className="border-t border-[var(--color-border)] pt-2 text-xs leading-5 text-[var(--color-text-secondary)] first:border-t-0 first:pt-0">
            <p className="font-medium text-[var(--color-text)]">{evidenceTitle(item)}</p>
            <p>来源：{evidenceSourceLabel(item.source)}{sourceReference && <> · {isLink ? <a href={sourceReference} target="_blank" rel="noreferrer" className="text-[var(--color-primary-bg)] hover:underline">查看来源</a> : sourceReference}</>}</p>
            <p>{item.freshness_status === 'stale' ? '时效：已过期' : item.freshness_status === 'fresh' ? '时效：较新' : '时效：未标明'}{item.conflict_status === 'conflicting' ? ' · 存在冲突' : ''}</p>
          </li>;
        })}
      </ul>
    </details>
  );
}

function CandidateDetails({ candidate, evidence, decision }: { candidate: SourcingRiskCandidate; evidence: SourcingRiskEvidence[]; decision?: SourcingRiskDecision }) {
  const matchedCategories = unique(candidate.categories ?? []);
  const mainProducts = unique([
    ...stringList(candidate.main_products),
    ...stringList(candidate.products),
    ...(candidate.capabilities ?? []).flatMap(item => stringList(item.product_name)),
  ]);
  const relatedProducts = unique([
    ...matchedCategories,
    ...mainProducts,
    ...stringList(candidate.specifications),
  ]).slice(0, 6);
  const reasons = unique([
    ...stringList(candidate.match_reasons).map(readableReason),
    ...(decision?.reason_codes ?? []).map(readableReason),
    ...(relatedProducts.length > 0 && candidate.match_reasons == null ? ['供应商资料包含相关产品或品类'] : []),
  ]);
  const candidateEvidence = evidence.length > 0 ? evidence : candidate.evidence ?? candidate.evidence_by_dimension ?? [];
  const pendingItems = unique([
    ...(candidate.identity_status && candidate.identity_status !== 'exact' ? [IDENTITY_STATUS_LABELS[candidate.identity_status] ?? '主体待确认'] : []),
    ...stringList(candidate.verification_reasons),
    ...(candidate.source === 'staged_external' || candidate.status === 'staged_candidate' ? ['外部候选尚未完成主体核验'] : []),
    ...(candidateEvidence.some(item => item.freshness_status === 'stale') ? ['部分风险证据已过期'] : []),
    ...(candidateEvidence.some(item => item.conflict_status === 'conflicting') ? ['存在冲突证据，需要人工复核'] : []),
    ...(decision?.reason_codes ?? []).filter(code => /^(MISSING_|STALE_|IDENTITY_UNVERIFIED|KEY_EVIDENCE_CONFLICT|MANDATORY_QUALIFICATION_MISSING)/i.test(code)).map(readableReason),
  ]);
  const sourceReference = typeof candidate.source_reference === 'string' ? candidate.source_reference : null;
  const sourceTitle = typeof candidate.source_title === 'string' ? candidate.source_title : null;
  const updatedAt = formatDate(candidate.source_updated_at ?? candidate.discovered_at);

  return <div className="space-y-3">
    <div className="grid gap-3 sm:grid-cols-2">
      <section className="rounded-xl border border-[var(--color-border)] p-3">
        <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">{candidate.source === 'gasgoo_manual_export' ? '盖世匹配品类' : '匹配品类'}</p>
        <p className="mt-1 text-sm leading-6 text-[var(--color-text)]">{matchedCategories.length > 0 ? matchedCategories.join('、') : '未标明匹配品类'}</p>
      </section>
      <section className="rounded-xl border border-blue-100 bg-blue-50/50 p-3">
        <p className="text-[11px] font-medium uppercase tracking-wide text-blue-800">主营产品（来源资料）</p>
        <p className="mt-1 text-sm leading-6 text-blue-950">{mainProducts.length > 0 ? mainProducts.join('、') : '资料未提供，需人工核验'}</p>
        {candidate.source === 'gasgoo_manual_export' && <p className="mt-1 text-[11px] leading-5 text-blue-800">仅用于判断产品范围，不等同于成品供货证明。</p>}
      </section>
    </div>
    <section className="rounded-xl border border-blue-100 bg-blue-50/60 p-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-blue-800">推荐理由</p>
      {reasons.length > 0 ? <ul className="mt-1 space-y-1 text-xs leading-5 text-blue-950">{reasons.map(reason => <li key={reason}>· {reason}</li>)}</ul> : <p className="mt-1 text-xs leading-5 text-blue-950">暂无结构化理由，建议展开证据明细后再确认。</p>}
    </section>
    {pendingItems.length > 0 && <section className="rounded-xl border border-amber-200 bg-amber-50 p-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-amber-900">待确认项</p>
      <ul className="mt-1 space-y-1 text-xs leading-5 text-amber-900">{pendingItems.map(item => <li key={item}>· {item}</li>)}</ul>
    </section>}
    <div className="grid gap-2 text-xs text-[var(--color-text-secondary)] sm:grid-cols-2">
      <div><span className="font-medium text-[var(--color-text)]">来源：</span>{sourceLabel(candidate)}{sourceTitle ? ` · ${sourceTitle}` : ''}{sourceReference && !/^https?:\/\//.test(sourceReference) ? ` · ${sourceReference}` : ''}</div>
      <div><span className="font-medium text-[var(--color-text)]">时间：</span>{updatedAt ? `来源更新于 ${updatedAt}` : '更新时间未标明'}</div>
    </div>
    {candidate.risk_level && <p className="text-xs text-[var(--color-text-secondary)]"><span className="font-medium text-[var(--color-text)]">风险核验：</span>{getRiskLevelLabel(candidate.risk_level)}{candidate.risk_score != null ? '（已有风险参考）' : ''}</p>}
    <div className="grid gap-1 text-xs text-[var(--color-text-secondary)]">
      {candidate.website_url ? <a href={candidate.website_url} target="_blank" rel="noreferrer" className="min-h-[44px] w-fit flex items-center text-[var(--color-primary-bg)] hover:underline">官网（待核验）</a> : <span>官网：未找到</span>}
      {candidate.contact_phone ? <span>电话（待核验）：{candidate.contact_phone}</span> : <span>电话：未找到</span>}
      {candidate.contact_email ? <a href={`mailto:${candidate.contact_email}`} className="min-h-[44px] w-fit flex items-center text-[var(--color-primary-bg)] hover:underline">邮箱（待核验）：{candidate.contact_email}</a> : <span>邮箱：未找到</span>}
    </div>
    {candidateEvidence.length > 0 && <EvidenceDetails evidence={candidateEvidence} />}
  </div>;
}

export default function SourcingRiskCandidateCard({ candidate, evidence, decision, onVerify, onContinueRisk, onAddToWatchlist, verifying = false }: { candidate: SourcingRiskCandidate; evidence?: SourcingRiskEvidence[]; decision?: SourcingRiskDecision; onVerify?: () => void; onContinueRisk?: () => void; onAddToWatchlist?: () => void; verifying?: boolean }) {
  const name = candidate.supplier_name ?? candidate.name ?? '未命名候选企业';
  const candidateEvidence = evidence ?? candidate.evidence ?? candidate.evidence_by_dimension ?? [];
  const profileId = candidate.supplier_id;
  const groupLabel = decision?.group ? GROUP_LABELS[decision.group] ?? '待确认' : null;
  const groupClass = decision?.group === 'recommended'
    ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
    : decision?.group === 'rejected'
      ? 'border-red-200 bg-red-50 text-red-800'
      : decision?.group === 'needs_review'
        ? 'border-amber-200 bg-amber-50 text-amber-900'
        : 'border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]';

  return (
    <article className="space-y-4 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4" aria-label={`候选供应商：${name}`}>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-base font-semibold text-[var(--color-text)]">{name}</h4>
            {groupLabel && <span className={`rounded-full border px-2 py-1 text-[11px] font-medium ${groupClass}`}>{groupLabel}</span>}
          </div>
          <p className="mt-1 text-xs text-[var(--color-text-secondary)]">来源：{sourceLabel(candidate)}</p>
        </div>
        <div className="space-y-1 text-right text-xs text-[var(--color-text-secondary)]">
          <p>主体核验：{IDENTITY_STATUS_LABELS[candidate.identity_status ?? ''] ?? '待确认'}</p>
          {candidate.status === 'staged_candidate' && <p className="text-amber-700">外部信息待核验</p>}
        </div>
      </header>
      <CandidateDetails candidate={candidate} evidence={candidateEvidence} decision={decision} />
      {profileId && candidate.source !== 'staged_external' && candidate.status !== 'staged_candidate' && <Link to={`/suppliers/${encodeURIComponent(profileId)}`} className="inline-flex min-h-[44px] items-center text-xs font-medium text-[var(--color-primary-bg)] hover:underline">查看供应商画像</Link>}
      {(onVerify || onContinueRisk || onAddToWatchlist) && <div className="flex flex-wrap gap-2 border-t border-[var(--color-border)] pt-3">
        {onVerify && candidate.identity_status !== 'exact' && <button type="button" onClick={onVerify} disabled={verifying} className="min-h-[44px] rounded-xl border border-[var(--color-primary-bg)] px-3 text-xs font-medium text-[var(--color-primary-bg)] disabled:opacity-50">{verifying ? '天眼查核验中…' : '核验主体与风险'}</button>}
        {onContinueRisk && <button type="button" onClick={onContinueRisk} className="min-h-[44px] rounded-xl border border-[var(--color-primary-bg)] px-3 text-xs font-medium text-[var(--color-primary-bg)]">继续风险核验</button>}
        {onAddToWatchlist && <button type="button" onClick={onAddToWatchlist} className="min-h-[44px] rounded-xl border border-amber-600 px-3 text-xs font-medium text-amber-700">申请加入监控</button>}
      </div>}
    </article>
  );
}
