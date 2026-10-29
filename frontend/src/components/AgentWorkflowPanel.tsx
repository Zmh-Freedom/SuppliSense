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
  historical?: boolean;
  answerText?: string;
  workflowStatus?: AgentWorkflowSnapshot;
}

interface AgentWorkflowPanelProps {
  state: AgentWorkflowState;
  onApproval: (approved: boolean) => void;
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
  return labels[status || ''] || status || '状态未知';
}

function isGenericTerminalStatusMessage(status: string, message: string): boolean {
  if (status === 'needs_review') return ['需要人工复核', '结果需要人工复核'].includes(message);
  if (status === 'partial') return ['部分结果待复核', '本轮 Agent 仅完成部分分析'].includes(message);
  return false;
}

function lifecycleHeading(status: string): string {
  return lifecycleLabel(status);
}

function lifecycleIndicatorClass(status: string, isRunning: boolean): string {
  if (isRunning) return 'animate-pulse bg-blue-600';
  if (status === 'running') return 'bg-blue-500';
  if (status === 'failed') return 'bg-red-600';
  if (['needs_review', 'partial', 'waiting_approval', 'clarifying', 'stopped', 'cancelled', 'rejected'].includes(status)) return 'bg-amber-500';
  return 'bg-emerald-600';
}

function compactJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) || '无数据';
  } catch {
    return String(value);
  }
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
  const [approvalTarget, setApprovalTarget] = useState<'approve' | 'reject' | null>(null);
  const workflow = state.workflowStatus;
  const lifecycle = state.error ? 'failed' : workflow?.status || (state.done ? 'completed' : 'running');
  const endedLifecycles = ['waiting_approval', 'completed', 'partial', 'failed', 'needs_review', 'clarifying', 'stopped', 'cancelled', 'rejected'];
  const isRunning = !state.historical && !endedLifecycles.includes(lifecycle) && !state.done && !state.error;
  const workflowMessage = workflow?.message?.trim() || state.error || (state.historical ? '状态消息未保存' : lifecycleLabel(lifecycle));
  const showWorkflowMessage = workflowMessage !== state.answerText?.trim();
  const externalProfile = state.approval ? externalIdentityFromApprovalArgs(state.approval.args) : null;
  const isExternalIdentityConfirmation = Boolean(externalProfile);
  const isCompleted = lifecycle === 'completed';
  const isFailure = lifecycle === 'failed';
  const hideRedundantTerminalStatus = Boolean(state.answerText?.trim())
    && isGenericTerminalStatusMessage(lifecycle, workflowMessage);
  const visibleStatusMessage = showWorkflowMessage ? workflowMessage : '';
  if (state.historical && !state.approval && ['completed', 'running'].includes(lifecycle)) return null;
  if (isCompleted && !state.approval) return null;
  if (hideRedundantTerminalStatus && !state.approval) return null;

  const handleApproval = (approved: boolean) => {
    setApprovalTarget(approved ? 'approve' : 'reject');
    onApproval(approved);
  };

  return (
    <section className="w-full max-w-full" aria-label={state.approval ? '审批状态' : '分析进度'}>
      {!isCompleted && !hideRedundantTerminalStatus && <p role={isFailure ? 'alert' : 'status'} aria-live={isFailure ? 'assertive' : 'polite'} className={`flex flex-wrap items-center gap-x-2 gap-y-1 text-sm ${isFailure ? 'text-red-700' : ['needs_review', 'partial', 'clarifying', 'waiting_approval', 'stopped', 'cancelled', 'rejected'].includes(lifecycle) ? 'text-amber-800' : 'text-[var(--color-text-secondary)]'}`}>
        <span aria-hidden="true" className={`inline-block h-2 w-2 shrink-0 rounded-full ${lifecycleIndicatorClass(lifecycle, isRunning)}`} />
        {isRunning ? <span>{visibleStatusMessage || lifecycleLabel(lifecycle)}</span> : <>
          <span className="font-medium">{lifecycleHeading(lifecycle)}</span>
          {visibleStatusMessage && <span>{visibleStatusMessage}</span>}
        </>}
      </p>}

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

    </section>
  );
}
