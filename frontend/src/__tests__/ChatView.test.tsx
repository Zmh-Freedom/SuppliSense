import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { StreamCallbacks } from '../api'
import ChatView from '../components/ChatView'

const mocks = vi.hoisted(() => ({
  chatRunEventStream: vi.fn(),
  chatStream: vi.fn(),
  resumeChat: vi.fn(),
}))

vi.mock('../api', () => ({
  chatRunEventStream: mocks.chatRunEventStream,
  chatStream: mocks.chatStream,
  dispatchStreamEvent: (eventType: string, data: unknown, callbacks: StreamCallbacks) => {
    if (eventType === 'answer_chunk') callbacks.onAnswerChunk?.(data as { text: string })
    if (eventType === 'done') callbacks.onDone?.(data as { answer: string; status?: string })
  },
  resumeChat: mocks.resumeChat,
}))

const OLD_SESSION = {
  sid: 'old-session',
  title: '旧会话',
  msgs: [],
  updatedAt: 1,
}

function renderChat(initialEntries = ['/chat']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <ChatView />
    </MemoryRouter>,
  )
}

function createPendingStream() {
  let callbacks: StreamCallbacks | undefined
  let resolveStream: ((answer: string) => void) | undefined

  mocks.chatStream.mockImplementation(
    (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      callbacks = handlers
      return new Promise<string>((resolve) => {
        resolveStream = resolve
      })
    },
  )

  return async () => {
    await act(async () => {
      callbacks?.onDone?.({ answer: '完成的回答' })
      resolveStream?.('完成的回答')
    })
  }
}

describe('ChatView session lifecycle', () => {
  beforeEach(() => {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
    localStorage.clear()
    localStorage.setItem('chat_sessions', JSON.stringify([OLD_SESSION]))
    mocks.chatStream.mockReset()
    mocks.chatRunEventStream.mockReset()
    mocks.resumeChat.mockReset()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('uses the outer composer focus state without an inner input ring', () => {
    renderChat()

    const input = screen.getByLabelText('向采购助手提问')
    expect(input).toHaveClass('focus-visible:ring-0')
    expect(input).not.toHaveClass('focus-visible:ring-2')
  })

  it('keeps a newly opened chat selected when an earlier stream completes', async () => {
    const completeStream = createPendingStream()
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByLabelText('向采购助手提问'), '分析旧会话')
    await user.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(mocks.chatStream).toHaveBeenCalledOnce())

    await user.click(screen.getAllByRole('button', { name: '+ 新对话' })[0])
    await completeStream()

    expect(screen.getByText('AI 工作台')).toBeInTheDocument()
  })

  it('does not load another account\'s legacy or scoped chat history', () => {
    localStorage.setItem('session', JSON.stringify({ username: 'xiaoli.meng', role: 'purchaser' }))
    localStorage.setItem('chat_sessions:minhao.zhou', JSON.stringify([OLD_SESSION]))

    renderChat()

    expect(screen.queryByText('旧会话')).not.toBeInTheDocument()
    expect(screen.getByText('暂无会话')).toBeInTheDocument()
  })

  it('does not recreate a deleted session when its stream completes', async () => {
    const completeStream = createPendingStream()
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByLabelText('向采购助手提问'), '分析旧会话')
    await user.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(mocks.chatStream).toHaveBeenCalledOnce())

    await user.click(screen.getByRole('button', { name: '×' }))
    await completeStream()

    expect(JSON.parse(localStorage.getItem('chat_sessions') || '[]')).toEqual([])
  })

  it('renders external supplier contact details from agent references', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      handlers.onReferences?.({ items: [{ name: '华东钢材供应有限公司', website_url: 'https://steel.example.com', contact_phone: '021-12345678', contact_email: 'sales@steel.example.com' }] })
      handlers.onDone?.({ answer: '已找到候选供应商。' })
      return '已找到候选供应商。'
    })
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByLabelText('向采购助手提问'), '查找钢材供应商')
    await user.click(screen.getByRole('button', { name: '发送' }))

    const supplierDetails = await screen.findByText('本轮识别供应商（1 家）')
    expect(supplierDetails.closest('details')).not.toHaveAttribute('open')
    await user.click(supplierDetails)
    expect(await screen.findByRole('link', { name: '官网（待核验）' })).toHaveAttribute('href', 'https://steel.example.com')
    expect(screen.getByText('电话（待核验）：021-12345678')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '邮箱（待核验）：sales@steel.example.com' })).toHaveAttribute('href', 'mailto:sales@steel.example.com')
  })

  it('replays persisted run events when the initial SSE stream disconnects', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      handlers.onRun?.({ run_id: 'run-1' })
      throw new Error('SSE 流在收到最终结果前断开')
    })
    mocks.chatRunEventStream.mockImplementation(async (_runId: string, _lastEventId: number | null, handlers: {
      onEvent?: (event: { eventId: number; eventType: string; data: unknown }) => void
    }) => {
      handlers.onEvent?.({ eventId: 1, eventType: 'answer_chunk', data: { text: '已从持久化事件恢复结果。' } })
      handlers.onEvent?.({ eventId: 2, eventType: 'done', data: { answer: '已从持久化事件恢复结果。', status: 'completed' } })
    })
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByLabelText('向采购助手提问'), '复核供应商')
    await user.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByText('已从持久化事件恢复结果。')).toBeInTheDocument()
    expect(mocks.chatRunEventStream).toHaveBeenCalledWith('run-1', null, expect.any(Object))
  })

  it('sends the current input value when Chinese IME composition has not updated React state', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      handlers.onDone?.({ answer: '已收到。', status: 'completed' })
      return '已收到。'
    })
    const user = userEvent.setup()
    renderChat()
    const input = screen.getByLabelText('向采购助手提问') as HTMLInputElement
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
    setter?.call(input, '复核青岛三祥科技股份有限公司')
    fireEvent.compositionStart(input)

    await user.click(screen.getByRole('button', { name: '发送' }))

    await waitFor(() => expect(mocks.chatStream).toHaveBeenCalledOnce())
    expect(mocks.chatStream.mock.calls[0][0]).toBe('复核青岛三祥科技股份有限公司')
  })

  it('starts the verified listed supplier review from the demo case entry', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      handlers.onDone?.({ answer: '已完成复核。', status: 'completed' })
      return '已完成复核。'
    })
    const user = userEvent.setup()
    renderChat()

    expect(screen.getByText('比赛演示案例')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '复核青岛三祥科技股份有限公司' }))

    expect(mocks.chatStream).toHaveBeenCalledWith(
      '复核青岛三祥科技股份有限公司',
      expect.any(String),
      expect.any(Object),
      'auto',
    )
    expect(mocks.chatStream.mock.calls[0][1]).not.toBe('old-session')
  })

  it('still sends on a plain HTTP client without crypto.randomUUID', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      handlers.onDone?.({ answer: '已收到。', status: 'completed' })
      return '已收到。'
    })
    const randomUuid = vi.spyOn(globalThis.crypto, 'randomUUID').mockImplementation(() => {
      throw new TypeError('randomUUID is unavailable in this context')
    })
    const user = userEvent.setup()
    renderChat()
    await user.click(screen.getAllByRole('button', { name: '+ 新对话' })[0])

    await user.type(screen.getByLabelText('向采购助手提问'), '复核青岛三祥科技股份有限公司')
    await user.click(screen.getByRole('button', { name: '发送' }))

    await waitFor(() => expect(mocks.chatStream).toHaveBeenCalledOnce())
    expect(mocks.chatStream.mock.calls[0][1]).toMatch(/^session-/)
    randomUuid.mockRestore()
  })

  it('starts each demo case review in a new conversation', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      handlers.onDone?.({ answer: '已完成复核。', status: 'completed' })
      return '已完成复核。'
    })
    const user = userEvent.setup()
    renderChat()

    await user.click(screen.getByRole('button', { name: '复核青岛三祥科技股份有限公司' }))
    await user.click(screen.getAllByRole('button', { name: '+ 新对话' })[0])
    await user.click(screen.getByRole('button', { name: '复核上海汽车制动系统有限公司' }))

    const calls = mocks.chatStream.mock.calls
    expect(calls).toHaveLength(2)
    expect(calls[0][1]).not.toBe('old-session')
    expect(calls[1][1]).not.toBe(calls[0][1])
  })

  it('auto-submits monitoring deep-link reviews in a fresh conversation', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      handlers.onDone?.({ answer: '已完成监控复核。', status: 'completed' })
      return '已完成监控复核。'
    })

    renderChat(['/chat?q=' + encodeURIComponent('请复核青岛三祥科技股份有限公司')])

    await waitFor(() => expect(mocks.chatStream).toHaveBeenCalledOnce())
    expect(mocks.chatStream.mock.calls[0][0]).toBe('请复核青岛三祥科技股份有限公司')
    expect(mocks.chatStream.mock.calls[0][1]).not.toBe('old-session')
  })

  it('persists the completed Agent workflow summary with the answer', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      handlers.onWorkflowStatus?.({ status: 'completed', stage: 'completed', message: '本轮 Agent 工作流已完成', target_suppliers: ['华东钢材供应有限公司'], sources: ['local_snapshot'], evidence_status: '证据充分', loop_exit_reason: 'evidence_sufficient' })
      handlers.onDone?.({ answer: '已完成分析。' })
      return '已完成分析。'
    })
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByLabelText('向采购助手提问'), '分析供应商')
    await user.click(screen.getByRole('button', { name: '发送' }))

    await user.click(await screen.findByRole('button', { name: '执行详情' }))
    expect(await screen.findByText('当前状态：已完成')).toBeInTheDocument()
    expect(screen.getByText('本轮结果：所需证据已覆盖')).toBeInTheDocument()
  })

  it('keeps a Harness needs_review answer visible as a review state', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      handlers.onAgentAnswer?.({
        status: 'needs_review',
        summary: '证据不足',
        claims: [],
        limitations: ['缺少 risk 维度的正式证据'],
        action_proposals: [],
        action_receipts: [],
        evidence_refs: [],
      })
      handlers.onDone?.({ answer: '证据不足' })
      return '证据不足'
    })
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByLabelText('向采购助手提问'), '分析供应商风险')
    await user.click(screen.getByRole('button', { name: '发送' }))

    expect(screen.getAllByText('业务结论').length).toBeGreaterThan(0)
    expect(screen.getByText('数据说明').closest('details')).not.toHaveAttribute('open')
    await user.click(await screen.findByRole('button', { name: '执行详情' }))
    expect(await screen.findByText('当前状态：需人工复核')).toBeInTheDocument()
    await user.click(screen.getByText('数据说明'))
    expect(screen.getByText('综合风险数据覆盖不足')).toBeInTheDocument()
  })

  it('persists the approval card so it can be confirmed from chat history', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, sessionId: string, handlers: StreamCallbacks) => {
      handlers.onApprovalRequired?.({
        message: '确认执行加入监控动作？',
        tool: 'agent_supervisor',
        args: { pending_approvals: [{ approval_id: 'approval-1', action_type: 'add_watchlist' }] },
        session_id: sessionId,
      })
      handlers.onDone?.({ answer: '已生成 1 项加入监控操作，等待人工确认后才会写入监控清单。', status: 'needs_review' })
      return '已生成 1 项加入监控操作，等待人工确认后才会写入监控清单。'
    })
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByLabelText('向采购助手提问'), '请复核并加入监控')
    await user.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByRole('button', { name: '批准动作' })).toBeInTheDocument()
    const sessions = JSON.parse(localStorage.getItem('chat_sessions') || '[]')
    expect(sessions[0].msgs.at(-1).approval.status).toBe('pending')
  })

  it('keeps an approval card when the supervisor stream ends without done', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, sessionId: string, handlers: StreamCallbacks) => {
      handlers.onApprovalRequired?.({
        message: '确认执行加入监控动作？',
        tool: 'agent_supervisor',
        args: { pending_approvals: [{ approval_id: 'approval-no-done', action_type: 'add_watchlist' }] },
        session_id: sessionId,
      })
      return ''
    })
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByLabelText('向采购助手提问'), '请加入监控')
    await user.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByRole('button', { name: '批准动作' })).toBeInTheDocument()
    expect(screen.queryByText('工作流执行失败')).not.toBeInTheDocument()
    const sessions = JSON.parse(localStorage.getItem('chat_sessions') || '[]')
    expect(sessions[0].msgs.at(-1).approval.status).toBe('pending')
  })

  it('shows the resolved approval result after confirming a persisted action', async () => {
    let resumeHandlers: StreamCallbacks | undefined
    mocks.chatStream.mockImplementation(async (_message: string, sessionId: string, handlers: StreamCallbacks) => {
      handlers.onApprovalRequired?.({
        message: '确认执行加入监控动作？',
        tool: 'agent_supervisor',
        args: { pending_approvals: [{ approval_id: 'approval-2', action_type: 'add_watchlist' }] },
        session_id: sessionId,
      })
      handlers.onDone?.({ answer: '已生成 1 项加入监控操作，等待人工确认后才会写入监控清单。', status: 'needs_review' })
      return '等待确认'
    })
    mocks.resumeChat.mockImplementation(async (_sessionId: string, _approved: boolean, handlers: StreamCallbacks) => {
      resumeHandlers = handlers
      handlers.onDone?.({ answer: '已生成 1 项加入监控操作，等待人工确认后才会写入监控清单。', status: 'completed' })
      return '已完成'
    })
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByLabelText('向采购助手提问'), '请复核并加入监控')
    await user.click(screen.getByRole('button', { name: '发送' }))
    await user.click(await screen.findByRole('button', { name: '批准动作' }))

    await waitFor(() => expect(mocks.resumeChat).toHaveBeenCalledOnce())
    expect(resumeHandlers).toBeDefined()
    expect(await screen.findByText('加入监控操作已获批准，服务端已返回执行结果。')).toBeInTheDocument()
    expect(screen.getAllByText('已批准').length).toBeGreaterThan(0)
  })

  it('translates internal fields and lists the covered risk items', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      handlers.onAgentAnswer?.({
        status: 'completed',
        summary: '已完成风险分析。',
        claims: [
          {
            claim_id: 'claim-risk-score',
            entity_id: 'entity:华东钢材供应有限公司',
            dimension: 'risk',
            statement: '华东钢材供应有限公司 risk_score 为 6',
            value: 86,
            fact_path: 'risk_score',
            evidence_refs: ['risk-evidence'],
            confidence: 0.85,
            validation_status: 'supported',
            validation_reasons: [],
          },
          {
            claim_id: 'claim-net-profit-growth',
            entity_id: 'entity:华东钢材供应有限公司',
            dimension: 'financial',
            statement: '华东钢材供应有限公司 Net Profit Growth: -10%',
            value: -0.1,
            fact_path: 'Net Profit Growth',
            evidence_refs: ['financial-history-evidence'],
            confidence: 0.85,
            validation_status: 'supported',
            validation_reasons: [],
          },
        ],
        limitations: [],
        action_proposals: [],
        action_receipts: [],
        evidence_refs: ['risk-evidence'],
      })
      handlers.onEvidence?.({
        records: [{
          evidence_id: 'risk-evidence',
          entity_id: 'entity:华东钢材供应有限公司',
          dimension: 'risk',
          provider: 'assess_risk',
          source_type: 'risk_service_result',
          status: 'available',
          data_mode: 'formal',
          collected_at: '2026-09-05T09:00:00Z',
          facts: {
            risk_score: 86,
            risk_level: '低风险',
            risk_detail: { lawsuit_count: 0, administrative_penalty_count: 0 },
            data_coverage: { available_dimensions: ['financial', 'judicial'] },
          },
        }, {
          evidence_id: 'financial-history-evidence',
          entity_id: 'entity:华东钢材供应有限公司',
          dimension: 'financial',
          provider: 'query_financials',
          source_type: 'financial_provider',
          status: 'available',
          data_mode: 'formal',
          collected_at: '2026-09-05T09:00:00Z',
          facts: {
            financial_history: [
              { period: '2024', revenue: 100, net_profit: 12 },
              { period: '2025', revenue: 120, net_profit: 10 },
            ],
          },
        }],
      })
      handlers.onDone?.({ answer: '已完成风险分析。' })
      return '已完成风险分析。'
    })
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByLabelText('向采购助手提问'), '分析供应商风险')
    await user.click(screen.getByRole('button', { name: '发送' }))

    expect(screen.getByText('86/100')).toBeInTheDocument()
    expect(screen.getByText('86/100').closest('tr')?.textContent).not.toContain('risk_score')
    expect(screen.getByText(/本轮围绕综合风险、财务风险形成 2 条判断，其中 2 条已有证据支持/)).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '指标/检查项' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '判断' })).toBeInTheDocument()
    expect(screen.getAllByText('综合风险评分').length).toBeGreaterThan(0)
    expect(screen.getByText('风险较低')).toBeInTheDocument()
    expect(screen.getAllByText('净利润同比增长率').length).toBeGreaterThan(0)
    expect(screen.getByText('需关注')).toBeInTheDocument()
    expect(screen.getByText('-10.0%').closest('tr')?.textContent).not.toContain('华东钢材供应有限公司')
    expect(screen.getAllByText('证据支持').length).toBeGreaterThan(0)
    await user.click(await screen.findByText('数据说明'))
    expect(screen.getByText('本次已评估的风险项')).toBeInTheDocument()
    expect(screen.getByText('司法风险')).toBeInTheDocument()
    expect(screen.getAllByText('财务数据').length).toBeGreaterThan(0)
    expect(screen.getAllByText('综合风险评分').length).toBeGreaterThan(0)
    expect(screen.getByText('综合风险评估')).toBeInTheDocument()
    expect(screen.getAllByText('已获取').length).toBeGreaterThan(0)
    expect(screen.getByText('上市供应商财务趋势')).toBeInTheDocument()
    expect(screen.getByText('营业收入')).toBeInTheDocument()
    expect(screen.getByText('净利润')).toBeInTheDocument()
  })

  it('translates sourcing claim fields into procurement wording', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      handlers.onAgentAnswer?.({
        status: 'completed',
        summary: '已完成寻源候选整理。',
        claims: [{
          claim_id: 'sourcing-name', entity_id: 'candidate:1', dimension: 'sourcing',
          statement: 'Supplier Name: 示例供应商', value: '示例供应商', fact_path: 'Supplier Name',
          evidence_refs: ['sourcing-evidence'], confidence: 0.9, validation_status: 'supported', validation_reasons: [],
        }],
        limitations: [], action_proposals: [], action_receipts: [], evidence_refs: ['sourcing-evidence'],
      })
      handlers.onDone?.({ answer: '已完成寻源候选整理。' })
      return '已完成寻源候选整理。'
    })
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByLabelText('向采购助手提问'), '寻找制动系统供应商')
    await user.click(screen.getByRole('button', { name: '发送' }))

    expect((await screen.findAllByText('供应商名称')).length).toBeGreaterThan(0)
    expect(screen.queryByText('Supplier Name')).not.toBeInTheDocument()
  })

  it('turns supplier review evidence into findings, basis, and procurement checks', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      handlers.onAgentAnswer?.({
        status: 'needs_review',
        summary: '分析已停止在证据复核点。',
        claims: [
          {
            claim_id: 'settlement-change', entity_id: 'supplier:8310163', dimension: 'business_risk',
            statement: '上海汽车制动系统有限公司 最新月实结算金额环比变化：-99.9%', value: -0.999, fact_path: 'settlement_change_ratio',
            evidence_refs: ['business-evidence'], confidence: 0.85, validation_status: 'supported', validation_reasons: [],
          },
          {
            claim_id: 'receipt-count', entity_id: 'supplier:8310163', dimension: 'business_risk',
            statement: '上海汽车制动系统有限公司 最新月收货记录数：32,602 条', value: 32602, fact_path: 'latest_received_record_count',
            evidence_refs: ['business-evidence'], confidence: 0.85, validation_status: 'supported', validation_reasons: [],
          },
        ],
        limitations: ['缺少 financial 维度的正式证据'], action_proposals: [], action_receipts: [], evidence_refs: ['business-evidence'],
      })
      handlers.onEvidence?.({
        records: [{
          evidence_id: 'business-evidence', entity_id: 'supplier:8310163', dimension: 'business_risk', provider: 'assess_business_risk',
          source_type: 'domain_service_result', status: 'available', data_mode: 'formal', collected_at: '2026-09-08T09:00:00Z',
          facts: {
            settlement_change_ratio: -0.999,
            latest_received_record_count: 32602,
            missing_month_count: 1,
            monthly_trend: [
              { month: '2026-07', actual_settlement_amount: 2460000, received_record_count: 36100 },
              { month: '2026-08', actual_settlement_amount: 2469.59, received_record_count: 32602 },
            ],
          },
        }],
      })
      handlers.onDone?.({ answer: '分析已停止在证据复核点。', status: 'needs_review' })
      return '分析已停止在证据复核点。'
    })
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByLabelText('向采购助手提问'), '复核上海汽车制动系统有限公司')
    await user.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByText('采购复核结论')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '建议复核' })).toBeInTheDocument()
    expect(screen.getByText('核对复核事项')).toBeInTheDocument()
    expect(screen.getByText('2 / 2 条已支持')).toBeInTheDocument()
    expect(screen.getByText('发现了什么')).toBeInTheDocument()
    expect(screen.getByText('依据是什么')).toBeInTheDocument()
    expect(screen.getByText('采购人员需要核实什么')).toBeInTheDocument()
    expect(screen.getByText('财务信息覆盖不足，当前无法判断其财务变化。')).toBeInTheDocument()
    expect(screen.getByText('最新月实结算金额环比显著下降，需要核实交易变化原因。')).toBeInTheDocument()
    expect(screen.getAllByText('最新月实结算金额环比变化').length).toBeGreaterThan(0)
    expect(screen.getByText('波动需复核')).toBeInTheDocument()
    expect(screen.getAllByText('商务风险')[0]).toHaveClass('bg-teal-50')
    expect(screen.getByText(/收货记录数仅表示源明细行数/)).toBeInTheDocument()
    expect(screen.getByText(/不将数据缺失判定为低风险或高风险/)).toBeInTheDocument()
    expect(screen.getByText('采购人员需要核实什么').closest('article')?.querySelector('ol')).toBeTruthy()
    expect(screen.getByText('供应商交易连续性趋势')).toBeInTheDocument()
    expect(screen.getByText('实结算金额（元）')).toBeInTheDocument()
    expect(screen.getByText('收货记录数（条）')).toBeInTheDocument()
    expect(screen.getByText('财务趋势暂未覆盖。')).toBeInTheDocument()
  })

  it('trusts the server terminal status instead of turning partial into completed', async () => {
    mocks.chatStream.mockImplementation(async (_message: string, _sessionId: string, handlers: StreamCallbacks) => {
      handlers.onAgentAnswer?.({
        status: 'partial',
        summary: '部分证据已完成',
        claims: [],
        limitations: ['部分维度缺少证据'],
        action_proposals: [],
        action_receipts: [],
        evidence_refs: [],
      })
      handlers.onDone?.({ answer: '部分证据已完成', status: 'partial', run_id: 'run-1' })
      return '部分证据已完成'
    })
    const user = userEvent.setup()
    renderChat()

    await user.type(screen.getByLabelText('向采购助手提问'), '分析供应商风险')
    await user.click(screen.getByRole('button', { name: '发送' }))

    await user.click(await screen.findByRole('button', { name: '执行详情' }))
    expect(await screen.findByText('当前状态：部分完成')).toBeInTheDocument()
    expect(screen.queryByText('当前状态：已完成')).not.toBeInTheDocument()
  })
})
