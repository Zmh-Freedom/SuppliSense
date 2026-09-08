import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import { queryKeys } from '../query-keys';
import { useDashboard, useWatchlist } from '../hooks';
import type { MonitorReviewTask, MonitorTarget } from '../types';
import MonitoringWorkbench from './MonitoringWorkbench';
import { SkeletonCard, SkeletonChart } from './Skeleton';

interface CoverageDimension {
  key: string;
  label: string;
  status: string;
  detail?: string;
}

interface CoverageDetails {
  summary?: string;
  dimensions?: CoverageDimension[];
  missing_dimensions?: string[];
}

const TARGET_TYPE_LABELS: Record<string, string> = {
  formal_supplier: '正式供应商',
  external_candidate: '外部候选',
  company: '企业主体',
};

function targetLabel(target: MonitorTarget): string {
  return TARGET_TYPE_LABELS[target.target_type] || '监控对象';
}

function coverageOf(target: MonitorTarget): CoverageDetails {
  return (target.data_coverage || {}) as CoverageDetails;
}

function actionPrompt(target: MonitorTarget): string {
  const name = target.display_name || target.company_name;
  const id = target.monitor_target_id;
  const action = target.next_action?.code;
  if (action === 'verify_identity') return `请核验监控对象“${name}”的主体身份，监控对象ID为 ${id}。只使用有证据支持的数据，并明确待补充资料。`;
  if (action === 'assess') return `请对监控对象“${name}”执行首次风险评估，监控对象ID为 ${id}，说明数据覆盖边界。`;
  if (action === 'supplement_data') return `请列出监控对象“${name}”（监控对象ID：${id}）缺失的数据域和采购人员需要补充的资料。`;
  if (action === 'review') return `请对监控对象“${name}”（监控对象ID：${id}）进行采购风险复核，按发现、依据、需要核实事项输出。`;
  return `请复核监控对象“${name}”（监控对象ID：${id}）当前是否需要采购动作，并说明依据。`;
}

export default function MonitoringView() {
  const { monitorTargetId } = useParams<{ monitorTargetId?: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const dashboardQuery = useDashboard();
  const watchlistQuery = useWatchlist();
  const [toast, setToast] = useState('');

  const targets = useMemo<MonitorTarget[]>(() => {
    if (dashboardQuery.data?.targets?.length) return dashboardQuery.data.targets;
    if (watchlistQuery.targets.length) return watchlistQuery.targets;
    return watchlistQuery.companies.map(companyName => ({
      monitor_target_id: `legacy:${companyName}`,
      target_type: 'company',
      company_name: companyName,
    }));
  }, [dashboardQuery.data?.targets, watchlistQuery.companies, watchlistQuery.targets]);

  const selectedTarget = monitorTargetId
    ? targets.find(target => target.monitor_target_id === decodeURIComponent(monitorTargetId))
    : undefined;
  const detailTaskQuery = useQuery({
    queryKey: ['monitor-review-task', selectedTarget?.review_task?.id],
    queryFn: () => api.get<MonitorReviewTask>(`/alert/review-tasks/${selectedTarget?.review_task?.id}`),
    enabled: Boolean(selectedTarget?.review_task?.id),
  });

  const invalidateMonitoring = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.watchlist });
    queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
    queryClient.invalidateQueries({ queryKey: queryKeys.alertHistory });
    queryClient.invalidateQueries({ queryKey: ['monitor-review-task'] });
  };

  const addMutation = useMutation({
    mutationFn: (target: { company_name: string; target_type: 'company' }) => api.post('/alert/watch', target),
    onSuccess: () => { invalidateMonitoring(); setToast('已加入待核验监控对象'); },
    onError: () => setToast('添加监控对象失败，请重试'),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.upload('/alert/watch/upload', file),
    onSuccess: () => { invalidateMonitoring(); setToast('监控对象导入完成'); },
    onError: () => setToast('导入失败，请检查文件中的身份字段'),
  });

  const checkMutation = useMutation({
    mutationFn: () => api.post('/alert/check-all'),
    onSuccess: () => { invalidateMonitoring(); setToast('风险快照检查完成'); },
    onError: () => setToast('风险快照检查失败，请稍后重试'),
  });

  const createTaskMutation = useMutation({
    mutationFn: (target: MonitorTarget) => api.post<MonitorReviewTask>('/alert/review-tasks', {
      monitor_target_id: target.monitor_target_id,
      task_type: target.next_action?.code || 'review',
      payload: {
        target_type: target.target_type,
        data_coverage: target.data_coverage,
        risk_change: target.risk_change,
      },
    }),
    onSuccess: () => { invalidateMonitoring(); setToast('复核任务已创建，等待审批'); },
    onError: () => setToast('创建复核任务失败，请重试'),
  });

  const taskDecisionMutation = useMutation({
    mutationFn: ({ task, decision }: { task: MonitorReviewTask; decision: 'approved' | 'rejected' }) => api.post<MonitorReviewTask>(`/alert/review-tasks/${task.id}/decision`, {
      expected_version: task.version,
      decision,
      comment: decision === 'approved' ? '采购人员批准执行复核' : '采购人员拒绝本次复核',
    }),
    onSuccess: (_, variables) => { invalidateMonitoring(); setToast(variables.decision === 'approved' ? '任务已批准，可执行复核' : '任务已拒绝'); },
    onError: () => setToast('审批失败，任务可能已被其他人更新'),
  });

  const executeTaskMutation = useMutation({
    mutationFn: (task: MonitorReviewTask) => api.post<MonitorReviewTask>(`/alert/review-tasks/${task.id}/execute`, { expected_version: task.version }),
    onSuccess: () => { invalidateMonitoring(); setToast('复核已执行，证据和结果已回写'); },
    onError: () => setToast('复核执行失败，请查看任务详情'),
  });

  const removeTarget = (target: MonitorTarget) => {
    const params: Record<string, string> = target.monitor_target_id.startsWith('legacy:')
      ? { company_name: target.company_name }
      : { monitor_target_id: target.monitor_target_id };
    api.delete('/alert/watch', params)
      .then(() => { invalidateMonitoring(); setToast('已移出监控'); if (selectedTarget?.monitor_target_id === target.monitor_target_id) navigate('/assess'); })
      .catch(() => setToast('移出监控失败，请重试'));
  };

  const openTarget = (target: MonitorTarget) => navigate(`/assess/${encodeURIComponent(target.monitor_target_id)}`);
  const openAgent = (target: MonitorTarget) => navigate(`/chat?q=${encodeURIComponent(actionPrompt(target))}`);
  const executeNextAction = (target: MonitorTarget) => {
    const current = target.review_task;
    if (current?.status === 'pending_approval' || current?.status === 'executing') return;
    if (current?.status === 'approved') {
      executeTaskMutation.mutate(current);
      return;
    }
    createTaskMutation.mutate(target);
  };
  const approveTask = (target: MonitorTarget) => {
    if (target.review_task?.status === 'pending_approval') taskDecisionMutation.mutate({ task: target.review_task, decision: 'approved' });
  };
  const rejectTask = (target: MonitorTarget) => {
    if (target.review_task?.status === 'pending_approval') taskDecisionMutation.mutate({ task: target.review_task, decision: 'rejected' });
  };

  const isLoading = dashboardQuery.isLoading || watchlistQuery.isLoading;
  if (isLoading) {
    return <div className="max-w-4xl mx-auto py-6 px-4 space-y-6"><SkeletonCard /><SkeletonChart /></div>;
  }

  if (dashboardQuery.error || watchlistQuery.error) {
    return <div className="max-w-3xl mx-auto py-20 px-4 text-center"><p className="text-sm text-gray-400 mb-4">监控工作台暂时无法加载，请检查后端服务。</p><button onClick={() => { dashboardQuery.refetch(); watchlistQuery.refetch(); }} className="text-sm text-[var(--color-primary-bg)] hover:underline">重试</button></div>;
  }

  if (monitorTargetId && !selectedTarget) {
    return <div className="max-w-3xl mx-auto py-20 px-4 text-center"><p className="text-sm text-gray-400 mb-4">未找到这个监控对象，可能已被移出监控。</p><button onClick={() => navigate('/assess')} className="text-sm text-[var(--color-primary-bg)] hover:underline">返回监控工作台</button></div>;
  }

  return (
    <div className="max-w-5xl mx-auto py-6 px-4 space-y-5">
      {selectedTarget ? (
        <MonitoringTargetDetail
          target={selectedTarget}
          task={detailTaskQuery.data || selectedTarget.review_task}
          onBack={() => navigate('/assess')}
          onAction={executeNextAction}
          onApprove={approveTask}
          onReject={rejectTask}
          onExecuteTask={executeNextAction}
          onAnalyze={openAgent}
          onRemove={removeTarget}
          onRefresh={() => checkMutation.mutate()}
          isRefreshing={checkMutation.isPending}
        />
      ) : (
        <>
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold text-[var(--color-text)]">风险监控</h2>
              <p className="mt-1 text-sm text-[var(--color-text-secondary)]">围绕监控对象身份、数据覆盖和风险变化安排采购复核。</p>
            </div>
            <button type="button" onClick={() => checkMutation.mutate()} disabled={checkMutation.isPending} className="min-h-[38px] rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-50">{checkMutation.isPending ? '检查中…' : '检查全部风险快照'}</button>
          </div>
          <MonitoringWorkbench
            targets={targets}
            onAdd={target => addMutation.mutate(target)}
            onUpload={file => uploadMutation.mutate(file)}
            onRefresh={() => checkMutation.mutate()}
            onAnalyze={openAgent}
          onAction={executeNextAction}
          onApprove={approveTask}
          onReject={rejectTask}
          onExecuteTask={executeNextAction}
            onOpen={openTarget}
            onRemove={removeTarget}
            isAdding={addMutation.isPending}
            isUploading={uploadMutation.isPending}
            isRefreshing={checkMutation.isPending}
            defaultOpen
          />
        </>
      )}

      {toast && <div role="status" className="fixed bottom-6 left-1/2 z-30 -translate-x-1/2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2 text-xs text-[var(--color-text-secondary)] shadow-md">{toast}<button type="button" onClick={() => setToast('')} className="ml-2 text-gray-400">&times;</button></div>}
    </div>
  );
}

function MonitoringTargetDetail({
  target,
  task,
  onBack,
  onAction,
  onApprove,
  onReject,
  onExecuteTask,
  onAnalyze,
  onRemove,
  onRefresh,
  isRefreshing,
}: {
  target: MonitorTarget;
  task?: MonitorReviewTask | null;
  onBack: () => void;
  onAction: (target: MonitorTarget) => void;
  onApprove: (target: MonitorTarget) => void;
  onReject: (target: MonitorTarget) => void;
  onExecuteTask: (target: MonitorTarget) => void;
  onAnalyze: (target: MonitorTarget) => void;
  onRemove: (target: MonitorTarget) => void;
  onRefresh: () => void;
  isRefreshing: boolean;
}) {
  const coverage = coverageOf(target);
  const action = target.next_action;
  const riskText = target.risk_score == null ? '暂无快照' : `${target.risk_level || '未知'} · ${target.risk_score}/100`;
  return (
    <div className="space-y-5">
      <button type="button" onClick={onBack} className="text-sm text-[var(--color-primary-bg)] hover:underline">← 返回监控工作台</button>
      <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="mb-2 flex flex-wrap items-center gap-2"><span className="rounded-full bg-gray-100 px-2 py-1 text-[11px] text-gray-600">{targetLabel(target)}</span><span className={`rounded-full px-2 py-1 text-[11px] ${target.identity_status === 'verified' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{target.identity_status === 'verified' ? '主体已核验' : '主体待核验'}</span></div>
            <h1 className="truncate text-xl font-semibold text-[var(--color-text)]">{target.display_name || target.company_name}</h1>
            <p className="mt-2 break-all text-xs text-gray-400">监控对象 ID：{target.monitor_target_id}</p>
          </div>
          <div className="flex flex-wrap gap-2"><TaskActionButton target={target} onAction={onAction} onApprove={onApprove} onReject={onReject} onExecute={onExecuteTask} /><button type="button" onClick={() => onAnalyze(target)} className="rounded-lg border border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-primary-bg)] hover:bg-[var(--color-surface-hover)]">Agent 复核</button><button type="button" onClick={onRefresh} disabled={isRefreshing} className="rounded-lg border border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-text-secondary)] disabled:opacity-50">{isRefreshing ? '检查中…' : '重新检查'}</button></div>
        </div>
        <div className="mt-5 grid grid-cols-2 gap-3 border-t border-[var(--color-border)] pt-4 md:grid-cols-4"><DetailMetric label="当前风险" value={riskText} /><DetailMetric label="风险变化" value={target.risk_change?.label || '暂无数据'} /><DetailMetric label="数据覆盖" value={coverage.summary || '覆盖情况未知'} /><DetailMetric label="下一步" value={action?.label || '继续观察'} /></div>
      </section>

      <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-sm"><div className="mb-3 flex items-center justify-between gap-3"><h2 className="text-sm font-semibold text-[var(--color-text)]">数据覆盖与复核依据</h2><span className="text-xs text-gray-400">缺口不会被解释为稳定</span></div>{coverage.dimensions?.length ? <div className="divide-y divide-[var(--color-border)]">{coverage.dimensions.map(dimension => <div key={dimension.key} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm"><div><span className="font-medium text-[var(--color-text)]">{dimension.label}</span><span className="ml-2 text-xs text-gray-400">{dimension.detail}</span></div><span className={`rounded-full px-2 py-1 text-[11px] ${dimension.status === 'available' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{dimension.status === 'available' ? '可用' : '待补充'}</span></div>)}</div> : <p className="text-sm text-gray-400">暂无数据覆盖明细。</p>}</section>

      <section className="rounded-2xl border border-amber-200 bg-amber-50 p-5"><h2 className="text-sm font-semibold text-amber-900">采购复核建议</h2><p className="mt-2 text-sm leading-6 text-amber-900">{action?.reason || '等待更多监控数据后再安排复核。'}</p><div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-amber-800"><span>建议动作：{action?.label || '继续观察'}</span><span>优先级：{action?.priority || 'low'}</span></div></section>

      <ReviewTaskPanel target={target} task={task} onAction={onAction} onApprove={onApprove} onReject={onReject} onExecute={onExecuteTask} />

      <button type="button" onClick={() => onRemove(target)} className="text-xs text-gray-400 hover:text-red-600">移出监控</button>
    </div>
  );
}

function taskStatusLabel(status?: string): string {
  return ({ pending_approval: '待审批', approved: '已审批', executing: '执行中', completed: '已完成', needs_review: '待人工复核', rejected: '已拒绝', failed: '执行失败', cancelled: '已取消' } as Record<string, string>)[status || ''] || '未创建';
}

function taskTypeLabel(taskType?: string): string {
  return ({ verify_identity: '主体核验', assess: '首次风险评估', review: '采购风险复核', supplement_data: '补充资料', continue_monitoring: '继续观察' } as Record<string, string>)[taskType || ''] || taskType || '复核任务';
}

function evidenceLabel(value?: string): string {
  return ({ identity: '主体身份', risk_monitoring: '风险监控', risk_assessment: '风险评估', supported: '已支持', partial: '部分支持', conflicting: '存在冲突', unsupported: '不支持', monitoring_workbench: '监控工作台', cached_risk_evidence: '已有风险证据', watchlist_identity: '监控对象身份', risk_snapshot: '风险快照', coverage_snapshot: '数据覆盖快照', monitoring_observation: '监控观察' } as Record<string, string>)[value || ''] || value || '已记录';
}

function TaskActionButton({ target, onAction, onApprove, onReject, onExecute }: { target: MonitorTarget; onAction: (target: MonitorTarget) => void; onApprove: (target: MonitorTarget) => void; onReject: (target: MonitorTarget) => void; onExecute: (target: MonitorTarget) => void }) {
  const task = target.review_task;
  if (!task || ['completed', 'needs_review', 'failed', 'rejected', 'cancelled'].includes(task.status)) return <button type="button" onClick={() => onAction(target)} className="rounded-lg bg-[var(--color-primary-bg)] px-3 py-2 text-xs text-white hover:bg-[var(--color-primary-hover)]">{task ? '再次发起复核' : target.next_action?.label || '发起复核'}</button>;
  if (task.status === 'pending_approval') return <><button type="button" onClick={() => onApprove(target)} className="rounded-lg bg-emerald-600 px-3 py-2 text-xs text-white hover:bg-emerald-700">批准任务</button><button type="button" onClick={() => onReject(target)} className="rounded-lg border border-red-200 px-3 py-2 text-xs text-red-600 hover:bg-red-50">拒绝</button></>;
  if (task.status === 'approved') return <button type="button" onClick={() => onExecute(target)} className="rounded-lg bg-[var(--color-primary-bg)] px-3 py-2 text-xs text-white hover:bg-[var(--color-primary-hover)]">执行复核</button>;
  return <span className="rounded-lg border border-[var(--color-border)] px-3 py-2 text-xs text-gray-500">{taskStatusLabel(task.status)}</span>;
}

function ReviewTaskPanel({ target, task, onAction, onApprove, onReject, onExecute }: { target: MonitorTarget; task?: MonitorReviewTask | null; onAction: (target: MonitorTarget) => void; onApprove: (target: MonitorTarget) => void; onReject: (target: MonitorTarget) => void; onExecute: (target: MonitorTarget) => void }) {
  return <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-sm"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-sm font-semibold text-[var(--color-text)]">复核任务闭环</h2><p className="mt-1 text-xs text-gray-400">任务 → 审批 → 执行 → 证据 → 结果</p></div><span className={`rounded-full px-2 py-1 text-[11px] ${task?.status === 'completed' ? 'bg-emerald-50 text-emerald-700' : task?.status === 'failed' ? 'bg-red-50 text-red-700' : 'bg-amber-50 text-amber-700'}`}>{taskStatusLabel(task?.status)}</span></div>{task ? <><div className="mt-4 grid grid-cols-2 gap-3 text-xs md:grid-cols-4"><DetailMetric label="任务类型" value={taskTypeLabel(task.task_type)} /><DetailMetric label="任务版本" value={`v${task.version}`} /><DetailMetric label="证据条数" value={`${task.evidence_count || task.evidence_refs?.length || 0} 条`} /><DetailMetric label="更新时间" value={new Date(task.updated_at).toLocaleString('zh-CN')} /></div><p className="mt-4 text-sm leading-6 text-[var(--color-text-secondary)]">{task.result?.summary || task.approval_comment || '任务已创建，等待审批。'}</p>{task.evidence?.length ? <div className="mt-4 space-y-2"><div className="text-xs font-medium text-[var(--color-text-secondary)]">证据回写</div>{task.evidence.map(item => <div key={item.evidence_id} className="rounded-lg bg-[var(--color-background)] px-3 py-2 text-xs"><div className="flex flex-wrap justify-between gap-2"><span className="font-medium text-[var(--color-text)]">{evidenceLabel(item.dimension)}</span><span className="text-gray-400">{evidenceLabel(item.status)}</span></div><div className="mt-1 text-gray-500">{evidenceLabel(item.provider)} · {evidenceLabel(item.source_type)}</div></div>)}</div> : null}<div className="mt-4 flex flex-wrap gap-2"><TaskActionButton target={{ ...target, review_task: task }} onAction={onAction} onApprove={onApprove} onReject={onReject} onExecute={onExecute} /></div></> : <p className="mt-4 text-sm text-gray-400">还没有持久化复核任务，使用页面顶部动作发起后需要审批才会执行。</p>}</section>;
}

function DetailMetric({ label, value }: { label: string; value: string }) {
  return <div><div className="text-xs text-gray-400">{label}</div><div className="mt-1 text-sm font-semibold text-[var(--color-text)]">{value}</div></div>;
}
