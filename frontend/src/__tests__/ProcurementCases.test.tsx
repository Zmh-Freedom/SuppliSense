import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ProcurementCases from '../components/ProcurementCases';
import { api, getStoredUser } from '../api';
import { queryKeys } from '../query-keys';

vi.mock('../api', () => ({
  api: { get: vi.fn(), post: vi.fn() },
  getStoredUser: vi.fn(() => ({ username: 'buyer-1', role: 'purchaser' })),
}));

const baseCase = {
  id: 'case-1',
  monitor_target_id: 'monitor-1',
  scope_key: 'monitor-1:D1:coverage_gap:default',
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
  afterEach(() => vi.restoreAllMocks());

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getStoredUser).mockReturnValue({ username: 'buyer-1', role: 'purchaser' });
  });

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
    expect(screen.queryByRole('tab', { name: '全部事项' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('link', { name: '打开事项' }));
    expect(await screen.findByRole('heading', { name: '补齐数据覆盖缺口' })).toBeInTheDocument();
  });

  it('opens the admin global list by default and keeps its cache scoped to the account', async () => {
    vi.mocked(getStoredUser).mockReturnValue({ username: 'admin-1', role: 'admin' });
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path !== '/procurement-cases') return Promise.resolve(detail());
      return Promise.resolve({
        items: [{ case: { ...baseCase, id: 'case-global', title: '采购员创建的事项' }, capabilities: { can_read: true } }],
        next_cursor: null,
        available_views: { mine: true, department: false, accessible: true },
      });
    });

    renderCases('/cases');

    expect(await screen.findByText('采购员创建的事项')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '全部事项' })).toHaveAttribute('aria-selected', 'true');
    expect(api.get).toHaveBeenCalledWith('/procurement-cases', expect.objectContaining({ view: 'accessible' }), expect.anything());
    expect(api.get).toHaveBeenCalledWith('/procurement-cases', expect.objectContaining({ view: 'accessible', limit: '50' }), expect.anything());
  });

  it('completes claim, receipt, accepted-limitation request, and independent manager approval', async () => {
    const user = userEvent.setup();
    const invalidateSpy = vi.spyOn(QueryClient.prototype, 'invalidateQueries');
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
    expect(invalidateSpy).toHaveBeenCalledWith(expect.objectContaining({ queryKey: queryKeys.procurementWorkbenchPrefix }));
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

  it('submits transfer, defer, due-date, and resume commands from the detail page', async () => {
    const user = userEvent.setup();
    let status = 'in_progress';
    let version = 1;
    let current = detail({ status, version, owner_user_id: 'buyer-1' }, { can_update: true });
    vi.mocked(api.get).mockImplementation((path: string) => path === '/procurement-cases/case-1'
      ? Promise.resolve(current)
      : Promise.resolve({ items: [], next_cursor: null, available_views: { mine: true, department: false, accessible: false } }));
    vi.mocked(api.post).mockImplementation((_path: string, body?: unknown) => {
      const command = body as Record<string, unknown> | undefined;
      version += 1;
      if (command?.command === 'defer') status = 'deferred';
      if (command?.command === 'resume') status = 'in_progress';
      current = detail({ status, version, owner_user_id: 'buyer-1' }, { can_update: true });
      return Promise.resolve({});
    });

    renderCases('/cases/case-1');

    await user.type(await screen.findByLabelText('转交原因'), '采购职责调整');
    await user.click(screen.getByRole('button', { name: '提交转交请求' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/procurement-cases/case-1/commands',
      expect.objectContaining({ command: 'request_transfer', reason: '采购职责调整', expected_version: 1 }),
      undefined,
      expect.any(Object),
    ));

    await user.type(await screen.findByLabelText('搁置原因'), '等待补充内部交易资料');
    await user.type(screen.getByLabelText('下次复查时间'), '2030-01-15T09:30');
    await user.click(screen.getByRole('button', { name: '搁置事项' }));
    await screen.findByRole('button', { name: '恢复事项' });
    expect(api.post).toHaveBeenCalledWith(
      '/procurement-cases/case-1/commands',
      expect.objectContaining({ command: 'defer', reason: '等待补充内部交易资料', expected_version: 2, next_review_at: expect.any(String) }),
      undefined,
      expect.any(Object),
    );

    await user.type(screen.getByLabelText('事项期限'), '2030-02-01T12:00');
    await user.click(screen.getByRole('button', { name: '保存期限' }));
    expect(api.post).toHaveBeenCalledWith(
      '/procurement-cases/case-1/commands',
      expect.objectContaining({ command: 'set_due', expected_version: 3, due_at: expect.any(String) }),
      undefined,
      expect.any(Object),
    );
    await user.click(await screen.findByRole('button', { name: '恢复事项' }));
    expect(api.post).toHaveBeenLastCalledWith(
      '/procurement-cases/case-1/commands',
      expect.objectContaining({ command: 'resume', expected_version: 4 }),
      undefined,
      expect.any(Object),
    );
  });

  it('selects an authorized same-scope duplicate target and sends its id', async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockImplementation((path: string, params?: Record<string, string>) => {
      if (path === '/procurement-cases/case-1') {
        return Promise.resolve(detail(
          { status: 'in_progress', version: 2, owner_user_id: 'buyer-1' },
          { can_request_closure: true },
          [receiptEvent],
        ));
      }
      if (path === '/procurement-cases' && params?.view === 'accessible') {
        return Promise.resolve({
          items: [
            { case: { ...baseCase, id: 'case-duplicate', title: '原事项' }, capabilities: { can_read: true } },
            { case: { ...baseCase, id: 'case-other-scope', scope_key: 'other-scope' }, capabilities: { can_read: true } },
          ],
          next_cursor: null,
          available_views: { mine: true, department: false, accessible: false },
        });
      }
      return Promise.resolve({ items: [], next_cursor: null, available_views: { mine: true, department: false, accessible: false } });
    });
    vi.mocked(api.post).mockRejectedValueOnce(new Error('事项版本已变化，请刷新后重试'));

    renderCases('/cases/case-1');
    await user.click(await screen.findByRole('button', { name: '申请关闭' }));
    await user.selectOptions(screen.getByLabelText('关闭类别'), 'duplicate');
    await user.selectOptions(await screen.findByLabelText('重复的事项'), 'case-duplicate');
    expect(screen.queryByRole('option', { name: /case-other-scope/ })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('关闭摘要'), '该事项已由原事项统一处理');
    await user.click(screen.getByRole('button', { name: '提交关闭申请' }));

    expect(api.post).toHaveBeenCalledWith(
      '/procurement-cases/case-1/commands',
      expect.objectContaining({ command: 'request_closure', category: 'duplicate', duplicate_case_id: 'case-duplicate' }),
      undefined,
      expect.any(Object),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent('事项版本已变化');
    expect(screen.getByLabelText('重复的事项')).toHaveValue('case-duplicate');
    expect(screen.getByLabelText('关闭摘要')).toHaveValue('该事项已由原事项统一处理');
  });

  it('hides case actions when the current actor has no update capabilities', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => path === '/procurement-cases/case-1'
      ? Promise.resolve(detail({ status: 'in_progress', version: 2 }, {}))
      : Promise.resolve({ items: [], next_cursor: null, available_views: { mine: false, department: false, accessible: false } }));

    renderCases('/cases/case-1');

    expect(await screen.findByRole('heading', { name: '补齐数据覆盖缺口' })).toBeInTheDocument();
    expect(screen.queryByLabelText('转交原因')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('搁置原因')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('事项期限')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '恢复事项' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '申请关闭' })).not.toBeInTheDocument();
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

  it('returns from a workbench case to the same dashboard scope', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => path === '/procurement-cases/case-1'
      ? Promise.resolve(detail())
      : Promise.resolve({ items: [], next_cursor: null, available_views: { mine: true, department: false } }));

    renderCases('/cases/case-1?workbench_view=department&department_code=D2');

    expect(await screen.findByRole('heading', { name: '补齐数据覆盖缺口' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '← 返回采购事项' })).toHaveAttribute(
      'href',
      '/?workbench_view=department&department_code=D2',
    );
  });
});
