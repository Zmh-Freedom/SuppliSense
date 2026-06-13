import { useState, useEffect } from 'react';
import { api } from '../api';

interface ImpactFactor {
  factor: string; detail: string; score: number; level: string;
}
interface AffectedParty {
  name: string; role: string; material: string; importance: string;
}
interface SimResult {
  company_name: string; scenario: string; scenario_desc: string;
  impact_score: number; impact_level: string;
  risk_score: number; risk_level: string;
  dependent_count: number; branch_count: number; related_count: number;
  impact_factors: ImpactFactor[];
  affected_parties: AffectedParty[];
  suggested_actions: string[];
}

interface SanctionMatch {
  type: string; name?: string; program?: string; authority?: string;
  match_type?: string; country?: string; sanction_level?: string;
  desc?: string; detail?: string; level: string;
}
interface SanctionsResult {
  company_name: string; sanctions_score: number; sanctions_level: string;
  match_count: number; matches: SanctionMatch[]; clean: boolean;
}

const SCENARIOS = [
  { key: 'bankruptcy', label: '🏭 供应商倒闭', desc: '供应完全中断' },
  { key: 'lawsuit', label: '⚖️ 重大诉讼', desc: '经营受限，交付延迟' },
  { key: 'disruption', label: '🔗 供应链中断', desc: '短期断供' },
  { key: 'quality', label: '⚠️ 质量事故', desc: '产品召回' },
];

const LEVEL_COLOR: Record<string, string> = {
  '严重': '#dc2626', '中等': '#d97706', '轻微': '#16a34a',
  'critical': '#dc2626', 'high': '#dc2626', 'medium': '#d97706', 'low': '#16a34a',
};

export default function ScenarioView() {
  const [companies, setCompanies] = useState<string[]>([]);
  const [selected, setSelected] = useState('');
  const [scenario, setScenario] = useState('bankruptcy');
  const [sim, setSim] = useState<SimResult | null>(null);
  const [sanc, setSanc] = useState<SanctionsResult | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    api.get<{ companies: string[] }>('/alert/watchlist', undefined, controller.signal)
      .then(d => setCompanies(d.companies || []))
      .catch(() => {});
    return () => controller.abort();
  }, []);

  const select = async (name: string) => {
    setSelected(name);
    setLoading(true);
    const [simRes, sancRes] = await Promise.all([
      api.get<SimResult>(`/analysis/scenario/${encodeURIComponent(name)}?scenario=${scenario}`),
      api.get<SanctionsResult>(`/analysis/sanctions/${encodeURIComponent(name)}`),
    ]);
    setSim(simRes); setSanc(sancRes); setLoading(false);
  };

  const runScenario = async (s: string) => {
    setScenario(s);
    if (!selected) return;
    setLoading(true);
    const simRes = await api.get<SimResult>(`/analysis/scenario/${encodeURIComponent(selected)}?scenario=${s}`);
    setSim(simRes); setLoading(false);
  };

  return (
    <div className="flex h-full">
      {/* left */}
      <div className="w-72 border-r border-[#e8e8e3] bg-[#fafaf8] overflow-y-auto shrink-0">
        <div className="px-4 py-3 border-b border-[#e8e8e3]">
          <h2 className="text-sm font-semibold text-[#333]">情景 & 制裁</h2>
          <p className="text-[11px] text-gray-400 mt-0.5">{companies.length} 家企业</p>
        </div>
        <div className="py-1">
          {companies.map(name => (
            <button key={name} onClick={() => select(name)}
              className={`w-full text-left px-4 py-2.5 text-sm ${selected === name ? 'bg-[#e8e8e3] font-medium' : 'hover:bg-[#eee]'}`}>{name}</button>
          ))}
        </div>
      </div>

      {/* right */}
      <div className="flex-1 overflow-y-auto p-6">
        {!selected ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">选择企业查看情景模拟与制裁筛查</div>
        ) : loading ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">加载中…</div>
        ) : (
          <div className="max-w-2xl space-y-6">
            <h2 className="text-lg font-semibold text-[#333]">{selected}</h2>

            {/* ---- scenario simulator ---- */}
            <div>
              <h3 className="text-sm font-medium text-[#555] mb-3">🎯 情景模拟</h3>

              {/* scenario selector */}
              <div className="grid grid-cols-4 gap-2 mb-4">
                {SCENARIOS.map(s => (
                  <button key={s.key} onClick={() => runScenario(s.key)}
                    className={`text-xs p-3 rounded-xl border transition-colors text-left ${
                      scenario === s.key ? 'border-[#333] bg-[#f5f5f5]' : 'border-[#e8e8e3] hover:border-[#ccc]'
                    }`}>
                    <div className="font-medium text-[#333]">{s.label}</div>
                    <div className="text-gray-400 mt-0.5">{s.desc}</div>
                  </button>
                ))}
              </div>

              {sim && (
                <>
                  {/* impact score */}
                  <div className="bg-white border border-[#e8e8e3] rounded-2xl p-5 mb-4">
                    <div className="flex items-center gap-4">
                      <div className="text-4xl font-bold" style={{ color: LEVEL_COLOR[sim.impact_level] }}>{sim.impact_score}</div>
                      <div>
                        <div className="text-sm font-medium" style={{ color: LEVEL_COLOR[sim.impact_level] }}>影响等级：{sim.impact_level}</div>
                        <div className="text-xs text-gray-400 mt-1">{sim.scenario_desc}</div>
                        <div className="text-xs text-gray-400">
                          当前风险 {sim.risk_score}/100 · {sim.dependent_count}个依赖 · {sim.branch_count}个分支
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* impact factors */}
                  <div className="grid grid-cols-2 gap-2 mb-4">
                    {sim.impact_factors.map((f, i) => (
                      <div key={i} className="bg-white border border-[#e8e8e3] rounded-xl p-3">
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-gray-600">{f.factor}</span>
                          <span className="text-xs font-bold" style={{ color: LEVEL_COLOR[f.level] }}>+{f.score}</span>
                        </div>
                        <div className="text-[11px] text-gray-400 mt-0.5">{f.detail}</div>
                      </div>
                    ))}
                  </div>

                  {/* affected parties */}
                  {sim.affected_parties.length > 0 && (
                    <div className="mb-4">
                      <div className="text-xs font-medium text-[#555] mb-2">受影响方</div>
                      <div className="space-y-1">
                        {sim.affected_parties.map((p, i) => (
                          <div key={i} className="bg-white border border-[#e8e8e3] rounded-lg px-3 py-2 text-xs flex items-center gap-2">
                            <span className="text-gray-700">{p.name.slice(0, 20)}</span>
                            <span className="text-gray-400">({p.role})</span>
                            {p.material && <span className="text-gray-400">{p.material}</span>}
                            <div className="flex-1" />
                            <span className={`px-1.5 py-0.5 rounded text-[10px] ${p.importance === 'critical' ? 'bg-red-50 text-red-500' : 'bg-gray-100 text-gray-500'}`}>
                              {p.importance}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* actions */}
                  <div className="bg-blue-50 border border-blue-100 rounded-xl p-4">
                    <div className="text-xs font-medium text-blue-700 mb-2">建议行动</div>
                    <ul className="space-y-1">
                      {sim.suggested_actions.map((a, i) => (
                        <li key={i} className="text-xs text-blue-800">{a}</li>
                      ))}
                    </ul>
                  </div>
                </>
              )}
            </div>

            {/* ---- sanctions ---- */}
            {sanc && (
              <div>
                <h3 className="text-sm font-medium text-[#555] mb-3">🛡️ 制裁筛查</h3>

                <div className="bg-white border border-[#e8e8e3] rounded-2xl p-5 mb-4">
                  <div className="flex items-center gap-4">
                    <div className="text-4xl font-bold" style={{ color: LEVEL_COLOR[sanc.sanctions_level] }}>{sanc.sanctions_score}</div>
                    <div>
                      <div className="text-sm font-medium" style={{ color: LEVEL_COLOR[sanc.sanctions_level] }}>
                        {sanc.clean ? '✅ 未命中制裁名单' : `⚠️ ${sanc.sanctions_level === 'critical' ? '严重' : sanc.sanctions_level === 'high' ? '高风险' : '中风险'}`}
                      </div>
                      <div className="text-xs text-gray-400 mt-1">
                        {sanc.match_count > 0 ? `命中 ${sanc.match_count} 条记录` : '不在已知制裁/黑名单中'}
                      </div>
                    </div>
                  </div>
                </div>

                {sanc.matches.length > 0 && (
                  <div className="space-y-1.5">
                    {sanc.matches.map((m, i) => (
                      <div key={i} className={`rounded-lg px-4 py-3 flex items-center gap-3 ${
                        m.level === 'critical' ? 'bg-red-50 border border-red-100' :
                        m.level === 'high' ? 'bg-amber-50 border border-amber-100' : 'bg-gray-50 border border-gray-100'
                      }`}>
                        <span className="text-lg">{m.level === 'critical' ? '🔴' : m.level === 'high' ? '🟡' : '⚪'}</span>
                        <div className="flex-1">
                          <div className="text-sm font-medium text-[#333]">{m.name || m.detail || m.country}</div>
                          <div className="text-xs text-gray-500">
                            {m.program && `${m.program} · `}{m.authority && `${m.authority} · `}
                            {m.match_type && `${m.match_type} · `}
                            {m.desc && m.desc}
                          </div>
                        </div>
                        <span className={`text-xs px-2 py-0.5 rounded-full ${
                          m.level === 'critical' ? 'bg-red-100 text-red-600' :
                          m.level === 'high' ? 'bg-amber-100 text-amber-700' : 'bg-gray-100 text-gray-600'
                        }`}>{m.level}</span>
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
