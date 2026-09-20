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
    name: '测试供应商有限公司', industry: '仪器仪表制造业', categories: ['仪器仪表制造业'], regions: ['深圳'], establish_time: '777052800000',
    contact_person: '王工', contact_phone: '400-000-0000', contact_email: 'sales@example.com', website_url: 'https://example.com',
    industry_source: 'tianyancha_baseinfo_cache', website_url_source: 'company_website', contact_phone_source: 'tianyancha_baseinfo', contact_email_source: 'company_website', source: 'manual', updated_at: '2026-08-20T10:00:00+00:00', status: 'active',
  },
  risk: { risk_score: 20, risk_level: '低风险', trend: [], alert_count: 1, in_watchlist: false },
  financial: { history: [{ period: '2025', revenue: 100, net_profit: 10 }, { period: '2025-03-31', revenue: 24, net_profit: 2, debt_ratio: 0.4, cash_flow: 1.2 }], revenue_growth: 0.1, cash_flow: 1.2 },
  sentiment: { overall_sentiment: 'neutral', sentiment_score: 0, negative_ratio: 0, article_count: 0, top_tags: [] },
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

const RISK_SNAPSHOTS = {
  company_name: '测试供应商有限公司', count: 2,
  snapshots: [
    { snapshot_id: 'snapshot-2', snapshot_version: 2, checked_at: '2026-09-01T10:00:00+00:00', risk_score: 20, risk_level: '低风险', scoring_version: 'v2' },
    { snapshot_id: 'snapshot-1', snapshot_version: 1, checked_at: '2026-08-20T10:00:00+00:00', risk_score: 35, risk_level: '中风险', scoring_version: 'v2' },
  ],
};

const SENTIMENT_DETAIL = {
  company_name: '测试供应商有限公司',
  analyzed_at: '2026-09-10T10:00:00+00:00',
  articles_count: 1,
  negative_count: 0,
  neutral_count: 1,
  positive_count: 0,
  sentiment_score: 0,
  risk_tags: [],
  articles: [{
    title: '测试供应商发布年度经营信息',
    body: '这是新闻正文摘要。',
    url: 'https://news.example.com/article-1',
    source: '公开来源',
    published_at: '2026-09-09T08:00:00+00:00',
    sentiment: 'neutral',
    confidence: 0.8,
    risk_tags: [],
    summary: '经营信息保持稳定',
    judgement_basis: '标题未体现明显风险或利好。',
  }],
  summary: '舆情整体中性',
  key_concerns: [],
  has_data: true,
  llm_analyzed: true,
};

function renderProfile() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={['/suppliers/supplier-1']}><Routes><Route path="/suppliers/:id" element={<SupplierProfilePage />} /></Routes></MemoryRouter></QueryClientProvider>);
}

describe('SupplierProfilePage', () => {
  beforeEach(() => {
    mocks.get.mockImplementation((path: string) => Promise.resolve(
      path.startsWith('/risk/business/') ? BUSINESS_RISK : path === '/alert/snapshots' ? RISK_SNAPSHOTS : path.startsWith('/sentiment/') ? SENTIMENT_DETAIL : PROFILE,
    ));
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
    expect(screen.getByText('中性')).toBeInTheDocument();
    expect(screen.queryByText('neutral')).not.toBeInTheDocument();
    expect(screen.getByText('诉讼：0 → 1')).toBeInTheDocument();
    expect(screen.getByText('1994-08-16')).toBeInTheDocument();
  });

  it('does not write until the user explicitly confirms the action', async () => {
    const user = userEvent.setup();
    renderProfile();
    await screen.findByText('测试供应商有限公司');
    await user.click(screen.getByRole('button', { name: '加入监控' }));
    expect(mocks.post).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '确认执行' }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith('/alert/watch', { company_name: '测试供应商有限公司', supplier_id: 'supplier-1', target_type: 'formal_supplier' }));
  });

  it('keeps the Feishu supplier master view read-only', async () => {
    renderProfile();
    await screen.findByText('测试供应商有限公司');
    expect(screen.queryByRole('button', { name: '编辑主数据' })).not.toBeInTheDocument();
    expect(mocks.put).not.toHaveBeenCalled();
  });

  it('opens financial detail and filters annual and quarterly records', async () => {
    const user = userEvent.setup();
    renderProfile();
    await screen.findByText('测试供应商有限公司');
    await user.click(screen.getByRole('button', { name: '财务' }));
    await user.click(screen.getByRole('button', { name: '查看财务明细' }));

    expect(screen.getByRole('region', { name: '财务明细' })).toBeInTheDocument();
    expect(screen.getAllByText('2025-03-31').length).toBeGreaterThan(0);
    expect(screen.getAllByText('季度', { exact: true }).length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: '年度（1）' }));
    expect(screen.getAllByText('2025', { exact: true }).length).toBeGreaterThan(0);
    expect(screen.queryByText('2025-03-31')).not.toBeInTheDocument();
  });

  it('renders financial ratios and cash flow with the Agent contract units', async () => {
    const user = userEvent.setup();
    renderProfile();
    await screen.findByText('测试供应商有限公司');
    await user.click(screen.getByRole('button', { name: '财务' }));

    expect(screen.getByText('10.0%')).toBeInTheDocument();
    expect(screen.getByText('每股经营现金流（元/股）')).toBeInTheDocument();
    expect(screen.getByText('1.20 元/股')).toBeInTheDocument();
  });

  it('shows sentiment article summaries and original links in the supplier profile', async () => {
    const user = userEvent.setup();
    renderProfile();
    await screen.findByText('测试供应商有限公司');
    await user.click(screen.getByRole('button', { name: '舆情' }));

    expect(await screen.findByText('测试供应商发布年度经营信息')).toBeInTheDocument();
    expect(screen.getByText('经营信息保持稳定')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '测试供应商发布年度经营信息' })).toHaveAttribute('href', 'https://news.example.com/article-1');
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

  it('labels supplier-month data as internal procurement evidence', async () => {
    const user = userEvent.setup();
    mocks.get.mockImplementation((path: string) => Promise.resolve(
      path.startsWith('/risk/business/') ? {
        assessment_status: 'partial',
        assessment_data_mode: 'formal',
        period: '2026-05',
        scope: { data_granularity: 'supplier_month' },
        enabled_dimension: {
          name: '内部采购敞口', model_weight: 0.15, risk_level: 'low', exposure_level: 'low',
          supplier_spend_share: 0.00001, settlement_share: 0.00001,
          supplier_received_amount: 60430.19, category_total_received_amount: 6000000,
          latest_actual_settlement_amount: 60430.19, comparison_actual_settlement_amount: 6000000,
          active_supplier_count: 200, single_source: false,
        },
        observed_signals: {
          receipts: { status: 'observed', received_record_count: 27, change_ratio: null },
          settlement: { status: 'observed', change_ratio: null },
          data_continuity: { status: 'incomplete', present_months: 8, expected_months: 12 },
        },
      } : path === '/alert/snapshots' ? RISK_SNAPSHOTS : PROFILE,
    ));

    renderProfile();
    await screen.findByText('测试供应商有限公司');
    await user.click(screen.getByRole('button', { name: '风险' }));

    expect(await screen.findByText('内部采购证据')).toBeInTheDocument();
    expect(screen.getByText('结算金额占比')).toBeInTheDocument();
    expect(screen.getByText('低敞口')).toBeInTheDocument();
    expect(screen.getByText('<0.1%')).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.textContent === '收货记录：27 条（源明细行数）')).toBeInTheDocument();
    expect(screen.getAllByText('相邻自然月数据不足')).toHaveLength(2);
  });

  it('shows append-only risk assessment versions', async () => {
    const user = userEvent.setup();
    renderProfile();
    await screen.findByText('测试供应商有限公司');
    await user.click(screen.getByRole('button', { name: '风险' }));
    expect(await screen.findByText('风险评估历史版本')).toBeInTheDocument();
    expect(screen.getByText('V2')).toBeInTheDocument();
    expect(screen.getAllByText('评分体系 v2').length).toBeGreaterThan(0);
    expect(mocks.get).toHaveBeenCalledWith('/alert/snapshots', { company_name: '测试供应商有限公司', limit: '20' });
  });

  it('renders a safe key when a risk snapshot id is missing', async () => {
    const user = userEvent.setup();
    mocks.get.mockImplementation((path: string) => Promise.resolve(
      path.startsWith('/risk/business/') ? BUSINESS_RISK : path === '/alert/snapshots' ? {
        ...RISK_SNAPSHOTS,
        snapshots: [{ ...RISK_SNAPSHOTS.snapshots[0], snapshot_id: '' }],
      } : PROFILE,
    ));
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    renderProfile();
    await screen.findByText('测试供应商有限公司');
    await user.click(screen.getByRole('button', { name: '风险' }));
    expect(await screen.findByText('V2')).toBeInTheDocument();
    expect(consoleError).not.toHaveBeenCalledWith(expect.stringContaining('Each child in a list should have a unique'), expect.anything(), expect.anything());
    consoleError.mockRestore();
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
