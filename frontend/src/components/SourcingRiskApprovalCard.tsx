import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../query-keys';
import type { SourcingRiskApprovalProposal } from '../types';

const ACTION_LABELS: Record<string, string> = {
  import_external_supplier: '导入外部供应商',
  add_watchlist: '加入监控',
  submit_access_application: '提交准入申请',
  export_report: '导出报告',
};

export default function SourcingRiskApprovalCard({ proposal, runId, runVersion }: { proposal: SourcingRiskApprovalProposal; runId: string; runVersion: number }) {
  const queryClient = useQueryClient();
  const decision = useMutation({
    mutationFn: (value: 'approved' | 'rejected') => api.post(`/agent-runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(proposal.id)}`, {
      expected_version: runVersion,
      decision: value,
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.agentRunDetail(runId) }),
  });
  const actionLabel = ACTION_LABELS[proposal.action_type] ?? proposal.action_type;
  const pending = proposal.status === 'pending';
  return (
    <article className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 flex flex-wrap items-center justify-between gap-3">
      <div>
        <h4 className="text-sm font-semibold text-[var(--color-text)]">待审批：{actionLabel}</h4>
        <p className="text-xs text-[var(--color-text-secondary)] mt-1">执行前需要确认，任务版本 {runVersion}</p>
      </div>
      {pending ? <div className="flex gap-2"><button type="button" disabled={decision.isPending} onClick={() => decision.mutate('rejected')} className="text-xs rounded-lg border border-[var(--color-border)] px-3 py-1.5 disabled:opacity-50">拒绝</button><button type="button" disabled={decision.isPending} onClick={() => decision.mutate('approved')} className="text-xs rounded-lg bg-[var(--color-primary-bg)] text-white px-3 py-1.5 disabled:opacity-50">批准</button></div> : <span className="text-xs text-[var(--color-text-secondary)]">{proposal.status}</span>}
      {decision.isError && <p className="w-full text-xs text-red-500">提交审批决定失败，请刷新任务后重试。</p>}
    </article>
  );
}
