import { useRef, useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate, useLocation } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { useAlertHistory } from '../hooks';
import { api } from '../api';
import { queryKeys } from '../query-keys';
import { TAB_ROUTES } from '../routes';
import ThemeSwitcher from './ThemeSwitcher';
import type { AlertDoc } from '../types';

interface Props {
  onClose?: () => void;
}

function NavIcon({ name, className }: { name: string; className?: string }) {
  const cls = className || 'w-5 h-5';
  switch (name) {
    case 'dashboard':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>;
    case 'assess':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>;
    case 'agent':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2l2.4 7.2h7.6l-6 4.8 2.4 7.2-6.4-4.8-6.4 4.8 2.4-7.2-6-4.8h7.6z"/></svg>;
    case 'contagion':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><path d="M12 7v5"/><path d="M9 14l-3 4"/><path d="M15 14l3 4"/></svg>;
    case 'settings':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>;
    case 'sourcing':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/></svg>;
    default:
      return null;
  }
}

/* ---- inline alert bell for sidebar ---- */

function AlertBell() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data } = useAlertHistory();
  const alertList = data?.alerts ?? [];
  const unreadCount = data?.unread_count ?? 0;
  const [open, setOpen] = useState(false);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (btnRef.current?.contains(target)) return;
      if (panelRef.current && !panelRef.current.contains(target)) { setOpen(false); setExpandedIdx(null); }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const toggle = () => {
    if (!open && btnRef.current) {
      const rect = btnRef.current.getBoundingClientRect();
      setPos({ top: rect.top, left: rect.right + 8 });
    }
    setOpen(!open);
    setExpandedIdx(null);
  };

  const invalidate = () => queryClient.invalidateQueries({ queryKey: queryKeys.alertHistory });

  const markReadMutation = useMutation({
    mutationFn: (alertId: string) => api.put(`/alert/history/${alertId}/read`),
    onSuccess: () => invalidate(),
  });

  const readAllMutation = useMutation({
    mutationFn: () => api.put('/alert/history/read-all'),
    onSuccess: () => invalidate(),
  });

  const handleAlertClick = (doc: AlertDoc, i: number) => {
    setExpandedIdx(expandedIdx === i ? null : i);
    if (!doc.read) {
      markReadMutation.mutate(doc._id);
    }
  };

  const dropdown = (
    <AnimatePresence>
      {open && (
        <motion.div
          ref={panelRef}
          className="fixed w-[380px] bg-white border border-[var(--color-border)] rounded-2xl shadow-xl z-50 overflow-hidden"
          style={{ top: pos.top, left: pos.left }}
          initial={{ opacity: 0, scale: 0.95, x: -8 }}
          animate={{ opacity: 1, scale: 1, x: 0 }}
          exit={{ opacity: 0, scale: 0.95, x: -8 }}
          transition={{ duration: 0.18, ease: [0.4, 0, 0.2, 1] }}
        >
          <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)]">
            <span className="text-sm font-medium text-[var(--color-text)]">告警通知</span>
            {unreadCount > 0 && (
              <button onClick={() => readAllMutation.mutate()} disabled={readAllMutation.isPending}
                className="text-xs text-[var(--color-primary-bg)] hover:opacity-80 disabled:opacity-50">
                {readAllMutation.isPending ? '标记中…' : '一键已读'}
              </button>
            )}
          </div>
          <div className="max-h-[70vh] overflow-auto">
            {alertList.length === 0 ? (
              <p className="text-sm text-gray-400 text-center py-8">暂无告警</p>
            ) : (
              alertList.slice(0, 50).map((doc: AlertDoc, i: number) => {
                const isCritical = doc.severity === 'critical';
                const isExpanded = expandedIdx === i;
                const isUnread = !doc.read;
                return (
                  <div key={doc._id}
                    onClick={() => handleAlertClick(doc, i)}
                    className={`px-4 py-3 border-b border-[var(--color-border)] last:border-b-0 hover:bg-[var(--color-surface-hover)] transition-colors cursor-pointer ${isExpanded ? 'bg-[var(--color-surface-hover)]' : ''}`}>
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        {isUnread && <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-[var(--color-primary-bg)]" />}
                        <span className={`text-sm truncate ${isUnread ? 'font-semibold text-[var(--color-text)]' : 'font-medium text-[var(--color-text)]'}`}>{doc.company_name}</span>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-[10px] text-gray-400">{doc.created_at.slice(5, 16).replace('T', ' ')}</span>
                        <span className={`shrink-0 w-1.5 h-1.5 rounded-full ${isCritical ? 'bg-red-500' : 'bg-amber-500'}`} />
                      </div>
                    </div>
                    <p className={`text-xs text-gray-500 mt-1 ${isExpanded ? '' : 'line-clamp-1'}`}>
                      {doc.changes.map(c => `${c.field} ${c.old} → ${c.new}`).join(' · ')}
                    </p>
                    {isExpanded && (
                      <div className="mt-2 pt-2 border-t border-[var(--color-border)] flex items-center gap-2">
                        <button onClick={(e) => { e.stopPropagation(); setOpen(false); setExpandedIdx(null); navigate(`/assess/${encodeURIComponent(doc.company_name)}`); }}
                          className="text-xs text-[var(--color-primary-bg)] hover:bg-[var(--color-primary-bg)]/10 rounded-md px-2 py-1 transition-colors">
                          查看详情
                        </button>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );

  return (
    <>
      <button ref={btnRef} onClick={toggle}
        className="relative p-2 rounded-lg hover:bg-[var(--color-surface-hover)] transition-colors min-w-[44px] min-h-[44px] flex items-center justify-center"
        aria-label={`告警通知${unreadCount > 0 ? `，${unreadCount} 条未读` : ''}`}
      >
        <svg className="w-5 h-5 text-gray-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>
        </svg>
        {unreadCount > 0 && (
          <span className="absolute top-1 right-1 w-4 h-4 bg-red-500 text-white text-[9px] rounded-full flex items-center justify-center font-medium leading-none">
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>
      {createPortal(dropdown, document.body)}
    </>
  );
}

/* ---- sidebar ---- */

export default function Sidebar({ onClose }: Props) {
  const navigate = useNavigate();
  const location = useLocation();
  const activePath = '/' + (location.pathname.split('/')[1] || '');

  const { data: notifData } = useQuery({
    queryKey: queryKeys.notifications(1),
    queryFn: () => api.get<{ unread_count: number }>('/notifications?limit=1'),
    refetchInterval: 30_000,
  });
  const unreadNotifCount = notifData?.unread_count ?? 0;

  return (
    <aside className="w-56 h-screen border-r border-[var(--color-border)] bg-[var(--color-sidebar-bg)] glass-surface flex flex-col text-sm">
      {/* mobile close */}
      {onClose && (
        <button className="md:hidden p-2 ml-auto text-gray-400 hover:text-gray-600" onClick={onClose} aria-label="关闭菜单">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M5 5l10 10M15 5l-10 10" />
          </svg>
        </button>
      )}

      {/* brand + alert bell */}
      <div className="px-4 pt-5 pb-3 flex items-center justify-between">
        <div>
          <h1 className="text-sm font-bold text-[var(--color-text)] tracking-tight">SuppliSense</h1>
          <p className="text-[11px] text-gray-400 mt-0.5">AI-Powered Sourcing &amp; Risk Intelligence</p>
        </div>
        <AlertBell />
      </div>

      {/* divider */}
      <div className="border-t border-[var(--color-border)] mx-4" />

      {/* nav items */}
      <nav className="flex-1 px-3 py-3 space-y-0.5">
        {TAB_ROUTES.map((tab) => {
          const isActive = activePath === tab.path;
          const isAgent = tab.primary;
          const isSettings = tab.path === '/settings';
          return (
            <button
              key={tab.path}
              onClick={() => { navigate(tab.path); onClose?.(); }}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg transition-colors min-h-[40px] text-left ${
                isActive
                  ? 'bg-[var(--color-primary-bg)] text-white shadow-sm'
                  : isAgent
                    ? 'text-[var(--color-primary-bg)] hover:bg-[var(--color-primary-bg)]/10 font-medium'
                    : 'text-gray-600 hover:text-gray-900 hover:bg-[var(--color-surface-hover)]'
              }`}
            >
              <NavIcon name={tab.icon} className="w-5 h-5 shrink-0" />
              <span className="flex-1">{tab.label}</span>
              {isSettings && unreadNotifCount > 0 && (
                <span className="shrink-0 min-w-[18px] h-[18px] bg-red-500 text-white text-[10px] rounded-full flex items-center justify-center font-medium leading-none px-1">
                  {unreadNotifCount > 99 ? '99+' : unreadNotifCount}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* bottom utilities */}
      <div className="border-t border-[var(--color-border)] px-3 py-3">
        <div className="px-3 py-1.5">
          <ThemeSwitcher />
        </div>
      </div>
    </aside>
  );
}
