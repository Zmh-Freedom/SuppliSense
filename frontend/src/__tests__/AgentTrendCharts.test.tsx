import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import AgentTrendCharts from '../components/AgentTrendCharts';
import type { AgentEvidenceRecord } from '../types';

const monthlyTrend = [
  ['2025-11', 110, 11], ['2025-12', 120, 12], ['2026-01', 130, 13],
  ['2026-03', 150, 15], ['2026-04', 160, 16], ['2026-05', 170, 17],
  ['2026-06', 180, 18], ['2026-07', 190, 19], ['2026-08', 200, 20],
  ['2026-09', 210, 21], ['2026-10', 220, 22],
].map(([month, actual_settlement_amount, received_record_count]) => ({
  month: String(month),
  actual_settlement_amount: Number(actual_settlement_amount),
  received_record_count: Number(received_record_count),
}));

function businessEvidence(status: string, facts: Record<string, unknown> = {}): AgentEvidenceRecord {
  return {
    evidence_id: `business-${status}`,
    entity_id: 'company:1',
    dimension: 'business_risk',
    provider: 'assess_business_risk',
    source_type: 'domain_service_result',
    status,
    data_mode: status === 'synthetic' ? 'synthetic' : 'formal',
    collected_at: '2026-10-29T00:00:00Z',
    facts,
  };
}

describe('AgentTrendCharts', () => {
  it('restores the missing natural month without treating it as zero', () => {
    render(<AgentTrendCharts evidence={[businessEvidence('available', {
      monthly_trend: monthlyTrend,
      missing_month_count: 1,
    })]} limitations={[]} />);

    expect(screen.getByText('供应商交易连续性')).toBeInTheDocument();
    screen.getAllByText('查看日期与数值').forEach(summary => fireEvent.click(summary));
    const missingRows = screen.getAllByRole('row', { name: /2026-02.*本月快照未取得/ });
    expect(missingRows).toHaveLength(2);
    expect(screen.getAllByText(/收货记录数表示源明细行数，不代表零件数量、送货批次或交付能力/)).toHaveLength(1);
  });

  it('shows transaction tables without a line when missing-month alignment cannot be verified', () => {
    render(<AgentTrendCharts evidence={[businessEvidence('available', {
      monthly_trend: [
        { month: '2026-09', actual_settlement_amount: 10, received_record_count: 2 },
        { month: '2026-10', actual_settlement_amount: 20, received_record_count: 3 },
      ],
      missing_month_count: 1,
    })]} limitations={[]} />);

    expect(screen.getByText(/缺失月份数量与月度日期不一致/)).toBeInTheDocument();
    expect(screen.queryAllByRole('img', { name: /趋势图/ })).toHaveLength(0);
    expect(screen.getAllByText('查看日期与数值')).toHaveLength(2);
  });

  it('does not infer monthly continuity when missing-month count is absent', () => {
    render(<AgentTrendCharts evidence={[businessEvidence('available', {
      monthly_trend: [
        { month: '2026-09', actual_settlement_amount: 10, received_record_count: 2 },
        { month: '2026-10', actual_settlement_amount: 20, received_record_count: 3 },
      ],
    })]} limitations={[]} />);

    expect(screen.getByText(/缺少可核验的最近 12 个月完整性信息/)).toBeInTheDocument();
    expect(screen.queryAllByRole('img', { name: /趋势图/ })).toHaveLength(0);
    expect(screen.getAllByText('查看日期与数值')).toHaveLength(2);
  });

  it('does not plot duplicate monthly periods as a continuous trend', () => {
    render(<AgentTrendCharts evidence={[businessEvidence('available', {
      monthly_trend: [
        { month: '2026-09', actual_settlement_amount: 10, received_record_count: 2 },
        { month: '2026-09', actual_settlement_amount: 11, received_record_count: 3 },
        { month: '2026-10', actual_settlement_amount: 20, received_record_count: 4 },
      ],
      missing_month_count: 10,
    })]} limitations={[]} />);

    expect(screen.getByText(/月度数据包含重复月份/)).toBeInTheDocument();
    expect(screen.queryAllByRole('img', { name: /趋势图/ })).toHaveLength(0);
    expect(screen.getAllByRole('row', { name: /2026-09/ })).toHaveLength(4);
  });

  it('does not connect financial reports across an unobserved period', () => {
    render(<AgentTrendCharts evidence={[{
      evidence_id: 'financial-history',
      entity_id: 'company:1',
      dimension: 'financial',
      provider: 'query_financials',
      source_type: 'financial_provider',
      status: 'available',
      data_mode: 'formal',
      collected_at: '2026-10-29T00:00:00Z',
      facts: { financial_history: [
        { period: '2024', revenue: 100, net_profit: 10 },
        { period: '2026', revenue: 120, net_profit: 12 },
      ] },
    }]} limitations={[]} />);

    expect(screen.getByText(/财务报告期不连续/)).toBeInTheDocument();
    expect(screen.queryAllByRole('img', { name: /趋势图/ })).toHaveLength(0);
    expect(screen.getAllByText('查看日期与数值')).toHaveLength(2);
  });

  it('draws annual financial trends only for adjacent reporting years', () => {
    render(<AgentTrendCharts evidence={[{
      evidence_id: 'financial-history',
      entity_id: 'company:1',
      dimension: 'financial',
      provider: 'query_financials',
      source_type: 'financial_provider',
      status: 'available',
      data_mode: 'formal',
      collected_at: '2026-10-29T00:00:00Z',
      facts: { financial_history: [
        { period: '2025', revenue: 120, net_profit: 12 },
        { period: '2024', revenue: 100, net_profit: 10 },
      ] },
    }]} limitations={[]} />);

    expect(screen.queryByText(/财务报告期不连续/)).not.toBeInTheDocument();
    expect(screen.getAllByRole('img', { name: /趋势图；日期按已确认的等周期排列/ })).toHaveLength(2);
  });

  it.each([
    ['missing', '内部交易数据当前未覆盖'],
    ['unavailable', '交易数据查询未成功'],
    ['confirmed_empty', '本次查询范围内未发现'],
    ['synthetic', '内部交易数据为演示数据'],
  ])('keeps the %s data state distinct', (status, label) => {
    render(<AgentTrendCharts evidence={[businessEvidence(status)]} limitations={[]} />);

    expect(screen.getByText(new RegExp(label))).toBeInTheDocument();
    expect(screen.queryByText('供应商交易连续性趋势')).not.toBeInTheDocument();
  });
});
