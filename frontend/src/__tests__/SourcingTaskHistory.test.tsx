import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SourcingTaskHistory from '../components/SourcingTaskHistory';

const timestamp = '2026-10-23T08:00:00+08:00';

function renderHistory() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><SourcingTaskHistory /></MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe('SourcingTaskHistory', () => {
  it('keeps run and legacy tasks with the same id as separate source links', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({
      items: [
        { source_type: 'agent_run', source_id: 'shared', title: '智能寻源', status: 'COMPLETED', updated_at: timestamp, resume_url: '/sourcing?run=shared' },
        { source_type: 'legacy_request', source_id: 'shared', title: '字段寻源', status: 'done', updated_at: timestamp, resume_url: '/sourcing?request=shared' },
      ],
      next_cursor: null,
      sources: { agent_run: { status: 'available' }, legacy_request: { status: 'available' } },
    })));

    renderHistory();

    expect(await screen.findByRole('link', { name: /智能寻源/ })).toHaveAttribute('href', '/sourcing?run=shared');
    expect(screen.getByRole('link', { name: /字段寻源/ })).toHaveAttribute('href', '/sourcing?request=shared');
    expect(screen.getAllByRole('link')).toHaveLength(2);
  });

  it('shows when a task still has candidates staged for a later round', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({
      items: [
        { source_type: 'agent_run', source_id: 'partial-run', title: '本轮部分完成', status: 'PARTIAL', updated_at: timestamp, time_basis: 'updated_at', resume_url: '/sourcing?run=partial-run' },
        { source_type: 'agent_run', source_id: 'review-run', title: '待核验任务', status: 'NEEDS_REVIEW', updated_at: timestamp, time_basis: 'updated_at', resume_url: '/sourcing?run=review-run' },
      ],
      next_cursor: null,
      sources: { agent_run: { status: 'available' }, legacy_request: { status: 'available' } },
    })));

    renderHistory();

    expect(await screen.findByText('部分完成（有候选暂存）')).toBeInTheDocument();
    expect(screen.getByText('待核验（含暂存候选）')).toBeInTheDocument();
  });

  it('shows the unavailable source and lets the user retry the current page', async () => {
    const fetchMock = vi.fn(async () => Response.json({
      items: [],
      next_cursor: null,
      sources: {
        agent_run: { status: 'available' },
        legacy_request: { status: 'unavailable', reason: '数据暂不可用' },
      },
    }));
    vi.stubGlobal('fetch', fetchMock);

    renderHistory();

    expect(await screen.findByText(/兼容寻源历史暂不可用/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试当前页' }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('lets an administrator explicitly switch to global task scope', async () => {
    localStorage.setItem('session', JSON.stringify({ username: 'admin', role: 'admin' }));
    const fetchMock = vi.fn(async () => Response.json({
      items: [], next_cursor: null,
      sources: { agent_run: { status: 'available' }, legacy_request: { status: 'available' } },
    }));
    vi.stubGlobal('fetch', fetchMock);

    renderHistory();
    await screen.findByText('我的寻源任务');
    fireEvent.click(screen.getByRole('checkbox', { name: '查看全局任务' }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('scope=all'),
      expect.anything(),
    ));
  });
});
