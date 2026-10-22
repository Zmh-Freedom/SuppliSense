import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { REALTIME_QUERY_KEYS } from '../query-keys';
import { wsClient } from '../websocket';

export function useRealtimeInvalidation() {
  const queryClient = useQueryClient();
  useEffect(() => {
    const invalidate = (event: string) => {
      for (const queryKey of REALTIME_QUERY_KEYS[event] ?? []) {
        void queryClient.invalidateQueries({ queryKey });
      }
    };
    const unsubscribe = Object.keys(REALTIME_QUERY_KEYS).map(event =>
      wsClient.on(event, () => invalidate(event)),
    );
    // Also close the initial handshake gap and any Redis/WS outage window.
    unsubscribe.push(wsClient.onConnection(() => invalidate('realtime_resync')));
    return () => { unsubscribe.forEach(off => off()); };
  }, [queryClient]);
}
