import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import MonitoringIntakePanel from '../components/MonitoringIntakePanel';
import type { MonitorIntake } from '../types';

const intake: MonitorIntake = {
  intake_id: 'intake-1',
  query: '虚构甲供应链有限公司',
  status: 'ready_for_selection',
  candidates: [{
    candidate_id: 'supplier-1', candidate_type: 'supplier', supplier_id: 'supplier-1', legal_name: '虚构甲供应链有限公司',
    verification_status: 'verified', match_type: 'legal_name', confidence: 1, source: '内部供应商库',
  }],
  selected_candidate_id: 'supplier-1',
  data_coverage: {
    dimensions: [{ key: 'history', label: '历史零件合作', status: 'available', detail: '存在 2 条历史合作记录，不代表当前供货能力。' }],
    missing_dimensions: ['当前供货能力'],
  },
  findings: [{ title: '存在历史零件合作记录', evidence: '有 2 条记录；不代表当前供货或资质已确认。', status: 'supported' }],
};

describe('MonitoringIntakePanel', () => {
  it('labels historical cooperation as reference instead of current available coverage', () => {
    render(<MonitoringIntakePanel intake={intake} isLoading={false} isConfirming={false} onSelect={vi.fn()} onConfirm={vi.fn()} onClose={vi.fn()} />);

    expect(screen.getByText('历史参考')).toBeInTheDocument();
    expect(screen.queryByText('已获取')).not.toBeInTheDocument();
    expect(screen.getByText(/不代表当前供货能力/)).toBeInTheDocument();
  });
});
