const TOKEN_KEY = 'auth_token';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export function isAuthenticated(): boolean {
  return !!getToken();
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    ...(options?.headers as Record<string, string> || {}),
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(path, { ...options, headers });

  if (res.status === 401) {
    clearToken();
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
  get: <T>(path: string, params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return request<T>(`${path}${qs}`);
  },

  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }),

  delete: <T>(path: string, params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return request<T>(`${path}${qs}`, { method: 'DELETE' });
  },

  upload: <T>(path: string, file: File) => {
    const form = new FormData();
    form.append('file', file);

    const token = getToken();
    const headers: Record<string, string> = {};
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    return fetch(path, { method: 'POST', body: form, headers }).then(r => {
      if (r.status === 401) {
        clearToken();
        window.location.reload();
        throw new Error('登录已过期');
      }
      return r.json();
    }) as Promise<T>;
  },
};

// ---- Streaming (SSE) ----
export interface StreamCallbacks {
  onSession?: (sessionId: string) => void;
  onThinking?: (data: { iteration?: number; message: string }) => void;
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
): Promise<string> {
  const token = getToken();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers,
    body: JSON.stringify({ message, session_id: sessionId }),
  });

  if (res.status === 401) {
    clearToken();
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

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // Parse SSE events
    const lines = buffer.split('\n');
    buffer = lines.pop() || ''; // Keep incomplete line in buffer

    let currentEvent = '';
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
        } catch {
          // Ignore parse errors
        }
      }
    }
  }

  return fullAnswer;
}
