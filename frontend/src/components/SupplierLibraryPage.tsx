import { useState, useEffect, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, getStoredUser } from '../api';
import { queryKeys } from '../query-keys';
import type { SupplierEntry, AccessApplicationItem } from '../types';

// GB/T 4754-2017 制造业大类
const INDUSTRIES: [string, string][] = [
  ["131","谷物磨制"],["132","饲料加工"],["133","植物油加工"],["134","制糖业"],["135","屠宰及肉类加工"],["136","水产品加工"],["137","蔬菜菌类水果坚果加工"],["139","其他农副食品加工"],["141","焙烤食品"],["142","糖果巧克力及蜜饯"],["143","方便食品"],["144","乳制品"],["145","罐头食品"],["146","调味品及发酵制品"],["149","其他食品制造"],["151","酒的制造"],["152","饮料制造"],["153","精制茶加工"],["161","烟叶复烤"],["162","卷烟制造"],["169","其他烟草制品"],["171","棉纺织及印染"],["172","毛纺织及染整"],["173","麻纺织及染整"],["174","丝绢纺织及印染"],["175","化纤织造及印染"],["176","针织或钩针编织物"],["177","家用纺织制成品"],["178","产业用纺织制成品"],["181","机织服装"],["182","针织或钩针编织服装"],["183","服饰制造"],["191","皮革鞣制加工"],["192","皮革制品"],["193","毛皮鞣制及制品"],["194","羽毛加工及制品"],["195","制鞋业"],["201","木材加工"],["202","人造板制造"],["203","木质制品"],["204","竹藤棕草制品"],["211","家具制造"],["212","竹藤家具"],["213","金属家具"],["214","塑料家具"],["219","其他家具"],["221","纸浆制造"],["222","造纸"],["223","纸制品制造"],["231","印刷"],["232","装订及印刷相关服务"],["241","文教办公用品"],["242","乐器制造"],["243","工艺美术及礼仪用品"],["244","体育用品"],["245","玩具制造"],["246","游艺器材及娱乐用品"],["251","精炼石油产品"],["252","煤炭加工"],["253","核燃料加工"],["261","基础化学原料"],["262","肥料制造"],["263","农药制造"],["264","涂料油墨颜料及类似产品"],["265","合成材料"],["266","专用化学产品"],["267","炸药火工及焰火产品"],["268","日用化学产品"],["271","化学药品原料药"],["272","化学药品制剂"],["273","中药饮片加工"],["274","中成药生产"],["275","兽用药品"],["276","生物药品制品"],["277","卫生材料及医药用品"],["278","药用辅料及包装材料"],["281","纤维素纤维原料及纤维"],["282","合成纤维"],["291","橡胶制品"],["292","塑料制品"],["301","水泥石灰和石膏"],["302","石膏水泥制品及类似制品"],["303","砖瓦石材等建筑材料"],["304","玻璃制造"],["305","玻璃制品"],["306","玻璃纤维和玻璃纤维增强塑料"],["307","陶瓷制品"],["308","耐火材料制品"],["309","石墨及其他非金属矿物制品"],["311","炼铁"],["312","炼钢"],["313","钢压延加工"],["314","铁合金冶炼"],["321","常用有色金属冶炼"],["322","贵金属冶炼"],["323","稀有稀土金属冶炼"],["324","有色金属合金"],["325","有色金属压延加工"],["331","结构性金属制品"],["332","金属工具制造"],["333","集装箱及金属包装容器"],["334","金属丝绳及其制品"],["335","建筑安全用金属制品"],["336","金属表面处理及热处理"],["337","搪瓷制品"],["338","金属制日用品"],["339","铸造及其他金属制品"],["341","锅炉及原动设备"],["342","金属加工机械"],["343","物料搬运设备"],["344","泵阀门压缩机及类似机械"],["345","轴承齿轮和传动部件"],["346","烘炉风机包装等设备"],["347","文化办公用机械"],["348","通用零部件"],["349","其他通用设备"],["351","采矿冶金建筑专用设备"],["352","化工木材非金属加工专用设备"],["353","食品饮料烟草及饲料生产专用设备"],["354","印刷制药日化及日用品生产专用设备"],["355","纺织服装和皮革加工专用设备"],["356","电子和电工机械专用设备"],["357","农林牧渔专用机械"],["358","医疗仪器设备及器械"],["359","环保邮政社会公共服务专用设备"],["361","汽车整车"],["362","汽车用发动机制造"],["363","改装汽车"],["364","低速汽车"],["365","电车制造"],["366","汽车车身挂车"],["367","汽车零部件及配件"],["371","铁路运输设备"],["372","城市轨道交通设备"],["373","船舶及相关装置"],["374","航空装备"],["375","航天器及运载火箭"],["376","海洋工程装备"],["377","摩托车"],["378","自行车和残疾人座车"],["379","非公路休闲车及零配件"],["381","电机制造"],["382","输配电及控制设备"],["383","电线电缆光缆及电工器材"],["384","电池制造"],["385","家用电力器具"],["386","非电力家用器具"],["387","照明器具"],["389","其他电气机械及器材"],["391","计算机"],["392","通信设备"],["393","广播电视设备"],["394","雷达及配套设备"],["395","非专业视听设备"],["396","智能消费设备"],["397","电子器件"],["398","电子元件"],["399","其他电子设备"],["401","通用仪器仪表"],["402","专用仪器仪表"],["403","钟表与计时仪器"],["404","光学仪器"],["405","衡器"],["409","其他仪器仪表"],["411","日用杂品"],["412","煤制品"],["413","核辐射加工"],["419","其他未列明制造业"],["421","金属废料和碎屑加工处理"],["422","非金属废料和碎屑加工处理"],
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
