import { useState, type FormEvent } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, getStoredUser } from '../api';
import { queryKeys } from '../query-keys';
import type { ProcurementCaseDetail, ProcurementCaseEvent, ProcurementCaseList, ProcurementClosureCategory } from '../types';

function idempotencyHeaders(): Record<string, string> {
  const key = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return { 'Idempotency-Key': key };
}

function statusLabel(status: string): string {
  return ({
    open: '待接手',
    in_progress: '事项处理中',
    deferred: '已搁置',
    pending_closure: '待审核关闭',
    closed: '已关闭',
  } as Record<string, string>)[status] || status;
}

function typeLabel(value: string): string {
  return ({ risk_review: '风险复核', coverage_gap: '资料缺口', identity_review: '主体核验' } as Record<string, string>)[value] || value;
}

function eventLabel(value: string): string {
  return ({
    created: '事项已创建', claimed: '负责人已接手', assigned: '事项已分派',
    receipt_recorded: '处理回执', closure_requested: '已申请关闭',
    closure_approved: '关闭审核通过', closure_rejected: '关闭审核退回',
    review_task_linked: '已关联技术复核', deferred: '事项已搁置', resumed: '事项已恢复',
  } as Record<string, string>)[value] || value.replaceAll('_', ' ');
}

function taskStatusLabel(value: string): string {
  return ({
    pending_approval: '待审批', approved: '已批准', executing: '执行中',
    completed: '执行完成', needs_review: '待人工复核', failed: '执行失败',
    rejected: '已拒绝', cancelled: '已取消',
  } as Record<string, string>)[value] || value;
}

function taskTypeLabel(value: string): string {
  return ({
    verify_identity: '主体核验', assess: '首次风险评估', review: '采购风险复核',
    supplement_data: '数据覆盖核验', continue_monitoring: '继续观察',
  } as Record<string, string>)[value] || value;
}

function formatDate(value?: string | null): string {
  if (!value) return '未设置';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString('zh-CN', { hour12: false });
}

function toLocalDateTimeInput(value?: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function eventDescription(event: ProcurementCaseEvent): string {
  const payload = event.payload || {};
  const summary = payload.summary || payload.reason;
  if (typeof summary === 'string') return summary;
  if (event.event_type === 'closure_approved') return '关闭审核已通过。';
  return '处理记录已保存。';
}

function commandError(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请重试';
}

export default function ProcurementCasePanel({ caseId }: { caseId: string }) {
  const queryClient = useQueryClient();
  const user = getStoredUser();
  const detailQuery = useQuery({
    queryKey: queryKeys.procurementCase(caseId),
    queryFn: ({ signal }) => api.get<ProcurementCaseDetail>(`/procurement-cases/${encodeURIComponent(caseId)}`, undefined, signal),
  });
  const [receiptSummary, setReceiptSummary] = useState('');
  const [receiptRefs, setReceiptRefs] = useState('');
  const [closureSummary, setClosureSummary] = useState('');
  const [closureCategory, setClosureCategory] = useState<ProcurementClosureCategory>('accepted_limitation');
  const [residualLimitations, setResidualLimitations] = useState('');
  const [reviewReason, setReviewReason] = useState('');
  const [reopenReason, setReopenReason] = useState('');
  const [ownerUserId, setOwnerUserId] = useState('');
  const [transferReason, setTransferReason] = useState('');
  const [deferReason, setDeferReason] = useState('');
  const [nextReviewAt, setNextReviewAt] = useState<string | null>(null);
  const [dueAt, setDueAt] = useState<string | null>(null);
  const [duplicateCaseId, setDuplicateCaseId] = useState('');
  const [selectedReceiptIds, setSelectedReceiptIds] = useState<string[] | null>(null);
  const [isClosureFormOpen, setIsClosureFormOpen] = useState(false);
  const [actionError, setActionError] = useState('');
  const detailCase = detailQuery.data?.case;
  const duplicateTargetsQuery = useInfiniteQuery({
    queryKey: queryKeys.procurementCases({
      view: 'accessible',
      monitor_target_id: detailCase?.monitor_target_id || '',
      limit: '100',
      actor: user?.username || '',
    }),
    queryFn: ({ signal, pageParam }) => api.get<ProcurementCaseList>(
      '/procurement-cases',
      {
        view: 'accessible',
        monitor_target_id: detailCase?.monitor_target_id || '',
        limit: '100',
        ...(pageParam ? { cursor: pageParam } : {}),
      },
      signal,
    ),
    initialPageParam: '',
    getNextPageParam: page => page.next_cursor || undefined,
    enabled: Boolean(isClosureFormOpen && closureCategory === 'duplicate' && detailCase?.monitor_target_id),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['procurement-cases'] });
    void queryClient.invalidateQueries({ queryKey: queryKeys.procurementCase(caseId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.watchlist });
    void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
    void queryClient.invalidateQueries({ queryKey: queryKeys.monitorReviewTasks });
  };

  const commandMutation = useMutation({
    mutationFn: (command: Record<string, unknown>) => api.post(
      `/procurement-cases/${encodeURIComponent(caseId)}/commands`, command, undefined, idempotencyHeaders(),
    ),
    onSuccess: (_response, command) => {
      setActionError('');
      if (command.command === 'receipt') {
        setReceiptSummary('');
        setReceiptRefs('');
      }
      if (command.command === 'request_closure' || command.command === 'approve_closure' || command.command === 'reject_closure') {
        setIsClosureFormOpen(false);
      }
      if (command.command === 'request_transfer') setTransferReason('');
      if (command.command === 'defer') {
        setDeferReason('');
        setNextReviewAt(null);
      }
      if (command.command === 'set_due') setDueAt(null);
      if (command.command === 'resume') setNextReviewAt(null);
      if (command.command === 'request_closure') setDuplicateCaseId('');
      setReviewReason('');
      invalidate();
    },
    onError: error => {
      const message = commandError(error);
      setActionError(message);
      if (message.includes('409') || message.includes('版本已变化') || message.toLowerCase().includes('conflict')) invalidate();
    },
  });

  const assignMutation = useMutation({
    mutationFn: (ownerId: string) => api.post(
      `/procurement-cases/${encodeURIComponent(caseId)}/commands`,
      { command: 'assign', expected_version: detailQuery.data?.case.version, owner_user_id: ownerId },
      undefined,
      idempotencyHeaders(),
    ),
    onSuccess: () => { setOwnerUserId(''); setActionError(''); invalidate(); },
    onError: error => {
      const message = commandError(error);
      setActionError(message);
      if (message.includes('409') || message.includes('版本已变化')) invalidate();
    },
  });

  const taskMutation = useMutation({
    mutationFn: () => {
      const caseType = detailQuery.data?.case.case_type;
      const taskType = caseType === 'coverage_gap' ? 'supplement_data' : caseType === 'identity_review' ? 'verify_identity' : 'review';
      return api.post(
        `/procurement-cases/${encodeURIComponent(caseId)}/review-tasks`,
        { task_type: taskType, payload: {} },
        undefined,
        idempotencyHeaders(),
      );
    },
    onSuccess: () => { setActionError(''); invalidate(); },
    onError: error => setActionError(commandError(error)),
  });

  if (detailQuery.isLoading) return <main className="mx-auto max-w-4xl space-y-4 px-4 py-8"><Link to="/cases" className="text-sm text-[var(--color-primary-bg)]">← 返回采购事项</Link><p className="rounded-xl border border-[var(--color-border)] p-6 text-sm text-[var(--color-text-secondary)]">正在读取采购事项…</p></main>;
  if (detailQuery.error || !detailQuery.data) return <main className="mx-auto max-w-4xl space-y-4 px-4 py-8"><Link to="/cases" className="text-sm text-[var(--color-primary-bg)]">← 返回采购事项</Link><section role="alert" className="rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-900">采购事项当前不可用，可能不存在或你的访问权限已变化。<button type="button" onClick={() => detailQuery.refetch()} className="ml-2 underline">重试</button></section></main>;

  const detail = detailQuery.data;
  const item = detail.case;
  const capabilities = detail.capabilities;
  const receiptEvents = detail.events.filter(event => event.event_type === 'receipt_recorded');
  const allReceiptIds = receiptEvents.map(event => event.id);
  const activeReceiptIds = selectedReceiptIds ?? allReceiptIds;
  const pending = commandMutation.isPending || assignMutation.isPending || taskMutation.isPending;
  const sendCommand = (command: string, extra: Record<string, unknown> = {}) => {
    setActionError('');
    commandMutation.mutate({ command, expected_version: item.version, ...extra });
  };
  const recordReceipt = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const refs = receiptRefs.split(/\r?\n/).map(ref => ref.trim()).filter(Boolean);
    sendCommand('receipt', { summary: receiptSummary, refs });
  };
  const requestClosure = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const command: Record<string, unknown> = {
      category: closureCategory,
      summary: closureSummary,
      receipt_ids: activeReceiptIds,
    };
    if (closureCategory === 'accepted_limitation') command.residual_limitations = residualLimitations;
    if (closureCategory === 'duplicate') command.duplicate_case_id = duplicateCaseId;
    sendCommand('request_closure', command);
  };
  const decideClosure = (command: 'approve_closure' | 'reject_closure') => {
    sendCommand(command, { reason: reviewReason });
  };
  const canSetDue = Boolean((capabilities?.can_update || capabilities?.can_assign)
    && item && ['open', 'in_progress', 'deferred'].includes(item.status));
  const duplicateCases = (duplicateTargetsQuery.data?.pages || [])
    .flatMap(page => page.items || [])
    .map(entry => entry.case)
    .filter(candidate => candidate.id !== item?.id
      && candidate.monitor_target_id === item?.monitor_target_id
      && Boolean(item?.scope_key)
      && candidate.scope_key === item?.scope_key);
  const dueInputValue = dueAt ?? toLocalDateTimeInput(item?.due_at);
  const nextReviewInputValue = nextReviewAt ?? toLocalDateTimeInput(item?.next_review_at);
  const submitTransfer = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    sendCommand('request_transfer', { reason: transferReason });
  };
  const submitDefer = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!nextReviewInputValue) return;
    sendCommand('defer', {
      reason: deferReason,
      next_review_at: new Date(nextReviewInputValue).toISOString(),
    });
  };
  const submitDue = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    sendCommand('set_due', { due_at: dueInputValue ? new Date(dueInputValue).toISOString() : null });
  };

  return (
    <main className="mx-auto max-w-4xl space-y-5 px-4 py-6">
      <Link to="/cases" className="inline-flex min-h-[40px] items-center text-sm text-[var(--color-primary-bg)] hover:underline">← 返回采购事项</Link>
      <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2"><span className="rounded-full bg-[var(--color-background)] px-2 py-1 text-[11px] text-[var(--color-text-secondary)]">{typeLabel(item.case_type)}</span><span className="rounded-full bg-amber-50 px-2 py-1 text-[11px] text-amber-800">{statusLabel(item.status)}</span><span className="text-xs text-[var(--color-text-secondary)]">版本 v{item.version}</span></div>
            <h1 className="mt-2 break-words text-xl font-semibold text-[var(--color-text)]">{item.title}</h1>
            <p className="mt-2 break-all text-xs text-[var(--color-text-secondary)]">监控对象 ID：{item.monitor_target_id} · 科室：{item.department_code || '未指定'}</p>
          </div>
          {capabilities.can_claim && <button type="button" disabled={pending} onClick={() => sendCommand('claim')} className="min-h-[40px] rounded-lg bg-[var(--color-primary-bg)] px-4 text-sm font-medium text-white disabled:opacity-50">接手事项</button>}
        </div>
        <dl className="mt-5 grid grid-cols-2 gap-4 border-t border-[var(--color-border)] pt-4 text-sm sm:grid-cols-4">
          <div><dt className="text-xs text-[var(--color-text-secondary)]">负责人</dt><dd className="mt-1 break-all font-medium text-[var(--color-text)]">{item.owner_user_id || '待分派'}</dd></div>
          <div><dt className="text-xs text-[var(--color-text-secondary)]">创建人</dt><dd className="mt-1 break-all font-medium text-[var(--color-text)]">{item.created_by}</dd></div>
          <div><dt className="text-xs text-[var(--color-text-secondary)]">期限</dt><dd className="mt-1 font-medium text-[var(--color-text)]">{formatDate(item.due_at)}</dd></div>
          <div><dt className="text-xs text-[var(--color-text-secondary)]">下次复查</dt><dd className="mt-1 font-medium text-[var(--color-text)]">{formatDate(item.next_review_at)}</dd></div>
        </dl>
        {capabilities.can_assign && <form className="mt-4 flex flex-wrap items-end gap-2 border-t border-[var(--color-border)] pt-4" onSubmit={event => { event.preventDefault(); assignMutation.mutate(ownerUserId.trim()); }}><label className="min-w-[220px] flex-1 text-xs text-[var(--color-text-secondary)]">负责人账号 ID<input value={ownerUserId} onChange={event => setOwnerUserId(event.target.value)} className="mt-1 min-h-[40px] w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 text-sm text-[var(--color-text)]" /></label><button type="submit" disabled={pending || !ownerUserId.trim()} className="min-h-[40px] rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-text)] disabled:opacity-50">分派事项</button></form>}
      </section>

      {capabilities.can_update && item.status === 'in_progress' && <section className="grid gap-4 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-sm md:grid-cols-2">
        <form className="space-y-3" onSubmit={submitTransfer}>
          <div><h2 className="text-sm font-semibold text-[var(--color-text)]">申请转交</h2><p className="mt-1 text-xs text-[var(--color-text-secondary)]">转交请求会记录原因，负责人变更仍由经理处理。</p></div>
          <label className="block text-xs text-[var(--color-text-secondary)]">转交原因<textarea aria-label="转交原因" required maxLength={2000} value={transferReason} onChange={event => setTransferReason(event.target.value)} rows={2} className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)]" /></label>
          <button type="submit" disabled={pending || !transferReason.trim()} className="min-h-[40px] rounded-lg border border-[var(--color-border)] px-3 text-sm text-[var(--color-primary-bg)] disabled:opacity-50">提交转交请求</button>
        </form>
        <form className="space-y-3" onSubmit={submitDefer}>
          <div><h2 className="text-sm font-semibold text-[var(--color-text)]">搁置并安排复查</h2><p className="mt-1 text-xs text-[var(--color-text-secondary)]">搁置不会暂停事项原期限。</p></div>
          <label className="block text-xs text-[var(--color-text-secondary)]">搁置原因<textarea aria-label="搁置原因" required maxLength={2000} value={deferReason} onChange={event => setDeferReason(event.target.value)} rows={2} className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)]" /></label>
          <label className="block text-xs text-[var(--color-text-secondary)]">下次复查时间<input aria-label="下次复查时间" required type="datetime-local" value={nextReviewInputValue} onChange={event => setNextReviewAt(event.target.value)} className="mt-1 min-h-[40px] w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 text-sm text-[var(--color-text)]" /></label>
          <button type="submit" disabled={pending || !deferReason.trim() || !nextReviewInputValue} className="min-h-[40px] rounded-lg border border-[var(--color-border)] px-3 text-sm text-[var(--color-primary-bg)] disabled:opacity-50">搁置事项</button>
        </form>
      </section>}

      {capabilities.can_update && item.status === 'deferred' && <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-sm"><h2 className="text-sm font-semibold text-[var(--color-text)]">恢复处理</h2><p className="mt-1 text-xs text-[var(--color-text-secondary)]">恢复后事项回到处理中，原期限继续有效。</p><button type="button" disabled={pending} onClick={() => sendCommand('resume')} className="mt-3 min-h-[40px] rounded-lg bg-[var(--color-primary-bg)] px-4 text-sm text-white disabled:opacity-50">恢复事项</button></section>}

      {canSetDue && <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-sm"><h2 className="text-sm font-semibold text-[var(--color-text)]">设置事项期限</h2><form className="mt-3 flex flex-wrap items-end gap-3" onSubmit={submitDue}><label className="min-w-[220px] flex-1 text-xs text-[var(--color-text-secondary)]">期限时间<input aria-label="事项期限" type="datetime-local" value={dueInputValue} onChange={event => setDueAt(event.target.value)} className="mt-1 min-h-[40px] w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 text-sm text-[var(--color-text)]" /></label><button type="submit" disabled={pending} className="min-h-[40px] rounded-lg border border-[var(--color-border)] px-3 text-sm text-[var(--color-primary-bg)] disabled:opacity-50">保存期限</button></form></section>}

      {actionError && <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">{actionError}</div>}

      {capabilities.can_update && <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-[var(--color-text)]">记录处理回执</h2>
        <p className="mt-1 text-xs text-[var(--color-text-secondary)]">回执记录采购处理过程；引用不会自动成为正式来源证据。</p>
        <form className="mt-4 space-y-3" onSubmit={recordReceipt}>
          <label className="block text-xs text-[var(--color-text-secondary)]">回执摘要<textarea aria-label="回执摘要" required maxLength={2000} value={receiptSummary} onChange={event => setReceiptSummary(event.target.value)} rows={3} className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)]" /></label>
          <label className="block text-xs text-[var(--color-text-secondary)]">资料引用，每行一项<textarea aria-label="资料引用，每行一项" required value={receiptRefs} onChange={event => setReceiptRefs(event.target.value)} rows={2} placeholder="粘贴事项处理相关的资料或证据引用" className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)]" /></label>
          <button type="submit" disabled={pending || !receiptSummary.trim() || !receiptRefs.trim()} className="min-h-[40px] rounded-lg bg-[var(--color-primary-bg)] px-4 text-sm text-white disabled:opacity-50">{commandMutation.isPending && commandMutation.variables?.command === 'receipt' ? '保存中…' : '记录回执'}</button>
        </form>
      </section>}

      {capabilities.can_update && detail.linked_tasks.length === 0 && <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-sm"><h2 className="text-sm font-semibold text-[var(--color-text)]">技术执行</h2><p className="mt-1 text-xs text-[var(--color-text-secondary)]">技术任务独立于采购事项状态记录。</p><button type="button" disabled={pending} onClick={() => taskMutation.mutate()} className="mt-3 min-h-[40px] rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-primary-bg)] disabled:opacity-50">{taskMutation.isPending ? '创建中…' : '创建技术复核任务'}</button></section>}

      {capabilities.can_request_closure && <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-sm font-semibold text-[var(--color-text)]">申请关闭</h2><p className="mt-1 text-xs text-[var(--color-text-secondary)]">关闭申请由经理或管理员独立审核，申请人不能自批。</p></div><button type="button" onClick={() => setIsClosureFormOpen(open => !open)} className="min-h-[40px] rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-primary-bg)]">{isClosureFormOpen ? '收起申请' : '申请关闭'}</button></div>
        {isClosureFormOpen && <form className="mt-4 space-y-3" onSubmit={requestClosure}>
          {receiptEvents.length > 0 && <fieldset className="space-y-2"><legend className="text-xs font-medium text-[var(--color-text-secondary)]">引用处理回执</legend>{receiptEvents.map(event => <label key={event.id} className="flex items-start gap-2 text-xs text-[var(--color-text)]"><input type="checkbox" checked={activeReceiptIds.includes(event.id)} onChange={change => setSelectedReceiptIds(change.target.checked ? [...activeReceiptIds, event.id] : activeReceiptIds.filter(id => id !== event.id))} /><span>{eventDescription(event)}</span></label>)}</fieldset>}
          <label className="block text-xs text-[var(--color-text-secondary)]">关闭类别<select aria-label="关闭类别" value={closureCategory} onChange={event => setClosureCategory(event.target.value as ProcurementClosureCategory)} className="mt-1 min-h-[40px] w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 text-sm text-[var(--color-text)]"><option value="accepted_limitation">接受资料限制</option><option value="resolved">已解决</option><option value="withdrawn">撤回事项</option><option value="duplicate">重复事项</option></select></label>
          {closureCategory === 'duplicate' && <div className="space-y-2"><label className="block text-xs text-[var(--color-text-secondary)]">重复的事项<select aria-label="重复的事项" required value={duplicateCaseId} onChange={event => setDuplicateCaseId(event.target.value)} className="mt-1 min-h-[40px] w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 text-sm text-[var(--color-text)]"><option value="">选择同对象、同范围的可见事项</option>{duplicateCases.map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.title} · {candidate.id}</option>)}</select></label>{duplicateTargetsQuery.isLoading && <p className="text-xs text-[var(--color-text-secondary)]">正在读取可选事项…</p>}{duplicateTargetsQuery.error && <p role="alert" className="text-xs text-amber-800">可选事项暂时无法加载，请重试。</p>}{!duplicateTargetsQuery.isLoading && duplicateCases.length === 0 && !duplicateTargetsQuery.error && <p className="text-xs text-[var(--color-text-secondary)]">当前没有其他同对象、同事项范围的可见事项。</p>}{duplicateTargetsQuery.hasNextPage && <button type="button" disabled={duplicateTargetsQuery.isFetchingNextPage} onClick={() => void duplicateTargetsQuery.fetchNextPage()} className="min-h-[36px] rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-primary-bg)] disabled:opacity-50">{duplicateTargetsQuery.isFetchingNextPage ? '加载中…' : '加载更多事项'}</button>}</div>}
          <label className="block text-xs text-[var(--color-text-secondary)]">关闭摘要<textarea aria-label="关闭摘要" required maxLength={2000} value={closureSummary} onChange={event => setClosureSummary(event.target.value)} rows={2} className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)]" /></label>
          {closureCategory === 'accepted_limitation' && <label className="block text-xs text-[var(--color-text-secondary)]">残余限制<textarea aria-label="残余限制" required maxLength={2000} value={residualLimitations} onChange={event => setResidualLimitations(event.target.value)} rows={2} className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)]" /></label>}
          <button type="submit" disabled={pending || !closureSummary.trim() || activeReceiptIds.length === 0 || (closureCategory === 'accepted_limitation' && !residualLimitations.trim()) || (closureCategory === 'duplicate' && (!duplicateCaseId || duplicateTargetsQuery.isLoading || Boolean(duplicateTargetsQuery.error)))} className="min-h-[40px] rounded-lg bg-[var(--color-primary-bg)] px-4 text-sm text-white disabled:opacity-50">提交关闭申请</button>
        </form>}
      </section>}

      {item.status === 'pending_closure' && item.closure_request && <section className="rounded-2xl border border-indigo-200 bg-indigo-50 p-5"><h2 className="text-sm font-semibold text-indigo-950">关闭申请待审核</h2><p className="mt-2 text-sm text-indigo-900">类别：{item.closure_request.category === 'accepted_limitation' ? '接受资料限制' : item.closure_request.category || '—'} · 申请人：{item.closure_request.requested_by || '—'}</p>{item.closure_request.summary && <p className="mt-2 text-sm leading-6 text-indigo-900">{item.closure_request.summary}</p>}{item.closure_request.residual_limitations && <p className="mt-2 text-sm leading-6 text-indigo-900">残余限制：{item.closure_request.residual_limitations}</p>}{capabilities.can_decide_closure && <div className="mt-4 space-y-3"><label className="block text-xs text-indigo-900">审核说明<textarea aria-label="审核说明" required value={reviewReason} onChange={event => setReviewReason(event.target.value)} rows={2} className="mt-1 w-full rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm" /></label><div className="flex flex-wrap gap-2"><button type="button" disabled={pending || !reviewReason.trim()} onClick={() => decideClosure('approve_closure')} className="min-h-[40px] rounded-lg bg-emerald-700 px-4 text-sm text-white disabled:opacity-50">批准关闭</button><button type="button" disabled={pending || !reviewReason.trim()} onClick={() => decideClosure('reject_closure')} className="min-h-[40px] rounded-lg border border-red-300 px-4 text-sm text-red-700 disabled:opacity-50">退回修改</button></div></div>}</section>}

      {item.status === 'closed' && item.closure_request?.category === 'accepted_limitation' && <section className="rounded-xl border border-amber-200 bg-amber-50 p-4"><h2 className="text-sm font-semibold text-amber-950">资料缺口仍未解决</h2><p className="mt-2 text-sm leading-6 text-amber-900">{item.closure_request.residual_limitations || '仍有资料限制。'}</p><p className="mt-2 text-xs text-amber-800">事项关闭记录了采购决策，不会改变风险结论或把未知状态改为安全。</p></section>}

      {capabilities.can_reopen && <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5"><h2 className="text-sm font-semibold text-[var(--color-text)]">重新打开</h2><label className="mt-3 block text-xs text-[var(--color-text-secondary)]">重新打开原因<textarea aria-label="重新打开原因" value={reopenReason} onChange={event => setReopenReason(event.target.value)} rows={2} className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)]" /></label><button type="button" disabled={pending || !reopenReason.trim()} onClick={() => sendCommand('reopen', { reason: reopenReason })} className="mt-3 min-h-[40px] rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-primary-bg)] disabled:opacity-50">重开事项</button></section>}

      <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-sm font-semibold text-[var(--color-text)]">技术执行记录</h2><p className="mt-1 text-xs text-[var(--color-text-secondary)]">技术任务状态独立于采购事项状态。</p></div>{capabilities.can_update && detail.linked_tasks.length > 0 && !detail.linked_tasks.some(task => !['completed', 'failed', 'cancelled', 'rejected'].includes(task.status)) && <button type="button" disabled={pending} onClick={() => taskMutation.mutate()} className="min-h-[40px] rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-primary-bg)]">再发起技术复核</button>}</div>
        {detail.linked_tasks.length === 0 ? <p className="mt-4 text-sm text-[var(--color-text-secondary)]">尚无关联的技术执行任务。</p> : <div className="mt-4 space-y-3">{detail.linked_tasks.map(task => <article key={task.id} className="rounded-xl border border-[var(--color-border)] p-4"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-medium text-[var(--color-text)]">{taskTypeLabel(task.task_type)}</h3><span className="rounded-full bg-[var(--color-background)] px-2 py-1 text-[11px] text-[var(--color-text-secondary)]">{taskStatusLabel(task.status)}</span></div><p className="mt-2 text-sm leading-6 text-[var(--color-text-secondary)]">{task.result?.summary || '暂无执行摘要'}</p><div className="mt-2 text-xs text-[var(--color-text-secondary)]">任务 ID：{task.id}{task.version ? ` · 版本 v${task.version}` : ''}{task.created_at ? ` · 创建于 ${formatDate(task.created_at)}` : ''}</div>{task.status === 'completed' && item.status !== 'closed' && <p className="mt-2 text-xs text-amber-800">执行完成不代表采购事项已经关闭。</p>}</article>)}</div>}
      </section>

      <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-sm"><h2 className="text-sm font-semibold text-[var(--color-text)]">处理时间线</h2><ol className="mt-4 space-y-4">{detail.events.map(event => <li key={event.id} className="border-l-2 border-[var(--color-border)] pl-4"><div className="flex flex-wrap justify-between gap-2"><span className="text-sm font-medium text-[var(--color-text)]">{eventLabel(event.event_type)}</span><time className="text-xs text-[var(--color-text-secondary)]">{formatDate(event.created_at)}</time></div><p className="mt-1 text-sm leading-6 text-[var(--color-text-secondary)]">{eventDescription(event)}</p><p className="mt-1 text-xs text-[var(--color-text-secondary)]">记录人：{event.actor_id || '—'}</p></li>)}</ol></section>
    </main>
  );
}
