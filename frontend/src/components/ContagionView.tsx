import { useState, useEffect } from 'react';
import { api } from '../api';

interface RelatedEntity {
  name: string;
  relation: string;
  relation_type: string;
  material?: string;
  importance?: string;
  in_watchlist: boolean;
}

interface ContagionResult {
  company_name: string;
  related_count: number;
  branch_count: number;
  dependency_count: number;
  same_industry_count: number;
  high_risk_related_count: number;
  related_entities: RelatedEntity[];
}

interface ContagionDashboard {
  total_companies: number;
  total_dependencies: number;
  total_branches: number;
  high_contagion_risk: number;
  companies: Array<{
    company_name: string;
    related_count: number;
    high_risk_related_count: number;
    branch_count: number;
    dependency_count: number;
  }>;
}

export default function ContagionView() {
  const [dash, setDash] = useState<ContagionDashboard | null>(null);
  const [selected, setSelected] = useState<ContagionResult | null>(null);
  const [companies, setCompanies] = useState<string[]>([]);
  const [showAddDep, setShowAddDep] = useState(false);
  const [depForm, setDepForm] = useState({ supplier: '', customer: '', material: '', importance: 'medium' });
  const [deps, setDeps] = useState<any[]>([]);

  const loadDash = (signal?: AbortSignal) => {
    api.get<ContagionDashboard>('/p2/contagion', undefined, signal).then(setDash).catch(() => {});
    api.get<{ companies: string[] }>('/alert/watchlist', undefined, signal)
      .then(d => setCompanies(d.companies || [])).catch(() => {});
  };

  useEffect(() => {
    const controller = new AbortController();
    loadDash(controller.signal);
    return () => controller.abort();
  }, []);

  const selectCompany = async (name: string) => {
    const r = await api.get<ContagionResult>(`/p2/contagion/${encodeURIComponent(name)}`);
    setSelected(r);
    // also load deps
    const d = await api.get<{ dependencies: any[] }>(`/p2/dependencies/${encodeURIComponent(name)}`);
    setDeps(d.dependencies || []);
  };

  const addDep = async () => {
    if (!depForm.supplier || !depForm.customer) return;
    await api.post('/p2/dependencies', depForm);
    setShowAddDep(false);
    setDepForm({ supplier: '', customer: '', material: '', importance: 'medium' });
    loadDash();
    if (selected) selectCompany(selected.company_name);
  };

  const removeDep = async (supplier: string, customer: string, material: string) => {
    await api.delete('/p2/dependencies', { supplier, customer, material });
    loadDash();
    if (selected) selectCompany(selected.company_name);
  };

  return (
    <div className="flex h-full">
      {/* left panel */}
      <div className="w-72 border-r border-[#e8e8e3] bg-[#fafaf8] overflow-y-auto shrink-0">
        <div className="px-4 py-3 border-b border-[#e8e8e3] flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-[#333]">风险传染</h2>
            <p className="text-[11px] text-gray-400 mt-0.5">
              {dash?.total_companies ?? 0} 企业 · {dash?.total_dependencies ?? 0} 依赖
            </p>
          </div>
          <button onClick={loadDash} className="text-xs text-blue-500">刷新</button>
        </div>

        {/* summary cards */}
        {dash && (
          <div className="grid grid-cols-2 gap-2 p-3 border-b border-[#eee]">
            <div className="bg-red-50 rounded-lg p-2 text-center">
              <div className="text-lg font-bold text-red-600">{dash.high_contagion_risk}</div>
              <div className="text-[10px] text-red-400">高传染风险</div>
            </div>
            <div className="bg-gray-50 rounded-lg p-2 text-center">
              <div className="text-lg font-bold text-gray-600">{dash.total_dependencies}</div>
              <div className="text-[10px] text-gray-400">依赖关系</div>
            </div>
          </div>
        )}

        {/* add dependency button */}
        <div className="px-3 py-2">
          <button
            onClick={() => setShowAddDep(true)}
            className="w-full text-xs py-1.5 rounded-lg border border-dashed border-[#ccc] text-gray-500 hover:bg-[#eee] transition-colors"
          >
            + 添加依赖关系
          </button>
        </div>

        {showAddDep && (
          <div className="px-3 pb-3 space-y-2 border-b border-[#eee]">
            <select
              value={depForm.supplier}
              onChange={e => setDepForm({ ...depForm, supplier: e.target.value })}
              className="w-full text-xs border border-[#e8e8e3] rounded-lg p-2"
            >
              <option value="">选择供应商</option>
              {companies.map(c => <option key={c} value={c}>{c.slice(0, 15)}</option>)}
            </select>
            <select
              value={depForm.customer}
              onChange={e => setDepForm({ ...depForm, customer: e.target.value })}
              className="w-full text-xs border border-[#e8e8e3] rounded-lg p-2"
            >
              <option value="">选择客户</option>
              {companies.filter(c => c !== depForm.supplier).map(c => <option key={c} value={c}>{c.slice(0, 15)}</option>)}
            </select>
            <input
              placeholder="物料/产品（可选）"
              value={depForm.material}
              onChange={e => setDepForm({ ...depForm, material: e.target.value })}
              className="w-full text-xs border border-[#e8e8e3] rounded-lg p-2"
            />
            <select
              value={depForm.importance}
              onChange={e => setDepForm({ ...depForm, importance: e.target.value })}
              className="w-full text-xs border border-[#e8e8e3] rounded-lg p-2"
            >
              <option value="low">低重要性</option>
              <option value="medium">中重要性</option>
              <option value="high">高重要性</option>
              <option value="critical">关键物料</option>
            </select>
            <div className="flex gap-2">
              <button onClick={addDep} className="flex-1 text-xs bg-[#333] text-white rounded-lg py-1.5">添加</button>
              <button onClick={() => setShowAddDep(false)} className="text-xs text-gray-400">取消</button>
            </div>
          </div>
        )}

        {/* company list */}
        <div className="py-1">
          {companies.map(name => (
            <button
              key={name}
              onClick={() => selectCompany(name)}
              className={`w-full text-left px-4 py-2.5 text-sm transition-colors ${
                selected?.company_name === name ? 'bg-[#e8e8e3] text-[#333] font-medium' : 'text-[#555] hover:bg-[#eee]'
              }`}
            >
              {name}
            </button>
          ))}
        </div>
      </div>

      {/* right detail */}
      <div className="flex-1 overflow-y-auto p-6">
        {!selected ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            选择企业查看风险传染分析
          </div>
        ) : (
          <div className="max-w-2xl">
            <h2 className="text-lg font-semibold text-[#333] mb-1">{selected.company_name}</h2>
            <p className="text-xs text-gray-400 mb-6">
              关联方 {selected.related_count} 个 · 高风险 {selected.high_risk_related_count} 个
            </p>

            {/* summary */}
            <div className="grid grid-cols-4 gap-3 mb-6">
              <div className="bg-white border border-[#e8e8e3] rounded-xl p-3 text-center">
                <div className="text-xl font-bold text-[#333]">{selected.branch_count}</div>
                <div className="text-[11px] text-gray-400">分支机构</div>
              </div>
              <div className="bg-white border border-[#e8e8e3] rounded-xl p-3 text-center">
                <div className="text-xl font-bold text-[#333]">{selected.dependency_count}</div>
                <div className="text-[11px] text-gray-400">供应链依赖</div>
              </div>
              <div className="bg-white border border-[#e8e8e3] rounded-xl p-3 text-center">
                <div className="text-xl font-bold text-[#333]">{selected.same_industry_count}</div>
                <div className="text-[11px] text-gray-400">同行业企业</div>
              </div>
              <div className="bg-red-50 border border-red-100 rounded-xl p-3 text-center">
                <div className="text-xl font-bold text-red-600">{selected.high_risk_related_count}</div>
                <div className="text-[11px] text-red-400">高风险关联</div>
              </div>
            </div>

            {/* no related entities */}
            {selected.related_entities.length === 0 ? (
              <div className="text-sm text-gray-400 text-center py-8">
                暂未发现关联方。可通过上方按钮添加供应链依赖关系。
              </div>
            ) : (
              <div className="space-y-2">
                <h3 className="text-sm font-medium text-[#555] mb-3">关联实体</h3>
                {selected.related_entities.map((e, i) => (
                  <div key={i} className="bg-white border border-[#e8e8e3] rounded-xl p-4 flex items-center gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-[#333] truncate">{e.name}</div>
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-[11px] px-2 py-0.5 rounded bg-gray-100 text-gray-500">
                          {e.relation_type}
                        </span>
                        {e.material && (
                          <span className="text-[11px] text-gray-400">{e.material}</span>
                        )}
                        {e.importance && e.importance !== 'medium' && (
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                            e.importance === 'critical' ? 'bg-red-50 text-red-500' :
                            e.importance === 'high' ? 'bg-orange-50 text-orange-500' :
                            'bg-gray-50 text-gray-500'
                          }`}>
                            {e.importance === 'critical' ? '关键' : e.importance === 'high' ? '重要' : '一般'}
                          </span>
                        )}
                      </div>
                    </div>
                    <span className={`text-[11px] px-2 py-0.5 rounded-full ${
                      e.in_watchlist ? 'bg-green-50 text-green-600' : 'bg-gray-50 text-gray-400'
                    }`}>
                      {e.in_watchlist ? '监控中' : '未监控'}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {/* dependencies section */}
            <div className="mt-6">
              <h3 className="text-sm font-medium text-[#555] mb-3">供应链依赖 ({deps.length})</h3>
              {deps.length === 0 ? (
                <p className="text-xs text-gray-400">暂无依赖关系</p>
              ) : (
                <div className="space-y-1.5">
                  {deps.map((d, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs bg-white border border-[#e8e8e3] rounded-lg px-3 py-2">
                      <span className="text-gray-700">{d.supplier.slice(0, 10)}</span>
                      <span className="text-gray-400">→</span>
                      <span className="text-gray-700">{d.customer.slice(0, 10)}</span>
                      {d.material && <span className="text-gray-400">({d.material})</span>}
                      <div className="flex-1" />
                      <button
                        onClick={() => removeDep(d.supplier, d.customer, d.material)}
                        className="text-red-400 hover:text-red-600"
                      >
                        删除
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
