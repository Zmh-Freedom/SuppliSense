import { useState } from 'react';
import Sidebar from './components/Sidebar';
import Dashboard from './components/Dashboard';
import AlertCenter from './components/AlertCenter';
import ChatView from './components/ChatView';
import AssessView from './components/AssessView';
import RiskMatrix from './components/RiskMatrix';
import SentimentView from './components/SentimentView';
import ESGView from './components/ESGView';
import ContagionView from './components/ContagionView';
import MacroView from './components/MacroView';

const TABS = ['风险看板', '告警中心', '智能对话', '风险评估', '风险矩阵', '舆情监控', 'ESG评分', '风险传染', '宏观&替代'];

function getTab(): number {
  try { return parseInt(localStorage.getItem('active_tab') || '0'); } catch { return 0; }
}

export default function App() {
  const [tab, setTab] = useState(getTab);
  const [refreshKey, setRefreshKey] = useState(0);
  const [assessTarget, setAssessTarget] = useState('');

  const switchTab = (i: number) => {
    setTab(i);
    localStorage.setItem('active_tab', String(i));
  };

  const onSelectCompany = (name: string) => {
    setAssessTarget(name);
    switchTab(3);  // jump to assess tab
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
          {tab === 1 && <AlertCenter />}
          {tab === 2 && <ChatView />}
          {tab === 3 && <AssessView initialName={assessTarget} />}
          {tab === 4 && <RiskMatrix />}
          {tab === 5 && <SentimentView />}
          {tab === 6 && <ESGView />}
          {tab === 7 && <ContagionView />}
          {tab === 8 && <MacroView />}
        </div>
      </main>
    </div>
  );
}
