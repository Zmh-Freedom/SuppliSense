import { useState, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { getStoredUser, api } from '../api';
import { queryKeys } from '../query-keys';

type SettingsTab = 'account' | 'knowledge' | 'notifications';

export default function Settings() {
  const [tab, setTab] = useState<SettingsTab>('account');
  const tabs: { key: SettingsTab; label: string }[] = [
    { key: 'account', label: '账号安全' },
    { key: 'knowledge', label: '知识库' },
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
      {tab === 'knowledge' && <KnowledgeSection />}
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

/* ── Knowledge Section ── */

function KnowledgeSection() {
  const queryClient = useQueryClient();
  const [uploadResult, setUploadResult] = useState<{filename: string; chunks_added: number; doc_ids: string[]} | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<{content: string; metadata: Record<string, unknown>; distance: number; id: string}[]>([]);
  const [message, setMessage] = useState<{type: 'success' | 'error'; text: string} | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const statsQuery = useQuery({
    queryKey: queryKeys.knowledgeStats,
    queryFn: () => api.get<{total_documents: number; collection_name: string; embedding_model: string}>('/knowledge/stats'),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.upload<{filename: string; chunks_added: number; doc_ids: string[]}>('/knowledge/upload', file),
    onSuccess: (res) => {
      setUploadResult(res);
      queryClient.invalidateQueries({ queryKey: queryKeys.knowledgeStats });
      if (fileInputRef.current) fileInputRef.current.value = '';
    },
    onError: () => setMessage({type: 'error', text: '上传失败，请重试'}),
  });

  const searchMutation = useMutation({
    mutationFn: () => api.post<{results: {content: string; metadata: Record<string, unknown>; distance: number; id: string}[]}>('/knowledge/search', {query: searchQuery, n_results: 5}),
    onSuccess: (res) => setSearchResults(res.results || []),
    onError: () => setSearchResults([]),
  });

  const clearMutation = useMutation({
    mutationFn: () => api.delete('/knowledge/clear'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.knowledgeStats });
      setSearchResults([]);
      setMessage({type: 'success', text: '知识库已清空'});
      setConfirmClear(false);
    },
    onError: () => { setMessage({type: 'error', text: '清空失败，请重试'}); setConfirmClear(false); },
  });

  const stats = statsQuery.data ?? null;

  return (
    <div className="space-y-4">
      <AnimatePresence>
        {message && (
          <motion.div className={`rounded-lg px-4 py-2 text-sm ${message.type === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-600 border border-red-200'}`}
            initial={{opacity: 0, y: -8}} animate={{opacity: 1, y: 0}} exit={{opacity: 0, y: -8}} transition={{duration: 0.2}}>
            {message.text}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Stats */}
      <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-4 shadow-sm">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium">知识库统计</span>
          <button onClick={() => statsQuery.refetch()} className="text-xs text-gray-400 hover:text-gray-600">刷新</button>
        </div>
        {stats ? (
          <div className="grid grid-cols-3 gap-4 text-sm">
            <div><div className="text-2xl font-bold text-[var(--color-text)]">{stats.total_documents}</div><div className="text-gray-400 text-xs">文档分块</div></div>
            <div><div className="text-sm font-mono text-[var(--color-text)] truncate">{stats.collection_name}</div><div className="text-gray-400 text-xs">集合名称</div></div>
            <div><div className="text-sm font-mono text-[var(--color-text)] truncate">{stats.embedding_model}</div><div className="text-gray-400 text-xs">向量模型</div></div>
          </div>
        ) : <p className="text-sm text-gray-400">点击刷新加载统计信息</p>}
      </div>

      {/* Upload */}
      <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-4 shadow-sm">
        <h3 className="text-sm font-medium mb-2">上传文档</h3>
        <p className="text-xs text-gray-400 mb-3">支持格式：PDF、Word、Excel、TXT。文档将被分块并存入向量数据库。</p>
        <div className="flex items-center gap-2">
          <input ref={fileInputRef} type="file" accept=".pdf,.docx,.xlsx,.txt"
            onChange={(e) => { const file = e.target.files?.[0]; if (file) { setUploadResult(null); uploadMutation.mutate(file); }}}
            disabled={uploadMutation.isPending}
            className="flex-1 text-sm text-gray-500 file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-sm file:bg-[var(--color-code-bg)] file:text-[var(--color-text)] hover:file:bg-[var(--color-surface-selected)] file:cursor-pointer disabled:opacity-50" />
          {uploadMutation.isPending && <span className="text-xs text-gray-400">上传中...</span>}
        </div>
        {uploadResult && (
          <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded-lg text-sm">
            <p className="text-green-700 font-medium">✓ 上传成功</p>
            <p className="text-green-600 text-xs mt-1">{uploadResult.filename}：提取 {uploadResult.chunks_added} 个文本块</p>
          </div>
        )}
      </div>

      {/* Search */}
      <div className="bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-2xl p-4 shadow-sm">
        <h3 className="text-sm font-medium mb-2">检索测试</h3>
        <div className="flex items-center gap-2 mb-3">
          <input type="text" value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && searchMutation.mutate()}
            placeholder="输入查询关键词..."
            className="flex-1 text-sm border border-[var(--color-border)] rounded-lg px-3 py-1.5 focus:outline-none focus:border-[var(--color-border-focus)] placeholder-gray-300" />
          <button onClick={() => searchMutation.mutate()} disabled={searchMutation.isPending || !searchQuery.trim()}
            className="text-sm bg-[var(--color-primary-bg)] text-white rounded-lg px-4 py-1.5 hover:bg-[var(--color-primary-hover)] disabled:opacity-40">
            {searchMutation.isPending ? '检索中' : '检索'}
          </button>
        </div>
        {searchResults.length > 0 && (
          <div className="space-y-2 max-h-64 overflow-auto">
            {searchResults.map((result, i) => (
              <div key={result.id || i} className="p-3 bg-[var(--color-surface-hover)] rounded-lg text-sm">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-gray-400">{String(result.metadata?.source || 'Unknown')}{result.metadata?.page ? ` (第${String(result.metadata.page)}页)` : ''}</span>
                  <span className="text-xs text-gray-400">相似度: {((1 - (result.distance || 0)) * 100).toFixed(1)}%</span>
                </div>
                <p className="text-[var(--color-text)] text-xs leading-relaxed line-clamp-3">{result.content}</p>
              </div>
            ))}
          </div>
        )}
        {searchResults.length === 0 && searchQuery && !searchMutation.isPending && (
          <p className="text-sm text-gray-400 text-center py-4">未找到相关文档</p>
        )}
      </div>

      {/* Clear */}
      <div className="pt-2 border-t border-[var(--color-border)]">
        <AnimatePresence mode="wait">
          {confirmClear ? (
            <motion.div key="confirm" className="flex items-center gap-3"
              initial={{opacity: 0, x: -8}} animate={{opacity: 1, x: 0}} exit={{opacity: 0, x: 8}} transition={{duration: 0.18}}>
              <span className="text-sm text-red-600">确定要清空知识库吗？此操作不可恢复。</span>
              <button onClick={() => clearMutation.mutate()} disabled={clearMutation.isPending}
                className="text-sm bg-red-500 text-white rounded-lg px-3 py-1.5 hover:bg-red-600 disabled:opacity-50">
                {clearMutation.isPending ? '清空中…' : '确定'}
              </button>
              <button onClick={() => setConfirmClear(false)} className="text-sm text-gray-500 hover:text-gray-700">取消</button>
            </motion.div>
          ) : (
            <motion.button key="clear-btn" onClick={() => setConfirmClear(true)}
              className="text-sm text-red-500 hover:text-red-600 disabled:opacity-50"
              initial={{opacity: 0, x: 8}} animate={{opacity: 1, x: 0}} exit={{opacity: 0, x: -8}} transition={{duration: 0.18}}>
              清空知识库
            </motion.button>
          )}
        </AnimatePresence>
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

  const markAllReadMutation = useMutation({
    mutationFn: () => api.put('/notifications/read-all'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.notifications() }),
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
              className={`bg-[var(--color-surface)] glass-surface border rounded-xl p-4 transition-colors shadow-sm ${
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
