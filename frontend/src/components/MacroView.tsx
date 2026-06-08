import { useState, useEffect } from 'react';
import { api } from '../api';

interface PolicyTag {
  tag: string;
  level: string;
  desc: string;
}

interface MacroResult {
  company_name: string;
  assessed_at: string;
  total_score: number;
  total_level: string;
  policy_risks: { score: number; count: number; tags: PolicyTag[] };
  regional_risk: { province: string; score: number; level: string; label?: string };
  industry_risk: { industry: string; pmi_value?: number; pmi_label?: string; risk_score: number; risk_level: string; pmi_date?: string };
}

interface Alternative {
  company_name: string;
  industry: string;
  risk_score: number | null;
  risk_level: string;
  source: string;
}

interface AltResult {
  company_name: string;
  source_industry: string;
  source_risk_score: number | null;
  alternatives_count: number;
  alternatives: Alternative[];
}

const LEVEL_COLOR: Record<string, string> = {
  '高风险': '#dc2626', '中风险': '#d97706', '低风险': '#16a34a',
};
const LEVEL_BG: Record<string, string> = {
  '高风险': '#fef2f2', '中风险': '#fffbf0', '低风险': '#ecfdf5',
};

export default function MacroView() {
  const [companies, setCompanies] = useState<string[]>([]);
  const [selected, setSelected] = useState('');
  const [macro, setMacro] = useState<MacroResult | null>(null);
  const [alt, setAlt] = useState<AltResult | null>(null);
  const [pmi, setPmi] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [altDash, setAltDash] = useState<any>(null);

  useEffect(() => {
    api.get<{ companies: string[] }>('/alert/watchlist').then(d => setCompanies(d.companies || []));
    api.get<any>('/analysis/macro/pmi').then(setPmi);
    api.get<any>('/analysis/alternatives').then(setAltDash);
  }, []);

  const select = async (name: string) => {
    setSelected(name);
    setLoading(true);
    const [m, a] = await Promise.all([
      api.get<MacroResult>(`/analysis/macro/${encodeURIComponent(name)}`),
      api.get<AltResult>(`/analysis/alternatives/${encodeURIComponent(name)}`),
    ]);
    setMacro(m); setAlt(a); setLoading(false);
  };

  return (
    <div className="flex h-full">
      {/* left */}
      <div className="w-72 border-r border-[#e8e8e3] bg-[#fafaf8] overflow-y-auto shrink-0">
        <div className="px-4 py-3 border-b border-[#e8e8e3]">
          <h2 className="text-sm font-semibold text-[#333]">宏观 & 替代</h2>
          <p className="text-[11px] text-gray-400 mt-0.5">{companies.length} 家企业</p>
        </div>

        {/* PMI indicator */}
        {pmi && !pmi.error && (
          <div className="px-3 py-2 border-b border-[#eee] text-xs">
            <span className="text-gray-500">
              PMI(制造业): <span className={`font-semibold ${pmi.manufacturing_pmi >= 50 ? 'text-green-600' : 'text-red-500'}`}>{pmi.manufacturing_pmi}</span>
              {' '}非制造业: <span className={`font-semibold ${pmi.non_manufacturing_pmi >= 50 ? 'text-green-600' : 'text-red-500'}`}>{pmi.non_manufacturing_pmi}</span>
            </span>
          </div>
        )}

        {/* high risk needing alternatives */}
        {altDash && altDash.high_risk_count > 0 && (
          <div className="px-3 py-2 border-b border-[#eee] bg-red-50">
            <p className="text-xs text-red-600 font-medium">{altDash.high_risk_count} 家高风险需替代</p>
          </div>
        )}

        <div className="py-1">
          {companies.map(name => (
            <button key={name} onClick={() => select(name)}
              className={`w-full text-left px-4 py-2.5 text-sm transition-colors ${
                selected === name ? 'bg-[#e8e8e3] font-medium' : 'hover:bg-[#eee]'
              }`}>{name}</button>
          ))}
        </div>
      </div>

      {/* right */}
      <div className="flex-1 overflow-y-auto p-6">
        {!selected ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">选择企业查看宏观风险与替代建议</div>
        ) : loading ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">加载中…</div>
        ) : (
          <div className="max-w-2xl space-y-6">
            <h2 className="text-lg font-semibold text-[#333]">{selected}</h2>

            {/* ---- macro risk ---- */}
            {macro && (
              <div>
                <h3 className="text-sm font-medium text-[#555] mb-3">🌐 宏观风险叠加</h3>

                {/* total */}
                <div className="bg-white border border-[#e8e8e3] rounded-2xl p-4 mb-4 flex items-center gap-4">
                  <div className="text-3xl font-bold" style={{ color: LEVEL_COLOR[macro.total_level] }}>{macro.total_score}</div>
                  <div>
                    <div className="text-sm font-medium" style={{ color: LEVEL_COLOR[macro.total_level] }}>{macro.total_level}</div>
                    <div className="text-xs text-gray-400">宏观风险总分</div>
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-3">
                  {/* industry */}
                  <div className="bg-white border border-[#e8e8e3] rounded-xl p-4">
                    <div className="text-xs text-gray-400 mb-1">行业景气</div>
                    <div className="text-lg font-bold" style={{ color: LEVEL_COLOR[macro.industry_risk.risk_level] }}>
                      {macro.industry_risk.risk_score}
                    </div>
                    <div className="text-xs text-gray-500 mt-1">{macro.industry_risk.industry || '未知行业'}</div>
                    {macro.industry_risk.pmi_value && (
                      <div className="text-[11px] text-gray-400">{macro.industry_risk.pmi_label}: {macro.industry_risk.pmi_value}</div>
                    )}
                  </div>

                  {/* regional */}
                  <div className="bg-white border border-[#e8e8e3] rounded-xl p-4">
                    <div className="text-xs text-gray-400 mb-1">地区风险</div>
                    <div className="text-lg font-bold" style={{ color: LEVEL_COLOR[macro.regional_risk.level] }}>
                      {macro.regional_risk.score}
                    </div>
                    <div className="text-xs text-gray-500 mt-1">{macro.regional_risk.province}</div>
                    {macro.regional_risk.label && <div className="text-[11px] text-gray-400">{macro.regional_risk.label}</div>}
                  </div>

                  {/* policy */}
                  <div className="bg-white border border-[#e8e8e3] rounded-xl p-4">
                    <div className="text-xs text-gray-400 mb-1">政策风险</div>
                    <div className="text-lg font-bold" style={{ color: macro.policy_risks.score > 20 ? '#dc2626' : '#d97706' }}>
                      {macro.policy_risks.score}
                    </div>
                    <div className="text-xs text-gray-500 mt-1">{macro.policy_risks.count} 个标签</div>
                  </div>
                </div>

                {/* policy tags */}
                {macro.policy_risks.tags.length > 0 && (
                  <div className="mt-3 space-y-1.5">
                    {macro.policy_risks.tags.map((t, i) => (
                      <div key={i} className="flex items-center gap-2 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
                        <span className={`text-xs px-2 py-0.5 rounded font-medium ${
                          t.level === 'high' ? 'bg-red-100 text-red-600' : 'bg-amber-100 text-amber-700'
                        }`}>{t.tag}</span>
                        <span className="text-xs text-gray-600 flex-1">{t.desc}</span>
                      </div>
                    ))}
                  </div>
                )}
                {macro.policy_risks.tags.length === 0 && (
                  <p className="text-xs text-gray-400 mt-2">未识别到特定政策风险标签</p>
                )}
              </div>
            )}

            {/* ---- alternatives ---- */}
            {alt && (
              <div>
                <h3 className="text-sm font-medium text-[#555] mb-3">🔀 替代供应商建议</h3>
                {alt.alternatives.length === 0 ? (
                  <p className="text-xs text-gray-400">
                    {alt.source_risk_score && alt.source_risk_score < 60
                      ? '当前企业风险较低，暂不需要替代'
                      : '暂未找到同行业低风险替代企业，可考虑扩大监控范围'}
                  </p>
                ) : (
                  <div className="space-y-2">
                    {alt.alternatives.map((a, i) => (
                      <div key={i} className="bg-white border border-[#e8e8e3] rounded-xl p-4 flex items-center gap-4 hover:border-green-200 transition-colors">
                        <div className="flex items-center justify-center w-8 h-8 rounded-full bg-green-50 text-green-600 font-bold text-sm">
                          {i + 1}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="text-sm text-[#333] font-medium truncate">{a.company_name}</div>
                          <div className="text-xs text-gray-400 mt-0.5">{a.industry}</div>
                        </div>
                        {a.risk_score !== null ? (
                          <span className="text-xs font-semibold px-2 py-1 rounded-full" style={{
                            background: LEVEL_BG[a.risk_level] || '#f5f5f5',
                            color: LEVEL_COLOR[a.risk_level] || '#999',
                          }}>
                            {a.risk_level} {a.risk_score}/100
                          </span>
                        ) : (
                          <span className="text-xs text-gray-400">未评估</span>
                        )}
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-400">
                          {a.source === 'watchlist' ? '监控清单' : '外部推荐'}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
