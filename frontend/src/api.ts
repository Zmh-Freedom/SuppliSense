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
export interface StreamCallbacks {
  onSession?: (sessionId: string) => void;
  onThinking?: (data: { iteration?: number; message: string }) => void;
  onPlan?: (data: { steps: Array<{ tool: string; args: Record<string, unknown>; parallel?: boolean }> }) => void;
  onAgentSelection?: (data: { agents: string[]; reasoning: string }) => void;
  onAgentStart?: (data: { agent: string; description: string }) => void;
  onAgentComplete?: (data: { agent: string; summary: string }) => void;
  onToolCall?: (data: { tool: string; args: Record<string, unknown> }) => void;
  onToolResult?: (data: { tool: string; result: unknown }) => void;
  onAnswerChunk?: (data: { text: string }) => void;
  onDone?: (data: { answer: string }) => void;
  onError?: (data: { message: string }) => void;
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

    if (!res.ok || !res.body) {
      throw new Error(`HTTP ${res.status}`);
    }

    const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let fullAnswer = '';
  let currentEvent = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7);
      } else if (line.startsWith('data: ')) {
        const dataStr = line.slice(6);
        try {
          const data = JSON.parse(dataStr);
          switch (currentEvent) {
            case 'session':
              callbacks.onSession?.(data);
              break;
            case 'thinking':
              callbacks.onThinking?.(data);
              break;
            case 'plan':
              callbacks.onPlan?.(data);
              break;
            case 'agent_selection':
              callbacks.onAgentSelection?.(data);
              break;
            case 'agent_start':
              callbacks.onAgentStart?.(data);
              break;
            case 'agent_complete':
              callbacks.onAgentComplete?.(data);
              break;
            case 'tool_call':
              callbacks.onToolCall?.(data);
              break;
            case 'tool_result':
              callbacks.onToolResult?.(data);
              break;
            case 'answer_chunk':
              fullAnswer += data.text;
              callbacks.onAnswerChunk?.(data);
              break;
            case 'done':
              callbacks.onDone?.(data);
              break;
            case 'error':
              callbacks.onError?.(data);
              break;
          }
          currentEvent = '';
        } catch {
          // Ignore parse errors
        }
      }
    }
  }

  return fullAnswer;
  } finally {
    clearTimeout(timeout);
  }
}
