import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useLocation, useSearchParams } from 'react-router-dom';
import { api, getStoredUser } from '../api';
import { queryKeys } from '../query-keys';
import type { ProcurementWorkbench, ProcurementWorkbenchView } from '../types';

const GROUP_LABELS: Record<string, string> = {
  pending_closure: '待我审核关闭',
  overdue: '已逾期',
  due_review: '到期复查',
  high: '高风险事项',
  normal: '其他待办',
};

const GROUP_ORDER: Array<keyof ProcurementWorkbench['counts']> = [
  'pending_closure',
  'overdue',
  'due_review',
  'high',
  'normal',
];

function formatDeadline(value: string | null): string {
  if (!value) return '未设置期限';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? '期限时间未知'
    : `期限 ${date.toLocaleString('zh-CN', { hour12: false })}`;
}

function WorkbenchSkeleton() {
  return <p role="status" className="mt-4 rounded-xl bg-[var(--color-background)] p-5 text-sm text-[var(--color-text-secondary)]">正在加载采购队列和证据覆盖…</p>;
}

export default function WorkQueue() {
  const user = getStoredUser();
  const actor = user ? `${user.username}:${user.role}` : 'anonymous';
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialView = searchParams.get('workbench_view') === 'department' ? 'department' : 'mine';
  const [view, setView] = useState<ProcurementWorkbenchView>(initialView);
  const [selectedDepartment, setSelectedDepartment] = useState(searchParams.get('department_code') || '');
  const departmentCode = view === 'department' ? selectedDepartment : undefined;
  const workbenchQuery = useQuery({
    queryKey: queryKeys.procurementWorkbench(view, departmentCode, actor),
    queryFn: ({ signal }) => api.get<ProcurementWorkbench>(
      '/procurement-workbench',
      {
        view,
        ...(departmentCode ? { department_code: departmentCode } : {}),
      },
      signal,
    ),
    enabled: Boolean(user),
    retry: false,
    refetchOnMount: 'always',
  });

  const data = workbenchQuery.data;
  const departmentCodes = data?.capabilities.department_codes || [];
  const canViewDepartment = Boolean(data?.capabilities.can_view_department && departmentCodes.length);

  return (
    <section aria-labelledby="work-queue-title" className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 shadow-sm sm:p-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2 id="work-queue-title" className="text-base font-semibold text-[var(--color-text)]">待办与证据缺口</h2>
          <p className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">按可执行事项排序。历史风险快照仍需按当前证据核验。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {view === 'department' ? (
            <>
              {(user?.role === 'admin' || departmentCodes.length > 1) && (
                <>
                  <label className="sr-only" htmlFor="workbench-department">选择科室</label>
                  <select
                    id="workbench-department"
                    aria-label="选择科室"
                    value={selectedDepartment}
                    onChange={event => {
                      setSelectedDepartment(event.target.value);
                      setSearchParams(previous => {
                        const next = new URLSearchParams(previous);
                        if (event.target.value) next.set('department_code', event.target.value);
                        else next.delete('department_code');
                        return next;
                      }, { replace: true });
                    }}
                    className="min-h-[40px] max-w-[150px] rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2 text-sm text-[var(--color-text)]"
                  >
                    <option value="">选择科室</option>
                    {departmentCodes.map(code => <option key={code} value={code}>{code}</option>)}
                  </select>
                </>
              )}
              <button type="button" onClick={() => {
                setView('mine');
                setSearchParams(previous => {
                  const next = new URLSearchParams(previous);
                  next.delete('workbench_view');
                  return next;
                }, { replace: true });
              }} className="min-h-[40px] rounded-lg border border-[var(--color-border)] px-3 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)]">我的待办</button>
            </>
          ) : canViewDepartment ? (
            <>
              {(user?.role === 'admin' || departmentCodes.length > 1) && (
                <label className="sr-only" htmlFor="workbench-department">选择科室</label>
              )}
              {(user?.role === 'admin' || departmentCodes.length > 1) ? (
                <select
                  id="workbench-department"
                  aria-label="选择科室"
                  value={selectedDepartment}
                  onChange={event => {
                    setSelectedDepartment(event.target.value);
                    setSearchParams(previous => {
                      const next = new URLSearchParams(previous);
                      if (event.target.value) next.set('department_code', event.target.value);
                      else next.delete('department_code');
                      return next;
                    }, { replace: true });
                  }}
                  className="min-h-[40px] max-w-[150px] rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2 text-sm text-[var(--color-text)]"
                >
                  <option value="">选择科室</option>
                  {departmentCodes.map(code => <option key={code} value={code}>{code}</option>)}
                </select>
              ) : null}
              <button
                type="button"
                disabled={departmentCodes.length > 1 && !selectedDepartment}
                onClick={() => {
                  const nextDepartment = selectedDepartment || departmentCodes[0] || '';
                  if (nextDepartment) {
                    setSelectedDepartment(nextDepartment);
                    setSearchParams(previous => {
                      const next = new URLSearchParams(previous);
                      next.set('workbench_view', 'department');
                      next.set('department_code', nextDepartment);
                      return next;
                    }, { replace: true });
                  }
                  setView('department');
                }}
                className="min-h-[40px] rounded-lg border border-[var(--color-border)] px-3 text-sm text-[var(--color-primary-bg)] hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-50"
              >查看科室队列</button>
            </>
          ) : null}
        </div>
      </div>

      {!user ? (
        <p role="status" className="mt-4 rounded-xl bg-[var(--color-background)] p-4 text-sm text-[var(--color-text-secondary)]">登录后查看你的采购事项。</p>
      ) : workbenchQuery.isPending ? (
        <WorkbenchSkeleton />
      ) : workbenchQuery.isError ? (
        <div role="alert" className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          待办暂时无法加载，当前范围没有被当作空队列。<button type="button" onClick={() => workbenchQuery.refetch()} className="ml-2 font-medium underline">重试</button>
        </div>
      ) : data ? (
        <div className="mt-4 space-y-4">
          {data.sections.cases.status === 'unavailable' ? (
            <div role="alert" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">采购事项暂时无法加载，事项数量未知。</div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
                {GROUP_ORDER.map(group => (
                  <div key={group} className="rounded-xl bg-[var(--color-background)] p-3">
                    <p className="text-[11px] leading-4 text-[var(--color-text-secondary)]">{GROUP_LABELS[group]}</p>
                    <p className="mt-1 text-xl font-semibold text-[var(--color-text)]">{data.counts[group] ?? 0}</p>
                  </div>
                ))}
              </div>
              {data.items.length === 0 ? (
                <p className="rounded-xl border border-dashed border-[var(--color-border)] p-5 text-center text-sm text-[var(--color-text-secondary)]">当前范围没有待处理事项。</p>
              ) : (
                <div className="space-y-2">
                  {data.items.map(item => (
                    <article key={item.case_id} className="flex min-w-0 flex-col gap-2 rounded-xl border border-[var(--color-border)] p-3 sm:flex-row sm:items-center sm:justify-between">
                      <div className="min-w-0">
                        <p className="text-[11px] font-medium text-[var(--color-text-secondary)]">{GROUP_LABELS[item.group]}</p>
                        <Link to={{ pathname: item.detail_url, search: location.search }} className="mt-1 block break-words text-sm font-medium text-[var(--color-primary-bg)] hover:underline">{item.title}</Link>
                        <p className="mt-1 break-words text-xs text-[var(--color-text-secondary)]">{formatDeadline(item.due_at)}{item.status === 'deferred' && item.next_review_at ? ` · 下次复查 ${new Date(item.next_review_at).toLocaleString('zh-CN', { hour12: false })}` : ''}</p>
                      </div>
                      <Link to={{ pathname: item.detail_url, search: location.search }} aria-label={`打开事项：${item.title}`} className="inline-flex min-h-[40px] shrink-0 items-center justify-center rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-primary-bg)] hover:bg-[var(--color-surface-hover)]">打开事项</Link>
                    </article>
                  ))}
                </div>
              )}
              {data.next_cursor && <p className="text-center text-xs text-[var(--color-text-secondary)]">还有更多事项，<Link to="/cases" className="font-medium text-[var(--color-primary-bg)] underline">打开采购事项列表</Link>继续查看。</p>}
            </>
          )}

          <div className="border-t border-[var(--color-border)] pt-4">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <h3 className="text-sm font-semibold text-[var(--color-text)]">监控对象证据覆盖</h3>
              {data.sections.coverage.status === 'available' && data.coverage && <span className="text-xs text-[var(--color-text-secondary)]">共 {data.coverage.target_total} 个对象</span>}
            </div>
            {data.sections.coverage.status === 'unavailable' || !data.coverage ? (
              <p role="alert" className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">证据覆盖暂时无法加载</p>
            ) : (
              <>
                <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
                  <CoverageMetric label="未知" count={data.coverage.unknown_count} total={data.coverage.target_total} />
                  <CoverageMetric label="过期" count={data.coverage.stale_count} total={data.coverage.target_total} />
                  <CoverageMetric label="来源失败" count={data.coverage.failed_count} total={data.coverage.target_total} />
                </div>
                <p className="mt-2 text-[11px] leading-5 text-[var(--color-text-secondary)]">未知、过期和来源失败按对象分别计数，状态可能重叠；每项比例均以全部监控对象为分母。高风险标签沿用风险快照记录，当前判断仍需复核证据。</p>
                {data.coverage.targets.length > 0 ? (
                  <ul className="mt-3 space-y-2">
                    {data.coverage.targets.slice(0, 5).map(target => (
                      <li key={target.target_id} className="flex min-w-0 items-start justify-between gap-3 rounded-xl bg-[var(--color-background)] px-3 py-2.5">
                        <div className="min-w-0">
                          <Link to={`/assess/${encodeURIComponent(target.target_id)}`} className="block break-words text-sm font-medium text-[var(--color-primary-bg)] hover:underline">{target.display_name}</Link>
                          <p className="mt-1 break-words text-xs text-[var(--color-text-secondary)]">{target.missing_dimensions.length ? `待核验：${target.missing_dimensions.join('、')}` : '当前证据仍有待核验项'}</p>
                        </div>
                        <Link to={`/assess/${encodeURIComponent(target.target_id)}`} aria-label={`查看${target.display_name}的风险与证据`} className="min-h-[40px] shrink-0 rounded-lg px-2 py-2 text-xs text-[var(--color-primary-bg)] hover:bg-[var(--color-surface-hover)]">查看</Link>
                      </li>
                    ))}
                  </ul>
                ) : <p className="mt-3 text-xs text-[var(--color-text-secondary)]">当前范围没有监控对象，覆盖比例暂不可计算。</p>}
              </>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function CoverageMetric({ label, count, total }: { label: string; count: number; total: number }) {
  const ratio = total > 0 ? Math.round((count / total) * 100) : 0;
  return (
    <div className="rounded-xl bg-[var(--color-background)] p-3">
      <p className="text-xs text-[var(--color-text-secondary)]">{label}</p>
      <p className="mt-1 text-sm font-semibold text-[var(--color-text)]">{count}/{total} 个对象 <span className="font-normal text-[var(--color-text-secondary)]">· {ratio}%</span></p>
    </div>
  );
}
