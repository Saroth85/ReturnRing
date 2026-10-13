// One polite announcement per question: nothing while streaming, then the outcome once.
import { describe, expect, it } from 'vitest';
import type { AgentEvent } from '@/api/models';
import { readExample } from '@/test/examples';
import { turnAnnouncement } from './announce';
import type { Turn } from './conversation';

const turn = (status: Turn['status'], file = 'agent_stream.marco_link.json'): Turn => ({
  id: 1,
  question: 'Q?',
  askedBy: 'sara',
  status,
  events: readExample<AgentEvent[]>(file),
  error: null,
});

describe('turnAnnouncement', () => {
  it('is silent with no question and while streaming', () => {
    expect(turnAnnouncement(undefined)).toBe('');
    expect(turnAnnouncement(turn('streaming'))).toBe('');
  });

  it('announces the answer with its text', () => {
    expect(turnAnnouncement(turn('done'))).toMatch(/^Answer ready\. C-MARCO-1 and C-MARCO-3 are linked/);
  });

  it('announces a refusal, a stop and a failure', () => {
    expect(turnAnnouncement(turn('done', 'agent_stream.write_refused.json'))).toBe(
      'The assistant only reads the case. Approvals and changes are made on the case card.',
    );
    expect(turnAnnouncement(turn('stopped'))).toBe('Stopped.');
    expect(turnAnnouncement({ ...turn('failed'), events: [] })).toBe('The assistant could not answer. The decision is unaffected.');
  });
});
