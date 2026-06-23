import { useState, useEffect, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, getStoredUser } from '../api';
import { queryKeys } from '../query-keys';
import type { SupplierEntry, AccessApplicationItem } from '../types';

// GB/T 4754-2017 全部 20 门类 97 大类
const INDUSTRIES: [string, string][] = [
  // A 农、林、牧、渔业
  ["01","农业"],["02","林业"],["03","畜牧业"],["04","渔业"],["05","农林牧渔专业及辅助性活动"],
  // B 采矿业
  ["06","煤炭开采和洗选业"],["07","石油和天然气开采业"],["08","黑色金属矿采选业"],["09","有色金属矿采选业"],["10","非金属矿采选业"],["11","开采专业及辅助性活动"],["12","其他采矿业"],
  // C 制造业
  ["13","农副食品加工业"],["14","食品制造业"],["15","酒饮料和精制茶制造业"],["16","烟草制品业"],["17","纺织业"],["18","纺织服装服饰业"],["19","皮革毛皮羽毛及其制品和制鞋业"],["20","木材加工和木竹藤棕草制品业"],["21","家具制造业"],["22","造纸和纸制品业"],["23","印刷和记录媒介复制业"],["24","文教工美体育和娱乐用品制造业"],["25","石油煤炭及其他燃料加工业"],["26","化学原料和化学制品制造业"],["27","医药制造业"],["28","化学纤维制造业"],["29","橡胶和塑料制品业"],["30","非金属矿物制品业"],["31","黑色金属冶炼和压延加工业"],["32","有色金属冶炼和压延加工业"],["33","金属制品业"],["34","通用设备制造业"],["35","专用设备制造业"],["36","汽车制造业"],["37","铁路船舶航空航天和其他运输设备制造业"],["38","电气机械和器材制造业"],["39","计算机通信和其他电子设备制造业"],["40","仪器仪表制造业"],["41","其他制造业"],["42","废弃资源综合利用业"],["43","金属制品机械和设备修理业"],
  // D 电力、热力、燃气及水生产和供应业
  ["44","电力热力生产和供应业"],["45","燃气生产和供应业"],["46","水的生产和供应业"],
  // E 建筑业
  ["47","房屋建筑业"],["48","土木工程建筑业"],["49","建筑安装业"],["50","建筑装饰装修和其他建筑业"],
  // F 批发和零售业
  ["51","批发业"],["52","零售业"],
  // G 交通运输、仓储和邮政业
  ["53","铁路运输业"],["54","道路运输业"],["55","水上运输业"],["56","航空运输业"],["57","管道运输业"],["58","多式联运和运输代理业"],["59","装卸搬运和仓储业"],["60","邮政业"],
  // H 住宿和餐饮业
  ["61","住宿业"],["62","餐饮业"],
  // I 信息传输、软件和信息技术服务业
  ["63","电信广播电视和卫星传输服务"],["64","互联网和相关服务"],["65","软件和信息技术服务业"],
  // J 金融业
  ["66","货币金融服务"],["67","资本市场服务"],["68","保险业"],["69","其他金融业"],
  // K 房地产业
  ["70","房地产业"],
  // L 租赁和商务服务业
  ["71","租赁业"],["72","商务服务业"],
  // M 科学研究和技术服务业
  ["73","研究和试验发展"],["74","专业技术服务业"],["75","科技推广和应用服务业"],
  // N 水利、环境和公共设施管理业
  ["76","水利管理业"],["77","生态保护和环境治理业"],["78","公共设施管理业"],["79","土地管理业"],
  // O 居民服务、修理和其他服务业
  ["80","居民服务业"],["81","机动车电子产品和日用产品修理业"],["82","其他服务业"],
  // P 教育
  ["83","教育"],
  // Q 卫生和社会工作
  ["84","卫生"],["85","社会工作"],
  // R 文化、体育和娱乐业
  ["86","新闻和出版业"],["87","广播电视电影和录音制作业"],["88","文化艺术业"],["89","体育"],["90","娱乐业"],
  // S 公共管理、社会保障和社会组织
  ["91","中国共产党机关"],["92","国家机构"],["93","人民政协民主党派"],["94","社会保障"],["95","群众团体社会团体和其他成员组织"],["96","基层群众自治组织及其他组织"],
  // T 国际组织
  ["97","国际组织"],
];

function splitList(s: string): string[] {
  return s.replace(/，/g, ',').split(',').map(x => x.trim()).filter(Boolean);
}

function formatEstablishTime(t: string): string {
  const ms = parseInt(t);
  if (!isNaN(ms) && ms > 0) return new Date(ms).toISOString().slice(0, 10);
  return t.slice(0, 10);
}

export default function SupplierLibraryPage() {
  const qc = useQueryClient();
  const user = getStoredUser();
  const canManage = user?.role === 'admin' || user?.role === 'analyst';
  const [keyword, setKeyword] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [form, setForm] = useState({ name: '', categories: '', regions: '' });

  // edit state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState({ name: '', categories: '', regions: '', status: '' });

  // approval state
  const [approvalStatus, setApprovalStatus] = useState<string>('pending');
  const [showApproval, setShowApproval] = useState(false);

  // tianyancha search state
  const [tycIndustry, setTycIndustry] = useState('');
  const [tycIndustryOpen, setTycIndustryOpen] = useState(false);
  const [tycIndustryFilter, setTycIndustryFilter] = useState('');
  const [tycRegion, setTycRegion] = useState('');
  const [tycResult, setTycResult] = useState<{ imported: number; skipped: number; errors: string[] } | null>(null);

  const filteredIndustries = INDUSTRIES.filter(([code, name]) =>
    !tycIndustryFilter || name.includes(tycIndustryFilter) || code.startsWith(tycIndustryFilter)
  );

  const tycIndustryName = useMemo(
    () => INDUSTRIES.find(([c]) => c === tycIndustry)?.[1],
    [tycIndustry],
  );

  const tycMutation = useMutation({
    mutationFn: () => api.post<{ imported: number; skipped: number; errors: string[] }>(
      `/sourcing/suppliers/import-tianyancha?industry=${encodeURIComponent(tycIndustry)}&region=${encodeURIComponent(tycRegion)}&max_results=50`,
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
    queryKey: [...queryKeys.suppliers, keyword, showAll] as const,
    queryFn: () =>
      api.get<{ items: SupplierEntry[]; total: number }>(
        `/sourcing/suppliers?keyword=${encodeURIComponent(keyword)}&hide_bare=${!showAll}`,
      ),
  });

  const addMutation = useMutation({
    mutationFn: () =>
      api.post('/sourcing/suppliers', {
        name: form.name,
        categories: splitList(form.categories),
        regions: splitList(form.regions),
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
    try {
      const data = await api.upload<{ imported: number; skipped: number; errors: string[] }>(
        '/sourcing/suppliers/import', file,
      );
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
        categories: editForm.categories ? splitList(editForm.categories) : undefined,
        regions: editForm.regions ? splitList(editForm.regions) : undefined,
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
                    {(appQuery.data?.items ?? []).map(app => (
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
          <div className="flex gap-2 items-center">
            {!showAll ? (
              <button
                onClick={() => setShowAll(true)}
                className="text-xs text-[var(--color-text-muted)] border border-[var(--color-border)] rounded-lg px-2 py-1 hover:bg-[var(--color-surface-hover)]"
              >
                显示全部
              </button>
            ) : (
              <button
                onClick={() => setShowAll(false)}
                className="text-xs text-blue-500 bg-blue-50 border border-blue-200 rounded-lg px-2 py-1 hover:bg-blue-100"
              >
                ✓ 显示全部
              </button>
            )}
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
            <div className="grid grid-cols-2 gap-2">
              <div className="relative">
                <input
                  value={tycIndustry ? (tycIndustryName || tycIndustry) : tycIndustryFilter}
                  onChange={e => { setTycIndustryFilter(e.target.value); setTycIndustry(''); setTycIndustryOpen(true); }}
                  onFocus={() => setTycIndustryOpen(true)}
                  onBlur={() => setTycIndustryOpen(false)}
                  placeholder="搜索行业..."
                  className="w-full text-xs rounded-lg border border-[var(--color-border)] px-3 py-2 bg-[var(--color-input-bg)] focus:outline-none focus:border-[var(--color-focus-ring)]"
                />
                {tycIndustryOpen && (
                  <div className="absolute z-10 mt-1 w-full max-h-48 overflow-y-auto bg-white border border-[var(--color-border)] rounded-lg shadow-lg">
                    {filteredIndustries.slice(0, 50).map(([code, name]) => (
                      <div
                        key={code}
                        onMouseDown={() => { setTycIndustry(code); setTycIndustryFilter(''); setTycIndustryOpen(false); }}
                        className={`px-3 py-1.5 text-xs cursor-pointer hover:bg-[var(--color-surface-hover)] ${tycIndustry === code ? 'bg-blue-50' : ''}`}
                      >
                        <span className="text-gray-400 mr-2">{code}</span>{name}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <input
                value={tycRegion}
                onChange={e => setTycRegion(e.target.value)}
                placeholder="地域代码（可选）：330100"
                className="text-xs rounded-lg border border-[var(--color-border)] px-3 py-2 bg-[var(--color-input-bg)] focus:outline-none focus:border-[var(--color-focus-ring)]"
              />
            </div>
            <p className="text-[10px] text-gray-400">支持搜索（输入关键词过滤）+ 下拉选择。制造业31个大类，每次最多导入50家。</p>
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
                          {supplier.establish_time && <span>成立: {formatEstablishTime(supplier.establish_time)}</span>}
                        </div>
                        {supplier.unified_code && <div className="text-gray-400 text-[10px] mt-0.5">{supplier.unified_code}</div>}
                      </div>
                      {canManage && (
                        <button
                          onClick={() => startEdit(supplier)}
                          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-primary-bg)] px-2"
                          aria-label={`编辑 ${supplier.name}`}
                        >
                          ✎
                        </button>
                      )}
                    </>
                  )}
                </div>
              ))}
              {(data?.items ?? []).length === 0 && (
                <p className="text-sm text-gray-400 text-center py-8">暂无供应商数据</p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
