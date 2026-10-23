import { useEffect, useRef, useState } from 'react';
import { api } from '../api';

export interface SourcingHandoffDetail {
  handoff_id: string;
  session_id: string;
  source_run_id: string;
  source_run_version: number;
  candidate_key: string;
  candidate: { supplier_name?: string; name?: string; identity_status?: string };
  requirement: { requirement_text?: string; category?: string };
  evidence_refs: Array<{ evidence_id?: string; dimension?: string; freshness_status?: string; use?: string }>;
  intent: 'risk_review' | 'monitor_proposal';
  status: 'ready' | 'consumed' | 'cancelled';
  continue_message: string;
  consumed_turn_id?: string | null;
  run_id?: string | null;
}

export default function SourcingHandoffPanel({
  handoffId,
  sessionId,
  onContinue,
  onConsumed,
}: {
  handoffId: string;
  sessionId: string;
  onContinue: (detail: SourcingHandoffDetail) => Promise<void> | void;
  onConsumed: (detail: SourcingHandoffDetail) => void;
}) {
  const [result, setResult] = useState<{ handoffId: string; detail: SourcingHandoffDetail | null; error: string }>({
    handoffId: '',
    detail: null,
    error: '',
  });
  const [continuing, setContinuing] = useState(false);
  const consumedNotified = useRef('');
  const loading = result.handoffId !== handoffId;
  const detail = loading ? null : result.detail;
  const error = loading ? '' : result.error;

  useEffect(() => {
    let active = true;
    void api.get<SourcingHandoffDetail>(`/sourcing-workspace/handoffs/${encodeURIComponent(handoffId)}`)
      .then(result => {
        if (!active) return;
        setResult({ handoffId, detail: result, error: '' });
        if (result.status === 'consumed' && result.run_id && consumedNotified.current !== result.handoff_id) {
          consumedNotified.current = result.handoff_id;
          onConsumed(result);
        }
      })
      .catch(() => {
        if (!active) return;
        setResult({ handoffId, detail: null, error: '无法读取该寻源交接，可能已失效或当前账号无权访问。' });
      });
    return () => { active = false; };
  }, [handoffId, onConsumed]);

  const sessionMatches = Boolean(detail && detail.session_id === sessionId);
  if (loading) return <section aria-label="寻源交接" className="mx-auto mt-6 max-w-5xl rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-text-secondary)]">正在读取寻源交接…</section>;
  if (error || !detail) return <section aria-label="寻源交接" role="alert" className="mx-auto mt-6 max-w-5xl rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error || '无法读取该寻源交接。'}</section>;
  if (!sessionMatches) return <section aria-label="寻源交接" role="alert" className="mx-auto mt-6 max-w-5xl rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">交接会话与当前地址不匹配，请从寻源任务重新打开。</section>;

  const candidateName = detail.candidate.supplier_name || detail.candidate.name || '待确认候选';
  const requirement = detail.requirement.requirement_text || detail.requirement.category || '未提供需求摘要';
  const staleCount = detail.evidence_refs.filter(item => item.use === 'historical' || item.freshness_status !== 'fresh').length;

  return <section aria-label="寻源交接" className="mx-auto mt-6 max-w-5xl rounded-2xl border border-[var(--color-primary-bg)]/25 bg-[var(--color-surface)] p-4 shadow-sm">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <p className="text-xs font-medium text-[var(--color-primary-bg)]">来自寻源任务的授权交接</p>
        <h2 className="mt-1 text-base font-semibold text-[var(--color-text)]">{candidateName}</h2>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">原采购需求：{requirement}</p>
        <p className="mt-1 text-xs text-[var(--color-text-secondary)]">{detail.intent === 'monitor_proposal' ? '继续后将准备监控申请，并在任何写入前请求人工审批。' : '继续后将基于当前权限重新核验主体并分析风险。'}</p>
        {detail.evidence_refs.length > 0 && <p className="mt-2 text-xs text-[var(--color-text-secondary)]">保留 {detail.evidence_refs.length} 条来源证据引用；{staleCount > 0 ? `${staleCount} 条仅作为历史材料` : '当前引用仍需服务端重新核验'}。</p>}
      </div>
      {detail.status === 'ready'
        ? <button type="button" disabled={continuing} onClick={() => {
          setContinuing(true);
          void Promise.resolve(onContinue(detail)).catch(() => setResult(previous => ({
            ...previous,
            error: '继续交接失败，请检查网络后重试。',
          }))).finally(() => setContinuing(false));
        }} className="min-h-[44px] shrink-0 rounded-xl bg-[var(--color-primary-bg)] px-4 py-2 text-sm font-medium text-white hover:bg-[var(--color-primary-hover)] disabled:opacity-50">{continuing ? '正在继续…' : '继续此候选'}</button>
        : detail.status === 'consumed'
          ? <span role="status" className="min-h-[44px] inline-flex items-center rounded-xl bg-emerald-50 px-4 py-2 text-sm text-emerald-800">正在恢复原交接任务</span>
          : <span role="status" className="min-h-[44px] inline-flex items-center rounded-xl bg-amber-50 px-4 py-2 text-sm text-amber-900">该交接已取消</span>}
    </div>
    {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
  </section>;
}
