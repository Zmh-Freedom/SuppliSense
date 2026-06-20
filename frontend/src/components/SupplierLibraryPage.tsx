import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../query-keys';
import type { SupplierEntry } from '../types';

export default function SupplierLibraryPage() {
  const qc = useQueryClient();
  const [keyword, setKeyword] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: '', categories: '', regions: '' });

  const { data, isLoading } = useQuery({
    queryKey: [...queryKeys.suppliers, keyword] as const,
    queryFn: () =>
      api.get<{ items: SupplierEntry[]; total: number }>(
        `/sourcing/suppliers?keyword=${encodeURIComponent(keyword)}`,
      ),
  });

  const addMutation = useMutation({
    mutationFn: () =>
      api.post('/sourcing/suppliers', {
        name: form.name,
        categories: form.categories.split(',').map(s => s.trim()).filter(Boolean),
        regions: form.regions.split(',').map(s => s.trim()).filter(Boolean),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.suppliers });
      setForm({ name: '', categories: '', regions: '' });
      setShowForm(false);
    },
  });

  return (
    <div className="h-full py-6 px-6 overflow-auto">
      <div className="max-w-3xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-[var(--color-text)]">供应商主库</h2>
          <button
            onClick={() => setShowForm(!showForm)}
            className="text-sm bg-[var(--color-primary-bg)] text-white rounded-xl px-4 py-2 hover:bg-[var(--color-primary-hover)] transition-colors"
          >
            {showForm ? '取消' : '录入供应商'}
          </button>
        </div>

        {showForm && (
          <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-5 space-y-3">
            <input
              value={form.name}
              onChange={e => setForm(p => ({ ...p, name: e.target.value }))}
              placeholder="企业全称 *"
              className="w-full text-sm rounded-xl border border-[var(--color-border)] px-3 py-2 bg-[var(--color-input-bg)]"
            />
            <input
              value={form.categories}
              onChange={e => setForm(p => ({ ...p, categories: e.target.value }))}
              placeholder="主营品类（逗号分隔）"
              className="w-full text-sm rounded-xl border border-[var(--color-border)] px-3 py-2 bg-[var(--color-input-bg)]"
            />
            <input
              value={form.regions}
              onChange={e => setForm(p => ({ ...p, regions: e.target.value }))}
              placeholder="经营地域（逗号分隔）"
              className="w-full text-sm rounded-xl border border-[var(--color-border)] px-3 py-2 bg-[var(--color-input-bg)]"
            />
            <button
              onClick={() => form.name && addMutation.mutate()}
              disabled={!form.name || addMutation.isPending}
              className="bg-[var(--color-primary-bg)] text-white rounded-xl px-4 py-2 text-sm hover:bg-[var(--color-primary-hover)] disabled:opacity-40"
            >
              {addMutation.isPending ? '保存中...' : '保存'}
            </button>
          </div>
        )}

        <div>
          <input
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            placeholder="搜索供应商..."
            className="w-full text-sm rounded-xl border border-[var(--color-border)] px-3 py-2 bg-[var(--color-input-bg)] mb-4"
          />

          {isLoading ? (
            <p className="text-sm text-gray-400">加载中...</p>
          ) : (
            <div className="space-y-2">
              {(data?.items ?? []).map(supplier => (
                <div
                  key={supplier._id}
                  className="flex items-center justify-between bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl px-4 py-3 text-sm"
                >
                  <div>
                    <span className="font-medium text-[var(--color-text)]">{supplier.name}</span>
                    <span className="text-[var(--color-text-muted)] ml-2 text-xs">
                      {supplier.categories?.join(', ') || '未分类'} | {supplier.status}
                    </span>
                  </div>
                </div>
              ))}
              {data?.total === 0 && (
                <p className="text-sm text-gray-400 text-center py-8">暂无供应商数据</p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
