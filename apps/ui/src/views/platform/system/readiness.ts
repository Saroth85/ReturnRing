// Readiness helpers (US-901, docs/ui-design.md 5.2 and 9.4): how a component check reads, and
// whether the X-ReturnRing-Mode header of the latest response agrees with /readyz.
import type { ComponentCheck, Mode, ReadyzResponse } from '@/api/models';
import { SYSTEM } from './copy';

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

/**
 * What this service runs on, from /readyz (ADR-15 sections 16, 17): the hero and the architecture
 * strip describe the target (every model on one MI300X); this line says what answers now, so a CPU
 * run or an unlabelled cached demo never reads as the MI300X. Only readyz.label names where cached
 * outputs were made.
 */
export function serviceLine(
  readyz: Pick<ReadyzResponse, 'mode' | 'device' | 'llm_mode' | 'label'> | undefined,
  apiMode: 'mock' | 'live',
): string | null {
  const t = SYSTEM.thisService;
  if (apiMode === 'mock') return t.contract;
  if (!readyz) return null;
  if (readyz.mode === 'cached') return t.cached(readyz.label ?? t.noLabel);
  const device = SYSTEM.devices[readyz.device] ?? readyz.device;
  return t.live(device, SYSTEM.llmModes[readyz.llm_mode] ?? readyz.llm_mode);
}
