import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../query-keys';

interface CompanySnap {
  name: string;
  score: number | null;
  level: string;
  alert_count: number;
  last_checked: string | null;
}

interface DashboardData {
  total: number;
  distribution: Record<string, number>;
  companies: CompanySnap[];
  alert_count: number;
}

export function useDashboard() {
  return useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.get<DashboardData>('/alert/dashboard'),
  });
}
