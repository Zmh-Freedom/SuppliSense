import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../query-keys';
import type { SupplierEntry } from '../types';

function formatEstablishTime(value: string): string {
  const timestamp = parseInt(value, 10);
  if (!Number.isNaN(timestamp) && timestamp > 0) {
    return new Date(timestamp).toISOString().slice(0, 10);
  }
  return value.slice(0, 10);
}

function uniqueDisplayValues(values?: string[]): string[] {
  return [...new Set(
    (values ?? [])
      .flatMap(value => value.split(/[，,、]/))
      .map(value => value.trim())
      .filter(Boolean),
  )];
}

const STATUS_LABELS: Record<string, string> = {
  approved: '正式供应商',
  active: '正式供应商',
  prospective: '潜在候选',
  suspended: '暂停合作',
  blocked: '禁止合作',
  deprecated: '已退出',
};

const STATUS_COLORS: Record<string, string> = {
  approved: 'text-green-600 bg-green-50',
  active: 'text-green-600 bg-green-50',
  prospective: 'text-blue-500 bg-blue-50',
  suspended: 'text-amber-600 bg-amber-50',
  blocked: 'text-red-500 bg-red-50',
  deprecated: 'text-gray-400 bg-gray-100',
};

function sourceLabel(source?: string): string {
  if (source?.toLowerCase().includes('feishu')) return '飞书';
  return source ? '外部同步' : '历史缓存';
}

export default function SupplierLibraryPage() {
  const navigate = useNavigate();
  const [keyword, setKeyword] = useState('');
  const [showAll, setShowAll] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: [...queryKeys.suppliers, keyword, showAll] as const,
    queryFn: () => api.get<{ items: SupplierEntry[]; total: number }>(
      `/sourcing/suppliers?keyword=${encodeURIComponent(keyword)}&hide_bare=${!showAll}`,
    ),
  });

  return (
    <div className="h-full py-6 px-6 overflow-auto">
      <div className="max-w-4xl mx-auto space-y-6">
        <div>
          <div className="flex items-center justify-between gap-4">
            <div>
              <h2 className="text-xl font-bold text-[var(--color-text)]">供应商库</h2>
              <p className="text-sm text-[var(--color-text-secondary)] mt-1">
                正式供应商主数据只读视图，用于查看、分析和加入风险监控
              </p>
            </div>
            <span className="shrink-0 text-xs rounded-full px-3 py-1 bg-blue-50 text-blue-600">
              只读主数据
            </span>
          </div>
        </div>

        <div className="rounded-xl border border-blue-200 bg-blue-50/70 px-4 py-3 text-xs text-blue-800">
          <div className="font-medium">数据来源：供应商主数据</div>
          <div className="mt-1 text-blue-700">
            当前按本地只读快照展示；已启用飞书同步时以飞书正式供应商数据为准。此页面不执行录入、编辑或准入操作。
          </div>
        </div>

        <div className="flex items-center gap-2">
          <input
            value={keyword}
            onChange={event => setKeyword(event.target.value)}
            placeholder="搜索供应商名称、统一社会信用代码..."
            className="flex-1 rounded-xl border border-[var(--color-border)] bg-[var(--color-input-bg)] px-3 py-2 text-sm focus:border-[var(--color-focus-ring)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
          />
          <button
            onClick={() => setShowAll(value => !value)}
            className="shrink-0 text-xs text-[var(--color-text-secondary)] border border-[var(--color-border)] rounded-lg px-3 py-2 hover:bg-[var(--color-surface-hover)]"
          >
            {showAll ? '隐藏空壳记录' : '显示全部'}
          </button>
        </div>

        {isError && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
            供应商主数据暂时无法加载，请检查后端或数据同步状态。
          </div>
        )}

        <div className="flex items-center justify-between text-sm">
          <h3 className="font-semibold text-[var(--color-text)]">正式供应商</h3>
          {data?.total !== undefined && <span className="text-xs text-gray-400">{data.total} 家</span>}
        </div>

        {isLoading ? (
          <p className="text-sm text-gray-400">加载中...</p>
        ) : (
          <div className="space-y-2">
            {(data?.items ?? []).map(supplier => (
              <article
                key={supplier._id}
                className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl px-4 py-3"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        onClick={() => navigate(`/suppliers/${supplier._id}`)}
                        className="font-medium text-[var(--color-primary-bg)] hover:underline text-left"
                      >
                        {supplier.name}
                      </button>
                      <span className={`text-xs px-1.5 py-0.5 rounded-full ${STATUS_COLORS[supplier.status] || 'text-gray-500 bg-gray-100'}`}>
                        {STATUS_LABELS[supplier.status] || supplier.status || '状态未知'}
                      </span>
                      <span className="text-[10px] text-gray-400 border border-gray-200 rounded-full px-1.5 py-0.5">
                        来源：{sourceLabel(supplier.source)}
                      </span>
                    </div>
                    <div className="text-[var(--color-text-muted)] text-xs mt-1 space-x-3">
                      <span>{uniqueDisplayValues(supplier.categories).join(', ') || '未分类'}</span>
                      {supplier.industry && <span>{supplier.industry}</span>}
                      {uniqueDisplayValues(supplier.regions).length > 0 && <span>{uniqueDisplayValues(supplier.regions).join(', ')}</span>}
                    </div>
                    {uniqueDisplayValues(supplier.products).length ? (
                      <div className="text-[var(--color-text-muted)] text-xs mt-1">
                        <span className="text-gray-400">供货产品：</span>{uniqueDisplayValues(supplier.products).join(', ')}
                      </div>
                    ) : null}
                    <div className="text-gray-400 text-[10px] mt-1 space-x-3">
                      {supplier.unified_code && <span>统一社会信用代码：{supplier.unified_code}</span>}
                      {supplier.legal_person && <span>法人：{supplier.legal_person}</span>}
                      {supplier.establish_time && <span>成立：{formatEstablishTime(supplier.establish_time)}</span>}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={() => navigate(`/chat?q=${encodeURIComponent(`请分析${supplier.name}的风险情况`)}`)}
                      className="text-xs border border-[var(--color-border)] rounded-lg px-3 py-1.5 hover:bg-[var(--color-surface-hover)]"
                    >
                      问 AI
                    </button>
                    <button
                      onClick={() => navigate(`/suppliers/${supplier._id}`)}
                      className="text-xs text-[var(--color-primary-bg)] hover:underline px-1"
                    >
                      查看画像
                    </button>
                  </div>
                </div>
              </article>
            ))}
            {(data?.items ?? []).length === 0 && (
              <p className="text-sm text-gray-400 text-center py-8">暂无供应商数据</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
