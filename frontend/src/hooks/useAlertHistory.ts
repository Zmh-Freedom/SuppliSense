import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../query-keys';
import type { AlertDoc } from '../types';

interface AlertHistoryResponse {
  count: number;
  unread_count: number;
  alerts: AlertDoc[];
}

export function useAlertHistory() {
  return useQuery({
    queryKey: queryKeys.alertHistory,
    queryFn: () => api.get<AlertHistoryResponse>('/alert/history'),
  });
}
