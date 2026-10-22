import { canonicalFactPath } from './factPaths';
import type { AgentAnswer, AgentEvidenceRecord, ClaimPresentation } from './types';

/** Interpret only known metric contracts, after checking the exact evidence binding. */
export function presentRiskClaim(
  claim: AgentAnswer['claims'][number],
  evidence: AgentEvidenceRecord[],
  peerClaims: AgentAnswer['claims'],
  coverageLimited: boolean,
): ClaimPresentation | null {
  const path = canonicalFactPath(claim.fact_path || '').split('.').pop() || '';
  const rate = ['quality_rate', 'on_time_rate'].includes(path) && ['quality', 'delivery'].includes(claim.dimension);
  const score = path === 'risk_score';
  const level = path === 'risk_level';
  if (!rate && !score && !level) return null;
  const operational = ['quality', 'delivery'].includes(claim.dimension);
  const metric = rate ? (path === 'quality_rate' ? '合格率' : '准时交付率')
    : score ? (operational ? '历史运营风险分' : claim.dimension === 'risk' ? '综合安全评分' : '评分') : '风险等级';
  const value = rate && typeof claim.value === 'number' && claim.unit === 'ratio' && Number.isFinite(claim.value)
    && claim.value >= 0 && claim.value <= 1 ? `${(claim.value * 100).toFixed(1)}%`
    : `${claim.value ?? '—'}${score && typeof claim.value === 'number' ? '/100' : ''}`;
  const context = [...new Set(evidence.filter(record => claim.evidence_refs.includes(record.evidence_id)
    && record.entity_id === claim.entity_id && record.dimension === claim.dimension).map(record => {
      const facts = record.facts || {};
      const scope = facts.scope && typeof facts.scope === 'object' && !Array.isArray(facts.scope) ? facts.scope as Record<string, unknown> : {};
      const labels: Record<string,string> = { supplier_code:'供应商代码', material_code:'物料', base:'基地', purchasing_org_code:'采购组织' };
      return [typeof facts.company_name === 'string' ? facts.company_name : '主体待核验',
        typeof facts.period === 'string' ? facts.period : '周期未知',
        ...Object.entries(scope).map(([key,value]) => `${labels[key] || '范围'} ${String(value)}`)].join(' · ');
    }))].join('；');
  const result = (label: string, tone: ClaimPresentation['tone'] = 'neutral'): ClaimPresentation => ({ metric, value, label, tone, ...(context ? {context} : {}) });
  if (claim.validation_status === 'conflicting') return result('数据有冲突', 'danger');
  if (claim.validation_status !== 'supported') return result('当前未覆盖', 'warning');
  if (operational && (score || level)) return result('历史规则待核验');
  if (!rate && claim.dimension !== 'risk') return result('评分含义待核验');
  const records = claim.evidence_refs.map(ref => evidence.find(record => record.evidence_id === ref));
  if (!records.length || records.some(record => !record || record.entity_id !== claim.entity_id
    || record.dimension !== claim.dimension)) return result('当前未覆盖', 'warning');
  const bound = records as AgentEvidenceRecord[];
  if (bound.some(record => record.status === 'conflicting')) return result('数据有冲突', 'danger');
  if (bound.some(record => record.status === 'stale' || (record.valid_until && Date.parse(record.valid_until) < Date.now()))) {
    return result('数据需要更新', 'warning');
  }
  if (bound.some(record => record.status !== 'available' || record.data_mode !== 'formal'
    || (record.valid_until && !Number.isFinite(Date.parse(record.valid_until)))
    || record.facts?.[path] !== claim.value)) return result('当前未覆盖', 'warning');
  if (rate) {
    if (claim.unit !== 'ratio' || bound.some(record => record.facts?.[`${path}_unit`] !== 'ratio')
      || typeof claim.value !== 'number' || !Number.isFinite(claim.value) || claim.value < 0 || claim.value > 1) {
      return result('指标单位或数值待核验', 'warning');
    }
    return result('已取得指标，风险等级待确认');
  }
  if (bound.some(record => record.provider !== 'assess_risk' && record.source_type !== 'risk_service_result')) {
    return result('评分含义待核验');
  }
  const classify = (v: unknown): 'high' | 'medium' | 'low' | null => {
    if (typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 100) return v < 40 ? 'high' : v < 70 ? 'medium' : 'low';
    return ({ 高风险: 'high', 中风险: 'medium', 低风险: 'low', high: 'high', medium: 'medium', low: 'low' } as const)[String(v)] ?? null;
  };
  const grade = classify(claim.value);
  const peers = peerClaims.filter(peer => peer.entity_id === claim.entity_id && peer.dimension === claim.dimension
    && peer.validation_status === 'supported' && canonicalFactPath(peer.fact_path || '').split('.').pop() === (score ? 'risk_level' : 'risk_score')
    && peer.evidence_refs.some(ref => claim.evidence_refs.includes(ref)));
  if (peers.some(peer => classify(peer.value) && classify(peer.value) !== grade)) return result('评分与等级有冲突', 'warning');
  if (grade === 'high') return result('高风险信号', 'danger');
  if (grade === 'medium') return result('需关注', 'warning');
  if (grade === 'low') return result(coverageLimited ? '资料范围内风险较低' : '风险较低', 'positive');
  return result('评分含义待核验');
}
