import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { wsClient } from '../websocket';
import { useWatchlist, useAlertHistory } from '../hooks';
import { queryKeys } from '../query-keys';

interface Props {
  onRefresh: () => void;
  onSelect?: (name: string) => void;
}

export default function Sidebar({ onRefresh, onSelect }: Props) {
  const queryClient = useQueryClient();
  const { companies: watchlist } = useWatchlist();
  const { data: alerts } = useAlertHistory();
  const alertCount = alerts?.length ?? 0;

  const [newName, setNewName] = useState('');
  const [hovered, setHovered] = useState<string | null>(null);
  const [toast, setToast] = useState('');

  const unreadQuery = useQuery({
    queryKey: queryKeys.unreadCount,
    queryFn: () => api.get<{ unread_count: number }>('/notifications?limit=1&read=false'),
    select: (d) => d.unread_count || 0,
  });
  const unreadCount = unreadQuery.data ?? 0;

  const addMutation = useMutation({
    mutationFn: (name: string) => api.post('/alert/watch', { company_name: name }),
    onSuccess: () => {
      setNewName('');
      queryClient.invalidateQueries({ queryKey: queryKeys.watchlist });
      onRefresh();
    },
    onError: () => setToast('操作失败，请重试'),
  });

  const removeMutation = useMutation({
    mutationFn: (name: string) => api.delete('/alert/watch', { company_name: name }),
    onSuccess: () => {
      setHovered(null);
      queryClient.invalidateQueries({ queryKey: queryKeys.watchlist });
      onRefresh();
    },
    onError: () => setToast('操作失败，请重试'),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.upload('/alert/watch/upload', file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.watchlist });
      onRefresh();
    },
    onError: () => setToast('操作失败，请重试'),
  });

  const checkAllMutation = useMutation({
    mutationFn: () => api.post('/alert/check-all'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.watchlist });
      queryClient.invalidateQueries({ queryKey: queryKeys.alertHistory });
    },
    onError: () => setToast('操作失败，请重试'),
  });

  const refreshAllMutation = useMutation({
    mutationFn: () => api.post('/alert/refresh-all'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.watchlist });
      queryClient.invalidateQueries({ queryKey: queryKeys.alertHistory });
    },
    onError: () => setToast('操作失败，请重试'),
  });

  const busy = checkAllMutation.isPending || refreshAllMutation.isPending;

  useEffect(() => {
    wsClient.connect();
    const unsubAlert = wsClient.on('alert_update', () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.watchlist });
      queryClient.invalidateQueries({ queryKey: queryKeys.alertHistory });
    });
    const unsubNotif = wsClient.on('notification', () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.unreadCount });
    });
    return () => {
      unsubAlert();
      unsubNotif();
      wsClient.disconnect();
    };
  }, [queryClient]);

  const add = () => {
    const name = newName.trim();
    if (!name || addMutation.isPending) return;
    addMutation.mutate(name);
  };

  const remove = (name: string) => {
    removeMutation.mutate(name);
  };

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    uploadMutation.mutate(file);
  };

  return (
    <aside className="w-64 h-screen border-r border-[#e8e8e3] bg-[#f5f5f0] flex flex-col text-sm relative">
      {/* header */}
      <div className="px-4 pt-4 pb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-[#555] tracking-wide">供应商分析</h2>
        <button className="relative" title="通知">
          <svg className="w-5 h-5 text-[#555] hover:text-[#333] transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6 6 0 10-12 0v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
          </svg>
          {unreadCount > 0 && (
            <span className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 text-white text-[10px] rounded-full flex items-center justify-center font-medium">
              {unreadCount > 9 ? '9+' : unreadCount}
            </span>
          )}
        </button>
      </div>

      {/* stats */}
      <div className="px-4 pb-3">
        <div className="flex gap-2">
          <div className="flex-1 bg-white rounded-xl border border-[#e8e8e3] px-3 py-2.5">
            <div className="text-xl font-bold text-[#333]">{watchlist.length}</div>
            <div className="text-[11px] text-gray-400 mt-0.5">监控中</div>
          </div>
          <div className="flex-1 bg-white rounded-xl border border-[#e8e8e3] px-3 py-2.5">
            <div className="text-xl font-bold text-[#333]">{alertCount}</div>
            <div className="text-[11px] text-gray-400 mt-0.5">告警</div>
          </div>
        </div>
      </div>

      {/* add form */}
      <div className="px-4 pb-2">
        <form onSubmit={e => { e.preventDefault(); add(); }} className="flex gap-1.5">
          <input
            value={newName}
            onChange={e => setNewName(e.target.value)}
            placeholder="添加企业…"
            className="flex-1 border border-[#e8e8e3] rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-[#bbb] bg-white placeholder-gray-300"
          />
          <button
            type="submit"
            disabled={addMutation.isPending || !newName.trim()}
            className="bg-[#333] text-white rounded-lg px-3 py-1.5 text-xs hover:bg-[#555] disabled:opacity-30 transition-opacity shrink-0"
          >
            {addMutation.isPending ? '...' : '添加'}
          </button>
        </form>
      </div>

      {/* excel import */}
      <div className="px-4 pb-3">
        <label className="flex items-center justify-center gap-1.5 text-[11px] text-gray-400 cursor-pointer hover:text-gray-500 transition-colors border border-dashed border-[#e0e0d8] rounded-lg py-1.5">
          <span>📎 Excel 批量导入</span>
          <input type="file" accept=".xlsx" onChange={handleFile} className="hidden" />
        </label>
      </div>

      {/* divider */}
      <div className="border-t border-[#e8e8e3] mx-4" />

      {/* list header */}
      <div className="px-4 pt-3 pb-1 flex justify-between items-center">
        <span className="text-[11px] text-gray-400 uppercase tracking-wide">
          监控清单 {watchlist.length > 0 && `(${watchlist.length})`}
        </span>
      </div>

      {/* list */}
      <div className="flex-1 overflow-auto px-2 pb-2">
        {watchlist.length === 0 ? (
          <p className="text-[11px] text-gray-300 text-center mt-6 px-4">暂无监控企业，输入名称添加</p>
        ) : (
          watchlist.map(c => (
            <div
              key={c}
              className="group flex items-center justify-between px-2 py-1.5 rounded-lg hover:bg-white/60 transition-colors cursor-pointer"
              onMouseEnter={() => setHovered(c)}
              onMouseLeave={() => setHovered(null)}
              onClick={() => onSelect?.(c)}
            >
              <span className="text-xs text-[#444] truncate flex-1">{c}</span>
              <button
                onClick={e => { e.stopPropagation(); remove(c); }}
                className={`text-gray-300 hover:text-red-400 text-sm leading-none transition-all shrink-0 ml-1 ${
                  hovered === c ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
                }`}
                title="移除"
              >
                ×
              </button>
            </div>
          ))
        )}
      </div>

      {/* actions */}
      <div className="border-t border-[#e8e8e3] px-4 py-3 space-y-2">
        <div className="flex gap-2">
          <button
            disabled={busy}
            onClick={() => checkAllMutation.mutate()}
            className="flex-1 border border-[#e8e8e3] bg-white rounded-lg py-1.5 text-[11px] text-[#555] hover:bg-[#f9f9f5] transition-colors disabled:opacity-50"
          >
            {busy ? '...' : '⚡ 免费巡检'}
          </button>
          <button
            disabled={busy}
            onClick={() => refreshAllMutation.mutate()}
            className="flex-1 border border-[#e8e8e3] bg-white rounded-lg py-1.5 text-[11px] text-[#555] hover:bg-[#f9f9f5] transition-colors disabled:opacity-50"
          >
            {busy ? '...' : '🔄 付费刷新'}
          </button>
        </div>
        <p className="text-[10px] text-gray-300 text-center">每日 9:00 免费 · 周一 9:00 付费</p>
      </div>

      {toast && (
        <div className="absolute bottom-4 left-4 right-4 bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-xs text-red-600 z-20">
          {toast}
          <button onClick={() => setToast('')} className="float-right text-red-400 hover:text-red-600">&times;</button>
        </div>
      )}
    </aside>
  );
}
