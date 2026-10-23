import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SourcingPage from '../components/SourcingPage';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe('SourcingPage history deep links', () => {
  it('opens the original legacy request results without launching a new search', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/sourcing/requests/legacy-1')) {
        return Response.json({
          request_id: 'legacy-1',
          user_id: 'user-1',
          title: '旧采购任务',
          category: '摄像头',
          spec: 'IP67',
          status: 'done',
          result_count: 1,
          created_at: '2026-10-22T08:00:00Z',
          completed_at: '2026-10-22T08:10:00Z',
          results: [{
            result_id: 'result-1', supplier_name: '历史供应商', match_score: 0.9,
            risk_score: 10, risk_level: 'low', final_rank: 0.92, match_reason: '历史供货',
            risk_summary: '无已知风险', selected: false, action: null,
          }],
        });
      }
      if (url.includes('/sourcing-workspace/tasks')) {
        return Response.json({ items: [], next_cursor: null, sources: { agent_run: { status: 'disabled' }, legacy_request: { status: 'available' } } });
      }
      return new Response('', { status: 204 });
    });
    vi.stubGlobal('fetch', fetchMock);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={['/sourcing?request=legacy-1']}><SourcingPage /></MemoryRouter></QueryClientProvider>);

    expect(await screen.findByText('历史供应商')).toBeInTheDocument();
    expect(screen.getByText(/兼容任务：旧采购任务/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/sourcing/requests/legacy-1/search'))).toBe(false);
  });
});
