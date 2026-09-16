import type { ComponentType } from 'react';
import type { ApprovalData } from '../api';
import type { AgentAnswer, AgentEvidenceRecord, AgentWorkflowSnapshot, ChartData, ChatMessage, SupplierIdentityCandidate, SupplierReference } from '../types';
import ChartRenderer from './ChartRenderer';
import AssistantRichText from './AssistantRichText';
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
  onConfirmSupplier: (candidate: SupplierIdentityCandidate, originalMessage?: string) => void;
  onApproval: (approved: boolean) => void;
  StructuredAgentResult: ComponentType<{ answer?: AgentAnswer; evidence?: AgentEvidenceRecord[] }>;
}

function SupplierIdentityChoices({ candidates, originalMessage, onConfirm }: { candidates: SupplierIdentityCandidate[]; originalMessage?: string; onConfirm: (candidate: SupplierIdentityCandidate, originalMessage?: string) => void }) {
  if (candidates.length === 0) return null;
  return <div className="mt-3 border-t border-[var(--color-border)] pt-3" role="group" aria-label="正式供应商候选">
    <p className="mb-2 text-xs text-[var(--color-text-secondary)]">请选择正式供应商：</p>
    <div className="grid gap-2 sm:grid-cols-2">
      {candidates.map(candidate => <button
        key={candidate.supplier_id}
        type="button"
        onClick={() => onConfirm(candidate, originalMessage)}
        className="min-h-11 rounded-xl border border-[var(--color-primary-bg)]/25 bg-[var(--color-primary-bg)]/5 px-3 py-2 text-left text-xs text-[var(--color-text)] transition-colors hover:border-[var(--color-primary-bg)] hover:bg-[var(--color-primary-bg)]/10"
      >
        <span className="block font-medium">{candidate.supplier_name}</span>
        {candidate.short_name && <span className="mt-0.5 block text-[var(--color-text-secondary)]">简称：{candidate.short_name}</span>}
      </button>)}
    </div>
  </div>;
}

export default function ChatMessageList({ messages, streamState, loading, onAnalyzeReference, onConfirmSupplier, onApproval, StructuredAgentResult }: ChatMessageListProps) {
  return <>
    {messages.map((message, index) => (
      <div key={index} className={`flex gap-3 ${message.role === 'user' ? 'flex-row-reverse' : ''}`}>
        <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-semibold shrink-0 ${message.role === 'user' ? 'bg-[var(--color-primary-bg)] text-white' : 'bg-[var(--color-surface-selected)] text-[var(--color-text-secondary)]'}`}>
          {message.role === 'user' ? '你' : 'AI'}
        </div>
        <div className={`${message.role === 'user' ? 'max-w-[80%]' : 'min-w-0 flex-1 max-w-5xl'} rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm ${message.role === 'user' ? 'bg-[var(--color-code-bg)] text-[var(--color-text)]' : 'bg-[var(--color-surface)] border border-[var(--color-border)] text-[var(--color-text)]'}`}>
          <AssistantRichText content={message.agentAnswer?.summary || message.content} label={message.agentAnswer ? '采购分析' : undefined} />
          {message.role === 'assistant' && message.identityCandidates && <SupplierIdentityChoices candidates={message.identityCandidates} originalMessage={message.clarificationMessage} onConfirm={onConfirmSupplier} />}
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
          <AssistantRichText content={streamState.agentAnswer?.summary || streamState.answerChunks.join('')} label={streamState.agentAnswer ? '采购分析' : undefined} />
          <span className="inline-block w-2 h-4 bg-[var(--color-primary-bg)] animate-pulse ml-1" />
          {streamState.references.length > 0 && <div className="mt-3 border-t border-[var(--color-border)] pt-2 text-xs text-gray-500">识别到：{streamState.references.map(reference => reference.name).join('、')}</div>}
          <StructuredAgentResult answer={streamState.agentAnswer ?? undefined} evidence={streamState.evidence} />
        </div>}
      </div>
    </div>}
  </>;
}
