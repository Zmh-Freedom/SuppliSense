import type { AgentAnswer, AgentEvidenceRecord } from '../types';
import { ReviewChecklist } from './ChatResultSections';
import { getAnswerCapability, getAnswerCapabilityMeta } from './answerCapabilities';

interface ReviewItem {
  id: string;
  kind: 'observed' | 'coverage';
  finding: string;
  basis: string;
  nextStep: string;
  boundaries: string[];
}

interface ReviewItemInput extends Omit<ReviewItem, 'basis'> {
  paths?: string[];
  explicitBasis?: string;
}

function createReviewItem(
  input: ReviewItemInput,
  answer: AgentAnswer,
  reviewClaims: (answer: AgentAnswer, paths: string[]) => string[],
): ReviewItem {
  const claims = reviewClaims(answer, input.paths || []);
  return {
    id: input.id,
    kind: input.kind,
    finding: input.finding,
    basis: input.explicitBasis || claims.join('；') || '本轮未取得该项可引用的结构化依据。',
    nextStep: input.nextStep,
    boundaries: [...new Set(input.boundaries)],
  };
}

export default function SupplierReviewConclusion({ answer, evidence, limitations, numericFact, reviewClaims }: {
  answer: AgentAnswer;
  evidence: AgentEvidenceRecord[];
  limitations: string[];
  numericFact: (facts: Record<string, unknown> | undefined, key: string) => number | null;
  reviewClaims: (answer: AgentAnswer, paths: string[]) => string[];
}) {
  const capability = getAnswerCapability(answer);
  if (capability === 'network') return null;
  const capabilityMeta = getAnswerCapabilityMeta(capability);
  const reviewLabel = capability === 'generic' ? '采购复核结论' : `${capabilityMeta.label}复核结论`;
  const reviewHeading = capability === 'generic' ? '发现问题、查看依据、明确下一步' : `发现${capabilityMeta.label}信号，查看依据并明确下一步`;
  const businessFacts = evidence.find(record => record.dimension === 'business_risk')?.facts;
  const netProfitGrowth = answer.claims.find(claim => claim.fact_path === 'net_profit_growth')?.value;
  const lawsuitCount = answer.claims.find(claim => claim.fact_path === 'risk_detail.lawsuit_count')?.value;
  const majorLawsuit = answer.claims.find(claim => claim.fact_path === 'risk_detail.major_lawsuit')?.value;
  const penaltyCount = answer.claims.find(claim => claim.fact_path === 'risk_detail.administrative_penalty_count')?.value;
  const financeMissing = limitations.some(item => item.includes('财务'));
  const settlementChange = numericFact(businessFacts, 'settlement_change_ratio');
  const missingMonths = numericFact(businessFacts, 'missing_month_count');
  const settlementWithoutReceipts = numericFact(businessFacts, 'settlement_without_receipts_month_count');
  const businessBoundaries = [
    '收货记录数仅表示源明细行数，不代表零件数量、送货批次或交付能力。',
    '结算变化只是复核信号，不能单独推断供应中断、付款逾期或供应商经营风险。',
  ];
  const reviewItems: ReviewItem[] = [];
  const addReviewItem = (input: ReviewItemInput) => reviewItems.push(createReviewItem(input, answer, reviewClaims));

  if (typeof netProfitGrowth === 'number' && netProfitGrowth < 0) {
    addReviewItem({
      id: 'net-profit-decline',
      kind: 'observed',
      finding: '盈利指标出现下滑，需要复核是否影响后续履约与合作稳定性。',
      paths: ['revenue_growth', 'net_profit_growth', 'debt_ratio', 'cash_flow'],
      nextStep: '核对最近一期财报的报告期、下滑原因、现金流变化，以及供应商对在手订单的履约说明。',
      boundaries: ['盈利指标变化本身不证明供应商无法履约，需结合现金流、订单和供应商说明确认。'],
    });
  }
  if (typeof lawsuitCount === 'number' && lawsuitCount > 0) {
    addReviewItem({
      id: 'lawsuit-count',
      kind: 'observed',
      finding: `公开司法信息中有 ${Math.round(lawsuitCount)} 起诉讼记录，需要确认是否影响履约。`,
      paths: ['risk_detail.lawsuit_count'],
      nextStep: '核实诉讼案件当前状态、涉诉金额及是否影响供应商履约与持续供货。',
      boundaries: ['公开诉讼记录不等于当前存在履约中断；需确认主体、案件状态和对供货的实际影响。'],
    });
  }
  if (majorLawsuit === true) {
    addReviewItem({
      id: 'major-lawsuit',
      kind: 'observed',
      finding: '公开司法信息带有重大诉讼标记，需要人工核实。',
      paths: ['risk_detail.major_lawsuit'],
      nextStep: '确认重大诉讼标记对应的案件性质、进展和对供应商经营的实际影响。',
      boundaries: ['重大诉讼标记仍需核实案件主体、当前进展和业务影响，不直接证明供货中断。'],
    });
  }
  if (typeof penaltyCount === 'number' && penaltyCount > 0) {
    addReviewItem({
      id: 'administrative-penalty-count',
      kind: 'observed',
      finding: `公开经营信息中有 ${Math.round(penaltyCount)} 条行政处罚记录，需要关注整改情况。`,
      paths: ['risk_detail.administrative_penalty_count'],
      nextStep: '核实行政处罚原因、整改状态以及是否影响相关产品或订单履约。',
      boundaries: ['行政处罚记录不一定与当前供货产品或订单相关，需核实主体、处罚事由和整改情况。'],
    });
  }
  if (settlementChange !== null && settlementChange <= -0.5) {
    addReviewItem({
      id: 'settlement-change',
      kind: 'observed',
      finding: '最新月实结算金额环比显著下降，需要核实交易变化原因。',
      paths: ['settlement_change_ratio', 'latest_actual_settlement_amount', 'latest_received_record_count'],
      nextStep: '向采购与财务核对该月是否存在结算跨月、退货冲销、暂停采购或订单调整，并保留对应单据。',
      boundaries: businessBoundaries,
    });
  }
  if ((settlementWithoutReceipts ?? 0) > 0) {
    addReviewItem({
      id: 'settlement-without-receipts',
      kind: 'observed',
      finding: `有 ${Math.round(settlementWithoutReceipts || 0)} 个月结算非零但未记录收货，需要核对数据口径。`,
      paths: ['settlement_without_receipts_month_count'],
      nextStep: '逐月核对结算与收货记录的业务口径、退货冲销及单据关联，确认数据差异原因。',
      boundaries: [
        '结算与收货记录不一致只说明记录需要核对，不足以判断是否发生供应中断。',
        businessBoundaries[0],
      ],
    });
  }
  if ((missingMonths ?? 0) > 0) {
    addReviewItem({
      id: 'missing-transaction-months',
      kind: 'coverage',
      finding: `最近 12 个月有 ${Math.round(missingMonths || 0)} 个月未取得交易快照，无法判断这些月份是否有交易。`,
      paths: ['missing_month_count'],
      nextStep: '核对缺失月份是未导入、范围外还是统计口径变更，并确认是否能补齐来源数据。',
      boundaries: ['缺少月度快照不代表没有交易，也不表示交付能力下降。'],
    });
  }
  if (financeMissing) {
    addReviewItem({
      id: 'financial-coverage',
      kind: 'coverage',
      finding: '财务信息覆盖不足，当前无法判断其财务变化。',
      explicitBasis: '本轮未取得可用的财务正式证据；这不是低风险或高风险判断。',
      nextStep: '核对本轮已覆盖的数据范围；财务维度未形成结论，不据此判断供应商经营状况。',
      boundaries: ['未取得财务数据不代表低风险或高风险，本轮不形成财务结论。'],
    });
  }
  if (reviewItems.length === 0) return null;

  const summaryItem = reviewItems.find(item => item.kind === 'observed') || reviewItems[0];
  const hasMoreDetails = reviewItems.length > 1 || summaryItem.boundaries.length > 0;
  return <ReviewChecklist><section className="mt-3 border-t border-[var(--color-border)] px-4 pb-3 pt-3 sm:px-5" aria-label={reviewLabel}>
    <div className="flex flex-wrap items-center justify-between gap-2"><div><p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-text-secondary)]">{reviewLabel}</p><h3 className="mt-1 text-sm font-semibold text-[var(--color-text)]">{reviewHeading}</h3></div><span className="rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-xs font-medium text-violet-700">需采购人员核实</span></div>
    <dl className="mt-3 grid gap-3 sm:grid-cols-3">
      <div><dt className="text-xs font-medium text-amber-800">发现</dt><dd className="mt-1 text-sm leading-5 text-[var(--color-text)]">{summaryItem.finding}{summaryItem.boundaries.length > 0 && <p className="mt-1 rounded-lg bg-[var(--color-code-bg)]/55 px-2 py-1.5 text-xs leading-5 text-[var(--color-text-secondary)]"><span className="font-medium text-[var(--color-text)]">业务边界：</span>{summaryItem.boundaries.join(' ')}</p>}</dd></div>
      <div><dt className="text-xs font-medium text-sky-800">结论依据</dt><dd className="mt-1 text-sm leading-5 text-[var(--color-text)]">{summaryItem.basis}</dd></div>
      <div><dt className="text-xs font-medium text-violet-800">下一步</dt><dd className="mt-1 text-sm leading-5 text-[var(--color-text)]">{summaryItem.nextStep}</dd></div>
    </dl>
    {hasMoreDetails && <details className="mt-2 border-t border-[var(--color-border)] pt-2">
      <summary className="min-h-8 cursor-pointer py-1 text-xs font-medium text-[var(--color-primary-bg)]">查看完整复核依据与检查项</summary>
      <ol className="mt-2 space-y-2">
        {reviewItems.map(item => <li key={item.id}><article className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-xs leading-5">
          <p><span className="font-semibold text-amber-800">发现：</span><span className="text-[var(--color-text)]">{item.finding}</span></p>
          <p className="mt-1"><span className="font-semibold text-sky-800">结论依据：</span><span className="text-[var(--color-text)]">{item.basis}</span></p>
          <p className="mt-1"><span className="font-semibold text-violet-800">下一步：</span><span className="text-[var(--color-text)]">{item.nextStep}</span></p>
          {item.boundaries.length > 0 && <p className="mt-1 text-[var(--color-text-secondary)]"><span className="font-semibold">业务边界：</span>{item.boundaries.join(' ')}</p>}
        </article></li>)}
      </ol>
    </details>}
  </section></ReviewChecklist>;
}
