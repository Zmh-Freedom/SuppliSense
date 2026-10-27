import { useEffect, useState } from 'react';
import type { ApprovalData } from '../api';
import type { AgentInputUnderstanding, AgentWorkflowSnapshot } from '../types';

export type AgentStatus = 'running' | 'complete' | 'error';

export interface AgentWorkflowToolCall {
  tool: string;
  args: Record<string, unknown>;
  result?: unknown;
}

export interface AgentWorkflowState {
  thinking: string;
  plan: Array<{ tool: string; args: Record<string, unknown>; parallel?: boolean }> | null;
  agents: {
    selected: string[];
    reasoning: string;
    status: Record<string, AgentStatus>;
    descriptions?: Record<string, string>;
  } | null;
  toolCalls: AgentWorkflowToolCall[];
  answerStarted: boolean;
  approval: ApprovalData | null;
  approvalSubmitting: boolean;
  error?: string;
  done?: boolean;
  workflowStatus?: AgentWorkflowSnapshot;
  queryText?: string;
}

interface AgentWorkflowPanelProps {
  state: AgentWorkflowState;
  onApproval: (approved: boolean) => void;
}

type MilestoneStatus = 'pending' | 'running' | 'complete' | 'waiting' | 'error';
type JourneyKind = 'risk' | 'comparison' | 'sourcing';

const JOURNEYS: Record<JourneyKind, Array<{ label: string; action: string }>> = {
  risk: [
    { label: '确认分析对象', action: '正在确认供应商信息' },
    { label: '查询企业和风险信息', action: '正在查询企业和风险信息' },
    { label: '核对近期风险证据', action: '正在核对近期风险证据' },
    { label: '整理风险要点和来源', action: '正在整理风险要点和来源' },
  ],
  comparison: [
    { label: '确认对比对象', action: '正在确认对比对象' },
    { label: '收集双方相关数据', action: '正在收集双方相关数据' },
    { label: '对齐风险与业务差异', action: '正在对齐风险与业务差异' },
    { label: '整理对比结果和来源', action: '正在整理对比结果和来源' },
  ],
  sourcing: [
    { label: '确认采购条件', action: '正在确认采购条件' },
    { label: '搜索候选供应商', action: '正在搜索候选供应商' },
    { label: '核对交付与风险信息', action: '正在核对交付与风险信息' },
    { label: '整理候选对比和来源', action: '正在整理候选对比和来源' },
  ],
};

function statusText(status: MilestoneStatus): string {
  if (status === 'running') return '进行中';
  if (status === 'complete') return '已完成';
  if (status === 'waiting') return '待你处理';
  if (status === 'error') return '异常';
  return '待开始';
}

function journeyKind(queryText?: string): JourneyKind {
  if (/对比|比较|差异|区别|优劣|哪家更|哪个好/.test(queryText || '')) return 'comparison';
  if (/寻源|找.{0,8}供应商|推荐.{0,8}供应商|候选供应商|替代.{0,4}供应商|供应商推荐/.test(queryText || '')) return 'sourcing';
  return 'risk';
}

const INPUT_FIELD_LABELS: Record<string, string> = {
  target_supplier_names: '分析对象',
  analysis_dimensions: '分析维度',
  sourcing_requirement: '采购条件',
  time_range: '时间范围',
};

const INPUT_SOURCE_LABELS: Record<string, string> = {
  explicit: '本轮输入',
  explicit_alias: '本轮别名',
  alias_resolution: '名称别名解析',
  conversation_state: '会话上下文',
  pending_task: '待确认任务',
  merged_with_conversation_state: '与历史条件合并',
  explicit_override: '本轮修改',
};

const CAPABILITY_LABELS: Record<string, string> = {
  sourcing: '供应商寻源',
  risk: '综合风险',
  financial: '财务分析',
  business_risk: '经营风险',
  quality: '质量风险',
  delivery: '交付风险',
  esg: 'ESG',
  sentiment: '舆情分析',
  compliance: '合规分析',
  risk_trend: '风险趋势',
  risk_prediction: '风险预测',
  risk_network: '供应链关系',
  report: '报告生成',
  identity_review: '主体核验',
  company_profile: '工商资料',
};

function inputValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join('、');
  if (typeof value !== 'object' || value === null) return String(value ?? '');
  const labels: Record<string, string> = {
    category: '品类', product: '产品', specification: '规格', quantity: '数量',
    budget: '预算', region: '地区', supply_region: '供货地区', delivery: '交付',
    qualifications: '资质', optional_conditions: '偏好',
  };
  return Object.entries(value as Record<string, unknown>)
    .filter(([, item]) => item !== null && item !== undefined && item !== '')
    .map(([key, item]) => `${labels[key] || key}：${Array.isArray(item) ? item.join('、') : String(item)}`)
    .join('；');
}

function inputFieldRows(input: AgentInputUnderstanding): Array<[string, string, string]> {
  return Object.entries(INPUT_FIELD_LABELS)
    .flatMap(([key, label]) => {
      const value = input[key as keyof AgentInputUnderstanding];
      const rendered = inputValue(value);
      return rendered ? [[label, rendered, INPUT_SOURCE_LABELS[input.field_sources?.[key] || ''] || '本轮解析']] : [];
    });
}

function candidateMilestone(state: AgentWorkflowState): number {
  const workflow = state.workflowStatus;
  if (!workflow) return state.done ? 3 : state.toolCalls.length > 0 ? 1 : 0;
  if (['completed', 'needs_review', 'partial'].includes(workflow.status)) return 3;
  if (workflow.status === 'clarifying') return 0;
  if (workflow.status === 'waiting_approval') return 3;
  if (workflow.stage === 'decision') return 3;
  if (workflow.stage === 'evidence' || workflow.completedToolCount > 0 || state.toolCalls.some(call => call.result !== undefined)) return 2;
  if (workflow.stage === 'executing' || workflow.stage === 'planning' || state.plan || state.agents || state.toolCalls.length > 0) return 1;
  return 0;
}

function milestoneStatus(state: AgentWorkflowState, index: number, activeIndex: number): MilestoneStatus {
  const lifecycle = state.workflowStatus?.status;
  if (lifecycle === 'completed') return 'complete';
  if (lifecycle === 'needs_review' || lifecycle === 'partial') {
    if (index < 3) return 'complete';
    return 'waiting';
  }
  if (lifecycle === 'waiting_approval') {
    if (index < 3) return 'complete';
    return 'waiting';
  }
  if (lifecycle === 'clarifying') {
    return index === 0 ? 'waiting' : 'pending';
  }
  if (lifecycle === 'failed' || state.error) {
    if (index < activeIndex) return 'complete';
    return index === activeIndex ? 'error' : 'pending';
  }
  if (lifecycle === 'rejected' || lifecycle === 'stopped' || lifecycle === 'cancelled') {
    if (index < activeIndex) return 'complete';
    return index === activeIndex ? 'waiting' : 'pending';
  }
  if (state.done) return 'complete';
  if (index < activeIndex) return 'complete';
  return index === activeIndex ? 'running' : 'pending';
}

function lifecycleLabel(status?: string): string {
  const labels: Record<string, string> = {
    running: '正在处理',
    waiting_approval: '等待你确认',
    completed: '本轮查询已完成',
    partial: '部分结果待复核',
    needs_review: '需要人工复核',
    failed: '执行遇到问题',
    clarifying: '需要补充信息',
    stopped: '本轮已停止',
    cancelled: '本轮已取消',
    rejected: '操作已拒绝',
  };
  return labels[status || ''] || '正在处理';
}

function lifecycleIndicatorClass(status: string, isRunning: boolean): string {
  if (isRunning) return 'animate-pulse bg-blue-600';
  if (status === 'failed') return 'bg-red-600';
  if (['needs_review', 'partial', 'waiting_approval', 'clarifying', 'stopped', 'cancelled', 'rejected'].includes(status)) return 'bg-amber-500';
  return 'bg-emerald-600';
}

function currentAction(state: AgentWorkflowState, journey: JourneyKind, activeIndex: number): string {
  const lifecycle = state.workflowStatus?.status;
  const message = state.workflowStatus?.message?.trim();
  if (lifecycle === 'clarifying' || lifecycle === 'failed' || lifecycle === 'waiting_approval' || lifecycle === 'rejected' || lifecycle === 'stopped' || lifecycle === 'cancelled') {
    return message || lifecycleLabel(lifecycle);
  }
  if (lifecycle === 'needs_review' || lifecycle === 'partial') return '结果已整理，建议核对证据和引用来源';
  if (lifecycle === 'completed' || state.done) return '本轮分析已完成';
  const target = state.workflowStatus?.targetSuppliers[0];
  const action = JOURNEYS[journey][Math.min(activeIndex, 3)].action;
  return target ? `${action} · ${target}` : action;
}

function elapsedLabel(seconds: number): string {
  if (seconds < 60) return `已用时 ${seconds} 秒`;
  return `已用时 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function compactJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) || '无数据';
  } catch {
    return String(value);
  }
}

function agentLabel(agent: string): string {
  const labels: Record<string, string> = {
    sourcing: '寻源 Agent',
    risk: '风险 Agent',
    compliance: '合规 Agent',
    sentiment: '舆情 Agent',
  };
  return labels[agent] || agent;
}

function toolLabel(tool: string): string {
  const labels: Record<string, string> = {
    assess_risk: '综合风险核验',
    assess_business_risk: '交易与结算核验',
    assess_operational_risk: '运营风险核验',
    query_financials: '财务数据核验',
    sentiment_analysis: '舆情信息核验',
    check_sanctions: '合规信息核验',
    list_formal_suppliers: '正式供应商目录查询',
    search_suppliers: '历史供应商查询',
    discover_supplier_candidates: '外部候选寻源',
    investigate_supplier_monitoring: '监控对象调查',
    get_watchlist: '监控清单查询',
    analyze_watchlist_trend: '风险变化分析',
    get_monitor_review_queue: '待复核事项查询',
    add_to_watchlist: '加入风险监控',
  };
  return labels[tool] || '采购数据核验';
}

function loopExitLabel(reason: string): string {
  const labels: Record<string, string> = {
    all_tasks_processed: '已完成本轮核验',
    evidence_sufficient: '所需证据已覆盖',
    no_remediation_spec: '暂无可用的补充数据来源',
    iteration_budget_exhausted: '已完成允许范围内的补充查询',
    tool_budget_exhausted: '本轮查询数量已达上限',
    deadline_exceeded: '本轮查询超时，建议稍后重试',
    llm_budget_exhausted: '本轮分析次数已达上限',
  };
  return labels[reason] || '本轮核验已结束';
}

function sourceLabel(source: string): string {
  const labels: Record<string, string> = {
    local_snapshot: '内部供应商数据',
    feishu_bitable: '飞书供应商数据',
    feishu_formal_supplier_snapshot: '飞书正式供应商主数据',
    formal_supplier_directory: '正式供应商目录',
    tianyancha: '天眼查企业数据',
  };
  return labels[source] || '已核验数据来源';
}

function stageLabel(stage?: string): string {
  const labels: Record<string, string> = {
    understand: '理解需求',
    planning: '任务规划',
    executing: 'Agent 执行',
    evidence: '证据汇总',
    decision: '风险决策',
    approval: '人工确认',
    completed: '已完成',
  };
  return labels[stage || ''] || stage || '执行中';
}

function approvalStatusLabel(status?: ApprovalData['status']): string {
  if (status === 'approved') return '已批准';
  if (status === 'rejected') return '已拒绝';
  if (status === 'failed') return '执行失败';
  if (status === 'submitting') return '提交中';
  return '待人工确认';
}

function approvalHeadingLabel(status?: ApprovalData['status']): string {
  if (!status || status === 'pending' || status === 'submitting') return '需要人工确认';
  return approvalStatusLabel(status);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function externalIdentityFromApprovalArgs(args: Record<string, unknown>): Record<string, unknown> | null {
  const direct = args.external_identity;
  if (isRecord(direct)) return direct;

  const target = args.target;
  if (isRecord(target) && isRecord(target.external_identity)) return target.external_identity;

  const pendingApprovals = args.pending_approvals;
  if (!Array.isArray(pendingApprovals)) return null;
  for (const approval of pendingApprovals) {
    if (!isRecord(approval) || !isRecord(approval.target)) continue;
    if (isRecord(approval.target.external_identity)) return approval.target.external_identity;
  }
  return null;
}

export default function AgentWorkflowPanel({ state, onApproval }: AgentWorkflowPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [approvalTarget, setApprovalTarget] = useState<'approve' | 'reject' | null>(null);
  const workflow = state.workflowStatus;
  const lifecycle = workflow?.status || (state.error ? 'failed' : state.done ? 'completed' : 'running');
  const journey = journeyKind(state.queryText);
  const candidateIndex = candidateMilestone(state);
  const runId = workflow?.runId;
  const [progress, setProgress] = useState({ runId, index: candidateIndex });
  if (progress.runId !== runId || candidateIndex > progress.index) {
    setProgress({ runId, index: candidateIndex });
  }
  const activeIndex = progress.runId === runId ? Math.max(progress.index, candidateIndex) : candidateIndex;
  const isRunning = lifecycle === 'running' && !state.done && !state.error;
  const action = currentAction(state, journey, activeIndex);
  const externalProfile = state.approval ? externalIdentityFromApprovalArgs(state.approval.args) : null;
  const isExternalIdentityConfirmation = Boolean(externalProfile);

  useEffect(() => {
    if (!isRunning) return;
    const startedAt = Date.now();
    const updateElapsed = () => setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    const intervalId = window.setInterval(updateElapsed, 1000);
    return () => window.clearInterval(intervalId);
  }, [isRunning, runId]);

  const handleApproval = (approved: boolean) => {
    setApprovalTarget(approved ? 'approve' : 'reject');
    onApproval(approved);
  };

  return (
    <section className="w-full max-w-full overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] shadow-sm" aria-label="分析进度">
      <div className="space-y-4 p-4 sm:p-5">
        <div className="rounded-xl bg-[var(--color-code-bg)] px-4 py-3" role="status" aria-live="polite">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text)]">
              <span aria-hidden="true" className={`inline-block h-2.5 w-2.5 rounded-full ${lifecycleIndicatorClass(lifecycle, isRunning)}`} />
              {lifecycleLabel(lifecycle)}
            </p>
            {isRunning && <span className="text-xs text-[var(--color-text-secondary)]">{elapsedLabel(elapsedSeconds)}</span>}
          </div>
          <p className="mt-1 text-sm leading-6 text-[var(--color-text-secondary)]">{action}</p>
          {workflow?.targetSuppliers.length ? <p className="mt-1 text-xs text-[var(--color-text-secondary)]">分析对象：{workflow.targetSuppliers.join('、')}</p> : null}
        </div>

        <ol className="space-y-3" aria-label="分析进度里程碑">
          {JOURNEYS[journey].map((milestone, index) => {
            const status = milestoneStatus(state, index, activeIndex);
            return <li key={milestone.label} className="flex items-start gap-3" aria-label={`${milestone.label}：${statusText(status)}`}>
              <span aria-hidden="true" className={`mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold ${
                status === 'complete' ? 'border-emerald-300 bg-emerald-50 text-emerald-700' :
                  status === 'error' ? 'border-red-300 bg-red-50 text-red-700' :
                    status === 'waiting' ? 'border-amber-300 bg-amber-50 text-amber-700' :
                      status === 'running' ? 'border-blue-300 bg-blue-50 text-blue-700' :
                        'border-gray-200 bg-gray-50 text-gray-400'
              }`}>{status === 'complete' ? '✓' : status === 'running' ? '◎' : status === 'error' ? '!' : ''}</span>
              <span className="min-w-0 flex-1">
                <span className={`block text-sm ${status === 'pending' ? 'text-[var(--color-text-secondary)]' : 'font-medium text-[var(--color-text)]'}`}>{milestone.label}</span>
                {status === 'running' && index === 2 && (workflow?.completedToolCount || 0) > 0 ? <span className="mt-0.5 block text-xs text-[var(--color-text-secondary)]">已完成 {workflow?.completedToolCount} 项查询</span> : null}
                {status === 'waiting' && index === 3 && <span className="mt-0.5 block text-xs text-amber-800">待你复核</span>}
                {status === 'waiting' && lifecycle === 'clarifying' && index === 0 && <span className="mt-0.5 block text-xs text-amber-800">等待你补充信息</span>}
              </span>
            </li>;
          })}
        </ol>

        {lifecycle !== 'clarifying' && <p className="text-xs text-[var(--color-text-secondary)]">结果将提供依据，最终判断由你确认。</p>}
        <button type="button" onClick={() => setExpanded(value => !value)} aria-expanded={expanded} className="flex min-h-[44px] w-full items-center justify-between border-t border-[var(--color-border)] pt-3 text-left text-sm font-medium text-[var(--color-text)]">
          <span>{expanded ? '收起执行详情' : '查看执行详情'}</span><span aria-hidden="true" className="text-gray-400">{expanded ? '⌃' : '⌄'}</span>
        </button>
      </div>

      {expanded && (
        <div className="space-y-4 border-t border-[var(--color-border)] px-4 py-4">
          {workflow?.inputUnderstanding && <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-code-bg)] p-3" aria-label="本轮理解">
            <h3 className="text-xs font-semibold text-[var(--color-text)]">本轮理解</h3>
            {workflow.inputUnderstanding.interpretation_summary && <p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">{workflow.inputUnderstanding.interpretation_summary}</p>}
            <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
              {workflow.inputUnderstanding.task_type === 'sourcing' ? '供应商寻源' : workflow.inputUnderstanding.task_type === 'analysis' ? '分析任务' : '对话请求'}
              {workflow.inputUnderstanding.capability && workflow.inputUnderstanding.capability !== 'none' ? ` · ${CAPABILITY_LABELS[workflow.inputUnderstanding.capability] || workflow.inputUnderstanding.capability}` : ''}
            </p>
            {inputFieldRows(workflow.inputUnderstanding).length > 0 && <dl className="mt-2 space-y-2">
              {inputFieldRows(workflow.inputUnderstanding).map(([label, value, source]) => <div key={label} className="flex flex-wrap items-start justify-between gap-2 text-xs">
                <dt className="text-[var(--color-text-secondary)]">{label}</dt>
                <dd className="flex flex-wrap items-center justify-end gap-1.5 text-right text-[var(--color-text)]">
                  <span>{value}</span>
                  <span className="rounded-full bg-[var(--color-surface-hover)] px-2 py-0.5 text-[10px] text-[var(--color-text-secondary)]">{source}</span>
                </dd>
              </div>)}
            </dl>}
            {workflow.inputUnderstanding.ambiguities?.length ? <ul className="mt-2 list-inside list-disc space-y-1 text-xs text-amber-700" aria-label="理解中的歧义">
              {workflow.inputUnderstanding.ambiguities.map((ambiguity, index) => <li key={`${index}-${ambiguity}`}>{ambiguity}</li>)}
            </ul> : null}
          </div>}
          {workflow && <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-code-bg)] p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs font-semibold text-[var(--color-text)]">当前状态：{lifecycleLabel(workflow.status)}</p>
              <span className="rounded-full bg-[var(--color-surface-hover)] px-2 py-0.5 text-[11px] text-[var(--color-text-secondary)]">{stageLabel(workflow.stage)}</span>
            </div>
            <p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">{workflow.message}</p>
            {workflow.targetSuppliers.length > 0 && <p className="mt-1 text-[11px] text-[var(--color-text-secondary)]">目标：{workflow.targetSuppliers.join('、')}</p>}
            {(workflow.sources.length > 0 || workflow.toolCallCount > 0) && <p className="mt-1 text-[11px] text-[var(--color-text-secondary)]">来源：{workflow.sources.length > 0 ? workflow.sources.map(sourceLabel).join('、') : '已核验数据'} · 已完成 {workflow.completedToolCount}/{workflow.toolCallCount} 项</p>}
            {workflow.evidenceStatus && <p className="mt-1 text-[11px] text-[var(--color-text-secondary)]">证据：{workflow.evidenceStatus}</p>}
            {workflow.loopExitReason && <p className="mt-1 text-[11px] text-[var(--color-text-secondary)]">本轮结果：{loopExitLabel(workflow.loopExitReason)}</p>}
          </div>}

          {state.thinking && <p className="truncate text-xs text-gray-500" aria-live="polite">{state.thinking}</p>}

          {state.plan && state.plan.length > 0 && (
            <details className="rounded-xl border border-[var(--color-border)] bg-[var(--color-code-bg)] p-3">
              <summary className="cursor-pointer text-xs font-medium text-[var(--color-text)]">任务计划（{state.plan.length} 项）</summary>
              <div className="mt-2 max-h-40 space-y-1 overflow-auto text-xs text-gray-500">
                {state.plan.map((step, index) => <p key={`${step.tool}-${index}`} className="truncate">{index + 1}. {toolLabel(step.tool)}{step.parallel ? ' · 并行' : ''}</p>)}
              </div>
            </details>
          )}

          {state.agents && state.agents.selected.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-semibold text-[var(--color-text)]">Agent 执行</h3>
              {state.agents.reasoning && <p className="text-[11px] text-gray-500">{state.agents.reasoning}</p>}
              <div className="grid gap-2 sm:grid-cols-2">
                {state.agents.selected.map(agent => {
                  const status = state.agents?.status[agent] || 'running';
                  return <div key={agent} className="flex min-w-0 items-center gap-2 rounded-lg border border-[var(--color-border)] px-3 py-2 text-xs" aria-label={`${agentLabel(agent)}：${statusText(status)}`}>
                    <span aria-hidden="true" className={status === 'complete' ? 'text-emerald-600' : status === 'error' ? 'text-red-600' : 'text-blue-600'}>{status === 'complete' ? '✓' : status === 'error' ? '!' : '…'}</span>
                    <span className="min-w-0 flex-1 truncate font-medium text-[var(--color-text)]">{agentLabel(agent)}</span>
                    <span className="shrink-0 text-gray-500">{statusText(status)}</span>
                  </div>;
                })}
              </div>
            </div>
          )}

          {state.toolCalls.length > 0 && (
            <div className="rounded-xl border border-[var(--color-border)]">
              <button type="button" onClick={() => setDetailsOpen(value => !value)} aria-expanded={detailsOpen} className="flex min-h-[44px] w-full items-center justify-between px-3 text-left text-xs font-medium text-[var(--color-text)]">
                <span>工具与证据摘要（{state.toolCalls.length}）</span><span aria-hidden="true">{detailsOpen ? '⌃' : '⌄'}</span>
              </button>
              {detailsOpen && <div className="max-h-64 space-y-2 overflow-auto border-t border-[var(--color-border)] p-3">
                {state.toolCalls.map((call, index) => <details key={`${call.tool}-${index}`} className="min-w-0 rounded-lg bg-[var(--color-code-bg)] p-2">
                  <summary className="cursor-pointer truncate text-xs text-[var(--color-text)]">{toolLabel(call.tool)}{call.result !== undefined ? ' · 已返回结果' : ' · 执行中'}</summary>
                  <pre className="mt-2 max-w-full overflow-x-auto whitespace-pre-wrap break-words text-[11px] text-gray-500">{compactJson({ args: call.args, result: call.result })}</pre>
                </details>)}
              </div>}
            </div>
          )}
        </div>
      )}

      {state.approval && (
        <div className="mx-4 mb-4 space-y-3 rounded-xl border border-amber-300 bg-amber-50/70 p-3" aria-label="人工审批">
              <div className="flex items-start gap-2">
                <span aria-hidden="true" className="mt-0.5 text-amber-700">!</span>
                <div className="min-w-0 space-y-1">
                  <h3 className="text-sm font-semibold text-amber-950">{approvalHeadingLabel(state.approval.status)}</h3>
                  <p className="break-words text-xs text-amber-900">{state.approval.message}</p>
                  {isExternalIdentityConfirmation && externalProfile ? <div className="mt-2 space-y-1 rounded-lg border border-amber-200 bg-white/70 p-3 text-xs text-amber-950">
                    <p className="font-semibold">请确认外部企业主体</p>
                    <p>企业名称：{String(externalProfile.company_name || state.approval.args.company_name || '未提供')}</p>
                    <p>统一社会信用代码：{String(externalProfile.unified_social_credit_code || '未提供')}</p>
                    <p>登记状态：{String(externalProfile.registration_status || '未提供')}</p>
                    <p>法定代表人：{String(externalProfile.legal_person || '未提供')}</p>
                    <p>资料来源：{String(externalProfile.source || '外部企业资料')}</p>
                    <p className="pt-1 text-[11px] text-amber-800">批准后将创建或复用正式主体，并加入风险监控清单。</p>
                  </div> : <>
                    <p className="break-words font-mono text-[11px] text-amber-800">动作：{state.approval.tool}</p>
                    <pre className="max-h-32 max-w-full overflow-auto whitespace-pre-wrap break-words text-[11px] text-amber-800">目标/参数：{compactJson(state.approval.args)}</pre>
                  </>}
                </div>
              </div>
              {state.approval.status === 'approved' ? <p className="text-[11px] text-emerald-800">该操作已获批准，执行结果见本轮回答。</p> :
                state.approval.status === 'rejected' ? <p className="text-[11px] text-red-800">该操作已拒绝，系统未执行写入。</p> :
                  state.approval.status === 'failed' ? <p className="text-[11px] text-red-800">该操作执行失败，请查看工作流异常并重试。</p> : <>
                    <p className="text-[11px] text-amber-900">影响：批准后将执行上述动作；拒绝则停止该写操作。</p>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" disabled={state.approvalSubmitting || state.approval.status === 'submitting'} onClick={() => handleApproval(true)} className="min-h-[44px] rounded-lg bg-emerald-600 px-4 text-xs font-medium text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60">
                        {state.approvalSubmitting && (approvalTarget === 'approve' || approvalTarget === null) ? (isExternalIdentityConfirmation ? '确认提交中…' : '批准提交中…') : isExternalIdentityConfirmation ? '确认主体并加入监控' : '批准动作'}
                      </button>
                      <button type="button" disabled={state.approvalSubmitting || state.approval.status === 'submitting'} onClick={() => handleApproval(false)} className="min-h-[44px] rounded-lg border border-gray-300 bg-white px-4 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60">
                        {state.approvalSubmitting && (approvalTarget === 'reject' || approvalTarget === null) ? '拒绝提交中…' : '拒绝动作'}
                      </button>
                    </div>
                  </>}
        </div>
      )}

      {state.error && <p role="alert" className="mx-4 mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">工作流异常：{state.error}</p>}
    </section>
  );
}
