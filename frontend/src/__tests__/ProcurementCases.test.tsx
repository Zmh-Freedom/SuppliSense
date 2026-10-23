import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ProcurementCases from '../components/ProcurementCases';
import { api } from '../api';

vi.mock('../api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));

const baseCase = {
  id: 'case-1',
  monitor_target_id: 'monitor-1',
  case_type: 'coverage_gap',
  department_code: 'D1',
  title: '补齐数据覆盖缺口',
  priority: 'high',
  status: 'open',
  version: 1,
  created_by: 'buyer-1',
  owner_user_id: null,
  due_at: null,
  next_review_at: null,
  closure_request: null,
  created_at: '2026-10-23T08:00:00+00:00',
  updated_at: '2026-10-23T08:00:00+00:00',
};

const receiptEvent = {
  id: 'receipt-1',
  event_type: 'receipt_recorded',
  actor_id: 'buyer-1',
  payload: { summary: '已核对资料，公开财务域仍未覆盖。', refs: ['monitoring://coverage/monitor-1'] },
  created_at: '2026-10-23T09:00:00+00:00',
};

function detail(
  overrides: Record<string, unknown> = {},
  capabilities: Record<string, boolean> = {},
  events: Array<Record<string, unknown>> = [],
) {
  return {
    case: { ...baseCase, ...overrides },
    events,
    linked_tasks: [],
    capabilities: {
      can_read: true,
      can_claim: false,
      can_assign: false,
      can_update: false,
      can_request_closure: false,
      can_decide_closure: false,
      can_reopen: false,
      ...capabilities,
    },
  };
}

function renderCases(initialEntry: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/cases" element={<ProcurementCases />} />
          <Route path="/cases/:caseId" element={<ProcurementCases />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('ProcurementCases', () => {
  beforeEach(() => vi.clearAllMocks());

  it('loads mine and department lists from server-approved views and opens a case by id', async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockImplementation((path: string, params?: Record<string, string>) => {
      if (path !== '/procurement-cases') return Promise.resolve(detail());
      const item = params?.view === 'department'
        ? { ...baseCase, id: 'case-department', title: '本科室复核事项' }
        : baseCase;
      return Promise.resolve({
        items: [{ case: item, capabilities: { can_read: true } }],
        next_cursor: null,
        available_views: { mine: true, department: true },
      });
    });

    renderCases('/cases');

    expect(await screen.findByText('补齐数据覆盖缺口')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: '本科室事项' }));
    expect(await screen.findByText('本科室复核事项')).toBeInTheDocument();
    expect(api.get).toHaveBeenLastCalledWith('/procurement-cases', expect.objectContaining({ view: 'department' }), expect.anything());
    await user.click(screen.getByRole('link', { name: '打开事项' }));
    expect(await screen.findByRole('heading', { name: '补齐数据覆盖缺口' })).toBeInTheDocument();
  });

  it('completes claim, receipt, accepted-limitation request, and independent manager approval', async () => {
    const user = userEvent.setup();
    let detailRead = 0;
    const snapshots = [
      detail({}, { can_claim: true }),
      detail({ status: 'in_progress', version: 2, owner_user_id: 'buyer-1' }, { can_update: true, can_request_closure: true }),
      detail({ status: 'in_progress', version: 3, owner_user_id: 'buyer-1' }, { can_update: true, can_request_closure: true }, [receiptEvent]),
      detail({
        status: 'pending_closure', version: 4, owner_user_id: 'buyer-1',
        closure_request: { category: 'accepted_limitation', requested_by: 'buyer-1', residual_limitations: '公开财务域资料仍未覆盖' },
      }, { can_decide_closure: true }, [receiptEvent]),
      detail({
        status: 'closed', version: 5, owner_user_id: 'buyer-1', closed_at: '2026-10-23T10:00:00+00:00',
        closure_request: { category: 'accepted_limitation', requested_by: 'buyer-1', residual_limitations: '公开财务域资料仍未覆盖' },
      }, { can_reopen: true }, [receiptEvent]),
    ];
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === '/procurement-cases/case-1') {
        const current = snapshots[Math.min(detailRead, snapshots.length - 1)];
        detailRead += 1;
        return Promise.resolve(current);
      }
      return Promise.resolve({ items: [], next_cursor: null, available_views: { mine: true, department: false } });
    });
    vi.mocked(api.post).mockResolvedValue({});

    renderCases('/cases/case-1');

    await user.click(await screen.findByRole('button', { name: '接手事项' }));
    expect(await screen.findByLabelText('回执摘要')).toBeInTheDocument();
    await user.type(screen.getByLabelText('回执摘要'), '已复核当前资料覆盖。');
    await user.type(screen.getByLabelText('资料引用，每行一项'), 'monitoring://coverage/monitor-1');
    await user.click(screen.getByRole('button', { name: '记录回执' }));
    expect(await screen.findByText('已核对资料，公开财务域仍未覆盖。')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '申请关闭' }));
    await user.selectOptions(screen.getByLabelText('关闭类别'), 'accepted_limitation');
    await user.type(screen.getByLabelText('关闭摘要'), '当前可用来源已核对');
    await user.type(screen.getByLabelText('残余限制'), '公开财务域资料仍未覆盖');
    await user.click(screen.getByRole('button', { name: '提交关闭申请' }));

    expect(await screen.findByRole('button', { name: '批准关闭' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '申请关闭' })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('审核说明'), '接受当前资料限制');
    await user.click(screen.getByRole('button', { name: '批准关闭' }));

    expect(await screen.findByText('已关闭')).toBeInTheDocument();
    expect(screen.getByText('资料缺口仍未解决')).toBeInTheDocument();
    expect(screen.getByText('公开财务域资料仍未覆盖')).toBeInTheDocument();
    expect(api.post).toHaveBeenNthCalledWith(1, '/procurement-cases/case-1/commands', { command: 'claim', expected_version: 1 }, undefined, expect.objectContaining({ 'Idempotency-Key': expect.any(String) }));
  });

  it('keeps the receipt draft and refreshes the case when a stale-version command conflicts', async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockImplementation((path: string) => path === '/procurement-cases/case-1'
      ? Promise.resolve(detail({ status: 'in_progress', version: 7 }, { can_update: true }))
      : Promise.resolve({ items: [], next_cursor: null, available_views: { mine: true, department: false } }));
    vi.mocked(api.post).mockRejectedValueOnce(new Error('事项版本已变化，请刷新后重试'));

    renderCases('/cases/case-1');
    await user.type(await screen.findByLabelText('回执摘要'), '尚未提交的处理说明');
    await user.type(screen.getByLabelText('资料引用，每行一项'), 'evidence://pending');
    await user.click(screen.getByRole('button', { name: '记录回执' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('事项版本已变化');
    expect(screen.getByLabelText('回执摘要')).toHaveValue('尚未提交的处理说明');
    expect(screen.getByLabelText('资料引用，每行一项')).toHaveValue('evidence://pending');
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
  });

  it('shows a linked technical task as execution completed without treating it as case closure', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => path === '/procurement-cases/case-1'
      ? Promise.resolve({
        ...detail({ status: 'in_progress', version: 2 }, { can_read: true }),
        linked_tasks: [{ id: 'task-1', task_type: 'supplement_data', status: 'completed', version: 3, result: { summary: '执行完成，但仍存在资料缺口' } }],
      })
      : Promise.resolve({ items: [], next_cursor: null, available_views: { mine: true, department: false } }));

    renderCases('/cases/case-1');

    expect(await screen.findByText('执行完成')).toBeInTheDocument();
    expect(screen.getByText('事项处理中')).toBeInTheDocument();
    expect(screen.getByText('执行完成，但仍存在资料缺口')).toBeInTheDocument();
  });
});
