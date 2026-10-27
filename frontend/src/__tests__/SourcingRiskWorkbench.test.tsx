import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
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

function CurrentRoute() {
  const location = useLocation();
  return <output data-testid="current-route">{`${location.pathname}${location.search}`}</output>;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe('SourcingRiskWorkbench', () => {
  it('shows retrieved candidate material without exposing a recommendation or action', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/agent-runs/run-1')) {
        return Response.json(identityReviewRun);
      }
      return new Response('', { status: 204 });
    }));

    renderWithQueryClient(<SourcingRiskWorkbench initialRunId="run-1" />);

    expect(await screen.findByText('暂未找到可确认主体')).toBeInTheDocument();
    expect(screen.getByRole('article', { name: '候选供应商：待确认企业' })).toBeInTheDocument();
    expect(screen.queryByText('推荐供应商')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '继续风险核验' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '申请加入监控' })).not.toBeInTheDocument();
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

  it('submits confirmed candidates while clearly staging the unselected candidate', async () => {
    const mixedRun = {
      ...identityReviewRun,
      id: 'run-mixed-review',
      version: 4,
      candidates: [
        { id: 'candidate-exact', supplier_name: '已确认供应商', company_id: 'company-exact', identity_status: 'exact' },
        { id: 'candidate-unknown', supplier_name: '暂不可确认供应商', identity_review: true, identity_status: 'pending_verification', identity_candidates: [] },
        { id: 'candidate-selectable', supplier_name: '可确认供应商', identity_review: true, identity_status: 'pending_verification', identity_candidates: [{ company_id: 'company-selectable', legal_name: '可确认供应商有限公司' }] },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/agent-runs/run-mixed-review')) return Response.json(mixedRun);
      if (url.endsWith('/candidate-decisions') && init?.method !== 'POST') {
        return Response.json({ run_id: 'run-mixed-review', run_version: 4, dispositions: [], resume_status: 'not_started', new_run_id: null });
      }
      if (url.endsWith('/candidate-decisions') && init?.method === 'POST') {
        return Response.json({ run_id: 'run-mixed-review', run_version: 5, dispositions: [], resume_status: 'pending', new_run_id: null });
      }
      return new Response('', { status: 204 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderWithQueryClient(<SourcingRiskWorkbench initialRunId="run-mixed-review" />);

    expect(await screen.findByText(/未选择的候选会暂存到下一轮/)).toBeInTheDocument();
    expect(screen.getByRole('article', { name: '候选供应商：已确认供应商' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('可确认供应商本轮处置'), { target: { value: 'confirm' } });
    fireEvent.change(screen.getByLabelText('可确认供应商主体'), { target: { value: 'company-selectable' } });
    fireEvent.click(screen.getByRole('button', { name: '提交候选处置' }));

    await vi.waitFor(() => {
      const postCall = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith('/candidate-decisions') && init?.method === 'POST');
      expect(postCall).toBeDefined();
      expect(JSON.parse(String(postCall?.[1]?.body))).toEqual({
        expected_version: 4,
        decisions: [{ candidate_key: 'candidate-selectable', action: 'confirm', company_id: 'company-selectable' }],
      });
    });
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
    expect(screen.getByText('主营产品（来源资料）')).toBeInTheDocument();
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

  it('shows source products separately from the matched category', () => {
    render(<SourcingRiskCandidateCard candidate={{
      supplier_name: 'LG化学（重庆）工程塑料有限公司',
      source: 'gasgoo_manual_export',
      categories: ['动力电池'],
      capabilities: [{ category: '动力电池', product_name: '改性材料、电解液、电芯' }],
    }} />);

    expect(screen.getByText('盖世匹配品类')).toBeInTheDocument();
    expect(screen.getByText('动力电池')).toBeInTheDocument();
    expect(screen.getByText('主营产品（来源资料）')).toBeInTheDocument();
    expect(screen.getByText('改性材料、电解液、电芯')).toBeInTheDocument();
    expect(screen.getByText('仅用于判断产品范围，不等同于成品供货证明。')).toBeInTheDocument();
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

  it('creates a version-bound handoff for a stable candidate instead of a q deep link', async () => {
    const run = {
      id: 'run-handoff', status: 'READY_FOR_REVIEW', version: 9,
      requirement: { requirement_text: '采购工业相机' },
      candidates: [{ id: 'candidate-handoff', company_id: 'company-1', supplier_name: '候选工厂', identity_status: 'exact', source: 'local' }],
      decisions: [],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/agent-runs/run-handoff')) return Response.json(run);
      if (url.endsWith('/sourcing-workspace/handoffs') && init?.method === 'POST') {
        return Response.json({ handoff_id: 'handoff-1', session_id: 'session-1', chat_url: '/chat?handoff_id=handoff-1&session_id=session-1' });
      }
      return new Response('', { status: 204 });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWithQueryClient(<><SourcingRiskWorkbench initialRunId="run-handoff" /><CurrentRoute /></>);
    fireEvent.click(await screen.findByRole('button', { name: '继续风险核验' }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/sourcing-workspace/handoffs'),
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'Idempotency-Key': expect.any(String) }),
        body: JSON.stringify({ run_id: 'run-handoff', candidate_key: 'candidate-handoff', expected_version: 9, intent: 'risk_review' }),
      }),
    ));
    await vi.waitFor(() => expect(screen.getByTestId('current-route')).toHaveTextContent('/chat?handoff_id=handoff-1&session_id=session-1'));
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/chat?q='))).toBe(false);
  });

  it('lets the user select deferred candidates and opens the derived run from a terminal task', async () => {
    const user = userEvent.setup();
    const terminalRun = {
      id: 'run-terminal', status: 'PARTIAL', version: 11,
      requirement: { requirement_text: '采购工业摄像头', category: '摄像头' },
      candidates: [
        { id: 'candidate-deferred', supplier_name: '待补资料供应商', identity_status: 'pending_verification' },
      ],
      decisions: [],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/agent-runs/run-terminal')) return Response.json(terminalRun);
      if (url.endsWith('/runs/run-terminal/candidate-decisions')) {
        return Response.json({
          run_id: 'run-terminal', run_version: 11,
          dispositions: [{ candidate_key: 'candidate-deferred', action: 'defer', reason: '等待补充主体资料' }],
          resume_status: 'followup_available', new_run_id: null,
        });
      }
      if (url.endsWith('/runs/run-terminal/followups') && init?.method === 'POST') {
        return Response.json({ parent_run_id: 'run-terminal', run_id: 'run-child', resume_url: '/sourcing?run=run-child' });
      }
      if (url.endsWith('/agent-runs/run-child')) {
        return Response.json({ ...terminalRun, id: 'run-child', status: 'CREATED', candidates: [] });
      }
      return new Response('', { status: 204 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderWithQueryClient(<><SourcingRiskWorkbench /><CurrentRoute /></>, ['/sourcing?run=run-terminal']);

    expect(await screen.findByRole('region', { name: '暂存候选恢复' })).toBeInTheDocument();
    expect(await screen.findByLabelText('选择待补资料供应商继续核验')).toBeInTheDocument();
    expect(screen.getByText('暂存原因：等待补充主体资料')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '创建恢复任务' })).toBeDisabled();
    await user.click(screen.getByLabelText('选择待补资料供应商继续核验'));
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '创建恢复任务' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: '创建恢复任务' }));

    await vi.waitFor(() => {
      const followupCall = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith('/runs/run-terminal/followups') && init?.method === 'POST');
      expect(followupCall, JSON.stringify(fetchMock.mock.calls.map(([url, init]) => [String(url), init?.method]))).toBeDefined();
      expect(followupCall?.[1]?.headers).toEqual(expect.objectContaining({ 'Idempotency-Key': expect.any(String) }));
      expect(JSON.parse(String(followupCall?.[1]?.body))).toEqual({ candidate_keys: ['candidate-deferred'] });
    });
    await vi.waitFor(() => expect(screen.getByTestId('current-route')).toHaveTextContent('/sourcing?run=run-child'));
  });

  it('lets the user reclassify an excluded candidate as deferred before follow-up', async () => {
    const user = userEvent.setup();
    const terminalRun = {
      id: 'run-excluded-terminal', status: 'NEEDS_REVIEW', version: 6,
      requirement: { requirement_text: '采购工业摄像头' },
      candidates: [{ id: 'candidate-excluded', supplier_name: '此前排除供应商', identity_status: 'pending_verification' }],
      decisions: [],
    };
    const dispositionResponse = (action: 'exclude' | 'defer', version: number) => Response.json({
      run_id: 'run-excluded-terminal', run_version: version,
      dispositions: [{ candidate_key: 'candidate-excluded', action, reason: action === 'exclude' ? '初次排除' : '补齐资料后再核验' }],
      resume_status: 'followup_available', new_run_id: null,
    });
    let reclassified = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/agent-runs/run-excluded-terminal')) return Response.json(terminalRun);
      if (url.endsWith('/runs/run-excluded-terminal/candidate-decisions') && init?.method === 'POST') {
        reclassified = true;
        return dispositionResponse('defer', 7);
      }
      if (url.endsWith('/runs/run-excluded-terminal/candidate-decisions')) {
        return dispositionResponse(reclassified ? 'defer' : 'exclude', reclassified ? 7 : 6);
      }
      if (url.endsWith('/runs/run-excluded-terminal/followups') && init?.method === 'POST') {
        return Response.json({ parent_run_id: 'run-excluded-terminal', run_id: 'run-excluded-child', resume_url: '/sourcing?run=run-excluded-child' });
      }
      if (url.endsWith('/agent-runs/run-excluded-child')) {
        return Response.json({ ...terminalRun, id: 'run-excluded-child', status: 'CREATED', candidates: [] });
      }
      return new Response('', { status: 204 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderWithQueryClient(<><SourcingRiskWorkbench /><CurrentRoute /></>, ['/sourcing?run=run-excluded-terminal']);

    expect(await screen.findByRole('region', { name: '暂存候选恢复' })).toBeInTheDocument();
    await user.type(await screen.findByLabelText('此前排除供应商重新暂存原因'), '补齐资料后再核验');
    await user.click(screen.getByRole('button', { name: '将此前排除供应商改为暂存' }));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/runs/run-excluded-terminal/candidate-decisions'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ expected_version: 6, decisions: [{ candidate_key: 'candidate-excluded', action: 'defer', reason: '补齐资料后再核验' }] }),
      }),
    ));
    expect(await screen.findByLabelText('选择此前排除供应商继续核验')).toBeInTheDocument();
  });
});
