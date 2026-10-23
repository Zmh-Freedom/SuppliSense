import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import WorkQueue from '../components/WorkQueue';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  user: { username: 'buyer-1', role: 'purchaser' as string },
}));

vi.mock('../api', () => ({
  api: { get: mocks.get },
  getStoredUser: () => mocks.user,
}));

function response(overrides: Record<string, unknown> = {}) {
  return {
    scope: { view: 'mine', department_code: null },
    capabilities: {
      can_view_mine: true,
      can_view_department: false,
      can_choose_department: false,
      department_codes: [],
    },
    counts: { pending_closure: 0, overdue: 0, due_review: 0, high: 1, normal: 0 },
    items: [{
      case_id: 'case-1',
      target_id: 'target-1',
      title: '核验供应商材料',
      group: 'high',
      status: 'in_progress',
      owner_user_id: 'buyer-1',
      due_at: null,
      next_review_at: null,
      detail_url: '/cases/case-1',
    }],
    next_cursor: null,
    coverage: {
      target_total: 4,
      unknown_count: 2,
      stale_count: 1,
      failed_count: 1,
      targets: [{
        target_id: 'target-coverage',
        display_name: '资料待核验供应商',
        missing_dimensions: ['主体身份', '舆情'],
        unknown: true,
        stale: true,
        failed: false,
      }],
    },
    sections: {
      cases: { status: 'available', count: 1 },
      coverage: { status: 'available', count: 4 },
    },
    ...overrides,
  };
}

function renderQueue(initialEntry = '/') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={[initialEntry]}><WorkQueue /></MemoryRouter></QueryClientProvider>);
}

describe('WorkQueue', () => {
  beforeEach(() => {
    mocks.user = { username: 'buyer-1', role: 'purchaser' };
    mocks.get.mockReset();
    mocks.get.mockResolvedValue(response());
  });

  it('links cases and coverage gaps to their exact destinations and counts unknown in the denominator', async () => {
    renderQueue();

    expect(await screen.findByRole('link', { name: '核验供应商材料' })).toHaveAttribute('href', '/cases/case-1');
    expect(screen.getByRole('link', { name: '资料待核验供应商' })).toHaveAttribute('href', '/assess/target-coverage');
    expect(screen.getByText('未知')).toBeInTheDocument();
    expect(screen.getByText(/2\/4 个对象/)).toBeInTheDocument();
    expect(screen.getAllByText(/1\/4 个对象/)).toHaveLength(2);
    expect(screen.getByText(/状态可能重叠/)).toBeInTheDocument();
  });

  it('shows a coverage source failure without replacing it with zero', async () => {
    mocks.get.mockResolvedValue(response({
      coverage: null,
      sections: {
        cases: { status: 'available', count: 1 },
        coverage: { status: 'unavailable', count: null },
      },
    }));
    renderQueue();

    expect(await screen.findByText('证据覆盖暂时无法加载')).toBeInTheDocument();
    expect(screen.queryByText('未知 0/0 个对象')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: '核验供应商材料' })).toBeInTheDocument();
  });

  it('shows a request failure as unavailable instead of an empty queue', async () => {
    mocks.get.mockRejectedValue(new Error('offline'));
    renderQueue();

    expect(await screen.findByRole('alert')).toHaveTextContent('待办暂时无法加载');
    expect(screen.queryByText('当前范围没有待处理事项')).not.toBeInTheDocument();
  });

  it('uses a separate query when a manager switches to another department', async () => {
    mocks.user = { username: 'manager-1', role: 'purchaser' };
    mocks.get.mockImplementation((_path: string, params?: Record<string, string>) => Promise.resolve(
      response({
        scope: { view: params?.view || 'mine', department_code: params?.department_code || null },
        capabilities: {
          can_view_mine: true,
          can_view_department: true,
          can_choose_department: true,
          department_codes: ['D1', 'D2'],
        },
        items: params?.view === 'department' ? [{
          case_id: 'dept-case', target_id: 'target-2', title: '科室材料复核', group: 'normal',
          status: 'open', owner_user_id: null, due_at: null, next_review_at: null, detail_url: '/cases/dept-case',
        }] : [{
          case_id: 'mine-case', target_id: 'target-1', title: '本人材料复核', group: 'normal',
          status: 'open', owner_user_id: null, due_at: null, next_review_at: null, detail_url: '/cases/mine-case',
        }],
      }),
    ));
    const user = userEvent.setup();
    renderQueue();

    expect(await screen.findByRole('link', { name: '本人材料复核' })).toBeInTheDocument();
    await user.selectOptions(screen.getByRole('combobox', { name: '选择科室' }), 'D2');
    await user.click(screen.getByRole('button', { name: '查看科室队列' }));

    expect(await screen.findByRole('link', { name: '科室材料复核' })).toHaveAttribute('href', '/cases/dept-case?department_code=D2&workbench_view=department');
    expect(mocks.get).toHaveBeenCalledWith('/procurement-workbench', { view: 'department', department_code: 'D2' }, expect.any(AbortSignal));
    expect(screen.getByRole('link', { name: '科室材料复核' })).toHaveAttribute('href', '/cases/dept-case?department_code=D2&workbench_view=department');
    expect(screen.queryByRole('link', { name: '本人材料复核' })).not.toBeInTheDocument();
  });

  it('does not reuse the previous account queue when the signed-in account changes', async () => {
    mocks.get.mockImplementation(() => Promise.resolve(response({
      items: [{
        case_id: `${mocks.user.username}-case`, target_id: 'target-1', title: `${mocks.user.username}事项`,
        group: 'normal', status: 'open', owner_user_id: mocks.user.username,
        due_at: null, next_review_at: null, detail_url: `/${mocks.user.username}-case`,
      }],
    })));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const app = () => <QueryClientProvider client={queryClient}><MemoryRouter><WorkQueue /></MemoryRouter></QueryClientProvider>;
    const view = render(app());

    expect(await screen.findByRole('link', { name: 'buyer-1事项' })).toBeInTheDocument();
    mocks.user = { username: 'buyer-2', role: 'purchaser' };
    view.rerender(app());

    expect(await screen.findByRole('link', { name: 'buyer-2事项' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'buyer-1事项' })).not.toBeInTheDocument();
  });

  it('keeps a current high-risk history label separate from the case queue', async () => {
    renderQueue();
    await screen.findByRole('link', { name: '核验供应商材料' });
    expect(screen.getAllByText('高风险事项').length).toBeGreaterThan(0);
    expect(screen.getByText(/历史风险快照仍需按当前证据核验/)).toBeInTheDocument();
  });
});
