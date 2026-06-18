import { createBrowserRouter } from 'react-router-dom';
import Layout from './components/Layout';
import AuthGuard from './components/AuthGuard';
import Dashboard from './components/Dashboard';
import AssessView from './components/AssessView';
import ChatView from './components/ChatView';
import ContagionView from './components/ContagionView';
import Settings from './components/Settings';
import SourcingPage from './components/SourcingPage';
import LoginPage from './components/LoginPage';

export const TAB_ROUTES = [
  { path: '/', label: '风险看板', icon: 'dashboard' },
  { path: '/assess', label: '企业评估', icon: 'assess' },
  { path: '/sourcing', label: '智能寻源', icon: 'sourcing' },
  { path: '/chat', label: 'Agent', icon: 'agent', primary: true },
  { path: '/contagion', label: '关系图谱', icon: 'contagion' },
  { path: '/settings', label: '设置', icon: 'settings' },
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
      { path: 'sourcing', element: <SourcingPage /> },
      { path: 'chat', element: <ChatView /> },
      { path: 'contagion', element: <ContagionView /> },
      { path: 'settings', element: <Settings /> },
    ],
  },
]);
