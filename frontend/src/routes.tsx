import { lazy, Suspense, type ReactNode } from 'react';
import { createBrowserRouter } from 'react-router-dom';
import Layout from './components/Layout';
import AuthGuard from './components/AuthGuard';
import LoginPage from './components/LoginPage';

const Dashboard = lazy(() => import('./components/Dashboard'));
const AssessView = lazy(() => import('./components/AssessView'));
const ChatView = lazy(() => import('./components/ChatView'));
const ContagionView = lazy(() => import('./components/ContagionView'));
const Settings = lazy(() => import('./components/Settings'));
const SourcingPage = lazy(() => import('./components/SourcingPage'));

function Lazy({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<div className="flex items-center justify-center p-8" style={{ color: '#555' }}>加载中...</div>}>
      {children}
    </Suspense>
  );
}

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
      { index: true, element: <Lazy><Dashboard /></Lazy> },
      { path: 'assess/:companyName?', element: <Lazy><AssessView /></Lazy> },
      { path: 'sourcing', element: <Lazy><SourcingPage /></Lazy> },
      { path: 'chat', element: <Lazy><ChatView /></Lazy> },
      { path: 'contagion', element: <Lazy><ContagionView /></Lazy> },
      { path: 'settings', element: <Lazy><Settings /></Lazy> },
    ],
  },
]);
