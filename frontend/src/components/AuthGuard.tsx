import { type ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { isAuthenticated } from '../api';

export default function AuthGuard({ children }: { children: ReactNode }) {
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}
