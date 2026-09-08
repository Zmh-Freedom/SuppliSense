import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { queryKeys } from '../query-keys';
import type { WatchlistData } from '../types';

export function useWatchlist() {
  const query = useQuery({
    queryKey: queryKeys.watchlist,
    queryFn: () => api.get<WatchlistData>('/alert/watchlist'),
    staleTime: 30_000,
  });

  return {
    companies: query.data?.companies ?? [],
    targets: query.data?.targets ?? [],
    count: query.data?.count ?? 0,
    isLoading: query.isLoading,
    error: query.error,
    refetch: query.refetch,
  };
}
