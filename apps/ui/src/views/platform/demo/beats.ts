// The six beats of the pitch (docs/ui-design.md 12) and which one is on screen.
import { PLATFORM } from '../copy';

export const BEATS = PLATFORM.demo.beatList;

/**
 * The beat whose route is on screen, or -1: the beat's path with at least its own query
 * parameters (extra ones such as `present=1` are fine); a case beat also matches its sub-pages.
 */
export function currentBeat(pathname: string, search: string): number {
  const here = new URLSearchParams(search);
  return BEATS.findIndex((beat) => {
    const [path = '', query = ''] = beat.to.split('?');
    const wanted = new URLSearchParams(query);
    const samePath = query ? pathname === path : pathname === path || pathname.startsWith(`${path}/`);
    return samePath && [...wanted].every(([key, value]) => here.get(key) === value);
  });
}
