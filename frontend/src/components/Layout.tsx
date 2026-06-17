import { useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import ErrorBoundary from './ErrorBoundary';
import NetworkStatus from './NetworkStatus';
import Sidebar from './Sidebar';
import { TAB_ROUTES } from '../routes';

export default function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const activePath = '/' + (location.pathname.split('/')[1] || '');
  const activeTab = TAB_ROUTES.findIndex(t => t.path === activePath);

  return (
    <div className="flex h-screen">
      <NetworkStatus />
      <ErrorBoundary>
        {/* Mobile hamburger */}
        <button
          className="md:hidden fixed top-4 left-4 z-30 p-2 bg-white border border-[#e8e8e3] rounded-lg shadow-sm"
          onClick={() => setSidebarOpen(!sidebarOpen)}
          aria-label="菜单"
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M3 5h14M3 10h14M3 15h14" />
          </svg>
        </button>

        {/* Mobile sidebar overlay */}
        {sidebarOpen && (
          <div className="md:hidden fixed inset-0 z-20">
            <div className="absolute inset-0 bg-black/20" onClick={() => setSidebarOpen(false)} />
            <div className="relative w-64 h-full">
              <Sidebar
                onRefresh={() => {}}
                onSelect={(name) => { navigate(`/assess/${encodeURIComponent(name)}`); setSidebarOpen(false); }}
                onClose={() => setSidebarOpen(false)}
              />
            </div>
          </div>
        )}

        {/* Desktop sidebar */}
        <div className="hidden md:flex">
          <Sidebar
            onRefresh={() => {}}
            onSelect={(name) => navigate(`/assess/${encodeURIComponent(name)}`)}
          />
        </div>

        <main className="flex-1 flex flex-col min-w-0">
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
                    ? 'bg-[#333] text-white'
                    : 'text-gray-500 hover:text-gray-700 hover:bg-[#eee]'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-auto">
            <Outlet />
          </div>
        </main>
      </ErrorBoundary>
    </div>
  );
}
