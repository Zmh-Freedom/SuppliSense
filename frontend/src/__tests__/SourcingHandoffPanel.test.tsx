import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SourcingHandoffPanel, { type SourcingHandoffDetail } from '../components/SourcingHandoffPanel';

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('../api', () => ({ api: { get: mocks.get } }));

const readyDetail: SourcingHandoffDetail = {
  handoff_id: 'handoff-1',
  session_id: 'session-1',
  source_run_id: 'run-1',
  source_run_version: 7,
  candidate_key: 'candidate-1',
  candidate: { supplier_name: '候选工厂', identity_status: 'ambiguous' },
  requirement: { requirement_text: '采购工业相机' },
  evidence_refs: [{ evidence_id: 'evidence-old', dimension: 'quality', freshness_status: 'stale', use: 'historical' }],
  intent: 'risk_review',
  status: 'ready',
  continue_message: '请核验候选供应商“候选工厂”的主体，并继续进行风险检查',
};

describe('SourcingHandoffPanel', () => {
  beforeEach(() => mocks.get.mockReset());

  it('loads the authorized handoff and waits for the user to continue', async () => {
    mocks.get.mockResolvedValue(readyDetail);
    const onContinue = vi.fn();
    const onConsumed = vi.fn();
    render(<SourcingHandoffPanel handoffId="handoff-1" sessionId="session-1" onContinue={onContinue} onConsumed={onConsumed} />);

    expect(await screen.findByText('候选工厂')).toBeInTheDocument();
    expect(screen.getByText(/采购工业相机/)).toBeInTheDocument();
    expect(screen.getByText(/1 条仅作为历史材料/)).toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledWith('/sourcing-workspace/handoffs/handoff-1');
    expect(onContinue).not.toHaveBeenCalled();

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '继续此候选' })); });
    await waitFor(() => expect(onContinue).toHaveBeenCalledWith(readyDetail));
    expect(onConsumed).not.toHaveBeenCalled();
  });

  it('restores the original run when the handoff was already consumed', async () => {
    const consumed = { ...readyDetail, status: 'consumed' as const, run_id: 'chat-run-1', consumed_turn_id: 'turn-1' };
    mocks.get.mockResolvedValue(consumed);
    const onConsumed = vi.fn();
    render(<SourcingHandoffPanel handoffId="handoff-1" sessionId="session-1" onContinue={vi.fn()} onConsumed={onConsumed} />);

    expect(await screen.findByRole('status')).toHaveTextContent('正在恢复原交接任务');
    expect(onConsumed).toHaveBeenCalledOnce();
    expect(onConsumed).toHaveBeenCalledWith(consumed);
    expect(screen.queryByRole('button', { name: '继续此候选' })).not.toBeInTheDocument();
  });

  it('refuses a URL session that differs from the authorized binding', async () => {
    mocks.get.mockResolvedValue(readyDetail);
    render(<SourcingHandoffPanel handoffId="handoff-1" sessionId="tampered-session" onContinue={vi.fn()} onConsumed={vi.fn()} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('交接会话与当前地址不匹配');
    expect(screen.queryByRole('button', { name: '继续此候选' })).not.toBeInTheDocument();
  });
});
