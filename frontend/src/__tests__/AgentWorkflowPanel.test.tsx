import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ApprovalData } from '../api'
import AgentWorkflowPanel from '../components/AgentWorkflowPanel'
import type { AgentWorkflowState } from '../components/AgentWorkflowPanel'

const approval: ApprovalData = {
  message: '准备将候选供应商加入监控清单',
  tool: 'add_to_watchlist',
  args: { company_name: '示例供应商' },
  session_id: 'session-1',
}

const externalIdentityApproval: ApprovalData = {
  message: '确认执行待审批的供应商操作？',
  tool: 'agent_supervisor',
  args: {
    pending_approvals: [{
      action_type: 'add_watchlist',
      target: {
        company_name: '赛克瑞浦动力电池系统有限公司',
        external_identity: {
          company_name: '赛克瑞浦动力电池系统有限公司',
          unified_social_credit_code: '91450200MAA7L76A5R',
          registration_status: '存续',
          legal_person: '廖鸿胡',
          source: '天眼查工商主体查询',
        },
      },
    }],
  },
  session_id: 'session-1',
}

function createState(overrides: Partial<AgentWorkflowState> = {}): AgentWorkflowState {
  return {
    thinking: '正在汇总证据',
    plan: [{ tool: 'assess_risk', args: { company_name: '示例供应商' } }],
    agents: {
      selected: ['sourcing', 'risk', 'compliance', 'sentiment'],
      reasoning: '并行检查供应商风险',
      status: { sourcing: 'complete', risk: 'running', compliance: 'complete', sentiment: 'error' },
    },
    toolCalls: [{ tool: 'assess_risk', args: { company_name: '示例供应商' }, result: { score: 42 } }],
    answerStarted: false,
    approval: null,
    approvalSubmitting: false,
    done: false,
    workflowStatus: {
      status: 'running',
      stage: 'evidence',
      message: '正在汇总证据',
      targetSuppliers: ['示例供应商'],
      sources: ['本地快照'],
      toolCallCount: 2,
      completedToolCount: 1,
      evidenceStatus: '1/2 已覆盖',
      loopExitReason: 'evidence_sufficient',
    },
    ...overrides,
  }
}

describe('AgentWorkflowPanel', () => {
  it('renders a risk-focused milestone summary and keeps execution details collapsed', async () => {
    render(<AgentWorkflowPanel state={createState()} onApproval={vi.fn()} />)

    expect(screen.getByText('确认分析对象')).toBeInTheDocument()
    expect(screen.getByText('查询企业和风险信息')).toBeInTheDocument()
    expect(screen.getByText('核对近期风险证据')).toBeInTheDocument()
    expect(screen.getByText('整理风险要点和来源')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('正在核对近期风险证据')
    expect(screen.getByText(/已完成\s*1\s*项查询/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看执行详情' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('风险 Agent')).not.toBeInTheDocument()

    await userEvent.setup().click(screen.getByRole('button', { name: '查看执行详情' }))
    expect(screen.getByText('当前状态：正在处理')).toBeInTheDocument()
    expect(screen.getByLabelText('风险 Agent：进行中')).toBeInTheDocument()
    expect(screen.getByLabelText('舆情 Agent：异常')).toBeInTheDocument()
    expect(screen.getByText('本轮结果：所需证据已覆盖')).toBeInTheDocument()
    expect(screen.getByText('1. 综合风险核验')).toBeInTheDocument()
  })

  it('shows parsed fields and whether each came from this turn or conversation context', async () => {
    const state = createState()
    state.workflowStatus = {
      ...state.workflowStatus!,
      inputUnderstanding: {
        task_type: 'analysis',
        capability: 'financial',
        target_supplier_names: ['甲电机有限公司'],
        analysis_dimensions: ['financial'],
        interpretation_summary: '用户想看甲电机有限公司的财务表现。',
        ambiguities: ['时间范围没有明确指定。'],
        field_sources: {
          target_supplier_names: 'conversation_state',
          analysis_dimensions: 'explicit',
        },
        confidence: 0.91,
      },
    }

    render(<AgentWorkflowPanel state={state} onApproval={vi.fn()} />)
    await userEvent.setup().click(screen.getByRole('button', { name: '查看执行详情' }))

    expect(screen.getByText('本轮理解')).toBeInTheDocument()
    expect(screen.getByText('甲电机有限公司')).toBeInTheDocument()
    expect(screen.getByText('会话上下文')).toBeInTheDocument()
    expect(screen.getByText('本轮输入')).toBeInTheDocument()
    expect(screen.getByText(/财务分析/)).toBeInTheDocument()
    expect(screen.getByText('用户想看甲电机有限公司的财务表现。')).toBeInTheDocument()
    expect(screen.getByLabelText('理解中的歧义')).toHaveTextContent('时间范围没有明确指定。')
  })

  it('keeps review as an explicit human review state instead of marking it as a decision complete', () => {
    const state = createState()
    state.workflowStatus = {
      ...state.workflowStatus!,
      status: 'needs_review',
      stage: 'decision',
      message: '结果需要人工复核',
    }

    render(<AgentWorkflowPanel state={state} onApproval={vi.fn()} />)
    expect(screen.getByText('需要人工复核')).toBeInTheDocument()
    expect(screen.getByText(/最终判断由你确认/)).toBeInTheDocument()
    expect(screen.getByLabelText('整理风险要点和来源：待你处理')).toBeInTheDocument()
    expect(screen.queryByText(/已用时/)).not.toBeInTheDocument()
    expect(screen.getByRole('status').querySelector('span')).toHaveClass('bg-amber-500')
  })

  it('uses sourcing milestones for a sourcing request', () => {
    render(<AgentWorkflowPanel state={createState({ queryText: '帮我找几家可替代的供应商' })} onApproval={vi.fn()} />)

    expect(screen.getByText('确认采购条件')).toBeInTheDocument()
    expect(screen.getByText('搜索候选供应商')).toBeInTheDocument()
    expect(screen.getByText('核对交付与风险信息')).toBeInTheDocument()
    expect(screen.getByText('整理候选对比和来源')).toBeInTheDocument()
  })

  it('uses comparison milestones for a supplier comparison request', () => {
    render(<AgentWorkflowPanel state={createState({ queryText: '对比示例供应商和华东精密的风险差异' })} onApproval={vi.fn()} />)

    expect(screen.getByText('确认对比对象')).toBeInTheDocument()
    expect(screen.getByText('收集双方相关数据')).toBeInTheDocument()
    expect(screen.getByText('对齐风险与业务差异')).toBeInTheDocument()
    expect(screen.getByText('整理对比结果和来源')).toBeInTheDocument()
  })

  it('does not move the active milestone backward when evidence remediation returns to execution', () => {
    const state = createState()
    const { rerender } = render(<AgentWorkflowPanel state={state} onApproval={vi.fn()} />)

    state.workflowStatus = { ...state.workflowStatus!, stage: 'executing', message: '正在补充查询', toolCallCount: 3 }
    rerender(<AgentWorkflowPanel state={state} onApproval={vi.fn()} />)

    expect(screen.getByRole('status')).toHaveTextContent('正在核对近期风险证据')
    expect(screen.getByLabelText('核对近期风险证据：进行中')).toBeInTheDocument()
  })

  it('shows clarification as waiting for user input', () => {
    const state = createState()
    state.workflowStatus = { ...state.workflowStatus!, status: 'clarifying', stage: 'understand', message: '请补充供应商名称' }

    render(<AgentWorkflowPanel state={state} onApproval={vi.fn()} />)

    expect(screen.getByText('需要补充信息')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('请补充供应商名称')
  })

  it('shows approval details and sends approve or reject decisions', async () => {
    const onApproval = vi.fn()
    const user = userEvent.setup()
    render(<AgentWorkflowPanel state={createState({ approval })} onApproval={onApproval} />)

    expect(screen.getByRole('button', { name: '批准动作' })).toBeVisible()
    expect(screen.getByRole('heading', { name: '需要人工确认' })).toBeInTheDocument()
    expect(screen.getByText('动作：add_to_watchlist')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '批准动作' }))
    expect(onApproval).toHaveBeenCalledWith(true)
    await user.click(screen.getByRole('button', { name: '拒绝动作' }))
    expect(onApproval).toHaveBeenCalledWith(false)
  })

  it('disables both approval actions and exposes submitting state', () => {
    render(<AgentWorkflowPanel state={createState({ approval, approvalSubmitting: true })} onApproval={vi.fn()} />)

    expect(screen.getByRole('button', { name: '批准提交中…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '拒绝提交中…' })).toBeDisabled()
  })

  it('renders nested external identity approval details from supervisor payloads', () => {
    render(<AgentWorkflowPanel state={createState({ approval: externalIdentityApproval })} onApproval={vi.fn()} />)

    expect(screen.getByText('请确认外部企业主体')).toBeInTheDocument()
    expect(screen.getByText('统一社会信用代码：91450200MAA7L76A5R')).toBeInTheDocument()
    expect(screen.getByText('登记状态：存续')).toBeInTheDocument()
    expect(screen.getByText('法定代表人：廖鸿胡')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认主体并加入监控' })).toBeInTheDocument()
    expect(screen.queryByText(/目标\/参数：/)).not.toBeInTheDocument()
  })
})
