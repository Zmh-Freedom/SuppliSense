import type { MonitorIntake } from '../types';

function statusLabel(status: string): string {
  return ({ available: '已获取', missing: '未找到', failed: '查询失败', unavailable: '无访问权限', not_queried: '未查询' } as Record<string, string>)[status] || '待确认';
}

function statusTone(status: string): string {
  if (status === 'available') return 'bg-emerald-50 text-emerald-700';
  if (status === 'failed') return 'bg-red-50 text-red-700';
  return 'bg-amber-50 text-amber-700';
}

export default function MonitoringIntakePanel({
  intake,
  isLoading,
  isConfirming,
  error,
  onSelect,
  onConfirm,
  onClose,
}: {
  intake?: MonitorIntake | null;
  isLoading: boolean;
  isConfirming: boolean;
  error?: string;
  onSelect: (candidateId: string) => void;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const selected = intake?.candidates.find(item => item.candidate_id === intake.selected_candidate_id);
  return <section className="rounded-2xl border border-indigo-200 bg-indigo-50/50 p-5 shadow-sm">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="text-base font-semibold text-indigo-950">供应商自动调查</h2>
        <p className="mt-1 text-sm text-indigo-900">先确认主体与数据覆盖，再写入采购组织共享监控清单。</p>
      </div>
      <button type="button" onClick={onClose} className="rounded-lg px-2 py-1 text-xs text-indigo-700 hover:bg-white">关闭</button>
    </div>

    {isLoading && <div className="mt-5 rounded-xl border border-indigo-100 bg-white px-4 py-5 text-sm text-indigo-900">正在检索主体、内部交易、历史合作和可用公开资料…</div>}
    {error && <div className="mt-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">调查未完成：{error}</div>}

    {!isLoading && intake && <div className="mt-5 space-y-5">
      <section className="rounded-xl border border-indigo-100 bg-white p-4">
        <div className="text-xs font-medium text-indigo-700">调查线索</div>
        <div className="mt-1 text-sm font-semibold text-[var(--color-text)]">{intake.query}</div>
        {intake.external_profile && <div className="mt-3 rounded-lg bg-indigo-50 px-3 py-2 text-xs leading-5 text-indigo-950">外部主体资料：{intake.external_profile.company_name || intake.query} · {intake.external_profile.unified_social_credit_code || '未返回统一社会信用代码'}{intake.external_profile.registration_status ? ` · ${intake.external_profile.registration_status}` : ''}</div>}
      </section>

      <section>
        <div className="flex flex-wrap items-baseline justify-between gap-2"><h3 className="text-sm font-semibold text-[var(--color-text)]">主体候选</h3><span className="text-xs text-gray-500">仅从本次调查结果中选择</span></div>
        {intake.candidates.length === 0 ? <div className="mt-3 rounded-xl border border-dashed border-amber-200 bg-white px-4 py-4 text-sm text-amber-900">未找到可安全绑定的本地主体。请补充供应商代码、统一社会信用代码或天眼查链接后重新调查。</div> : <div className="mt-3 space-y-2">{intake.candidates.map(candidate => {
          const isSelected = selected?.candidate_id === candidate.candidate_id;
          return <button type="button" key={candidate.candidate_id} onClick={() => onSelect(candidate.candidate_id)} className={`w-full rounded-xl border p-4 text-left transition ${isSelected ? 'border-indigo-500 bg-white ring-2 ring-indigo-100' : 'border-indigo-100 bg-white hover:border-indigo-300'}`}>
            <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="font-medium text-[var(--color-text)]">{candidate.legal_name}</div><div className="mt-1 text-xs text-gray-500">{candidate.source} · {candidate.match_type} · {candidate.unified_social_credit_code || '未提供统一社会信用代码'}</div>{candidate.supplier_code && <div className="mt-1 text-xs text-gray-400">内部供应商代码：{candidate.supplier_code}</div>}</div><span className="rounded-full bg-indigo-50 px-2 py-1 text-xs font-medium text-indigo-700">匹配 {Math.round(candidate.confidence * 100)}%</span></div>
          </button>;
        })}</div>}
      </section>

      <section className="rounded-xl border border-[var(--color-border)] bg-white p-4">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">资料覆盖</h3>
        <div className="mt-3 divide-y divide-[var(--color-border)]">{intake.data_coverage.dimensions.map(item => <div key={item.key} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm"><div><div className="font-medium text-[var(--color-text)]">{item.label}</div><div className="mt-1 text-xs text-gray-500">{item.detail}</div></div><span className={`rounded-full px-2 py-1 text-xs ${statusTone(item.status)}`}>{statusLabel(item.status)}</span></div>)}</div>
      </section>

      <section className="rounded-xl border border-[var(--color-border)] bg-white p-4">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">调查结论</h3>
        <div className="mt-3 space-y-3">{intake.findings.map((finding, index) => <div key={`${finding.title}-${index}`} className="rounded-lg bg-[var(--color-background)] p-3"><div className="text-sm font-medium text-[var(--color-text)]">发现了什么：{finding.title}</div><div className="mt-1 text-xs leading-5 text-[var(--color-text-secondary)]">依据是什么：{finding.evidence}</div></div>)}</div>
        {intake.data_coverage.missing_dimensions.length > 0 && <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">采购人员需要核实什么：{intake.data_coverage.missing_dimensions.join('、')}。缺失资料不代表风险较低。</div>}
      </section>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-indigo-100 pt-4"><p className="text-xs leading-5 text-indigo-900">确认后会创建监控对象，并尽可能保存首次风险评估基线。</p><button type="button" onClick={onConfirm} disabled={!selected || isConfirming} className="min-h-[40px] rounded-lg bg-indigo-700 px-4 py-2 text-sm text-white hover:bg-indigo-800 disabled:cursor-not-allowed disabled:opacity-40">{isConfirming ? '正在建立监控…' : '确认主体并加入监控'}</button></div>
    </div>}
  </section>;
}
