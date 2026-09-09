import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import MonitoringView from '../components/MonitoringView';
import type { MonitorTarget } from '../types';
import { api } from '../api';

const target: MonitorTarget = {
  monitor_target_id: 'monitor-1',
  target_type: 'external_candidate',
  identity_status: 'candidate',
  company_name: '待核验候选有限公司',
  display_name: '待核验候选有限公司',
  risk_change: { status: 'no_data', label: '暂无快照' },
  data_coverage: {
    status: 'partial',
    summary: '2/5 个数据域可用',
    dimensions: [
      { key: 'identity', label: '主体身份', status: 'pending', detail: '待核验' },
      { key: 'financial', label: '财务/公开信息', status: 'available', detail: '已获取' },
    ],
  },
  next_action: { code: 'verify_identity', label: '完成主体核验', priority: 'high', reason: '外部候选尚未完成主体确认' },
};

vi.mock('../api', () => ({ api: { get: vi.fn(), post: vi.fn(), delete: vi.fn(), upload: vi.fn() } }));
vi.mock('../hooks', () => ({
  useDashboard: () => ({ data: { total: 1, alert_count: 0, distribution: {}, targets: [target] }, isLoading: false, error: null, refetch: vi.fn() }),
  useWatchlist: () => ({ companies: [], targets: [target], isLoading: false, error: null, refetch: vi.fn() }),
}));

function LocationProbe() {
  const location = useLocation();
  return <span data-testid="location">{location.pathname}{location.search}</span>;
}

function renderView(initialEntry: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={[initialEntry]}><Routes><Route path="/assess/:monitorTargetId?" element={<MonitoringView />} /></Routes><LocationProbe /></MemoryRouter></QueryClientProvider>);
}

describe('MonitoringView', () => {
  it('opens a monitor target by stable monitor_target_id', async () => {
    const user = userEvent.setup();
    renderView('/assess');
    expect(screen.getByRole('heading', { name: '风险监控' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '待核验候选有限公司' }));
    expect(await screen.findByText('监控对象 ID：monitor-1')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '完成主体核验' })).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('/assess/monitor-1');
  });

  it('shows automatically resolved identity candidates for confirmation', async () => {
    vi.mocked(api.get).mockResolvedValue({
      monitor_target_id: 'monitor-1',
      query: '待核验候选有限公司',
      resolution: 'candidates',
      exact: null,
      candidates: [{
        company_id: 'company-1',
        legal_name: '待核验候选有限公司',
        unified_social_credit_code: '91310000TEST000001',
        verification_status: 'verified',
        match_type: 'legal_name',
        confidence: 0.96,
      }],
    });
    renderView('/assess/monitor-1');
    expect(await screen.findByText('主体候选（自动检索）')).toBeInTheDocument();
    expect(await screen.findByText('主体已核验，可绑定')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: '确认此主体' })).toBeInTheDocument();
  });
});
