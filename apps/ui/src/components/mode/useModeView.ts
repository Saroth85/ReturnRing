// What the mode banner, the corner tag and the document title say (docs/ui-design.md 5.2, US-901).
// Sources: GET /readyz (mode, llm_mode, device, label, checks), the X-ReturnRing-Mode header of
// every response (polled through GET /healthz), and the adapter: the mock adapter means the data
// are contract examples.
import { API_MODE } from '@/api/adapters';
import { useApiMeta, type ApiMeta } from '@/api/meta';
import type { ComponentCheck, ReadyzResponse } from '@/api/models';
import { useHealthz, useReadyz } from '@/api/queries';
import { MODE } from '@/copy/common';
import { READ_ONLY } from '@/components/feedback/roles';

export type ModeKind = 'checking' | 'unknown' | 'live' | 'live-cpu' | 'cached' | 'contract' | 'mixed';

export interface ModeView {
  kind: ModeKind;
  title: string;
  detail: string | null;
  /** Extra segments after the detail ("contract examples", "read-only public demo"). */
  suffix: string[];
  /** The service is cached (readyz or the response header): `[Cached]` title, cached styles. */
  isCached: boolean;
  /** The data are contract examples (mock adapter): `[Mock]` title, corner tag. */
  isMock: boolean;
  /** Components that are not ready, listed on a second line. */
  degraded: ComponentCheck[];
}

export interface ModeInputs {
  readyz: ReadyzResponse | undefined;
  readyzFailed: boolean;
  meta: Pick<ApiMeta, 'lastMode' | 'modesSeen'>;
  isMock: boolean;
  readOnly: boolean;
}

/** The banner of docs/ui-design.md 5.2, row by row, from its inputs (pure, tested). */
export function describeMode({ readyz: r, readyzFailed, meta, isMock, readOnly }: ModeInputs): ModeView {
  const isCached = r?.mode === 'cached' || meta.lastMode === 'cached';
  const mixed =
    !isMock && r !== undefined && ((r.mode === 'live' && meta.modesSeen.cached > 0) || (r.mode === 'cached' && meta.modesSeen.live > 0));
  const suffix: string[] = [];
  if (readOnly) suffix.push(MODE.readOnly);
  const degraded = r && r.status !== 'ready' ? r.checks.filter((c) => c.status === 'unavailable') : [];
  const base = { suffix, isCached, isMock, degraded };

  if (mixed) return { ...base, kind: 'mixed', title: MODE.mixed, detail: null };
  if (isCached) {
    if (isMock) suffix.unshift(MODE.contractSuffix);
    const detail = [r?.label, MODE.cachedTail].filter(Boolean).join(' · ');
    return { ...base, kind: 'cached', title: MODE.cachedTitle, detail };
  }
  if (isMock) return { ...base, kind: 'contract', title: MODE.contractTitle, detail: MODE.contractLine };
  if (!r) {
    return readyzFailed
      ? { ...base, kind: 'unknown', title: MODE.unknownTitle, detail: MODE.unknown }
      : { ...base, kind: 'checking', title: MODE.checking, detail: null };
  }
  if (r.llm_mode === 'mock') return { ...base, kind: 'live-cpu', title: MODE.liveCpuTitle, detail: MODE.liveCpu };
  return { ...base, kind: 'live', title: MODE.liveTitle, detail: r.device === 'gpu' ? MODE.liveGpu : r.device };
}

export function useModeView(): ModeView {
  const readyz = useReadyz();
  useHealthz();
  const meta = useApiMeta();
  return describeMode({
    readyz: readyz.data,
    readyzFailed: readyz.isError,
    meta,
    isMock: API_MODE === 'mock',
    readOnly: READ_ONLY,
  });
}
