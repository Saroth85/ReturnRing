// Whether a restriction behind shown terms is still in force, from the API's fields only
// (docs/ui-design.md P9 and 11.4), through the one shared reading (src/domain/lapse.ts): once the
// pending fields land, regenerating the types is all it takes; until then the answer is "unknown"
// and the screens show the review date only. The client clock never decides a lapse.
import { readLapse } from '@/domain/lapse';

export type ServerLapse = { kind: 'in_force' } | { kind: 'lapsed' } | { kind: 'unknown' };

export function serverLapse(entity: object): ServerLapse {
  return { kind: readLapse(entity) };
}
