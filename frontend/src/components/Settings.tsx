import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { getStoredUser, api } from '../api';

export default function Settings() {
  const user = getStoredUser();
  const [oldPw, setOldPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [msg, setMsg] = useState('');
  const [msgType, setMsgType] = useState<'success' | 'error'>('success');

  const changePwMutation = useMutation({
    mutationFn: () => api.put('/auth/me/password', { old_password: oldPw, new_password: newPw }),
    onSuccess: () => {
      setMsg('密码修改成功');
      setMsgType('success');
      setOldPw('');
      setNewPw('');
    },
    onError: (e: Error) => {
      setMsg(e.message || '修改失败');
      setMsgType('error');
    },
  });

  const changePassword = () => {
    if (!oldPw || !newPw) {
      setMsg('请填写所有字段');
      setMsgType('error');
      return;
    }
    if (newPw.length < 6) {
      setMsg('新密码至少 6 位');
      setMsgType('error');
      return;
    }
    setMsg('');
    changePwMutation.mutate();
  };

  return (
    <div className="max-w-md mx-auto py-6 px-4">
      <h2 className="text-lg font-semibold text-[var(--color-text)] mb-6">账户设置</h2>

      {/* User info */}
      <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 mb-6 shadow-sm">
        <h3 className="text-sm font-medium text-[var(--color-text)] mb-3">账户信息</h3>
        <div className="space-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-gray-400">用户名</span>
            <span className="text-[var(--color-text)]">{user?.username || '—'}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">角色</span>
            <span className="text-[var(--color-text)]">{user?.role || '—'}</span>
          </div>
        </div>
      </div>

      {/* Change password */}
      <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
        <h3 className="text-sm font-medium text-[var(--color-text)] mb-4">修改密码</h3>
        {msg && (
          <div className={`rounded-lg px-4 py-2 text-xs mb-4 ${
            msgType === 'success'
              ? 'bg-green-50 text-green-700 border border-green-200'
              : 'bg-red-50 text-red-600 border border-red-200'
          }`}>
            {msg}
          </div>
        )}
        <div className="space-y-3">
          <label htmlFor="old-password" className="sr-only">原密码</label>
          <input
            id="old-password"
            type="password"
            placeholder="原密码"
            value={oldPw}
            onChange={e => setOldPw(e.target.value)}
            className="w-full border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-text-muted)]"
          />
          <label htmlFor="new-password" className="sr-only">新密码</label>
          <input
            id="new-password"
            type="password"
            placeholder="新密码（至少 6 位）"
            value={newPw}
            onChange={e => setNewPw(e.target.value)}
            className="w-full border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-text-muted)]"
          />
          <button
            onClick={changePassword}
            disabled={changePwMutation.isPending}
            className="w-full bg-[var(--color-primary-bg)] text-white rounded-lg px-4 py-2 text-sm hover:bg-[var(--color-primary-hover)] disabled:opacity-50 transition-colors"
          >
            {changePwMutation.isPending ? '修改中…' : '修改密码'}
          </button>
        </div>
      </div>
    </div>
  );
}
