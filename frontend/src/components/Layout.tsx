import { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import ErrorBoundary from './ErrorBoundary';
import NetworkStatus from './NetworkStatus';
import Sidebar from './Sidebar';
import ThemeSwitcher from './ThemeSwitcher';
import { TAB_ROUTES } from '../routes';
import { useTheme } from '../hooks/useTheme';
import { useAlertHistory } from '../hooks';
import { api } from '../api';
import { queryKeys } from '../query-keys';
import type { AlertDoc } from '../types';

function TabIcon({ name, className }: { name: string; className?: string }) {
  const cls = className || 'w-4 h-4';
  switch (name) {
    case 'dashboard':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>;
    case 'assess':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>;
    case 'alerts':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>;
    case 'sentiment':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>;
    case 'agent':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2l2.4 7.2h7.6l-6 4.8 2.4 7.2-6.4-4.8-6.4 4.8 2.4-7.2-6-4.8h7.6z"/></svg>;
    case 'knowledge':
      return <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>;
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

function AlertBell() {
  const queryClient = useQueryClient();
  const { data: alerts } = useAlertHistory();
  const alertList = alerts ?? [];
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, right: 0 });
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (btnRef.current?.contains(target)) return;
      if (panelRef.current && !panelRef.current.contains(target)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const toggle = () => {
    if (!open && btnRef.current) {
      const rect = btnRef.current.getBoundingClientRect();
      setPos({ top: rect.bottom + 8, right: window.innerWidth - rect.right });
    }
    setOpen(!open);
  };

  const clearMutation = useMutation({
    mutationFn: () => api.delete('/alert/history'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.alertHistory }),
  });

  const dropdown = (
    <AnimatePresence>
      {open && (
        <motion.div
          ref={panelRef}
          className="fixed w-80 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl shadow-xl z-50 overflow-hidden"
          style={{ top: pos.top, right: pos.right }}
          initial={{ opacity: 0, scale: 0.95, y: -8 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: -8 }}
          transition={{ duration: 0.18, ease: [0.4, 0, 0.2, 1] }}
        >
          <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)]">
            <span className="text-sm font-medium text-[var(--color-text)]">告警通知</span>
            {alertList.length > 0 && (
              <button
                onClick={() => clearMutation.mutate()}
                disabled={clearMutation.isPending}
                className="text-xs text-gray-400 hover:text-gray-600 disabled:opacity-50"
              >
                {clearMutation.isPending ? '清空中…' : '清空'}
              </button>
            )}
          </div>

          <div className="max-h-80 overflow-auto">
            {alertList.length === 0 ? (
              <p className="text-sm text-gray-400 text-center py-8">暂无告警</p>
            ) : (
              alertList.slice(0, 20).map((doc: AlertDoc, i: number) => {
                const isCritical = doc.severity === 'critical';
                return (
                  <div
                    key={i}
                    className="px-4 py-3 border-b border-[var(--color-border)] last:border-b-0 hover:bg-[var(--color-surface-hover)] transition-colors"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-[var(--color-text)] truncate">{doc.company_name}</span>
                      <span className={`shrink-0 w-1.5 h-1.5 rounded-full ${isCritical ? 'bg-red-500' : 'bg-amber-500'}`} />
                    </div>
                    <p className="text-xs text-gray-500 mt-1 truncate">
                      {doc.changes.map(c => `${c.field} ${c.old} → ${c.new}`).join(' · ')}
                    </p>
                    <p className="text-[10px] text-gray-300 mt-1">{doc.created_at.slice(0, 16).replace('T', ' ')}</p>
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
      <button
        ref={btnRef}
        onClick={toggle}
        className="relative p-2 rounded-lg hover:bg-[var(--color-surface-hover)] transition-colors min-w-[44px] min-h-[44px] flex items-center justify-center"
        aria-label={`告警通知${alertList.length > 0 ? `，${alertList.length} 条` : ''}`}
      >
        <svg className="w-5 h-5 text-gray-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>
          <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
        </svg>
        {alertList.length > 0 && (
          <span className="absolute top-1.5 right-1.5 w-4.5 h-4.5 bg-red-500 text-white text-[10px] rounded-full flex items-center justify-center font-medium leading-none">
            {alertList.length > 99 ? '99+' : alertList.length}
          </span>
        )}
      </button>
      {createPortal(dropdown, document.body)}
    </>
  );
}

export default function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  useTheme(); // initialize theme on mount

  const activePath = '/' + (location.pathname.split('/')[1] || '');
  const activeTab = TAB_ROUTES.findIndex(t => t.path === activePath);

  return (
    <div className="flex h-screen">
      <NetworkStatus />
      <ErrorBoundary>
        {/* Glass theme decorative blobs */}
        <div className="fixed inset-0 pointer-events-none z-0 overflow-hidden hidden" id="glass-blobs">
          <div className="absolute w-[400px] h-[400px] rounded-full opacity-30 blur-[60px] animate-[float-blob_25s_ease-in-out_infinite_alternate]"
            style={{ background: 'radial-gradient(circle, #f472b6 0%, #a78bfa 60%, transparent 80%)', top: '30%', left: '40%' }} />
        </div>
        <style>{`[data-theme="glass"] #glass-blobs { display: block; }`}</style>

        {/* Mobile hamburger */}
        <button
          className="md:hidden fixed top-4 left-4 z-30 p-2 bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-lg shadow-sm"
          onClick={() => setSidebarOpen(!sidebarOpen)}
          aria-label="菜单"
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M3 5h14M3 10h14M3 15h14" />
          </svg>
        </button>

        {/* Mobile sidebar overlay */}
        <AnimatePresence>
          {sidebarOpen && (
            <motion.div
              className="md:hidden fixed inset-0 z-20"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              <motion.div
                className="absolute inset-0 bg-black/20"
                onClick={() => setSidebarOpen(false)}
              />
              <motion.div
                className="relative w-64 h-full"
                initial={{ x: -256 }}
                animate={{ x: 0 }}
                exit={{ x: -256 }}
                transition={{ duration: 0.25, ease: [0.4, 0, 0.2, 1] }}
              >
                <Sidebar
                  onSelect={(name) => { navigate(`/assess/${encodeURIComponent(name)}`); setSidebarOpen(false); }}
                  onClose={() => setSidebarOpen(false)}
                />
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Desktop sidebar */}
        <div className="hidden md:flex">
          <Sidebar
            onSelect={(name) => navigate(`/assess/${encodeURIComponent(name)}`)}
          />
        </div>

        <main className="flex-1 flex flex-col min-w-0 relative z-10">
          <div className="flex items-center gap-1 px-6 pt-4 pb-0 overflow-x-auto" role="tablist">
            {TAB_ROUTES.map((tab, i) => {
              const isActive = activeTab === i;
              const isAgent = tab.primary;
              return (
                <button
                  key={tab.path}
                  role="tab"
                  aria-selected={isActive}
                  aria-label={tab.label}
                  onClick={() => navigate(tab.path)}
                  className={`px-4 py-2 text-sm rounded-lg transition-colors min-h-[44px] inline-flex items-center gap-1.5 whitespace-nowrap ${
                    isActive
                      ? 'bg-[var(--color-primary-bg)] text-[var(--color-primary-text)] shadow-sm'
                      : isAgent
                        ? 'text-[var(--color-primary-bg)] hover:bg-[var(--color-primary-bg)]/10 font-medium'
                        : 'text-gray-500 hover:text-gray-700 hover:bg-[var(--color-surface-hover)]'
                  }`}
                >
                  <TabIcon name={tab.icon} />
                  {tab.label}
                  {isAgent && !isActive && (
                    <span className="relative flex h-2 w-2">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[var(--color-primary-bg)] opacity-40" />
                      <span className="relative inline-flex rounded-full h-2 w-2 bg-[var(--color-primary-bg)]" />
                    </span>
                  )}
                </button>
              );
            })}
            <div className="flex-1" />
            <AlertBell />
            <ThemeSwitcher />
          </div>

          <div className="flex-1 overflow-auto">
            <Outlet />
          </div>
        </main>

        {/* Floating Agent button — hidden on Agent page and login */}
        {activePath !== '/chat' && activePath !== '/login' && (
          <button
            onClick={() => {
              const isAssess = activePath === '/assess';
              const segments = location.pathname.split('/');
              const companyFromUrl = isAssess && segments.length > 2 ? decodeURIComponent(segments[2]) : '';
              const q = companyFromUrl ? `?q=${encodeURIComponent(`请对${companyFromUrl}进行全面深度分析`)}` : '';
              navigate(`/chat${q}`);
            }}
            className="fixed bottom-6 right-6 z-20 w-14 h-14 rounded-2xl bg-[var(--color-primary-bg)] text-white shadow-lg hover:shadow-xl hover:-translate-y-0.5 transition-all duration-200 flex items-center justify-center group"
            title="AI Agent"
            aria-label="AI Agent"
          >
            <svg className="w-6 h-6 group-hover:scale-110 transition-transform duration-200" viewBox="0 0 24 24" fill="currentColor" stroke="none">
              <path d="M12 2l2.4 7.2h7.6l-6 4.8 2.4 7.2-6.4-4.8-6.4 4.8 2.4-7.2-6-4.8h7.6z"/>
            </svg>
            {/* pulse ring */}
            <span className="absolute inset-0 rounded-2xl bg-[var(--color-primary-bg)] opacity-30 animate-ping pointer-events-none" style={{ animationDuration: '2.5s' }} />
          </button>
        )}
      </ErrorBoundary>
    </div>
  );
}
