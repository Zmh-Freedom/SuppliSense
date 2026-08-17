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
    ...overrides,
  }
}

describe('AgentWorkflowPanel', () => {
  it('renders the five workflow phases and explicit agent statuses', () => {
    render(<AgentWorkflowPanel state={createState()} onApproval={vi.fn()} />)

    expect(screen.getByText('理解需求')).toBeInTheDocument()
    expect(screen.getByText('任务规划')).toBeInTheDocument()
    expect(screen.getAllByText('Agent 执行').length).toBeGreaterThan(0)
    expect(screen.getByText('证据汇总')).toBeInTheDocument()
    expect(screen.getByText('风险决策')).toBeInTheDocument()
    expect(screen.getByLabelText('寻源 Agent：已完成')).toBeInTheDocument()
    expect(screen.getByLabelText('风险 Agent：进行中')).toBeInTheDocument()
    expect(screen.getByLabelText('舆情 Agent：异常')).toBeInTheDocument()
  })

  it('shows approval details and sends approve or reject decisions', async () => {
    const onApproval = vi.fn()
    const user = userEvent.setup()
    render(<AgentWorkflowPanel state={createState({ approval })} onApproval={onApproval} />)

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
})
