import { useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import { queryKeys } from '../query-keys';
import { useDashboard, useWatchlist } from '../hooks';
import type { MonitorTarget } from '../types';
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

  const invalidateMonitoring = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.watchlist });
    queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
    queryClient.invalidateQueries({ queryKey: queryKeys.alertHistory });
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
  const executeNextAction = (target: MonitorTarget) => openAgent(target);

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
          onBack={() => navigate('/assess')}
          onAction={executeNextAction}
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
  onBack,
  onAction,
  onAnalyze,
  onRemove,
  onRefresh,
  isRefreshing,
}: {
  target: MonitorTarget;
  onBack: () => void;
  onAction: (target: MonitorTarget) => void;
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
          <div className="flex flex-wrap gap-2"><button type="button" onClick={() => onAction(target)} className="rounded-lg bg-[var(--color-primary-bg)] px-3 py-2 text-xs text-white hover:bg-[var(--color-primary-hover)]">{action?.label || '发起复核'}</button><button type="button" onClick={() => onAnalyze(target)} className="rounded-lg border border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-primary-bg)] hover:bg-[var(--color-surface-hover)]">Agent 复核</button><button type="button" onClick={onRefresh} disabled={isRefreshing} className="rounded-lg border border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-text-secondary)] disabled:opacity-50">{isRefreshing ? '检查中…' : '重新检查'}</button></div>
        </div>
        <div className="mt-5 grid grid-cols-2 gap-3 border-t border-[var(--color-border)] pt-4 md:grid-cols-4"><DetailMetric label="当前风险" value={riskText} /><DetailMetric label="风险变化" value={target.risk_change?.label || '暂无数据'} /><DetailMetric label="数据覆盖" value={coverage.summary || '覆盖情况未知'} /><DetailMetric label="下一步" value={action?.label || '继续观察'} /></div>
      </section>

      <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-sm"><div className="mb-3 flex items-center justify-between gap-3"><h2 className="text-sm font-semibold text-[var(--color-text)]">数据覆盖与复核依据</h2><span className="text-xs text-gray-400">缺口不会被解释为稳定</span></div>{coverage.dimensions?.length ? <div className="divide-y divide-[var(--color-border)]">{coverage.dimensions.map(dimension => <div key={dimension.key} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm"><div><span className="font-medium text-[var(--color-text)]">{dimension.label}</span><span className="ml-2 text-xs text-gray-400">{dimension.detail}</span></div><span className={`rounded-full px-2 py-1 text-[11px] ${dimension.status === 'available' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{dimension.status === 'available' ? '可用' : '待补充'}</span></div>)}</div> : <p className="text-sm text-gray-400">暂无数据覆盖明细。</p>}</section>

      <section className="rounded-2xl border border-amber-200 bg-amber-50 p-5"><h2 className="text-sm font-semibold text-amber-900">采购复核建议</h2><p className="mt-2 text-sm leading-6 text-amber-900">{action?.reason || '等待更多监控数据后再安排复核。'}</p><div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-amber-800"><span>建议动作：{action?.label || '继续观察'}</span><span>优先级：{action?.priority || 'low'}</span></div></section>

      <button type="button" onClick={() => onRemove(target)} className="text-xs text-gray-400 hover:text-red-600">移出监控</button>
    </div>
  );
}

function DetailMetric({ label, value }: { label: string; value: string }) {
  return <div><div className="text-xs text-gray-400">{label}</div><div className="mt-1 text-sm font-semibold text-[var(--color-text)]">{value}</div></div>;
}
