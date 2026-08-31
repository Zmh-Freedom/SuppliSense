import type { ReactNode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SupplierProfile } from '../types';
import SupplierProfilePage from '../components/SupplierProfilePage';

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));

vi.mock('../api', () => ({ api: mocks }));
vi.mock('@xyflow/react', () => ({
  ReactFlow: ({ children }: { children?: ReactNode }) => <div data-testid="relationship-graph">{children}</div>,
  Background: () => null,
  Controls: () => null,
}));
vi.mock('recharts', () => {
  const Chart = ({ children }: { children?: ReactNode }) => <div>{children}</div>;
  return { Line: Chart, LineChart: Chart, CartesianGrid: Chart, ResponsiveContainer: Chart, Tooltip: Chart, XAxis: Chart, YAxis: Chart };
});

const PROFILE: SupplierProfile = {
  basic_info: {
    name: '测试供应商有限公司', industry: '仪器仪表制造业', categories: ['仪器仪表制造业'], regions: ['深圳'],
    contact_person: '王工', contact_phone: '400-000-0000', contact_email: 'sales@example.com', website_url: 'https://example.com',
    industry_source: 'tianyancha_baseinfo_cache', website_url_source: 'company_website', contact_phone_source: 'tianyancha_baseinfo', contact_email_source: 'company_website', source: 'manual', updated_at: '2026-08-20T10:00:00+00:00', status: 'active',
  },
  risk: { risk_score: 20, risk_level: '低风险', trend: [], alert_count: 1, in_watchlist: false },
  financial: { history: [{ period: '2025', revenue: 100, net_profit: 10 }], revenue_growth: 10 },
  alerts: [{ _id: 'a-1', severity: 'warning', changes: [{ field: '诉讼', old: 0, new: 1 }], created_at: '2026-08-20' }],
  relationships: { related_count: 1, branch_count: 0, dependency_count: 1, high_risk_related_count: 0, entities: [{ name: '关联供应商', relation_type: '供应链', supplier_id: 'related-1', risk_score: 15 }] },
  changelog: [{ changed_at: '2026-08-20T10:00:00+00:00', changed: { industry: { old: '旧行业', new: '仪器仪表制造业' } } }],
};

const BUSINESS_RISK = {
  assessment_status: 'partial',
  assessment_data_mode: 'demo',
  decision_usable: false,
  period: '2026-08',
  coverage: 0.3,
  enabled_dimension: {
    name: '供应依赖与可替代性', model_weight: 0.3, risk_level: 'medium', supplier_spend_share: 0.6385,
    supplier_received_amount: 1008450, category_total_received_amount: 1579500, active_supplier_count: 3, single_source: false,
  },
  observed_signals: {
    contract: { status: 'observed', active_rows: 1, expiring_rows: 0 },
    settlement: { status: 'observed', unsettled_ratio: 0.1 },
    price: { status: 'observed', change_ratio: 0.05 },
  },
  limitations: ['当前使用合成交易快照进行演示，不可用于正式采购决策、告警或供应商评价。'],
  evidence: [{ source: 'feishu_transaction_snapshot', period: '2026-08', claim: '测试证据', data_mode: 'synthetic', rows: 3 }],
};

function renderProfile() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={['/suppliers/supplier-1']}><Routes><Route path="/suppliers/:id" element={<SupplierProfilePage />} /></Routes></MemoryRouter></QueryClientProvider>);
}

describe('SupplierProfilePage', () => {
  beforeEach(() => {
    mocks.get.mockImplementation((path: string) => Promise.resolve(path.startsWith('/risk/business/') ? BUSINESS_RISK : PROFILE));
    mocks.post.mockResolvedValue({});
    mocks.put.mockResolvedValue({});
    mocks.delete.mockResolvedValue({});
  });
  afterEach(() => vi.clearAllMocks());

  it('shows traceable contact details and alert change detail', async () => {
    renderProfile();
    expect(await screen.findByText('联系方式与数据来源')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'https://example.com' })).toHaveAttribute('href', 'https://example.com');
    expect(screen.getAllByText('来源：company_website').length).toBeGreaterThan(0);
    expect(screen.getByText('诉讼：0 → 1')).toBeInTheDocument();
  });

  it('does not write until the user explicitly confirms the action', async () => {
    const user = userEvent.setup();
    renderProfile();
    await screen.findByText('测试供应商有限公司');
    await user.click(screen.getByRole('button', { name: '加入监控' }));
    expect(mocks.post).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '确认执行' }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith('/alert/watch', { company_name: '测试供应商有限公司' }));
  });

  it('confirms an edit before updating supplier master data', async () => {
    const user = userEvent.setup();
    renderProfile();
    await screen.findByText('测试供应商有限公司');
    await user.click(screen.getByRole('button', { name: '编辑主数据' }));
    const industryInput = screen.getByRole('textbox', { name: '行业' });
    await user.clear(industryInput);
    await user.type(industryInput, '新行业');
    await user.click(screen.getByRole('button', { name: '保存并确认' }));
    expect(mocks.put).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '确认执行' }));
    await waitFor(() => expect(mocks.put).toHaveBeenCalledWith('/suppliers/supplier-1', expect.objectContaining({ industry: '新行业' })));
  });

  it('confirms reassessment before submitting a risk refresh', async () => {
    const user = userEvent.setup();
    renderProfile();
    await screen.findByText('测试供应商有限公司');
    await user.click(screen.getByRole('button', { name: '重新评估' }));
    expect(mocks.post).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '确认执行' }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith('/risk/assess', { company_name: '测试供应商有限公司', force_refresh: true }));
  });

  it('links related suppliers and renders the relationship graph', async () => {
    const user = userEvent.setup();
    renderProfile();
    await screen.findByText('测试供应商有限公司');
    await user.click(screen.getByRole('button', { name: '关联' }));
    expect(screen.getByTestId('relationship-graph')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '关联供应商' })).toHaveAttribute('href', '/suppliers/related-1');
  });

  it('shows a clearly marked non-decision demo business risk result', async () => {
    const user = userEvent.setup();
    renderProfile();
    await screen.findByText('测试供应商有限公司');
    await user.click(screen.getByRole('button', { name: '风险' }));
    expect(await screen.findByText('商务风险 P0')).toBeInTheDocument();
    expect(screen.getByText('演示数据 · 不可决策')).toBeInTheDocument();
    expect(screen.getByText('采购占比')).toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledWith('/risk/business/supplier-1');
  });

  it('uses the embedded changelog without a duplicate request', async () => {
    const user = userEvent.setup();
    renderProfile();
    await screen.findByText('测试供应商有限公司');
    await user.click(screen.getByRole('button', { name: '日志' }));
    expect(screen.getByText('旧行业')).toBeInTheDocument();
    expect(mocks.get).not.toHaveBeenCalledWith(expect.stringContaining('/changelog'));
  });
});
