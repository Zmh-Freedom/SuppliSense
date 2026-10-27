import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Dashboard from '../components/Dashboard';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  dashboardRefetch: vi.fn(),
}));

vi.mock('../api', () => ({
  api: { get: mocks.get },
  getStoredUser: () => ({ username: 'buyer-1', role: 'purchaser' }),
}));
vi.mock('../hooks', () => ({
  useDashboard: () => ({
    data: {
      total: 3,
      alert_count: 1,
      distribution: { 高风险: 1, 中风险: 1, 低风险: 0, 未知: 1 },
      companies: [],
      targets: [
        { monitor_target_id: 'target-low', company_name: '低风险供应商', target_type: 'formal_supplier', risk_score: 82, risk_level: '低风险', risk_change: { status: 'stable', label: '变化不明显', delta: 0 }, data_coverage: { status: 'partial', summary: '3/5 个数据域可用', dimensions: [{ key: 'risk_snapshot', label: '风险快照', status: 'pending', detail: '历史评估依据已过期' }] }, next_action: { code: 'continue_monitoring', label: '继续观察', priority: 'low' } },
        { monitor_target_id: 'target-high', company_name: '高风险供应商', target_type: 'formal_supplier', risk_score: 10, risk_level: '高风险', risk_change: { status: 'deteriorating', label: '风险恶化', delta: -12 }, data_coverage: { summary: '3/5 个数据域可用' }, next_action: { label: '优先采购复核', priority: 'high' } },
        { monitor_target_id: 'target-unknown', company_name: '暂无快照供应商', target_type: 'formal_supplier', risk_score: null, risk_level: null, risk_change: { status: 'no_data', label: '暂无快照' }, data_coverage: { summary: '1/5 个数据域可用' }, next_action: { label: '执行首次评估', priority: 'high' } },
      ],
    },
    isLoading: false,
    isFetching: false,
    error: null,
    refetch: mocks.dashboardRefetch,
  }),
}));
vi.mock('../components/SentimentPanel', () => ({ default: () => <div>舆情占位</div> }));

function renderDashboard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><MemoryRouter><Dashboard /></MemoryRouter></QueryClientProvider>);
}

describe('Dashboard', () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.get.mockImplementation((path: string) => {
      if (path === '/alert/predict') return Promise.resolve([]);
      if (path === '/procurement-workbench') return Promise.resolve({
        scope: { view: 'mine', department_code: null },
        capabilities: { can_view_mine: true, can_view_department: false, can_choose_department: false, department_codes: [] },
        counts: { pending_closure: 0, overdue: 0, due_review: 0, high: 0, normal: 0 },
        items: [], next_cursor: null,
        coverage: { target_total: 0, unknown_count: 0, stale_count: 0, failed_count: 0, targets: [] },
        sections: { cases: { status: 'available', count: 0 }, coverage: { status: 'available', count: 0 } },
      });
      return Promise.resolve({ data: [] });
    });
    mocks.dashboardRefetch.mockReset();
    mocks.dashboardRefetch.mockResolvedValue({ isError: false });
  });

  it('shows the highest risk suppliers first in the ranking table', () => {
    renderDashboard();
    expect(screen.getByRole('heading', { name: '风险排名' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: '高风险供应商' })[0]).toBeInTheDocument();
    expect(screen.getAllByText('暂无快照')[0]).toBeInTheDocument();
    const rows = screen.getAllByRole('row');
    expect(rows[1]).toHaveTextContent('高风险供应商');
    expect(rows[2]).toHaveTextContent('低风险供应商');
    expect(rows[3]).toHaveTextContent('暂无快照供应商');
  });

  it('marks an old low-risk score as historical and does not show a green continue action', () => {
    renderDashboard();

    const lowRiskRow = screen.getAllByRole('row').find(row => row.textContent?.includes('低风险供应商'));
    expect(lowRiskRow).toHaveTextContent('历史评估：低风险 82/100；当前待核验');
    expect(lowRiskRow).toHaveTextContent('核验状态未知');
    expect(lowRiskRow).not.toHaveTextContent('继续观察');
  });

  it('shows unknown in the historical distribution and keeps fixed examples collapsed', () => {
    renderDashboard();

    expect(screen.getByRole('heading', { name: '风险历史快照分布' })).toBeInTheDocument();
    expect(screen.getByText('未知 1 个')).toBeInTheDocument();
    expect(screen.getByTitle('未知: 1')).toBeInTheDocument();
    const examples = screen.getByText('帮助与示例问题').closest('details');
    expect(examples).not.toHaveAttribute('open');
    expect(examples).toHaveTextContent('复核上市供应商案例');
  });

  it('refreshes all active dashboard queries from the header action', async () => {
    const user = userEvent.setup();
    renderDashboard();

    await user.click(await screen.findByRole('button', { name: '刷新看板数据' }));
    await waitFor(() => expect(mocks.dashboardRefetch).toHaveBeenCalledOnce());
    expect(mocks.get).toHaveBeenCalledWith('/alert/predict');
    expect(mocks.get).toHaveBeenCalledWith('/trend/alert?days=30');
  });
});
