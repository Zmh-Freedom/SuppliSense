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

  it('shows a scope-level score overview instead of a generic detail instruction', () => {
    const overviewAnswer: AgentAnswer = {
      ...answer,
      summary: '已完成当前用户责任范围内 3 家供应商的本月风险概览；3 家已有安全评分，平均 76.7/100（分数越高风险越低）；风险等级：中风险 1 家、低风险 2 家；本月变化：恶化 1 家、基本稳定 1 家、仅有1次评分 1 家；需要优先复核 1 家。',
    };
    render(<StructuredAgentResult answer={overviewAnswer} evidence={evidence} />);

    expect(screen.getByText(/平均 76\.7\/100/)).toBeInTheDocument();
    expect(screen.queryByText(/可直接打开详情/)).not.toBeInTheDocument();
  });
});

it('renders operational facts without a formal risk classification', () => {
  const operational: AgentAnswer = {...answer, summary:'已取得质量指标。', limitations:['质量评级政策尚未确认'],
    claims: [{...answer.claims[0], claim_id:'quality-1',entity_id:'company:1',dimension:'quality',
      statement:'甲公司 合格率：95.2%',fact_path:'quality_rate',value:0.952,unit:'ratio',evidence_refs:['quality-e1']}],
    evidence_refs:['quality-e1']};
  const records: AgentEvidenceRecord[] = [{...evidence[0],evidence_id:'quality-e1',entity_id:'company:1',dimension:'quality',
    provider:'assess_operational_risk',facts:{quality_rate:0.952,quality_rate_unit:'ratio'}}];
  render(<StructuredAgentResult answer={operational} evidence={records} />);
  expect(screen.getAllByText('合格率').length).toBeGreaterThan(0);
  expect(screen.getByText('95.2%')).toBeInTheDocument();
  expect(screen.getByText('已取得指标，风险等级待确认')).toBeInTheDocument();
  expect(screen.queryByText('风险较低')).not.toBeInTheDocument();
});

it('consumes the actual Harness answer contract used by backend E2E', async () => {
  const fixture = (await import('./fixtures/operationalMetricAnswer.json')).default;
  render(<StructuredAgentResult answer={fixture.answer as AgentAnswer} evidence={fixture.evidence as AgentEvidenceRecord[]} />);
  expect(screen.getAllByText('95.2%').length).toBeGreaterThan(0);
  expect(screen.getAllByText('70.0%').length).toBeGreaterThan(0);
  expect(screen.getAllByText('已取得指标，风险等级待确认')).toHaveLength(2);
  expect(screen.queryByText(/可按正常流程推进/)).not.toBeInTheDocument();
  expect(screen.getAllByText(fixture.answer.summary).length).toBeGreaterThan(0);
  expect(screen.queryByText('风险较低')).not.toBeInTheDocument();
});

it('retains neutral historical scores for both subjects and dimensions', () => {
  const historical: AgentAnswer = {...answer, summary:'历史运营分析', limitations:[], claims:[
    {...answer.claims[0],claim_id:'old-quality',entity_id:'company:1',dimension:'quality',fact_path:'risk_score',value:4.8,statement:'原质量风险分4.8'},
    {...answer.claims[0],claim_id:'old-delivery',entity_id:'company:2',dimension:'delivery',fact_path:'risk_score',value:90,statement:'原交付风险分90'},
  ]};
  const before = JSON.stringify(historical);
  render(<StructuredAgentResult answer={historical} evidence={[]} />);
  expect(screen.getAllByText('历史规则待核验')).toHaveLength(2);
  expect(screen.getByText('4.8/100')).toBeInTheDocument();
  expect(screen.getByText('90/100')).toBeInTheDocument();
  expect(JSON.stringify(historical)).toBe(before);
});

it('shows the operational source gap instead of implying a detected risk signal', () => {
  const gap: AgentAnswer = {...answer,status:'needs_review',claims:[],evidence_refs:[],
    summary:'已完成甲公司的质量/交付查询，但当前没有可验证的指标数据，当前未覆盖，暂不评级。',
    limitations:['来源指标单位、分母和范围契约尚未确认']};
  render(<StructuredAgentResult answer={gap} evidence={[]} />);
  expect(screen.getAllByText(gap.summary).length).toBeGreaterThan(0);
  expect(screen.getByText('当前未覆盖')).toBeInTheDocument();
  expect(screen.queryByText('发现信号，需人工复核')).not.toBeInTheDocument();
});

it('does not create a green action signal from a completed historical score', () => {
  const historical: AgentAnswer = {...answer,status:'completed',summary:'历史运营分析',limitations:[],
    claims:[{...answer.claims[0],dimension:'delivery',fact_path:'riskScore',value:90,statement:'历史90分',evidence_refs:[]}]};
  render(<StructuredAgentResult answer={historical} evidence={[]} />);
  expect(screen.queryByText('可继续推进')).not.toBeInTheDocument();
  expect(screen.queryByText('风险较低')).not.toBeInTheDocument();
  expect(screen.getAllByText('历史规则待核验').length).toBeGreaterThan(0);
});

it('does not present unavailable screening as completed or procurement-ready', () => {
  const unavailable: AgentEvidenceRecord = {
    ...evidence[0], evidence_id: 'screening-unavailable', dimension: 'compliance',
    provider: 'check_sanctions', source_type: 'screening_query', status: 'unavailable',
    facts: { screening_status: 'unavailable', coverage_status: 'insufficient' },
  };
  const completedCompliance: AgentAnswer = {
    ...answer, status: 'completed', summary: '已完成供应商名单筛查，未发现风险。', claims: [], evidence_refs: [],
    action_proposals: [{ action_type: 'continue', label: '可按正常流程推进', reason: '旧筛查工具未发现命中' }],
  };
  render(<StructuredAgentResult answer={completedCompliance} evidence={[unavailable]} />);

  expect(screen.getAllByText('名单核验状态未知').length).toBeGreaterThan(0);
  expect(screen.queryByText('已完成供应商名单筛查，未发现风险。')).not.toBeInTheDocument();
  expect(screen.queryByText('可按正常流程推进并持续关注指标变化。')).not.toBeInTheDocument();
  expect(screen.queryByText('可按正常流程推进')).not.toBeInTheDocument();
  expect(screen.getByText('名单核验后再确定采购动作')).toBeInTheDocument();
  expect(screen.getByText('需采购人员处理')).toBeInTheDocument();
});

it('keeps a possible name match in manual review even if the answer status is completed', () => {
  const possible: AgentEvidenceRecord = {
    ...evidence[0], evidence_id: 'screening-possible', dimension: 'compliance',
    provider: 'fixture-list', source_type: 'sanctions_list_match', status: 'available',
    facts: { screening_status: 'possible_match', match_type: 'possible', record_name: '同名主体有限公司' },
  };
  const completedCompliance: AgentAnswer = {
    ...answer, status: 'completed', summary: '名单筛查已完成。', claims: [], evidence_refs: [],
  };
  render(<StructuredAgentResult answer={completedCompliance} evidence={[possible]} />);

  expect(screen.getAllByText('疑似名单线索，待核验').length).toBeGreaterThan(0);
  expect(screen.queryByText('名单筛查已完成。')).not.toBeInTheDocument();
  expect(screen.queryByText('可按正常流程推进并持续关注指标变化。')).not.toBeInTheDocument();
  expect(screen.getByText('需采购人员处理')).toBeInTheDocument();
});

it('does not treat a legacy clean boolean as a current screening conclusion', () => {
  const legacyClaim = {
    ...answer.claims[0], claim_id: 'legacy-screening', dimension: 'compliance', fact_path: 'clean', value: true,
    statement: '虚构甲供应链有限公司 合规筛查结果：未命中风险记录', evidence_refs: ['legacy-screening-evidence'],
  };
  const legacyEvidence: AgentEvidenceRecord = {
    ...evidence[0], evidence_id: 'legacy-screening-evidence', dimension: 'compliance',
    provider: 'check_sanctions', source_type: 'screening_query', status: 'available', facts: { clean: true },
  };
  const completedCompliance: AgentAnswer = {
    ...answer, status: 'completed', summary: '已完成供应商名单筛查，未发现风险。', claims: [legacyClaim],
    evidence_refs: ['legacy-screening-evidence'],
  };
  render(<StructuredAgentResult answer={completedCompliance} evidence={[legacyEvidence]} />);

  expect(screen.getAllByText('名单核验状态未知').length).toBeGreaterThan(0);
  expect(screen.queryByText('未见风险信号')).not.toBeInTheDocument();
  expect(screen.queryByText('未命中风险记录')).not.toBeInTheDocument();
  expect(screen.queryByText('可继续推进')).not.toBeInTheDocument();
});

it('identifies the subject period and scope of each metric and distinguishes source time', () => {
  const c = {...answer.claims[0],entity_id:'company:1',dimension:'quality',fact_path:'quality_rate',value:0.952,unit:'ratio',evidence_refs:['scoped']};
  const scoped: AgentEvidenceRecord = {...evidence[0],evidence_id:'scoped',entity_id:'company:1',dimension:'quality',
    facts:{company_name:'甲公司',period:'2026-10',scope:{material_code:'M1',base:'P1'},quality_rate:0.952,quality_rate_unit:'ratio',observed_at:null}};
  render(<StructuredAgentResult answer={{...answer,claims:[c]}} evidence={[scoped]} />);
  expect(screen.getByText(/甲公司.*2026-10.*物料 M1.*基地 P1/)).toBeInTheDocument();
  expect(screen.getByText(/本次采集时间/)).toBeInTheDocument();
  expect(screen.getByText(/来源观察时间：未知/)).toBeInTheDocument();
  expect(screen.queryByText(/更新于/)).not.toBeInTheDocument();
});
