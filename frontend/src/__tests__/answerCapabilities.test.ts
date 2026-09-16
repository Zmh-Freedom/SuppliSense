import { describe, expect, it } from 'vitest';
import type { AgentAnswer } from '../types';
import { getAnswerCapability } from '../components/answerCapabilities';

function answer(summary: string, dimension = 'risk', factPath?: string): AgentAnswer {
  return {
    status: 'completed',
    summary,
    claims: [{
      claim_id: 'claim-1', entity_id: 'entity:test', dimension, statement: '测试判断',
      fact_path: factPath, evidence_refs: [], confidence: 0.9, validation_status: 'supported', validation_reasons: [],
    }],
    limitations: [], action_proposals: [], action_receipts: [], evidence_refs: [],
  };
}

describe('answer capability routing', () => {
  it.each([
    ['已完成财务分析', 'financial', undefined, 'financial'],
    ['已完成舆情分析', 'sentiment', undefined, 'sentiment'],
    ['已完成合规筛查', 'compliance', undefined, 'compliance'],
    ['已完成 ESG 评估', 'esg', undefined, 'esg'],
    ['已完成风险趋势预测', 'risk', 'risk_prediction.probability', 'prediction'],
    ['已完成综合风险分析', 'risk', 'risk_score', 'risk'],
    ['已完成司法风险分析', 'risk', 'risk_detail.lawsuit_count', 'legal'],
    ['已完成经营风险分析', 'business_risk', undefined, 'business'],
    ['已完成主体核验', 'identity_review', undefined, 'identity'],
    ['已完成寻源候选整理', 'sourcing', undefined, 'sourcing'],
    ['已生成风险报告', 'report', undefined, 'report'],
  ])('routes %s to %s', (summary, dimension, factPath, expected) => {
    expect(getAnswerCapability(answer(summary, dimension, factPath))).toBe(expected);
  });

  it('uses specialized claims even when the summary is generic', () => {
    expect(getAnswerCapability(answer('已完成供应商复核', 'financial', 'net_profit_growth'))).toBe('financial');
    expect(getAnswerCapability(answer('已完成供应商复核', 'risk_network', 'risk_network.related_count'))).toBe('network');
  });

  it('keeps generic answers as the fallback', () => {
    expect(getAnswerCapability(answer('已完成供应商复核'))).toBe('generic');
  });
});
