import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../query-keys';
import type { AlertDoc } from '../types';

export function useAlertHistory() {
  return useQuery({
    queryKey: queryKeys.alertHistory,
    queryFn: () => api.get<AlertDoc[]>('/alert/history'),
  });
}
