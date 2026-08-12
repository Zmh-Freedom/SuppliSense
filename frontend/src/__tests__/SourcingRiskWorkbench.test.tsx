import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SourcingRiskApprovalCard from '../components/SourcingRiskApprovalCard';
import SourcingRiskCandidateCard from '../components/SourcingRiskCandidateCard';
import SourcingRiskWorkbench from '../components/SourcingRiskWorkbench';

const identityReviewRun = {
  id: 'run-1',
  status: 'IDENTITY_REVIEW',
  version: 3,
  requirement: { requirement_text: '采购工业摄像头', category: '摄像头' },
  candidates: [{ supplier_name: '待确认企业', identity_review: true, identity_candidates: [] }],
  decisions: [{ group: 'recommended', company_id: 'company-1', final_score: 92 }],
};

const proposal = {
  id: 'approval-1',
  action_type: 'add_watchlist',
  status: 'pending',
  payload: { company_name: '示例供应商' },
};

function renderWithQueryClient(node: React.ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe('SourcingRiskWorkbench', () => {
  it('shows an identity review card and does not display a recommendation', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/agent-runs/run-1')) {
        return Response.json(identityReviewRun);
      }
      return new Response('', { status: 204 });
    }));

    renderWithQueryClient(<SourcingRiskWorkbench initialRunId="run-1" />);

    expect(await screen.findByText('请确认企业主体')).toBeInTheDocument();
    expect(screen.queryByText('推荐供应商')).not.toBeInTheDocument();
  });

  it('sends expected_version when approving an action', async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
      async () => Response.json({}),
    );
    vi.stubGlobal('fetch', fetchMock);

    renderWithQueryClient(<SourcingRiskApprovalCard proposal={proposal} runId="run-1" runVersion={4} />);
    fireEvent.click(screen.getByRole('button', { name: '批准' }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(init?.body).toContain('"expected_version":4');
  });

  it('labels a staged external candidate as an external review item', () => {
    render(<SourcingRiskCandidateCard candidate={{ supplier_name: '外部候选', status: 'staged_candidate' }} />);

    expect(screen.getByText('外部暂存')).toBeInTheDocument();
  });
});
