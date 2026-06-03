async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${res.status}`);
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
    return fetch(path, { method: 'POST', body: form }).then(r => r.json()) as Promise<T>;
  },
};
