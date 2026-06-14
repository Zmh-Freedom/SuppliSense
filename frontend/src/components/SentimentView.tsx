import { useState, useEffect, useCallback } from 'react';
import { api } from '../api';

interface RiskTag {
  tag: string;
  count: number;
}

interface Article {
  title: string;
  body?: string;
  source?: string;
  url?: string;
  date?: string;
  sentiment: string;
  confidence: number;
  risk_tags: string[];
  summary: string;
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
  articles: Article[];
  summary: string;
  key_concerns: string[];
  has_data: boolean;
}

const SENTIMENT_COLORS: Record<string, string> = {
  negative: '#dc2626',
  neutral: '#6b7280',
  positive: '#16a34a',
};

const SENTIMENT_BG: Record<string, string> = {
  negative: '#fef2f2',
  neutral: '#f9fafb',
  positive: '#ecfdf5',
};

const SENTIMENT_LABEL: Record<string, string> = {
  negative: '负面',
  neutral: '中性',
  positive: '正面',
};

export default function SentimentView() {
  const [companies, setCompanies] = useState<string[]>([]);
  const [selected, setSelected] = useState<string>('');
  const [detail, setDetail] = useState<CompanySentiment | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<string>('all');
  const [listError, setListError] = useState(false);

  const loadCompanies = useCallback((signal?: AbortSignal) => {
    setListError(false);
    api.get<{ companies: string[] }>('/alert/watchlist', undefined, signal)
      .then(d => setCompanies(d.companies || []))
      .catch((err) => { if (err.name !== 'AbortError') setListError(true); });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    loadCompanies(controller.signal);
    return () => controller.abort();
  }, [loadCompanies]);

  const selectCompany = async (name: string) => {
    setSelected(name);
    setLoading(true);
    try {
      const d = await api.get<CompanySentiment>(`/sentiment/${encodeURIComponent(name)}`);
      setDetail(d);
    } catch {
      setDetail(null);
    }
    setLoading(false);
  };

  const onRefresh = async (name: string) => {
    setLoading(true);
    try {
      const d = await api.post<CompanySentiment>('/sentiment/analyze', {
        company_name: name,
        force_refresh: true,
      });
      setDetail(d);
    } catch {
      // 刷新失败，保持旧数据
    } finally {
      setLoading(false);
    }
  };

  const filteredArticles = (detail?.articles || []).filter(a =>
    filter === 'all' ? true : a.sentiment === filter
  );

  const scoreColor =
    (detail?.sentiment_score ?? 0) < -0.2 ? '#dc2626' :
    (detail?.sentiment_score ?? 0) > 0.2 ? '#16a34a' : '#6b7280';

  return (
    <div className="flex h-full">
      {/* left: company list */}
      <div className="w-72 border-r border-[#e8e8e3] bg-[#fafaf8] overflow-y-auto shrink-0">
        <div className="px-4 py-3 border-b border-[#e8e8e3]">
          <h2 className="text-sm font-semibold text-[#333]">舆情监控</h2>
          <p className="text-[11px] text-gray-400 mt-0.5">{companies.length} 家监控企业</p>
        </div>
        <div className="py-1">
          {listError ? (
            <p className="text-xs text-red-400 text-center py-4">加载失败</p>
          ) : companies.length === 0 ? (
            <p className="text-xs text-gray-400 text-center py-4">暂无监控企业</p>
          ) : (
            companies.map(name => (
              <button
                key={name}
                onClick={() => selectCompany(name)}
                className={`w-full text-left px-4 py-2.5 text-sm transition-colors ${
                  selected === name
                    ? 'bg-[#e8e8e3] text-[#333] font-medium'
                    : 'text-[#555] hover:bg-[#eee]'
                }`}
              >
                {name}
              </button>
            ))
          )}
        </div>
      </div>

      {/* right: detail */}
      <div className="flex-1 overflow-y-auto">
        {!selected ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            选择左侧企业查看舆情详情
          </div>
        ) : loading ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            加载中…
          </div>
        ) : !detail ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            加载失败
          </div>
        ) : (
          <div className="p-6 max-w-3xl">
            {/* header */}
            <div className="flex items-center justify-between mb-6">
              <div>
                <h2 className="text-lg font-semibold text-[#333]">{selected}</h2>
                <p className="text-xs text-gray-400 mt-0.5">
                  分析时间：{detail.analyzed_at?.slice(0, 16).replace('T', ' ') || '-'}
                </p>
              </div>
              <button
                onClick={() => onRefresh(selected)}
                disabled={loading}
                className="text-xs bg-[#333] text-white rounded-lg px-3 py-1.5 hover:bg-[#555] disabled:opacity-50 transition-colors"
              >
                {loading ? '分析中…' : detail.has_data ? '刷新分析' : '开始分析'}
              </button>
            </div>

            {/* summary card */}
            <div className="grid grid-cols-5 gap-3 mb-6">
              <div className="bg-white border border-[#e8e8e3] rounded-xl p-4 text-center">
                <div className="text-2xl font-bold" style={{ color: scoreColor }}>
                  {detail.has_data ? (detail.sentiment_score > 0 ? '+' : '') + detail.sentiment_score.toFixed(2) : '—'}
                </div>
                <div className="text-[11px] text-gray-400 mt-1">情感得分</div>
              </div>
              <div className="bg-red-50 rounded-xl p-4 text-center">
                <div className="text-2xl font-bold text-red-600">{detail.negative_count}</div>
                <div className="text-[11px] text-red-400 mt-1">负面</div>
              </div>
              <div className="bg-gray-50 rounded-xl p-4 text-center">
                <div className="text-2xl font-bold text-gray-600">{detail.neutral_count}</div>
                <div className="text-[11px] text-gray-400 mt-1">中性</div>
              </div>
              <div className="bg-green-50 rounded-xl p-4 text-center">
                <div className="text-2xl font-bold text-green-600">{detail.positive_count}</div>
                <div className="text-[11px] text-green-400 mt-1">正面</div>
              </div>
              <div className="bg-white border border-[#e8e8e3] rounded-xl p-4 text-center">
                <div className="text-2xl font-bold text-[#333]">{detail.articles_count}</div>
                <div className="text-[11px] text-gray-400 mt-1">总计</div>
              </div>
            </div>

            {/* AI summary */}
            {detail.summary && (
              <div className="bg-amber-50 border border-amber-100 rounded-xl p-4 mb-6">
                <div className="text-xs font-semibold text-amber-700 mb-1">🤖 AI 分析</div>
                <p className="text-sm text-amber-800 leading-relaxed">{detail.summary}</p>
                {detail.key_concerns?.length > 0 && (
                  <ul className="mt-2 space-y-0.5">
                    {detail.key_concerns.map((c, i) => (
                      <li key={i} className="text-xs text-amber-700 flex gap-1">
                        <span>•</span> {c}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {/* risk tags */}
            {detail.risk_tags.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-6">
                {detail.risk_tags.map(t => (
                  <span key={t.tag} className="text-xs px-2.5 py-1 rounded-full bg-red-50 text-red-600 border border-red-100">
                    {t.tag} ×{t.count}
                  </span>
                ))}
              </div>
            )}

            {/* filter tabs */}
            <div className="flex items-center gap-2 mb-4">
              <span className="text-sm font-medium text-[#555]">新闻列表</span>
              <span className="text-xs text-gray-400">({filteredArticles.length}篇)</span>
              <div className="flex-1" />
              {['all', 'negative', 'neutral', 'positive'].map(f => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={`text-xs px-3 py-1 rounded-full transition-colors ${
                    filter === f
                      ? 'bg-[#333] text-white'
                      : 'text-gray-500 hover:bg-gray-100'
                  }`}
                >
                  {f === 'all' ? '全部' : SENTIMENT_LABEL[f]}
                </button>
              ))}
            </div>

            {/* articles list */}
            {filteredArticles.length === 0 ? (
              <p className="text-sm text-gray-400 text-center py-8">
                {detail.has_data ? '无匹配新闻' : '暂无舆情数据，请点击「开始分析」获取'}
              </p>
            ) : (
              <div className="space-y-2">
                {filteredArticles.map((a, i) => {
                  const s = a.sentiment || 'neutral';
                  return (
                    <div
                      key={i}
                      className="bg-white border border-[#e8e8e3] rounded-xl p-4 hover:border-[#ccc] transition-colors"
                    >
                      <div className="flex items-start gap-3">
                        {/* sentiment dot */}
                        <span
                          className="w-2 h-2 rounded-full mt-1.5 shrink-0"
                          style={{ background: SENTIMENT_COLORS[s] || '#999' }}
                          title={SENTIMENT_LABEL[s]}
                        />

                        <div className="flex-1 min-w-0">
                          {/* title */}
                          {a.url ? (
                            <a
                              href={a.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-sm text-[#333] hover:text-blue-600 leading-relaxed"
                            >
                              {a.title}
                            </a>
                          ) : (
                            <span className="text-sm text-[#333] leading-relaxed">{a.title}</span>
                          )}

                          {/* meta */}
                          <div className="flex items-center gap-2 mt-1.5">
                            {a.source && (
                              <span className="text-[11px] text-gray-400">{a.source}</span>
                            )}
                            {a.date && (
                              <span className="text-[11px] text-gray-400">{a.date?.slice(0, 10)}</span>
                            )}
                            <span
                              className="text-[10px] px-1.5 py-0.5 rounded"
                              style={{
                                background: SENTIMENT_BG[s] || '#f5f5f5',
                                color: SENTIMENT_COLORS[s] || '#999',
                              }}
                            >
                              {SENTIMENT_LABEL[s]}
                              {a.confidence > 0 && a.confidence < 1 && ` ${(a.confidence * 100).toFixed(0)}%`}
                            </span>
                          </div>

                          {/* summary */}
                          {a.summary && (
                            <p className="text-xs text-gray-500 mt-1 leading-relaxed">{a.summary}</p>
                          )}

                          {/* risk tags */}
                          {a.risk_tags.length > 0 && (
                            <div className="flex flex-wrap gap-1 mt-2">
                              {a.risk_tags.map(tag => (
                                <span
                                  key={tag}
                                  className="text-[10px] px-1.5 py-0.5 rounded bg-red-50 text-red-500 border border-red-100"
                                >
                                  {tag}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
