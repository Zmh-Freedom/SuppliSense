import { createBrowserRouter } from 'react-router-dom';
import Layout from './components/Layout';
import AuthGuard from './components/AuthGuard';
import Dashboard from './components/Dashboard';
import AssessView from './components/AssessView';
import CompareView from './components/CompareView';
import AlertCenter from './components/AlertCenter';
import SentimentView from './components/SentimentView';
import ChatView from './components/ChatView';
import KnowledgePanel from './components/KnowledgePanel';
import ContagionView from './components/ContagionView';
import Settings from './components/Settings';
import LoginPage from './components/LoginPage';

export const TAB_ROUTES = [
  { path: '/', label: '风险看板' },
  { path: '/assess', label: '企业评估' },
  { path: '/compare', label: '供应商对比' },
  { path: '/alerts', label: '告警中心' },
  { path: '/sentiment', label: '舆情监控' },
  { path: '/chat', label: '智能对话' },
  { path: '/knowledge', label: '知识库' },
  { path: '/contagion', label: '关系图谱' },
  { path: '/settings', label: '设置' },
];

export const router = createBrowserRouter([
  {
    path: '/login',
    element: <LoginPage />,
  },
  {
    element: <AuthGuard><Layout /></AuthGuard>,
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'assess/:companyName?', element: <AssessView /> },
      { path: 'compare', element: <CompareView /> },
      { path: 'alerts', element: <AlertCenter /> },
      { path: 'sentiment', element: <SentimentView /> },
      { path: 'chat', element: <ChatView /> },
      { path: 'knowledge', element: <KnowledgePanel /> },
      { path: 'contagion', element: <ContagionView /> },
      { path: 'settings', element: <Settings /> },
    ],
  },
]);
