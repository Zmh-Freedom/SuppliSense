import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../query-keys';
import type { MonitorTarget } from '../types';

export interface MonitoringTargetSummary extends MonitorTarget {
  name: string;
  score: number | null;
  level: string;
  risk_trend: number | null;
  last_checked: string | null;
}

export interface DashboardData {
  total: number;
  distribution: Record<string, number>;
  companies: MonitoringTargetSummary[];
  targets: MonitorTarget[];
  alert_count: number;
}

export function useDashboard() {
  return useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.get<DashboardData>('/alert/dashboard'),
  });
}
