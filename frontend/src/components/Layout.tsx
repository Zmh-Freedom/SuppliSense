import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { AnimatePresence, motion } from 'framer-motion';
import ErrorBoundary from './ErrorBoundary';
import NetworkStatus from './NetworkStatus';
import Sidebar from './Sidebar';
import { useTheme } from '../hooks/useTheme';
import { wsClient } from '../websocket';

export default function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  useTheme();

  useEffect(() => {
    if (Notification.permission === 'default') {
      Notification.requestPermission();
    }
  }, []);

  useEffect(() => {
    const unsub = wsClient.on('sourcing_suggestion', (data) => {
      if (Notification.permission === 'granted') {
        new Notification(`供应商备选建议: ${data.company}`, {
          body: `推荐: ${data.alternatives.map((a: { supplier_name: string }) => a.supplier_name).join(', ')}`,
        });
      }
    });
    return unsub;
  }, []);

  const activePath = '/' + (location.pathname.split('/')[1] || '');

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
                className="relative w-56 h-full"
                initial={{ x: -224 }}
                animate={{ x: 0 }}
                exit={{ x: -224 }}
                transition={{ duration: 0.25, ease: [0.4, 0, 0.2, 1] }}
              >
                <Sidebar onClose={() => setSidebarOpen(false)} />
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Desktop sidebar */}
        <div className="hidden md:flex">
          <Sidebar />
        </div>

        {/* Main content */}
        <main className="flex-1 flex flex-col min-w-0 relative z-10">
          <div className="flex-1 overflow-auto">
            <Outlet />
          </div>
        </main>

        {/* Global AI entry: every business page can continue with current context. */}
        {activePath !== '/chat' && activePath !== '/login' && (
          <button
            onClick={() => {
              const isAssess = activePath === '/assess';
              const segments = location.pathname.split('/');
              const companyFromUrl = isAssess && segments.length > 2 ? decodeURIComponent(segments[2]) : '';
              const prompt = companyFromUrl
                ? `请对${companyFromUrl}进行风险评估，并列出需要人工复核的证据`
                : activePath === '/sourcing'
                  ? '请结合当前智能寻源结果，比较候选供应商的匹配度和风险'
                  : activePath === '/suppliers'
                    ? '请帮我分析供应商库中需要重点关注的风险'
                    : '请结合当前页面内容继续分析';
              const q = `?q=${encodeURIComponent(prompt)}`;
              navigate(`/chat${q}`);
            }}
            className="fixed bottom-6 right-6 z-20 w-14 h-14 rounded-2xl bg-[var(--color-primary-bg)] text-white shadow-lg hover:shadow-xl hover:-translate-y-0.5 transition-all duration-200 flex items-center justify-center group"
            title="问 AI"
            aria-label="问 AI"
          >
            <svg className="w-6 h-6 group-hover:scale-110 transition-transform duration-200" viewBox="0 0 24 24" fill="currentColor" stroke="none">
              <path d="M12 2l2.4 7.2h7.6l-6 4.8 2.4 7.2-6.4-4.8-6.4 4.8 2.4-7.2-6-4.8h7.6z"/>
            </svg>
            <span className="absolute inset-0 rounded-2xl bg-[var(--color-primary-bg)] opacity-30 animate-ping pointer-events-none" style={{ animationDuration: '2.5s' }} />
          </button>
        )}
      </ErrorBoundary>
    </div>
  );
}
