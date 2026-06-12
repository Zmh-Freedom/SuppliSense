import { useState } from 'react';
import Sidebar from './components/Sidebar';
import Dashboard from './components/Dashboard';
import AlertCenter from './components/AlertCenter';
import ChatView from './components/ChatView';
import AssessView from './components/AssessView';
import SentimentView from './components/SentimentView';

const TABS = ['风险看板', '企业评估', '告警中心', '舆情监控', '智能对话'];

function getTab(): number {
  try { return parseInt(localStorage.getItem('active_tab') || '0'); } catch { return 0; }
}

function getAssessTarget(): string {
  try { return localStorage.getItem('assess_target') || ''; } catch { return ''; }
}

export default function App() {
  const [tab, setTab] = useState(getTab);
  const [refreshKey, setRefreshKey] = useState(0);
  const [assessTarget, setAssessTarget] = useState(getAssessTarget);

  const switchTab = (i: number) => {
    setTab(i);
    localStorage.setItem('active_tab', String(i));
  };

  const onSelectCompany = (name: string) => {
    setAssessTarget(name);
    localStorage.setItem('assess_target', name);
    switchTab(1);  // jump to assess tab
  };

  return (
    <div className="flex h-screen">
      <Sidebar onRefresh={() => setRefreshKey(k => k + 1)} onSelect={onSelectCompany} />

      <main className="flex-1 flex flex-col min-w-0">
        <div className="flex items-center gap-1 px-6 pt-4 pb-0">
          {TABS.map((name, i) => (
            <button
              key={name}
              onClick={() => switchTab(i)}
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
          {tab === 1 && <AssessView initialName={assessTarget} />}
          {tab === 2 && <AlertCenter />}
          {tab === 3 && <SentimentView />}
          {tab === 4 && <ChatView />}
        </div>
      </main>
    </div>
  );
}
