import { useState, useEffect, useRef, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { api, chatStream } from '../api';
import { getRiskColor } from '../riskColors';
import type { ChatMessage, RiskResult } from '../types';

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
  agents: { selected: string[]; reasoning: string; status: Record<string, 'running' | 'complete' | 'error'> } | null;
  toolCalls: Array<{ tool: string; args: Record<string, unknown>; result?: unknown }>;
  answerChunks: string[];
}

export default function ChatView() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [sessions, setSessions] = useState<Session[]>(loadSessions);
  const [activeSid, setActiveSid] = useState<string>(() => {
    const list = loadSessions();
    return list.length > 0 ? list[list.length - 1].sid : '';
  });
  const [input, setInput] = useState<string>(() => {
    try { return localStorage.getItem('chat_input') || ''; } catch { return ''; }
  });

  // Read ?q= param from URL and auto-populate input
  useEffect(() => {
    const q = searchParams.get('q');
    if (q) {
      setInput(q);
      // Clear the param from URL without navigation
      const next = new URLSearchParams(searchParams);
      next.delete('q');
      setSearchParams(next, { replace: true });
    }
  }, []); // run once on mount
  const [loading, setLoading] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [streamState, setStreamState] = useState<StreamState | null>(null);
  const [mode, setMode] = useState<'react' | 'plan-execute' | 'multi-agent' | 'sourcing'>('react');
  const bottomRef = useRef<HTMLDivElement>(null);
  const saveTimerRef = useRef<number | null>(null);
  const answerAccRef = useRef<string>('');  // 累积流式答案，用于 onDone 回退

  // Debounced localStorage save for chat input
  useEffect(() => {
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      try { localStorage.setItem('chat_input', input); } catch {}
    }, 500);
    return () => { if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current); };
  }, [input]);

  const active = sessions.find(s => s.sid === activeSid);
  const msgs = active?.msgs ?? [];

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [msgs, streamState]);

  const persist = (sid: string, newMsgs: ChatMessage[]) => {
    const list = loadSessions();
    const idx = list.findIndex(s => s.sid === sid);
    const title = newMsgs.find(m => m.role === 'user')?.content.slice(0, 40) || '新对话';
    const session: Session = { sid, title, msgs: newMsgs, updatedAt: Date.now() };

    if (idx >= 0) list[idx] = session;
    else list.push(session);

    list.sort((a, b) => b.updatedAt - a.updatedAt);
    saveSessions(list);
    setSessions(list);
    if (!activeSid) setActiveSid(sid);
  };

  const send = useCallback(async (msg?: string) => {
    const text = (msg ?? input).trim();
    if (!text || loading) return;
    setInput('');

    const sid = activeSid || crypto.randomUUID();
    if (!activeSid) setActiveSid(sid);

    const newMsgs: ChatMessage[] = [...msgs, { role: 'user', content: text }];
    persist(sid, newMsgs);
    setLoading(true);
    setStreamState({ thinking: '', plan: null, agents: null, toolCalls: [], answerChunks: [] });
    answerAccRef.current = '';

    try {
      await chatStream(text, sid, {
        onSession: (sessionId) => {
          if (!activeSid) setActiveSid(sessionId);
        },
        onThinking: (data) => {
          setStreamState(prev => prev ? { ...prev, thinking: data.message } : null);
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
              status: Object.fromEntries(data.agents.map(a => [a, 'running']))
            }
          } : null);
        },
        onAgentStart: (data) => {
          setStreamState(prev => {
            if (!prev || !prev.agents) return null;
            return {
              ...prev,
              agents: {
                ...prev.agents,
                status: { ...prev.agents.status, [data.agent]: 'running' }
              }
            };
          });
        },
        onAgentComplete: (data) => {
          setStreamState(prev => {
            if (!prev || !prev.agents) return null;
            return {
              ...prev,
              agents: {
                ...prev.agents,
                status: { ...prev.agents.status, [data.agent]: 'complete' }
              }
            };
          });
        },
        onToolCall: (data) => {
          setStreamState(prev => prev ? {
            ...prev,
            toolCalls: [...prev.toolCalls, { tool: data.tool, args: data.args }]
          } : null);
        },
        onToolResult: (data) => {
          setStreamState(prev => {
            if (!prev) return null;
            const toolCalls = [...prev.toolCalls];
            const lastTool = toolCalls[toolCalls.length - 1];
            if (lastTool && lastTool.tool === data.tool) {
              lastTool.result = data.result;
            }
            return { ...prev, toolCalls };
          });
        },
        onAnswerChunk: (data) => {
          answerAccRef.current += data.text;
          setStreamState(prev => prev ? {
            ...prev,
            answerChunks: [...prev.answerChunks, data.text]
          } : null);
        },
        onDone: (data) => {
          const finalAnswer = answerAccRef.current || data.answer;
          newMsgs.push({ role: 'assistant', content: finalAnswer });
          answerAccRef.current = '';
          persist(sid, newMsgs);
          setStreamState(null);
          setLoading(false);
        },
        onError: (data) => {
          console.error('Stream error:', data.message);
          newMsgs.push({ role: 'assistant', content: `错误：${data.message}` });
          persist(sid, newMsgs);
          setStreamState(null);
          setLoading(false);
        },
      }, mode);
    } catch (err) {
      const isTimeout = err instanceof DOMException && err.name === 'AbortError';
      newMsgs.push({ role: 'assistant', content: isTimeout ? '请求超时（2分钟），请简化问题后重试' : '请求失败，请重试' });
      persist(sid, newMsgs);
      setStreamState(null);
      setLoading(false);
    }
  }, [input, loading, activeSid, msgs, mode]);

  const handleCapabilityClick = (prompt: string) => {
    setInput(prompt);
  };

  const handleRecommendedClick = (question: string) => {
    send(question);
  };

  const newChat = () => {
    setActiveSid('');
    setShowHistory(false);
    setStreamState(null);
  };

  const switchSession = (sid: string) => {
    setActiveSid(sid);
    setShowHistory(false);
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
    <div className="flex flex-col h-full max-w-full md:max-w-3xl mx-auto relative">
      {/* top bar */}
      <div className="flex justify-end px-4 pt-3 pb-0">
        <button
          onClick={() => setShowHistory(!showHistory)}
          className="text-xs text-gray-400 hover:text-gray-600 transition-colors"
        >
          历史 {sessions.length > 0 ? `(${sessions.length})` : ''}
        </button>
      </div>

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
                <h2 className="text-xl font-bold text-[var(--color-text)]">AI Agent</h2>
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

              {/* Mode selector in welcome */}
              <div>
                <p className="text-xs text-gray-400 mb-3 px-1">推理模式</p>
                <div className="flex gap-2">
                  <button
                    onClick={() => setMode('react')}
                    className={`flex-1 text-left rounded-xl px-3 py-2.5 transition-all duration-200 ${
                      mode === 'react'
                        ? 'bg-[var(--color-primary-bg)] text-white shadow-sm'
                        : 'bg-white border border-slate-200 text-gray-500 hover:border-slate-300'
                    }`}
                  >
                    <p className="text-sm font-medium">标准</p>
                    <p className="text-[11px] opacity-70 mt-0.5">单一分析任务</p>
                  </button>
                  <button
                    onClick={() => setMode('plan-execute')}
                    className={`flex-1 text-left rounded-xl px-3 py-2.5 transition-all duration-200 ${
                      mode === 'plan-execute'
                        ? 'bg-[var(--color-primary-bg)] text-white shadow-sm'
                        : 'bg-white border border-slate-200 text-gray-500 hover:border-slate-300'
                    }`}
                  >
                    <p className="text-sm font-medium">规划执行</p>
                    <p className="text-[11px] opacity-70 mt-0.5">复杂任务拆解</p>
                  </button>
                  <button
                    onClick={() => setMode('multi-agent')}
                    className={`flex-1 text-left rounded-xl px-3 py-2.5 transition-all duration-200 ${
                      mode === 'multi-agent'
                        ? 'bg-[var(--color-primary-bg)] text-white shadow-sm'
                        : 'bg-white border border-slate-200 text-gray-500 hover:border-slate-300'
                    }`}
                  >
                    <p className="text-sm font-medium">多Agent</p>
                    <p className="text-[11px] opacity-70 mt-0.5">专业分工协作</p>
                  </button>
                  <button
                    onClick={() => setMode('sourcing')}
                    className={`flex-1 text-left rounded-xl px-3 py-2.5 transition-all duration-200 ${
                      mode === 'sourcing'
                        ? 'bg-[var(--color-primary-bg)] text-white shadow-sm'
                        : 'bg-white border border-slate-200 text-gray-500 hover:border-slate-300'
                    }`}
                  >
                    <p className="text-sm font-medium">寻源</p>
                    <p className="text-[11px] opacity-70 mt-0.5">采购寻源推荐</p>
                  </button>
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
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
              </div>
            </div>
          </div>
        ))}
        {loading && streamState && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-full bg-[var(--color-surface-selected)] flex items-center justify-center text-xs font-semibold text-[var(--color-text-secondary)] shrink-0">AI</div>
            <div className="max-w-[80%] space-y-2">
              {/* Thinking indicator */}
              {streamState.thinking && streamState.answerChunks.length === 0 && (
                <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl px-4 py-3 text-sm text-gray-500 shadow-sm">
                  <span className="inline-block animate-pulse">{streamState.thinking}</span>
                </div>
              )}
              {/* Execution plan (Plan-and-Execute mode) */}
              {streamState.plan && streamState.plan.length > 0 && streamState.toolCalls.length === 0 && (
                <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl px-4 py-3 text-xs shadow-sm">
                  <div className="text-gray-500 mb-2">📋 执行计划：</div>
                  <div className="space-y-1">
                    {streamState.plan.map((step, i) => (
                      <div key={i} className="flex items-center gap-2 text-gray-600">
                        <span className="text-gray-400">{i + 1}.</span>
                        <span className="font-mono">{step.tool}</span>
                        {step.parallel && <span className="text-blue-400 text-[10px]">并行</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {/* Agent selection (Multi-Agent mode) */}
              {streamState.agents && streamState.agents.selected.length > 0 && streamState.toolCalls.length === 0 && (
                <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl px-4 py-3 text-xs shadow-sm">
                  <div className="text-gray-500 mb-2">🤖 Agent 分配：</div>
                  <div className="space-y-2">
                    <div className="text-gray-400 text-[11px] italic">{streamState.agents.reasoning}</div>
                    <div className="space-y-1">
                      {streamState.agents.selected.map((agent, i) => {
                        const status = streamState.agents?.status[agent] || 'running';
                        const statusIcon = status === 'complete' ? '✓' : status === 'error' ? '✗' : '⏳';
                        const statusColor = status === 'complete' ? 'text-green-500' : status === 'error' ? 'text-red-500' : 'text-blue-400';
                        return (
                          <div key={i} className="flex items-center gap-2 text-gray-600">
                            <span className={statusColor}>{statusIcon}</span>
                            <span className="font-mono">{agent}</span>
                            {status === 'running' && <span className="text-blue-400 text-[10px] animate-pulse">分析中...</span>}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              )}
              {/* Tool calls */}
              {streamState.toolCalls.length > 0 && (
                <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl px-4 py-3 text-xs space-y-2 shadow-sm">
                  {streamState.toolCalls.map((tc, i) => (
                    <div key={i} className="flex items-start gap-2">
                      <span className="text-[var(--color-text)] font-mono">🔧 {tc.tool}</span>
                      <span className="text-gray-400 truncate flex-1">
                        {JSON.stringify(tc.args)}
                      </span>
                      {tc.result !== undefined && (
                        <span className="text-green-500">✓</span>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {/* Streaming answer */}
              {streamState.answerChunks.length > 0 && (
                <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl px-4 py-3 text-sm text-[var(--color-text)] shadow-sm">
                  <div className="prose prose-sm max-w-none prose-p:my-1 prose-headings:my-2 prose-ul:my-1 prose-li:my-0">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{streamState.answerChunks.join('')}</ReactMarkdown>
                  </div>
                  <span className="inline-block w-2 h-4 bg-[var(--color-primary-bg)] animate-pulse ml-1" />
                </div>
              )}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* quick assess bar */}
      <QuickAssess />

      {/* input */}
      <div className="px-4 pb-6 pt-2">
        {/* Mode selector — only show when chat is active */}
        {msgs.length > 0 && (
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs text-gray-400">模式：</span>
          <button
            onClick={() => setMode('react')}
            className={`text-xs px-3 py-1.5 rounded-md transition-colors min-h-[36px] inline-flex items-center ${
              mode === 'react'
                ? 'bg-[var(--color-primary-bg)] text-white'
                : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
            }`}
          >
            标准
          </button>
          <button
            onClick={() => setMode('plan-execute')}
            className={`text-xs px-3 py-1.5 rounded-md transition-colors min-h-[36px] inline-flex items-center ${
              mode === 'plan-execute'
                ? 'bg-[var(--color-primary-bg)] text-white'
                : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
            }`}
          >
            规划执行
          </button>
          <button
            onClick={() => setMode('multi-agent')}
            className={`text-xs px-3 py-1.5 rounded-md transition-colors min-h-[36px] inline-flex items-center ${
              mode === 'multi-agent'
                ? 'bg-[var(--color-primary-bg)] text-white'
                : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
            }`}
          >
            多Agent
          </button>
          <button
            onClick={() => setMode('sourcing')}
            className={`text-xs px-3 py-1.5 rounded-md transition-colors min-h-[36px] inline-flex items-center ${
              mode === 'sourcing'
                ? 'bg-[var(--color-primary-bg)] text-white'
                : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
            }`}
          >
            寻源
          </button>
          <span className="text-xs text-gray-400 ml-1">
            {mode === 'react' ? '逐步推理' : mode === 'plan-execute' ? '先规划后执行' : mode === 'multi-agent' ? '专业Agent协作' : '采购寻源'}
          </span>
        </div>
        )}
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

      {/* history panel */}
      <AnimatePresence>
        {showHistory && (
          <motion.div
            className="absolute top-12 right-4 w-80 bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl shadow-lg max-h-96 overflow-auto z-10"
            initial={{ opacity: 0, scale: 0.95, y: -8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: -8 }}
            transition={{ duration: 0.18, ease: [0.4, 0, 0.2, 1] }}
          >
            <div className="p-2">
              <p className="text-xs text-gray-400 px-3 py-2">会话历史</p>
              {sessions.map(s => (
                <div
                  key={s.sid}
                  onClick={() => switchSession(s.sid)}
                  className={`flex items-center justify-between px-3 py-2 rounded-xl cursor-pointer transition-colors ${
                    s.sid === activeSid ? 'bg-[var(--color-code-bg)]' : 'hover:bg-[var(--color-surface-hover)]'
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-[var(--color-text)] truncate">{s.title}</p>
                    <p className="text-[11px] text-gray-400">{new Date(s.updatedAt).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</p>
                  </div>
                  <button
                    onClick={(e) => deleteSession(s.sid, e)}
                    className="text-gray-300 hover:text-red-400 text-sm ml-2 shrink-0"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function QuickAssess() {
  const [name, setName] = useState('');
  const [data, setData] = useState<RiskResult | null>(null);
  const [open, setOpen] = useState(false);

  const assessMutation = useMutation({
    mutationFn: () => api.post<RiskResult>('/risk/assess', { company_name: name.trim() }),
    onSuccess: (res) => { setData(res); setOpen(true); },
    onError: () => setData(null),
  });

  const assess = () => {
    if (!name.trim() || assessMutation.isPending) return;
    assessMutation.mutate();
  };

  const rd = data?.risk_detail;
  const fin = data?.financial;

  return (
    <div className="px-4">
      <div className="border-t border-[var(--color-divider)] pt-3">
        <div className="flex gap-2 items-center">
          <input
            value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && assess()}
            placeholder="快速查看风险详情…"
            className="flex-1 text-xs border border-[var(--color-border)] rounded-lg px-3 py-1.5 focus:outline-none focus:border-[var(--color-border-focus)] placeholder-gray-300"
          />
          <button onClick={assess} disabled={assessMutation.isPending}
            className="text-xs bg-[var(--color-primary-bg)] text-white rounded-lg px-3 py-1.5 hover:bg-[var(--color-primary-hover)] disabled:opacity-40">
            {assessMutation.isPending ? '查询中' : '查看'}
          </button>
        </div>
      </div>

      <AnimatePresence>
        {open && data && (
          <motion.div
            className="mt-3 bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-4 shadow-sm"
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            transition={{ duration: 0.22, ease: [0.4, 0, 0.2, 1] }}
          >
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-semibold">{name}</span>
              <button onClick={() => { setOpen(false); setName(''); }} className="text-gray-400 hover:text-gray-600 text-sm">×</button>
            </div>

            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold text-white"
                style={{ background: getRiskColor(data.risk_score) }}>
                {data.risk_score}
              </div>
              <span className="text-sm font-semibold">{data.risk_level}</span>
            </div>

            <div className="grid grid-cols-4 gap-2 mb-3">
              {fin ? (
                <>
                  <MiniMetric label="营收增长" value={`${(fin.revenue_growth * 100).toFixed(1)}%`} />
                  <MiniMetric label="净利增长" value={`${(fin.net_profit_growth * 100).toFixed(1)}%`} />
                  <MiniMetric label="负债率" value={`${(fin.debt_ratio * 100).toFixed(1)}%`} />
                  <MiniMetric label="每股现金流" value={`¥${fin.cash_flow.toFixed(2)}`} />
                </>
              ) : <p className="text-xs text-gray-400 col-span-4">无财报数据</p>}
            </div>

            {rd && (
              <div className="grid grid-cols-3 gap-1 text-xs text-gray-500">
                <span>诉讼 {rd.lawsuit_count}</span>
                <span>被执行 {rd.executed_count}</span>
                <span>失信 {rd.dishonesty_count}</span>
                <span>经营异常 {rd.abnormal_operation_count}</span>
                <span>行政处罚 {rd.administrative_penalty_count}</span>
                <span>重大诉讼 {rd.major_lawsuit ? '⚠️是' : '✓否'}</span>
                <span>法人变更 {rd.legal_person_change_frequent ? '⚠️是' : '✓否'}</span>
                <span>对外担保 {rd.guarantee_count ?? 0}</span>
                <span>股权质押 {rd.pledge_count ?? 0}</span>
                <span>破产/清算 {rd.bankruptcy_count ?? 0}</span>
                <span>环保处罚 {rd.env_penalty_count ?? 0}</span>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-[var(--color-surface-hover)] rounded-lg p-1.5 text-center">
      <div className="text-xs font-semibold">{value}</div>
      <div className="text-[10px] text-gray-400">{label}</div>
    </div>
  );
}
