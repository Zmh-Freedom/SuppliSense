import { describe, expect, it } from 'vitest';
import { monitorEvidencePresentation, screeningEvidenceReview, screeningPresentation } from '../evidencePresentation';
import { queryKeys } from '../query-keys';
import type { MonitorTarget, ScreeningReadResult, ScreeningStatus } from '../types';

const screening = (status: ScreeningStatus): ScreeningReadResult => ({
  schema_version: 1,
  company_name: '虚构甲供应链有限公司',
  entity_id: 'company-fixture-a',
  scope: '采购组织范围',
  screening_status: status,
  coverage: {
    status: 'complete',
    required_sources: ['fixture-list'],
    sources: [{
      source: 'fixture-list',
      required: true,
      scope: '采购组织范围',
      validity: { status: 'empty' },
      evidence: {
        observed_at: '2026-10-22T00:00:00Z',
        collected_at: '2026-10-22T00:05:00Z',
      },
    }],
  },
  matches: [],
  domestic_findings: [],
  limitations: [],
  checked_at: '2026-10-23T00:00:00Z',
});

const target = (overrides: Partial<MonitorTarget> = {}): MonitorTarget => ({
  monitor_target_id: 'monitor-1',
  target_type: 'formal_supplier',
  identity_status: 'verified',
  company_name: '虚构甲供应链有限公司',
  risk_score: 82,
  risk_level: '低风险',
  data_coverage: {
    status: 'partial',
    summary: '3/5 个数据域可用',
    dimensions: [{
      key: 'risk_snapshot',
      label: '风险快照',
      status: 'pending',
      detail: '历史评估依据已过期，当前待核验。',
      facts: { checked_at: '2025-01-01T00:00:00Z' },
    }],
  },
  ...overrides,
});

describe('screeningPresentation', () => {
  it('keeps a failed read neutral and unknown', () => {
    expect(screeningPresentation(undefined, 'error')).toEqual({
      label: '核验状态未知',
      tone: 'neutral',
      detail: expect.stringContaining('当前无法读取'),
    });
  });

  it('shows the scope and keeps source observation time separate from collection time', () => {
    const result = screeningPresentation(screening('no_match_in_scope'), 'ready');

    expect(result.label).toBe('范围内未发现匹配');
    expect(result.detail).toContain('筛查范围：采购组织范围');
    expect(result.detail).toContain('来源观察时间：2026-10-22T00:00:00Z');
    expect(result.detail).toContain('采集时间：2026-10-22T00:05:00Z');
    expect(result.detail).not.toContain('整体正常');
  });

  it.each([
    ['possible_match', '疑似名单线索，待核验', 'warning'],
    ['confirmed_match', '名单命中，待人工复核', 'danger'],
    ['stale', '依据已过期，需重新核验', 'warning'],
    ['partial', '部分来源已核验', 'warning'],
    ['unavailable', '核验状态未知', 'neutral'],
  ] as const)('renders %s without an overall clean label', (status, label, tone) => {
    const result = screeningPresentation(screening(status), 'ready');

    expect(result.label).toBe(label);
    expect(result.tone).toBe(tone);
    expect(result.label).not.toMatch(/正常|无风险/);
  });
});

describe('screeningEvidenceReview', () => {
  const record = (
    screeningStatus: string,
    overrides: Partial<import('../types').AgentEvidenceRecord> = {},
  ): import('../types').AgentEvidenceRecord => ({
    evidence_id: 'screening-evidence',
    entity_id: 'company-1',
    dimension: 'compliance',
    provider: 'screening-fixture',
    source_type: 'sanctions_list',
    status: 'available',
    data_mode: 'formal',
    collected_at: '2026-10-27T00:00:00Z',
    facts: { validity_status: 'available', screening_status: screeningStatus },
    ...overrides,
  });

  it.each(['confirmed_match', 'no_match_in_scope'])(
    'does not use synthetic %s as a current result', (status) => {
      const result = screeningEvidenceReview([
        record(status, { data_mode: 'synthetic' }),
      ]);

      expect(result?.status).toBe('unavailable');
      expect(result?.label).toBe('名单核验状态未知');
    },
  );

  it('keeps a formal available screening result', () => {
    expect(screeningEvidenceReview([record('confirmed_match')])?.status).toBe('confirmed_match');
  });

  it('does not let an unavailable record claim a confirmed match', () => {
    const result = screeningEvidenceReview([
      record('confirmed_match', { status: 'unavailable' }),
    ]);

    expect(result?.status).toBe('unavailable');
  });
});

describe('monitorEvidencePresentation', () => {
  it('preserves a historical low score while marking the current assessment unknown', () => {
    const result = monitorEvidencePresentation(target({
      next_action: { code: 'continue_monitoring', label: '继续观察', priority: 'low', reason: '旧动作' },
    }));

    expect(result.riskLabel).toContain('历史评估');
    expect(result.riskLabel).toContain('82/100');
    expect(result.riskLabel).toContain('当前待核验');
    expect(result.actionLabel).toBe('核验状态未知');
    expect(result.actionTone).toBe('warning');
    expect(result.detail).toContain('当前待核验');
  });

  it('keeps a historical high-risk review urgent and explains the historical boundary', () => {
    const result = monitorEvidencePresentation(target({
      risk_score: 24,
      risk_level: '高风险',
      next_action: { code: 'review', label: '优先采购复核', priority: 'high', reason: '历史风险需重新核验' },
    }));

    expect(result.riskLabel).toContain('历史评估');
    expect(result.actionLabel).toBe('优先采购复核');
    expect(result.actionTone).toBe('danger');
    expect(result.detail).toContain('历史');
  });

  it('does not turn a missing action into green continued monitoring', () => {
    const result = monitorEvidencePresentation(target({ next_action: undefined }));

    expect(result.actionLabel).toBe('核验状态未知');
    expect(result.actionTone).toBe('warning');
  });

  it('allows continue monitoring only with complete current risk evidence', () => {
    const result = monitorEvidencePresentation(target({
      data_coverage: {
        status: 'complete',
        summary: '5/5 个数据域可用',
        dimensions: [{ key: 'risk_snapshot', label: '风险快照', status: 'available', detail: '当前有效' }],
      },
      next_action: { code: 'continue_monitoring', label: '继续观察', priority: 'low', reason: '当前证据有效' },
    }));

    expect(result.riskLabel).toBe('低风险 82/100');
    expect(result.actionLabel).toBe('继续观察');
    expect(result.actionTone).toBe('neutral');
  });
});

it('defines the supplier compliance query key with the backend route identity', () => {
  expect(queryKeys.supplierComplianceStatus('supplier-1')).toEqual(['supplier', 'compliance-status', 'supplier-1']);
});
