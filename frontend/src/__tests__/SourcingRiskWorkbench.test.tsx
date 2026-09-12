import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SourcingRiskApprovalCard from '../components/SourcingRiskApprovalCard';
import SourcingRiskCandidateCard from '../components/SourcingRiskCandidateCard';
import SourcingRiskWorkbench from '../components/SourcingRiskWorkbench';
import { agentRunEventStream } from '../api';

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

const clarificationRun = {
  id: 'run-clarify',
  status: 'CLARIFYING',
  version: 2,
  requirement: { requirement_text: '寻找制动系统供应商' },
  missing_fields: ['category', 'specification'],
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

  it('shows missing sourcing fields and resumes the same run', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/agent-runs/run-clarify')) return Response.json(clarificationRun);
      if (String(input).endsWith('/agent-runs/run-clarify/clarification')) return Response.json({ ...clarificationRun, status: 'CREATED', version: 3 });
      return new Response('', { status: 204 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderWithQueryClient(<SourcingRiskWorkbench initialRunId="run-clarify" />);

    expect(await screen.findByText('请补充寻源条件')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('例如：制动系统、电子元器件'), { target: { value: '制动系统' } });
    fireEvent.change(screen.getByPlaceholderText('例如：后轮制动鼓、IP67 工业摄像头'), { target: { value: '后轮制动鼓' } });
    fireEvent.click(screen.getByRole('button', { name: '继续寻源' }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/agent-runs/run-clarify/clarification'),
      expect.objectContaining({ method: 'POST', body: expect.stringContaining('"category":"制动系统"') }),
    ));
  });

  it('sends the canonical approval decision URL, expected_version, and comment', async () => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
      async () => Response.json({}),
    );
    vi.stubGlobal('fetch', fetchMock);

    renderWithQueryClient(<SourcingRiskApprovalCard proposal={proposal} runId="run-1" runVersion={4} />);
    fireEvent.change(screen.getByLabelText('审批意见'), { target: { value: '材料完整，可以执行' } });
    fireEvent.click(screen.getByRole('button', { name: '批准' }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/agent-runs/run-1/approvals/approval-1/decisions');
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(init?.body).toContain('"expected_version":4');
    expect(init?.body).toContain('"comment":"材料完整，可以执行"');
  });

  it('labels a staged external candidate as an external review item', () => {
    render(<SourcingRiskCandidateCard candidate={{ supplier_name: '外部候选', status: 'staged_candidate', website_url: 'https://supplier.example.com', contact_phone: '021-12345678', contact_email: 'sales@supplier.example.com' }} />);

    expect(screen.getByText('外部待核验')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '官网（待核验）' })).toHaveAttribute('href', 'https://supplier.example.com');
    expect(screen.getByText('电话（待核验）：021-12345678')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '邮箱（待核验）：sales@supplier.example.com' })).toHaveAttribute('href', 'mailto:sales@supplier.example.com');
  });

  it.each([
    ['CREATED', '创建任务'], ['CLARIFYING', '等待需求澄清'], ['POLICY_LOCKED', '锁定规则'],
    ['LOCAL_SEARCHING', '检索本地候选'], ['EXTERNAL_REVIEW', '审核外部候选'], ['IDENTITY_RESOLVING', '核验企业主体'],
    ['IDENTITY_REVIEW', '企业主体人工复核'], ['INVESTIGATING', '调查风险证据'], ['EVIDENCE_REVIEW', '证据人工复核'],
    ['SCORING', '形成候选决策'], ['READY_FOR_REVIEW', '人工复核'], ['ACTION_PENDING', '等待操作审批'],
    ['ACTION_EXECUTING', '执行批准操作'], ['COMPLETED', '已完成'], ['PARTIAL', '部分完成'],
    ['NEEDS_REVIEW', '需要人工复核'], ['ACTION_FAILED', '操作执行失败'], ['FAILED', '任务失败'], ['CANCELLED', '已取消'],
  ])('maps %s to its durable timeline phase', async (status, label) => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith(`/agent-runs/${status}`)) {
        return Response.json({ ...identityReviewRun, id: status, status, candidates: [], decisions: [] });
      }
      return new Response('', { status: 204 });
    }));

    renderWithQueryClient(<SourcingRiskWorkbench initialRunId={status} />);

    expect(await screen.findByText(label, { selector: 'li[aria-current="step"]' })).toBeInTheDocument();
  });

  it('reports a normal non-terminal event stream EOF as reconnectable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 200 })));
    const errors: Error[] = [];

    await agentRunEventStream('run-1', 7, { onError: error => errors.push(error) });

    expect(errors).toHaveLength(1);
    expect(errors[0]?.message).toContain('断开');
  });
});
