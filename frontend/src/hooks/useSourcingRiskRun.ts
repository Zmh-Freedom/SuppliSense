import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { agentRunEventStream, api } from '../api';
import { queryKeys } from '../query-keys';
import type { AgentRunEvent, SourcingRiskAgentRun, SourcingRiskRequirement } from '../types';

const EVENT_CURSOR_PREFIX = 'agent_run_event_cursor:';
const RECONNECT_DELAY_MS = 1_000;

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
    ...(Array.isArray(payload.candidates) ? { candidates: payload.candidates as SourcingRiskAgentRun['candidates'] } : {}),
    ...(Array.isArray(payload.decisions) ? { decisions: payload.decisions as SourcingRiskAgentRun['decisions'] } : {}),
    ...(Array.isArray(payload.proposals) ? { proposals: payload.proposals as SourcingRiskAgentRun['proposals'] } : {}),
    ...(event.eventType === 'identity_review' ? { status: 'IDENTITY_REVIEW', next_action: 'identity_review_required' } : {}),
  };
}

export function useSourcingRiskRun(initialRunId?: string) {
  const queryClient = useQueryClient();
  const retryTimer = useRef<number | null>(null);
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
      const storedCursor = sessionStorage.getItem(cursorKey);
      const lastEventId = storedCursor ? Number(storedCursor) : null;
      await agentRunEventStream(initialRunId, Number.isFinite(lastEventId) ? lastEventId : null, {
        onEvent: (event) => {
          sessionStorage.setItem(cursorKey, String(event.eventId));
          queryClient.setQueryData<SourcingRiskAgentRun>(queryKeys.agentRunDetail(initialRunId), current =>
            current ? applyEvent(current, event) : current,
          );
        },
        onDone: () => queryClient.invalidateQueries({ queryKey: queryKeys.agentRunDetail(initialRunId) }),
        onError: () => {
          if (!stopped) retryTimer.current = window.setTimeout(connect, RECONNECT_DELAY_MS);
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

  return { ...query, createRun, refresh, runIdOf };
}
