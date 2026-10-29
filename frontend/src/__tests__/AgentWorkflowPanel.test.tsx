import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ApprovalData } from '../api'
import AgentWorkflowPanel from '../components/AgentWorkflowPanel'
import type { AgentWorkflowState } from '../components/AgentWorkflowPanel'
import type { AgentWorkflowSnapshot } from '../types'

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
  it('uses the latest actual workflow message for the concise live status line', () => {
    const state = createState()
    state.workflowStatus = {
      ...state.workflowStatus!,
      stage: 'evidence',
      message: '正在补充查询正式供应商目录',
    }

    render(<AgentWorkflowPanel state={state} onApproval={vi.fn()} />)

    expect(screen.getByRole('status')).toHaveTextContent('正在补充查询正式供应商目录')
    expect(screen.queryByText('当前阶段：证据汇总')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('分析进度里程碑')).not.toBeInTheDocument()
  })

  it('hides completed historical workflow details', () => {
    const state = createState()
    state.historical = true
    state.workflowStatus = {
      ...state.workflowStatus!,
      status: 'completed',
      stage: 'completed',
      message: '恢复的历史快照：本轮工作流已完成',
    }

    render(<AgentWorkflowPanel state={state} onApproval={vi.fn()} />)

    expect(screen.queryByText('查看处理过程')).not.toBeInTheDocument()
    expect(screen.queryByText(/历史记录/)).not.toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('does not present a saved running snapshot as a live run or historical status card', () => {
    const state = createState({ historical: true })
    state.workflowStatus = {
      ...state.workflowStatus!,
      status: 'running',
      message: '上次事件：正在执行工具',
    }

    render(<AgentWorkflowPanel state={state} onApproval={vi.fn()} />)

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.queryByText(/历史记录/)).not.toBeInTheDocument()
    expect(screen.queryByText('查看处理过程')).not.toBeInTheDocument()
  })

  it('safely hides completed legacy snapshots that lack optional progress arrays', () => {
    const legacySnapshot = {
      status: 'completed',
      stage: 'completed',
      message: '旧版本保存的状态',
    } as AgentWorkflowSnapshot

    expect(() => render(<AgentWorkflowPanel state={createState({ workflowStatus: legacySnapshot })} onApproval={vi.fn()} />)).not.toThrow()
    expect(screen.queryByText('旧版本保存的状态')).not.toBeInTheDocument()
    expect(screen.queryByText('查看处理过程')).not.toBeInTheDocument()
  })

  it.each([
    ['failed', '执行遇到问题'],
    ['cancelled', '本轮已取消'],
  ])('keeps %s distinct without inferred milestones', (status, expectedLabel) => {
    const state = createState()
    state.workflowStatus = {
      ...state.workflowStatus!,
      status,
      message: `服务端状态：${status}`,
    }

    render(<AgentWorkflowPanel state={state} onApproval={vi.fn()} />)

    expect(screen.getByText(expectedLabel)).toBeInTheDocument()
    const statusRegion = status === 'failed' ? screen.getByRole('alert') : screen.getByRole('status')
    expect(statusRegion).toHaveTextContent(`服务端状态：${status}`)
    expect(screen.queryByText('查看处理过程')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('分析进度里程碑')).not.toBeInTheDocument()
  })

  it('shows one concise progress line while a run is active', () => {
    render(<AgentWorkflowPanel state={createState()} onApproval={vi.fn()} />)

    expect(screen.getByRole('status')).toHaveTextContent('正在汇总证据')
    expect(screen.queryByText('当前阶段：证据汇总')).not.toBeInTheDocument()
    expect(screen.queryByText('分析对象：示例供应商')).not.toBeInTheDocument()
    expect(screen.queryByText('工具查询：已完成 1/2 项')).not.toBeInTheDocument()
    expect(screen.queryByText('查看处理过程')).not.toBeInTheDocument()
    expect(screen.queryByText('风险 Agent')).not.toBeInTheDocument()
  })

  it('hides completed workflow detail text', () => {
    const state = createState({ historical: true })
    state.workflowStatus = {
      ...state.workflowStatus!,
      status: 'completed',
      stage: 'completed',
      message: '已完成实际记录的核验',
    }

    render(<AgentWorkflowPanel state={state} onApproval={vi.fn()} />)

    expect(screen.queryByText('查看处理过程')).not.toBeInTheDocument()
    expect(screen.queryByText('当前状态：本轮查询已完成')).not.toBeInTheDocument()
    expect(screen.queryByText('已完成实际记录的核验')).not.toBeInTheDocument()
  })

  it('keeps parsed fields in the saved snapshot without exposing them in completed history', () => {
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

    state.workflowStatus = { ...state.workflowStatus!, status: 'completed', stage: 'completed' }
    state.historical = true
    render(<AgentWorkflowPanel state={state} onApproval={vi.fn()} />)

    expect(state.workflowStatus.inputUnderstanding?.target_supplier_names).toEqual(['甲电机有限公司'])
    expect(screen.queryByText('查看处理过程')).not.toBeInTheDocument()
    expect(screen.queryByText('甲电机有限公司')).not.toBeInTheDocument()
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
    expect(screen.getByRole('status')).toHaveTextContent('结果需要人工复核')
    expect(screen.queryByText('风险决策')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('分析进度里程碑')).not.toBeInTheDocument()
    expect(screen.queryByText(/已用时/)).not.toBeInTheDocument()
    expect(screen.getByRole('status').querySelector('span')).toHaveClass('bg-amber-500')
  })

  it('shows the latest server message when evidence remediation returns to execution', () => {
    const state = createState()
    const { rerender } = render(<AgentWorkflowPanel state={state} onApproval={vi.fn()} />)

    state.workflowStatus = { ...state.workflowStatus!, stage: 'executing', message: '正在补充查询', toolCallCount: 3 }
    rerender(<AgentWorkflowPanel state={state} onApproval={vi.fn()} />)

    expect(screen.getByRole('status')).toHaveTextContent('正在补充查询')
    expect(screen.queryByText('Agent 执行')).not.toBeInTheDocument()
  })

  it('shows clarification as waiting for user input', () => {
    const state = createState()
    state.workflowStatus = { ...state.workflowStatus!, status: 'clarifying', stage: 'understand', message: '请补充供应商名称' }

    render(<AgentWorkflowPanel state={state} onApproval={vi.fn()} />)

    expect(screen.getByText('需要补充信息')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('请补充供应商名称')
    expect(screen.queryByText('查看处理过程')).not.toBeInTheDocument()
  })

  it('shows approval details and sends approve or reject decisions', async () => {
    const onApproval = vi.fn()
    const user = userEvent.setup()
    render(<AgentWorkflowPanel state={createState({ approval })} onApproval={onApproval} />)

    expect(screen.getByRole('button', { name: '批准动作' })).toBeVisible()
    expect(screen.getByRole('heading', { name: '需要人工确认' })).toBeInTheDocument()
    expect(screen.getByText('动作：add_to_watchlist')).toBeInTheDocument()
    expect(screen.queryByText('查看处理过程')).not.toBeInTheDocument()
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
