// useAgentStream over the mock adapter: the events of a question land in its turn, a new question
// aborts the previous stream, another return starts an empty history, and a 429 disables new
// questions for RATE_LIMIT_COOLDOWN_SECONDS (docs/ui-design.md 7, 10.1).
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import type { AgentEvent } from '@/api/models';
import { setScenario } from '@/api/scenario';
import { readExample } from '@/test/examples';
import { AGENT } from './copy';
import { RATE_LIMIT_COOLDOWN_SECONDS } from './limits';
import { useAgentStream } from './useAgentStream';

afterEach(() => {
  setScenario('default');
  vi.useRealTimers();
});

describe('useAgentStream', () => {
  it('collects the contract stream of a question, in order', async () => {
    const { result } = renderHook(() => useAgentStream('RET-000102', 'sara'));
    await act(() => result.current.ask(AGENT.suggested[0] as string));
    expect(result.current.status).toBe('done');
    expect(result.current.turns[0]?.events).toEqual(readExample<AgentEvent[]>('agent_stream.marco_link.json'));
    expect(result.current.turns[0]).toMatchObject({ askedBy: 'sara', question: AGENT.suggested[0] });
  });

  it('sends the acting user, the return and English', async () => {
    const spy = vi.spyOn(api, 'askAgent');
    const { result } = renderHook(() => useAgentStream('RET-000102', 'luca'));
    await act(() => result.current.ask('  Which terms applied when the order was placed?  '));
    expect(spy).toHaveBeenCalledWith(
      { return_id: 'RET-000102', question: 'Which terms applied when the order was placed?', lang: 'en', asked_by: 'luca' },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it('ignores an empty question', async () => {
    const { result } = renderHook(() => useAgentStream('RET-000102', 'sara'));
    await act(() => result.current.ask('   '));
    expect(result.current.turns).toEqual([]);
  });

  it('a new question aborts the stream of the previous one', async () => {
    const signals: AbortSignal[] = [];
    vi.spyOn(api, 'askAgent').mockImplementation(async function* (_body, options) {
      signals.push(options?.signal as AbortSignal);
      yield readExample<AgentEvent[]>('agent_stream.marco_link.json')[0] as AgentEvent;
      await new Promise<never>((_, reject) =>
        options?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }),
      );
    });
    const { result } = renderHook(() => useAgentStream('RET-000102', 'sara'));
    act(() => void result.current.ask('First?'));
    await waitFor(() => expect(result.current.turns[0]?.events).toHaveLength(1));
    act(() => void result.current.ask('Second?'));
    await waitFor(() => expect(result.current.turns[0]?.status).toBe('stopped'));
    expect(signals[0]?.aborted).toBe(true);
    expect(signals[1]?.aborted).toBe(false);
    expect(result.current.status).toBe('streaming');
    act(() => result.current.stop());
    await waitFor(() => expect(result.current.status).toBe('stopped'));
  });

  it('another return shows an empty history', async () => {
    const { result, rerender } = renderHook(({ id }) => useAgentStream(id, 'sara'), { initialProps: { id: 'RET-000102' } });
    await act(() => result.current.ask(AGENT.suggested[1] as string));
    expect(result.current.turns).toHaveLength(1);
    rerender({ id: 'RET-000103' });
    expect(result.current.turns).toEqual([]);
    expect(result.current.status).toBe('idle');
  });

  it('a 429 disables new questions for the cooldown, then lets them through', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    setScenario('rate_limited');
    const { result } = renderHook(() => useAgentStream('RET-000102', 'sara'));
    await act(() => result.current.ask('Anything?'));
    expect(result.current.status).toBe('error');
    expect(result.current.coolingDown).toBe(true);
    act(() => vi.advanceTimersByTime(RATE_LIMIT_COOLDOWN_SECONDS * 1000 - 1));
    expect(result.current.coolingDown).toBe(true);
    act(() => vi.advanceTimersByTime(1));
    expect(result.current.coolingDown).toBe(false);
  });
});
