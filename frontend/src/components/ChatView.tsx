import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { chatRunEventStream, chatStream, dispatchStreamEvent, resumeChat } from '../api';
import type { ApprovalData, StreamCallbacks } from '../api';
import type { AgentAnswer, AgentEvidenceRecord, AgentWorkflowLifecycle, AgentWorkflowSnapshot, ChatMessage, SupplierIdentityCandidate, SupplierReference } from '../types';
import type { AgentStatus } from './AgentWorkflowPanel';
import ChatMessageList, { type ChatStreamViewState } from './ChatMessageList';
import StructuredAgentResult from './StructuredAgentResult';

const CAPABILITIES = [
  { label: '风险评估', desc: '全面分析企业风险状况', prompt: '对「公司名」进行全面的风险评估' },
  { label: '舆情分析', desc: '监测企业最新舆情动态', prompt: '分析「公司名」的最新舆情和新闻动态' },
  { label: '合规筛查', desc: '制裁名单与黑名单筛查', prompt: '对「公司名」进行制裁名单和合规筛查' },
  { label: '趋势预测', desc: '预测未来风险变化趋势', prompt: '预测「公司名」未来6-12个月的风险趋势' },
  { label: '关系图谱', desc: '供应链关系与传染风险', prompt: '分析「公司名」的供应链关系和传染风险' },
  { label: '报告生成', desc: '一键生成风险评估报告', prompt: '生成「公司名」的风险评估报告' },
];

const RECOMMENDED = [
  '复核青岛三祥科技股份有限公司',
  '复核上海汽车制动系统有限公司',
];

interface Session {
  sid: string;
  title: string;
  msgs: ChatMessage[];
  updatedAt: number;
}

const STORAGE_KEY = 'chat_sessions';

/** Scope browser-only chat state to the authenticated account. */
function storageScope(): string {
  try {
    const raw = localStorage.getItem('session');
    const user = raw ? JSON.parse(raw) as { username?: string } : null;
    const username = String(user?.username || '').trim();
    return username || 'anonymous';
  } catch {
    return 'anonymous';
  }
}

function scopedKey(base: string): string {
  const scope = storageScope();
  // The anonymous key is kept for isolated component tests and legacy local
  // previews; authenticated sessions always use a per-user namespace.
  return scope === 'anonymous' ? base : `${base}:${encodeURIComponent(scope)}`;
}

function createSessionId(): string {
  try {
    const randomUuid = globalThis.crypto?.randomUUID;
    if (typeof randomUuid === 'function') return randomUuid.call(globalThis.crypto);
  } catch {
    // LAN demos may run over plain HTTP where randomUUID is unavailable.
  }
  return `session-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function agentFromTool(tool: string): string | null {
  const match = tool.match(/^(sourcing|risk|compliance|sentiment)_agent$/);
  return match ? match[1] : null;
}

function loadSessions(): Session[] {
  try {
    const raw = localStorage.getItem(scopedKey(STORAGE_KEY));
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function saveSessions(sessions: Session[]) {
  // keep last 50 sessions max
  const trimmed = sessions.slice(-50);
  localStorage.setItem(scopedKey(STORAGE_KEY), JSON.stringify(trimmed));
}

type StreamState = ChatStreamViewState;

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

function normalizeApprovalAnswer(answer: string, approved?: boolean): string {
  const waitingText = /已生成\s*\d+\s*项加入监控操作，等待人工确认后才会写入监控清单。?/;
  if (approved === true) {
    return waitingText.test(answer)
      ? answer.replace(waitingText, '加入监控操作已获批准，服务端已返回执行结果。')
      : answer;
  }
  if (approved === false) {
    return waitingText.test(answer)
      ? answer.replace(waitingText, '加入监控操作已拒绝，系统未写入监控清单。')
      : answer;
  }
  return waitingText.test(answer)
    ? answer.replace(waitingText, '分析已完成；如需执行加入监控，请在下方“执行详情”中确认。')
    : answer;
}

function restoreConfirmedSupplierQuestion(
  originalMessage: string | undefined,
  candidate: SupplierIdentityCandidate,
): string {
  const canonicalName = candidate.supplier_name.trim();
  const original = originalMessage?.trim();
  if (!original) return `查看${canonicalName}的风险情况`;

  let restored = original;
  const aliases = [candidate.short_name, canonicalName]
    .filter((value): value is string => Boolean(value?.trim()))
    .sort((left, right) => right.length - left.length);
  const alias = aliases.find(value => value !== canonicalName && restored.includes(value));
  if (alias) {
    // A short name can be a prefix of the name used in the question (for
    // example “网易云音乐” vs. “网易云”). Consume the overlapping suffix
    // already present in the canonical name so it is not duplicated.
    const canonicalAliasIndex = canonicalName.indexOf(alias);
    const canonicalTail = canonicalAliasIndex >= 0
      ? canonicalName.slice(canonicalAliasIndex + alias.length)
      : '';
    const aliasIndex = restored.indexOf(alias);
    let overlap = 0;
    while (
      overlap < canonicalTail.length
      && aliasIndex + alias.length + overlap < restored.length
      && restored[aliasIndex + alias.length + overlap] === canonicalTail[overlap]
    ) overlap += 1;
    restored = restored.replace(`${alias}${canonicalTail.slice(0, overlap)}`, canonicalName);
  }

  // Keep the original analysis wording, but remove a spoken filler that a
  // model may have accidentally treated as part of the company name.
  restored = restored.replace(/(查看|查询|查找|分析|评估|复核|监控|看看)(一下|下)(?=[\u4e00-\u9fffA-Za-z0-9])/g, '$1');
  if (restored.includes(canonicalName)) return restored;

  // Legacy clarification records may not contain the original question. In
  // that case send an explicit, executable risk request instead of a bare
  // company name, which has no analysis dimension for the Harness to plan.
  if (restored.includes('舆情') || restored.includes('新闻')) return `分析${canonicalName}的舆情和新闻动态`;
  if (restored.includes('财务')) return `查看${canonicalName}的财务情况`;
  if (restored.includes('供应链') || restored.includes('传染')) return `分析${canonicalName}的供应链关系和传染风险`;
  if (restored.includes('合规') || restored.includes('制裁') || restored.includes('司法') || restored.includes('诉讼')) return `分析${canonicalName}的合规与司法风险`;
  if (restored.includes('趋势')) return `预测${canonicalName}未来6-12个月的风险趋势`;
  return `查看${canonicalName}的风险情况`;
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
    try { return localStorage.getItem(scopedKey('chat_input')) || ''; } catch { return ''; }
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
  const inputRef = useRef<HTMLInputElement>(null);
  const saveTimerRef = useRef<number | null>(null);
  const answerAccRef = useRef<string>('');  // 累积流式答案，用于 onDone 回退
  const approvalAccRef = useRef<ApprovalData | null>(null);
  const autoSubmittedQueryRef = useRef(false);
  const referencesAccRef = useRef<SupplierReference[]>([]);
  const agentAnswerAccRef = useRef<AgentAnswer | undefined>(undefined);
  const evidenceAccRef = useRef<AgentEvidenceRecord[]>([]);
  const workflowAccRef = useRef<AgentWorkflowSnapshot>(createWorkflowSnapshot());

  // Debounced localStorage save for chat input
  useEffect(() => {
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      try {
        localStorage.setItem(scopedKey('chat_input'), input);
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

  const send = useCallback(async (msg?: string, forceNewSession = false) => {
    const text = (msg ?? inputRef.current?.value ?? input).trim();
    if (!text || loading) return;
    setInput('');

    const isNewSession = forceNewSession || !activeSid;
    const sid = isNewSession ? createSessionId() : activeSid;
    if (isNewSession) setActiveSid(sid);

    const newMsgs: ChatMessage[] = [...msgs, { role: 'user', content: text }];
    persist(sid, newMsgs, isNewSession);
    setLoading(true);
    const initialWorkflow = createWorkflowSnapshot();
    workflowAccRef.current = initialWorkflow;
    setStreamState({ thinking: '', plan: null, agents: null, toolCalls: [], answerChunks: [], answerStarted: false, approval: null, approvalSubmitting: false, charts: [], references: [], agentAnswer: null, evidence: [], workflowStatus: initialWorkflow });
    answerAccRef.current = '';
    approvalAccRef.current = null;
    referencesAccRef.current = [];
    agentAnswerAccRef.current = undefined;
    evidenceAccRef.current = [];

    let runId: string | null = null;
    let lastEventId: number | null = null;
    let receivedTerminalEvent = false;
    const streamCallbacks: StreamCallbacks = {
        onRun: (data) => {
          runId = data.run_id;
        },
        onEventId: (eventId) => {
          lastEventId = eventId;
        },
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
          receivedTerminalEvent = true;
          const pendingApproval = approvalAccRef.current;
          const rawAnswer = answerAccRef.current || data.answer;
          const finalAnswer = normalizeApprovalAnswer(rawAnswer);
          const contractStatus = agentAnswerAccRef.current?.status;
          const serverStatus = data.status || contractStatus || workflowAccRef.current.status;
          const hasServerTerminalStatus = ['completed', 'partial', 'needs_review', 'failed', 'rejected'].includes(serverStatus);
          const finalWorkflow = {
            ...workflowAccRef.current,
            status: pendingApproval ? 'waiting_approval' : (hasServerTerminalStatus ? serverStatus : 'failed') as AgentWorkflowLifecycle | string,
            stage: pendingApproval ? 'approval' : hasServerTerminalStatus && ['completed', 'partial'].includes(serverStatus) ? 'completed' : 'decision',
            message: pendingApproval ? '分析已完成，等待人工确认写操作' : !hasServerTerminalStatus ? '服务端未返回有效终态，已停止显示为成功' : serverStatus === 'needs_review' ? '结果需要人工复核' : serverStatus === 'partial' ? '本轮 Agent 仅完成部分分析' : serverStatus === 'failed' ? '本轮 Agent 执行失败' : serverStatus === 'rejected' ? '操作已拒绝，未写入业务数据' : '本轮 Agent 工作流已完成',
          };
          workflowAccRef.current = finalWorkflow;
          const completedMsgs: ChatMessage[] = [...newMsgs, { role: 'assistant', content: finalAnswer, references: referencesAccRef.current, agentAnswer: agentAnswerAccRef.current, evidence: evidenceAccRef.current, workflow: finalWorkflow, approval: pendingApproval ? { ...pendingApproval, status: 'pending' } : undefined }];
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
          receivedTerminalEvent = true;
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
          receivedTerminalEvent = true;
          const clarificationWorkflow = { ...workflowAccRef.current, status: 'stopped' as const, stage: data.stage || 'understand', message: data.message };
          workflowAccRef.current = clarificationWorkflow;
          const clarifiedMsgs: ChatMessage[] = [...newMsgs, { role: 'assistant', content: data.message, identityCandidates: data.candidates, clarificationMessage: text, workflow: clarificationWorkflow }];
          persist(sid, clarifiedMsgs);
          setStreamState(null);
          setLoading(false);
        },
        onApprovalRequired: (data) => {
          receivedTerminalEvent = true;
          approvalAccRef.current = { ...data, status: 'pending' };
          const waiting = { ...workflowAccRef.current, status: 'waiting_approval' as const, stage: 'approval', message: '等待人工确认后继续执行' };
          workflowAccRef.current = waiting;
          // Approval is a deliberate pause, not a failed stream. Persist the
          // approval card immediately because the supervisor stream ends
          // after approval_required and does not emit a normal done event.
          const approvalAnswer = normalizeApprovalAnswer(answerAccRef.current || data.message);
          const approvalMsgs: ChatMessage[] = [...newMsgs, {
            role: 'assistant',
            content: approvalAnswer,
            references: referencesAccRef.current,
            agentAnswer: agentAnswerAccRef.current,
            evidence: evidenceAccRef.current,
            workflow: waiting,
            approval: { ...data, status: 'pending' },
          }];
          persist(sid, approvalMsgs);
          answerAccRef.current = '';
          setStreamState(null);
          setLoading(false);
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
      };

    try {
      await chatStream(text, sid, streamCallbacks, 'auto');
    } catch (err) {
      if (runId && !receivedTerminalEvent) {
        try {
          await chatRunEventStream(runId, lastEventId, {
            onEvent: (event) => {
              dispatchStreamEvent(event.eventType, event.data, streamCallbacks);
              lastEventId = event.eventId;
            },
          });
          if (receivedTerminalEvent) return;
        } catch (replayError) {
          console.error('Chat event replay failed:', replayError);
        }
      }
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

  // Deep links from the monitoring workbench represent an explicit Agent
  // review action. Start them in a fresh conversation and submit immediately
  // so the button is an executable workflow rather than a prefilled draft.
  useEffect(() => {
    const query = searchParams.get('q')?.trim();
    if (!query || autoSubmittedQueryRef.current) return;
    autoSubmittedQueryRef.current = true;
    void send(query, true);
  }, [searchParams, send]);

  const handleApproval = useCallback(async (approved: boolean) => {
    const persistedApprovalIndex = [...msgs].map((message, index) => ({ message, index })).reverse().find(
      item => item.message.role === 'assistant' && item.message.approval && !['approved', 'rejected'].includes(item.message.approval.status || ''),
    )?.index;
    const persistedApproval = persistedApprovalIndex === undefined ? null : msgs[persistedApprovalIndex].approval;
    const approval = streamState?.approval || persistedApproval;
    if (!approval || ['approved', 'rejected'].includes(approval.status || '')) return;
    const { session_id: approvalSid } = approval;

    // Persist the pending approval before resuming so a refresh does not lose
    // the only place where the user can confirm the write action.
    const submittingApproval: ApprovalData = { ...approval, status: 'submitting' };
    let resumeMsgs: ChatMessage[];
    if (persistedApprovalIndex !== undefined) {
      resumeMsgs = msgs.map((item, index) => index === persistedApprovalIndex ? { ...item, approval: submittingApproval } : item);
    } else {
      resumeMsgs = [...msgs, {
        role: 'assistant',
        content: approval.message,
        approval: submittingApproval,
        workflow: { ...workflowAccRef.current, status: 'waiting_approval', stage: 'approval', message: '等待人工确认后继续执行' },
      }];
    }
    persist(approvalSid, resumeMsgs);
    setLoading(true);

    // 保留审批内容并标记提交中，恢复 loading 状态继续流式输出
    setStreamState(prev => prev ? {
      ...prev,
      approval: submittingApproval,
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
          const rawAnswer = answerAccRef.current || data.answer;
          const finalAnswer = normalizeApprovalAnswer(rawAnswer, approved);
          const contractStatus = agentAnswerAccRef.current?.status;
          const serverStatus = data.status || contractStatus || workflowAccRef.current.status;
          const hasServerTerminalStatus = ['completed', 'partial', 'needs_review', 'failed', 'rejected'].includes(serverStatus);
          const finalWorkflow = {
            ...workflowAccRef.current,
            status: (hasServerTerminalStatus ? serverStatus : 'failed') as AgentWorkflowLifecycle | string,
            stage: hasServerTerminalStatus && ['completed', 'partial'].includes(serverStatus) ? 'completed' : 'decision',
            message: !hasServerTerminalStatus ? '服务端未返回有效终态，已停止显示为成功' : serverStatus === 'needs_review' ? '结果需要人工复核' : serverStatus === 'partial' ? '本轮 Agent 仅完成部分分析' : serverStatus === 'failed' ? '本轮 Agent 执行失败' : serverStatus === 'rejected' ? '操作已拒绝，未写入业务数据' : '本轮 Agent 工作流已完成',
          };
          workflowAccRef.current = finalWorkflow;
          const resolvedApproval: ApprovalData = { ...approval, status: approved ? 'approved' : 'rejected' };
          const completedMsgs: ChatMessage[] = resumeMsgs.map((item, index) => {
            if (index !== (persistedApprovalIndex ?? resumeMsgs.length - 1)) return item;
            return { ...item, content: finalAnswer, references: referencesAccRef.current, agentAnswer: agentAnswerAccRef.current, evidence: evidenceAccRef.current, workflow: finalWorkflow, approval: resolvedApproval };
          });
          answerAccRef.current = '';
          approvalAccRef.current = null;
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
          const failedApproval: ApprovalData = { ...approval, status: 'failed' };
          const failedMsgs: ChatMessage[] = resumeMsgs.map((item, index) => index === (persistedApprovalIndex ?? resumeMsgs.length - 1)
            ? { ...item, content: `错误：${data.message}`, workflow: failedWorkflow, approval: failedApproval }
            : item);
          persist(approvalSid, failedMsgs);
          setStreamState(prev => prev ? { ...prev, error: data.message, workflowStatus: failedWorkflow, approvalSubmitting: false } : null);
          setLoading(false);
        },
      });
    } catch (err) {
      const isTimeout = err instanceof DOMException && err.name === 'AbortError';
      const failedApproval: ApprovalData = { ...approval, status: 'failed' };
      const failedMsgs: ChatMessage[] = resumeMsgs.map((item, index) => index === (persistedApprovalIndex ?? resumeMsgs.length - 1)
        ? { ...item, content: isTimeout ? '请求超时，请重试' : '操作失败，请重试', approval: failedApproval }
        : item);
      persist(approvalSid, failedMsgs);
      setStreamState(prev => prev ? { ...prev, error: isTimeout ? '请求超时，请重试' : '操作失败，请重试', approvalSubmitting: false } : null);
      setLoading(false);
    }
  }, [streamState, msgs, persist]);

  const handleCapabilityClick = (prompt: string) => {
    const recentSupplier = [...msgs].reverse().flatMap(message => message.references || []).find(reference => reference.name)?.name;
    setInput(prompt.replace('「公司名」', recentSupplier || '青岛三祥科技股份有限公司'));
  };

  const handleRecommendedClick = (question: string) => {
    // Demo cases and Agent review entry points are standalone workflows. Do
    // not append them to whichever conversation happens to be selected.
    send(question, true);
  };

  const newChat = () => {
    setActiveSid('');
    setStreamState(null);
    setLoading(false);
  };

  const switchSession = (sid: string) => {
    setActiveSid(sid);
    setStreamState(null);
    setLoading(false);
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
      <div className="flex-1 flex flex-col min-w-0 w-full max-w-6xl mx-auto h-full">
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
                <h2 className="text-xl font-bold text-[var(--color-text)]">采购助手</h2>
                <p className="text-sm text-gray-500 leading-relaxed">
                  用自然语言获取寻源建议，持续核验负责供应商的风险变化
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
                      className="group rounded-xl border border-slate-200 bg-white p-3 text-left transition-[transform,box-shadow,border-color] duration-200 hover:-translate-y-0.5 hover:border-[var(--color-primary-bg)]/30 hover:shadow-sm"
                    >
                      <p className="text-sm font-medium text-[var(--color-text)] group-hover:text-[var(--color-primary-bg)] transition-colors">{cap.label}</p>
                      <p className="text-[11px] text-gray-400 mt-0.5 leading-tight">{cap.desc}</p>
                    </button>
                  ))}
                </div>
              </div>

              {/* Recommended questions */}
              <div>
                <p className="text-xs text-gray-400 mb-3 px-1">比赛演示案例</p>
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
        <ChatMessageList
          messages={msgs}
          streamState={streamState}
          loading={loading}
          onAnalyzeReference={name => setInput(`继续分析 ${name} 的风险`)}
          onConfirmSupplier={(candidate, originalMessage) => send(restoreConfirmedSupplierQuestion(originalMessage, candidate))}
          onApproval={handleApproval}
          StructuredAgentResult={StructuredAgentResult}
        />
        <div ref={bottomRef} />
      </div>

      {/* input */}
      <div className="px-4 pb-6 pt-2">
        <label htmlFor="chat-input" className="sr-only">向采购助手提问</label>
        <div className="flex items-center gap-2 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-1 shadow-sm transition-shadow focus-within:border-[var(--color-border-focus)] glass-surface">
          <input
            id="chat-input"
            name="chat-input"
            autoComplete="off"
            ref={inputRef}
            value={input}
            onChange={e => { setInput(e.target.value); }}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="输入问题，如：对比海康威视和宝钢的风险…"
            className="flex-1 border-none bg-transparent py-2.5 text-sm placeholder-gray-300 outline-none focus-visible:ring-0"
          />
          <button
            onClick={() => send()}
            disabled={loading}
            title={loading ? '当前请求仍在处理中' : '发送问题'}
            className="bg-[var(--color-primary-bg)] text-white rounded-xl px-4 py-2 text-sm hover:bg-[var(--color-primary-hover)] disabled:opacity-40 shrink-0 transition-colors min-h-[44px] inline-flex items-center"
          >
            {loading ? '处理中…' : '发送'}
          </button>
        </div>
        <div className="flex justify-between mt-2 px-1">
          <div className="flex items-center gap-3">
            <button onClick={newChat} className="text-xs text-gray-400 hover:text-gray-600 transition-colors">
              + 新对话
            </button>
            {loading && <span role="status" aria-live="polite" className="text-xs text-[var(--color-text-secondary)]">{streamState?.workflowStatus?.message || '正在提交并等待 Agent 响应…'}</span>}
          </div>
        </div>
      </div>
      </div>
    </div>
  );
}
