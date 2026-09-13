import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { setStoredUser } from '../api';

export default function LoginPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;

    setLoading(true);
    setError('');

    try {
      const res = await fetch('/api/v1/auth/login/json', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password }),
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || '登录失败');
      }

      const data = await res.json();
      setStoredUser(data.username, data.role);
      navigate('/', { replace: true });
    } catch (err: unknown) {
      const message = err instanceof Error && err.message
        ? err.message
        : '登录失败，请检查用户名和密码';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--color-page-bg)]">
      <div className="w-full max-w-sm">
        <div className="bg-[var(--color-surface)] glass-surface rounded-2xl shadow-md border border-[var(--color-border)] p-8">
          <div className="text-center mb-8">
            <h1 className="text-2xl font-bold text-[var(--color-text)]">SuppliSense</h1>
            <p className="text-sm text-gray-400 mt-2">AI-Powered Sourcing &amp; Risk Intelligence</p>
          </div>

          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <label htmlFor="login-username" className="block text-sm text-[var(--color-text-secondary)] mb-1.5">用户名</label>
              <input
                id="login-username"
                name="username"
                type="text"
                value={username}
                onChange={e => setUsername(e.target.value)}
                placeholder="请输入用户名…"
                autoComplete="username"
                className="w-full rounded-xl border border-[var(--color-border)] px-4 py-2.5 text-sm transition-colors focus:border-[var(--color-border-focus)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
                autoFocus
              />
            </div>

            <div>
              <label htmlFor="login-password" className="block text-sm text-[var(--color-text-secondary)] mb-1.5">密码</label>
              <input
                id="login-password"
                name="password"
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="请输入密码…"
                autoComplete="current-password"
                className="w-full rounded-xl border border-[var(--color-border)] px-4 py-2.5 text-sm transition-colors focus:border-[var(--color-border-focus)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
              />
            </div>

            {error && (
              <p role="alert" aria-live="polite" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-500">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading || !username.trim() || !password}
              className="min-h-[44px] w-full rounded-xl bg-[var(--color-primary-bg)] py-2.5 text-sm font-medium text-white transition-colors hover:bg-[var(--color-primary-hover)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? '登录中…' : '登录'}
            </button>
          </form>

          <div className="mt-6 pt-4 border-t border-[var(--color-border)]">
            <p className="text-xs text-gray-400 text-center">
              请联系管理员获取账号
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
