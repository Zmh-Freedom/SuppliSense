import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import StructuredAgentResult from '../components/StructuredAgentResult';
import type { AgentAnswer, AgentEvidenceRecord } from '../types';

const answer: AgentAnswer = {
  status: 'completed',
  summary: '已完成当前责任范围内供应商最近 1 个月的风险变化检查。',
  claims: [{
    claim_id: 'trend-1',
    entity_id: 'watchlist',
    dimension: 'risk_monitoring',
    statement: '可继续观察供应商 最近 1 个月风险变化：稳定',
    value: '稳定',
    evidence_refs: ['trend-evidence'],
    confidence: 0.9,
    validation_status: 'supported',
    validation_reasons: [],
  }],
  limitations: [],
  action_proposals: [],
  action_receipts: [],
  evidence_refs: ['trend-evidence'],
};

const evidence: AgentEvidenceRecord[] = [{
  evidence_id: 'trend-evidence',
  entity_id: 'watchlist',
  dimension: 'risk_monitoring',
  provider: 'analyze_watchlist_trend',
  source_type: 'domain_service_result',
  status: 'available',
  data_mode: 'formal',
  collected_at: '2026-09-16T00:00:00Z',
  facts: {
    period_months: 1,
    companies: [{
      company_name: '可继续观察供应商',
      trend: '稳定',
      latest_score: 93,
      latest_level: '低风险',
      previous_score: 93,
      delta: 0,
      trend_data_points: 2,
      trend_score_points: 2,
      comparison_text: '较周期初+0分',
      latest_checked_at: '2026-09-16',
    }],
  },
}];

describe('StructuredAgentResult', () => {
  it('shows current score and explains what stable means for watchlist trends', () => {
    render(<StructuredAgentResult answer={answer} evidence={evidence} />);

    expect(screen.getByText('当前风险评分')).toBeInTheDocument();
    expect(screen.getByText('93/100')).toBeInTheDocument();
    expect(screen.getByText('低风险')).toBeInTheDocument();
    expect(screen.getByText('基本稳定')).toBeInTheDocument();
    expect(screen.getByText('较周期初+0分')).toBeInTheDocument();
  });
});
