import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ComponentType } from 'react';
import type { AgentAnswer, AgentEvidenceRecord, AgentWorkflowSnapshot, ChatMessage } from '../types';
import ChatMessageList from '../components/ChatMessageList';
import StructuredAgentResult from '../components/StructuredAgentResult';
import type { ChatStreamViewState } from '../components/ChatMessageList';

const verifiedAnswer: AgentAnswer = {
  status: 'completed',
  summary: '华东钢材供应有限公司的综合风险评分为 73。',
  claims: [{
    claim_id: 'risk-score',
    entity_id: 'supplier:steel',
    dimension: 'risk',
    statement: '综合风险评分为 73。',
    value: 73,
    fact_path: 'risk_score',
    evidence_refs: ['risk-evidence'],
    confidence: 0.9,
    validation_status: 'supported',
    validation_reasons: [],
  }],
  limitations: [],
  action_proposals: [],
  action_receipts: [],
  evidence_refs: ['risk-evidence'],
};

const verifiedEvidence: AgentEvidenceRecord = {
  evidence_id: 'risk-evidence',
  entity_id: 'supplier:steel',
  dimension: 'risk',
  provider: 'assess_risk',
  source_type: 'risk_service_result',
  status: 'available',
  data_mode: 'formal',
  collected_at: '2026-10-29T00:00:00Z',
};

function withRiskLayout(): AgentAnswer {
  const layout = { v: 1, b: [
    { t: 'overview', g: 'summary' },
    { t: 'fact_list', g: 'risk', c: [0] },
  ] };
  const bytes = new TextEncoder().encode(JSON.stringify(layout));
  let binary = '';
  bytes.forEach(byte => { binary += String.fromCharCode(byte); });
  const marker = `[//]: # "ss-answer-layout:v1:${btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '')}"`;
  return { ...verifiedAnswer, summary: `${verifiedAnswer.summary}\n\n${marker}` };
}

const noEvidenceConversation: AgentAnswer = {
  status: 'completed',
  summary: '我可以帮你查询和分析供应商风险。',
  claims: [],
  limitations: [],
  action_proposals: [],
  action_receipts: [],
  evidence_refs: [],
};

const noVerifiedEvidenceCases: Array<{ name: string; answer: AgentAnswer; evidence: AgentEvidenceRecord[] }> = [
  { name: 'ordinary conversation', answer: noEvidenceConversation, evidence: [] },
  { name: 'missing evidence record', answer: verifiedAnswer, evidence: [] },
  {
    name: 'unsupported claim',
    answer: { ...verifiedAnswer, claims: [{ ...verifiedAnswer.claims[0], validation_status: 'unsupported' }] },
    evidence: [verifiedEvidence],
  },
  {
    name: 'failed answer',
    answer: { ...verifiedAnswer, status: 'failed' },
    evidence: [verifiedEvidence],
  },
  {
    name: 'synthetic evidence',
    answer: verifiedAnswer,
    evidence: [{ ...verifiedEvidence, data_mode: 'synthetic' }],
  },
  {
    name: 'unavailable evidence',
    answer: verifiedAnswer,
    evidence: [{ ...verifiedEvidence, status: 'unavailable' }],
  },
  {
    name: 'evidence bound to another entity',
    answer: verifiedAnswer,
    evidence: [{ ...verifiedEvidence, entity_id: 'supplier:other' }],
  },
  {
    name: 'expired evidence',
    answer: verifiedAnswer,
    evidence: [{ ...verifiedEvidence, valid_until: '2020-01-01T00:00:00Z' }],
  },
];

function ResultStub({ answer }: { answer?: AgentAnswer; evidence?: AgentEvidenceRecord[] }) {
  return answer?.claims.length ? <div data-testid="business-result">{answer.summary}</div> : null;
}

const surfaces = [
  {
    name: 'historical message',
    render(answer: AgentAnswer, evidence: AgentEvidenceRecord[]) {
      const message: ChatMessage = { role: 'assistant', content: answer.summary, agentAnswer: answer, evidence };
      renderList({ messages: [message], streamState: null, loading: false });
    },
  },
  {
    name: 'live stream',
    render(answer: AgentAnswer, evidence: AgentEvidenceRecord[]) {
      const streamState: ChatStreamViewState = {
        thinking: '',
        plan: null,
        agents: null,
        toolCalls: [],
        answerChunks: [answer.summary],
        answerStarted: true,
        approval: null,
        approvalSubmitting: false,
        charts: [],
        references: [],
        agentAnswer: answer,
        evidence,
        workflowStatus: {
          status: 'completed',
          message: '',
          targetSuppliers: [],
          sources: [],
          toolCallCount: 0,
          completedToolCount: 0,
        },
      };
      renderList({ messages: [], streamState, loading: true });
    },
  },
] as const;

function renderList({ messages, streamState, loading }: {
  messages: ChatMessage[];
  streamState: ChatStreamViewState | null;
  loading: boolean;
}, resultComponent: ComponentType<{ answer?: AgentAnswer; evidence?: AgentEvidenceRecord[] }> = ResultStub as ComponentType<{ answer?: AgentAnswer; evidence?: AgentEvidenceRecord[] }>) {
  render(<ChatMessageList
    messages={messages}
    streamState={streamState}
    loading={loading}
    onAnalyzeReference={() => undefined}
    onConfirmSupplier={() => undefined}
    onApproval={() => undefined}
    StructuredAgentResult={resultComponent}
  />);
}

describe.each(surfaces)('$name', surface => {
  it.each(noVerifiedEvidenceCases)('does not show verified-analysis labels for $name', testCase => {
    surface.render(testCase.answer, testCase.evidence);

    expect(screen.queryByText('采购分析')).not.toBeInTheDocument();
    expect(screen.queryByText('基于已核验数据生成')).not.toBeInTheDocument();
    if (testCase.answer.claims.length > 0) {
      expect(screen.getByTestId('business-result')).toHaveTextContent(testCase.answer.summary);
    }
  });

  it('labels a supported business claim only when its evidence is available and formal', () => {
    surface.render(verifiedAnswer, [verifiedEvidence]);

    expect(screen.getByText('采购分析')).toBeInTheDocument();
    expect(screen.getByText('基于已核验数据生成')).toBeInTheDocument();
    expect(screen.getByTestId('business-result')).toHaveTextContent(verifiedAnswer.summary);
  });

  it('renders the same validated layout for persisted messages and live SSE answers', () => {
    surface.render(withRiskLayout(), [verifiedEvidence]);

    expect(screen.getByRole('group', { name: '综合风险' })).toBeInTheDocument();
    expect(screen.getByText('综合风险评分为 73。')).toBeInTheDocument();
  });
});

describe('saved workflow status', () => {
  it('keeps a clarification visible when the answer itself has no business claims', () => {
    const message: ChatMessage = {
      role: 'assistant',
      content: '请补充供应商名称。',
      agentAnswer: { ...noEvidenceConversation, summary: '已完成供应商分析。' },
      workflow: {
        status: 'clarifying',
        stage: 'understand',
        message: '请补充供应商名称。',
        targetSuppliers: [],
        sources: [],
        toolCallCount: 0,
        completedToolCount: 0,
      },
    };

    renderList({ messages: [message], streamState: null, loading: false });

    expect(screen.getByRole('status')).toHaveTextContent('需要补充信息');
    expect(screen.getByRole('status')).toHaveTextContent('请补充供应商名称。');
    expect(screen.queryByText('查看处理过程')).not.toBeInTheDocument();
    expect(screen.queryByText(/历史记录/)).not.toBeInTheDocument();
  });

  it('hides completed history workflow UI without deleting saved state or evidence', () => {
    const workflow: AgentWorkflowSnapshot = {
      status: 'completed',
      stage: 'completed',
      message: '完成实际记录的风险核验',
      targetSuppliers: ['华东钢材供应有限公司'],
      sources: ['local_snapshot'],
      toolCallCount: 1,
      completedToolCount: 1,
    };
    const evidence = [verifiedEvidence];
    const message: ChatMessage = {
      role: 'assistant',
      content: '已完成供应商分析。',
      agentAnswer: { ...noEvidenceConversation, summary: '已完成供应商分析。' },
      workflow,
      evidence,
    };

    renderList({ messages: [message], streamState: null, loading: false });

    expect(screen.getByText('已完成供应商分析。')).toBeInTheDocument();
    expect(screen.queryByText(/历史记录/)).not.toBeInTheDocument();
    expect(screen.queryByText('查看处理过程')).not.toBeInTheDocument();
    expect(message.workflow).toBe(workflow);
    expect(message.evidence).toBe(evidence);
  });
});

describe.each(['historical', 'live'] as const)('%s final result status', surface => {
  it.each([
    ['needs_review', '结果需要人工复核'],
    ['partial', '本轮 Agent 仅完成部分分析'],
  ] as const)('does not repeat the generic %s status after the result', (status, genericMessage) => {
    const answer: AgentAnswer = {
      ...noEvidenceConversation,
      status,
      summary: '甲公司存在已确认的履约异常，但当前资料不足以判断后续交付影响。',
      limitations: ['尚未取得后续订单的交付记录，无法判断履约异常是否持续。'],
    };
    const workflow: AgentWorkflowSnapshot = {
      status,
      stage: 'decision',
      message: genericMessage,
      targetSuppliers: ['甲公司'],
      sources: ['delivery_records'],
      toolCallCount: 1,
      completedToolCount: 1,
    };

    if (surface === 'historical') {
      renderList({
        messages: [{ role: 'assistant', content: answer.summary, agentAnswer: answer, workflow }],
        streamState: null,
        loading: false,
      }, StructuredAgentResult);
    } else {
      const streamState: ChatStreamViewState = {
        thinking: '', plan: null, agents: null, toolCalls: [],
        answerChunks: [answer.summary], answerStarted: true,
        approval: null, approvalSubmitting: false, charts: [], references: [],
        agentAnswer: answer, evidence: [], workflowStatus: workflow,
      };
      renderList({ messages: [], streamState, loading: true }, StructuredAgentResult);
    }

    expect(screen.getByText(answer.summary)).toBeInTheDocument();
    expect(screen.getByText(answer.limitations[0])).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.queryByText(genericMessage)).not.toBeInTheDocument();
  });
});
