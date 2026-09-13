import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
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

function renderWithQueryClient(node: React.ReactNode, initialEntries = ['/sourcing']) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={initialEntries}>{node}</MemoryRouter></QueryClientProvider>);
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
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

    expect(await screen.findByText('暂未找到可确认主体')).toBeInTheDocument();
    expect(screen.queryByText('推荐供应商')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '返回修改需求' })).toBeInTheDocument();
  });

  it('recovers an active run from the sourcing URL', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/agent-runs/run-url')) return Response.json(identityReviewRun);
      return new Response('', { status: 204 });
    }));

    renderWithQueryClient(<SourcingRiskWorkbench />, ['/sourcing?run=run-url']);

    expect(await screen.findByText('暂未找到可确认主体')).toBeInTheDocument();
    expect(screen.getByLabelText('采购需求')).toBeInTheDocument();
  });

  it('lets an unresolved identity run return to the editable requirement', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/agent-runs/run-edit')) return Response.json(identityReviewRun);
      return new Response('', { status: 204 });
    }));

    renderWithQueryClient(<SourcingRiskWorkbench initialRunId="run-edit" />);

    fireEvent.click(await screen.findByRole('button', { name: '返回修改需求' }));

    expect(screen.getByLabelText('采购需求')).toHaveValue('采购工业摄像头');
    expect(screen.queryByText('暂未找到可确认主体')).not.toBeInTheDocument();
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

  it('can stop an active run through the existing cancel endpoint', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/agent-runs/run-active')) return Response.json({ ...clarificationRun, id: 'run-active', status: 'LOCAL_SEARCHING', version: 3 });
      if (String(input).endsWith('/agent-runs/run-active/cancel')) return Response.json({ ...clarificationRun, id: 'run-active', status: 'CANCELLED', version: 4 });
      return new Response('', { status: 204 });
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    renderWithQueryClient(<SourcingRiskWorkbench initialRunId="run-active" />);

    fireEvent.click(await screen.findByRole('button', { name: '停止任务' }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/agent-runs/run-active/cancel'),
      expect.objectContaining({ method: 'POST', body: expect.stringContaining('"expected_version":3') }),
    ));
  });

  it('merges candidate, decision, and evidence into one readable recommendation card', async () => {
    const run = {
      id: 'run-results',
      status: 'READY_FOR_REVIEW',
      version: 4,
      next_action: 'review_required',
      requirement: { requirement_text: '采购工业摄像头', category: '摄像头' },
      candidates: [{ company_id: 'company-1', supplier_id: 'supplier-1', supplier_name: '推荐供应商', categories: ['工业摄像头'], identity_status: 'exact', source: 'local', source_updated_at: '2026-09-12T00:00:00Z', match_reasons: ['category_match'] }],
      decisions: [{ company_id: 'company-1', group: 'recommended', reason_codes: ['category_match'] }],
      evidence_by_company_id: { 'company-1': [{ evidence_id: 'evidence-1', dimension: 'financial', claim: '财务资料核验记录', source: 'local', source_reference: 'internal:financial:1', freshness_status: 'fresh', conflict_status: 'clear' }] },
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/agent-runs/run-results')) return Response.json(run);
      return new Response('', { status: 204 });
    }));

    renderWithQueryClient(<SourcingRiskWorkbench initialRunId="run-results" />);

    expect(await screen.findByRole('article', { name: '候选供应商：推荐供应商' })).toBeInTheDocument();
    expect(screen.getAllByRole('article', { name: '候选供应商：推荐供应商' })).toHaveLength(1);
    expect(screen.getByText('相关产品 / 能力')).toBeInTheDocument();
    expect(screen.getByText(/采购品类匹配/)).toBeInTheDocument();
    expect(screen.getByText('查看证据明细（1）')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看供应商画像' })).toHaveAttribute('href', '/suppliers/supplier-1');
    expect(screen.queryByText(/综合评分/)).not.toBeInTheDocument();
  });

  it('distinguishes partial no-result runs from a completed empty search', async () => {
    const run = { ...clarificationRun, id: 'run-partial', status: 'PARTIAL', candidates: [], decisions: [], error_code: 'EXTERNAL_DISCOVERY_FAILED' };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/agent-runs/run-partial')) return Response.json(run);
      return new Response('', { status: 204 });
    }));

    renderWithQueryClient(<SourcingRiskWorkbench initialRunId="run-partial" />);

    expect(await screen.findByText('部分来源完成，暂未形成可用候选')).toBeInTheDocument();
    expect(screen.getByText('已有结果会保留；请刷新任务查看后续候选，或调整采购条件后重新寻源。')).toBeInTheDocument();
  });

  it('does not present an active run without candidates as a completed empty search', async () => {
    const run = { ...clarificationRun, id: 'run-searching', status: 'LOCAL_SEARCHING', candidates: [], decisions: [] };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/agent-runs/run-searching')) return Response.json(run);
      return new Response('', { status: 204 });
    }));

    renderWithQueryClient(<SourcingRiskWorkbench initialRunId="run-searching" />);

    expect(await screen.findByText('候选仍在准备中')).toBeInTheDocument();
    expect(screen.queryByText('暂未找到可比较候选')).not.toBeInTheDocument();
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

    expect(screen.getByText('来源：外部候选')).toBeInTheDocument();
    expect(screen.getByText('外部信息待核验')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '官网（待核验）' })).toHaveAttribute('href', 'https://supplier.example.com');
    expect(screen.getByText('电话（待核验）：021-12345678')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '邮箱（待核验）：sales@supplier.example.com' })).toHaveAttribute('href', 'mailto:sales@supplier.example.com');
  });

  it('uses procurement wording for internal run and identity statuses', () => {
    render(<SourcingRiskCandidateCard candidate={{ supplier_name: '待确认企业', identity_status: 'pending_verification' }} />);

    expect(screen.getByText('主体核验：主体待人工确认')).toBeInTheDocument();
    expect(screen.queryByText('pending_verification')).not.toBeInTheDocument();
  });

  it('does not expose the raw run status in the task badge', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/agent-runs/IDENTITY_REVIEW')) {
        return Response.json({ ...identityReviewRun, id: 'IDENTITY_REVIEW', candidates: [] });
      }
      return new Response('', { status: 204 });
    }));

    renderWithQueryClient(<SourcingRiskWorkbench initialRunId="IDENTITY_REVIEW" />);

    expect((await screen.findAllByText('核验主体与风险')).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('确认主体后继续寻源')).toBeInTheDocument();
    expect(screen.queryByText('IDENTITY_REVIEW')).not.toBeInTheDocument();
  });

  it.each([
    ['CREATED', '理解需求'], ['CLARIFYING', '理解需求'], ['POLICY_LOCKED', '检索候选'],
    ['LOCAL_SEARCHING', '检索候选'], ['EXTERNAL_REVIEW', '检索候选'], ['IDENTITY_RESOLVING', '核验主体与风险'],
    ['IDENTITY_REVIEW', '核验主体与风险'], ['INVESTIGATING', '核验主体与风险'], ['EVIDENCE_REVIEW', '核验主体与风险'],
    ['SCORING', '形成寻源建议'], ['READY_FOR_REVIEW', '形成寻源建议'], ['ACTION_PENDING', '等待采购动作'],
    ['ACTION_EXECUTING', '等待采购动作'], ['COMPLETED', '等待采购动作'], ['PARTIAL', '等待采购动作'],
    ['NEEDS_REVIEW', '形成寻源建议'], ['ACTION_FAILED', '等待采购动作'],
  ])('maps %s to its procurement progress phase', async (status, label) => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith(`/agent-runs/${status}`)) {
        return Response.json({ ...identityReviewRun, id: status, status, candidates: [], decisions: [] });
      }
      return new Response('', { status: 204 });
    }));

    renderWithQueryClient(<SourcingRiskWorkbench initialRunId={status} />);

    const currentStep = await vi.waitFor(() => {
      const progress = screen.getByLabelText('采购业务进度');
      const step = progress.querySelector('li[aria-current="step"]');
      if (!step) throw new Error('当前采购进度尚未出现');
      return step;
    });
    expect(currentStep).toHaveTextContent(label);
  });

  it.each([
    ['FAILED', '任务未完成'], ['CANCELLED', '任务已取消'],
  ])('shows a distinct business outcome for %s', async (status, label) => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith(`/agent-runs/${status}`)) {
        return Response.json({ ...identityReviewRun, id: status, status, candidates: [], decisions: [] });
      }
      return new Response('', { status: 204 });
    }));

    renderWithQueryClient(<SourcingRiskWorkbench initialRunId={status} />);

    expect(await screen.findByText(label)).toBeInTheDocument();
  });

  it('reports a normal non-terminal event stream EOF as reconnectable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 200 })));
    const errors: Error[] = [];

    await agentRunEventStream('run-1', 7, { onError: error => errors.push(error) });

    expect(errors).toHaveLength(1);
    expect(errors[0]?.message).toContain('断开');
  });
});
