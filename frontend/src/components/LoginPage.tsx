import { useState } from 'react';

interface LoginPageProps {
  onLogin: () => void;
}

export default function LoginPage({ onLogin }: LoginPageProps) {
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
      const res = await fetch('/auth/login/json', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password }),
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || '登录失败');
      }

      const data = await res.json();
      localStorage.setItem('auth_token', data.access_token);
      onLogin();
    } catch (err: any) {
      setError(err.message || '登录失败，请检查用户名和密码');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#f5f5f0]">
      <div className="w-full max-w-sm">
        <div className="bg-white rounded-2xl shadow-sm border border-[#e8e8e3] p-8">
          <div className="text-center mb-8">
            <h1 className="text-2xl font-bold text-[#333]">供应商风险分析</h1>
            <p className="text-sm text-gray-400 mt-2">请登录以继续</p>
          </div>

          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <label className="block text-sm text-[#555] mb-1.5">用户名</label>
              <input
                type="text"
                value={username}
                onChange={e => setUsername(e.target.value)}
                placeholder="请输入用户名"
                className="w-full border border-[#e8e8e3] rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-[#bbb] transition-colors"
                autoFocus
              />
            </div>

            <div>
              <label className="block text-sm text-[#555] mb-1.5">密码</label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="请输入密码"
                className="w-full border border-[#e8e8e3] rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-[#bbb] transition-colors"
              />
            </div>

            {error && (
              <p className="text-sm text-red-500 bg-red-50 rounded-lg px-3 py-2">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading || !username.trim() || !password}
              className="w-full bg-[#333] text-white rounded-xl py-2.5 text-sm font-medium hover:bg-[#555] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? '登录中…' : '登录'}
            </button>
          </form>

          <div className="mt-6 pt-4 border-t border-[#e8e8e3]">
            <p className="text-xs text-gray-400 text-center">
              默认管理员：admin / admin123
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
