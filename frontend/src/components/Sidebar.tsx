import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { wsClient } from '../websocket';
import { useWatchlist } from '../hooks';
import { queryKeys } from '../query-keys';

interface Props {
  onSelect?: (name: string) => void;
  onClose?: () => void;
}

export default function Sidebar({ onSelect, onClose }: Props) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { companies: watchlist } = useWatchlist();
  const [hovered, setHovered] = useState<string | null>(null);

  const unreadQuery = useQuery({
    queryKey: queryKeys.unreadCount,
    queryFn: () => api.get<{ unread_count: number }>('/notifications?limit=1&read=false'),
    select: (d) => d.unread_count || 0,
  });
  const unreadCount = unreadQuery.data ?? 0;

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

  return (
    <aside className="w-56 h-screen border-r border-[var(--color-border)] bg-[var(--color-page-bg)] glass-surface flex flex-col text-sm relative">
      {/* mobile close */}
      {onClose && (
        <button className="md:hidden p-2 ml-auto text-gray-400 hover:text-gray-600" onClick={onClose} aria-label="关闭菜单">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M5 5l10 10M15 5l-10 10" />
          </svg>
        </button>
      )}

      {/* header */}
      <div className="px-4 pt-4 pb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)] tracking-wide">供应商分析</h2>
        <button
          className="relative p-2 min-w-[44px] min-h-[44px] flex items-center justify-center"
          title="通知"
          aria-label="通知"
          onClick={() => navigate('/settings')}
        >
          <svg className="w-5 h-5 text-[var(--color-text-secondary)] hover:text-[var(--color-text)] transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6 6 0 10-12 0v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
          </svg>
          {unreadCount > 0 && (
            <span className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 text-white text-[10px] rounded-full flex items-center justify-center font-medium">
              {unreadCount > 9 ? '9+' : unreadCount}
            </span>
          )}
        </button>
      </div>

      {/* divider */}
      <div className="border-t border-[var(--color-border)] mx-4" />

      {/* list header */}
      <div className="px-4 pt-3 pb-1">
        <span className="text-[11px] text-gray-400 uppercase tracking-wide">
          监控清单 {watchlist.length > 0 && `(${watchlist.length})`}
        </span>
      </div>

      {/* company list */}
      <div className="flex-1 overflow-auto px-2 pb-2">
        {watchlist.length === 0 ? (
          <p className="text-[11px] text-gray-300 text-center mt-6 px-4">暂无监控企业</p>
        ) : (
          watchlist.map(c => (
            <div
              key={c}
              className="group flex items-center justify-between px-2 py-2 rounded-lg hover:bg-[var(--color-surface-hover)] transition-colors cursor-pointer min-h-[44px]"
              onMouseEnter={() => setHovered(c)}
              onMouseLeave={() => setHovered(null)}
              onClick={() => onSelect?.(c)}
            >
              <span className="text-xs text-[var(--color-text)] truncate flex-1">{c}</span>
              {hovered === c && (
                <button
                  onClick={e => { e.stopPropagation(); navigate('/chat'); }}
                  className="text-xs text-[var(--color-primary-bg)] hover:bg-[var(--color-primary-bg)]/10 rounded-md px-2 py-1 transition-colors shrink-0 ml-1"
                  title="Agent 分析"
                >
                  分析
                </button>
              )}
            </div>
          ))
        )}
      </div>

      {/* footer hint */}
      <div className="border-t border-[var(--color-border)] px-4 py-2">
        <p className="text-[10px] text-gray-300 text-center">监控管理请前往看板页</p>
      </div>
    </aside>
  );
}
