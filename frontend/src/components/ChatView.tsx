import { useState, useEffect, useRef } from 'react';
import { useMutation } from '@tanstack/react-query';
import ReactMarkdown from 'react-markdown';
import { api, chatStream } from '../api';
import type { ChatMessage, RiskResult } from '../types';

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
  const [sessions, setSessions] = useState<Session[]>(loadSessions);
  const [activeSid, setActiveSid] = useState<string>(() => {
    const list = loadSessions();
    return list.length > 0 ? list[list.length - 1].sid : '';
  });
  const [input, setInput] = useState<string>(() => {
    try { return localStorage.getItem('chat_input') || ''; } catch { return ''; }
  });
  const [loading, setLoading] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [streamState, setStreamState] = useState<StreamState | null>(null);
  const [mode, setMode] = useState<'react' | 'plan-execute' | 'multi-agent'>('react');
  const bottomRef = useRef<HTMLDivElement>(null);
  const saveTimerRef = useRef<number | null>(null);

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

  // Timeout safeguard: auto-reset loading after 60s
  useEffect(() => {
    if (!loading) return;
    const timeout = setTimeout(() => {
      setLoading(false);
      setStreamState(null);
    }, 60000);
    return () => clearTimeout(timeout);
  }, [loading]);

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

  const send = async () => {
    const msg = input.trim();
    if (!msg || loading) return;
    setInput('');

    const sid = activeSid || crypto.randomUUID();
    if (!activeSid) setActiveSid(sid);

    const newMsgs: ChatMessage[] = [...msgs, { role: 'user', content: msg }];
    persist(sid, newMsgs);
    setLoading(true);
    setStreamState({ thinking: '', plan: null, agents: null, toolCalls: [], answerChunks: [] });

    try {
      await chatStream(msg, sid, {
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
          setStreamState(prev => prev ? {
            ...prev,
            answerChunks: [...prev.answerChunks, data.text]
          } : null);
        },
        onDone: (data) => {
          newMsgs.push({ role: 'assistant', content: data.answer });
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
      newMsgs.push({ role: 'assistant', content: '请求失败，请重试' });
      persist(sid, newMsgs);
      setStreamState(null);
      setLoading(false);
    }
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
          <p className="text-gray-300 text-center mt-20 text-lg">输入问题开始分析…</p>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`flex gap-3 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}>
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-semibold shrink-0 ${
              m.role === 'user' ? 'bg-[#333] text-white' : 'bg-[#e8e8e3] text-[#555]'
            }`}>
              {m.role === 'user' ? '你' : 'AI'}
            </div>
            <div className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm ${
              m.role === 'user'
                ? 'bg-[#f0f0eb] text-[#1a1a1a]'
                : 'bg-white border border-[#e8e8e3] text-[#2d2d2d]'
            }`}>
              <div className="prose prose-sm max-w-none prose-p:my-1 prose-headings:my-2 prose-ul:my-1 prose-li:my-0">
                <ReactMarkdown>{m.content}</ReactMarkdown>
              </div>
            </div>
          </div>
        ))}
        {loading && streamState && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-full bg-[#e8e8e3] flex items-center justify-center text-xs font-semibold text-[#555] shrink-0">AI</div>
            <div className="max-w-[80%] space-y-2">
              {/* Thinking indicator */}
              {streamState.thinking && streamState.answerChunks.length === 0 && (
                <div className="bg-white border border-[#e8e8e3] rounded-2xl px-4 py-3 text-sm text-gray-500 shadow-sm">
                  <span className="inline-block animate-pulse">{streamState.thinking}</span>
                </div>
              )}
              {/* Execution plan (Plan-and-Execute mode) */}
              {streamState.plan && streamState.plan.length > 0 && streamState.toolCalls.length === 0 && (
                <div className="bg-white border border-[#e8e8e3] rounded-2xl px-4 py-3 text-xs shadow-sm">
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
                <div className="bg-white border border-[#e8e8e3] rounded-2xl px-4 py-3 text-xs shadow-sm">
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
                <div className="bg-white border border-[#e8e8e3] rounded-2xl px-4 py-3 text-xs space-y-2 shadow-sm">
                  {streamState.toolCalls.map((tc, i) => (
                    <div key={i} className="flex items-start gap-2">
                      <span className="text-[#333] font-mono">🔧 {tc.tool}</span>
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
                <div className="bg-white border border-[#e8e8e3] rounded-2xl px-4 py-3 text-sm text-[#2d2d2d] shadow-sm">
                  <div className="prose prose-sm max-w-none prose-p:my-1 prose-headings:my-2 prose-ul:my-1 prose-li:my-0">
                    <ReactMarkdown>{streamState.answerChunks.join('')}</ReactMarkdown>
                  </div>
                  <span className="inline-block w-2 h-4 bg-[#333] animate-pulse ml-1" />
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
        {/* Mode selector */}
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs text-gray-400">模式：</span>
          <button
            onClick={() => setMode('react')}
            className={`text-xs px-3 py-1.5 rounded-md transition-colors min-h-[44px] ${
              mode === 'react'
                ? 'bg-[#333] text-white'
                : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
            }`}
          >
            标准
          </button>
          <button
            onClick={() => setMode('plan-execute')}
            className={`text-xs px-3 py-1.5 rounded-md transition-colors min-h-[44px] ${
              mode === 'plan-execute'
                ? 'bg-[#333] text-white'
                : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
            }`}
          >
            规划执行
          </button>
          <button
            onClick={() => setMode('multi-agent')}
            className={`text-xs px-3 py-1.5 rounded-md transition-colors min-h-[44px] ${
              mode === 'multi-agent'
                ? 'bg-[#333] text-white'
                : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
            }`}
          >
            多Agent
          </button>
          <span className="text-xs text-gray-400 ml-1">
            {mode === 'react' ? '逐步推理' : mode === 'plan-execute' ? '先规划后执行' : '专业Agent协作'}
          </span>
        </div>
        <div className="flex items-center gap-2 bg-white border border-[#e8e8e3] rounded-2xl px-4 py-1 focus-within:border-[#bbb] focus-within:shadow-sm transition-shadow">
          <input
            value={input}
            onChange={e => { setInput(e.target.value); }}
            onKeyDown={e => e.key === 'Enter' && send()}
            placeholder="输入问题，如：对比海康威视和宝钢的风险"
            className="flex-1 border-none outline-none py-2.5 text-sm bg-transparent placeholder-gray-300"
          />
          <button
            onClick={send}
            disabled={loading}
            className="bg-[#333] text-white rounded-xl px-4 py-2 text-sm hover:bg-[#555] disabled:opacity-40 shrink-0 transition-colors min-h-[44px]"
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
      {showHistory && (
        <div className="absolute top-12 right-4 w-80 bg-white border border-[#e8e8e3] rounded-2xl shadow-lg max-h-96 overflow-auto z-10">
          <div className="p-2">
            <p className="text-xs text-gray-400 px-3 py-2">会话历史</p>
            {sessions.map(s => (
              <div
                key={s.sid}
                onClick={() => switchSession(s.sid)}
                className={`flex items-center justify-between px-3 py-2 rounded-xl cursor-pointer transition-colors ${
                  s.sid === activeSid ? 'bg-[#f0f0eb]' : 'hover:bg-[#f9f9f5]'
                }`}
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-[#333] truncate">{s.title}</p>
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
        </div>
      )}
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
      <div className="border-t border-[#eee] pt-3">
        <div className="flex gap-2 items-center">
          <input
            value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && assess()}
            placeholder="快速查看风险详情…"
            className="flex-1 text-xs border border-[#e8e8e3] rounded-lg px-3 py-1.5 focus:outline-none focus:border-[#bbb] placeholder-gray-300"
          />
          <button onClick={assess} disabled={assessMutation.isPending}
            className="text-xs bg-[#333] text-white rounded-lg px-3 py-1.5 hover:bg-[#555] disabled:opacity-40">
            {assessMutation.isPending ? '查询中' : '查看'}
          </button>
        </div>
      </div>

      {open && data && (
        <div className="mt-3 bg-white border border-[#e8e8e3] rounded-2xl p-4 shadow-sm">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-semibold">{name}</span>
            <button onClick={() => { setOpen(false); setName(''); }} className="text-gray-400 hover:text-gray-600 text-sm">×</button>
          </div>

          <div className="flex items-center gap-3 mb-3">
            <div className="w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold text-white"
              style={{ background: data.risk_score <= 30 ? '#2d8c63' : data.risk_score <= 60 ? '#d4a040' : '#e06060' }}>
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
        </div>
      )}
    </div>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-[#f9f9f5] rounded-lg p-1.5 text-center">
      <div className="text-xs font-semibold">{value}</div>
      <div className="text-[10px] text-gray-400">{label}</div>
    </div>
  );
}
