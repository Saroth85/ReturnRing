// Readiness helpers (US-901, docs/ui-design.md 5.2 and 9.4): how a component check reads, and
// whether the X-ReturnRing-Mode header of the latest response agrees with /readyz.
import type { ComponentCheck, Mode } from '@/api/models';

/** ok: working; off: disabled on purpose (cached mode, no demo cache when live); fail: not ready. */
export type ReadinessTone = 'ok' | 'off' | 'fail';

export function readinessTone(status: ComponentCheck['status']): ReadinessTone {
  if (status === 'ok') return 'ok';
  if (status === 'disabled') return 'off';
  return 'fail';
}

export type HeaderAgreement = 'agrees' | 'differs' | 'unknown';

export function headerAgreement(readyzMode: Mode, headerMode: Mode | null): HeaderAgreement {
  if (headerMode === null) return 'unknown';
  return headerMode === readyzMode ? 'agrees' : 'differs';
}
