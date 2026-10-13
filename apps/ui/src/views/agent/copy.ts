// Agent area strings (docs/ui-design.md 7). English; no number literals.
import { AGENT_MAX_STEPS } from '@/api/contract.gen';

export const AGENT = {
  title: (returnId: string) => `Ask about ${returnId} · read-only assistant`,
  readOnly: 'It can read the case; it cannot change it.',
  footer: `Read-only: at most ${AGENT_MAX_STEPS} steps. Answers cannot change the decision.`,
  placeholder: 'The case assistant is built here: suggested questions, streamed steps, the answer with the evidence consulted and missing (US-701, US-702).',
  close: 'Close',
  harnessTitle: 'Agent harness',
  harnessIntro: 'Dev only: the case assistant without the case card, over the streamed answer of POST /api/agent/ask.',
  openDrawer: 'Open the drawer',
  ask: 'Ask',
  events: 'Stream events',
  streaming: 'Streaming',
  done: 'Done',
  /** The three questions of US-702, as the contract examples ask them (agent_ask_request.*.json). */
  suggested: [
    'Which evidence supports the link between C-MARCO-1 and C-MARCO-3?',
    'What makes this case different from the Rossi family?',
    'Which terms applied when the order was placed?',
  ],
};
