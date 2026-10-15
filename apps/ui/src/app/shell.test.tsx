// The shell on the contract examples (docs/ui-design.md 3.4, 5.2): the service's clock in the mode
// banner and the top bar (ADR-15 sections 15, 19, 20), the cached label exactly as /readyz gives
// it, the demo cases loaded for every role and the global "run the reset" line on their 404.
import { act, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { ApiError } from '@/api/errors';
import { metaStore, recordReadyz, resetModesSeen } from '@/api/meta';
import type { GenerationInfo, ReadyzResponse } from '@/api/models';
import { setScenario } from '@/api/scenario';
import { setPersona } from '@/app/persona';
import { GenerationBadge } from '@/components/message';
import { SHELL } from '@/copy/common';
import { readExample } from '@/test/examples';
import { renderApp, renderWithProviders } from '@/test/render';

const live = readExample<ReadyzResponse>('readyz_response.live.json');
const cached = readExample<ReadyzResponse>('readyz_response.cached.json');

afterEach(() => {
  act(() => {
    setScenario('default');
    setPersona('sara');
  });
  resetModesSeen();
  metaStore.set((prev) => ({ ...prev, cachedLabel: null }));
});

async function banner() {
  const region = await screen.findByRole('region', { name: 'Service mode' });
  await waitFor(() => expect(region.querySelector('[data-banner-clock]')).not.toBeNull());
  return region;
}

describe('the service clock in the shell', () => {
  it('a running demo clock: the banner names it with the snapshot, the top bar shows its now', async () => {
    renderApp('/about');
    const region = await banner();
    const clock = region.querySelector('[data-banner-clock]') as HTMLElement;
    expect(clock.getAttribute('data-banner-clock')).toBe('demo');
    expect(clock.getAttribute('data-clock-frozen')).toBe('no');
    expect(clock.textContent).toContain('demo clock running since the reset');
    expect(clock.textContent).toContain('snapshot 30 Sep 2026, 18:00 UTC');
    expect(clock.getAttribute('title')).toBe(live.clock.note);
    const top = document.querySelector('[data-top-clock]') as HTMLElement;
    // The service's 'now' (19:00 on the example), never the browser's date.
    expect(top.textContent).toContain(`${SHELL.serviceNow} 30 Sep 2026, 19:0`);
    expect(top.querySelector('time')?.getAttribute('datetime')?.startsWith('2026-09-30T19:0')).toBe(true);
  });

  it('a frozen clock (the read-only public demo) says so and stands at the snapshot', async () => {
    act(() => setScenario('cached'));
    renderApp('/about');
    const region = await banner();
    const clock = region.querySelector('[data-banner-clock]') as HTMLElement;
    expect(clock.getAttribute('data-clock-frozen')).toBe('yes');
    expect(clock.textContent).toContain('demo clock frozen at the snapshot');
    expect(clock.getAttribute('title')).toBe(cached.clock.note);
    const top = document.querySelector('[data-top-clock]') as HTMLElement;
    expect(top.getAttribute('data-clock-frozen')).toBe('yes');
    expect(top.querySelector('time')?.getAttribute('datetime')).toBe('2026-09-30T18:00:00.000Z');
  });

  it('a frozen clock past its snapshot (a store acted on, then read-only) never claims the snapshot', async () => {
    const note = 'Demo clock, frozen at the latest act recorded after the serving snapshot';
    vi.spyOn(api, 'readyz').mockResolvedValue({ ...cached, clock: { ...cached.clock, now: '2026-09-30T19:12:00Z', note } });
    act(() => setScenario('cached'));
    renderApp('/about');
    const region = await banner();
    const clock = region.querySelector('[data-banner-clock]') as HTMLElement;
    expect(clock.getAttribute('data-clock-frozen')).toBe('yes');
    expect(clock.textContent).toContain('demo clock frozen at the latest recorded act');
    expect(clock.textContent).not.toContain('frozen at the snapshot');
    expect(clock.textContent).toContain('snapshot 30 Sep 2026, 18:00 UTC');
    expect(clock.getAttribute('title')).toBe(note);
    const top = document.querySelector('[data-top-clock]') as HTMLElement;
    expect(top.querySelector('time')?.getAttribute('datetime')).toBe('2026-09-30T19:12:00.000Z');
  });
});

describe('the cached label is /readyz label, verbatim', () => {
  it('in the banner and on a cached text, MI300X only when /readyz says so', async () => {
    act(() => setScenario('cached'));
    renderApp('/about');
    const region = await banner();
    await waitFor(() => expect(region.textContent).toContain('Precomputed on AMD MI300X'));
    expect(metaStore.get().cachedLabel).toBe('Precomputed on AMD MI300X');
  });

  it('a rehearsal package is labelled as one, in the banner and on a cached draft', async () => {
    const rehearsal = 'Precomputed in a rehearsal, not verified on AMD MI300X' as const;
    // A /readyz of a rehearsal package: the cached example with the other label of the enum.
    const readyz = api.readyz;
    vi.spyOn(api, 'readyz').mockImplementation(async (options) => {
      const body = { ...(await readyz(options)), label: rehearsal };
      recordReadyz(body);
      return body;
    });
    act(() => setScenario('cached'));
    renderApp('/about');
    const region = await screen.findByRole('region', { name: 'Service mode' });
    await waitFor(() => expect(region.textContent).toContain(rehearsal));
    expect(region.textContent).not.toContain('Precomputed on AMD MI300X');
    const generation = readExample<{ draft: { generation: GenerationInfo } }>('case_card.marco.json').draft.generation;
    renderWithProviders(<GenerationBadge generation={{ ...generation, served_from: 'cached' }} />);
    const badge = document.querySelector('[data-served-from="cached"]') as HTMLElement;
    expect(badge.textContent).toContain(rehearsal);
    expect(badge.textContent).not.toContain('Precomputed on AMD MI300X');
  });
});

describe('the demo cases in the shell', () => {
  it('are loaded once for every role, whatever page opens first', async () => {
    const spy = vi.spyOn(api, 'demoCases');
    act(() => setPersona('ahmed'));
    renderApp('/inspection');
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(document.querySelector('[data-demo-reset-missing]')).toBeNull();
  });

  it('before a reset (404), every page names the reset with the API message verbatim', async () => {
    act(() => setScenario('empty'));
    renderApp('/queue');
    const line = await waitFor(() => {
      const found = document.querySelector('[data-demo-reset-missing]');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(line.textContent).toContain(SHELL.noDemoReset.title);
    expect(within(line).getByText('no demo reset yet: run python -m apps.api.reset')).toHaveAttribute('data-verbatim');
    expect(line.textContent).toContain('python -m apps.api.reset');
  });

  it('another failure of the demo cases is left to the pages', async () => {
    vi.spyOn(api, 'demoCases').mockRejectedValue(new ApiError({ status: 503, code: 'internal', message: 'down' }));
    renderApp('/about');
    await screen.findByRole('heading', { level: 1 });
    await waitFor(() => expect(document.querySelector('[data-top-clock]')).not.toBeNull());
    expect(document.querySelector('[data-demo-reset-missing]')).toBeNull();
  });
});
