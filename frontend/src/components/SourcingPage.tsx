import { useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../query-keys';
import { getRiskColor } from '../riskColors';
import type { SourcingResultItem, SourcingRequestDetail } from '../types';

type Step = { label: string; done: boolean };

export default function SourcingPage() {
  const qc = useQueryClient();
  const [form, setForm] = useState({ title: '', category: '', spec: '', region: '', quantity: '' });
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<SourcingResultItem[]>([]);
  const [error, setError] = useState('');
  const [currentRequestId, setCurrentRequestId] = useState('');
  const [steps, setSteps] = useState<Step[]>([]);
  const [selectMsg, setSelectMsg] = useState('');

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
    setSteps([{ label: '检索中', done: false }, { label: '评估中', done: false }, { label: '排序中', done: false }]);

    const token = localStorage.getItem('token') || '';
    try {
      const res = await fetch(`/api/v1/sourcing/requests/${requestId}/search`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });

      const reader = res.body?.getReader();
      if (!reader) throw new Error('No stream');

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        let eventType = '';
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
              } else if (eventType === 'sourcing_result' && data.results) {
                setResults(data.results);
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
      setResults(prev => prev.map(r =>
        r.result_id === vars.resultId ? { ...r, selected: true } : r
      ));
      setSelectMsg(vars.action === 'watchlist' ? '已加入监控列表' : '已提交准入申请');
      setTimeout(() => setSelectMsg(''), 3000);
    },
  });

  const canSubmit = form.category && form.spec;
  const hasResults = results.length > 0;
  const hasHistory = historyQuery.data && historyQuery.data.items.length > 0;
  const showEmpty = !searching && !error && currentRequestId && !hasResults;

  return (
    <div className="h-full py-6 px-6 overflow-auto">
      <div className="max-w-3xl mx-auto space-y-6">
        <h2 className="text-lg font-bold text-[var(--color-text)]">智能寻源</h2>

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
                onSelect={action => selectMutation.mutate({ resultId: r.result_id, action })}
              />
            ))}
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
    </div>
  );
}

function StatusBadge({ status, count }: { status: string; count: number }) {
  if (status === 'done') return <span className="text-xs text-green-600 bg-green-50 px-2 py-0.5 rounded-full">{count} 个结果</span>;
  if (status === 'searching') return <span className="text-xs text-blue-500 animate-pulse">搜索中</span>;
  if (status === 'draft') return <span className="text-xs text-gray-400">草稿</span>;
  return <span className="text-xs text-gray-400">{status}</span>;
}

function SourcingResultCard({ result, onSelect }: {
  result: SourcingResultItem;
  onSelect: (action: string) => void;
}) {
  const color = getRiskColor(result.risk_score ?? 50);
  const matchPct = (result.match_score * 100).toFixed(0);
  const rankPct = (result.final_rank * 100).toFixed(0);

  return (
    <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-4 shadow-sm">
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-2">
            <h4 className="font-semibold text-[var(--color-text)]">{result.supplier_name}</h4>
            {result.risk_level && result.risk_level !== 'unknown' && (
              <span className="text-xs px-2 py-0.5 rounded-full text-white" style={{ backgroundColor: color }}>
                {result.risk_level}
              </span>
            )}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--color-text-muted)]">
            <span title="向量语义匹配度">
              匹配 {matchPct}%
            </span>
            <span title="综合风险评分" style={{ color }}>
              风险 {result.risk_score ?? '—'}分
            </span>
            <span title="综合推荐度（匹配+风险加权）">
              推荐 {rankPct}%
            </span>
          </div>
        </div>

        {result.selected ? (
          <span className="text-xs text-green-600 bg-green-50 px-3 py-1.5 rounded-lg">
            {result.action === 'watchlist' ? '已加入监控' : '已申请准入'}
          </span>
        ) : (
          <div className="flex gap-2 shrink-0 ml-4">
            <button
              onClick={() => onSelect('watchlist')}
              className="text-xs border border-[var(--color-border)] rounded-lg px-3 py-1.5 hover:bg-[var(--color-surface-hover)] transition-colors"
            >
              加入监控
            </button>
            <button
              onClick={() => onSelect('apply_access')}
              className="text-xs bg-[var(--color-primary-bg)] text-white rounded-lg px-3 py-1.5 hover:bg-[var(--color-primary-hover)] transition-colors"
            >
              申请准入
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
