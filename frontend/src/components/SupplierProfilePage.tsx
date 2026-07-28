import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../query-keys';
import type { SupplierProfile } from '../types';
import { getRiskColor } from '../riskColors';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';

export default function SupplierProfilePage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [tab, setTab] = useState<'overview' | 'risk' | 'financial' | 'relationships' | 'changelog'>('overview');

  const { data, isLoading, error } = useQuery({
    queryKey: queryKeys.supplierProfile(id!),
    queryFn: () => api.get<SupplierProfile>(`/suppliers/${id}/profile`),
    enabled: !!id,
  });

  if (isLoading) {
    return (
      <div className="max-w-4xl mx-auto py-6 px-4">
        <div className="animate-pulse space-y-4">
          <div className="h-8 w-48 bg-gray-200 rounded" />
          <div className="h-64 bg-gray-100 rounded-2xl" />
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="max-w-4xl mx-auto py-6 px-4">
        <button onClick={() => navigate('/suppliers')} className="text-sm text-[var(--color-primary-bg)] hover:underline mb-4">
          &larr; 返回供应商列表
        </button>
        <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-8 text-center">
          <p className="text-[var(--color-text-secondary)]">
            {error ? '加载供应商画像失败' : '供应商不存在'}
          </p>
        </div>
      </div>
    );
  }

  const { basic_info, risk, financial, sentiment, compliance, esg, alerts, relationships, changelog } = data;
  const riskScore = risk?.risk_score ?? 0;
  const riskLevel = risk?.risk_level ?? '未知';

  return (
    <div className="max-w-4xl mx-auto py-6 px-4">
      {/* ---- header ---- */}
      <button
        onClick={() => navigate('/suppliers')}
        className="text-sm text-[var(--color-primary-bg)] hover:underline mb-3 inline-block"
      >
        &larr; 返回供应商列表
      </button>

      <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 shadow-sm mb-5">
        <div className="flex items-start justify-between flex-wrap gap-4">
          <div className="flex-1 min-w-0">
            <h1 className="text-xl font-bold" style={{ color: '#333' }}>{basic_info.name}</h1>
            <div className="flex flex-wrap gap-x-4 gap-y-1 mt-1.5 text-xs" style={{ color: '#555' }}>
              {basic_info.unified_code && <span>统一社会信用代码：{basic_info.unified_code}</span>}
              {basic_info.legal_person && <span>法定代表人：{basic_info.legal_person}</span>}
              {basic_info.reg_status && <span>经营状态：{basic_info.reg_status}</span>}
            </div>
            <div className="flex flex-wrap gap-2 mt-2">
              <StatusBadge status={basic_info.status} />
              {basic_info.scale && <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">{basic_info.scale}</span>}
              {risk?.in_watchlist && <span className="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-600">监控中</span>}
            </div>
          </div>

          {/* risk score circle */}
          <div className="flex items-center gap-3 shrink-0">
            <div className="flex flex-col items-center">
              <div
                className="w-20 h-20 rounded-full flex items-center justify-center text-white text-2xl font-bold shadow-md"
                style={{ background: getRiskColor(riskScore) }}
              >
                {riskScore}
              </div>
              <span className="text-xs mt-1 font-semibold" style={{ color: getRiskColor(riskScore) }}>
                {riskLevel}
              </span>
            </div>
          </div>
        </div>

        {/* quick info cards */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-5 pt-4 border-t border-[var(--color-border)]">
          <InfoCard label="注册资本" value={basic_info.registered_capital ?? '-'} />
          <InfoCard label="成立时间" value={basic_info.establish_time ?? '-'} />
          <InfoCard label="行业" value={basic_info.industry ?? basic_info.categories?.[0] ?? '-'} />
          <InfoCard label="地区" value={basic_info.regions?.[0] ?? '-'} />
        </div>
      </div>

      {/* ---- tabs ---- */}
      <div className="flex gap-1 mb-4 border-b border-[var(--color-border)] pb-0">
        {([
          ['overview', '概览'],
          ['risk', '风险'],
          ['financial', '财务'],
          ['relationships', '关联'],
          ['changelog', '日志'],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`px-4 py-2 text-sm rounded-t-lg transition-colors ${
              tab === key
                ? 'bg-[var(--color-surface)] border border-[var(--color-border)] border-b-transparent font-semibold text-[var(--color-primary-bg)]'
                : 'text-[var(--color-text-secondary)] hover:text-[var(--color-text)]'
            }`}
            style={tab === key ? { marginBottom: -1 } : undefined}
          >
            {label}
          </button>
        ))}
      </div>

      {/* ---- tab content ---- */}
      {tab === 'overview' && <OverviewTab risk={risk} sentiment={sentiment} compliance={compliance} alerts={alerts} />}
      {tab === 'risk' && <RiskTab compliance={compliance} esg={esg} />}
      {tab === 'financial' && <FinancialTab financial={financial} />}
      {tab === 'relationships' && <RelationshipsTab relationships={relationships} />}
      {tab === 'changelog' && <ChangelogTab changelog={changelog} supplierId={id!} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab content components
// ---------------------------------------------------------------------------

function OverviewTab({
  risk, sentiment, compliance, alerts,
}: {
  risk?: SupplierProfile['risk'];
  sentiment?: SupplierProfile['sentiment'];
  compliance?: SupplierProfile['compliance'];
  alerts: SupplierProfile['alerts'];
}) {
  return (
    <div className="space-y-4">
      {/* summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <SummaryCard label="风险评分" value={risk?.risk_score?.toString() ?? '-'} color={risk ? getRiskColor(risk.risk_score) : '#999'} />
        <SummaryCard label="舆情" value={sentiment?.overall_sentiment ?? '-'} color="#8b5cf6" />
        <SummaryCard label="制裁筛查" value={compliance?.sanctions_clean ? '正常' : `${compliance?.sanctions_match_count}条匹配`} color={compliance?.sanctions_clean ? '#2d8c63' : '#e06060'} />
        <SummaryCard label="告警" value={alerts.length.toString()} color={alerts.length > 0 ? '#e06060' : '#999'} />
      </div>

      {/* risk trend chart */}
      {risk && risk.trend.length >= 2 && (
        <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
          <h3 className="text-sm font-semibold mb-3" style={{ color: '#333' }}>风险评分趋势（近90天）</h3>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={risk.trend}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f5f5f5" vertical={false} />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#999' }} axisLine={{ stroke: '#eee' }} tickLine={false} />
              <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: '#999' }} axisLine={{ stroke: '#eee' }} tickLine={false} />
              <Tooltip
                contentStyle={{ background: '#fff', border: '1px solid #e8e8e3', borderRadius: 12, fontSize: 12 }}
                formatter={(value) => [`${Number(value ?? 0)} 分`, '风险评分']}
              />
              <Line type="monotone" dataKey="risk_score" stroke={getRiskColor(risk.risk_score)} strokeWidth={2} dot={{ r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* recent alerts */}
      {alerts.length > 0 && (
        <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
          <h3 className="text-sm font-semibold mb-3" style={{ color: '#333' }}>最近告警</h3>
          <div className="space-y-2">
            {alerts.slice(0, 5).map((a) => (
              <div key={a._id} className="flex items-center justify-between py-2 px-3 rounded-xl bg-gray-50 text-sm">
                <span className="font-medium" style={{ color: a.severity === 'critical' ? '#e06060' : '#d4a040' }}>
                  {a.severity === 'critical' ? '🔴 严重' : '🟡 警告'}
                </span>
                <span className="text-xs text-gray-400">{a.created_at}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function RiskTab({
  compliance, esg,
}: {
  compliance?: SupplierProfile['compliance'];
  esg?: SupplierProfile['esg'];
}) {
  return (
    <div className="space-y-4">
      {/* compliance detail */}
      {compliance && (
        <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
          <h3 className="text-sm font-semibold mb-3" style={{ color: '#333' }}>合规状态</h3>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <Metric label="制裁匹配" value={compliance.sanctions_match_count.toString()} warn={!compliance.sanctions_clean} />
            <Metric label="诉讼" value={compliance.lawsuit_count.toString()} warn={compliance.lawsuit_count > 10} />
            <Metric label="被执行" value={compliance.executed_count.toString()} warn={compliance.executed_count > 0} />
            <Metric label="失信" value={compliance.dishonesty_count.toString()} warn={compliance.dishonesty_count > 0} />
            <Metric label="经营异常" value={compliance.abnormal_operation_count.toString()} warn={compliance.abnormal_operation_count > 0} />
            <Metric label="行政处罚" value={compliance.administrative_penalty_count.toString()} warn={compliance.administrative_penalty_count > 3} />
          </div>
        </div>
      )}

      {/* esg */}
      {esg && (esg.environmental || esg.social || esg.governance) && (
        <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
          <h3 className="text-sm font-semibold mb-3" style={{ color: '#333' }}>ESG 评估</h3>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {esg.environmental && (
              <ESGMetric title="环境 (E)" score={esg.environmental.score} level={esg.environmental.level} />
            )}
            {esg.social && (
              <ESGMetric title="社会 (S)" score={esg.social.score} level={esg.social.level} />
            )}
            {esg.governance && (
              <ESGMetric title="治理 (G)" score={esg.governance.score} level={esg.governance.level} />
            )}
          </div>
        </div>
      )}

      {!compliance && !esg && (
        <div className="text-center py-12 text-sm text-gray-400">暂无风险详细数据</div>
      )}
    </div>
  );
}

function FinancialTab({
  financial,
}: {
  financial?: SupplierProfile['financial'];
}) {
  if (!financial) {
    return <div className="text-center py-12 text-sm text-gray-400">暂无财务数据（可能为非上市企业）</div>;
  }

  return (
    <div className="space-y-4">
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
        <h3 className="text-sm font-semibold mb-3" style={{ color: '#333' }}>增长指标</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Metric label="营收增长率" value={financial.revenue_growth != null ? `${financial.revenue_growth.toFixed(1)}%` : '-'} warn={financial.revenue_growth != null && financial.revenue_growth < 0} />
          <Metric label="净利润增长率" value={financial.net_profit_growth != null ? `${financial.net_profit_growth.toFixed(1)}%` : '-'} warn={financial.net_profit_growth != null && financial.net_profit_growth < 0} />
          <Metric label="ROE" value={financial.roe != null ? `${financial.roe.toFixed(1)}%` : '-'} />
          <Metric label="净利率" value={financial.net_profit_margin != null ? `${financial.net_profit_margin.toFixed(1)}%` : '-'} />
        </div>
      </div>

      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
        <h3 className="text-sm font-semibold mb-3" style={{ color: '#333' }}>偿债与流动性</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Metric label="资产负债率" value={financial.debt_ratio != null ? `${financial.debt_ratio.toFixed(1)}%` : '-'} warn={financial.debt_ratio != null && financial.debt_ratio > 70} />
          <Metric label="现金流(亿)" value={financial.cash_flow != null ? financial.cash_flow.toFixed(2) : '-'} warn={financial.cash_flow != null && financial.cash_flow < 0} />
          <Metric label="流动比率" value={financial.current_ratio != null ? financial.current_ratio.toFixed(2) : '-'} />
          <Metric label="速动比率" value={financial.quick_ratio != null ? financial.quick_ratio.toFixed(2) : '-'} />
        </div>
      </div>

      {(financial.credit_rating || financial.annual_revenue) && (
        <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
          <h3 className="text-sm font-semibold mb-3" style={{ color: '#333' }}>主数据财务信息</h3>
          <div className="grid grid-cols-2 gap-3">
            {financial.credit_rating && <Metric label="信用评级" value={financial.credit_rating} />}
            {financial.annual_revenue && <Metric label="年营收(万元)" value={financial.annual_revenue.toLocaleString()} />}
          </div>
        </div>
      )}

      {financial.cached_at && (
        <p className="text-xs text-gray-400 text-right">数据更新于 {financial.cached_at}</p>
      )}
    </div>
  );
}

function RelationshipsTab({ relationships }: { relationships?: SupplierProfile['relationships'] }) {
  if (!relationships || relationships.related_count === 0) {
    return <div className="text-center py-12 text-sm text-gray-400">暂无关联关系数据</div>;
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <SummaryCard label="关联方" value={relationships.related_count.toString()} color="#6366f1" />
        <SummaryCard label="分支机构" value={relationships.branch_count.toString()} color="#8b5cf6" />
        <SummaryCard label="供应链依赖" value={relationships.dependency_count.toString()} color="#a78bfa" />
        <SummaryCard label="高风险关联" value={relationships.high_risk_related_count.toString()} color="#e06060" />
      </div>

      {relationships.entities.length > 0 && (
        <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
          <h3 className="text-sm font-semibold mb-3" style={{ color: '#333' }}>关联实体</h3>
          <div className="space-y-2">
            {relationships.entities.map((e, i) => (
              <div key={i} className="flex items-center justify-between py-2.5 px-3 rounded-xl bg-gray-50 text-sm">
                <span className="font-medium">{e.name}</span>
                <div className="flex items-center gap-2">
                  <span className="text-xs px-1.5 py-0.5 rounded bg-purple-50 text-purple-600">{e.relation_type}</span>
                  {e.risk_score != null && (
                    <span className="text-xs font-semibold" style={{ color: getRiskColor(e.risk_score) }}>
                      {e.risk_score}分
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ChangelogTab({ changelog, supplierId }: { changelog: SupplierProfile['changelog']; supplierId: string }) {
  const { data: changelogData, isLoading } = useQuery({
    queryKey: queryKeys.supplierChangelog(supplierId),
    queryFn: () => api.get<{ items: SupplierProfile['changelog'] }>(`/suppliers/${supplierId}/changelog?limit=20`),
    enabled: changelog.length === 0,
  });

  const entries = changelog.length > 0 ? changelog : (changelogData?.items ?? []);

  if (isLoading) {
    return <div className="text-center py-12 text-sm text-gray-400">加载中...</div>;
  }

  if (entries.length === 0) {
    return <div className="text-center py-12 text-sm text-gray-400">暂无变更记录</div>;
  }

  return (
    <div className="space-y-3">
      {entries.map((entry, i) => (
        <div key={i} className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-4 shadow-sm">
          <div className="text-xs text-gray-400 mb-2">{entry.changed_at}</div>
          {Object.entries(entry.changed).map(([field, change]) => (
            <div key={field} className="flex items-center gap-2 text-sm py-1">
              <span className="font-medium min-w-[80px]">{field}</span>
              <span className="text-red-400 line-through">{String(change.old ?? '-')}</span>
              <span className="text-gray-300">&rarr;</span>
              <span className="text-green-600">{String(change.new ?? '-')}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    approved: 'bg-green-50 text-green-600',
    active: 'bg-green-50 text-green-600',
    prospective: 'bg-blue-50 text-blue-600',
    suspended: 'bg-amber-50 text-amber-600',
    blocked: 'bg-red-50 text-red-600',
    deprecated: 'bg-gray-50 text-gray-400',
  };
  const labels: Record<string, string> = {
    approved: '已准入',
    active: '活跃',
    prospective: '待考察',
    suspended: '已停用',
    blocked: '已拉黑',
    deprecated: '已淘汰',
  };
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded-full ${colors[status] ?? 'bg-gray-50 text-gray-500'}`}>
      {labels[status] ?? status}
    </span>
  );
}

function InfoCard({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-gray-400">{label}</div>
      <div className="text-sm font-medium mt-0.5 truncate" title={value}>{value}</div>
    </div>
  );
}

function SummaryCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-4 shadow-sm">
      <div className="text-2xl font-bold" style={{ color }}>{value}</div>
      <div className="text-xs text-gray-400 mt-1">{label}</div>
    </div>
  );
}

function Metric({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div>
      <div className="text-xs text-gray-400">{label}</div>
      <div className={`text-sm font-semibold mt-0.5 ${warn ? 'text-red-500' : ''}`} style={warn ? undefined : { color: '#333' }}>
        {value}
      </div>
    </div>
  );
}

function ESGMetric({ title, score, level }: { title: string; score: number; level: string }) {
  return (
    <div className="text-center p-3 rounded-xl bg-gray-50">
      <div className="text-xs text-gray-400">{title}</div>
      <div className="text-xl font-bold mt-1" style={{ color: getRiskColor(score) }}>{score}</div>
      <div className="text-xs mt-0.5" style={{ color: getRiskColor(score) }}>{level}</div>
    </div>
  );
}
