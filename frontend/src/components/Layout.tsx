import { useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { AnimatePresence, motion } from 'framer-motion';
import ErrorBoundary from './ErrorBoundary';
import NetworkStatus from './NetworkStatus';
import Sidebar from './Sidebar';
import ThemeSwitcher from './ThemeSwitcher';
import { TAB_ROUTES } from '../routes';
import { useTheme } from '../hooks/useTheme';

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
                  onRefresh={() => {}}
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
            onRefresh={() => {}}
            onSelect={(name) => navigate(`/assess/${encodeURIComponent(name)}`)}
          />
        </div>

        <main className="flex-1 flex flex-col min-w-0 relative z-10">
          <div className="flex items-center gap-1 px-6 pt-4 pb-0 overflow-x-auto" role="tablist">
            {TAB_ROUTES.map((tab, i) => (
              <button
                key={tab.path}
                role="tab"
                aria-selected={activeTab === i}
                aria-label={tab.label}
                onClick={() => navigate(tab.path)}
                className={`px-4 py-2 text-sm rounded-lg transition-colors min-h-[44px] flex items-center ${
                  activeTab === i
                    ? 'bg-[var(--color-primary-bg)] text-[var(--color-primary-text)]'
                    : 'text-gray-500 hover:text-gray-700 hover:bg-[var(--color-surface-hover)]'
                }`}
              >
                {tab.label}
              </button>
            ))}
            <div className="flex-1" />
            <ThemeSwitcher />
          </div>

          <div className="flex-1 overflow-auto">
            <Outlet />
          </div>
        </main>
      </ErrorBoundary>
    </div>
  );
}
