import { useState, useEffect } from 'react';
import { api } from '../api';

interface ESGDetail {
  item: string;
  value: string;
  score: number;
  level: string;
}

interface ESGDimension {
  score: number;
  level: string;
  detail: ESGDetail[];
}

interface ESGResult {
  company_name: string;
  assessed_at: string;
  total_score: number;
  total_level: string;
  environmental: ESGDimension;
  social: ESGDimension;
  governance: ESGDimension;
}

const LEVEL_COLOR: Record<string, string> = {
  '高风险': '#dc2626',
  '中风险': '#d97706',
  '低风险': '#16a34a',
};

const LEVEL_BG: Record<string, string> = {
  '高风险': '#fef2f2',
  '中风险': '#fffbf0',
  '低风险': '#ecfdf5',
};

const DIM_LABELS: Record<string, string> = {
  environmental: '🌍 环境 (E)',
  social: '👥 社会 (S)',
  governance: '🏛️ 治理 (G)',
};

export default function ESGView() {
  const [companies, setCompanies] = useState<ESGResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<ESGResult | null>(null);

  const loadAll = () => {
    setLoading(true);
    api.get<{ companies: ESGResult[] }>('/p2/esg').then(d => {
      setCompanies(d.companies || []);
      setLoading(false);
    });
  };

  useEffect(() => { loadAll(); }, []);

  const selectCompany = async (name: string) => {
    const r = await api.get<ESGResult>(`/p2/esg/${encodeURIComponent(name)}`);
    setSelected(r);
  };

  if (loading && companies.length === 0) {
    return <div className="p-6 text-gray-400 text-sm">加载 ESG 数据中…</div>;
  }

  return (
    <div className="flex h-full">
      {/* left list */}
      <div className="w-72 border-r border-[#e8e8e3] bg-[#fafaf8] overflow-y-auto shrink-0">
        <div className="px-4 py-3 border-b border-[#e8e8e3] flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-[#333]">ESG 评分</h2>
            <p className="text-[11px] text-gray-400 mt-0.5">{companies.length} 家企业</p>
          </div>
          <button onClick={loadAll} className="text-xs text-blue-500 hover:text-blue-600">刷新</button>
        </div>
        {companies.map(c => {
          const color = LEVEL_COLOR[c.total_level] || '#999';
          return (
            <button
              key={c.company_name}
              onClick={() => selectCompany(c.company_name)}
              className={`w-full text-left px-4 py-3 border-b border-[#eee] transition-colors hover:bg-[#eee] ${
                selected?.company_name === c.company_name ? 'bg-[#e8e8e3]' : ''
              }`}
            >
              <div className="text-sm text-[#333] truncate">{c.company_name}</div>
              <div className="flex items-center gap-2 mt-1">
                <span className="text-xs font-semibold" style={{ color }}>{c.total_level}</span>
                <span className="text-xs text-gray-400">{c.total_score.toFixed(0)}/100</span>
              </div>
              <div className="flex gap-1 mt-1">
                {['environmental', 'social', 'governance'].map(dim => {
                  const d = c[dim as keyof ESGResult] as ESGDimension;
                  return (
                    <span key={dim} className="text-[10px] px-1 rounded" style={{ background: LEVEL_BG[d.level], color: LEVEL_COLOR[d.level] }}>
                      {dim[0].toUpperCase()}:{d.score.toFixed(0)}
                    </span>
                  );
                })}
              </div>
            </button>
          );
        })}
      </div>

      {/* right detail */}
      <div className="flex-1 overflow-y-auto p-6">
        {!selected ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            选择企业查看 ESG 评分详情
          </div>
        ) : (
          <div className="max-w-2xl">
            <h2 className="text-lg font-semibold text-[#333] mb-1">{selected.company_name}</h2>
            <p className="text-xs text-gray-400 mb-6">
              评估时间：{selected.assessed_at?.slice(0, 16).replace('T', ' ') || '-'}
            </p>

            {/* total */}
            <div className="bg-white border border-[#e8e8e3] rounded-2xl p-6 mb-6 text-center">
              <div className="text-4xl font-bold" style={{ color: LEVEL_COLOR[selected.total_level] }}>
                {selected.total_score.toFixed(0)}
              </div>
              <div className="text-sm mt-1" style={{ color: LEVEL_COLOR[selected.total_level] }}>
                {selected.total_level}
              </div>
              <div className="text-xs text-gray-400 mt-1">ESG 综合风险评分</div>
            </div>

            {/* 3 dimensions */}
            <div className="space-y-4">
              {(['environmental', 'social', 'governance'] as const).map(dim => {
                const d = selected[dim];
                return (
                  <div key={dim} className="bg-white border border-[#e8e8e3] rounded-2xl p-5">
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="text-sm font-medium text-[#555]">{DIM_LABELS[dim]}</h3>
                      <span className="text-xs font-semibold px-2 py-0.5 rounded-full" style={{ background: LEVEL_BG[d.level], color: LEVEL_COLOR[d.level] }}>
                        {d.level} · {d.score.toFixed(0)}分
                      </span>
                    </div>
                    {/* score bar */}
                    <div className="h-2 rounded-full bg-gray-100 mb-3">
                      <div className="h-full rounded-full transition-all" style={{ width: `${Math.min(d.score, 100)}%`, background: LEVEL_COLOR[d.level] }} />
                    </div>
                    {/* detail items */}
                    <div className="space-y-1.5">
                      {d.detail.map((item, i) => (
                        <div key={i} className="flex items-center justify-between text-xs">
                          <span className="text-gray-600">{item.item}</span>
                          <span className="text-gray-500">{item.value}</span>
                          <span className="text-[11px] font-medium" style={{ color: LEVEL_COLOR[item.level] }}>
                            {item.score > 0 ? `+${item.score}` : '—'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
