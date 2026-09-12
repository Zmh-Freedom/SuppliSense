import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import AgentExecutionTrace from '../components/AgentExecutionTrace';

describe('AgentExecutionTrace', () => {
  it('shows a clear empty state when no durable trace is available', () => {
    render(<AgentExecutionTrace events={[]} />);

    expect(screen.getByText('暂未收到可展示的执行事件。任务开始后，这里会显示任务进度、证据校验与人工审批状态。')).toBeInTheDocument();
  });

  it('renders safe agent and graph trace fields while excluding private model fields', () => {
    render(<AgentExecutionTrace events={[
      { eventId: 1, source: 'agent_trace', kind: 'plan_created', status: 'INVESTIGATING', message: '已生成可执行任务计划', data: { task_count: 2, task_ids: ['risk-1', 'esg-1'], target_supplier_names: ['示例供应商'], analysis_dimensions: ['risk', 'esg'], reasoning: 'must never be shown' } },
      { eventId: 2, source: 'agent_trace', kind: 'loop_evaluated', status: 'EVIDENCE_REVIEW', message: '已评估证据补全循环', data: { iteration: 1, max_iterations: 2, tool_call_count: 3, stop_reason: 'evidence_sufficient' } },
      { eventId: 3, source: 'agent_trace', kind: 'validator_completed', status: 'NEEDS_REVIEW', message: '已完成证据校验', data: { can_recommend: false, missing_requirements: ['风险证据'], prompt: 'must never be shown' } },
      { eventId: 4, source: 'graph_trace', kind: 'identity_checked', status: 'INVESTIGATING', message: '工作流节点：identity_checked', data: { node: 'identity', raw_response: 'must never be shown' }, atMs: 42 },
    ]} />);

    expect(screen.getByText('任务矩阵：0/2')).toBeInTheDocument();
    expect(screen.getByText('示例供应商')).toBeInTheDocument();
    expect(screen.getByText(/分析范围：风险、可持续性/)).toBeInTheDocument();
    expect(screen.getByText('第 1 / 2 轮，工具调用 3 次；停止原因：证据已足够')).toBeInTheDocument();
    expect(screen.getAllByText('缺少条件：风险证据')).toHaveLength(2);
    expect(screen.getByText('工作流节点事件 · 42 ms')).toBeInTheDocument();
    expect(screen.queryByText('must never be shown')).not.toBeInTheDocument();
  });
});
