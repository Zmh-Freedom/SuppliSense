import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { useRealtimeInvalidation } from '../hooks/useRealtimeInvalidation';
import { wsClient } from '../websocket';
import { queryKeys } from '../query-keys';

afterEach(() => vi.restoreAllMocks());

describe('shared invalidation and reconciliation', () => {
  it('invalidates projected results, reconnects, and unsubscribes on unmount', () => {
    const events = new Map<string, (data: unknown) => void>();
    let connected = () => {};
    const unsubscribed: string[] = [];
    vi.spyOn(wsClient, 'on').mockImplementation((event, handler) => {
      events.set(event, handler);
      return () => { events.delete(event); unsubscribed.push(event); };
    });
    vi.spyOn(wsClient, 'onConnection').mockImplementation((handler) => {
      connected = () => handler({ reconnected: true });
      return () => unsubscribed.push('connection');
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(queryKeys.supplierProfile('supplier-1'), { score: 10 });
    client.setQueryData(queryKeys.agentRunDetail('run-1'), { status: 'RUNNING' });
    client.setQueryData(queryKeys.unreadCount, 0);
    client.setQueryData(['monitor-risk-detail', 'target'], { score: 10 });
    client.setQueryData(['monitor-review-task', 'task'], { status: 'pending' });
    client.setQueryData(['monitor-identity-candidates', 'target'], { candidates: [] });
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    const hook = renderHook(() => useRealtimeInvalidation(), { wrapper });

    act(() => events.get('assessment_updated')?.({}));
    expect(client.getQueryState(queryKeys.supplierProfile('supplier-1'))?.isInvalidated).toBe(true);
    expect(client.getQueryState(['monitor-risk-detail', 'target'])?.isInvalidated).toBe(true);
    expect(client.getQueryState(queryKeys.agentRunDetail('run-1'))?.isInvalidated).toBe(false);
    act(() => events.get('agent_run_updated')?.({}));
    expect(client.getQueryState(queryKeys.agentRunDetail('run-1'))?.isInvalidated).toBe(true);
    act(() => connected());
    expect(client.getQueryState(queryKeys.unreadCount)?.isInvalidated).toBe(true);
    expect(client.getQueryState(['monitor-review-task', 'task'])?.isInvalidated).toBe(true);
    expect(client.getQueryState(['monitor-identity-candidates', 'target'])?.isInvalidated).toBe(true);
    client.setQueryData(queryKeys.unreadCount, 1);
    act(() => events.get('realtime_resync')?.({}));
    expect(client.getQueryState(queryKeys.unreadCount)?.isInvalidated).toBe(true);
    hook.unmount();
    expect(events.size).toBe(0);
    expect(unsubscribed).toContain('connection');
    client.clear();
  });
});
