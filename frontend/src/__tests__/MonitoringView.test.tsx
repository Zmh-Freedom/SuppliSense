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
  risk_score: 85,
  risk_level: '低风险',
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
  it('shows a high safety score in the low-risk green tone', () => {
    renderView('/assess');
    expect(screen.getByText('低风险 85/100')).toHaveStyle({ color: '#2d8c63' });
  });

  it('shows procurement wording for risk detail levels and priorities', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path.includes('/risk-detail')) return Promise.resolve({
        monitor_target_id: 'monitor-1', company_name: '待核验候选有限公司', has_snapshot: true,
        risk_change: { status: 'stable', label: '变化不明显' },
        latest_snapshot: {
          risk_score: 85, risk_level: 'low', scoring_version: 'v2', checked_at: '2026-09-12T10:00:00Z',
          score_breakdown: { 舆情: { 原始分: 0, 数据状态: 'LLM 不可用' } }, risk_detail: {},
        }, history: [{ risk_score: 85, risk_level: 'low' }],
      });
      if (path.includes('/identity-candidates')) return Promise.resolve({ monitor_target_id: 'monitor-1', query: '待核验候选有限公司', resolution: 'pending_verification', candidates: [] });
      return Promise.resolve({});
    });
    renderView('/assess/monitor-1');

    expect((await screen.findAllByText('低风险 · 85/100')).length).toBeGreaterThan(0);
    expect(await screen.findByText('该维度暂未形成可用结论')).toBeInTheDocument();
    expect(screen.queryByText('LLM 不可用')).not.toBeInTheDocument();
  });

  it('investigates a supplier before adding it to monitoring', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockResolvedValueOnce({
      intake_id: 'intake-1',
      query: '青岛三祥',
      status: 'ready_for_selection',
      candidates: [{
        candidate_id: 'supplier:s-1', candidate_type: 'supplier', supplier_id: 's-1', supplier_code: 'SUP-001',
        legal_name: '青岛三祥科技股份有限公司', verification_status: 'verified', match_type: 'legal_name', confidence: 1, source: '内部供应商库',
      }],
      selected_candidate_id: 'supplier:s-1',
      data_coverage: { dimensions: [{ key: 'transaction', label: '内部月度交易', status: 'available', detail: '已关联 12 条月度快照' }], missing_dimensions: [] },
      findings: [{ title: '已关联内部交易', evidence: '最新月度快照：2026-08。', status: 'supported' }],
    });
    renderView('/assess');

    await user.type(screen.getByLabelText('供应商名称、代码或统一社会信用代码'), '青岛三祥');
    await user.click(screen.getByRole('button', { name: '开始调查' }));

    expect(await screen.findByRole('heading', { name: '供应商自动调查' })).toBeInTheDocument();
    expect(screen.getByText('青岛三祥科技股份有限公司')).toBeInTheDocument();
    expect(screen.getByText(/发现了什么：已关联内部交易/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认主体并加入监控' })).toBeEnabled();
    expect(vi.mocked(api.post)).toHaveBeenCalledWith('/alert/intakes', { query: '青岛三祥' });
  });

  it('opens a monitor target by stable monitor_target_id', async () => {
    const user = userEvent.setup();
    renderView('/assess');
    expect(screen.getByRole('heading', { name: '风险监控' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '待核验候选有限公司' }));
    expect(await screen.findByText('监控对象 ID：monitor-1')).toBeInTheDocument();
    expect(screen.getByText('采购优先级摘要')).toBeInTheDocument();
    expect(screen.getByText('下一步：完成主体核验')).toBeInTheDocument();
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

  it('shows cached external identity evidence without allowing direct binding', async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path.includes('/identity-candidates')) return Promise.resolve({
        monitor_target_id: 'monitor-1',
        query: '赛克瑞浦动力电池系统有限公司',
        resolution: 'candidates',
        candidates: [{
          candidate_type: 'external_identity',
          candidate_id: 'external_identity:tyc:赛克瑞浦动力电池系统有限公司',
          legal_name: '赛克瑞浦动力电池系统有限公司',
          unified_social_credit_code: '91450200MAA7L76A5R',
          registration_number: '450205000188432',
          registration_status: '存续',
          legal_person: '廖鸿胡',
          verification_status: 'pending_verification',
          match_type: 'external_profile',
          confidence: 0.95,
          source: '天眼查工商主体查询',
          binding_note: '已取得外部主体资料，但尚未绑定本地主体；需管理员完成主体核验后才能绑定监控。',
        }],
      });
      return Promise.resolve({});
    });
    renderView('/assess/monitor-1');
    expect(await screen.findByText('赛克瑞浦动力电池系统有限公司')).toBeInTheDocument();
    expect(await screen.findByText(/统一社会信用代码：91450200MAA7L76A5R/)).toBeInTheDocument();
    expect(screen.getByText(/注册号：450205000188432/)).toBeInTheDocument();
    expect(screen.getByText('主体尚未核验，不能绑定')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '确认此主体' })).not.toBeInTheDocument();
  });
});
