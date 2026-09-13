import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import MonitoringWorkbench from '../components/MonitoringWorkbench';
import type { MonitorTarget } from '../types';

const callbacks = {
  onInvestigate: vi.fn(),
  onUpload: vi.fn(),
  onRefresh: vi.fn(),
  onAnalyze: vi.fn(),
  onAction: vi.fn(),
  onApprove: vi.fn(),
  onReject: vi.fn(),
  onExecuteTask: vi.fn(),
  onOpen: vi.fn(),
  onRemove: vi.fn(),
};

const targets: MonitorTarget[] = [
  {
    monitor_target_id: 'low-target', target_type: 'formal_supplier', company_name: '可继续观察供应商', display_name: '可继续观察供应商',
    risk_score: 88, risk_level: '低风险', risk_change: { status: 'stable', label: '变化不明显' },
    data_coverage: { status: 'complete', summary: '5/5 个数据域可用' }, next_action: { code: 'continue_monitoring', label: '继续观察', priority: 'low', reason: '暂无需要立即处理的变化' },
  },
  {
    monitor_target_id: 'high-target', target_type: 'formal_supplier', company_name: '需要优先复核供应商', display_name: '需要优先复核供应商',
    risk_score: 42, risk_level: '中风险', risk_change: { status: 'deteriorating', label: '风险上升', delta: 8 },
    data_coverage: { status: 'partial', summary: '3/5 个数据域可用' }, next_action: { code: 'review', label: '发起复核', priority: 'high', reason: '风险变化需要人工复核' },
  },
];

describe('MonitoringWorkbench', () => {
  it('puts the highest-priority target first and labels its next action', () => {
    render(<MonitoringWorkbench targets={targets} {...callbacks} isInvestigating={false} isUploading={false} isRefreshing={false} defaultOpen />);

    const rows = screen.getAllByRole('row');
    expect(rows[1]).toHaveTextContent('需要优先复核供应商');
    expect(rows[1]).toHaveTextContent('高优先级');
    expect(rows[2]).toHaveTextContent('可继续观察供应商');
  });
});
