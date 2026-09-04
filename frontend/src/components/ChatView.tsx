import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { chatStream, resumeChat } from '../api';
import type { ApprovalData } from '../api';
import type { AgentAnswer, AgentEvidenceRecord, AgentWorkflowLifecycle, AgentWorkflowSnapshot, ChatMessage, ChartData, SupplierReference } from '../types';
import ChartRenderer from './ChartRenderer';
import AgentWorkflowPanel from './AgentWorkflowPanel';
import type { AgentStatus } from './AgentWorkflowPanel';

// react-markdown 自定义渲染：支持 ```chart 代码块
const markdownComponents = {
  code: ({ className, children, ...rest }: React.ComponentPropsWithoutRef<'code'> & { className?: string }) => {
    if (className === 'language-chart') {
      try {
        const chartData = JSON.parse(String(children).replace(/\n/g, ''));
        return <ChartRenderer data={chartData} />;
      } catch {
        return <code className={className} {...rest}>{children}</code>;
      }
    }
    return <code className={className} {...rest}>{children}</code>;
  },
};

const CAPABILITIES = [
  { label: '风险评估', desc: '全面分析企业风险状况', prompt: '对 {公司名} 进行全面的风险评估' },
  { label: '舆情分析', desc: '监测企业最新舆情动态', prompt: '分析 {公司名} 的最新舆情和新闻动态' },
  { label: '合规筛查', desc: '制裁名单与黑名单筛查', prompt: '对 {公司名} 进行制裁名单和合规筛查' },
  { label: '趋势预测', desc: '预测未来风险变化趋势', prompt: '预测 {公司名} 未来6-12个月的风险趋势' },
  { label: '关系图谱', desc: '供应链关系与传染风险', prompt: '分析 {公司名} 的供应链关系和传染风险' },
  { label: '报告生成', desc: '一键生成风险评估报告', prompt: '生成 {公司名} 的风险评估报告' },
];

const RECOMMENDED = [
  '分析监控清单中本月风险变化趋势',
  '对比台积电和中芯国际的综合风险差异',
  '帮我找光电器件领域风险最低的供应商',
];

interface Session {
  sid: string;
  title: string;
  msgs: ChatMessage[];
  updatedAt: number;
}

const STORAGE_KEY = 'chat_sessions';

function agentFromTool(tool: string): string | null {
  const match = tool.match(/^(sourcing|risk|compliance|sentiment)_agent$/);
  return match ? match[1] : null;
}

function loadSessions(): Session[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function saveSessions(sessions: Session[]) {
  // keep last 50 sessions max
  const trimmed = sessions.slice(-50);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
}

interface StreamState {
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

function createWorkflowSnapshot(): AgentWorkflowSnapshot {
  return {
    status: 'running',
    stage: 'understand',
    message: '正在分析您的问题…',
    targetSuppliers: [],
    sources: [],
    toolCallCount: 0,
    completedToolCount: 0,
  };
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

function StructuredAgentResult({ answer, evidence }: { answer?: AgentAnswer; evidence?: AgentEvidenceRecord[] }) {
  if (!answer && (!evidence || evidence.length === 0)) return null;
  const statusLabel: Record<string, string> = { completed: '证据充分', partial: '部分覆盖', needs_review: '需人工复核', failed: '执行失败' };
  return <div className="mt-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-code-bg)]/50 p-3 text-xs">
    {answer && <>
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">结构化结论</span>
        <span className="text-[var(--color-text-secondary)]">{statusLabel[answer.status] || answer.status}</span>
      </div>
      <div className="mt-2 grid gap-1 text-[var(--color-text-secondary)]">
        <span>有效 Claim：{answer.claims.length} 条</span>
        <span>引用证据：{answer.evidence_refs.length} 条</span>
        {answer.action_receipts.length > 0 && <span>副作用回执：{answer.action_receipts.length} 条</span>}
      </div>
      {answer.limitations.length > 0 && <p className="mt-2 text-amber-700">限制：{answer.limitations.join('；')}</p>}
    </>}
    {evidence && evidence.length > 0 && <p className="mt-2 text-[var(--color-text-secondary)]">本轮已加载 {evidence.length} 条证据记录。</p>}
  </div>;
}

export default function ChatView() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [sessions, setSessions] = useState<Session[]>(loadSessions);
  const [activeSid, setActiveSid] = useState<string>(() => {
    const list = loadSessions();
    return list.length > 0 ? list[list.length - 1].sid : '';
  });
  const [input, setInput] = useState<string>(() => {
    const queryInput = searchParams.get('q');
    if (queryInput) return queryInput;
    try { return localStorage.getItem('chat_input') || ''; } catch { return ''; }
  });

  useEffect(() => {
    if (!searchParams.get('q')) return;
    const next = new URLSearchParams(searchParams);
    next.delete('q');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const [loading, setLoading] = useState(false);
  const [streamState, setStreamState] = useState<StreamState | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const saveTimerRef = useRef<number | null>(null);
  const answerAccRef = useRef<string>('');  // 累积流式答案，用于 onDone 回退
  const referencesAccRef = useRef<SupplierReference[]>([]);
  const agentAnswerAccRef = useRef<AgentAnswer | undefined>(undefined);
  const evidenceAccRef = useRef<AgentEvidenceRecord[]>([]);
  const workflowAccRef = useRef<AgentWorkflowSnapshot>(createWorkflowSnapshot());

  // Debounced localStorage save for chat input
  useEffect(() => {
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      try {
        localStorage.setItem('chat_input', input);
      } catch {
        return;
      }
    }, 500);
    return () => { if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current); };
  }, [input]);

  const active = sessions.find(s => s.sid === activeSid);
  const msgs = useMemo(() => active?.msgs ?? [], [active]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [msgs, streamState]);

  const persist = useCallback((sid: string, newMsgs: ChatMessage[], create = false) => {
    const list = loadSessions();
    const idx = list.findIndex(s => s.sid === sid);
    const title = newMsgs.find(m => m.role === 'user')?.content.slice(0, 40) || '新对话';
    const session: Session = { sid, title, msgs: newMsgs, updatedAt: Date.now() };

    if (idx >= 0) list[idx] = session;
    else if (create) list.push(session);
    else return;

    list.sort((a, b) => b.updatedAt - a.updatedAt);
    saveSessions(list);
    setSessions(list);
  }, []);

  const send = useCallback(async (msg?: string) => {
    const text = (msg ?? input).trim();
    if (!text || loading) return;
    setInput('');

    const isNewSession = !activeSid;
    const sid = activeSid || crypto.randomUUID();
    if (isNewSession) setActiveSid(sid);

    const newMsgs: ChatMessage[] = [...msgs, { role: 'user', content: text }];
    persist(sid, newMsgs, isNewSession);
    setLoading(true);
    const initialWorkflow = createWorkflowSnapshot();
    workflowAccRef.current = initialWorkflow;
    setStreamState({ thinking: '', plan: null, agents: null, toolCalls: [], answerChunks: [], answerStarted: false, approval: null, approvalSubmitting: false, charts: [], references: [], agentAnswer: null, evidence: [], workflowStatus: initialWorkflow });
    answerAccRef.current = '';
    referencesAccRef.current = [];
    agentAnswerAccRef.current = undefined;
    evidenceAccRef.current = [];

    try {
      await chatStream(text, sid, {
        onThinking: (data) => {
          setStreamState(prev => prev ? { ...prev, thinking: data.message } : null);
        },
        onWorkflowStatus: (data) => {
          const previous = workflowAccRef.current;
          const next: AgentWorkflowSnapshot = {
            ...previous,
            status: data.status as AgentWorkflowLifecycle | string,
            stage: data.stage ?? previous.stage,
            message: data.message,
            runId: data.run_id ?? previous.runId,
            targetSuppliers: data.target_suppliers ?? previous.targetSuppliers,
            sources: data.sources ?? previous.sources,
            evidenceStatus: data.evidence_status ?? previous.evidenceStatus,
            loopExitReason: data.loop_exit_reason ?? previous.loopExitReason,
            toolCallCount: data.tool_call_count ?? previous.toolCallCount,
            completedToolCount: data.completed_tool_count ?? previous.completedToolCount,
          };
          workflowAccRef.current = next;
          setStreamState(prev => prev ? { ...prev, workflowStatus: next } : null);
        },
        onPlan: (data) => {
          setStreamState(prev => prev ? { ...prev, plan: data.steps } : null);
        },
        onAgentSelection: (data) => {
          setStreamState(prev => prev ? {
            ...prev,
            agents: {
              selected: data.agents,
              reasoning: data.reasoning,
              status: Object.fromEntries(data.agents.map(a => [a, 'running' as AgentStatus]))
            }
          } : null);
        },
        onAgentStart: (data) => {
          setStreamState(prev => {
            if (!prev) return null;
            const agents = prev.agents || { selected: [], reasoning: '', status: {} };
            return {
              ...prev,
              agents: {
                ...agents,
                selected: agents.selected.includes(data.agent) ? agents.selected : [...agents.selected, data.agent],
                status: { ...agents.status, [data.agent]: 'running' }
              }
            };
          });
        },
        onAgentComplete: (data) => {
          setStreamState(prev => {
            if (!prev) return null;
            const agents = prev.agents || { selected: [], reasoning: '', status: {} };
            return {
              ...prev,
              agents: {
                ...agents,
                selected: agents.selected.includes(data.agent) ? agents.selected : [...agents.selected, data.agent],
                status: { ...agents.status, [data.agent]: 'complete' }
              }
            };
          });
        },
        onToolCall: (data) => {
          setStreamState(prev => {
            if (!prev) return null;
            const agent = agentFromTool(data.tool);
            const agents = agent ? (prev.agents || { selected: [], reasoning: '', status: {} }) : prev.agents;
            return {
              ...prev,
              agents: agent && agents ? {
                ...agents,
                selected: agents.selected.includes(agent) ? agents.selected : [...agents.selected, agent],
                status: { ...agents.status, [agent]: 'running' },
              } : agents,
              toolCalls: [...prev.toolCalls, { tool: data.tool, args: data.args, task_id: data.task_id }],
              workflowStatus: { ...prev.workflowStatus, toolCallCount: prev.toolCalls.length + 1 },
            };
          });
        },
        onToolResult: (data) => {
          setStreamState(prev => {
            if (!prev) return null;
            const toolCalls = [...prev.toolCalls];
            const target = [...toolCalls].reverse().find(call =>
              (data.task_id && call.task_id === data.task_id) || (!data.task_id && call.tool === data.tool && call.result === undefined),
            );
            if (target) {
              target.result = data.result;
            }
            const agent = agentFromTool(data.tool);
            const agents = agent && prev.agents ? {
              ...prev.agents,
              status: { ...prev.agents.status, [agent]: 'complete' as AgentStatus },
            } : prev.agents;
            return { ...prev, agents, toolCalls, workflowStatus: { ...prev.workflowStatus, completedToolCount: toolCalls.filter(call => call.result !== undefined).length } };
          });
        },
        onAnswerChunk: (data) => {
          answerAccRef.current += data.text;
          setStreamState(prev => prev ? {
            ...prev,
            answerChunks: [...prev.answerChunks, data.text],
            answerStarted: true,
          } : null);
        },
        onDone: (data) => {
          const finalAnswer = answerAccRef.current || data.answer;
          const contractStatus = agentAnswerAccRef.current?.status;
          const serverStatus = data.status || contractStatus || workflowAccRef.current.status;
          const hasServerTerminalStatus = ['completed', 'partial', 'needs_review', 'failed'].includes(serverStatus);
          const finalWorkflow = {
            ...workflowAccRef.current,
            status: (hasServerTerminalStatus ? serverStatus : 'failed') as AgentWorkflowLifecycle | string,
            stage: hasServerTerminalStatus && ['completed', 'partial'].includes(serverStatus) ? 'completed' : 'decision',
            message: !hasServerTerminalStatus ? '服务端未返回有效终态，已停止显示为成功' : serverStatus === 'needs_review' ? '结果需要人工复核' : serverStatus === 'partial' ? '本轮 Agent 仅完成部分分析' : serverStatus === 'failed' ? '本轮 Agent 执行失败' : '本轮 Agent 工作流已完成',
          };
          workflowAccRef.current = finalWorkflow;
          const completedMsgs: ChatMessage[] = [...newMsgs, { role: 'assistant', content: finalAnswer, references: referencesAccRef.current, agentAnswer: agentAnswerAccRef.current, evidence: evidenceAccRef.current, workflow: finalWorkflow }];
          answerAccRef.current = '';
          persist(sid, completedMsgs);
          setStreamState(null);
          setLoading(false);
        },
        onAgentAnswer: (data) => {
          agentAnswerAccRef.current = data;
          setStreamState(prev => prev ? { ...prev, agentAnswer: data } : null);
        },
        onEvidence: (data) => {
          evidenceAccRef.current = data.records;
          setStreamState(prev => prev ? { ...prev, evidence: data.records } : null);
        },
        onError: (data) => {
          console.error('Stream error:', data.message);
          const failedWorkflow = { ...workflowAccRef.current, status: 'failed' as const, message: data.message };
          workflowAccRef.current = failedWorkflow;
          const failedMsgs: ChatMessage[] = [...newMsgs, { role: 'assistant', content: `错误：${data.message}`, workflow: failedWorkflow }];
          persist(sid, failedMsgs);
          setStreamState(prev => prev ? {
            ...prev,
            error: data.message,
            workflowStatus: failedWorkflow,
            approvalSubmitting: false,
            agents: prev.agents ? {
              ...prev.agents,
              status: Object.fromEntries(Object.entries(prev.agents.status).map(([agent, status]) => [agent, status === 'running' ? 'error' : status])) as Record<string, AgentStatus>,
            } : null,
          } : null);
          setLoading(false);
        },
        onClarification: (data) => {
          const clarificationWorkflow = { ...workflowAccRef.current, status: 'clarifying' as const, stage: 'understand', message: data.message };
          workflowAccRef.current = clarificationWorkflow;
          const clarifiedMsgs: ChatMessage[] = [...newMsgs, { role: 'assistant', content: data.message, workflow: clarificationWorkflow }];
          persist(sid, clarifiedMsgs);
          setStreamState(null);
          setLoading(false);
        },
        onApprovalRequired: (data) => {
          const waiting = { ...workflowAccRef.current, status: 'waiting_approval' as const, stage: 'approval', message: '等待人工确认后继续执行' };
          workflowAccRef.current = waiting;
          setStreamState(prev => prev ? { ...prev, approval: data, workflowStatus: waiting } : null);
        },
        onChartData: (data) => {
          setStreamState(prev => prev ? {
            ...prev,
            charts: [...prev.charts, data]
          } : null);
        },
        onReferences: (data) => {
          referencesAccRef.current = data.items;
          const next = {
            ...workflowAccRef.current,
            targetSuppliers: data.items.map(item => item.name),
            sources: [...new Set(data.items.map(item => item.source || item.discovery_source).filter((item): item is string => Boolean(item)))],
          };
          workflowAccRef.current = next;
          setStreamState(prev => prev ? { ...prev, references: data.items, workflowStatus: next } : null);
        },
      }, 'auto');
    } catch (err) {
      const isTimeout = err instanceof DOMException && err.name === 'AbortError';
      const failureMessage = isTimeout ? '请求超时（2分钟），请简化问题后重试' : '请求失败，请重试';
      const failedWorkflow = { ...workflowAccRef.current, status: 'failed' as const, message: isTimeout ? '工作流超时，尚未完成' : '工作流执行失败' };
      workflowAccRef.current = failedWorkflow;
      const failedMsgs: ChatMessage[] = [...newMsgs, { role: 'assistant', content: failureMessage, workflow: failedWorkflow }];
      persist(sid, failedMsgs);
      setStreamState(null);
      setLoading(false);
    }
  }, [input, loading, activeSid, msgs, persist]);

  const handleApproval = useCallback(async (approved: boolean) => {
    if (!streamState?.approval) return;
    const { session_id: approvalSid, tool, args, message } = streamState.approval;

    // 记录审批结果到消息历史
    const statusText = approved ? '✅ 已批准' : '❌ 已拒绝';
    const approvalMsg: ChatMessage = {
      role: 'assistant',
      content: `${message}\n\n${statusText}：${tool}(${JSON.stringify(args)})`,
    };
    const updatedMsgs = [...msgs, approvalMsg];
    persist(approvalSid, updatedMsgs);

    // 保留审批内容并标记提交中，恢复 loading 状态继续流式输出
    setStreamState(prev => prev ? {
      ...prev,
      approval: prev.approval,
      approvalSubmitting: true,
      thinking: '正在执行操作...',
      toolCalls: [],
      answerChunks: [],
      charts: [],
      references: [],
      agentAnswer: null,
      evidence: [],
    } : null);
    answerAccRef.current = '';
    agentAnswerAccRef.current = undefined;
    evidenceAccRef.current = [];

    const resumeMsgs: ChatMessage[] = [...updatedMsgs];

    try {
      await resumeChat(approvalSid, approved, {
        onWorkflowStatus: (data) => {
          const previous = workflowAccRef.current;
          const next = {
            ...previous,
            status: data.status as AgentWorkflowLifecycle | string,
            stage: data.stage ?? previous.stage,
            message: data.message,
            runId: data.run_id ?? previous.runId,
            targetSuppliers: data.target_suppliers ?? previous.targetSuppliers,
            sources: data.sources ?? previous.sources,
            evidenceStatus: data.evidence_status ?? previous.evidenceStatus,
            loopExitReason: data.loop_exit_reason ?? previous.loopExitReason,
            toolCallCount: data.tool_call_count ?? previous.toolCallCount,
            completedToolCount: data.completed_tool_count ?? previous.completedToolCount,
          };
          workflowAccRef.current = next;
          setStreamState(prev => prev ? { ...prev, workflowStatus: next, approvalSubmitting: true } : null);
        },
        onThinking: (data) => {
          setStreamState(prev => prev ? { ...prev, thinking: data.message, approvalSubmitting: true } : null);
        },
        onToolCall: (data) => {
          setStreamState(prev => prev ? {
            ...prev,
            toolCalls: [...prev.toolCalls, { tool: data.tool, args: data.args, task_id: data.task_id }],
            workflowStatus: { ...prev.workflowStatus, toolCallCount: prev.workflowStatus.toolCallCount + 1 },
          } : null);
        },
        onToolResult: (data) => {
          setStreamState(prev => {
            if (!prev) return null;
            const toolCalls = [...prev.toolCalls];
            const target = [...toolCalls].reverse().find(call =>
              (data.task_id && call.task_id === data.task_id) || (!data.task_id && call.tool === data.tool && call.result === undefined),
            );
            if (target) {
              target.result = data.result;
            }
            return { ...prev, toolCalls, workflowStatus: { ...prev.workflowStatus, completedToolCount: prev.workflowStatus.completedToolCount + 1 } };
          });
        },
        onAnswerChunk: (data) => {
          answerAccRef.current += data.text;
          setStreamState(prev => prev ? {
            ...prev,
            answerChunks: [...prev.answerChunks, data.text],
            answerStarted: true,
          } : null);
        },
        onChartData: (data) => {
          setStreamState(prev => prev ? {
            ...prev,
            charts: [...prev.charts, data]
          } : null);
        },
        onDone: (data) => {
          const finalAnswer = answerAccRef.current || data.answer;
          const contractStatus = agentAnswerAccRef.current?.status;
          const serverStatus = data.status || contractStatus || workflowAccRef.current.status;
          const hasServerTerminalStatus = ['completed', 'partial', 'needs_review', 'failed'].includes(serverStatus);
          const finalWorkflow = {
            ...workflowAccRef.current,
            status: (hasServerTerminalStatus ? serverStatus : 'failed') as AgentWorkflowLifecycle | string,
            stage: hasServerTerminalStatus && ['completed', 'partial'].includes(serverStatus) ? 'completed' : 'decision',
            message: !hasServerTerminalStatus ? '服务端未返回有效终态，已停止显示为成功' : serverStatus === 'needs_review' ? '结果需要人工复核' : serverStatus === 'partial' ? '本轮 Agent 仅完成部分分析' : serverStatus === 'failed' ? '本轮 Agent 执行失败' : '本轮 Agent 工作流已完成',
          };
          workflowAccRef.current = finalWorkflow;
          const completedMsgs: ChatMessage[] = [...resumeMsgs, { role: 'assistant', content: finalAnswer, references: referencesAccRef.current, agentAnswer: agentAnswerAccRef.current, evidence: evidenceAccRef.current, workflow: finalWorkflow }];
          answerAccRef.current = '';
          persist(approvalSid, completedMsgs);
          setStreamState(null);
          setLoading(false);
        },
        onAgentAnswer: (data) => {
          agentAnswerAccRef.current = data;
          setStreamState(prev => prev ? { ...prev, agentAnswer: data } : null);
        },
        onEvidence: (data) => {
          evidenceAccRef.current = data.records;
          setStreamState(prev => prev ? { ...prev, evidence: data.records } : null);
        },
        onError: (data) => {
          const failedWorkflow = { ...workflowAccRef.current, status: 'failed' as const, message: data.message };
          workflowAccRef.current = failedWorkflow;
          const failedMsgs: ChatMessage[] = [...resumeMsgs, { role: 'assistant', content: `错误：${data.message}`, workflow: failedWorkflow }];
          persist(approvalSid, failedMsgs);
          setStreamState(prev => prev ? { ...prev, error: data.message, workflowStatus: failedWorkflow, approvalSubmitting: false } : null);
          setLoading(false);
        },
      });
    } catch (err) {
      const isTimeout = err instanceof DOMException && err.name === 'AbortError';
      const failedMsgs: ChatMessage[] = [...resumeMsgs, { role: 'assistant', content: isTimeout ? '请求超时，请重试' : '操作失败，请重试' }];
      persist(approvalSid, failedMsgs);
      setStreamState(prev => prev ? { ...prev, error: isTimeout ? '请求超时，请重试' : '操作失败，请重试', approvalSubmitting: false } : null);
      setLoading(false);
    }
  }, [streamState, msgs, persist]);

  const handleCapabilityClick = (prompt: string) => {
    setInput(prompt);
  };

  const handleRecommendedClick = (question: string) => {
    send(question);
  };

  const newChat = () => {
    setActiveSid('');
    setStreamState(null);
  };

  const switchSession = (sid: string) => {
    setActiveSid(sid);
    setStreamState(null);
  };

  const deleteSession = (sid: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const list = sessions.filter(s => s.sid !== sid);
    saveSessions(list);
    setSessions(list);
    if (activeSid === sid) {
      setActiveSid(list.length > 0 ? list[0].sid : '');
    }
  };

  return (
    <div className="flex h-full max-w-full">
      {/* History card — left column, persistent on lg+ screens */}
      <aside className="hidden lg:flex w-52 shrink-0 flex-col pt-6 pl-4 pr-2">
        <div className="flex-1 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl shadow-md overflow-hidden flex flex-col">
          <div className="flex items-center justify-between px-3 pt-3 pb-2">
            <h3 className="text-[11px] font-medium text-gray-400">会话历史</h3>
            <button onClick={newChat} className="text-[11px] text-gray-400 hover:text-gray-600 transition-colors">+ 新建</button>
          </div>
          {sessions.length === 0 ? (
            <div className="flex-1 flex items-center justify-center pb-6">
              <p className="text-[11px] text-gray-300 px-3">暂无会话</p>
            </div>
          ) : (
            <div className="flex-1 overflow-auto px-1.5 pb-3">
              <div className="space-y-0.5">
                {sessions.map(s => (
                  <div
                    key={s.sid}
                    onClick={() => switchSession(s.sid)}
                    className={`group flex items-center rounded-lg px-3 py-2 cursor-pointer transition-colors ${
                      s.sid === activeSid
                        ? 'bg-[var(--color-primary-bg)] text-white'
                        : 'text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]'
                    }`}
                  >
                    <div className="flex-1 min-w-0">
                      <p className="text-sm truncate">{s.title}</p>
                      <p className={`text-[11px] ${s.sid === activeSid ? 'text-white/60' : 'text-gray-400'}`}>
                        {new Date(s.updatedAt).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                      </p>
                    </div>
                    <button
                      onClick={(e) => deleteSession(s.sid, e)}
                      className={`shrink-0 text-xs opacity-0 group-hover:opacity-100 transition-opacity ml-1 ${
                        s.sid === activeSid ? 'text-white/60 hover:text-white' : 'text-gray-300 hover:text-red-400'
                      }`}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </aside>

      {/* Main chat */}
      <div className="flex-1 flex flex-col min-w-0 max-w-3xl mx-auto h-full">
      {/* messages */}
      <div className="flex-1 overflow-auto px-4 space-y-6 py-6">
        {msgs.length === 0 && !loading && (
          <div className="flex-1 flex items-center justify-center px-4">
            <div className="w-full max-w-lg space-y-8 py-8">
              {/* Hero */}
              <div className="text-center space-y-2">
                <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-[var(--color-primary-bg)]/10 mb-2">
                  <svg className="w-7 h-7 text-[var(--color-primary-bg)]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M12 2l2.4 7.2h7.6l-6 4.8 2.4 7.2-6.4-4.8-6.4 4.8 2.4-7.2-6-4.8h7.6z"/>
                  </svg>
                </div>
                <h2 className="text-xl font-bold text-[var(--color-text)]">AI 工作台</h2>
                <p className="text-sm text-gray-500 leading-relaxed">
                  自主调用 20+ 数据工具，完成供应商风险分析、舆情监控、合规筛查等任务
                </p>
              </div>

              {/* Capability cards */}
              <div>
                <p className="text-xs text-gray-400 mb-3 px-1">快速能力</p>
                <div className="grid grid-cols-3 gap-2">
                  {CAPABILITIES.map((cap) => (
                    <button
                      key={cap.label}
                      onClick={() => handleCapabilityClick(cap.prompt)}
                      className="text-left bg-white border border-slate-200 rounded-xl p-3 hover:border-[var(--color-primary-bg)]/30 hover:shadow-sm hover:-translate-y-0.5 transition-all duration-200 group"
                    >
                      <p className="text-sm font-medium text-[var(--color-text)] group-hover:text-[var(--color-primary-bg)] transition-colors">{cap.label}</p>
                      <p className="text-[11px] text-gray-400 mt-0.5 leading-tight">{cap.desc}</p>
                    </button>
                  ))}
                </div>
              </div>

              {/* Recommended questions */}
              <div>
                <p className="text-xs text-gray-400 mb-3 px-1">推荐问题</p>
                <div className="space-y-1.5">
                  {RECOMMENDED.map((q) => (
                    <button
                      key={q}
                      onClick={() => handleRecommendedClick(q)}
                      className="w-full text-left text-sm text-gray-600 bg-amber-50/50 border border-amber-100/50 rounded-lg px-4 py-2.5 hover:bg-amber-50 hover:border-amber-200 transition-colors duration-150"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>

            </div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`flex gap-3 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}>
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-semibold shrink-0 ${
              m.role === 'user' ? 'bg-[var(--color-primary-bg)] text-white' : 'bg-[var(--color-surface-selected)] text-[var(--color-text-secondary)]'
            }`}>
              {m.role === 'user' ? '你' : 'AI'}
            </div>
            <div className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm ${
              m.role === 'user'
                ? 'bg-[var(--color-code-bg)] text-[var(--color-text)]'
                : 'bg-[var(--color-surface)] border border-[var(--color-border)] text-[var(--color-text)]'
            }`}>
              <div className="prose prose-sm max-w-none prose-p:my-1 prose-headings:my-2 prose-ul:my-1 prose-li:my-0">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{m.content}</ReactMarkdown>
              </div>
              {m.role === 'assistant' && m.references && m.references.length > 0 && (
                <div className="mt-3 border-t border-[var(--color-border)] pt-2">
                  <p className="text-[11px] text-gray-400 mb-1.5">本轮识别供应商</p>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {m.references.map(reference => (
                      <SupplierReferenceCard
                        key={`${reference.name}-${reference.source ?? ''}`}
                        reference={reference}
                        onAnalyze={name => setInput(`继续分析 ${name} 的风险`)}
                      />
                    ))}
                  </div>
                </div>
              )}
              {m.role === 'assistant' && <StructuredAgentResult answer={m.agentAnswer} evidence={m.evidence} />}
              {m.role === 'assistant' && m.workflow && <AgentWorkflowPanel state={{ thinking: '', plan: null, agents: null, toolCalls: [], answerStarted: true, approval: null, approvalSubmitting: false, workflowStatus: m.workflow }} onApproval={handleApproval} />}
            </div>
          </div>
        ))}
        {streamState && (loading || streamState.error) && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-full bg-[var(--color-surface-selected)] flex items-center justify-center text-xs font-semibold text-[var(--color-text-secondary)] shrink-0">AI</div>
            <div className="max-w-[80%] space-y-2">
              <AgentWorkflowPanel state={streamState} onApproval={handleApproval} />
              {/* Auto-injected charts from tool results */}
              {streamState.charts.map((chart, i) => (
                <ChartRenderer key={`chart-${i}`} data={chart} />
              ))}
              {/* Streaming answer */}
              {streamState.answerChunks.length > 0 && (
                <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl px-4 py-3 text-sm text-[var(--color-text)] shadow-sm">
                  <div className="prose prose-sm max-w-none prose-p:my-1 prose-headings:my-2 prose-ul:my-1 prose-li:my-0">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{streamState.answerChunks.join('')}</ReactMarkdown>
                  </div>
                  <span className="inline-block w-2 h-4 bg-[var(--color-primary-bg)] animate-pulse ml-1" />
                  {streamState.references.length > 0 && (
                    <div className="mt-3 border-t border-[var(--color-border)] pt-2 text-xs text-gray-500">
                      识别到：{streamState.references.map(reference => reference.name).join('、')}
                    </div>
                  )}
                  <StructuredAgentResult answer={streamState.agentAnswer ?? undefined} evidence={streamState.evidence} />
                </div>
              )}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* input */}
      <div className="px-4 pb-6 pt-2">
        <div className="flex items-center gap-2 bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl px-4 py-1 focus-within:border-[var(--color-border-focus)] focus-within:shadow-sm transition-shadow">
          <input
            value={input}
            onChange={e => { setInput(e.target.value); }}
            onKeyDown={e => e.key === 'Enter' && send()}
            placeholder="输入问题，如：对比海康威视和宝钢的风险"
            className="flex-1 border-none outline-none py-2.5 text-sm bg-transparent placeholder-gray-300"
          />
          <button
            onClick={() => send()}
            disabled={loading}
            className="bg-[var(--color-primary-bg)] text-white rounded-xl px-4 py-2 text-sm hover:bg-[var(--color-primary-hover)] disabled:opacity-40 shrink-0 transition-colors min-h-[44px] inline-flex items-center"
          >
            发送
          </button>
        </div>
        <div className="flex justify-between mt-2 px-1">
          <button onClick={newChat} className="text-xs text-gray-400 hover:text-gray-600 transition-colors">
            + 新对话
          </button>
        </div>
      </div>
      </div>
    </div>
  );
}
