import { useState } from 'react';
import type { MonitorTarget } from '../types';
import { getRiskColor } from '../riskColors';

interface CoverageSummary {
  status?: string;
  summary?: string;
  missing_dimensions?: string[];
}

interface MonitoringWorkbenchProps {
  targets: MonitorTarget[];
  onInvestigate: (query: string) => void;
  onUpload: (file: File) => void;
  onRefresh: () => void;
  onAnalyze: (target: MonitorTarget) => void;
  onAction: (target: MonitorTarget) => void;
  onApprove: (target: MonitorTarget) => void;
  onReject: (target: MonitorTarget) => void;
  onExecuteTask: (target: MonitorTarget) => void;
  onOpen: (target: MonitorTarget) => void;
  onRemove: (target: MonitorTarget) => void;
  isInvestigating: boolean;
  isUploading: boolean;
  isRefreshing: boolean;
  defaultOpen?: boolean;
}

const TARGET_TYPE_LABELS: Record<string, string> = {
  formal_supplier: '正式供应商',
  external_candidate: '外部候选',
  company: '企业主体',
};

const IDENTITY_LABELS: Record<string, string> = {
  verified: '已核验',
  candidate: '待核验',
  unresolved: '未解析',
};

function coverageOf(target: MonitorTarget): CoverageSummary {
  return (target.data_coverage || {}) as CoverageSummary;
}

function targetTypeLabel(target: MonitorTarget): string {
  return TARGET_TYPE_LABELS[target.target_type] || '监控对象';
}

function identityLabel(target: MonitorTarget): string {
  return IDENTITY_LABELS[target.identity_status || ''] || '待确认';
}

function formatCheckedAt(value?: string | null): string {
  if (!value) return '尚未检查';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间未知';
  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
}

function riskTone(target: MonitorTarget): { color: string; background: string } {
  if (target.risk_level === '高风险') return { color: getRiskColor(70), background: '#fff1f2' };
  if (target.risk_level === '中风险') return { color: getRiskColor(40), background: '#fffbeb' };
  if (target.risk_level === '低风险') return { color: getRiskColor(10), background: '#ecfdf5' };
  return { color: '#737373', background: '#f5f5f4' };
}

function trendTone(status?: string): { color: string; background: string } {
  if (status === 'deteriorating') return { color: '#b91c1c', background: '#fef2f2' };
  if (status === 'improving') return { color: '#047857', background: '#ecfdf5' };
  if (status === 'stable') return { color: '#57534e', background: '#f5f5f4' };
  return { color: '#a16207', background: '#fffbeb' };
}

export default function MonitoringWorkbench({
  targets,
  onInvestigate,
  onUpload,
  onRefresh,
  onAnalyze,
  onAction,
  onApprove,
  onReject,
  onExecuteTask,
  onOpen,
  onRemove,
  isInvestigating,
  isUploading,
  isRefreshing,
  defaultOpen = false,
}: MonitoringWorkbenchProps) {
  const [newName, setNewName] = useState('');
  const [open, setOpen] = useState(defaultOpen);
  const activeTargets = targets.filter(target => target.monitor_status !== 'removed');
  const reviewCount = activeTargets.filter(target => target.next_action?.priority === 'high').length;
  const insufficientCount = activeTargets.filter(target => coverageOf(target).status !== 'complete').length;
  const readyCount = activeTargets.filter(target => target.next_action?.priority === 'low').length;

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = newName.trim();
    if (!name || isInvestigating) return;
    onInvestigate(name);
  };

  return (
    <section className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl shadow-sm">
      <button type="button" onClick={() => setOpen(value => !value)} aria-expanded={open} className="flex w-full items-center justify-between gap-4 p-5 text-left">
        <span>
          <span className="text-sm font-semibold text-[var(--color-text)]">采购复核工作台</span>
          <span className="ml-2 text-xs text-gray-400">{activeTargets.length} 个监控对象</span>
          <span className="block mt-1 text-xs text-gray-400">按身份、数据覆盖和风险变化安排复核优先级</span>
        </span>
        <span className={`text-gray-400 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true">⌄</span>
      </button>

      <div className="grid grid-cols-3 gap-2 px-5 pb-5">
        <div className="rounded-xl border border-red-100 bg-red-50 px-3 py-2">
          <div className="text-lg font-semibold text-red-700">{reviewCount}</div>
          <div className="text-[11px] text-red-600">优先复核</div>
        </div>
        <div className="rounded-xl border border-amber-100 bg-amber-50 px-3 py-2">
          <div className="text-lg font-semibold text-amber-700">{insufficientCount}</div>
          <div className="text-[11px] text-amber-600">数据未覆盖</div>
        </div>
        <div className="rounded-xl border border-emerald-100 bg-emerald-50 px-3 py-2">
          <div className="text-lg font-semibold text-emerald-700">{readyCount}</div>
          <div className="text-[11px] text-emerald-600">继续观察</div>
        </div>
      </div>

      {open && <div className="border-t border-[var(--color-border)] p-5">
        <div className="mb-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-background)] px-4 py-3 text-xs leading-5 text-[var(--color-text-secondary)]">
          这里管理的是需要持续复核的监控对象。外部候选在完成主体核验前只作为待核验对象展示；数据不足会明确标记，不会被解释为风险稳定。
        </div>

        <form onSubmit={submit} className="mb-3 flex gap-2">
          <input
            value={newName}
            onChange={event => setNewName(event.target.value)}
            placeholder="输入供应商名称、代码或统一社会信用代码"
            className="min-h-[44px] flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-input-bg)] px-3 py-2 text-sm placeholder-gray-300 focus:border-[var(--color-border-focus)] focus:outline-none"
          />
          <button type="submit" disabled={isInvestigating || !newName.trim()} className="min-h-[44px] shrink-0 rounded-lg bg-[var(--color-primary-bg)] px-4 py-2 text-sm text-white transition-opacity hover:bg-[var(--color-primary-hover)] disabled:opacity-30">
            {isInvestigating ? '调查中…' : '开始调查'}
          </button>
        </form>

        <div className="mb-4 flex gap-2">
          <label className="flex min-h-[40px] flex-1 cursor-pointer items-center justify-center rounded-lg border border-dashed border-[var(--color-border)] py-2 text-xs text-gray-500 hover:text-gray-700">
            <span>{isUploading ? '导入中…' : 'Excel 批量导入'}</span>
            <input type="file" accept=".xlsx" onChange={event => { const file = event.target.files?.[0]; if (file) onUpload(file); }} className="hidden" disabled={isUploading} />
          </label>
          <button type="button" disabled={isRefreshing} onClick={onRefresh} className="min-h-[40px] flex-1 rounded-lg border border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-50">
            {isRefreshing ? '检查中…' : '重新检查风险快照'}
          </button>
        </div>

        {activeTargets.length === 0 ? (
          <div className="rounded-xl border border-dashed border-[var(--color-border)] px-4 py-8 text-center text-sm text-gray-400">暂无监控对象。输入供应商线索后，系统会先自动调查主体和资料覆盖。</div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-[var(--color-border)]">
            <table className="w-full min-w-[820px] border-collapse text-left">
              <thead className="bg-[var(--color-background)] text-[11px] text-gray-500">
                <tr>
                  <th className="px-3 py-3 font-medium">监控对象</th>
                  <th className="px-3 py-3 font-medium">身份状态</th>
                  <th className="px-3 py-3 font-medium">风险变化</th>
                  <th className="px-3 py-3 font-medium">数据覆盖</th>
              <th className="px-3 py-3 font-medium">下一步</th>
              <th className="px-3 py-3 font-medium">复核任务</th>
                  <th className="px-3 py-3 font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {activeTargets.map(target => {
                  const coverage = coverageOf(target);
                  const risk = riskTone(target);
                  const trend = trendTone(target.risk_change?.status);
                  return (
                    <tr key={target.monitor_target_id} className="align-top hover:bg-[var(--color-surface-hover)]">
                      <td className="px-3 py-3">
                        <button type="button" onClick={() => onOpen(target)} className="max-w-[220px] text-left text-sm font-medium text-[var(--color-text)] hover:text-[var(--color-primary-bg)]">
                          <span className="block truncate">{target.display_name || target.company_name || '未命名对象'}</span>
                        </button>
                        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-gray-400">
                          <span className="rounded-full bg-gray-100 px-2 py-0.5">{targetTypeLabel(target)}</span>
                          {target.is_responsible_supplier && <span className="rounded-full bg-blue-50 px-2 py-0.5 text-blue-700">我负责</span>}
                          {target.supplier_code && <span>{target.supplier_code}</span>}
                          <span>上次检查 {formatCheckedAt(target.last_checked_at)}</span>
                        </div>
                      </td>
                      <td className="px-3 py-3">
                        <span className={`inline-flex rounded-full px-2 py-1 text-[11px] ${target.identity_status === 'verified' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>
                          {identityLabel(target)}
                        </span>
                      </td>
                      <td className="px-3 py-3">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="rounded-full px-2 py-1 text-[11px] font-medium" style={{ color: risk.color, background: risk.background }}>
                            {target.risk_score != null ? `${target.risk_level || '未知'} ${target.risk_score}/100` : '暂无风险快照'}
                          </span>
                          <span className="rounded-full px-2 py-1 text-[11px]" style={{ color: trend.color, background: trend.background }}>
                            {target.risk_change?.label || '暂无数据'}{target.risk_change?.delta != null ? ` ${target.risk_change.delta > 0 ? '+' : ''}${target.risk_change.delta}` : ''}
                          </span>
                        </div>
                      </td>
                      <td className="px-3 py-3">
                        <div className="text-xs font-medium text-[var(--color-text)]">{coverage.summary || '覆盖情况未知'}</div>
                        {coverage.missing_dimensions && coverage.missing_dimensions.length > 0 && <div className="mt-1 max-w-[150px] text-[10px] leading-4 text-gray-400">缺少：{coverage.missing_dimensions.slice(0, 2).join('、')}</div>}
                      </td>
                      <td className="px-3 py-3">
                        <button type="button" onClick={() => onAction(target)} disabled={target.review_task?.status === 'pending_approval' || target.review_task?.status === 'executing'} className={`text-left text-xs font-medium hover:underline disabled:cursor-not-allowed disabled:no-underline ${target.next_action?.priority === 'high' ? 'text-red-700' : target.next_action?.priority === 'medium' ? 'text-amber-700' : 'text-emerald-700'}`}>{target.next_action?.label || '继续观察'}</button>
                        <div className="mt-1 max-w-[170px] text-[10px] leading-4 text-gray-400">{target.next_action?.reason || '等待更多监控数据'}</div>
                      </td>
                      <td className="px-3 py-3">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <TaskStatusBadge target={target} />
                          {target.review_task?.status === 'pending_approval' && <><button type="button" onClick={() => onApprove(target)} className="rounded-md bg-emerald-600 px-2 py-1 text-[11px] text-white">批准</button><button type="button" onClick={() => onReject(target)} className="rounded-md border border-red-200 px-2 py-1 text-[11px] text-red-600">拒绝</button></>}
                          {target.review_task?.status === 'approved' && <button type="button" onClick={() => onExecuteTask(target)} className="rounded-md bg-[var(--color-primary-bg)] px-2 py-1 text-[11px] text-white">执行</button>}
                        </div>
                        {target.review_task?.result?.summary && <div className="mt-1 max-w-[180px] text-[10px] leading-4 text-gray-400">{target.review_task.result.summary}</div>}
                      </td>
                      <td className="px-3 py-3">
                        <div className="flex items-center gap-2 whitespace-nowrap">
                          <button type="button" onClick={() => onAnalyze(target)} className="rounded-md px-2 py-1 text-xs text-[var(--color-primary-bg)] hover:bg-[var(--color-primary-bg)]/10">Agent 复核</button>
                          <button type="button" onClick={() => onRemove(target)} className="rounded-md px-2 py-1 text-xs text-gray-400 hover:bg-red-50 hover:text-red-600">移除</button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>}
    </section>
  );
}

function TaskStatusBadge({ target }: { target: MonitorTarget }) {
  const status = target.review_task?.status;
  const labels: Record<string, string> = { pending_approval: '待审批', approved: '已审批', executing: '执行中', completed: '已完成', needs_review: '待人工复核', rejected: '已拒绝', failed: '执行失败', cancelled: '已取消' };
  const tone = status === 'completed' ? 'bg-emerald-50 text-emerald-700' : status === 'failed' ? 'bg-red-50 text-red-700' : status ? 'bg-amber-50 text-amber-700' : 'bg-gray-100 text-gray-500';
  return <span className={`rounded-full px-2 py-1 text-[11px] ${tone}`}>{labels[status || ''] || '未创建'}</span>;
}
