import { useState, useEffect, useRef } from 'react';
import { api } from '../api';
import type { RiskResult } from '../types';
import SentimentPanel from './SentimentPanel';

export default function AssessView({ initialName = '' }: { initialName?: string }) {
  const [name, setName] = useState(initialName);
  const [data, setData] = useState<RiskResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const prevName = useRef(initialName);

  useEffect(() => {
    if (initialName && initialName !== prevName.current) {
      prevName.current = initialName;
      setName(initialName);
      runAssess(initialName);
    }
  }, [initialName]);

  const runAssess = async (target: string) => {
    if (!target.trim()) return;
    setLoading(true); setError('');
    try {
      const res = await api.post<RiskResult>('/risk/assess', { company_name: target.trim() });
      setData(res);
    } catch {
      setError('评估失败');
      setData(null);
    }
    setLoading(false);
  };

  const assess = () => runAssess(name);

  const score = data?.risk_score ?? 0;
  const color = score <= 30 ? '#2d8c63' : score <= 60 ? '#d4a040' : '#e06060';
  const bg = score <= 30 ? '#ecfdf5' : score <= 60 ? '#fffbf0' : '#fef5f5';
  const rd = data?.risk_detail;
  const fin = data?.financial;

  return (
    <div className="max-w-2xl mx-auto py-6 px-4">
      <div className="flex gap-2 mb-6">
        <input
          value={name}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && assess()}
          placeholder="输入完整企业名称"
          className="flex-1 border border-[#e8e8e3] rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-[#bbb]"
        />
        <button onClick={assess} disabled={loading} className="bg-[#333] text-white rounded-xl px-6 py-2.5 text-sm hover:bg-[#555] disabled:opacity-50">
          {loading ? '评估中…' : '评估'}
        </button>
      </div>

      {error && <p className="text-red-400 text-sm mb-4">{error}</p>}

      {data && (
        <>
          {/* score */}
          <div className="bg-white border border-[#e8e8e3] rounded-2xl p-6 mb-4" style={{ background: bg }}>
            <div className="flex items-center gap-6">
              <div className="w-14 h-14 rounded-full flex items-center justify-center text-white font-bold text-lg" style={{ background: color }}>
                {data.risk_score}
              </div>
              <span className="text-lg font-semibold" style={{ color }}>{data.risk_level}</span>
              <div className="flex-1 bg-[#e5e5e0] h-2 rounded-full">
                <div className="h-full rounded-full transition-all duration-700" style={{ width: `${data.risk_score}%`, background: color }} />
              </div>
            </div>
          </div>

          {/* metrics */}
          {fin && (
            <>
              <div className="grid grid-cols-4 gap-3 mb-3">
                <Metric label="营收增长" value={`${(fin.revenue_growth * 100).toFixed(1)}%`} />
                <Metric label="净利增长" value={`${(fin.net_profit_growth * 100).toFixed(1)}%`} />
                <Metric label="负债率" value={`${(fin.debt_ratio * 100).toFixed(1)}%`} />
                <Metric label="每股现金流" value={`¥${fin.cash_flow.toFixed(2)}`} />
              </div>
              <div className="grid grid-cols-4 gap-3 mb-3">
                <Metric label="ROE" value={`${((fin.roe ?? 0) * 100).toFixed(1)}%`} />
                <Metric label="净利率" value={`${((fin.net_profit_margin ?? 0) * 100).toFixed(1)}%`} />
                <Metric label="流动比率" value={`${(fin.current_ratio ?? 0).toFixed(2)}`} />
                <Metric label="速动比率" value={`${(fin.quick_ratio ?? 0).toFixed(2)}`} />
              </div>
              <div className="grid grid-cols-4 gap-3 mb-3">
                <Metric label="存货周转" value={`${(fin.inventory_turnover ?? 0).toFixed(1)}`} />
                <Metric label="应收款周转" value={`${(fin.ar_turnover_days ?? 0).toFixed(0)}天`} />
                <Metric label="扣非占比" value={`${((fin.recurring_profit_ratio ?? 0) * 100).toFixed(1)}%`} />
                <Metric label="产权比率" value={`${(fin.equity_ratio ?? 0).toFixed(2)}`} />
              </div>
              <div className="grid grid-cols-3 gap-3 mb-6">
                <Metric label="营收趋势" value={fin.revenue_trend && fin.revenue_trend < 0 ? `↓ ${Math.abs(fin.revenue_trend * 100).toFixed(1)}%` : fin.revenue_trend ? '→ 稳定' : '-'} />
                <Metric label="负债趋势" value={fin.debt_trend && fin.debt_trend > 0 ? `↑ +${(fin.debt_trend * 100).toFixed(1)}%` : fin.debt_trend ? '→ 稳定' : '-'} />
                <Metric label="净利趋势" value={fin.net_profit_trend && fin.net_profit_trend < 0 ? `↓ ${Math.abs(fin.net_profit_trend * 100).toFixed(1)}%` : fin.net_profit_trend ? '→ 稳定' : '-'} />
              </div>
            </>
          )}
          {!fin && (
            <div className="grid grid-cols-4 gap-3 mb-6">
              {Array(4).fill(null).map((_, i) => <Metric key={i} label="—" value="无数据" />)}
            </div>
          )}

          {/* risk detail */}
          <h3 className="text-sm font-semibold mb-3 text-[#555]">风险明细</h3>
          <div className="grid grid-cols-4 gap-4 text-sm">
            <div>
              <p className="font-medium mb-2 text-[#555]">司法</p>
              <Row label="诉讼" value={rd?.lawsuit_count ?? 0} />
              <Row label="被执行" value={rd?.executed_count ?? 0} />
              <Row label="失信" value={rd?.dishonesty_count ?? 0} />
              <Row label="重大诉讼" warn={rd?.major_lawsuit} />
            </div>
            <div>
              <p className="font-medium mb-2 text-[#555]">经营</p>
              <Row label="经营异常" value={rd?.abnormal_operation_count ?? 0} />
              <Row label="行政处罚" value={rd?.administrative_penalty_count ?? 0} />
              <Row label="法人频繁变更" warn={rd?.legal_person_change_frequent} />
              <Row label="环保处罚" value={rd?.env_penalty_count ?? 0} />
            </div>
            <div>
              <p className="font-medium mb-2 text-[#555]">资金链</p>
              <Row label="对外担保" value={rd?.guarantee_count ?? 0} />
              <Row label="股权质押" value={rd?.pledge_count ?? 0} />
              <Row label="破产/清算" value={rd?.bankruptcy_count ?? 0} />
            </div>
            <div>
              <p className="font-medium mb-2 text-[#555]">财务</p>
              {fin ? (
                <>
                  <Row label="负债率>70%" warn={fin.debt_ratio > 0.7} extra={`${(fin.debt_ratio * 100).toFixed(1)}%`} />
                  <Row label="现金为负" warn={fin.cash_flow < 0} extra={`¥${fin.cash_flow.toFixed(2)}`} />
                </>
              ) : <p className="text-gray-400 text-xs">无财报数据</p>}
            </div>
          </div>

          {/* sentiment panel for this company */}
          {data && <div className="mt-4"><SentimentPanel companyName={name} /></div>}
        </>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-white border border-[#e8e8e3] rounded-xl p-3 text-center">
      <div className="text-base font-semibold">{value}</div>
      <div className="text-xs text-gray-400 mt-0.5">{label}</div>
    </div>
  );
}

function Row({ label, value, warn, extra }: { label: string; value?: number; warn?: boolean; extra?: string }) {
  const icon = warn === undefined ? '' : warn ? '⚠️' : '✓';
  const text = value !== undefined ? String(value) : '';
  return (
    <p className="text-xs text-gray-500 py-0.5">
      {label}  {icon && <span className="text-xs">{icon}</span>}  {text}{extra ? ` (${extra})` : ''}
    </p>
  );
}
