import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ComponentPropsWithoutRef, ComponentType } from 'react';
import type { ApprovalData } from '../api';
import type { AgentAnswer, AgentEvidenceRecord, AgentWorkflowSnapshot, ChartData, ChatMessage, SupplierReference } from '../types';
import ChartRenderer from './ChartRenderer';
import AgentWorkflowPanel from './AgentWorkflowPanel';
import type { AgentStatus } from './AgentWorkflowPanel';

export interface ChatStreamViewState {
  thinking: string;
  plan: Array<{ tool: string; args: Record<string, unknown>; parallel?: boolean }> | null;
  agents: { selected: string[]; reasoning: string; status: Record<string, AgentStatus>; descriptions?: Record<string, string> } | null;
  toolCalls: Array<{ tool: string; args: Record<string, unknown>; task_id?: string; result?: unknown }>;
  answerChunks: string[];
  answerStarted: boolean;
  approval: ApprovalData | null;
  approvalSubmitting: boolean;
  error?: string;
  done?: boolean;
  charts: ChartData[];
  references: SupplierReference[];
  agentAnswer: AgentAnswer | null;
  evidence: AgentEvidenceRecord[];
  workflowStatus: AgentWorkflowSnapshot;
}

const markdownComponents = {
  code: ({ className, children, ...rest }: ComponentPropsWithoutRef<'code'> & { className?: string }) => {
    if (className === 'language-chart') {
      try {
        return <ChartRenderer data={JSON.parse(String(children).replace(/\n/g, ''))} />;
      } catch {
        return <code className={className} {...rest}>{children}</code>;
      }
    }
    return <code className={className} {...rest}>{children}</code>;
  },
};

function SupplierReferenceCard({ reference, onAnalyze }: { reference: SupplierReference; onAnalyze: (name: string) => void }) {
  return <article className="rounded-xl border border-amber-100 bg-amber-50/60 p-2.5 text-xs text-amber-950 space-y-1.5">
    <button onClick={() => onAnalyze(reference.name)} className="font-medium text-left hover:underline">{reference.name}</button>
    <div className="grid gap-1 text-amber-800">
      {reference.website_url ? <a href={reference.website_url} target="_blank" rel="noreferrer" className="w-fit hover:underline">官网（待核验）</a> : <span>官网：未找到</span>}
      {reference.contact_phone ? <span>电话（待核验）：{reference.contact_phone}</span> : <span>电话：未找到</span>}
      {reference.contact_email ? <a href={`mailto:${reference.contact_email}`} className="w-fit hover:underline">邮箱（待核验）：{reference.contact_email}</a> : <span>邮箱：未找到</span>}
    </div>
  </article>;
}

interface ChatMessageListProps {
  messages: ChatMessage[];
  streamState: ChatStreamViewState | null;
  loading: boolean;
  onAnalyzeReference: (name: string) => void;
  onApproval: (approved: boolean) => void;
  StructuredAgentResult: ComponentType<{ answer?: AgentAnswer; evidence?: AgentEvidenceRecord[] }>;
}

export default function ChatMessageList({ messages, streamState, loading, onAnalyzeReference, onApproval, StructuredAgentResult }: ChatMessageListProps) {
  return <>
    {messages.map((message, index) => (
      <div key={index} className={`flex gap-3 ${message.role === 'user' ? 'flex-row-reverse' : ''}`}>
        <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-semibold shrink-0 ${message.role === 'user' ? 'bg-[var(--color-primary-bg)] text-white' : 'bg-[var(--color-surface-selected)] text-[var(--color-text-secondary)]'}`}>
          {message.role === 'user' ? '你' : 'AI'}
        </div>
        <div className={`${message.role === 'user' ? 'max-w-[80%]' : 'min-w-0 flex-1 max-w-5xl'} rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm ${message.role === 'user' ? 'bg-[var(--color-code-bg)] text-[var(--color-text)]' : 'bg-[var(--color-surface)] border border-[var(--color-border)] text-[var(--color-text)]'}`}>
          <div className="prose prose-sm max-w-none prose-p:my-1 prose-headings:my-2 prose-ul:my-1 prose-li:my-0">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{message.agentAnswer?.summary || message.content}</ReactMarkdown>
          </div>
          {message.role === 'assistant' && message.references && message.references.length > 0 && <details className="group mt-3 border-t border-[var(--color-border)] pt-2">
            <summary className="flex min-h-10 cursor-pointer list-none items-center justify-between gap-3 py-1 text-left text-xs text-[var(--color-text-secondary)] [&::-webkit-details-marker]:hidden">
              <span>本轮识别供应商（{message.references.length} 家）</span><span className="flex items-center gap-1.5"><span>按需展开查看</span><span aria-hidden="true" className="transition-transform group-open:rotate-180">⌄</span></span>
            </summary>
            <div className="grid gap-2 pt-2 sm:grid-cols-2">{message.references.map(reference => <SupplierReferenceCard key={`${reference.name}-${reference.source ?? ''}`} reference={reference} onAnalyze={onAnalyzeReference} />)}</div>
          </details>}
          {message.role === 'assistant' && <StructuredAgentResult answer={message.agentAnswer} evidence={message.evidence} />}
          {message.role === 'assistant' && message.workflow && <AgentWorkflowPanel state={{ thinking: '', plan: null, agents: null, toolCalls: [], answerStarted: true, approval: message.approval ?? null, approvalSubmitting: message.approval?.status === 'submitting', workflowStatus: message.workflow }} onApproval={onApproval} />}
        </div>
      </div>
    ))}
    {streamState && (loading || streamState.error) && <div className="flex gap-3">
      <div className="w-8 h-8 rounded-full bg-[var(--color-surface-selected)] flex items-center justify-center text-xs font-semibold text-[var(--color-text-secondary)] shrink-0">AI</div>
      <div className="min-w-0 flex-1 max-w-5xl space-y-2">
        <AgentWorkflowPanel state={streamState} onApproval={onApproval} />
        {streamState.charts.map((chart, index) => <ChartRenderer key={`chart-${index}`} data={chart} />)}
        {streamState.answerChunks.length > 0 && <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl px-4 py-3 text-sm text-[var(--color-text)] shadow-sm">
          <div className="prose prose-sm max-w-none prose-p:my-1 prose-headings:my-2 prose-ul:my-1 prose-li:my-0"><ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{streamState.agentAnswer?.summary || streamState.answerChunks.join('')}</ReactMarkdown></div>
          <span className="inline-block w-2 h-4 bg-[var(--color-primary-bg)] animate-pulse ml-1" />
          {streamState.references.length > 0 && <div className="mt-3 border-t border-[var(--color-border)] pt-2 text-xs text-gray-500">识别到：{streamState.references.map(reference => reference.name).join('、')}</div>}
          <StructuredAgentResult answer={streamState.agentAnswer ?? undefined} evidence={streamState.evidence} />
        </div>}
      </div>
    </div>}
  </>;
}
