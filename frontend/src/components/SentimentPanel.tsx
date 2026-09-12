import { useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { wsClient } from '../websocket';
import { queryKeys } from '../query-keys';

interface RiskTag {
  tag: string;
  count: number;
}

interface SentimentItem {
  company_name: string;
  sentiment_score: number;
  negative_count: number;
  articles_count: number;
  top_risk_tags: string[];
  summary: string;
  key_concerns: string[];
  analyzed_at: string | null;
  has_data: boolean;
}

interface SentimentDashboard {
  total_monitored: number;
  analyzed_count: number;
  negative_alert_count: number;
  companies: SentimentItem[];
  negative_companies: SentimentItem[];
}

interface CompanySentiment {
  company_name: string;
  analyzed_at: string;
  articles_count: number;
  negative_count: number;
  neutral_count: number;
  positive_count: number;
  sentiment_score: number;
  risk_tags: RiskTag[];
  articles: SentimentArticle[];
  summary: string;
  key_concerns: string[];
  has_data: boolean;
  llm_analyzed?: boolean;
  analysis_mode?: 'llm' | 'fallback' | 'no_data' | string;
}

interface SentimentArticle {
  title: string;
  body?: string;
  url?: string;
  original_url?: string;
  source?: string;
  source_name?: string;
  source_type?: string;
  published_at?: string | null;
  publish_time?: string;
  detail_fetched?: boolean;
  sentiment: string;
  confidence: number;
  risk_tags: string[];
  summary: string;
  judgement_basis?: string;
}

export default function SentimentPanel({ companyName, embedded }: { companyName?: string; embedded?: boolean }) {
  const queryClient = useQueryClient();

  const dashQuery = useQuery({
    queryKey: queryKeys.sentimentDashboard,
    queryFn: () => api.get<SentimentDashboard>('/sentiment/dashboard/overview'),
    enabled: !companyName,
  });

  const detailQuery = useQuery({
    queryKey: queryKeys.sentimentDetail(companyName || ''),
    queryFn: () => api.get<CompanySentiment & { analyzing?: boolean; is_stale?: boolean }>(
      `/sentiment/${encodeURIComponent(companyName!)}`
    ),
    enabled: !!companyName,
    refetchInterval: (query) => {
      const d = query.state.data;
      return (d?.analyzing) ? 3000 : false;
    },
  });

  const analyzeMutation = useMutation({
    mutationFn: () => api.post<CompanySentiment>('/sentiment/analyze', {
      company_name: companyName,
      force_refresh: true,
    }),
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: queryKeys.sentimentDetail(companyName || '') });
      const prev = queryClient.getQueryData<CompanySentiment & { analyzing?: boolean }>(
        queryKeys.sentimentDetail(companyName || '')
      );
      if (prev) {
        queryClient.setQueryData(queryKeys.sentimentDetail(companyName || ''), { ...prev, analyzing: true });
      }
      return { prev };
    },
    onError: (_err, _vars, context) => {
      if (context?.prev) {
        queryClient.setQueryData(queryKeys.sentimentDetail(companyName || ''), context.prev);
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.sentimentDetail(companyName || '') }),
  });

  const dash = dashQuery.data ?? null;
  const detail = detailQuery.data ?? null;
  const error = dashQuery.error || detailQuery.error;
  const analyzing = detail?.analyzing ?? false;
  const isRefreshing = analyzing || analyzeMutation.isPending;

  // WebSocket invalidation
  useEffect(() => {
    if (!companyName) return;
    const unsub = wsClient.on('sentiment_ready', (data: { company_name: string }) => {
      if (data.company_name === companyName) {
        queryClient.invalidateQueries({ queryKey: queryKeys.sentimentDetail(companyName) });
      }
    });
    return () => unsub();
  }, [companyName, queryClient]);

  // ---- single company detail ----
  if (companyName) {
    // 正在首次分析（无缓存）
    if (analyzing && !detail?.has_data) {
      return (
        <div className={embedded ? 'text-center' : 'bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 text-center'}>
          <div className="text-xs text-gray-400">🔄 正在分析舆情数据，请稍候…</div>
          <div className="mt-2 text-[11px] text-gray-300">首次分析需要 15-20 秒，之后会缓存</div>
        </div>
      );
    }
    if (!detail && !error) return <div className="text-xs text-gray-400 p-4">加载舆情数据…</div>;
    if (error) return <div className="text-xs text-red-400 p-4">加载失败</div>;

    const scoreColor =
      detail!.sentiment_score < -0.2 ? '#dc2626' : detail!.sentiment_score > 0.2 ? '#16a34a' : '#999';

    return (
      <div className={embedded ? 'space-y-4' : 'bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 space-y-4'}>
        <div className={`flex items-center ${embedded ? 'justify-end' : 'justify-between'}`}>
          {!embedded && <h3 className="text-sm font-medium text-[var(--color-text-secondary)]">📰 舆情分析</h3>}
          <div className="flex items-center gap-2">
            {isRefreshing && (
              <span className="text-[10px] text-blue-500 bg-blue-50 px-1.5 py-0.5 rounded animate-pulse">
                刷新中…
              </span>
            )}
            <button
              onClick={() => analyzeMutation.mutate()}
              disabled={isRefreshing}
              className="text-xs text-blue-500 hover:text-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isRefreshing ? '刷新中…' : '刷新分析'}
            </button>
          </div>
        </div>

        {!detail!.has_data ? (
          <p className="text-xs text-gray-400">暂无舆情数据，请确保已在监控列表中并执行过数据刷新。</p>
        ) : (
          <>
            {/* sentiment score */}
            <div className="flex items-center gap-4">
              <div className="text-center">
                <div className="text-3xl font-bold" style={{ color: scoreColor }}>
                  {detail!.sentiment_score > 0 ? '+' : ''}{detail!.sentiment_score.toFixed(2)}
                </div>
                <div className="text-[11px] text-gray-400 mt-1">情感得分</div>
              </div>
              <div className="flex-1 grid grid-cols-3 gap-2 text-center">
                <div className="bg-red-50 rounded-lg py-2">
                  <div className="text-lg font-bold text-red-600">{detail!.negative_count}</div>
                  <div className="text-[11px] text-red-400">负面</div>
                </div>
                <div className="bg-gray-50 rounded-lg py-2">
                  <div className="text-lg font-bold text-gray-600">{detail!.neutral_count}</div>
                  <div className="text-[11px] text-gray-400">中性</div>
                </div>
                <div className="bg-green-50 rounded-lg py-2">
                  <div className="text-lg font-bold text-green-600">{detail!.positive_count}</div>
                  <div className="text-[11px] text-gray-400">正面</div>
                </div>
              </div>
            </div>

            {/* risk tags */}
            {detail!.risk_tags.length > 0 && (
              <div>
                <div className="text-xs text-gray-500 mb-2">风险标签</div>
                <div className="flex flex-wrap gap-1.5">
                  {detail!.risk_tags.map(t => (
                    <span
                      key={t.tag}
                      className="text-[11px] px-2 py-0.5 rounded-full bg-red-50 text-red-600 border border-red-100"
                    >
                      {t.tag} ×{t.count}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* LLM summary + key concerns */}
            {detail!.summary && (
              <div className="bg-amber-50 border border-amber-100 rounded-xl p-3">
                <div className="text-xs font-medium text-amber-700 mb-1">🤖 AI 分析摘要</div>
                <p className="text-xs text-amber-800 leading-relaxed">{detail!.summary}</p>
                {detail!.key_concerns?.length > 0 && (
                  <ul className="mt-2 space-y-0.5">
                    {detail!.key_concerns.map((c, i) => (
                      <li key={i} className="text-[11px] text-amber-700 flex items-start gap-1">
                        <span className="text-amber-400">•</span> {c}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {/* articles: 原文 → LLM 判断 → 判断依据 */}
            {detail!.articles.length > 0 && (
              <div>
                <div className="flex items-center justify-between text-xs text-gray-500 mb-2">
                  <span>逐条新闻核验 ({detail!.articles.length}篇)</span>
                  <span className="text-[10px] text-gray-400">
                    {detail!.llm_analyzed === false ? '未形成 LLM 判断' : '已完成 LLM 判断'}
                  </span>
                </div>
                <div className="space-y-3 max-h-[34rem] overflow-y-auto pr-1">
                  {detail!.articles.slice(0, 10).map((a, i) => {
                    const sColor =
                      a.sentiment === 'negative'
                        ? '#dc2626'
                        : a.sentiment === 'positive'
                        ? '#16a34a'
                        : '#999';
                    const source = a.source_name || a.source || '公开来源';
                    const publishedAt = a.published_at || a.publish_time || '';
                    const originalUrl = a.original_url || a.url;
                    return (
                      <article key={a.url || `${a.title}-${i}`} className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-muted)] p-3 space-y-2">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            {originalUrl ? (
                              <a href={originalUrl} target="_blank" rel="noreferrer" className="text-sm font-medium text-blue-700 hover:text-blue-800 hover:underline leading-relaxed">
                                {a.title}
                              </a>
                            ) : (
                              <div className="text-sm font-medium text-gray-700 leading-relaxed">{a.title}</div>
                            )}
                            <div className="mt-1 text-[10px] text-gray-400">
                              {source}{publishedAt ? ` · ${publishedAt.slice(0, 10)}` : ''}
                              {a.detail_fetched ? ' · 已抓取详情' : ' · 列表摘要'}
                            </div>
                          </div>
                          <span className="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium" style={{ color: sColor, background: `${sColor}14` }}>
                            {a.sentiment === 'negative' ? '负面' : a.sentiment === 'positive' ? '正面' : '中性'}
                          </span>
                        </div>

                        <details className="rounded-lg bg-white/70 px-2.5 py-2">
                          <summary className="cursor-pointer text-[11px] font-medium text-gray-600">新闻原文</summary>
                          <p className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap text-[11px] leading-relaxed text-gray-600">
                            {a.body || '当前来源未提供正文，仅保留标题。'}
                          </p>
                        </details>

                        <div className="grid gap-1 text-[11px] leading-relaxed">
                          <div><span className="font-medium text-gray-500">LLM 判断：</span><span className="text-gray-700">{a.summary || '未生成单篇摘要'}</span></div>
                          <div><span className="font-medium text-gray-500">判断依据：</span><span className="text-gray-600">{a.judgement_basis || '基于新闻标题及已抓取正文内容判断。'}</span></div>
                          {a.risk_tags.length > 0 && <div><span className="font-medium text-gray-500">风险标签：</span><span className="text-red-500">{a.risk_tags.join(' · ')}</span></div>}
                        </div>
                      </article>
                    );
                  })}
                </div>
              </div>
            )}

            <div className="text-[11px] text-gray-400 text-right">
              分析时间：{detail!.analyzed_at?.slice(0, 16).replace('T', ' ') || '-'}
            </div>
          </>
        )}
      </div>
    );
  }

  // ---- dashboard overview ----
  if (!dash && !error) return <div className="text-xs text-gray-400 p-4">加载舆情总览…</div>;
  if (error) return <div className="text-xs text-red-400 p-4">舆情加载失败</div>;

  return (
    <div className={embedded ? '' : 'bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5'}>
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-[var(--color-text-secondary)]">
          📰 舆情监控
          <span className="text-xs text-gray-400 ml-2">
            {dash!.analyzed_count}/{dash!.total_monitored} 家已分析
          </span>
        </h3>
        {dash!.negative_alert_count > 0 && (
          <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-red-50 text-red-600">
            {dash!.negative_alert_count} 家负面舆情
          </span>
        )}
      </div>

      {dash!.companies.length === 0 ? (
        <p className="text-xs text-gray-400 py-4 text-center">暂无监控企业</p>
      ) : dash!.negative_companies.length === 0 ? (
        <p className="text-xs text-gray-400 py-4 text-center">舆情正常 ✅</p>
      ) : (
        <div className="space-y-1.5">
          {dash!.negative_companies.map(c => {
            const barPct = Math.min(Math.abs(c.sentiment_score) * 100, 100);
            return (
              <div
                key={c.company_name}
                className="flex items-center gap-3 px-3 py-2 rounded-xl hover:bg-[var(--color-surface-hover)] transition-colors"
              >
                <span className="text-sm text-[var(--color-text)] flex-1 truncate">{c.company_name}</span>
                <span className="text-[11px] text-red-500">
                  {c.articles_count} 条新闻
                </span>
                <div className="w-20 h-1.5 rounded-full bg-gray-100 shrink-0">
                  <div
                    className="h-full rounded-full bg-red-400 transition-all"
                    style={{ width: `${barPct}%` }}
                  />
                </div>
                <span className="text-xs font-mono font-medium text-red-500 w-10 text-right">
                  {c.sentiment_score.toFixed(2)}
                </span>
                {c.top_risk_tags.length > 0 && (
                  <span className="text-[10px] text-gray-400 hidden lg:block">
                    {c.top_risk_tags.slice(0, 2).join(' · ')}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
