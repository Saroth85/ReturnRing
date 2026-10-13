// The agent stream (US-701, docs/ui-design.md 7): POST /api/agent/ask read with fetch and the
// SSE parser; aborted when the drawer closes or a new question is asked.
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/api/client';
import type { AgentEvent } from '@/api/models';

export interface AgentStreamState {
  status: 'idle' | 'streaming' | 'done' | 'error';
  events: AgentEvent[];
  error: unknown;
}

export function useAgentStream(returnId: string, askedBy: string) {
  const [state, setState] = useState<AgentStreamState>({ status: 'idle', events: [], error: null });
  const controller = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
  }, []);

  const ask = useCallback(
    async (question: string) => {
      cancel();
      const current = new AbortController();
      controller.current = current;
      setState({ status: 'streaming', events: [], error: null });
      try {
        for await (const event of api.askAgent({ return_id: returnId, question, lang: 'en', asked_by: askedBy }, { signal: current.signal })) {
          if (current.signal.aborted) return;
          setState((prev) => ({ ...prev, events: [...prev.events, event] }));
        }
        if (!current.signal.aborted) setState((prev) => ({ ...prev, status: 'done' }));
      } catch (error) {
        if (!current.signal.aborted) setState((prev) => ({ ...prev, status: 'error', error }));
      }
    },
    [returnId, askedBy, cancel],
  );

  useEffect(() => cancel, [cancel]);
  return { ...state, ask, cancel };
}
