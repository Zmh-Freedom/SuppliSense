import type { AgentEvidenceRecord } from '../types';

interface SentimentArticle {
  title?: string;
  body?: string;
  url?: string;
  source?: string;
  source_name?: string;
  published_at?: string | null;
  date?: string;
  sentiment?: string;
  confidence?: number;
  risk_tags?: string[];
  summary?: string;
  judgement_basis?: string;
  detail_fetched?: boolean;
}

function articlesFromEvidence(evidence: AgentEvidenceRecord[]): SentimentArticle[] {
  const seen = new Set<string>();
  const articles: SentimentArticle[] = [];
  evidence.forEach(record => {
    if (record.dimension !== 'sentiment') return;
    const values = record.facts?.articles;
    if (!Array.isArray(values)) return;
    values.forEach(value => {
      if (!value || typeof value !== 'object') return;
      const article = value as SentimentArticle;
      const key = article.url || article.title || '';
      if (!key || seen.has(key)) return;
      seen.add(key);
      articles.push(article);
    });
  });
  return articles;
}

function sentimentMeta(value: string | undefined): { label: string; color: string } {
  if (value === 'negative') return { label: '负面', color: '#dc2626' };
  if (value === 'positive') return { label: '正面', color: '#16a34a' };
  return { label: '中性', color: '#6b7280' };
}

export default function SentimentEvidence({ evidence }: { evidence: AgentEvidenceRecord[] }) {
  const articles = articlesFromEvidence(evidence);
  if (articles.length === 0) return null;
  const hasFallback = evidence.some(record => record.dimension === 'sentiment' && record.facts?.llm_analyzed === false);

  return (
    <section className="mt-4 rounded-xl border border-amber-200 bg-amber-50/40 p-4" aria-label="逐条舆情新闻核验">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="font-semibold text-amber-950">逐条舆情新闻核验</h4>
          <p className="mt-1 text-xs text-amber-900/70">每条报道均保留原文链接，并展示{hasFallback ? '当前可用的规则结果和判断边界' : ' LLM 判断和判断依据'}。</p>
        </div>
        <span className="text-xs text-amber-900/70">{articles.length} 条报道</span>
      </div>
      <div className="mt-3 space-y-3">
        {articles.slice(0, 10).map((article, index) => {
          const meta = sentimentMeta(article.sentiment);
          const source = article.source_name || article.source || '公开来源';
          const date = article.published_at || article.date || '';
          return (
            <article key={article.url || `${article.title}-${index}`} className="rounded-xl border border-amber-200/80 bg-white/80 p-3 text-sm">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  {article.url ? (
                    <a href={article.url} target="_blank" rel="noreferrer" className="font-medium text-blue-700 hover:underline">
                      {article.title || '未命名报道'}
                    </a>
                  ) : <span className="font-medium text-gray-800">{article.title || '未命名报道'}</span>}
                  <div className="mt-1 text-[10px] text-gray-400">{source}{date ? ` · ${date.slice(0, 10)}` : ''}{article.detail_fetched ? ' · 已抓取详情' : ' · 列表摘要'}</div>
                </div>
                <span className="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium" style={{ color: meta.color, background: `${meta.color}14` }}>{meta.label}</span>
              </div>
              <details className="mt-2 rounded-lg bg-amber-50/60 px-2.5 py-2">
                <summary className="cursor-pointer text-xs font-medium text-amber-900">新闻原文</summary>
                <p className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-gray-700">{article.body || '当前来源未提供正文，仅保留标题。'}</p>
              </details>
              <div className="mt-2 grid gap-1 text-xs leading-5">
                <div><span className="font-medium text-gray-500">{hasFallback ? '当前判断：' : 'LLM 判断：'}</span>{article.summary || '未生成单篇摘要'}</div>
                <div><span className="font-medium text-gray-500">判断依据：</span>{article.judgement_basis || '基于新闻标题及已抓取正文内容判断。'}</div>
                {article.risk_tags && article.risk_tags.length > 0 && <div><span className="font-medium text-gray-500">风险标签：</span><span className="text-red-600">{article.risk_tags.join(' · ')}</span></div>}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
