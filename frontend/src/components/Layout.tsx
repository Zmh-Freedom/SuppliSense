import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import ErrorBoundary from './ErrorBoundary';
import NetworkStatus from './NetworkStatus';
import Sidebar from './Sidebar';
import { TAB_ROUTES } from '../routes';

export default function Layout() {
  const location = useLocation();
  const navigate = useNavigate();

  const activePath = '/' + (location.pathname.split('/')[1] || '');
  const activeTab = TAB_ROUTES.findIndex(t => t.path === activePath);

  return (
    <div className="flex h-screen">
      <NetworkStatus />
      <ErrorBoundary>
        <Sidebar
          onRefresh={() => {}}
          onSelect={(name) => navigate(`/assess/${encodeURIComponent(name)}`)}
        />

        <main className="flex-1 flex flex-col min-w-0">
          <div className="flex items-center gap-1 px-6 pt-4 pb-0">
            {TAB_ROUTES.map((tab, i) => (
              <button
                key={tab.path}
                onClick={() => navigate(tab.path)}
                className={`px-4 py-1.5 text-sm rounded-lg transition-colors ${
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
