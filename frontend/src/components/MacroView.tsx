import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { useWatchlist } from '../hooks';
import { queryKeys } from '../query-keys';
import type { PMIOverview, AlternativeDashboard } from '../types';

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
  const { companies } = useWatchlist();
  const [selected, setSelected] = useState<string>(() => {
    try { return localStorage.getItem('macro_company') || ''; } catch { return ''; }
  });

  const pmiQuery = useQuery({
    queryKey: queryKeys.pmi,
    queryFn: () => api.get<PMIOverview>('/analysis/macro/pmi'),
  });

  const altDashQuery = useQuery({
    queryKey: queryKeys.alternativeDashboard,
    queryFn: () => api.get<AlternativeDashboard>('/analysis/alternatives'),
  });

  const macroQuery = useQuery({
    queryKey: queryKeys.macroDetail(selected),
    queryFn: () => api.get<MacroResult>(`/analysis/macro/${encodeURIComponent(selected)}`),
    enabled: !!selected,
  });

  const altQuery = useQuery({
    queryKey: queryKeys.alternativeDetail(selected),
    queryFn: () => api.get<AltResult>(`/analysis/alternatives/${encodeURIComponent(selected)}`),
    enabled: !!selected,
  });

  const pmi = pmiQuery.data ?? null;
  const altDash = altDashQuery.data ?? null;
  const macro = macroQuery.data ?? null;
  const alt = altQuery.data ?? null;
  const loading = (macroQuery.isLoading || altQuery.isLoading) && !!selected;

  const select = (name: string) => {
    setSelected(name);
    localStorage.setItem('macro_company', name);
  };

  return (
    <div className="flex h-full">
      {/* left */}
      <div className="w-72 border-r border-[var(--color-border)] bg-[var(--color-page-bg)] glass-surface overflow-y-auto shrink-0">
        <div className="px-4 py-3 border-b border-[var(--color-border)]">
          <h2 className="text-sm font-semibold text-[var(--color-text)]">宏观 & 替代</h2>
          <p className="text-[11px] text-gray-400 mt-0.5">{companies.length} 家企业</p>
        </div>

        {/* PMI indicator */}
        {pmi && !pmi.error && (
          <div className="px-3 py-2 border-b border-[var(--color-divider)] text-xs">
            <span className="text-gray-500">
              PMI(制造业): <span className={`font-semibold ${pmi.manufacturing_pmi >= 50 ? 'text-green-600' : 'text-red-500'}`}>{pmi.manufacturing_pmi}</span>
              {' '}非制造业: <span className={`font-semibold ${pmi.non_manufacturing_pmi >= 50 ? 'text-green-600' : 'text-red-500'}`}>{pmi.non_manufacturing_pmi}</span>
            </span>
          </div>
        )}

        {/* high risk needing alternatives */}
        {altDash && altDash.high_risk_count > 0 && (
          <div className="px-3 py-2 border-b border-[var(--color-divider)] bg-red-50">
            <p className="text-xs text-red-600 font-medium">{altDash.high_risk_count} 家高风险需替代</p>
          </div>
        )}

        <div className="py-1">
          {companies.map(name => (
            <button key={name} onClick={() => select(name)} disabled={loading}
              className={`w-full text-left px-4 py-2.5 text-sm transition-colors disabled:opacity-50 ${
                selected === name ? 'bg-[var(--color-surface-selected)] font-medium' : 'hover:bg-[var(--color-surface-hover)]'
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
            <h2 className="text-lg font-semibold text-[var(--color-text)]">{selected}</h2>

            {/* ---- macro risk ---- */}
            {macro && (
              <div>
                <h3 className="text-sm font-medium text-[var(--color-text-secondary)] mb-3">🌐 宏观风险叠加</h3>

                {/* total */}
                <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-4 mb-4 flex items-center gap-4">
                  <div className="text-3xl font-bold" style={{ color: LEVEL_COLOR[macro.total_level] }}>{macro.total_score}</div>
                  <div>
                    <div className="text-sm font-medium" style={{ color: LEVEL_COLOR[macro.total_level] }}>{macro.total_level}</div>
                    <div className="text-xs text-gray-400">宏观风险总分</div>
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-3">
                  {/* industry */}
                  <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4">
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
                  <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4">
                    <div className="text-xs text-gray-400 mb-1">地区风险</div>
                    <div className="text-lg font-bold" style={{ color: LEVEL_COLOR[macro.regional_risk.level] }}>
                      {macro.regional_risk.score}
                    </div>
                    <div className="text-xs text-gray-500 mt-1">{macro.regional_risk.province}</div>
                    {macro.regional_risk.label && <div className="text-[11px] text-gray-400">{macro.regional_risk.label}</div>}
                  </div>

                  {/* policy */}
                  <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4">
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
                <h3 className="text-sm font-medium text-[var(--color-text-secondary)] mb-3">🔀 替代供应商建议</h3>
                {alt.alternatives.length === 0 ? (
                  <p className="text-xs text-gray-400">
                    {alt.source_risk_score && alt.source_risk_score < 60
                      ? '当前企业风险较低，暂不需要替代'
                      : '暂未找到同行业低风险替代企业，可考虑扩大监控范围'}
                  </p>
                ) : (
                  <div className="space-y-2">
                    {alt.alternatives.map((a, i) => (
                      <div key={i} className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-4 flex items-center gap-4 hover:border-green-200 transition-colors">
                        <div className="flex items-center justify-center w-8 h-8 rounded-full bg-green-50 text-green-600 font-bold text-sm">
                          {i + 1}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="text-sm text-[var(--color-text)] font-medium truncate">{a.company_name}</div>
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
