import { useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { useSourcingRiskRun } from '../hooks/useSourcingRiskRun';
import { queryKeys } from '../query-keys';
import type { SourcingRiskCandidate, SourcingRiskDecision, SourcingRiskRequirement } from '../types';
import SourcingRiskApprovalCard from './SourcingRiskApprovalCard';
import SourcingRiskCandidateCard from './SourcingRiskCandidateCard';
import AgentExecutionTrace from './AgentExecutionTrace';

const STAGES = [
  ['CREATED', '创建任务'], ['CLARIFYING', '等待需求澄清'], ['POLICY_LOCKED', '锁定规则'],
  ['LOCAL_SEARCHING', '检索本地候选'], ['EXTERNAL_REVIEW', '审核外部候选'], ['IDENTITY_RESOLVING', '核验企业主体'],
  ['IDENTITY_REVIEW', '企业主体人工复核'], ['INVESTIGATING', '调查风险证据'], ['EVIDENCE_REVIEW', '证据人工复核'],
  ['SCORING', '形成候选决策'], ['READY_FOR_REVIEW', '人工复核'], ['ACTION_PENDING', '等待操作审批'],
  ['ACTION_EXECUTING', '执行批准操作'], ['COMPLETED', '已完成'], ['PARTIAL', '部分完成'],
  ['NEEDS_REVIEW', '需要人工复核'], ['ACTION_FAILED', '操作执行失败'], ['FAILED', '任务失败'], ['CANCELLED', '已取消'],
] as const;

const STATUS_LABELS: Record<string, string> = Object.fromEntries(STAGES);

const GROUP_TITLES: Record<string, string> = {
  recommended: '推荐供应商',
  alternative: '备选供应商',
  needs_review: '需要复核',
  rejected: '不建议采用',
};

const REASON_CODE_LABELS: Record<string, string> = {
  capability_match: '能力匹配',
  category_match: '品类匹配',
  historical_supplier: '历史合作',
  risk_evidence: '风险证据',
  identity_verified: '主体已核验',
  identity_pending: '主体待核验',
  insufficient_evidence: '证据不足',
};

function candidateId(candidate: SourcingRiskCandidate): string {
  return String(candidate.id ?? candidate.candidate_id ?? candidate.company_id ?? candidate.supplier_id ?? candidate.supplier_name ?? candidate.name ?? 'candidate');
}

function candidateName(candidate: SourcingRiskCandidate): string {
  return candidate.supplier_name ?? candidate.name ?? '未命名候选企业';
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? '处理中';
}

function IdentityReviewCard({ runId, version, candidates }: { runId: string; version: number; candidates: SourcingRiskCandidate[] }) {
  const queryClient = useQueryClient();
  const [resolutions, setResolutions] = useState<Record<string, string>>({});
  const reviewCandidates = candidates.filter(candidate => candidate.identity_review || candidate.identity_status !== 'exact');
  const resolve = useMutation({
    mutationFn: () => api.post(`/agent-runs/${encodeURIComponent(runId)}/identity-resolution`, { expected_version: version, resolutions }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.agentRunDetail(runId) }),
  });
  const canSubmit = reviewCandidates.length > 0 && reviewCandidates.every(candidate => resolutions[candidateId(candidate)]);

  return (
    <section className="bg-amber-50 border border-amber-200 rounded-2xl p-5 space-y-4">
      <div>
        <h3 className="font-semibold text-amber-900">请确认企业主体</h3>
        <p className="text-xs text-amber-800 mt-1">主体未确认前不会给出供应商推荐。</p>
      </div>
      {reviewCandidates.map(candidate => {
        const id = candidateId(candidate);
        const options = candidate.identity_candidates ?? [];
        return <div key={id} className="bg-white/70 rounded-xl p-3 space-y-2"><p className="text-sm font-medium text-[var(--color-text)]">{candidateName(candidate)}</p>{options.length > 0 ? <select aria-label={`${candidateName(candidate)}主体`} value={resolutions[id] ?? ''} onChange={event => setResolutions(current => ({ ...current, [id]: event.target.value }))} className="w-full text-sm rounded-lg border border-amber-200 px-3 py-2 bg-white"><option value="">选择匹配的企业主体</option>{options.map(option => <option key={option.company_id} value={option.company_id}>{option.legal_name ?? option.name ?? option.company_id}</option>)}</select> : <p className="text-xs text-amber-800">暂无可确认主体，请补充企业信息后重试。</p>}</div>;
      })}
      <button type="button" disabled={!canSubmit || resolve.isPending} onClick={() => resolve.mutate()} className="text-sm rounded-xl bg-amber-700 text-white px-4 py-2 disabled:opacity-50">提交主体确认</button>
      {resolve.isError && <p className="text-xs text-red-600">主体确认提交失败，请刷新后重试。</p>}
    </section>
  );
}

const CLARIFICATION_LABELS: Record<string, { label: string; placeholder: string }> = {
  category: { label: '采购品类', placeholder: '例如：制动系统、电子元器件' },
  specification: { label: '物料或规格', placeholder: '例如：后轮制动鼓、IP67 工业摄像头' },
};

function ClarificationCard({
  runId,
  version,
  requirement,
  missingFields,
}: {
  runId: string;
  version: number;
  requirement: SourcingRiskRequirement;
  missingFields: string[];
}) {
  const queryClient = useQueryClient();
  const fields = missingFields.length > 0 ? missingFields : ['category', 'specification'];
  const [answers, setAnswers] = useState<Record<string, string>>(() => Object.fromEntries(
    fields.map(field => [field, String(requirement[field] ?? '')]),
  ));
  const clarify = useMutation({
    mutationFn: () => api.post(`/agent-runs/${encodeURIComponent(runId)}/clarification`, {
      expected_version: version,
      answers,
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.agentRunDetail(runId) }),
  });
  const canSubmit = fields.every(field => String(answers[field] ?? '').trim());

  return (
    <section className="bg-amber-50 border border-amber-200 rounded-2xl p-5 space-y-4">
      <div>
        <h3 className="font-semibold text-amber-900">请补充寻源条件</h3>
        <p className="text-xs text-amber-800 mt-1">已有条件不足以安全比较供应商，补充后将继续当前任务。</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {fields.map(field => {
          const config = CLARIFICATION_LABELS[field] ?? { label: field, placeholder: '请输入采购条件' };
          return (
            <label key={field} className="text-xs text-amber-900">
              <span className="mb-1 block font-medium">{config.label}</span>
              <input
                value={answers[field] ?? ''}
                onChange={event => setAnswers(current => ({ ...current, [field]: event.target.value }))}
                placeholder={config.placeholder}
                className="w-full rounded-lg border border-amber-200 px-3 py-2 text-sm bg-white text-[var(--color-text)]"
              />
            </label>
          );
        })}
      </div>
      <button type="button" disabled={!canSubmit || clarify.isPending} onClick={() => clarify.mutate()} className="text-sm rounded-xl bg-amber-700 text-white px-4 py-2 disabled:opacity-50">
        {clarify.isPending ? '继续处理中…' : '继续寻源'}
      </button>
      {clarify.isError && <p className="text-xs text-red-600">提交失败，请刷新任务后重试。</p>}
    </section>
  );
}

function StageTimeline({ status }: { status: string }) {
  const currentIndex = STAGES.findIndex(([stage]) => stage === status);
  if (currentIndex < 0) return <p className="text-xs text-amber-700">未知阶段：{status}</p>;
  return <ol className="flex flex-wrap gap-2 text-xs text-[var(--color-text-secondary)]">{STAGES.map(([stage, label], index) => <li key={stage} aria-current={index === currentIndex ? 'step' : undefined} className={`rounded-full px-2.5 py-1 ${index <= currentIndex ? 'bg-[var(--color-primary-bg)] text-white' : 'bg-[var(--color-surface-hover)]'}`}>{label}</li>)}</ol>;
}

function DecisionGroup({ title, decisions, candidates }: { title: string; decisions: SourcingRiskDecision[]; candidates: SourcingRiskCandidate[] }) {
  const candidateByCompany = new Map(candidates.map(candidate => [String(candidate.company_id ?? candidateId(candidate)), candidate]));
  return <section className="space-y-3"><h3 className="text-sm font-semibold text-[var(--color-text-secondary)]">{title}</h3>{decisions.map((decision, index) => { const candidate = candidateByCompany.get(String(decision.company_id ?? decision.candidate_id)) ?? { supplier_name: decision.company_id ?? '候选供应商' }; return <div key={`${decision.company_id ?? decision.candidate_id ?? index}`} className="space-y-2"><SourcingRiskCandidateCard candidate={candidate} /><div className="flex flex-wrap gap-2 text-xs text-[var(--color-text-secondary)]">{decision.final_score != null && <span>综合评分 {decision.final_score.toFixed(1)}</span>}{decision.confidence != null && <span>证据置信度 {(decision.confidence * 100).toFixed(0)}%</span>}{decision.reason_codes?.map(code => <span key={code} className="px-1.5 py-0.5 rounded bg-[var(--color-surface-hover)]">{REASON_CODE_LABELS[code] ?? code}</span>)}</div></div>; })}</section>;
}

export default function SourcingRiskWorkbench({ initialRunId }: { initialRunId?: string }) {
  const [requirementText, setRequirementText] = useState('');
  const [activeRunId, setActiveRunId] = useState(initialRunId);
  const { data: run, isLoading, error, createRun, traceEvents } = useSourcingRiskRun(activeRunId);
  const isIdentityReview = run?.status === 'IDENTITY_REVIEW' || run?.next_action === 'identity_review_required';
  const isClarifying = run?.status === 'CLARIFYING' || run?.next_action === 'clarification_required';
  const decisionsByGroup = useMemo(() => {
    const groups = new Map<string, SourcingRiskDecision[]>();
    for (const decision of run?.decisions ?? []) groups.set(decision.group, [...(groups.get(decision.group) ?? []), decision]);
    return groups;
  }, [run?.decisions]);
  const proposals = (run?.proposals ?? run?.action_proposals ?? []).filter(
    proposal => proposal.action_type === 'add_watchlist',
  );
  const openChatForCandidate = (candidate: SourcingRiskCandidate, action: 'risk' | 'watchlist') => {
    const name = candidateName(candidate);
    const prompt = action === 'risk'
      ? `请核验候选供应商“${name}”的主体，并继续进行风险检查`
      : `请将候选供应商“${name}”加入风险监控清单，先完成主体核验并在写入前请求审批`;
    window.location.assign(`/chat?q=${encodeURIComponent(prompt)}`);
  };
  const runId = run?.id ?? run?.run_id ?? activeRunId;

  const submitRequirement = () => {
    const value = requirementText.trim();
    if (!value || createRun.isPending) return;
    createRun.mutate({ requirement_text: value }, { onSuccess: created => setActiveRunId(created.id ?? created.run_id) });
  };

  return <div className="space-y-6">
    <section className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 space-y-3">
      <div><h2 className="text-lg font-bold text-[var(--color-text)]">寻源风险工作台</h2><p className="text-xs text-[var(--color-text-secondary)] mt-1">本地优先检索、主体核验与证据驱动的供应商决策。</p></div>
      <div className="flex gap-2"><input value={requirementText} onChange={event => setRequirementText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') submitRequirement(); }} placeholder="例如：采购工业摄像头，IP67，华东交付" className="flex-1 text-sm rounded-xl border border-[var(--color-border)] px-3 py-2 bg-[var(--color-input-bg)]" /><button type="button" onClick={submitRequirement} disabled={!requirementText.trim() || createRun.isPending} className="text-sm rounded-xl bg-[var(--color-primary-bg)] text-white px-4 py-2 disabled:opacity-50">创建任务</button></div>
      {createRun.isError && <p className="text-xs text-red-500">创建任务失败，请重试。</p>}
    </section>
    {isLoading && <p className="text-sm text-[var(--color-text-secondary)]">正在加载任务…</p>}
    {error && <p className="text-sm text-red-500">任务加载失败，请稍后重试。</p>}
    {run && runId && <>
      <section className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-5 space-y-3"><div className="flex flex-wrap justify-between gap-2"><div><h3 className="font-semibold text-[var(--color-text)]">{run.requirement.requirement_text}</h3>{run.requirement.category && <p className="text-xs text-[var(--color-text-secondary)] mt-1">品类：{run.requirement.category}</p>}</div><span className="text-xs rounded-full px-2 py-1 bg-[var(--color-surface-hover)]">{statusLabel(run.status)}</span></div><StageTimeline status={run.status} /></section>
      <AgentExecutionTrace events={traceEvents} />
      {isClarifying ? <ClarificationCard runId={runId} version={run.version} requirement={run.requirement} missingFields={run.missing_fields ?? []} /> : isIdentityReview ? <IdentityReviewCard runId={runId} version={run.version} candidates={run.candidates ?? []} /> : <>
        {(run.candidates?.length ?? 0) > 0 && <section className="space-y-3"><h3 className="text-sm font-semibold text-[var(--color-text-secondary)]">候选与证据</h3>{run.candidates?.map(candidate => <SourcingRiskCandidateCard key={candidateId(candidate)} candidate={candidate} evidence={run.evidence_by_company_id?.[String(candidate.company_id)]} onContinueRisk={() => openChatForCandidate(candidate, 'risk')} onAddToWatchlist={() => openChatForCandidate(candidate, 'watchlist')} />)}</section>}
        {[...decisionsByGroup.entries()].map(([group, decisions]) => <DecisionGroup key={group} title={GROUP_TITLES[group] ?? group} decisions={decisions} candidates={run.candidates ?? []} />)}
        {proposals.length > 0 && <section className="space-y-3"><h3 className="text-sm font-semibold text-[var(--color-text-secondary)]">操作审批</h3>{proposals.map(proposal => <SourcingRiskApprovalCard key={proposal.id} proposal={proposal} runId={runId} runVersion={run.version} />)}</section>}
      </>}
    </>}
  </div>;
}
