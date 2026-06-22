import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, getStoredUser } from '../api';
import { queryKeys } from '../query-keys';
import type { SupplierEntry, AccessApplicationItem } from '../types';

export default function SupplierLibraryPage() {
  const qc = useQueryClient();
  const user = getStoredUser();
  const canManage = user?.role === 'admin' || user?.role === 'analyst';
  const [keyword, setKeyword] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: '', categories: '', regions: '' });

  // edit state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState({ name: '', categories: '', regions: '', status: '' });

  // approval state
  const [approvalStatus, setApprovalStatus] = useState<string>('pending');
  const [showApproval, setShowApproval] = useState(false);

  // tianyancha search state
  const [tycKeyword, setTycKeyword] = useState('');
  const [tycIndustry, setTycIndustry] = useState('');
  const [tycRegion, setTycRegion] = useState('');
  const [tycResult, setTycResult] = useState<{ imported: number; skipped: number; errors: string[] } | null>(null);

  const tycMutation = useMutation({
    mutationFn: () => api.post<{ imported: number; skipped: number; errors: string[] }>(
      `/sourcing/suppliers/import-tianyancha?keyword=${encodeURIComponent(tycKeyword)}&industry=${encodeURIComponent(tycIndustry)}&region=${encodeURIComponent(tycRegion)}&max_results=50`,
    ),
    onSuccess: (data) => {
      setTycResult(data);
      qc.invalidateQueries({ queryKey: queryKeys.suppliers });
    },
    onError: () => setTycResult({ imported: 0, skipped: 0, errors: ['请求失败'] }),
  });

  useEffect(() => {
    if (tycResult) {
      const t = setTimeout(() => setTycResult(null), 10000);
      return () => clearTimeout(t);
    }
  }, [tycResult]);

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

  // ---- Excel Import ----
  const [importResult, setImportResult] = useState<{ imported: number; skipped: number; errors: string[] } | null>(null);

  const handleFileImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    try {
      const res = await fetch('/api/v1/sourcing/suppliers/import', {
        method: 'POST',
        body: formData,
        headers: { Authorization: `Bearer ${localStorage.getItem('token') || ''}` },
      });
      const data = await res.json();
      setImportResult(data);
      qc.invalidateQueries({ queryKey: queryKeys.suppliers });
    } catch {
      setImportResult({ imported: 0, skipped: 0, errors: ['上传失败'] });
    }
    e.target.value = '';
  };

  useEffect(() => {
    if (importResult) {
      const t = setTimeout(() => setImportResult(null), 8000);
      return () => clearTimeout(t);
    }
  }, [importResult]);

  // ---- Supplier Edit ----
  const updateMutation = useMutation({
    mutationFn: (sid: string) =>
      api.put(`/sourcing/suppliers/${sid}`, {
        name: editForm.name || undefined,
        categories: editForm.categories
          ? editForm.categories.split(',').map(s => s.trim()).filter(Boolean)
          : undefined,
        regions: editForm.regions
          ? editForm.regions.split(',').map(s => s.trim()).filter(Boolean)
          : undefined,
        status: editForm.status || undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.suppliers });
      setEditingId(null);
    },
  });

  function startEdit(s: SupplierEntry) {
    setEditingId(s._id);
    setEditForm({
      name: s.name,
      categories: (s.categories ?? []).join(', '),
      regions: (s.regions ?? []).join(', '),
      status: s.status,
    });
  }

  // ---- Access Applications (admin) ----
  const appQuery = useQuery({
    queryKey: queryKeys.accessApplications(approvalStatus),
    queryFn: () =>
      api.get<{ items: AccessApplicationItem[]; total: number }>(
        `/access-applications?status=${approvalStatus}`,
      ),
    enabled: canManage && showApproval,
  });

  const approveMutation = useMutation({
    mutationFn: (aid: string) => api.post(`/access-applications/${aid}/approve`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.accessApplications() }),
  });

  const rejectMutation = useMutation({
    mutationFn: (aid: string) => api.post(`/access-applications/${aid}/reject`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.accessApplications() }),
  });

  const statusLabels: Record<string, string> = { pending: '待审批', approved: '已通过', rejected: '已拒绝' };
  const statusFilters = ['pending', 'approved', 'rejected'];

  const suppStatusColor: Record<string, string> = {
    approved: 'text-green-600 bg-green-50',
    prospective: 'text-blue-500 bg-blue-50',
    blocked: 'text-red-500 bg-red-50',
    deprecated: 'text-gray-400 bg-gray-100',
  };

  return (
    <div className="h-full py-6 px-6 overflow-auto">
      <div className="max-w-3xl mx-auto space-y-6">
        {/* ---- 准入审批 (admin) ---- */}
        {canManage && (
          <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
            <button
              onClick={() => setShowApproval(!showApproval)}
              className="flex items-center justify-between w-full text-left"
            >
              <h2 className="text-lg font-bold text-[var(--color-text)]">准入审批</h2>
              <span className="text-xs text-[var(--color-text-muted)]">
                {showApproval ? '收起' : '展开'}
              </span>
            </button>

            {showApproval && (
              <div className="mt-4 space-y-3">
                <div className="flex gap-2">
                  {statusFilters.map(s => (
                    <button
                      key={s}
                      onClick={() => setApprovalStatus(s)}
                      className={`text-xs rounded-lg px-3 py-1 transition-colors ${
                        approvalStatus === s
                          ? 'bg-[var(--color-primary-bg)] text-white'
                          : 'border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)]'
                      }`}
                    >
                      {statusLabels[s]}
                    </button>
                  ))}
                </div>

                {(appQuery.data?.items ?? []).length === 0 ? (
                  <p className="text-xs text-gray-400 py-2">暂无{statusLabels[approvalStatus]}申请</p>
                ) : (
                  <div className="space-y-2">
                    {appQuery.data?.items.map(app => (
                      <div
                        key={app.application_id}
                        className="flex items-center justify-between bg-[var(--color-input-bg)] rounded-xl px-4 py-3 text-sm"
                      >
                        <div>
                          <span className="font-medium text-[var(--color-text)]">{app.supplier_name}</span>
                          <span className="text-[var(--color-text-muted)] ml-2 text-xs">
                            {app.applicant_id} · {app.created_at?.slice(0, 10)}
                          </span>
                          {app.status !== 'pending' && (
                            <span className="text-[var(--color-text-muted)] ml-2 text-xs">
                              {app.reviewer_id} · {app.reviewed_at?.slice(0, 10)}
                            </span>
                          )}
                        </div>
                        {app.status === 'pending' && (
                          <div className="flex gap-2">
                            <button
                              onClick={() => approveMutation.mutate(app.application_id)}
                              disabled={approveMutation.isPending}
                              className="text-xs text-green-600 border border-green-300 rounded-lg px-3 py-1 hover:bg-green-50 disabled:opacity-40"
                            >
                              通过
                            </button>
                            <button
                              onClick={() => rejectMutation.mutate(app.application_id)}
                              disabled={rejectMutation.isPending}
                              className="text-xs text-red-500 border border-red-300 rounded-lg px-3 py-1 hover:bg-red-50 disabled:opacity-40"
                            >
                              拒绝
                            </button>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ---- 供应商管理 ---- */}
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-[var(--color-text)]">
            供应商主库
            {data?.total !== undefined && <span className="text-sm font-normal text-gray-400 ml-2">{data.total} 家</span>}
          </h2>
          <div className="flex gap-2">
            {canManage && (
              <label className="text-sm border border-[var(--color-border)] rounded-xl px-4 py-2 cursor-pointer hover:bg-[var(--color-surface-hover)] transition-colors">
                批量导入
                <input type="file" accept=".xlsx,.xls" onChange={handleFileImport} className="hidden" />
              </label>
            )}
            <button
              onClick={() => setShowForm(!showForm)}
              className="text-sm bg-[var(--color-primary-bg)] text-white rounded-xl px-4 py-2 hover:bg-[var(--color-primary-hover)] transition-colors"
            >
              {showForm ? '取消' : '录入供应商'}
            </button>
          </div>
        </div>

        {importResult && (
          <div className={`text-xs rounded-xl px-4 py-3 ${importResult.errors.length > 0 ? 'bg-amber-50 border border-amber-200' : 'bg-green-50 border border-green-200'}`}>
            导入完成：新增 {importResult.imported} 家，跳过 {importResult.skipped} 家（重复）
            {importResult.errors.length > 0 && (
              <div className="mt-1 text-red-500">{importResult.errors.join('；')}</div>
            )}
          </div>
        )}

        {tycResult && (
          <div className={`text-xs rounded-xl px-4 py-3 ${tycResult.errors.length > 0 ? 'bg-amber-50 border border-amber-200' : 'bg-green-50 border border-green-200'}`}>
            天眼查导入完成：新增 {tycResult.imported} 家，跳过 {tycResult.skipped} 家
            {tycResult.errors.length > 0 && (
              <div className="mt-1 text-red-500">{tycResult.errors.join('；')}</div>
            )}
          </div>
        )}

        {/* 天眼查搜索导入 */}
        {canManage && (
          <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-4 shadow-sm space-y-3">
            <h3 className="text-sm font-semibold text-[var(--color-text)]">天眼查批量导入</h3>
            <div className="grid grid-cols-3 gap-2">
              <select
                value={tycIndustry}
                onChange={e => setTycIndustry(e.target.value)}
                className="text-xs rounded-lg border border-[var(--color-border)] px-3 py-2 bg-[var(--color-input-bg)]"
              >
                <option value="">选择行业...</option>
                <option value="381">电机制造</option>
                <option value="382">输配电及控制设备</option>
                <option value="401">电子器件</option>
                <option value="402">电子元件</option>
                <option value="356">电子专用设备</option>
                <option value="651">软件开发</option>
                <option value="652">信息技术服务</option>
                <option value="292">塑料制品</option>
                <option value="223">纸制品</option>
                <option value="231">印刷</option>
                <option value="359">环保设备</option>
                <option value="342">金属加工机械</option>
                <option value="345">通用零部件</option>
              </select>
              <input
                value={tycRegion}
                onChange={e => setTycRegion(e.target.value)}
                placeholder="地域代码：330100(杭州)"
                className="text-xs rounded-lg border border-[var(--color-border)] px-3 py-2 bg-[var(--color-input-bg)]"
              />
              <input
                value={tycKeyword}
                onChange={e => setTycKeyword(e.target.value)}
                placeholder="公司名关键词（可选）"
                className="text-xs rounded-lg border border-[var(--color-border)] px-3 py-2 bg-[var(--color-input-bg)]"
              />
            </div>
            <p className="text-[10px] text-gray-400">选择行业代码（GB/T 4754-2017），可选填地域代码缩小范围。每次最多导入 50 家。</p>
            <button
              onClick={() => tycMutation.mutate()}
              disabled={!tycIndustry || tycMutation.isPending}
              className="text-sm bg-[var(--color-primary-bg)] text-white rounded-xl px-6 py-2 hover:bg-[var(--color-primary-hover)] disabled:opacity-40 transition-colors"
            >
              {tycMutation.isPending ? '搜索中...' : '搜索并导入'}
            </button>
          </div>
        )}

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
                  {editingId === supplier._id ? (
                    <div className="flex-1 space-y-2">
                      <input
                        value={editForm.name}
                        onChange={e => setEditForm(p => ({ ...p, name: e.target.value }))}
                        placeholder="企业全称"
                        className="w-full text-xs rounded-lg border border-[var(--color-border)] px-2 py-1 bg-[var(--color-input-bg)]"
                      />
                      <div className="flex gap-2">
                        <input
                          value={editForm.categories}
                          onChange={e => setEditForm(p => ({ ...p, categories: e.target.value }))}
                          placeholder="品类"
                          className="flex-1 text-xs rounded-lg border border-[var(--color-border)] px-2 py-1 bg-[var(--color-input-bg)]"
                        />
                        <input
                          value={editForm.regions}
                          onChange={e => setEditForm(p => ({ ...p, regions: e.target.value }))}
                          placeholder="地域"
                          className="flex-1 text-xs rounded-lg border border-[var(--color-border)] px-2 py-1 bg-[var(--color-input-bg)]"
                        />
                        <select
                          value={editForm.status}
                          onChange={e => setEditForm(p => ({ ...p, status: e.target.value }))}
                          className="text-xs rounded-lg border border-[var(--color-border)] px-2 py-1 bg-[var(--color-input-bg)]"
                        >
                          <option value="prospective">待考察</option>
                          <option value="approved">已准入</option>
                          <option value="blocked">已拉黑</option>
                          <option value="deprecated">已停用</option>
                        </select>
                      </div>
                      <div className="flex gap-2">
                        <button
                          onClick={() => updateMutation.mutate(supplier._id)}
                          disabled={updateMutation.isPending}
                          className="text-xs bg-[var(--color-primary-bg)] text-white rounded-lg px-3 py-1 hover:bg-[var(--color-primary-hover)] disabled:opacity-40"
                        >
                          保存
                        </button>
                        <button
                          onClick={() => setEditingId(null)}
                          className="text-xs border border-[var(--color-border)] rounded-lg px-3 py-1 text-[var(--color-text-secondary)]"
                        >
                          取消
                        </button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-[var(--color-text)]">{supplier.name}</span>
                          <span className={`text-xs px-1.5 py-0.5 rounded-full ${suppStatusColor[supplier.status] || 'text-gray-400 bg-gray-100'}`}>
                            {supplier.status === 'prospective' ? '待考察' : supplier.status === 'approved' ? '已准入' : supplier.status === 'blocked' ? '已拉黑' : supplier.status === 'deprecated' ? '已停用' : supplier.status}
                          </span>
                        </div>
                        <div className="text-[var(--color-text-muted)] text-xs mt-0.5 space-x-3">
                          <span>{supplier.categories?.join(', ') || '未分类'}</span>
                          {supplier.legal_person && <span>法人: {supplier.legal_person}</span>}
                          {supplier.registered_capital && <span>注册资本: {supplier.registered_capital}</span>}
                          {supplier.establish_time && <span>成立: {supplier.establish_time}</span>}
                        </div>
                        {supplier.unified_code && <div className="text-gray-400 text-[10px] mt-0.5">{supplier.unified_code}</div>}
                      </div>
                      {canManage && (
                        <button
                          onClick={() => startEdit(supplier)}
                          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-primary-bg)] px-2"
                          title="编辑"
                        >
                          ✎
                        </button>
                      )}
                    </>
                  )}
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
