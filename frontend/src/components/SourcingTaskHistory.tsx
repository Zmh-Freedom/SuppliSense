import { useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, getStoredUser } from '../api';
import { queryKeys } from '../query-keys';
import type { SourcingTaskHistoryItem, SourcingTaskListResponse } from '../types';

function taskStatusLabel(status: string): string {
  return ({
    COMPLETED: '已完成',
    PARTIAL: '部分完成（有候选暂存）',
    NEEDS_REVIEW: '待核验（含暂存候选）',
    IDENTITY_REVIEW: '待确认主体',
    CLARIFYING: '待补充条件',
    ACTION_PENDING: '待审批',
    ACTION_FAILED: '动作未完成',
    FAILED: '未完成',
    CANCELLED: '已取消',
    done: '已完成',
    pending: '处理中',
    searching: '检索中',
    draft: '草稿',
  } as Record<string, string>)[status] || '处理中';
}

function taskDate(item: SourcingTaskHistoryItem): string {
  const date = new Date(item.updated_at);
  if (Number.isNaN(date.getTime())) return '时间未知';
  const dateLabel = date.toLocaleDateString('zh-CN');
  return `${dateLabel}（${item.time_basis === 'created_at' ? '创建时间' : '更新时间'}）`;
}

export default function SourcingTaskHistory() {
  const isAdmin = getStoredUser()?.role === 'admin';
  const [scope, setScope] = useState<'mine' | 'all'>('mine');
  const tasksQuery = useInfiniteQuery({
    queryKey: queryKeys.sourcingTaskList(scope),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }): Promise<SourcingTaskListResponse> => {
      const params: Record<string, string> = { limit: '20', scope };
      if (pageParam) params.cursor = pageParam;
      return api.get<SourcingTaskListResponse>('/sourcing-workspace/tasks', params);
    },
    getNextPageParam: lastPage => lastPage.next_cursor ?? undefined,
  });

  const pages = tasksQuery.data?.pages ?? [];
  const sourceStatus = pages.at(-1)?.sources;
  const items = pages.flatMap(page => page.items).filter((item, index, all) =>
    all.findIndex(candidate => candidate.source_type === item.source_type && candidate.source_id === item.source_id) === index,
  );
  const hasUnavailableSource = Object.values(sourceStatus ?? {}).some(source => source.status === 'unavailable');
  const hasDisabledSource = Object.values(sourceStatus ?? {}).some(source => source.status === 'disabled');
  const unavailableLabels = Object.entries(sourceStatus ?? {})
    .filter(([, source]) => source.status === 'unavailable')
    .map(([sourceType]) => sourceType === 'legacy_request' ? '兼容寻源历史暂不可用' : '智能寻源历史暂不可用');

  return <section aria-label="寻源任务历史" className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h3 className="text-sm font-semibold text-[var(--color-text-secondary)]">我的寻源任务</h3>
        <p className="mt-1 text-xs text-[var(--color-text-secondary)]">智能寻源和兼容寻源分别保留原任务来源与结果。</p>
      </div>
      {isAdmin && <label className="flex min-h-[40px] items-center gap-2 text-xs text-[var(--color-text-secondary)]">
        <input type="checkbox" checked={scope === 'all'} onChange={event => setScope(event.target.checked ? 'all' : 'mine')} />
        查看全局任务
      </label>}
    </div>

    {hasUnavailableSource && <div role="status" className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
      <span>{unavailableLabels.join('；')}；当前页面不继续翻页，请重试以重新合并来源。</span>
      <button type="button" onClick={() => void tasksQuery.refetch()} className="min-h-[36px] rounded-lg border border-amber-300 bg-white px-3">重试当前页</button>
    </div>}
    {hasDisabledSource && <p role="status" className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">当前灰度未开放部分智能寻源任务；仍可查看可用的兼容任务。</p>}
    {tasksQuery.isError && <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
      <span>寻源历史加载失败，请重试当前页。</span>
      <button type="button" onClick={() => void tasksQuery.refetch()} className="min-h-[36px] rounded-lg border border-amber-300 bg-white px-3">重试当前页</button>
    </div>}

    {tasksQuery.isLoading && <p className="text-xs text-[var(--color-text-secondary)]">正在读取寻源历史…</p>}
    {items.map(item => <Link
      key={`${item.source_type}:${item.source_id}`}
      to={item.resume_url}
      className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-sm hover:bg-[var(--color-surface-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
    >
      <span className="min-w-0">
        <span className="mr-2 rounded-full bg-[var(--color-background)] px-2 py-1 text-[11px] text-[var(--color-text-secondary)]">{item.source_type === 'agent_run' ? '智能寻源' : '兼容寻源'}</span>
        <span className="font-medium text-[var(--color-text)]">{item.title}</span>
      </span>
      <span className="flex items-center gap-3 text-xs text-[var(--color-text-secondary)]">
        <span>{taskStatusLabel(item.status)}</span>
        <span>{taskDate(item)}</span>
        <span aria-hidden="true">打开任务 →</span>
      </span>
    </Link>)}

    {!tasksQuery.isLoading && !tasksQuery.isError && items.length === 0 && !hasUnavailableSource && <p className="rounded-xl border border-dashed border-[var(--color-border)] px-4 py-5 text-center text-xs text-[var(--color-text-secondary)]">暂无寻源任务。</p>}
    {tasksQuery.hasNextPage && !hasUnavailableSource && <button
      type="button"
      onClick={() => void tasksQuery.fetchNextPage()}
      disabled={tasksQuery.isFetchingNextPage}
      className="min-h-[40px] rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-text-secondary)] disabled:opacity-50"
    >{tasksQuery.isFetchingNextPage ? '正在加载…' : '加载更多任务'}</button>}
  </section>;
}
