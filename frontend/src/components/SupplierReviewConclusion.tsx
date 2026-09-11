import type { AgentAnswer, AgentEvidenceRecord } from '../types';
import { ReviewChecklist } from './ChatResultSections';

function ReviewList({ title, items, className, ordered = false }: { title: string; items: string[]; className: string; ordered?: boolean }) {
  if (items.length === 0) return null;
  return <article className={`rounded-xl border p-3.5 ${className}`}><h4 className="text-sm font-semibold">{title}</h4>{ordered ? <ol className="mt-2 space-y-2 text-sm leading-6">{items.map((item, index) => <li key={item} className="flex gap-2"><span aria-hidden="true" className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-white/70 text-[11px] font-semibold">{index + 1}</span><span>{item}</span></li>)}</ol> : <ul className="mt-2 space-y-1.5 text-sm leading-6">{items.map(item => <li key={item} className="flex gap-2"><span aria-hidden="true">•</span><span>{item}</span></li>)}</ul>}</article>;
}

export default function SupplierReviewConclusion({ answer, evidence, limitations, numericFact, reviewClaims }: {
  answer: AgentAnswer;
  evidence: AgentEvidenceRecord[];
  limitations: string[];
  numericFact: (facts: Record<string, unknown> | undefined, key: string) => number | null;
  reviewClaims: (answer: AgentAnswer, paths: string[]) => string[];
}) {
  const businessFacts = evidence.find(record => record.dimension === 'business_risk')?.facts;
  const netProfitGrowth = answer.claims.find(claim => claim.fact_path === 'net_profit_growth')?.value;
  const lawsuitCount = answer.claims.find(claim => claim.fact_path === 'risk_detail.lawsuit_count')?.value;
  const majorLawsuit = answer.claims.find(claim => claim.fact_path === 'risk_detail.major_lawsuit')?.value;
  const penaltyCount = answer.claims.find(claim => claim.fact_path === 'risk_detail.administrative_penalty_count')?.value;
  const financeMissing = limitations.some(item => item.includes('财务'));
  const settlementChange = numericFact(businessFacts, 'settlement_change_ratio');
  const missingMonths = numericFact(businessFacts, 'missing_month_count');
  const settlementWithoutReceipts = numericFact(businessFacts, 'settlement_without_receipts_month_count');
  const findings: string[] = [];
  const basis: string[] = [];
  const checks: string[] = [];
  const boundaries: string[] = [];
  if (typeof netProfitGrowth === 'number' && netProfitGrowth < 0) {
    findings.push('盈利指标出现下滑，需要复核是否影响后续履约与合作稳定性。');
    basis.push(...reviewClaims(answer, ['revenue_growth', 'net_profit_growth', 'debt_ratio', 'cash_flow']));
    checks.push('核对最近一期财报的报告期、下滑原因、现金流变化，以及供应商对在手订单的履约说明。');
  }
  if (financeMissing) {
    findings.push('财务信息覆盖不足，当前无法判断其财务变化。');
    basis.push('本轮未取得可用的财务正式证据；这不是低风险或高风险判断。');
    checks.push('核对本轮已覆盖的数据范围；财务维度未形成结论，不据此判断供应商经营状况。');
    boundaries.push('未取得财务数据时，系统不形成财务风险结论，也不将数据缺失判定为低风险或高风险。');
  }
  if (typeof lawsuitCount === 'number' && lawsuitCount > 0) {
    findings.push(`公开司法信息中有 ${Math.round(lawsuitCount)} 起诉讼记录，需要确认是否影响履约。`);
    basis.push(...reviewClaims(answer, ['risk_detail.lawsuit_count']));
    checks.push('核实诉讼案件当前状态、涉诉金额及是否影响供应商履约与持续供货。');
  }
  if (majorLawsuit === true) {
    findings.push('公开司法信息带有重大诉讼标记，需要人工核实。');
    basis.push(...reviewClaims(answer, ['risk_detail.major_lawsuit']));
    checks.push('确认重大诉讼标记对应的案件性质、进展和对供应商经营的实际影响。');
  }
  if (typeof penaltyCount === 'number' && penaltyCount > 0) {
    findings.push(`公开经营信息中有 ${Math.round(penaltyCount)} 条行政处罚记录，需要关注整改情况。`);
    basis.push(...reviewClaims(answer, ['risk_detail.administrative_penalty_count']));
    checks.push('核实行政处罚原因、整改状态以及是否影响相关产品或订单履约。');
  }
  if (settlementChange !== null && settlementChange <= -0.5) {
    findings.push('最新月实结算金额环比显著下降，需要核实交易变化原因。');
    basis.push(...reviewClaims(answer, ['settlement_change_ratio', 'latest_actual_settlement_amount', 'latest_received_record_count']));
    checks.push('向采购与财务核对该月是否存在结算跨月、退货冲销、暂停采购或订单调整，并保留对应单据。');
  }
  if ((missingMonths ?? 0) > 0 || (settlementWithoutReceipts ?? 0) > 0) {
    findings.push('采购交易连续性存在需要人工确认的信号。');
    basis.push(...reviewClaims(answer, ['missing_month_count', 'settlement_without_receipts_month_count']));
    checks.push('核对缺失月份是否为未导入、无交易或口径变更；对结算与收货记录不一致的月份逐笔查验。');
  }
  if (businessFacts) {
    boundaries.push('收货记录数仅表示源明细行数，不代表零件数量、送货批次或交付能力。');
    boundaries.push('结算变化只是复核信号，不能单独推断供应中断、付款逾期或供应商经营风险。');
  }
  if (findings.length === 0) return null;
  return <ReviewChecklist><section className="mt-4 border-t border-[var(--color-border)] px-4 pb-4 pt-4 sm:px-5" aria-label="采购复核结论">
    <div className="flex flex-wrap items-center justify-between gap-2"><div><div className="flex items-center gap-1.5"><span aria-hidden="true" className="text-[var(--color-primary-bg)]">③</span><p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-secondary)]">采购复核结论</p></div><h3 className="mt-1 text-base font-semibold text-[var(--color-text)]">发现问题、查看依据、明确下一步</h3></div><span className="rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-xs font-medium text-violet-700">需采购人员核实</span></div>
    <div className="mt-3 grid gap-3 lg:grid-cols-3"><ReviewList title="发现了什么" items={[...new Set(findings)]} className="border-amber-200 bg-amber-50/70 text-amber-950" /><ReviewList title="依据是什么" items={[...new Set(basis)]} className="border-sky-200 bg-sky-50/70 text-sky-950" /><ReviewList title="采购人员需要核实什么" items={[...new Set(checks)]} className="border-violet-200 bg-violet-50/70 text-violet-950" ordered /></div>
    {boundaries.length > 0 && <div className="mt-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-code-bg)]/55 p-3 text-xs leading-5 text-[var(--color-text-secondary)]"><p className="font-medium text-[var(--color-text)]">数据边界</p><ul className="mt-1.5 list-disc space-y-1 pl-4">{[...new Set(boundaries)].map(item => <li key={item}>{item}</li>)}</ul></div>}
  </section></ReviewChecklist>;
}
