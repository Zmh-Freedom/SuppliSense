import { useState } from 'react';
import Sidebar from './components/Sidebar';
import Dashboard from './components/Dashboard';
import AlertCenter from './components/AlertCenter';
import ChatView from './components/ChatView';
import AssessView from './components/AssessView';

const TABS = ['风险看板', '告警中心', '智能对话', '风险评估'];

export default function App() {
  const [tab, setTab] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <div className="flex h-screen">
      <Sidebar onRefresh={() => setRefreshKey(k => k + 1)} />

      <main className="flex-1 flex flex-col min-w-0">
        <div className="flex items-center gap-1 px-6 pt-4 pb-0">
          {TABS.map((name, i) => (
            <button
              key={name}
              onClick={() => setTab(i)}
              className={`px-4 py-1.5 text-sm rounded-lg transition-colors ${
                tab === i
                  ? 'bg-[#333] text-white'
                  : 'text-gray-500 hover:text-gray-700 hover:bg-[#eee]'
              }`}
            >
              {name}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-auto" key={refreshKey}>
          {tab === 0 && <Dashboard />}
          {tab === 1 && <AlertCenter />}
          {tab === 2 && <ChatView />}
          {tab === 3 && <AssessView />}
        </div>
      </main>
    </div>
  );
}
