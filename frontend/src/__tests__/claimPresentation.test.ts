import { describe, expect, it } from 'vitest';
import { presentRiskClaim } from '../claimPresentation';
import type { AgentClaim, AgentEvidenceRecord } from '../types';

const claim: AgentClaim = { claim_id: 'c1', entity_id: 'company:1', dimension: 'quality',
  fact_path: 'quality_rate', value: 0.952, unit: 'ratio', statement: '原始事实正文',
  evidence_refs: ['e1'], confidence: 0.85, validation_status: 'supported', validation_reasons: [] };
const record: AgentEvidenceRecord = { evidence_id: 'e1', entity_id: 'company:1', dimension: 'quality',
  provider: 'assess_operational_risk', source_type: 'feishu_transaction_snapshot', status: 'available',
  data_mode: 'formal', collected_at: '2026-10-01T00:00:00Z',
  facts: { quality_rate: 0.952, quality_rate_unit: 'ratio' } };

describe('risk claim semantics', () => {
  it('shows a rate as a neutral percentage without a risk rating', () => {
    const result = presentRiskClaim(claim, [record], [claim], false)!;
    expect(result.metric).toBe('合格率'); expect(result.value).toBe('95.2%');
    expect(result.tone).toBe('neutral'); expect(result.label).toContain('风险等级待确认');
    expect(claim.statement).toBe('原始事实正文');
  });
  it.each([['quality',4.8], ['delivery',90]])('keeps historical %s risk scores neutral', (dimension,value) => {
    const c = {...claim, dimension: String(dimension), fact_path:'risk_score', value:Number(value), unit:undefined};
    expect(presentRiskClaim(c, [], [c], false)).toMatchObject({tone:'neutral', label:'历史规则待核验'});
    expect(presentRiskClaim(c, [], [c], false)!.value).toContain(String(value));
  });
  it('does not treat an unknown score as safety', () => {
    const c = {...claim, dimension:'new_dimension', fact_path:'risk_score', value:90};
    expect(presentRiskClaim(c, [], [c], false)).toMatchObject({tone:'neutral', label:'评分含义待核验'});
  });
  it('retains recognized company safety score direction', () => {
    const c = {...claim, dimension:'risk', fact_path:'risk_score', value:90, unit:undefined};
    const r = {...record, dimension:'risk', provider:'assess_risk', facts:{risk_score:90}};
    expect(presentRiskClaim(c, [r], [c], false)).toMatchObject({tone:'positive', label:'风险较低'});
    expect(presentRiskClaim(c, [], [c], false)!.tone).not.toBe('positive');
  });
  it.each(['stale','conflicting','unavailable'])('prioritizes %s evidence over green', status => {
    expect(presentRiskClaim(claim, [{...record,status}], [claim], false)!.tone).not.toBe('positive');
    expect(presentRiskClaim(claim, [{...record,status}], [claim], false)!.label).not.toContain('已取得指标');
  });
  it('rejects another entity and expired reference', () => {
    expect(presentRiskClaim(claim, [{...record,entity_id:'company:2'}], [claim], false)!.label).toContain('未覆盖');
    expect(presentRiskClaim(claim, [{...record,valid_until:'2020-01-01T00:00:00Z'}], [claim], false)!.label).toContain('更新');
  });
  it('does not pair risk levels across subjects or evidence', () => {
    const c = {...claim,dimension:'risk',fact_path:'risk_score',value:90,unit:undefined};
    const level = {...c,claim_id:'c2',fact_path:'risk_level',value:'高风险',entity_id:'company:2'};
    const r = {...record,dimension:'risk',provider:'assess_risk',facts:{risk_score:90}};
    expect(presentRiskClaim(c,[r],[c,level],false)!.label).toBe('风险较低');
    const same = {...level,entity_id:c.entity_id};
    expect(presentRiskClaim(c,[{...r,facts:{risk_score:90,risk_level:'高风险'}}],[c,same],false)!.tone).not.toBe('positive');
    expect(presentRiskClaim(c,[r],[c,{...same,evidence_refs:['different']}],false)!.label).toBe('风险较低');
  });
  it.each(['unsupported','partial','conflicting'] as const)('prioritizes %s validation', validation_status => {
    expect(presentRiskClaim({...claim,validation_status},[record],[claim],false)!.label).not.toContain('已取得指标');
  });
  it('does not reinterpret a legacy operational level', () => {
    expect(presentRiskClaim({...claim,fact_path:'risk_level',value:'low'},[record],[claim],false))
      .toMatchObject({tone:'neutral',label:'历史规则待核验'});
  });
});

it.each(['riskScore','risk.score','risk-score','risk.level'])('protects historical alias %s', fact_path => {
  const c = {...claim,dimension:'delivery',fact_path,value:90};
  expect(presentRiskClaim(c,[],[c],false)).toMatchObject({tone:'neutral',label:'历史规则待核验'});
});
