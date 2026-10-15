// The case assistant's questions and answers (US-701, docs/ui-design.md 7): POST /api/agent/ask
// read with fetch and the SSE parser (EventSource cannot POST). An AbortController cancels the
// stream when the drawer closes, Stop is pressed or a new question is asked. A 429 disables new
// questions for the whole seconds of its Retry-After header (ADR-15 section 16), or
// RATE_LIMIT_COOLDOWN_SECONDS when it has none; the seconds left count down on screen.
import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { api } from '@/api/client';
import { isApiError } from '@/api/errors';
import { conversationReducer, conversationStatus, emptyConversation, type Turn } from './conversation';
import { RATE_LIMIT_COOLDOWN_SECONDS } from './limits';

export interface AgentConversation {
  returnId: string;
  /** Earlier questions first, the latest last (session history, not persisted). */
  turns: Turn[];
  status: ReturnType<typeof conversationStatus>;
  streaming: boolean;
  /** After a 429: new questions wait for the Retry-After seconds. */
  coolingDown: boolean;
  /** The seconds left of that wait, while it lasts. */
  cooldownSeconds: number | null;
  ask(question: string): Promise<void>;
  /** Aborts the answer being streamed, if any. */
  stop(): void;
}

export function useAgentStream(returnId: string, askedBy: string): AgentConversation {
  const [state, dispatch] = useReducer(conversationReducer, returnId, emptyConversation);
  const [cooldownSeconds, setCooldownSeconds] = useState<number | null>(null);
  const controller = useRef<AbortController | null>(null);
  const lastId = useRef(0);
  const cooldown = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** A one-second tick from `seconds` down to zero, then new questions are let through. */
  const startCooldown = useCallback((seconds: number) => {
    if (cooldown.current) clearTimeout(cooldown.current);
    const tick = (left: number) => {
      setCooldownSeconds(left > 0 ? left : null);
      cooldown.current = left > 0 ? setTimeout(() => tick(left - 1), 1000) : null;
    };
    tick(seconds);
  }, []);

  const stop = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
  }, []);

  const ask = useCallback(
    async (raw: string) => {
      const question = raw.trim();
      if (!question) return;
      controller.current?.abort();
      const current = new AbortController();
      controller.current = current;
      lastId.current += 1;
      const id = lastId.current;
      dispatch({ type: 'start', id, returnId, question, askedBy });
      try {
        const events = api.askAgent({ return_id: returnId, question, lang: 'en', asked_by: askedBy }, { signal: current.signal });
        for await (const event of events) {
          if (current.signal.aborted) break;
          dispatch({ type: 'event', id, event });
        }
        dispatch(current.signal.aborted ? { type: 'stop', id } : { type: 'end', id });
      } catch (error) {
        if (current.signal.aborted) {
          dispatch({ type: 'stop', id });
          return;
        }
        dispatch({ type: 'fail', id, error });
        if (isApiError(error) && error.code === 'rate_limited') startCooldown(error.retryAfterS ?? RATE_LIMIT_COOLDOWN_SECONDS);
      } finally {
        if (controller.current === current) controller.current = null;
      }
    },
    [returnId, askedBy, startCooldown],
  );

  // Another case, or the component goes away: the stream stops.
  useEffect(() => stop, [returnId, stop]);
  useEffect(
    () => () => {
      if (cooldown.current) clearTimeout(cooldown.current);
    },
    [],
  );

  const turns = state.returnId === returnId ? state.turns : [];
  const status = conversationStatus({ returnId, turns });
  return {
    returnId,
    turns,
    status,
    streaming: status === 'streaming',
    coolingDown: cooldownSeconds !== null,
    cooldownSeconds,
    ask,
    stop,
  };
}
