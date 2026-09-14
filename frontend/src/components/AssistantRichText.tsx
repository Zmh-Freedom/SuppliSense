import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ChartRenderer from './ChartRenderer';

const markdownComponents: Components = {
  h1: ({ children }) => <h1 className="mb-3 mt-0 text-lg font-semibold tracking-tight text-[var(--color-text)]">{children}</h1>,
  h2: ({ children }) => <h2 className="mb-2 mt-5 border-b border-[var(--color-border)] pb-2 text-base font-semibold text-[var(--color-text)] first:mt-0">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-1.5 mt-4 text-sm font-semibold text-[var(--color-text)] first:mt-0">{children}</h3>,
  p: ({ children }) => <p className="my-0 text-sm leading-7 text-[var(--color-text)] [&+p]:mt-4">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-[var(--color-text)]">{children}</strong>,
  em: ({ children }) => <em className="text-[var(--color-text-secondary)]">{children}</em>,
  ul: ({ children }) => <ul className="my-3 list-disc space-y-1.5 pl-5 text-sm leading-6 text-[var(--color-text)]">{children}</ul>,
  ol: ({ children }) => <ol className="my-3 list-decimal space-y-1.5 pl-5 text-sm leading-6 text-[var(--color-text)]">{children}</ol>,
  li: ({ children }) => <li className="pl-1 leading-6">{children}</li>,
  blockquote: ({ children }) => <blockquote className="my-4 border-l-4 border-[var(--color-primary-bg)]/30 bg-[var(--color-code-bg)]/60 px-4 py-3 text-sm leading-6 text-[var(--color-text-secondary)]">{children}</blockquote>,
  hr: () => <hr className="my-5 border-[var(--color-border)]" />,
  a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer" className="font-medium text-[var(--color-primary-bg)] underline decoration-[var(--color-primary-bg)]/30 underline-offset-2 transition-colors hover:text-[var(--color-primary-hover)] hover:decoration-current">{children}</a>,
  table: ({ children }) => <div className="my-4 overflow-x-auto rounded-xl border border-[var(--color-border)]"><table className="w-full min-w-[520px] border-collapse text-left text-sm">{children}</table></div>,
  thead: ({ children }) => <thead className="bg-[var(--color-code-bg)]/70 text-xs text-[var(--color-text-secondary)]">{children}</thead>,
  th: ({ children }) => <th className="border-b border-[var(--color-border)] px-3 py-2.5 font-semibold">{children}</th>,
  td: ({ children }) => <td className="border-b border-[var(--color-border)] px-3 py-2.5 align-top leading-6 text-[var(--color-text)] last:border-b-0">{children}</td>,
  code: ({ className, children, ...rest }) => {
    if (className === 'language-chart') {
      try {
        return <ChartRenderer data={JSON.parse(String(children).replace(/\n/g, ''))} />;
      } catch {
        return <code className={className} {...rest}>{children}</code>;
      }
    }
    return <code className={`${className || ''} rounded-md bg-[var(--color-code-bg)] px-1.5 py-0.5 text-[0.85em] text-[var(--color-text)]`} {...rest}>{children}</code>;
  },
  pre: ({ children }) => <pre className="my-4 overflow-x-auto rounded-xl bg-[var(--color-code-bg)] p-3 text-xs leading-5 text-[var(--color-text)]">{children}</pre>,
};

export default function AssistantRichText({ content, label }: { content: string; label?: string }) {
  return <div className="assistant-rich-text">
    {label && <div className="mb-3 flex flex-wrap items-center gap-2 border-b border-[var(--color-border)] pb-2.5">
      <span className="rounded-full bg-[var(--color-primary-bg)]/10 px-2.5 py-1 text-[11px] font-semibold tracking-wide text-[var(--color-primary-bg)]">{label}</span>
      <span className="text-[11px] text-[var(--color-text-muted)]">基于已核验数据生成</span>
    </div>}
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{content}</ReactMarkdown>
  </div>;
}
