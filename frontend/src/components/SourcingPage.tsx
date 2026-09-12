import { useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../query-keys';
import { getRiskColor, getRiskLevelLabel } from '../riskColors';
import type { SourcingRequestDetail, SourcingResultItem, SourcingRiskCandidate } from '../types';
import SourcingRiskWorkbench from './SourcingRiskWorkbench';
import SourcingRiskCandidateCard from './SourcingRiskCandidateCard';

type Step = { label: string; done: boolean };

export default function SourcingPage() {
  const qc = useQueryClient();
  const [form, setForm] = useState({ title: '', category: '', spec: '', region: '', quantity: '' });
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<SourcingResultItem[]>([]);
  const [externalCandidates, setExternalCandidates] = useState<SourcingRiskCandidate[]>([]);
  const [externalStatus, setExternalStatus] = useState('not_required');
  const [externalFailures, setExternalFailures] = useState<Array<{ stage?: string; reason?: string }>>([]);
  const [error, setError] = useState('');
  const [currentRequestId, setCurrentRequestId] = useState('');
  const [steps, setSteps] = useState<Step[]>([]);
  const [selectMsg, setSelectMsg] = useState('');
  // Track which results have been added to risk monitoring.
  const [watchedIds, setWatchedIds] = useState<Set<string>>(new Set());

  const historyQuery = useQuery({
    queryKey: queryKeys.sourcingRequests,
    queryFn: () => api.get<{ items: SourcingRequestDetail[]; total: number }>('/sourcing/requests'),
  });

  const createMutation = useMutation({
    mutationFn: () => api.post<{ request_id: string }>('/sourcing/requests', {
      title: form.title || `${form.category}采购`,
      category: form.category,
      spec: form.spec,
      region_required: form.region || undefined,
      quantity: form.quantity ? parseInt(form.quantity) : undefined,
    }),
    onSuccess: (data) => {
      setCurrentRequestId(data.request_id);
      startSearch(data.request_id);
    },
    onError: () => setError('创建寻源请求失败'),
  });

  const startSearch = useCallback(async (requestId: string) => {
    setSearching(true);
    setError('');
    setResults([]);
    setExternalCandidates([]);
    setExternalStatus('not_required');
    setExternalFailures([]);
    setSteps([{ label: '检索中', done: false }, { label: '评估中', done: false }, { label: '排序中', done: false }]);

    try {
      const res = await fetch(`/api/v1/sourcing/requests/${requestId}/search`, {
        method: 'POST',
        credentials: 'same-origin',
      });

      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(typeof detail?.detail === 'string' ? detail.detail : `搜索请求失败（${res.status}）`);
      }

      const reader = res.body?.getReader();
      if (!reader) throw new Error('No stream');

      const decoder = new TextDecoder();
      let buffer = '';
      let eventType = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim();
          } else if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              if (eventType === 'retrieving') {
                setSteps(prev => prev.map((s, i) => i === 0 ? { ...s, done: true } : s));
              } else if (eventType === 'assessing') {
                setSteps(prev => prev.map((s, i) => i <= 1 ? { ...s, done: true } : s));
              } else if (eventType === 'ranking') {
                setSteps(prev => prev.map(s => ({ ...s, done: true })));
              } else if (eventType === 'sourcing_result') {
                setResults(Array.isArray(data.results) ? data.results : []);
                setExternalCandidates(Array.isArray(data.external_candidates) ? data.external_candidates : []);
                setExternalStatus(String(data.external_status || 'not_required'));
                setExternalFailures(Array.isArray(data.external_failure_reasons) ? data.external_failure_reasons : []);
              } else if (eventType === 'done') {
                setSteps(prev => prev.map(s => ({ ...s, done: true })));
              } else if (eventType === 'error') {
                setError(data.message || '搜索失败');
              }
            } catch { /* ignore */ }
          }
        }
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '搜索请求失败');
    } finally {
      setSearching(false);
      qc.invalidateQueries({ queryKey: queryKeys.sourcingRequests });
    }
  }, [qc]);

  const selectMutation = useMutation({
    mutationFn: ({ resultId, action }: { resultId: string; action: string }) =>
      api.post(`/sourcing/results/${resultId}/select`, { action }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: queryKeys.sourcingRequests });
      if (vars.action === 'watchlist') {
        setWatchedIds(prev => new Set(prev).add(vars.resultId));
        setSelectMsg('已加入监控列表');
      }
      setTimeout(() => setSelectMsg(''), 3000);
    },
  });

  const verifyMutation = useMutation({
    mutationFn: (candidateId: string) => api.post<SourcingRiskCandidate>(`/sourcing/external-candidates/${encodeURIComponent(candidateId)}/verify`),
    onSuccess: (candidate) => {
      setExternalCandidates(current => current.map(item => item.candidate_id === candidate.candidate_id ? candidate : item));
      setSelectMsg('天眼查核验已完成，请继续确认技术能力、认证、产能与报价');
      setTimeout(() => setSelectMsg(''), 3000);
    },
    onError: () => setError('天眼查核验失败，请稍后重试'),
  });

  const canSubmit = form.category && form.spec;
  const hasResults = results.length > 0;
  const hasExternalCandidates = externalCandidates.length > 0;
  const hasHistory = historyQuery.data && historyQuery.data.items.length > 0;
  const showEmpty = !searching && !error && currentRequestId && !hasResults && !hasExternalCandidates;

  return (
    <div className="h-full py-6 px-6 overflow-auto">
      <div className="max-w-3xl mx-auto space-y-6">
        <div>
          <h2 className="text-lg font-bold text-[var(--color-text)]">智能寻源</h2>
          <p className="text-sm text-[var(--color-text-secondary)] mt-1">先从历史合作和外部候选中召回供应商，再进行主体与风险核验。</p>
        </div>

        <SourcingRiskWorkbench />

        <details className="border-t border-[var(--color-border)] pt-4">
          <summary className="cursor-pointer list-none text-sm font-medium text-[var(--color-text-secondary)] [&::-webkit-details-marker]:hidden">快速寻源（辅助入口）<span className="ml-2 text-xs font-normal text-gray-400">适用于已有明确规格的旧式提交</span></summary>
          <div className="pt-5">
          <h3 className="text-sm font-semibold text-[var(--color-text-secondary)] mb-4">快速提交采购需求</h3>

        {/* 采购需求表单 */}
        <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 shadow-sm space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-[var(--color-text-secondary)] mb-1 block">采购品类 *</label>
              <input
                value={form.category}
                onChange={e => setForm(p => ({ ...p, category: e.target.value }))}
                placeholder="如：安防设备、电子元器件"
                className="w-full text-sm rounded-xl border border-[var(--color-border)] px-3 py-2 bg-[var(--color-input-bg)] focus:outline-none focus:border-[var(--color-focus-ring)]"
              />
            </div>
            <div>
              <label className="text-xs text-[var(--color-text-secondary)] mb-1 block">需求标题</label>
              <input
                value={form.title}
                onChange={e => setForm(p => ({ ...p, title: e.target.value }))}
                placeholder="如：2025Q3摄像头采购"
                className="w-full text-sm rounded-xl border border-[var(--color-border)] px-3 py-2 bg-[var(--color-input-bg)] focus:outline-none focus:border-[var(--color-focus-ring)]"
              />
            </div>
            <div className="col-span-2">
              <label className="text-xs text-[var(--color-text-secondary)] mb-1 block">规格/技术要求 *</label>
              <textarea
                value={form.spec}
                onChange={e => setForm(p => ({ ...p, spec: e.target.value }))}
                placeholder="如：1080P红外夜视、IP67防水、支持PoE供电"
                rows={2}
                className="w-full text-sm rounded-xl border border-[var(--color-border)] px-3 py-2 bg-[var(--color-input-bg)] focus:outline-none focus:border-[var(--color-focus-ring)] resize-none"
              />
            </div>
            <div>
              <label className="text-xs text-[var(--color-text-secondary)] mb-1 block">期望地域</label>
              <input
                value={form.region}
                onChange={e => setForm(p => ({ ...p, region: e.target.value }))}
                placeholder="如：广东、华东"
                className="w-full text-sm rounded-xl border border-[var(--color-border)] px-3 py-2 bg-[var(--color-input-bg)] focus:outline-none focus:border-[var(--color-focus-ring)]"
              />
            </div>
            <div>
              <label className="text-xs text-[var(--color-text-secondary)] mb-1 block">预计数量</label>
              <input
                value={form.quantity}
                onChange={e => setForm(p => ({ ...p, quantity: e.target.value }))}
                placeholder="如：1000"
                type="number"
                className="w-full text-sm rounded-xl border border-[var(--color-border)] px-3 py-2 bg-[var(--color-input-bg)] focus:outline-none focus:border-[var(--color-focus-ring)]"
              />
            </div>
          </div>

          <button
            onClick={() => canSubmit && createMutation.mutate()}
            disabled={!canSubmit || searching || createMutation.isPending}
            className="bg-[var(--color-primary-bg)] text-white rounded-xl px-6 py-2.5 text-sm hover:bg-[var(--color-primary-hover)] disabled:opacity-40 transition-colors"
          >
            {searching || createMutation.isPending ? '提交中...' : '提交采购需求'}
          </button>

          {error && (
            <p className="text-xs text-red-500 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>
          )}
          {selectMsg && (
            <p className="text-xs text-green-600 bg-green-50 border border-green-200 rounded-lg px-3 py-2">{selectMsg}</p>
          )}
        </div>

        {/* 搜索进度 */}
        {searching && steps.length > 0 && (
          <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
            <div className="flex items-center gap-4">
              {steps.map((s, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <span className={`w-5 h-5 rounded-full flex items-center justify-center text-xs text-white transition-colors ${s.done ? 'bg-green-500' : 'bg-[var(--color-primary-bg)] animate-pulse'}`}>
                    {s.done ? '✓' : i + 1}
                  </span>
                  <span className={`text-xs ${s.done ? 'text-green-600' : 'text-[var(--color-text-secondary)]'}`}>{s.label}</span>
                  {i < steps.length - 1 && <span className="text-gray-300 mx-1">→</span>}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 搜索结果 */}
        {hasResults && (
          <div className="space-y-3">
            <h3 className="text-sm font-semibold text-[var(--color-text-secondary)]">
              候选供应商 ({results.length})
            </h3>
            {results.map(r => (
              <SourcingResultCard
                key={r.result_id}
                result={r}
                watched={watchedIds.has(r.result_id)}
                onWatch={() => selectMutation.mutate({ resultId: r.result_id, action: 'watchlist' })}
              />
            ))}
          </div>
        )}

        {hasExternalCandidates && (
          <div className="space-y-3">
            <div>
              <h3 className="text-sm font-semibold text-[var(--color-text-secondary)]">外部待核验候选 ({externalCandidates.length})</h3>
              <p className="text-xs text-amber-700 mt-1">盖世人工获取候选优先展示；需完成天眼查主体与风险核验，以及采购技术、认证、产能和报价确认，才可进入后续准入流程。</p>
            </div>
            {externalCandidates.map(candidate => <SourcingRiskCandidateCard key={candidate.candidate_id ?? candidate.supplier_name} candidate={candidate} onVerify={candidate.candidate_id ? () => verifyMutation.mutate(candidate.candidate_id!) : undefined} verifying={verifyMutation.isPending && verifyMutation.variables === candidate.candidate_id} />)}
          </div>
        )}

        {!searching && externalStatus === 'failed' && externalFailures.length > 0 && (
          <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800">
            外部发现未完成：{externalFailures.map(item => `${item.stage || '外部阶段'}：${item.reason || '调用失败'}`).join('；')}
          </div>
        )}

        {/* 空结果 */}
        {showEmpty && (
          <div className="text-center py-12 space-y-2">
            <p className="text-sm text-gray-400">本地供应商库未找到匹配结果</p>
            <p className="text-xs text-gray-300">建议扩充供应商库或调整搜索条件</p>
          </div>
        )}

        {/* 历史记录 */}
        {hasHistory && (
          <div className="space-y-3">
            <h3 className="text-sm font-semibold text-[var(--color-text-secondary)]">历史寻源</h3>
            {historyQuery.data.items.slice(0, 10).map(item => (
              <div
                key={item.request_id}
                className="flex items-center justify-between bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl px-4 py-3 text-sm"
              >
                <div className="flex items-center gap-3">
                  <span className="font-medium text-[var(--color-text)]">{item.title || item.category}</span>
                  <StatusBadge status={item.status} count={item.result_count} />
                </div>
                <div className="flex items-center gap-3">
                  {item.created_at && (
                    <span className="text-xs text-gray-400">{item.created_at.slice(0, 10)}</span>
                  )}
                  <button
                    onClick={() => {
                      setCurrentRequestId(item.request_id);
                      startSearch(item.request_id);
                    }}
                    disabled={searching}
                    className="text-xs text-[var(--color-primary-bg)] hover:underline disabled:opacity-40"
                  >
                    重新搜索
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
          </div>
        </details>
      </div>
    </div>
  );
}

function StatusBadge({ status, count }: { status: string; count: number }) {
  if (status === 'done') return <span className="text-xs text-green-600 bg-green-50 px-2 py-0.5 rounded-full">{count} 个结果</span>;
  if (status === 'searching') return <span className="text-xs text-blue-500 animate-pulse">搜索中</span>;
  if (status === 'draft') return <span className="text-xs text-gray-400">草稿</span>;
  return <span className="text-xs text-gray-400">{status}</span>;
}

function SourcingResultCard({ result, watched, onWatch }: {
  result: SourcingResultItem;
  watched: boolean;
  onWatch: () => void;
}) {
  const color = getRiskColor(result.risk_score ?? 50);
  const matchPct = (result.match_score * 100).toFixed(0);
  const rankPct = (result.final_rank * 100).toFixed(0);
  const isHistoricalCandidate = result.source === 'internal_supplier_material_list';

  return (
    <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-4 shadow-sm">
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-2">
            <h4 className="font-semibold text-[var(--color-text)]">{result.supplier_name}</h4>
            {isHistoricalCandidate && <span className="text-xs px-2 py-0.5 rounded-full bg-blue-50 text-blue-700">历史合作候选</span>}
            {result.risk_level && result.risk_level !== 'unknown' && (
              <span className="text-xs px-2 py-0.5 rounded-full text-white" style={{ backgroundColor: color }}>
                {getRiskLevelLabel(result.risk_level)}
              </span>
            )}
            {watched && <span className="text-xs text-blue-500 bg-blue-50 px-1.5 py-0.5 rounded-full">已监控</span>}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--color-text-muted)]">
            <span title="本地供应商关键词匹配度">
              匹配 {matchPct}%
            </span>
            <span title="综合风险评分" style={{ color }}>
              风险 {result.risk_score ?? '—'}分
            </span>
            <span title="综合推荐度（匹配+风险加权）">
              推荐 {rankPct}%
            </span>
          </div>
          {(result.categories?.length || result.capabilities?.length) && (
            <div className="mt-2 text-xs text-[var(--color-text-muted)] space-y-1">
              {result.categories?.length ? <p>主营品类：{result.categories.join('、')}</p> : null}
              {result.capabilities?.length ? <p>供货能力：{result.capabilities.map(item => String(item.product_name || item.category || '')).filter(Boolean).join('、')}</p> : null}
            </div>
          )}
          {isHistoricalCandidate && result.match_reason && <p className="mt-2 text-xs text-blue-700">历史供货依据：{result.match_reason}</p>}
          {(result.website_url || result.contact_person || result.contact_phone || result.contact_email) && (
            <div className="mt-2 text-xs text-[var(--color-text-muted)] space-y-1">
              {result.website_url ? <a href={result.website_url} target="_blank" rel="noreferrer" className="block w-fit text-[var(--color-primary-bg)] hover:underline">官网</a> : null}
              {result.contact_person ? <p>联系人：{result.contact_person}</p> : null}
              {result.contact_phone ? <p>电话：{result.contact_phone}</p> : null}
              {result.contact_email ? <a href={`mailto:${result.contact_email}`} className="block w-fit text-[var(--color-primary-bg)] hover:underline">邮箱：{result.contact_email}</a> : null}
            </div>
          )}
        </div>

        <div className="flex gap-2 shrink-0 ml-4">
          {watched ? (
            <span className="text-xs text-blue-400 border border-blue-200 rounded-lg px-3 py-1.5">✓ 已监控</span>
          ) : (
            <button
              onClick={onWatch}
              className="text-xs border border-[var(--color-border)] rounded-lg px-3 py-1.5 hover:bg-[var(--color-surface-hover)] transition-colors"
            >
              加入监控
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
