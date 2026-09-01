import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getStoredUser, api } from '../api';
import { queryKeys } from '../query-keys';

type SettingsTab = 'account' | 'notifications';

export default function Settings() {
  const [tab, setTab] = useState<SettingsTab>('account');
  const tabs: { key: SettingsTab; label: string }[] = [
    { key: 'account', label: '账号安全' },
    { key: 'notifications', label: '通知中心' },
  ];

  return (
    <div className="max-w-3xl mx-auto py-6 px-4">
      <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">设置</h2>

      {/* sub tabs */}
      <div className="flex gap-1 mb-6 border-b border-[var(--color-border)]">
        {tabs.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2.5 text-sm rounded-t-lg transition-colors min-h-[44px] -mb-[1px] ${
              tab === t.key
                ? 'text-[var(--color-primary-bg)] border-b-2 border-[var(--color-primary-bg)] font-medium'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'account' && <AccountSection />}
      {tab === 'notifications' && <NotificationSection />}
    </div>
  );
}

/* ── Account Section ── */

function AccountSection() {
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
    if (!oldPw || !newPw) { setMsg('请填写所有字段'); setMsgType('error'); return; }
    if (newPw.length < 6) { setMsg('新密码至少 6 位'); setMsgType('error'); return; }
    setMsg('');
    changePwMutation.mutate();
  };

  return (
    <div className="space-y-6">
      <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
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

      <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-5 shadow-sm">
        <h3 className="text-sm font-medium text-[var(--color-text)] mb-4">修改密码</h3>
        {msg && (
          <div className={`rounded-lg px-4 py-2 text-xs mb-4 ${msgType === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-600 border border-red-200'}`}>
            {msg}
          </div>
        )}
        <div className="space-y-3">
          <label htmlFor="old-password" className="sr-only">原密码</label>
          <input id="old-password" type="password" placeholder="原密码" value={oldPw} onChange={e => setOldPw(e.target.value)}
            className="w-full border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-text-muted)]" />
          <label htmlFor="new-password" className="sr-only">新密码</label>
          <input id="new-password" type="password" placeholder="新密码（至少 6 位）" value={newPw} onChange={e => setNewPw(e.target.value)}
            className="w-full border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-text-muted)]" />
          <button onClick={changePassword} disabled={changePwMutation.isPending}
            className="w-full bg-[var(--color-primary-bg)] text-white rounded-lg px-4 py-2 text-sm hover:bg-[var(--color-primary-hover)] disabled:opacity-50 transition-colors">
            {changePwMutation.isPending ? '修改中…' : '修改密码'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Notification Section ── */

function NotificationSection() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.notifications(100),
    queryFn: () => api.get<{notifications: {_id: string; type: string; title: string; message: string; company_name?: string; read: boolean; created_at: string}[]}>('/notifications?limit=100'),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: queryKeys.notifications() });

  const markAllReadMutation = useMutation({
    mutationFn: () => api.put('/notifications/read-all'),
    onSuccess: () => invalidate(),
  });

  const markReadMutation = useMutation({
    mutationFn: (id: string) => api.put(`/notifications/${id}/read`),
    onSuccess: () => invalidate(),
  });

  const notifs = data?.notifications ?? [];

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <p className="text-xs text-gray-400">{notifs.filter(n => !n.read).length} 条未读 · 共 {notifs.length} 条</p>
        <button onClick={() => markAllReadMutation.mutate()}
          className="text-xs text-[var(--color-text)] hover:underline disabled:opacity-50"
          disabled={notifs.every(n => n.read) || markAllReadMutation.isPending}>
          全部标为已读
        </button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-5 h-5 border-2 border-[var(--color-primary-bg)] border-t-transparent rounded-full animate-spin" />
        </div>
      ) : notifs.length === 0 ? (
        <p className="text-sm text-gray-400 text-center py-12">暂无通知</p>
      ) : (
        <div className="space-y-2">
          {notifs.map(n => (
            <div key={n._id}
              onClick={() => { if (!n.read) markReadMutation.mutate(n._id); }}
              className={`bg-[var(--color-surface)] glass-surface border rounded-xl p-4 transition-colors shadow-sm cursor-pointer ${
                n.read ? 'border-[var(--color-border)]' : 'border-[var(--color-border-hover)] bg-[var(--color-page-bg)]'
              }`}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className={`text-sm truncate ${n.read ? 'text-gray-500' : 'text-[var(--color-text)] font-medium'}`}>{n.title}</p>
                  <p className="text-xs text-gray-400 mt-0.5">{n.message}</p>
                  {n.company_name && <p className="text-[10px] text-gray-300 mt-0.5">关联企业：{n.company_name}</p>}
                </div>
                <div className="text-right shrink-0">
                  <p className="text-[10px] text-gray-300 whitespace-nowrap">{n.created_at?.slice(0, 16).replace('T', ' ')}</p>
                  {!n.read && <span className="text-[10px] text-[var(--color-text)]">● 未读</span>}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
