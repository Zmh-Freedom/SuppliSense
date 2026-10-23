import { useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api';
import { queryKeys } from '../query-keys';
import type { ProcurementCase, ProcurementCaseList } from '../types';
import ProcurementCasePanel from './ProcurementCasePanel';

type CaseListView = 'mine' | 'department';

function statusLabel(status: string): string {
  return ({
    open: '待接手',
    in_progress: '处理中',
    deferred: '已搁置',
    pending_closure: '待审核关闭',
    closed: '已关闭',
  } as Record<string, string>)[status] || status;
}

function caseTypeLabel(caseType: string): string {
  return ({
    risk_review: '风险复核',
    coverage_gap: '资料缺口',
    identity_review: '主体核验',
  } as Record<string, string>)[caseType] || '采购事项';
}

function formatDate(value?: string | null): string {
  if (!value) return '未设置';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString('zh-CN', { hour12: false });
}

function CaseRow({ item }: { item: ProcurementCase }) {
  return (
    <article className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-[var(--color-background)] px-2 py-1 text-[11px] text-[var(--color-text-secondary)]">{caseTypeLabel(item.case_type)}</span>
            <span className="rounded-full bg-amber-50 px-2 py-1 text-[11px] text-amber-800">{statusLabel(item.status)}</span>
            {item.priority === 'high' && <span className="rounded-full bg-red-50 px-2 py-1 text-[11px] text-red-700">高优先级</span>}
          </div>
          <h3 className="mt-2 truncate text-sm font-semibold text-[var(--color-text)]">{item.title}</h3>
          <p className="mt-1 break-all text-xs text-[var(--color-text-secondary)]">监控对象：{item.monitor_target_id}</p>
        </div>
        <Link to={`/cases/${encodeURIComponent(item.id)}`} className="inline-flex min-h-[40px] shrink-0 items-center rounded-lg border border-[var(--color-border)] px-3 text-xs font-medium text-[var(--color-primary-bg)] hover:bg-[var(--color-surface-hover)]">打开事项</Link>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-[var(--color-border)] pt-3 text-xs sm:grid-cols-4">
        <div><dt className="text-[var(--color-text-secondary)]">负责人</dt><dd className="mt-1 font-medium text-[var(--color-text)]">{item.owner_user_id || '待分派'}</dd></div>
        <div><dt className="text-[var(--color-text-secondary)]">科室</dt><dd className="mt-1 font-medium text-[var(--color-text)]">{item.department_code || '未指定'}</dd></div>
        <div><dt className="text-[var(--color-text-secondary)]">期限</dt><dd className="mt-1 font-medium text-[var(--color-text)]">{formatDate(item.due_at)}</dd></div>
        <div><dt className="text-[var(--color-text-secondary)]">下次复查</dt><dd className="mt-1 font-medium text-[var(--color-text)]">{formatDate(item.next_review_at)}</dd></div>
      </dl>
    </article>
  );
}

function CaseList() {
  const [view, setView] = useState<CaseListView>('mine');
  const filters = { view, limit: '50' };
  const casesQuery = useInfiniteQuery({
    queryKey: queryKeys.procurementCases(filters),
    queryFn: ({ signal, pageParam }) => api.get<ProcurementCaseList>(
      '/procurement-cases',
      pageParam ? { ...filters, cursor: pageParam } : filters,
      signal,
    ),
    initialPageParam: '',
    getNextPageParam: page => page.next_cursor || undefined,
  });
  const pages = casesQuery.data?.pages || [];
  const items = pages.flatMap(page => page.items || []);
  const availableViews = pages[0]?.available_views;

  return (
    <main className="mx-auto max-w-5xl space-y-5 px-4 py-6">
      <header>
        <h1 className="text-lg font-semibold text-[var(--color-text)]">采购事项</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">跟踪责任人、处理回执、关闭审核和技术执行记录。</p>
      </header>
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="事项范围">
        {availableViews?.mine && <button type="button" role="tab" aria-selected={view === 'mine'} onClick={() => setView('mine')} className={`min-h-[40px] rounded-lg px-3 text-sm ${view === 'mine' ? 'bg-[var(--color-primary-bg)] text-white' : 'border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)]'}`}>我的事项</button>}
        {availableViews?.department && <button type="button" role="tab" aria-selected={view === 'department'} onClick={() => setView('department')} className={`min-h-[40px] rounded-lg px-3 text-sm ${view === 'department' ? 'bg-[var(--color-primary-bg)] text-white' : 'border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)]'}`}>本科室事项</button>}
      </div>
      {casesQuery.isLoading ? <p className="rounded-xl border border-[var(--color-border)] p-6 text-sm text-[var(--color-text-secondary)]">正在读取事项…</p> : null}
      {casesQuery.error ? <section role="alert" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">事项列表暂时无法加载，请重试。<button type="button" onClick={() => casesQuery.refetch()} className="ml-2 font-medium underline">重试</button></section> : null}
      {casesQuery.data && !availableViews?.mine && !availableViews?.department ? <p role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-900">当前角色没有采购事项列表权限。</p> : null}
      {!casesQuery.isLoading && !casesQuery.error && (availableViews?.mine || availableViews?.department) && items.length === 0 ? <p className="rounded-xl border border-dashed border-[var(--color-border)] p-8 text-center text-sm text-[var(--color-text-secondary)]">当前范围内没有采购事项。</p> : null}
      {items.length > 0 && <div className="space-y-3">{items.map(({ case: item }) => <CaseRow key={item.id} item={item} />)}</div>}
      {casesQuery.hasNextPage && <button type="button" onClick={() => casesQuery.fetchNextPage()} disabled={casesQuery.isFetchingNextPage} className="min-h-[40px] w-full rounded-lg border border-[var(--color-border)] text-sm text-[var(--color-primary-bg)] disabled:opacity-50">{casesQuery.isFetchingNextPage ? '加载中…' : '加载更多事项'}</button>}
    </main>
  );
}

export default function ProcurementCases() {
  const { caseId } = useParams<{ caseId?: string }>();
  return caseId ? <ProcurementCasePanel caseId={caseId} /> : <CaseList />;
}
