import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { useSourcingRiskRun } from '../hooks/useSourcingRiskRun';
import { queryKeys } from '../query-keys';
import type { SourcingRiskCandidate, SourcingRiskDecision, SourcingRiskEvidence, SourcingRiskRequirement } from '../types';
import SourcingRiskApprovalCard from './SourcingRiskApprovalCard';
import SourcingRiskCandidateCard from './SourcingRiskCandidateCard';
import AgentExecutionTrace from './AgentExecutionTrace';

const BUSINESS_PHASES = [
  { key: 'understand', label: '理解需求', statuses: ['CREATED', 'CLARIFYING'] },
  { key: 'discover', label: '检索候选', statuses: ['POLICY_LOCKED', 'LOCAL_SEARCHING', 'EXTERNAL_REVIEW'] },
  { key: 'verify', label: '核验主体与风险', statuses: ['IDENTITY_RESOLVING', 'IDENTITY_REVIEW', 'INVESTIGATING', 'EVIDENCE_REVIEW'] },
  { key: 'recommend', label: '形成寻源建议', statuses: ['SCORING', 'READY_FOR_REVIEW', 'NEEDS_REVIEW'] },
  { key: 'action', label: '等待采购动作', statuses: ['ACTION_PENDING', 'ACTION_EXECUTING', 'COMPLETED', 'PARTIAL', 'ACTION_FAILED'] },
] as const;

type StatusTone = 'info' | 'attention' | 'success' | 'danger' | 'neutral';

const STATUS_COPY: Record<string, { label: string; description: string; tone: StatusTone }> = {
  CREATED: { label: '正在理解需求', description: '已创建任务，正在解析采购条件。', tone: 'info' },
  CLARIFYING: { label: '等待补充条件', description: '补充缺少的采购条件后，任务会在原任务上继续。', tone: 'attention' },
  POLICY_LOCKED: { label: '规则已锁定', description: '筛选规则已固定，接下来会检索可比较的候选。', tone: 'info' },
  LOCAL_SEARCHING: { label: '正在检索候选', description: '正在从已有供应商资料中查找匹配候选。', tone: 'info' },
  EXTERNAL_REVIEW: { label: '正在补充外部候选', description: '本地候选不足，正在补充外部候选并标记待核验信息。', tone: 'attention' },
  IDENTITY_RESOLVING: { label: '正在核验主体', description: '正在确认候选企业对应的真实经营主体。', tone: 'info' },
  IDENTITY_REVIEW: { label: '等待确认主体', description: '请确认候选企业主体，确认前不会形成正式推荐。', tone: 'attention' },
  INVESTIGATING: { label: '正在核验风险', description: '正在检查候选供应商的风险证据和数据覆盖。', tone: 'info' },
  EVIDENCE_REVIEW: { label: '等待复核证据', description: '部分风险证据需要采购员复核后才能进入建议。', tone: 'attention' },
  SCORING: { label: '正在形成建议', description: '正在综合匹配条件、风险证据和主体状态。', tone: 'info' },
  READY_FOR_REVIEW: { label: '建议待查看', description: '候选与依据已准备好，请查看推荐理由和待确认项。', tone: 'success' },
  ACTION_PENDING: { label: '等待采购动作审批', description: '已有采购动作需要审批确认，批准前不会写入监控清单。', tone: 'attention' },
  ACTION_EXECUTING: { label: '正在执行采购动作', description: '已批准的采购动作正在执行，请等待回执。', tone: 'info' },
  COMPLETED: { label: '建议已生成', description: '寻源建议已完成，可以查看候选、依据并选择后续动作。', tone: 'success' },
  PARTIAL: { label: '部分结果可用', description: '部分数据源未完成，已有候选仍可查看，请结合覆盖状态判断。', tone: 'attention' },
  NEEDS_REVIEW: { label: '等待人工复核', description: '候选或风险证据需要采购员确认，完成后再决定是否采用。', tone: 'attention' },
  ACTION_FAILED: { label: '采购动作未完成', description: '采购动作执行失败，已获取的寻源结果仍会保留。', tone: 'danger' },
  FAILED: { label: '任务未完成', description: '任务未能完成，已获取的内容会保留；请刷新后查看最新状态。', tone: 'danger' },
  CANCELLED: { label: '任务已取消', description: '本次寻源已停止，已获取的内容会保留，也可以重新发起任务。', tone: 'neutral' },
};

const NEXT_ACTION_LABELS: Record<string, string> = {
  clarification_required: '补充缺少的采购条件',
  identity_review_required: '确认候选企业主体',
  evidence_review_required: '复核风险证据',
  review_required: '查看候选与寻源依据',
  external_discovery_required: '等待外部候选补充',
};

const TERMINAL_RUN_STATUSES = new Set(['COMPLETED', 'PARTIAL', 'NEEDS_REVIEW', 'ACTION_FAILED', 'FAILED', 'CANCELLED']);

const STATUS_TONE_CLASSES: Record<StatusTone, string> = {
  info: 'border-blue-200 bg-blue-50 text-blue-800',
  attention: 'border-amber-200 bg-amber-50 text-amber-900',
  success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  danger: 'border-red-200 bg-red-50 text-red-800',
  neutral: 'border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]',
};

const GROUP_TITLES: Record<string, string> = {
  recommended: '推荐供应商',
  alternative: '备选供应商',
  needs_review: '需要复核',
  rejected: '不建议采用',
  unclassified: '候选供应商',
};

function candidateId(candidate: SourcingRiskCandidate): string {
  return String(candidate.id ?? candidate.candidate_id ?? candidate.company_id ?? candidate.supplier_id ?? candidate.supplier_name ?? candidate.name ?? 'candidate');
}

function candidateName(candidate: SourcingRiskCandidate): string {
  return candidate.supplier_name ?? candidate.name ?? '未命名候选企业';
}

function IdentityReviewCard({ runId, version, candidates, onEditRequirement }: { runId: string; version: number; candidates: SourcingRiskCandidate[]; onEditRequirement: () => void }) {
  const queryClient = useQueryClient();
  const [resolutions, setResolutions] = useState<Record<string, string>>({});
  const reviewCandidates = candidates.filter(candidate => candidate.identity_review || candidate.identity_status !== 'exact');
  const resolve = useMutation({
    mutationFn: () => api.post(`/agent-runs/${encodeURIComponent(runId)}/identity-resolution`, { expected_version: version, resolutions }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.agentRunDetail(runId) }),
  });
  const canSubmit = reviewCandidates.length > 0 && reviewCandidates.every(candidate => resolutions[candidateId(candidate)]);
  const hasSelectableCandidate = reviewCandidates.some(candidate => (candidate.identity_candidates ?? []).length > 0);
  const hasUnresolvedCandidates = reviewCandidates.some(candidate => (candidate.identity_candidates ?? []).length === 0);
  const hasNoCandidates = reviewCandidates.length === 0;

  return (
    <section className="bg-amber-50 border border-amber-200 rounded-2xl p-5 space-y-4">
      <div>
        <h3 className="font-semibold text-amber-900">{hasNoCandidates || !hasSelectableCandidate ? '暂未找到可确认主体' : '请确认企业主体'}</h3>
        <p className="text-xs text-amber-800 mt-1">主体未确认前不会给出供应商推荐。{hasNoCandidates || !hasSelectableCandidate ? '可以补充更明确的企业信息，或返回修改采购需求。' : ''}</p>
      </div>
      {hasNoCandidates && <p className="rounded-xl border border-dashed border-amber-200 bg-white/70 px-3 py-3 text-xs leading-5 text-amber-900">本次主体核验没有返回可选择的企业候选，当前任务不会被解释为已完成推荐。</p>}
      {reviewCandidates.map(candidate => {
        const id = candidateId(candidate);
        const options = candidate.identity_candidates ?? [];
        return <div key={id} className="bg-white/70 rounded-xl p-3 space-y-2"><p className="text-sm font-medium text-[var(--color-text)]">{candidateName(candidate)}</p>{options.length > 0 ? <select aria-label={`${candidateName(candidate)}主体`} value={resolutions[id] ?? ''} onChange={event => setResolutions(current => ({ ...current, [id]: event.target.value }))} className="w-full text-sm rounded-lg border border-amber-200 px-3 py-2 bg-white"><option value="">选择匹配的企业主体</option>{options.map(option => <option key={option.company_id} value={option.company_id}>{option.legal_name ?? option.name ?? option.company_id}</option>)}</select> : <p className="text-xs text-amber-800">暂无可确认主体，请补充企业信息后重试。</p>}</div>;
      })}
      <div className="flex flex-wrap gap-2">
        {hasSelectableCandidate && <button type="button" disabled={!canSubmit || hasUnresolvedCandidates || resolve.isPending} onClick={() => resolve.mutate()} className="min-h-[44px] text-sm rounded-xl bg-amber-700 text-white px-4 py-2 disabled:opacity-50">提交主体确认</button>}
        <button type="button" onClick={onEditRequirement} className="min-h-[44px] text-sm rounded-xl border border-amber-300 bg-white px-4 py-2 text-amber-900 hover:bg-amber-100">返回修改需求</button>
      </div>
      {hasUnresolvedCandidates && hasSelectableCandidate && <p className="text-xs text-amber-800">仍有候选缺少可确认主体，全部候选完成核验后才能提交。</p>}
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

function ProcurementProgress({ status, nextAction, errorCode }: { status: string; nextAction?: string | null; errorCode?: string | null }) {
  const currentIndex = BUSINESS_PHASES.findIndex(phase => phase.statuses.some(stage => stage === status));
  const statusCopy = STATUS_COPY[status] ?? { label: '任务处理中', description: '任务正在处理，请稍后刷新查看最新状态。', tone: 'info' as StatusTone };
  const nextActionLabel = nextAction ? NEXT_ACTION_LABELS[nextAction] : null;
  const isTerminalIssue = ['FAILED', 'ACTION_FAILED', 'CANCELLED'].includes(status);
  const currentAction = nextActionLabel
    ?? (status === 'IDENTITY_REVIEW' ? '确认主体后继续寻源' : status === 'CLARIFYING' ? '补充条件后继续寻源' : status === 'COMPLETED' || status === 'PARTIAL' ? '查看寻源建议和证据' : '系统正在处理');

  return (
    <section className="space-y-4" aria-labelledby="procurement-progress-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 id="procurement-progress-heading" className="text-sm font-semibold text-[var(--color-text)]">采购进度</h4>
          <p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">{statusCopy.description}</p>
        </div>
        <span role="status" className={`rounded-full border px-2.5 py-1 text-xs font-medium ${STATUS_TONE_CLASSES[statusCopy.tone]}`}>{statusCopy.label}</span>
      </div>
      <ol className="grid gap-2 sm:grid-cols-5" aria-label="采购业务进度">
        {BUSINESS_PHASES.map((phase, index) => {
          const isComplete = currentIndex >= 0 && index < currentIndex;
          const isCurrent = currentIndex >= 0 && index === currentIndex;
          return (
            <li key={phase.key} aria-current={isCurrent ? 'step' : undefined} className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-xs ${isCurrent ? 'border-[var(--color-primary-bg)] bg-[var(--color-primary-bg)]/10 text-[var(--color-text)]' : isComplete ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]'}`}>
              <span aria-hidden="true" className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${isCurrent ? 'bg-[var(--color-primary-bg)] text-white' : isComplete ? 'bg-emerald-600 text-white' : 'bg-[var(--color-surface)] text-[var(--color-text-secondary)]'}`}>{isComplete ? '✓' : index + 1}</span>
              <span className="leading-4">{phase.label}</span>
            </li>
          );
        })}
      </ol>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--color-text-secondary)]">
        <span><span className="font-medium text-[var(--color-text)]">当前动作：</span>{currentAction}</span>
        {errorCode && <span><span className="font-medium text-[var(--color-text)]">处理提示：</span>请刷新任务获取最新结果</span>}
      </div>
      {isTerminalIssue && <div role="status" className={`rounded-xl border px-3 py-2 text-xs leading-5 ${STATUS_TONE_CLASSES[statusCopy.tone]}`}>{statusCopy.description}</div>}
    </section>
  );
}

function candidateKeys(candidate: SourcingRiskCandidate): string[] {
  return [candidate.company_id, candidate.candidate_id, candidate.id].filter((value): value is string => Boolean(value)).map(String);
}

function decisionKeys(decision: SourcingRiskDecision): string[] {
  return [decision.company_id, decision.candidate_id].filter((value): value is string => Boolean(value)).map(String);
}

function candidateRecommendationRows(candidates: SourcingRiskCandidate[], decisions: SourcingRiskDecision[]): Array<{ candidate: SourcingRiskCandidate; decision?: SourcingRiskDecision }> {
  const decisionByKey = new Map<string, SourcingRiskDecision>();
  decisions.forEach(decision => decisionKeys(decision).forEach(key => decisionByKey.set(key, decision)));
  const attached = new Set<SourcingRiskDecision>();
  const rows = candidates.map(candidate => {
    const decision = candidateKeys(candidate).map(key => decisionByKey.get(key)).find((item): item is SourcingRiskDecision => Boolean(item));
    if (decision) attached.add(decision);
    return { candidate, decision };
  });
  decisions.forEach(decision => {
    if (!attached.has(decision)) rows.push({ candidate: { supplier_name: decision.company_id ?? '候选供应商' }, decision });
  });
  const order: Record<string, number> = { recommended: 0, alternative: 1, needs_review: 2, rejected: 3 };
  return rows.sort((left, right) => (order[left.decision?.group ?? 'unclassified'] ?? 4) - (order[right.decision?.group ?? 'unclassified'] ?? 4));
}

function CandidateResults({ candidates, decisions, evidenceByCompanyId, onContinueRisk, onAddToWatchlist }: { candidates: SourcingRiskCandidate[]; decisions: SourcingRiskDecision[]; evidenceByCompanyId?: Record<string, SourcingRiskEvidence[]>; onContinueRisk: (candidate: SourcingRiskCandidate) => void; onAddToWatchlist: (candidate: SourcingRiskCandidate) => void }) {
  const rows = candidateRecommendationRows(candidates, decisions);
  const groups = new Map<string, Array<{ candidate: SourcingRiskCandidate; decision?: SourcingRiskDecision }>>();
  rows.forEach(row => {
    const group = row.decision?.group ?? 'unclassified';
    groups.set(group, [...(groups.get(group) ?? []), row]);
  });
  return <section className="space-y-4" aria-labelledby="sourcing-candidates-heading">
    <div><h3 id="sourcing-candidates-heading" className="text-sm font-semibold text-[var(--color-text)]">候选与依据</h3><p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">每个候选只展示一次；先看相关产品和推荐理由，再确认待核验信息与后续动作。</p></div>
    {[...groups.entries()].map(([group, groupRows]) => <section key={group} className="space-y-3" aria-labelledby={`candidate-group-${group}`}><h4 id={`candidate-group-${group}`} className="text-sm font-semibold text-[var(--color-text-secondary)]">{GROUP_TITLES[group] ?? group} <span className="font-normal">（{groupRows.length}）</span></h4>{groupRows.map(({ candidate, decision }) => <SourcingRiskCandidateCard key={`${candidateId(candidate)}-${group}`} candidate={candidate} decision={decision} evidence={candidate.company_id ? evidenceByCompanyId?.[String(candidate.company_id)] : undefined} onContinueRisk={() => onContinueRisk(candidate)} onAddToWatchlist={() => onAddToWatchlist(candidate)} />)}</section>)}
  </section>;
}

function CandidateEmptyState({ status, errorCode }: { status: string; errorCode?: string | null }) {
  const isPartial = status === 'PARTIAL';
  const isFailed = status === 'FAILED' || status === 'ACTION_FAILED';
  const isProcessing = !['COMPLETED', 'PARTIAL', 'NEEDS_REVIEW', 'ACTION_FAILED', 'FAILED', 'CANCELLED'].includes(status);
  const title = isPartial ? '部分来源完成，暂未形成可用候选' : isFailed ? '任务未完成，暂不判断为无候选' : isProcessing ? '候选仍在准备中' : '暂未找到可比较候选';
  const description = isPartial
    ? '已有结果会保留；请刷新任务查看后续候选，或调整采购条件后重新寻源。'
    : isFailed
      ? '请刷新任务查看最新状态；不要把任务失败理解为供应商不存在。'
      : isProcessing
        ? '系统仍在检索、核验或整理证据，候选结果准备好后会显示在这里。'
        : '当前筛选条件下没有可展示候选，可以补充或放宽品类、规格和交付条件后重新寻源。';
  return <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5" aria-label="候选结果为空"><h3 className="text-sm font-semibold text-[var(--color-text)]">{title}</h3><p className="mt-1 text-sm leading-6 text-[var(--color-text-secondary)]">{description}</p>{errorCode && <p className="mt-2 text-xs text-[var(--color-text-secondary)]">任务仍保留，可通过“刷新任务”重新读取结果。</p>}</section>;
}

export default function SourcingRiskWorkbench({ initialRunId }: { initialRunId?: string }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [requirementText, setRequirementText] = useState('');
  const [activeRunId, setActiveRunId] = useState(initialRunId ?? searchParams.get('run') ?? undefined);
  const { data: run, isLoading, isFetching, error, createRun, refresh, traceEvents } = useSourcingRiskRun(activeRunId);
  const isIdentityReview = run?.status === 'IDENTITY_REVIEW' || run?.next_action === 'identity_review_required';
  const isClarifying = run?.status === 'CLARIFYING' || run?.next_action === 'clarification_required';
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
  const cancelRun = useMutation({
    mutationFn: () => {
      if (!runId || !run) return Promise.reject(new Error('当前任务不可用'));
      return api.post(`/agent-runs/${encodeURIComponent(runId)}/cancel`, { expected_version: run.version });
    },
    onSuccess: () => void refresh(),
  });
  const canCancel = Boolean(run && runId && !TERMINAL_RUN_STATUSES.has(run.status));

  const selectRun = (nextRunId?: string) => {
    setActiveRunId(nextRunId);
    const nextParams = new URLSearchParams(searchParams);
    if (nextRunId) nextParams.set('run', nextRunId);
    else nextParams.delete('run');
    setSearchParams(nextParams, { replace: true });
  };

  const submitRequirement = () => {
    const value = requirementText.trim();
    if (!value || createRun.isPending) return;
    createRun.mutate({ requirement_text: value }, { onSuccess: created => selectRun(created.id ?? created.run_id) });
  };

  const editRequirement = () => {
    if (run?.requirement.requirement_text) setRequirementText(run.requirement.requirement_text);
    selectRun();
  };

  return <div className="space-y-6">
    <section className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 space-y-3">
      <div>
        <h2 className="text-lg font-bold text-[var(--color-text)]">描述采购需求</h2>
        <p id="sourcing-requirement-help" className="text-xs text-[var(--color-text-secondary)] mt-1">告诉我物料、规格、交付区域或其他限制条件，系统会先给出候选和需要核验的事项。</p>
      </div>
      <div className="space-y-1.5">
        <label htmlFor="sourcing-requirement" className="text-xs font-medium text-[var(--color-text)]">采购需求</label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input id="sourcing-requirement" aria-describedby="sourcing-requirement-help" value={requirementText} onChange={event => setRequirementText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') submitRequirement(); }} placeholder="例如：采购工业摄像头，IP67，华东交付" className="min-h-[44px] flex-1 rounded-xl border border-[var(--color-border)] px-3 py-2 text-sm bg-[var(--color-input-bg)] focus:outline-none focus:ring-2 focus:ring-[var(--color-focus-ring)]" />
          <button type="button" onClick={submitRequirement} disabled={!requirementText.trim() || createRun.isPending} className="min-h-[44px] rounded-xl bg-[var(--color-primary-bg)] px-4 text-sm text-white transition-colors hover:bg-[var(--color-primary-hover)] disabled:opacity-50">{createRun.isPending ? '提交中…' : '开始寻源'}</button>
        </div>
      </div>
      {createRun.isError && <p className="text-xs text-red-500">创建任务失败，请重试。</p>}
    </section>
    {isLoading && <p className="text-sm text-[var(--color-text-secondary)]">正在加载任务…</p>}
    {error && <p className="text-sm text-red-500">任务加载失败，请稍后重试。</p>}
    {run && runId && <>
      <section className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-5 space-y-4" aria-label="当前寻源任务"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold text-[var(--color-text)]">{run.requirement.requirement_text}</h3>{run.requirement.category && <p className="text-xs text-[var(--color-text-secondary)] mt-1">品类：{run.requirement.category}</p>}<p className="mt-2 text-xs text-[var(--color-text-secondary)]">任务已保存到当前地址，刷新页面后可以继续查看结果或补充条件。</p></div><div className="flex flex-wrap gap-2"><button type="button" onClick={() => void refresh()} disabled={isFetching} className="min-h-[40px] rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-hover)] disabled:opacity-50">{isFetching ? '刷新中…' : '刷新任务'}</button>{canCancel && <button type="button" onClick={() => { if (window.confirm('确定停止当前寻源任务吗？已获取的结果会保留。')) cancelRun.mutate(); }} disabled={cancelRun.isPending} className="min-h-[40px] rounded-lg border border-red-200 px-3 text-xs text-red-700 transition-colors hover:bg-red-50 disabled:opacity-50">{cancelRun.isPending ? '停止中…' : '停止任务'}</button>}</div></div>{cancelRun.isError && <p role="alert" className="text-xs text-red-600">停止任务失败，任务仍在运行，请刷新后重试。</p>}<ProcurementProgress status={run.status} nextAction={run.next_action} errorCode={run.error_code} /></section>
      <details className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)]">
        <summary className="cursor-pointer list-none px-5 py-4 text-sm font-medium text-[var(--color-text)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] focus-visible:ring-inset">查看执行细节<span className="ml-2 text-xs font-normal text-[var(--color-text-secondary)]">技术追踪、数据范围与证据校验</span></summary>
        <div className="border-t border-[var(--color-border)] p-3"><AgentExecutionTrace events={traceEvents} /></div>
      </details>
      {isClarifying ? <ClarificationCard runId={runId} version={run.version} requirement={run.requirement} missingFields={run.missing_fields ?? []} /> : isIdentityReview ? <IdentityReviewCard runId={runId} version={run.version} candidates={run.candidates ?? []} onEditRequirement={editRequirement} /> : <>
        {(run.candidates?.length ?? 0) > 0 || (run.decisions?.length ?? 0) > 0 ? <CandidateResults candidates={run.candidates ?? []} decisions={run.decisions ?? []} evidenceByCompanyId={run.evidence_by_company_id} onContinueRisk={candidate => openChatForCandidate(candidate, 'risk')} onAddToWatchlist={candidate => openChatForCandidate(candidate, 'watchlist')} /> : <CandidateEmptyState status={run.status} errorCode={run.error_code} />}
        {proposals.length > 0 && <section className="space-y-3"><h3 className="text-sm font-semibold text-[var(--color-text-secondary)]">操作审批</h3>{proposals.map(proposal => <SourcingRiskApprovalCard key={proposal.id} proposal={proposal} runId={runId} runVersion={run.version} />)}</section>}
      </>}
    </>}
  </div>;
}
