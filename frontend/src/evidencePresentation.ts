import type { AgentAnswer, AgentEvidenceRecord, MonitorTarget, ScreeningReadResult, ScreeningStatus } from './types';
import { getRiskLevelLabel } from './riskColors';

export interface ScreeningPresentation {
  label: string;
  tone: 'neutral' | 'warning' | 'danger';
  detail: string;
}

export interface MonitorEvidencePresentation {
  riskLabel: string;
  actionLabel: string;
  actionTone: 'neutral' | 'warning' | 'danger';
  detail: string;
}

type ScreeningReadState = 'loading' | 'error' | 'ready';

const SCREENING_LABELS: Record<ScreeningStatus, Pick<ScreeningPresentation, 'label' | 'tone'>> = {
  no_match_in_scope: { label: '范围内未发现匹配', tone: 'neutral' },
  possible_match: { label: '疑似名单线索，待核验', tone: 'warning' },
  confirmed_match: { label: '名单命中，待人工复核', tone: 'danger' },
  partial: { label: '部分来源已核验', tone: 'warning' },
  stale: { label: '依据已过期，需重新核验', tone: 'warning' },
  conflicting: { label: '来源信息存在冲突', tone: 'warning' },
  not_queried: { label: '尚未完成名单核验', tone: 'warning' },
  unavailable: { label: '核验状态未知', tone: 'neutral' },
};

const SCREENING_REVIEW: Partial<Record<ScreeningStatus, { label: string; summary: string }>> = {
  confirmed_match: {
    label: '名单命中，待人工复核',
    summary: '发现与主体标识匹配的名单记录，需要人工复核，当前不可按未命中处理。',
  },
  possible_match: {
    label: '疑似名单线索，待核验',
    summary: '发现名称相似的名单线索，主体尚未确认，需要人工核验后再作判断。',
  },
  stale: {
    label: '名单依据已过期',
    summary: '名单依据已过期，当前无法确认是否命中，需要重新核验。',
  },
  conflicting: {
    label: '名单来源存在冲突',
    summary: '名单来源或主体信息存在冲突，当前结论需要人工核对。',
  },
  partial: {
    label: '名单来源覆盖不完整',
    summary: '名单来源覆盖不完整，当前无法确认完整筛查结果。',
  },
  not_queried: {
    label: '名单尚未完成核验',
    summary: '已配置名单来源尚未完成查询，当前无法确认筛查结果。',
  },
  unavailable: {
    label: '名单核验状态未知',
    summary: '名单筛查状态未知，当前没有足够的正式来源证据判断是否命中。',
  },
};

export function screeningPresentation(
  screening: ScreeningReadResult | null | undefined,
  state: ScreeningReadState,
): ScreeningPresentation {
  if (state === 'loading') {
    return { label: '正在核验', tone: 'neutral', detail: '正在读取当前名单来源与证据状态。' };
  }
  if (state === 'error' || !screening) {
    return { label: '核验状态未知', tone: 'neutral', detail: '当前无法读取名单筛查状态，旧缓存不代表当前结论。' };
  }

  const meta = SCREENING_LABELS[screening.screening_status] || SCREENING_LABELS.unavailable;
  const details = [`筛查范围：${screening.scope || '未声明'}`];
  if (screening.coverage?.status) details.push(`来源覆盖：${screening.coverage.status}`);
  for (const source of screening.coverage?.sources || []) {
    const observedAt = source.evidence?.observed_at || source.validity?.observed_at || '未知';
    const collectedAt = source.evidence?.collected_at || source.validity?.collected_at || '未知';
    details.push(`来源 ${source.source}：来源观察时间：${observedAt}；采集时间：${collectedAt}`);
  }
  if (screening.screening_status === 'no_match_in_scope') {
    details.push('未命中结论仅适用于本次列明的来源和筛查范围，不代表整体合规正常。');
  }
  if (screening.coverage?.sources?.length === 0) details.push('当前没有可用的名单来源。');
  if (screening.limitations?.length) details.push(...screening.limitations);
  return { ...meta, detail: details.join('；') };
}

export function screeningEvidenceReview(evidence: AgentEvidenceRecord[], claims: AgentAnswer['claims'] = []): {
  status: ScreeningStatus;
  label: string;
  summary: string;
} | null {
  const priority: ScreeningStatus[] = [
    'confirmed_match', 'possible_match', 'conflicting', 'stale', 'partial', 'not_queried', 'unavailable',
  ];
  const statuses = new Set<ScreeningStatus>();
  let hasFormalStructuredScreeningStatus = false;
  let hasSyntheticScreeningStatus = false;
  for (const record of evidence) {
    if (record.dimension !== 'compliance') continue;
    const value = record.facts?.screening_status;
    const hasScreeningStatus = typeof value === 'string' && value in SCREENING_LABELS;
    if (record.data_mode !== 'formal' || record.status === 'synthetic') {
      hasSyntheticScreeningStatus ||= hasScreeningStatus;
      continue;
    }

    if (record.status === 'stale'
      || (record.valid_until
        && (!Number.isFinite(Date.parse(record.valid_until))
          || Date.parse(record.valid_until) < Date.now()))) {
      hasFormalStructuredScreeningStatus = true;
      statuses.add('stale');
      continue;
    }
    if (record.status === 'conflicting') {
      hasFormalStructuredScreeningStatus = true;
      statuses.add('conflicting');
      continue;
    }
    if (record.status === 'unavailable' || record.status === 'missing') {
      hasFormalStructuredScreeningStatus = true;
      statuses.add('unavailable');
      continue;
    }
    if (!['available', 'confirmed_empty'].includes(record.status)) continue;
    if (hasScreeningStatus) {
      hasFormalStructuredScreeningStatus = true;
      if (value in SCREENING_REVIEW) statuses.add(value as ScreeningStatus);
    }
  }
  const status = priority.find(item => statuses.has(item));
  if (!status) {
    const hasLegacyScreeningClaim = claims.some(claim =>
      claim.dimension === 'compliance' || claim.fact_path?.split('.').at(-1) === 'clean',
    );
    if ((!hasLegacyScreeningClaim && !hasSyntheticScreeningStatus)
      || hasFormalStructuredScreeningStatus) return null;
    return {
      status: 'unavailable',
      ...SCREENING_REVIEW.unavailable!,
    };
  }
  return { status, ...SCREENING_REVIEW[status]! };
}

function riskSnapshotState(target: MonitorTarget): { current: boolean; detail: string; checkedAt?: string } {
  const coverage = target.data_coverage as {
    status?: string;
    summary?: string;
    dimensions?: Array<{ key?: string; status?: string; detail?: string; facts?: Record<string, unknown> }>;
  } | undefined;
  const snapshot = coverage?.dimensions?.find(item => item.key === 'risk_snapshot');
  const current = snapshot
    ? snapshot.status === 'available'
    : coverage?.status === 'complete' && target.risk_score != null;
  const checkedAt = typeof snapshot?.facts?.checked_at === 'string' ? snapshot.facts.checked_at : undefined;
  const detail = snapshot?.detail || (current ? '当前风险快照证据有效。' : '当前风险快照未能核验。');
  return { current, detail, checkedAt };
}

export function isCurrentRiskEvidence(target: MonitorTarget): boolean {
  return riskSnapshotState(target).current;
}

export function monitorEvidencePresentation(target: MonitorTarget): MonitorEvidencePresentation {
  const coverage = target.data_coverage as { status?: string; summary?: string } | undefined;
  const snapshot = riskSnapshotState(target);
  const hasScore = typeof target.risk_score === 'number';
  const riskLevel = target.risk_level ? getRiskLevelLabel(target.risk_level) : '风险等级未知';
  const historical = hasScore && !snapshot.current;
  const riskLabel = historical
    ? `历史评估：${riskLevel} ${target.risk_score}/100；当前待核验`
    : hasScore
      ? `${riskLevel} ${target.risk_score}/100`
      : '当前风险待核验';

  const historicalHighRisk = historical && (
    riskLevel.includes('高') || target.risk_change?.status === 'deteriorating'
  );
  const action = target.next_action;
  let actionLabel = '核验状态未知';
  let actionTone: MonitorEvidencePresentation['actionTone'] = 'warning';
  let actionDetail = '当前缺少可信的下一步建议，需要先核实风险快照和数据覆盖。';
  if (historicalHighRisk) {
    actionLabel = action?.priority === 'high' ? action.label : '优先采购复核';
    actionTone = 'danger';
    actionDetail = '历史评估仍显示高风险或风险恶化，当前证据待核验，需要优先复核。';
  } else if (action?.priority === 'high') {
    actionLabel = action.label || '优先采购复核';
    actionTone = 'danger';
    actionDetail = action.reason || '存在需要优先复核的风险信号。';
  } else if (action && action.code !== 'continue_monitoring') {
    actionLabel = action.label || '核验数据覆盖范围';
    actionTone = action.priority === 'medium' ? 'warning' : 'neutral';
    actionDetail = action.reason || '请按建议核验当前数据覆盖。';
  } else if (action?.code === 'continue_monitoring' && snapshot.current && coverage?.status === 'complete') {
    actionLabel = action.label || '继续观察';
    actionTone = 'neutral';
    actionDetail = action.reason || '当前风险快照和所需数据覆盖均已核验。';
  } else if (action?.code === 'continue_monitoring') {
    actionDetail = '当前证据覆盖不足，请先核验风险快照和数据范围。';
  }

  const detail = [
    snapshot.detail,
    historical && snapshot.checkedAt ? `历史评估时间：${snapshot.checkedAt}` : '',
    coverage?.summary ? `当前数据覆盖：${coverage.summary}` : '当前数据覆盖：未知',
    actionDetail,
  ].filter(Boolean).join(' ');
  return { riskLabel, actionLabel, actionTone, detail };
}
