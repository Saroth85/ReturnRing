// The questions and answers of one case assistant (docs/ui-design.md 7): one turn per question,
// earlier turns listed above the latest, kept while the case stays open (not persisted).
import type { AgentEvent } from '@/api/models';

export type TurnStatus = 'streaming' | 'done' | 'stopped' | 'failed';

export interface Turn {
  id: number;
  question: string;
  /** The acting user who asked (AgentAskRequest.asked_by). */
  askedBy: string;
  status: TurnStatus;
  events: AgentEvent[];
  /** The request error of a failed turn (ApiError for HTTP errors). */
  error: unknown;
}

export interface ConversationState {
  returnId: string;
  turns: Turn[];
}

export type ConversationAction =
  | { type: 'start'; id: number; returnId: string; question: string; askedBy: string }
  | { type: 'event'; id: number; event: AgentEvent }
  /** The response body closed. */
  | { type: 'end'; id: number }
  | { type: 'fail'; id: number; error: unknown }
  /** Aborted: the drawer closed, a new question was asked, or Stop. */
  | { type: 'stop'; id: number };

export const emptyConversation = (returnId: string): ConversationState => ({ returnId, turns: [] });

function updateStreaming(state: ConversationState, id: number, update: (turn: Turn) => Turn): ConversationState {
  let changed = false;
  const turns = state.turns.map((turn) => {
    if (turn.id !== id || turn.status !== 'streaming') return turn;
    changed = true;
    return update(turn);
  });
  return changed ? { ...state, turns } : state;
}

export function conversationReducer(state: ConversationState, action: ConversationAction): ConversationState {
  switch (action.type) {
    case 'start': {
      const turn: Turn = { id: action.id, question: action.question, askedBy: action.askedBy, status: 'streaming', events: [], error: null };
      const base = action.returnId === state.returnId ? state.turns : [];
      return { returnId: action.returnId, turns: [...base, turn] };
    }
    case 'event':
      return updateStreaming(state, action.id, (turn) => ({ ...turn, events: [...turn.events, action.event] }));
    case 'end':
      return updateStreaming(state, action.id, (turn) => ({ ...turn, status: 'done' }));
    case 'fail':
      return updateStreaming(state, action.id, (turn) => ({ ...turn, status: 'failed', error: action.error }));
    case 'stop':
      return updateStreaming(state, action.id, (turn) => ({ ...turn, status: 'stopped' }));
  }
}

/** The panel's `data-agent-stream`: the latest turn's state, or idle. */
export function conversationStatus(state: ConversationState): 'idle' | 'streaming' | 'done' | 'stopped' | 'error' {
  const latest = state.turns[state.turns.length - 1];
  if (!latest) return 'idle';
  return latest.status === 'failed' ? 'error' : latest.status;
}
