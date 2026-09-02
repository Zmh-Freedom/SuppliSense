import { useState } from 'react';
import type { ApprovalData } from '../api';
import type { AgentWorkflowSnapshot } from '../types';

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
}

interface AgentWorkflowPanelProps {
  state: AgentWorkflowState;
  onApproval: (approved: boolean) => void;
}

type PhaseStatus = 'pending' | 'running' | 'complete' | 'error';

const PHASES = [
  { key: 'understand', label: '理解需求', icon: '◎' },
  { key: 'plan', label: '任务规划', icon: '≡' },
  { key: 'agents', label: 'Agent 执行', icon: '◇' },
  { key: 'evidence', label: '证据汇总', icon: '▤' },
  { key: 'decision', label: '风险决策', icon: '✓' },
] as const;

function statusText(status: PhaseStatus): string {
  if (status === 'running') return '进行中';
  if (status === 'complete') return '已完成';
  if (status === 'error') return '异常';
  return '待开始';
}

function phaseStatus(state: AgentWorkflowState, index: number): PhaseStatus {
  const lifecycle = state.workflowStatus?.status;
  const stageIndex: Record<string, number> = {
    understand: 0,
    planning: 1,
    executing: 2,
    evidence: 3,
    decision: 4,
    approval: 4,
    completed: 4,
  };
  const currentStage = state.workflowStatus?.stage;
  if (lifecycle === 'completed') return 'complete';
  if (lifecycle === 'failed') return index >= (stageIndex[currentStage || ''] ?? 2) ? 'error' : 'complete';
  if (lifecycle === 'partial') return index >= (stageIndex[currentStage || ''] ?? 3) ? 'error' : 'complete';
  if (lifecycle === 'waiting_approval') return index < 4 ? 'complete' : index === 4 ? 'running' : 'pending';
  if (lifecycle === 'clarifying') return index === 0 ? 'running' : 'pending';
  if (lifecycle === 'running' && currentStage && currentStage in stageIndex) {
    const currentIndex = stageIndex[currentStage];
    if (index < currentIndex) return 'complete';
    if (index === currentIndex) return 'running';
    return 'pending';
  }
  if (state.error && index >= 2) return 'error';
  if (index === 0) {
    if (state.thinking || state.plan || state.agents) return 'complete';
    return state.done ? 'complete' : 'running';
  }
  if (index === 1) {
    if (state.plan) return state.agents || state.toolCalls.length > 0 ? 'complete' : 'running';
    return state.agents || state.toolCalls.length > 0 ? 'complete' : 'pending';
  }
  if (index === 2) {
    if (state.agents) {
      const statuses = Object.values(state.agents.status);
      if (statuses.some(status => status === 'error')) return 'error';
      if (statuses.length > 0 && statuses.every(status => status === 'complete')) return 'complete';
      return 'running';
    }
    return state.toolCalls.length > 0 ? 'running' : 'pending';
  }
  if (index === 3) {
    if (state.toolCalls.some(call => call.result !== undefined)) {
      return state.approval || state.answerStarted ? 'complete' : 'running';
    }
    return state.toolCalls.length > 0 ? 'running' : 'pending';
  }
  if (state.error) return 'error';
  if (state.approval) return 'running';
  return state.done ? 'complete' : state.toolCalls.length > 0 ? 'running' : 'pending';
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

function lifecycleLabel(status: string): string {
  const labels: Record<string, string> = {
    running: '执行中',
    waiting_approval: '等待人工确认',
    completed: '已完成',
    partial: '部分完成',
    needs_review: '需人工复核',
    failed: '失败',
    clarifying: '等待澄清',
  };
  return labels[status] || status;
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

export default function AgentWorkflowPanel({ state, onApproval }: AgentWorkflowPanelProps) {
  const [expanded, setExpanded] = useState(true);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [approvalTarget, setApprovalTarget] = useState<'approve' | 'reject' | null>(null);

  const handleApproval = (approved: boolean) => {
    setApprovalTarget(approved ? 'approve' : 'reject');
    onApproval(approved);
  };

  return (
    <section className="w-full max-w-full overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] shadow-sm" aria-label="Agent 工作流">
      <button
        type="button"
        onClick={() => setExpanded(value => !value)}
        aria-expanded={expanded}
        className="flex min-h-[44px] w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <span className="flex min-w-0 items-center gap-2 text-sm font-semibold text-[var(--color-text)]">
          <span aria-hidden="true" className="text-[var(--color-primary-bg)]">◇</span>
          <span className="truncate">Agent 工作流</span>
          {state.approval && <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800">待人工确认</span>}
        </span>
        <span aria-hidden="true" className="shrink-0 text-lg text-gray-400">{expanded ? '⌃' : '⌄'}</span>
      </button>

      {expanded && (
        <div className="space-y-4 border-t border-[var(--color-border)] px-4 py-4">
          {state.workflowStatus && <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-code-bg)] p-3" role="status" aria-live="polite">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs font-semibold text-[var(--color-text)]">当前状态：{lifecycleLabel(state.workflowStatus.status)}</p>
              <span className="rounded-full bg-[var(--color-surface-hover)] px-2 py-0.5 text-[11px] text-[var(--color-text-secondary)]">{stageLabel(state.workflowStatus.stage)}</span>
            </div>
            <p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">{state.workflowStatus.message}</p>
            {state.workflowStatus.targetSuppliers.length > 0 && <p className="mt-1 text-[11px] text-[var(--color-text-secondary)]">目标：{state.workflowStatus.targetSuppliers.join('、')}</p>}
            {(state.workflowStatus.sources.length > 0 || state.workflowStatus.toolCallCount > 0) && <p className="mt-1 text-[11px] text-[var(--color-text-secondary)]">来源：{state.workflowStatus.sources.length > 0 ? state.workflowStatus.sources.join('、') : '工具执行结果'} · 工具 {state.workflowStatus.completedToolCount}/{state.workflowStatus.toolCallCount} 已返回</p>}
            {state.workflowStatus.evidenceStatus && <p className="mt-1 text-[11px] text-[var(--color-text-secondary)]">证据：{state.workflowStatus.evidenceStatus}</p>}
            {state.workflowStatus.loopExitReason && <p className="mt-1 text-[11px] text-[var(--color-text-secondary)]">Loop 退出：{state.workflowStatus.loopExitReason}</p>}
          </div>}
          <ol className="grid gap-2 sm:grid-cols-5" aria-label="Agent 阶段时间线">
            {PHASES.map((phase, index) => {
              const status = phaseStatus(state, index);
              return (
                <li key={phase.key} className="flex min-w-0 items-center gap-2 sm:block">
                  <span className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-sm font-semibold ${
                    status === 'complete' ? 'border-emerald-300 bg-emerald-50 text-emerald-700' :
                      status === 'error' ? 'border-red-300 bg-red-50 text-red-700' :
                        status === 'running' ? 'border-blue-300 bg-blue-50 text-blue-700' :
                          'border-gray-200 bg-gray-50 text-gray-400'
                  }`} aria-label={`${phase.label}：${statusText(status)}`}>{phase.icon}</span>
                  <span className="min-w-0 sm:mt-2 sm:block">
                    <span className="block truncate text-xs font-medium text-[var(--color-text)]">{phase.label}</span>
                    <span className="block text-[11px] text-gray-400">{statusText(status)}</span>
                  </span>
                </li>
              );
            })}
          </ol>

          {state.thinking && <p className="truncate text-xs text-gray-500" aria-live="polite">{state.thinking}</p>}

          {state.plan && state.plan.length > 0 && (
            <details className="rounded-xl border border-[var(--color-border)] bg-[var(--color-code-bg)] p-3">
              <summary className="cursor-pointer text-xs font-medium text-[var(--color-text)]">任务计划（{state.plan.length} 项）</summary>
              <div className="mt-2 max-h-40 space-y-1 overflow-auto text-xs text-gray-500">
                {state.plan.map((step, index) => <p key={`${step.tool}-${index}`} className="truncate">{index + 1}. {step.tool}{step.parallel ? ' · 并行' : ''}</p>)}
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
                  <summary className="cursor-pointer truncate text-xs text-[var(--color-text)]">{call.tool}{call.result !== undefined ? ' · 已返回结果' : ' · 执行中'}</summary>
                  <pre className="mt-2 max-w-full overflow-x-auto whitespace-pre-wrap break-words text-[11px] text-gray-500">{compactJson({ args: call.args, result: call.result })}</pre>
                </details>)}
              </div>}
            </div>
          )}

          {state.approval && (
            <div className="space-y-3 rounded-xl border border-amber-300 bg-amber-50/70 p-3" aria-label="人工审批">
              <div className="flex items-start gap-2">
                <span aria-hidden="true" className="mt-0.5 text-amber-700">!</span>
                <div className="min-w-0 space-y-1">
                  <h3 className="text-sm font-semibold text-amber-950">需要人工确认</h3>
                  <p className="break-words text-xs text-amber-900">{state.approval.message}</p>
                  <p className="break-words font-mono text-[11px] text-amber-800">动作：{state.approval.tool}</p>
                  <pre className="max-h-32 max-w-full overflow-auto whitespace-pre-wrap break-words text-[11px] text-amber-800">目标/参数：{compactJson(state.approval.args)}</pre>
                </div>
              </div>
              <p className="text-[11px] text-amber-900">影响：批准后将执行上述动作；拒绝则停止该写操作。</p>
              <div className="flex flex-wrap gap-2">
                <button type="button" disabled={state.approvalSubmitting} onClick={() => handleApproval(true)} className="min-h-[44px] rounded-lg bg-emerald-600 px-4 text-xs font-medium text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60">
                  {state.approvalSubmitting && (approvalTarget === 'approve' || approvalTarget === null) ? '批准提交中…' : '批准动作'}
                </button>
                <button type="button" disabled={state.approvalSubmitting} onClick={() => handleApproval(false)} className="min-h-[44px] rounded-lg border border-gray-300 bg-white px-4 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60">
                  {state.approvalSubmitting && (approvalTarget === 'reject' || approvalTarget === null) ? '拒绝提交中…' : '拒绝动作'}
                </button>
              </div>
            </div>
          )}

          {state.error && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">工作流异常：{state.error}</p>}
        </div>
      )}
    </section>
  );
}
