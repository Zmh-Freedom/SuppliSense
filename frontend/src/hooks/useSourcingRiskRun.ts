import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { agentRunEventStream, api } from '../api';
import { queryKeys } from '../query-keys';
import type { AgentRunEvent, AgentTraceEvent, SourcingRiskAgentRun, SourcingRiskRequirement } from '../types';

const EVENT_CURSOR_PREFIX = 'agent_run_event_cursor:';
const RECONNECT_DELAY_MS = 1_000;
const TERMINAL_STATUSES = new Set(['COMPLETED', 'PARTIAL', 'NEEDS_REVIEW', 'ACTION_FAILED', 'FAILED', 'CANCELLED']);

function runIdOf(run: SourcingRiskAgentRun): string | undefined {
  return run.id ?? run.run_id;
}

function applyEvent(run: SourcingRiskAgentRun, event: AgentRunEvent): SourcingRiskAgentRun {
  const payload = event.data;
  const embeddedRun = payload.run;
  if (embeddedRun && typeof embeddedRun === 'object') return { ...run, ...(embeddedRun as Partial<SourcingRiskAgentRun>) };

  return {
    ...run,
    ...(typeof payload.status === 'string' ? { status: payload.status } : {}),
    ...(typeof payload.version === 'number' ? { version: payload.version } : {}),
    ...(Array.isArray(payload.missing)
      ? { missing_fields: payload.missing.filter(item => typeof item === 'string') as string[] }
      : Array.isArray(payload.missing_fields)
        ? { missing_fields: payload.missing_fields.filter(item => typeof item === 'string') as string[] }
        : {}),
    ...(Array.isArray(payload.candidates) ? { candidates: payload.candidates as SourcingRiskAgentRun['candidates'] } : {}),
    ...(Array.isArray(payload.decisions) ? { decisions: payload.decisions as SourcingRiskAgentRun['decisions'] } : {}),
    ...(Array.isArray(payload.proposals) ? { proposals: payload.proposals as SourcingRiskAgentRun['proposals'] } : {}),
    ...(Array.isArray(payload.action_proposals) ? { action_proposals: payload.action_proposals as SourcingRiskAgentRun['action_proposals'] } : {}),
    ...(typeof payload.evidence_by_company_id === 'object' && payload.evidence_by_company_id !== null
      ? { evidence_by_company_id: payload.evidence_by_company_id as SourcingRiskAgentRun['evidence_by_company_id'] } : {}),
    ...(typeof payload.evidence_reviews === 'object' && payload.evidence_reviews !== null
      ? { evidence_reviews: payload.evidence_reviews as SourcingRiskAgentRun['evidence_reviews'] } : {}),
    ...(Array.isArray(payload.approvals) ? { approvals: payload.approvals as SourcingRiskAgentRun['approvals'] } : {}),
    ...(event.eventType === 'clarification'
      ? { status: 'CLARIFYING', next_action: 'clarification_required' }
      : {}),
    ...(event.eventType === 'identity_review' ? { status: 'IDENTITY_REVIEW', next_action: 'identity_review_required' } : {}),
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function traceEventFrom(event: AgentRunEvent): AgentTraceEvent | null {
  if (event.eventType === 'agent_trace') {
    const kind = typeof event.data.kind === 'string' ? event.data.kind : null;
    const status = typeof event.data.status === 'string' ? event.data.status : null;
    const message = typeof event.data.message === 'string' ? event.data.message : null;
    if (!kind || !status || !message) return null;
    return {
      eventId: event.eventId,
      kind,
      status,
      message,
      data: asRecord(event.data.data) ?? {},
      source: 'agent_trace',
    };
  }
  if (event.eventType !== 'graph_trace') return null;
  const kind = typeof event.data.type === 'string' ? event.data.type : null;
  if (!kind) return null;
  const payload = asRecord(event.data.payload) ?? {};
  return {
    eventId: event.eventId,
    kind,
    status: typeof payload.status === 'string' ? payload.status : '执行中',
    message: `工作流节点：${kind}`,
    data: payload,
    source: 'graph_trace',
    atMs: typeof event.data.at_ms === 'number' ? event.data.at_ms : undefined,
  };
}

export function useSourcingRiskRun(initialRunId?: string) {
  const queryClient = useQueryClient();
  const retryTimer = useRef<number | null>(null);
  const [traceState, setTraceState] = useState<{ runId?: string; events: AgentTraceEvent[] }>({ events: [] });
  const query = useQuery({
    queryKey: queryKeys.agentRunDetail(initialRunId ?? ''),
    queryFn: () => api.get<SourcingRiskAgentRun>(`/agent-runs/${encodeURIComponent(initialRunId!)}`),
    enabled: Boolean(initialRunId),
  });

  const createRun = useMutation({
    mutationFn: (requirement: SourcingRiskRequirement) => api.post<SourcingRiskAgentRun>('/agent-runs', requirement),
  });

  useEffect(() => {
    if (!initialRunId) return;
    const controller = new AbortController();
    const cursorKey = `${EVENT_CURSOR_PREFIX}${initialRunId}`;
    let stopped = false;

    const connect = async () => {
      let terminal = false;
      const storedCursor = sessionStorage.getItem(cursorKey);
      const lastEventId = storedCursor ? Number(storedCursor) : null;
      await agentRunEventStream(initialRunId, Number.isFinite(lastEventId) ? lastEventId : null, {
        onEvent: (event) => {
          terminal = typeof event.data.status === 'string' && TERMINAL_STATUSES.has(event.data.status);
          sessionStorage.setItem(cursorKey, String(event.eventId));
          queryClient.setQueryData<SourcingRiskAgentRun>(queryKeys.agentRunDetail(initialRunId), current =>
            current ? applyEvent(current, event) : current,
          );
          const traceEvent = traceEventFrom(event);
          if (traceEvent) {
            setTraceState(current => {
              if (current.runId !== initialRunId) return { runId: initialRunId, events: [traceEvent] };
              return current.events.some(item => item.eventId === traceEvent.eventId)
                ? current
                : { ...current, events: [...current.events, traceEvent] };
            });
          }
        },
        onDone: () => queryClient.invalidateQueries({ queryKey: queryKeys.agentRunDetail(initialRunId) }),
        onError: () => {
          if (!stopped && !terminal) retryTimer.current = window.setTimeout(connect, RECONNECT_DELAY_MS);
        },
      }, controller.signal);
    };

    void connect();
    return () => {
      stopped = true;
      controller.abort();
      if (retryTimer.current !== null) window.clearTimeout(retryTimer.current);
    };
  }, [initialRunId, queryClient]);

  const refresh = () => initialRunId
    ? queryClient.invalidateQueries({ queryKey: queryKeys.agentRunDetail(initialRunId) })
    : Promise.resolve();

  return { ...query, createRun, refresh, runIdOf, traceEvents: traceState.runId === initialRunId ? traceState.events : [] };
}
