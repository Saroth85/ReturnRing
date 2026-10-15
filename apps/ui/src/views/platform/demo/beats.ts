// The beats of the pitch (spec 16, docs/ui-design.md 12) and which one is on screen. Every case id
// comes from GET /api/demo/cases (the golden cases the reset found in the live store, ADR-15
// section 15): a beat whose case the store does not hold is left out, and before a reset only the
// beats without a case remain. The six beats of the main path come first; the beats one key away
// for the Q&A follow, the ring checkout after Marco's beat (a proposal awaiting approval is not in
// force, rule 5: the shop shows the restriction only once Marco is approved, ADR-15 section 18).
import type { DemoCasesResponse, DemoDispute, DemoUserId } from '@/api/models';
import { caseOf, cartOf, disputeOf, disputePath } from '@/domain/demoCases';
import { PLATFORM } from '../copy';

export type BeatId =
  | 'same_indicator'
  | 'marco'
  | 'rossi'
  | 'elena'
  | 'net_benefit'
  | 'amd'
  | 'shop_ring'
  | 'shop_giulia'
  | 'dispute'
  | 'insufficient';

export interface Beat {
  id: BeatId;
  label: string;
  /** The route, built from the demo cases. */
  to: string;
  persona: DemoUserId;
  say: string;
  /** Off the main path: one key away for the Q&A (spec 16). */
  offPath: boolean;
  /** The beat that must be played first (Marco approved before the ring checkout). */
  after: BeatId | null;
  /** The dispute of the reset, on the dispute beat only. */
  dispute: DemoDispute | null;
}

type Cases = DemoCasesResponse | null | undefined;

const t = PLATFORM.demo.beat;
const returnPath = (returnId: string) => `/returns/${encodeURIComponent(returnId)}`;

function beat(id: BeatId, to: string, persona: DemoUserId, extra: Partial<Pick<Beat, 'offPath' | 'after' | 'dispute' | 'say'>> = {}): Beat {
  return {
    id,
    label: t[id].label,
    to,
    persona,
    say: extra.say ?? t[id].say,
    offPath: extra.offPath ?? false,
    after: extra.after ?? null,
    dispute: extra.dispute ?? null,
  };
}

/** The beats in pitch order, from the demo cases (none of them hard-codes an id). */
export function buildBeats(cases: Cases): Beat[] {
  const giulia = caseOf(cases, 'giulia');
  const marco = caseOf(cases, 'marco');
  // The Rossi beat ends on the variant with a shared card (the evidence beat), else the family.
  const rossi = caseOf(cases, 'rossi_card') ?? caseOf(cases, 'rossi');
  const elena = caseOf(cases, 'elena');
  const insufficient = caseOf(cases, 'insufficient');
  const dispute = disputeOf(cases);
  const disputeTo = disputePath(cases);
  const out: Beat[] = [];
  if (giulia && marco) {
    const query = new URLSearchParams({ a: giulia.return_id, b: marco.return_id });
    out.push(beat('same_indicator', `/compare?${query.toString()}`, 'sara'));
  }
  if (marco) out.push(beat('marco', returnPath(marco.return_id), 'sara'));
  if (rossi) out.push(beat('rossi', returnPath(rossi.return_id), 'sara'));
  if (elena) out.push(beat('elena', returnPath(elena.return_id), 'sara'));
  out.push(beat('net_benefit', '/simulator', 'luca'));
  out.push(beat('amd', '/system', 'luca'));
  if (cartOf(cases, 'ring')) out.push(beat('shop_ring', '/shop?preset=ring', 'sara', { offPath: true, after: marco ? 'marco' : null }));
  if (cartOf(cases, 'giulia')) out.push(beat('shop_giulia', '/shop?preset=giulia', 'sara', { offPath: true }));
  if (dispute && disputeTo) {
    const say = dispute.changes_decision ? t.dispute.sayChanges : t.dispute.sayUnchanged;
    out.push(beat('dispute', disputeTo, 'sara', { offPath: true, dispute, say }));
  }
  if (insufficient) out.push(beat('insufficient', returnPath(insufficient.return_id), 'sara', { offPath: true }));
  return out;
}

/** `?preset=marco` is the old name of the ring cart: the same beat. */
function normalise(pathname: string, params: URLSearchParams): URLSearchParams {
  if (pathname !== '/shop' || params.get('preset') !== 'marco') return params;
  const next = new URLSearchParams(params);
  next.set('preset', 'ring');
  return next;
}

/**
 * The beat whose route is on screen, or -1: the beat's path with at least its own query
 * parameters (extra ones such as `present=1` are fine); a case beat without a query also matches
 * its sub-pages. When two beats match (a dispute on a case of another beat), the one that names
 * more parameters wins.
 */
export function currentBeat(beats: readonly Beat[], pathname: string, search: string): number {
  const here = normalise(pathname, new URLSearchParams(search));
  let best = -1;
  let bestScore = -1;
  beats.forEach((b, index) => {
    const [path = '', query = ''] = b.to.split('?');
    const wanted = new URLSearchParams(query);
    const samePath = query ? pathname === path : pathname === path || pathname.startsWith(`${path}/`);
    if (!samePath || ![...wanted].every(([key, value]) => here.get(key) === value)) return;
    const score = [...wanted].length;
    if (score > bestScore) {
      best = index;
      bestScore = score;
    }
  });
  return best;
}
