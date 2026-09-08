import { lazy, Suspense, type ReactNode } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import AuthGuard from './components/AuthGuard';
import LoginPage from './components/LoginPage';

const Dashboard = lazy(() => import('./components/Dashboard'));
const MonitoringView = lazy(() => import('./components/MonitoringView'));
const ChatView = lazy(() => import('./components/ChatView'));
const Settings = lazy(() => import('./components/Settings'));
const SourcingPage = lazy(() => import('./components/SourcingPage'));
const SupplierLibraryPage = lazy(() => import('./components/SupplierLibraryPage'));
const SupplierProfilePage = lazy(() => import('./components/SupplierProfilePage'));

function Lazy({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<div className="flex items-center justify-center p-8" style={{ color: '#555' }}>加载中...</div>}>
      {children}
    </Suspense>
  );
}

export const TAB_ROUTES = [
  { path: '/chat', label: 'AI 工作台', icon: 'agent', primary: true },
  { path: '/', label: '总览', icon: 'dashboard' },
  { path: '/sourcing', label: '智能寻源', icon: 'sourcing' },
  { path: '/assess', label: '风险监控', icon: 'assess' },
  { path: '/suppliers', label: '供应商库', icon: 'sourcing' },
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
      { path: 'assess/:monitorTargetId?', element: <Lazy><MonitoringView /></Lazy> },
      { path: 'sourcing', element: <Lazy><SourcingPage /></Lazy> },
      { path: 'suppliers', element: <Lazy><SupplierLibraryPage /></Lazy> },
      { path: 'suppliers/:id', element: <Lazy><SupplierProfilePage /></Lazy> },
      { path: 'chat', element: <Lazy><ChatView /></Lazy> },
      { path: 'settings', element: <Lazy><Settings /></Lazy> },
    ],
  },
  { path: '*', element: <Navigate to="/chat" replace /> },
]);
