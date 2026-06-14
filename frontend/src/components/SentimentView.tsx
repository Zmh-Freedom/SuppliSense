import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../api';
import { wsClient } from '../websocket';

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
  negative: '#dc2626', neutral: '#6b7280', positive: '#16a34a',
};
const SENTIMENT_LABEL: Record<string, string> = {
  negative: '负面', neutral: '中性', positive: '正面',
};

export default function SentimentView() {
  const [companies, setCompanies] = useState<string[]>([]);
  const [selected, setSelected] = useState<string>('');
  const [detail, setDetail] = useState<CompanySentiment | null>(null);
  const [loading, setLoading] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [filter, setFilter] = useState<string>('all');
  const [listError, setListError] = useState(false);
  const [reqError, setReqError] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadDetail = useCallback(async (name: string) => {
    try {
      const d = await api.get<CompanySentiment & { analyzing?: boolean }>(`/sentiment/${encodeURIComponent(name)}`);
      if ((d as any).analyzing) {
        setAnalyzing(true);
      } else {
        setDetail(d);
        setAnalyzing(false);
        setReqError('');
        stopPoll();
      }
    } catch {
      setAnalyzing(false);
    }
  }, []);

  const stopPoll = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  };

  // Poll while analyzing
  useEffect(() => {
    if (!analyzing || !selected) return;
    pollRef.current = setInterval(() => loadDetail(selected), 3000);
    return () => stopPoll();
  }, [analyzing, selected, loadDetail]);

  // WebSocket
  useEffect(() => {
    const unsub = wsClient.on('sentiment_ready', (data: { company_name: string }) => {
      if (data.company_name === selected) loadDetail(selected);
    });
    return () => unsub();
  }, [selected, loadDetail]);

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
    setDetail(null);
    setAnalyzing(false);
    setReqError('');
    setLoading(true);
    stopPoll();
    try {
      const d = await api.get<CompanySentiment & { analyzing?: boolean }>(`/sentiment/${encodeURIComponent(name)}`);
      if ((d as any).analyzing) {
        setAnalyzing(true);
      } else {
        setDetail(d);
      }
    } catch {
      setDetail(null);
    }
    setLoading(false);
  };

  const onRefresh = async (name: string) => {
    setLoading(true);
    setReqError('');
    setAnalyzing(true);
    stopPoll();
    try {
      const d = await api.post<any>('/sentiment/analyze', {
        company_name: name,
        force_refresh: true,
      });
      if (d.analyzing) {
        setAnalyzing(true);
        if (d.has_data && d.articles) {
          setDetail(d);
        }
      } else if (d.has_data) {
        setDetail(d);
        setAnalyzing(false);
      } else {
        setReqError('分析请求已发送，请稍候刷新');
      }
    } catch (e: any) {
      setReqError(e.message || '请求失败，请确保后端服务正常运行');
      setAnalyzing(false);
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

  const isWorking = loading || analyzing;

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
          ) : (
            companies.map(name => (
              <button key={name} onClick={() => selectCompany(name)}
                className={`w-full text-left px-4 py-2.5 text-sm ${selected === name ? 'bg-[#e8e8e3] font-medium' : 'hover:bg-[#eee]'}`}>
                {name.slice(0, 16)}
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
        ) : isWorking ? (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <div className="w-8 h-8 border-2 border-[#333] border-t-transparent rounded-full animate-spin" />
            <p className="text-sm text-gray-500">
              {analyzing ? '正在搜索新闻并分析舆情，预计 15-20 秒…' : '加载中…'}
            </p>
            <p className="text-xs text-gray-400">{selected}</p>
          </div>
        ) : !detail ? (
          <div className="flex flex-col items-center justify-center h-full gap-4">
            <p className="text-sm text-gray-400">{reqError || '暂无舆情数据'}</p>
            <button
              onClick={() => onRefresh(selected)}
              disabled={isWorking}
              className="text-sm bg-[#333] text-white rounded-lg px-5 py-2 hover:bg-[#555] disabled:opacity-60 inline-flex items-center gap-2"
            >
              {isWorking && <span className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />}
              {isWorking ? '分析中…' : '开始分析'}
            </button>
            <p className="text-xs text-gray-300">通过 DuckDuckGo 搜索新闻 + AI 情感分析</p>
          </div>
        ) : (
          <div className="p-6 max-w-3xl">
            {/* header */}
            <div className="flex items-center justify-between mb-6">
              <div>
                <h2 className="text-lg font-semibold text-[#333]">{selected}</h2>
                <p className="text-xs text-gray-400 mt-0.5">
                  分析时间：{detail.analyzed_at?.slice(0, 16).replace('T', ' ') || '-'}
                  {analyzing && <span className="ml-2 text-blue-500">● 刷新中</span>}
                </p>
              </div>
              <button
                onClick={() => onRefresh(selected)}
                disabled={isWorking}
                className="text-xs bg-[#333] text-white rounded-lg px-3 py-1.5 hover:bg-[#555] disabled:opacity-60 transition-colors inline-flex items-center gap-1.5"
              >
                {isWorking && <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />}
                {isWorking ? '分析中…' : detail.has_data ? '刷新分析' : '开始分析'}
              </button>
            </div>

            {reqError && (
              <div className="bg-red-50 border border-red-100 rounded-lg px-4 py-3 mb-4 text-sm text-red-600">{reqError}</div>
            )}

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
                      <li key={i} className="text-xs text-amber-700 flex gap-1"><span>•</span> {c}</li>
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
                <button key={f} onClick={() => setFilter(f)}
                  className={`text-xs px-3 py-1 rounded-full transition-colors ${filter === f ? 'bg-[#333] text-white' : 'text-gray-500 hover:bg-gray-100'}`}>
                  {f === 'all' ? '全部' : SENTIMENT_LABEL[f]}
                </button>
              ))}
            </div>

            {/* articles list */}
            {!detail.has_data ? (
              <p className="text-sm text-gray-400 text-center py-8">暂无舆情数据，请点击「开始分析」获取</p>
            ) : filteredArticles.length === 0 ? (
              <div className="text-center py-8 space-y-1">
                <p className="text-sm text-gray-400">未搜索到相关新闻</p>
                <p className="text-xs text-gray-300">DuckDuckGo 未找到「{selected}」的近期新闻</p>
                <button onClick={() => onRefresh(selected)} disabled={isWorking}
                  className="text-xs text-blue-500 hover:text-blue-600 disabled:opacity-50 mt-2">
                  重新搜索
                </button>
              </div>
            ) : (
              <div className="space-y-2">
                {filteredArticles.map((a, i) => {
                  const s = a.sentiment || 'neutral';
                  return (
                    <div key={i} className="bg-white border border-[#e8e8e3] rounded-xl p-4 hover:border-[#ccc] transition-colors">
                      <div className="flex items-start gap-3">
                        <span className="w-2 h-2 rounded-full mt-1.5 shrink-0" style={{ background: SENTIMENT_COLORS[s] || '#999' }} />
                        <div className="flex-1 min-w-0">
                          {a.url ? (
                            <a href={a.url} target="_blank" rel="noopener noreferrer"
                              className="text-sm text-[#333] hover:text-blue-500 transition-colors line-clamp-2">
                              {a.title}
                            </a>
                          ) : (
                            <p className="text-sm text-[#333] line-clamp-2">{a.title}</p>
                          )}
                          <div className="flex items-center gap-2 mt-1">
                            <span className="text-[10px] text-gray-400">{a.source || a.date || '-'}</span>
                            <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: SENTIMENT_COLORS[s] + '18', color: SENTIMENT_COLORS[s] }}>
                              {SENTIMENT_LABEL[s]}
                            </span>
                            {a.summary && <span className="text-[10px] text-gray-400 truncate">{a.summary}</span>}
                          </div>
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
