// The one polite announcement per question (docs/ui-design.md 7 and 10.3): empty while the answer
// streams (the answer region is aria-busy), then the answer, the refusal or the failure, once.
import type { Turn } from './conversation';
import { AGENT } from './copy';
import { foldAgentEvents } from './stream';

export function turnAnnouncement(turn: Turn | undefined): string {
  if (!turn || turn.status === 'streaming') return '';
  if (turn.status === 'stopped') return AGENT.announce.stopped;
  if (turn.status === 'failed') return AGENT.announce.failed;
  const { final } = foldAgentEvents(turn.events, { ended: true });
  if (final?.kind === 'answer') return `${AGENT.announce.answer} ${final.text}`;
  if (final?.kind === 'refusal') return `${AGENT.refusal[final.reason].title} ${AGENT.refusal[final.reason].body}`;
  return AGENT.announce.failed;
}
