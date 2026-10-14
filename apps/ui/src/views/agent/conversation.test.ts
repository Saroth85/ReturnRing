// The questions and answers of one drawer (docs/ui-design.md 7, session history): each question
// is a turn; its events land only in that turn; a stopped turn ignores late events; another
// return starts an empty history.
import { describe, expect, it } from 'vitest';
import type { AgentEvent } from '@/api/models';
import { ApiError } from '@/api/errors';
import { readExample } from '@/test/examples';
import { conversationReducer, conversationStatus, emptyConversation, type ConversationState } from './conversation';

const events = () => readExample<AgentEvent[]>('agent_stream.marco_link.json');
const start = (state: ConversationState, id: number, question = 'Q?', returnId = 'RET-000102') =>
  conversationReducer(state, { type: 'start', id, returnId, question, askedBy: 'sara' });

describe('conversationReducer', () => {
  it('starts a turn per question, latest last', () => {
    let state = emptyConversation('RET-000102');
    state = start(state, 1, 'First?');
    state = conversationReducer(state, { type: 'end', id: 1 });
    state = start(state, 2, 'Second?');
    expect(state.turns.map((t) => [t.id, t.question, t.status])).toEqual([
      [1, 'First?', 'done'],
      [2, 'Second?', 'streaming'],
    ]);
    expect(conversationStatus(state)).toBe('streaming');
  });

  it('appends events to their own turn only', () => {
    let state = start(emptyConversation('RET-000102'), 1);
    state = start(state, 2);
    const [first, second] = events();
    state = conversationReducer(state, { type: 'event', id: 2, event: first as AgentEvent });
    state = conversationReducer(state, { type: 'event', id: 1, event: second as AgentEvent });
    expect(state.turns[0]?.events).toEqual([second]);
    expect(state.turns[1]?.events).toEqual([first]);
  });

  it('a stopped turn ignores late events and a late end', () => {
    let state = start(emptyConversation('RET-000102'), 1);
    state = conversationReducer(state, { type: 'stop', id: 1 });
    state = conversationReducer(state, { type: 'event', id: 1, event: events()[0] as AgentEvent });
    state = conversationReducer(state, { type: 'end', id: 1 });
    expect(state.turns[0]).toMatchObject({ status: 'stopped', events: [] });
    expect(conversationStatus(state)).toBe('stopped');
  });

  it('keeps the error of a failed turn', () => {
    const error = new ApiError({ status: 503, code: 'llm_unavailable', message: 'down', requestId: 'req-1' });
    let state = start(emptyConversation('RET-000102'), 1);
    state = conversationReducer(state, { type: 'fail', id: 1, error });
    expect(state.turns[0]).toMatchObject({ status: 'failed', error });
    expect(conversationStatus(state)).toBe('error');
  });

  it('another return starts a new history', () => {
    let state = start(emptyConversation('RET-000102'), 1);
    state = start(state, 2, 'Other?', 'RET-000103');
    expect(state.returnId).toBe('RET-000103');
    expect(state.turns.map((t) => t.id)).toEqual([2]);
  });

  it('is idle with no turn', () => {
    expect(conversationStatus(emptyConversation('RET-000102'))).toBe('idle');
  });
});
