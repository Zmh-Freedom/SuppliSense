import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ComponentType } from 'react';
import type { AgentAnswer, AgentEvidenceRecord, ChatMessage } from '../types';
import ChatMessageList from '../components/ChatMessageList';
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
}) {
  render(<ChatMessageList
    messages={messages}
    streamState={streamState}
    loading={loading}
    onAnalyzeReference={() => undefined}
    onConfirmSupplier={() => undefined}
    onApproval={() => undefined}
    StructuredAgentResult={ResultStub as ComponentType<{ answer?: AgentAnswer; evidence?: AgentEvidenceRecord[] }>}
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
});
