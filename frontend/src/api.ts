const SESSION_KEY = 'session';
const API_BASE = '/api/v1';

export function getStoredUser(): { username: string; role: string } | null {
  try {
    const data = localStorage.getItem(SESSION_KEY);
    return data ? JSON.parse(data) : null;
  } catch {
    return null;
  }
}

export function setStoredUser(username: string, role: string): void {
  localStorage.setItem(SESSION_KEY, JSON.stringify({ username, role }));
}

export function clearStoredUser(): void {
  localStorage.removeItem(SESSION_KEY);
}

export function isAuthenticated(): boolean {
  return !!getStoredUser();
}

async function refreshAccessToken(): Promise<boolean> {
  const stored = getStoredUser();
  if (!stored) return false;
  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // refresh token is stored in httpOnly cookie; the backend reads it from the cookie, not the request body
      body: JSON.stringify({}),
    });
    return res.ok;
  } catch {
    return false;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: 'same-origin',
  });

  if (res.status === 401) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      const retryRes = await fetch(`${API_BASE}${path}`, {
        ...options,
        credentials: 'same-origin',
      });
      if (retryRes.ok) return retryRes.json();
    }
    clearStoredUser();
    window.location.reload();
    throw new Error('登录已过期，请重新登录');
  }

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `${res.status}`);
  }

  return res.json();
}

export const api = {
  get: <T>(path: string, params?: Record<string, string>, signal?: AbortSignal) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return request<T>(`${path}${qs}`, { signal });
  },

  post: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    request<T>(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
      signal,
    }),

  put: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    request<T>(path, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
      signal,
    }),

  delete: <T>(path: string, params?: Record<string, string>, signal?: AbortSignal) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return request<T>(`${path}${qs}`, { method: 'DELETE', signal });
  },

  upload: <T>(path: string, file: File, signal?: AbortSignal) => {
    const form = new FormData();
    form.append('file', file);
    return request<T>(path, { method: 'POST', body: form, signal });
  },
};

// ---- Streaming (SSE) ----
export interface ApprovalData {
  message: string;
  tool: string;
  args: Record<string, unknown>;
  session_id: string;
  status?: 'pending' | 'submitting' | 'approved' | 'rejected' | 'failed';
}

export interface StreamCallbacks {
  onSession?: (sessionId: string) => void;
  onRun?: (data: { run_id: string; turn_id?: string }) => void;
  onEventId?: (eventId: number) => void;
  onThinking?: (data: { iteration?: number; message: string }) => void;
  onWorkflowStatus?: (data: {
    status: import('./types').AgentWorkflowLifecycle | string;
    stage?: string;
    message: string;
    target_suppliers?: string[];
    sources?: string[];
    evidence_status?: string;
    loop_exit_reason?: string;
    run_id?: string;
    tool_call_count?: number;
    completed_tool_count?: number;
  }) => void;
  onPlan?: (data: { steps: Array<{ tool: string; args: Record<string, unknown>; parallel?: boolean }> }) => void;
  onAgentSelection?: (data: { agents: string[]; reasoning: string }) => void;
  onAgentStart?: (data: { agent: string; description: string }) => void;
  onAgentComplete?: (data: { agent: string; summary: string }) => void;
  onToolCall?: (data: { tool: string; args: Record<string, unknown>; task_id?: string }) => void;
  onToolResult?: (data: { tool: string; result: unknown; task_id?: string }) => void;
  onAnswerChunk?: (data: { text: string }) => void;
  onDone?: (data: { answer: string; status?: string; run_id?: string }) => void;
  onAgentAnswer?: (data: import('./types').AgentAnswer) => void;
  onEvidence?: (data: { records: import('./types').AgentEvidenceRecord[]; coverage?: Record<string, unknown> }) => void;
  onError?: (data: { message: string }) => void;
  onClarification?: (data: { message: string; missing: string[]; status?: string; stage?: string }) => void;
  onApprovalRequired?: (data: ApprovalData) => void;
  onChartData?: (data: import('./types').ChartData) => void;
  onReferences?: (data: { items: import('./types').SupplierReference[] }) => void;
}

async function _parseSSEStream(
  res: Response,
  callbacks: StreamCallbacks,
): Promise<string> {
  if (!res.body) throw new Error('No response body');

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let fullAnswer = '';
  let currentEvent = '';
  let currentEventId: number | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (line.startsWith('id: ')) {
        currentEventId = Number(line.slice(4));
      } else if (line.startsWith('event: ')) {
        currentEvent = line.slice(7);
      } else if (line.startsWith('data: ')) {
        const dataStr = line.slice(6);
        try {
          const data = JSON.parse(dataStr);
          dispatchStreamEvent(currentEvent, data, callbacks, (text) => {
            fullAnswer += text;
          });
          if (currentEventId !== null && Number.isFinite(currentEventId)) callbacks.onEventId?.(currentEventId);
          currentEvent = '';
          currentEventId = null;
        } catch {
          // Ignore parse errors
        }
      }
    }
  }

  return fullAnswer;
}

/** Dispatch a persisted or live chat SSE event through the shared UI contract. */
export function dispatchStreamEvent(
  eventType: string,
  data: unknown,
  callbacks: StreamCallbacks,
  onAnswerChunk?: (text: string) => void,
): void {
  switch (eventType) {
    case 'session': callbacks.onSession?.(typeof data === 'string' ? data : (data as { session_id: string }).session_id); break;
    case 'run': callbacks.onRun?.(data as { run_id: string }); break;
    case 'thinking': callbacks.onThinking?.(data as { message: string }); break;
    case 'workflow_status': callbacks.onWorkflowStatus?.(data as Parameters<NonNullable<StreamCallbacks['onWorkflowStatus']>>[0]); break;
    case 'plan': callbacks.onPlan?.(data as Parameters<NonNullable<StreamCallbacks['onPlan']>>[0]); break;
    case 'agent_selection': callbacks.onAgentSelection?.(data as Parameters<NonNullable<StreamCallbacks['onAgentSelection']>>[0]); break;
    case 'agent_start': callbacks.onAgentStart?.(data as Parameters<NonNullable<StreamCallbacks['onAgentStart']>>[0]); break;
    case 'agent_complete': callbacks.onAgentComplete?.(data as Parameters<NonNullable<StreamCallbacks['onAgentComplete']>>[0]); break;
    case 'tool_call': callbacks.onToolCall?.(data as Parameters<NonNullable<StreamCallbacks['onToolCall']>>[0]); break;
    case 'tool_result': callbacks.onToolResult?.(data as Parameters<NonNullable<StreamCallbacks['onToolResult']>>[0]); break;
    case 'answer_chunk': {
      const chunk = data as { text: string };
      onAnswerChunk?.(chunk.text);
      callbacks.onAnswerChunk?.(chunk);
      break;
    }
    case 'done': callbacks.onDone?.(data as Parameters<NonNullable<StreamCallbacks['onDone']>>[0]); break;
    case 'agent_answer': callbacks.onAgentAnswer?.(data as Parameters<NonNullable<StreamCallbacks['onAgentAnswer']>>[0]); break;
    case 'evidence': callbacks.onEvidence?.(data as Parameters<NonNullable<StreamCallbacks['onEvidence']>>[0]); break;
    case 'error': callbacks.onError?.(data as { message: string }); break;
    case 'clarification': callbacks.onClarification?.(data as Parameters<NonNullable<StreamCallbacks['onClarification']>>[0]); break;
    case 'approval_required': callbacks.onApprovalRequired?.(data as ApprovalData); break;
    case 'chart_data': callbacks.onChartData?.(data as import('./types').ChartData); break;
    case 'references': callbacks.onReferences?.(data as { items: import('./types').SupplierReference[] }); break;
  }
}

export async function chatStream(
  message: string,
  sessionId: string,
  callbacks: StreamCallbacks,
  mode: string = 'auto',
): Promise<string> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 120_000);

  try {
    const res = await fetch(`${API_BASE}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId, mode }),
      credentials: 'same-origin',
      signal: controller.signal,
    });

    if (res.status === 401) {
      clearStoredUser();
      window.location.reload();
      throw new Error('登录已过期，请重新登录');
    }

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

    let receivedTerminalEvent = false;
    const trackedCallbacks: StreamCallbacks = {
      ...callbacks,
      onDone: (data) => {
        receivedTerminalEvent = true;
        callbacks.onDone?.(data);
      },
      onError: (data) => {
        receivedTerminalEvent = true;
        callbacks.onError?.(data);
      },
      onClarification: (data) => {
        receivedTerminalEvent = true;
        callbacks.onClarification?.(data);
      },
    };
    const answer = await _parseSSEStream(res, trackedCallbacks);
    if (!receivedTerminalEvent) throw new Error('SSE 流在收到最终结果前断开');
    return answer;
  } finally {
    clearTimeout(timeout);
  }
}

export async function resumeChat(
  sessionId: string,
  approved: boolean,
  callbacks: StreamCallbacks,
): Promise<string> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 120_000);

  try {
    const res = await fetch(`${API_BASE}/chat/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, approved }),
      credentials: 'same-origin',
      signal: controller.signal,
    });

    if (res.status === 401) {
      clearStoredUser();
      window.location.reload();
      throw new Error('登录已过期，请重新登录');
    }

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

    return await _parseSSEStream(res, callbacks);
  } finally {
    clearTimeout(timeout);
  }
}

export interface AgentRunEventStreamCallbacks {
  onEvent?: (event: import('./types').AgentRunEvent) => void;
  onDone?: (event: import('./types').AgentRunEvent) => void;
  onError?: (error: Error) => void;
}

/** Read one durable agent-run event replay, resuming after the supplied event cursor. */
export async function agentRunEventStream(
  runId: string,
  lastEventId: number | null,
  callbacks: AgentRunEventStreamCallbacks,
  signal?: AbortSignal,
  pathPrefix = '/agent-runs',
): Promise<void> {
  const headers: HeadersInit = {};
  if (lastEventId !== null) headers['Last-Event-ID'] = String(lastEventId);

  try {
    const res = await fetch(`${API_BASE}${pathPrefix}/${encodeURIComponent(runId)}/events`, {
      headers,
      credentials: 'same-origin',
      signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    if (!res.body) return;

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let eventId: number | null = null;
    let eventType = 'message';
    let dataLines: string[] = [];
    let receivedTerminalEvent = false;

    const dispatch = () => {
      if (eventId === null || dataLines.length === 0) return;
      try {
        const event = { eventId, eventType, data: JSON.parse(dataLines.join('\n')) };
        callbacks.onEvent?.(event);
        if (eventType === 'done') {
          receivedTerminalEvent = true;
          callbacks.onDone?.(event);
        }
      } catch {
        // A malformed event must not break later durable event replays.
      } finally {
        eventId = null;
        eventType = 'message';
        dataLines = [];
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        if (!line) {
          dispatch();
        } else if (line.startsWith('id:')) {
          eventId = Number(line.slice(3).trim());
        } else if (line.startsWith('event:')) {
          eventType = line.slice(6).trim();
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trimStart());
        }
      }
    }
    dispatch();
    if (!signal?.aborted && !receivedTerminalEvent) {
      callbacks.onError?.(new Error('事件流连接已断开'));
    }
  } catch (error) {
    if (!signal?.aborted) callbacks.onError?.(error instanceof Error ? error : new Error('事件流连接失败'));
  }
}

/** Read a chat Harness replay using the same durable event/cursor protocol. */
export async function chatRunEventStream(
  runId: string,
  lastEventId: number | null,
  callbacks: AgentRunEventStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  return agentRunEventStream(runId, lastEventId, callbacks, signal, '/chat/runs');
}
