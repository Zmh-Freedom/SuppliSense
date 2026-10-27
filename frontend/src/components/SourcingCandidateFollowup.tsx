import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { queryKeys } from '../query-keys';
import type {
  SourcingCandidateDispositionState,
  SourcingCandidateFollowupResponse,
  SourcingRiskCandidate,
} from '../types';

function candidateKey(candidate: SourcingRiskCandidate): string {
  return String(candidate.id ?? candidate.candidate_id ?? candidate.candidate_key ?? '');
}

function candidateLabel(candidate: SourcingRiskCandidate): string {
  const key = candidateKey(candidate);
  return candidate.supplier_name ?? candidate.name ?? (key || '未命名候选企业');
}

function newRequestKey(): string {
  try { return globalThis.crypto?.randomUUID?.() ?? ''; } catch { return ''; }
}

function followupRequestKey(runId: string, candidateKeys: string[]): string {
  const storageKey = `sourcing_followup:${runId}:${[...candidateKeys].sort().join(',')}`;
  try {
    const saved = sessionStorage.getItem(storageKey);
    if (saved) return saved;
  } catch { /* Continue with an in-memory request key. */ }
  const key = newRequestKey() || `followup-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
  try { sessionStorage.setItem(storageKey, key); } catch { /* The server still validates the parent and candidate set. */ }
  return key;
}

export default function SourcingCandidateFollowup({
  runId,
  candidates,
}: {
  runId: string;
  candidates: SourcingRiskCandidate[];
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string[]>([]);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const dispositionsQuery = useQuery({
    queryKey: queryKeys.sourcingCandidateDecisions(runId),
    queryFn: () => api.get<SourcingCandidateDispositionState>(
      `/sourcing-workspace/runs/${encodeURIComponent(runId)}/candidate-decisions`,
    ),
    enabled: Boolean(runId),
  });
  const candidatesByKey = new Map(candidates.map(candidate => [candidateKey(candidate), candidate]));
  const deferred = (dispositionsQuery.data?.dispositions ?? []).filter(item => item.action === 'defer');
  const excluded = (dispositionsQuery.data?.dispositions ?? []).filter(item => item.action === 'exclude');
  const selectedKeys = selected.filter(key => deferred.some(item => item.candidate_key === key)).sort();

  const followupMutation = useMutation({
    mutationFn: (candidateKeys: string[]) => api.post<SourcingCandidateFollowupResponse>(
      `/sourcing-workspace/runs/${encodeURIComponent(runId)}/followups`,
      { candidate_keys: candidateKeys },
      undefined,
      { 'Idempotency-Key': followupRequestKey(runId, candidateKeys) },
    ),
    onSuccess: result => navigate(result.resume_url),
  });
  const reclassifyMutation = useMutation({
    mutationFn: ({ candidateKey: key, reason }: { candidateKey: string; reason: string }) => {
      const runVersion = dispositionsQuery.data?.run_version;
      if (runVersion === undefined) return Promise.reject(new Error('当前任务版本不可用'));
      const requestKey = newRequestKey() || `defer-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
      return api.post<SourcingCandidateDispositionState>(
        `/sourcing-workspace/runs/${encodeURIComponent(runId)}/candidate-decisions`,
        {
          expected_version: runVersion,
          decisions: [{ candidate_key: key, action: 'defer', reason }],
        },
        undefined,
        { 'Idempotency-Key': requestKey },
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.sourcingCandidateDecisions(runId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentRunDetail(runId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.sourcingTasks });
    },
  });

  return <section aria-label="暂存候选恢复" className="space-y-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
    <div>
      <h3 className="text-sm font-semibold text-[var(--color-text)]">继续暂存候选</h3>
      <p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">勾选后会创建新的寻源任务；原任务及其结果保持不变。排除候选需先明确改为暂存。</p>
    </div>
    {dispositionsQuery.isLoading && <p role="status" className="text-xs text-[var(--color-text-secondary)]">正在读取候选处置…</p>}
    {dispositionsQuery.isError && <p role="alert" className="text-xs text-red-600">暂存候选暂不可用，请刷新任务后重试。</p>}
    {dispositionsQuery.data && <>
      {deferred.length === 0 && excluded.length === 0 && <p className="text-xs text-[var(--color-text-secondary)]">当前没有可继续的暂存候选。</p>}
      {deferred.length > 0 && <fieldset className="space-y-2">
        <legend className="text-xs font-medium text-[var(--color-text)]">选择需要继续核验的候选</legend>
        {deferred.map(item => {
          const candidate = candidatesByKey.get(item.candidate_key);
          const label = candidate ? candidateLabel(candidate) : `候选 ${item.candidate_key}`;
          return <label key={item.candidate_key} className="flex min-h-[44px] items-start gap-3 rounded-xl border border-[var(--color-border)] px-3 py-2 text-sm">
            <input
              type="checkbox"
              aria-label={`选择${label}继续核验`}
              checked={selected.includes(item.candidate_key)}
              onChange={event => setSelected(current => event.target.checked
                ? [...new Set([...current, item.candidate_key])]
                : current.filter(key => key !== item.candidate_key))}
              className="mt-1"
            />
            <span className="min-w-0">
              <span className="block text-[var(--color-text)]">{label}</span>
              {item.reason && <span className="mt-0.5 block text-xs text-[var(--color-text-secondary)]">暂存原因：{item.reason}</span>}
            </span>
          </label>;
        })}
        <button
          type="button"
          disabled={selectedKeys.length === 0 || followupMutation.isPending}
          onClick={() => followupMutation.mutate(selectedKeys)}
          className="min-h-[44px] rounded-xl bg-[var(--color-primary-bg)] px-4 py-2 text-sm text-white disabled:opacity-50"
        >{followupMutation.isPending ? '正在创建恢复任务…' : '创建恢复任务'}</button>
      </fieldset>}
      {excluded.length > 0 && <div className="space-y-2 border-t border-[var(--color-border)] pt-3">
        <h4 className="text-xs font-medium text-[var(--color-text)]">重新暂存已排除候选</h4>
        <p className="text-xs leading-5 text-[var(--color-text-secondary)]">排除记录会保留；只有重新说明原因并改为暂存后，候选才可进入新任务。</p>
        {excluded.map(item => {
          const candidate = candidatesByKey.get(item.candidate_key);
          const label = candidate ? candidateLabel(candidate) : `候选 ${item.candidate_key}`;
          const reason = reasons[item.candidate_key] ?? '';
          return <div key={item.candidate_key} className="space-y-2 rounded-xl border border-[var(--color-border)] p-3">
            <p className="text-sm font-medium text-[var(--color-text)]">{label}</p>
            <textarea
              aria-label={`${label}重新暂存原因`}
              value={reason}
              onChange={event => setReasons(current => ({ ...current, [item.candidate_key]: event.target.value }))}
              placeholder="说明为什么现在可以重新核验"
              rows={2}
              className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-input-bg)] px-3 py-2 text-sm"
            />
            <button
              type="button"
              disabled={!reason.trim() || reclassifyMutation.isPending}
              onClick={() => reclassifyMutation.mutate({ candidateKey: item.candidate_key, reason: reason.trim() })}
              className="min-h-[40px] rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-primary-bg)] disabled:opacity-50"
            >{reclassifyMutation.isPending ? '正在更新…' : `将${label}改为暂存`}</button>
          </div>;
        })}
      </div>}
    </>}
    {followupMutation.isError && <p role="alert" className="text-xs text-red-600">创建恢复任务失败，请刷新父任务确认状态后重试。</p>}
    {reclassifyMutation.isError && <p role="alert" className="text-xs text-red-600">候选处置更新失败，请刷新父任务确认最新版本后重试。</p>}
  </section>;
}
