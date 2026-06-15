import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api } from '../api';
import { useWatchlist } from '../hooks';

interface CompareItem {
  company_name: string;
  risk_score: number | null;
  risk_level: string | null;
  financial: Record<string, number> | null;
  esg?: { total_score: number; total_level: string };
  sentiment?: { sentiment_score: number; articles_count: number };
}

const DIM_MAP: Record<string, { label: string; keys: string[] }> = {
  risk: { label: '风险评分', keys: ['risk_score', 'risk_level'] },
  financial: { label: '财务指标', keys: ['revenue', 'net_profit', 'debt_ratio', 'roe', 'cash_flow'] },
  esg: { label: 'ESG评分', keys: ['esg'] },
  sentiment: { label: '舆情', keys: ['sentiment'] },
};

const KEY_LABEL: Record<string, string> = {
  risk_score: '风险评分', risk_level: '风险等级',
  revenue: '营收', net_profit: '净利润', debt_ratio: '负债率', roe: 'ROE', cash_flow: '现金流',
  esg: 'ESG 评分',
  sentiment: '情感得分',
};

function riskColor(score: number) {
  if (score >= 60) return '#dc2626';
  if (score >= 30) return '#d97706';
  return '#16a34a';
}

export default function CompareView() {
  const { companies } = useWatchlist();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [results, setResults] = useState<CompareItem[]>([]);
  const [dim, setDim] = useState('risk');
  const [error, setError] = useState('');

  const compareMutation = useMutation({
    mutationFn: (names: string[]) => api.post<{ companies: CompareItem[] }>('/compare', { company_names: names }),
    onSuccess: (r) => setResults(r.companies || []),
    onError: () => setError('对比失败，请重试'),
  });

  const toggle = (name: string) => {
    const next = new Set(selected);
    if (next.has(name)) next.delete(name); else next.add(name);
    setSelected(next);
  };

  const compare = () => {
    if (selected.size < 2) return;
    setError('');
    compareMutation.mutate(Array.from(selected));
  };

  return (
    <div className="flex h-full">
      {/* Left: company selection */}
      <div className="w-72 border-r border-[#e8e8e3] bg-[#fafaf8] overflow-y-auto shrink-0 flex flex-col">
        <div className="px-4 py-3 border-b border-[#e8e8e3]">
          <h2 className="text-sm font-semibold text-[#333]">供应商对比</h2>
          <p className="text-[11px] text-gray-400 mt-0.5">
            已选 {selected.size} · 至少选择 2 家
          </p>
        </div>
        <div className="flex-1 overflow-y-auto py-1">
          {companies.map(name => (
            <button key={name} onClick={() => toggle(name)}
              className={`w-full text-left px-4 py-2.5 text-sm flex items-center gap-2 transition-colors ${
                selected.has(name) ? 'bg-[#e8e8e3] font-medium' : 'hover:bg-[#eee]'
              }`}>
              <input type="checkbox" checked={selected.has(name)} readOnly className="w-3.5 h-3.5 accent-[#333]" />
              {name.slice(0, 18)}
            </button>
          ))}
        </div>
        <div className="px-4 py-3 border-t border-[#e8e8e3]">
          <button onClick={compare} disabled={selected.size < 2 || compareMutation.isPending}
            className="w-full bg-[#333] text-white rounded-lg px-4 py-2 text-sm hover:bg-[#555] disabled:opacity-50 transition-colors">
            {compareMutation.isPending ? '对比中…' : `开始对比 (${selected.size})`}
          </button>
        </div>
      </div>

      {/* Right: comparison table */}
      <div className="flex-1 overflow-auto p-6">
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-4 text-sm text-red-600">{error}</div>
        )}

        {results.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            选择至少 2 家企业开始对比
          </div>
        ) : (
          <>
            {/* Dimension tabs */}
            <div className="flex gap-2 mb-6">
              {Object.entries(DIM_MAP).map(([k, v]) => (
                <button key={k} onClick={() => setDim(k)}
                  className={`text-xs px-3 py-1 rounded-full transition-colors ${
                    dim === k ? 'bg-[#333] text-white' : 'text-gray-500 hover:bg-gray-100'
                  }`}>
                  {v.label}
                </button>
              ))}
            </div>

            {/* Table */}
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="border-b border-[#e8e8e3]">
                    <th className="text-left py-3 px-4 text-xs text-gray-400 font-medium w-20">指标</th>
                    {results.map(r => (
                      <th key={r.company_name} className="text-center py-3 px-4 text-sm font-medium text-[#333]">
                        {r.company_name.slice(0, 12)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {dim === 'risk' && (
                    <>
                      <tr className="border-b border-[#eee]">
                        <td className="py-3 px-4 text-gray-500 text-xs">风险评分</td>
                        {results.map(r => (
                          <td key={r.company_name} className="text-center py-3 px-4 font-semibold text-lg"
                            style={{ color: riskColor(r.risk_score ?? 0) }}>
                            {r.risk_score ?? '—'}
                          </td>
                        ))}
                      </tr>
                      <tr className="border-b border-[#eee]">
                        <td className="py-3 px-4 text-gray-500 text-xs">风险等级</td>
                        {results.map(r => (
                          <td key={r.company_name} className="text-center py-3 px-4 text-sm">{r.risk_level ?? '—'}</td>
                        ))}
                      </tr>
                    </>
                  )}
                  {dim === 'financial' && (
                    <>
                      {['revenue', 'net_profit', 'debt_ratio', 'roe'].map(key => (
                        <tr key={key} className="border-b border-[#eee]">
                          <td className="py-3 px-4 text-gray-500 text-xs">{KEY_LABEL[key]}</td>
                          {results.map(r => (
                            <td key={r.company_name} className="text-center py-3 px-4 text-xs">
                              {r.financial?.[key] != null ? r.financial[key] : '—'}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </>
                  )}
                  {dim === 'esg' && (
                    <tr className="border-b border-[#eee]">
                      <td className="py-3 px-4 text-gray-500 text-xs">ESG 总分</td>
                      {results.map(r => (
                        <td key={r.company_name} className="text-center py-3 px-4 text-sm">
                          {r.esg != null ? r.esg.total_score : '—'}
                        </td>
                      ))}
                    </tr>
                  )}
                  {dim === 'sentiment' && (
                    <>
                      <tr className="border-b border-[#eee]">
                        <td className="py-3 px-4 text-gray-500 text-xs">情感得分</td>
                        {results.map(r => (
                          <td key={r.company_name} className="text-center py-3 px-4 text-sm">
                            {r.sentiment != null ? (r.sentiment.sentiment_score > 0 ? '+' : '') + r.sentiment.sentiment_score.toFixed(2) : '—'}
                          </td>
                        ))}
                      </tr>
                      <tr className="border-b border-[#eee]">
                        <td className="py-3 px-4 text-gray-500 text-xs">新闻数量</td>
                        {results.map(r => (
                          <td key={r.company_name} className="text-center py-3 px-4 text-sm">
                            {r.sentiment?.articles_count ?? '—'}
                          </td>
                        ))}
                      </tr>
                    </>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
