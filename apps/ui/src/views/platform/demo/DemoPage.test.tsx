// Demo path on the contract examples (US-903; docs/ui-design.md 9.5 and 12). The beats and the
// preflight read the golden cases from GET /api/demo/cases (demo_cases.json on the mock).
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import type { DemoCasesResponse, ReadyzResponse } from '@/api/models';
import { setScenario } from '@/api/scenario';
import { personaStore, setPersona } from '@/app/persona';
import { readExample } from '@/test/examples';
import { renderApp } from '@/test/render';
import { findBannedWording, findUnprovenancedNumbers } from '@/test/scan';

const cases = readExample<DemoCasesResponse>('demo_cases.json');

afterEach(() => {
  vi.restoreAllMocks();
  act(() => {
    setPersona('sara');
    setScenario('default');
  });
});

async function open(route = '/demo') {
  const view = renderApp(route);
  await waitFor(() => expect(['pass', 'fail']).toContain(document.querySelector('[data-preflight-state]')?.getAttribute('data-preflight-state')));
  return view;
}

const states = () =>
  Object.fromEntries(
    Array.from(document.querySelectorAll('[data-preflight]')).map((li) => [li.getAttribute('data-preflight'), li.getAttribute('data-state')]),
  );

describe('/demo (US-903)', () => {
  it('is red on the mock adapter only, every other check green, with the load as a real figure', async () => {
    await open();
    // Contract examples are never a ready pitch (UI12, UI29): the adapter line names the fix.
    expect(document.querySelector('[data-preflight-state]')?.getAttribute('data-preflight-state')).toBe('fail');
    await waitFor(() => expect(document.querySelector('[data-preflight="load"]')?.getAttribute('data-state')).toBe('pass'));
    expect(states()).toEqual({
      adapter: 'fail',
      readiness: 'pass',
      components: 'pass',
      contract: 'pass',
      cases: 'pass',
      marco: 'pass',
      rossi: 'pass',
      assumptions: 'pass',
      load: 'pass',
    });
    expect(document.querySelector('[data-preflight="adapter"]')?.textContent).toContain('npm run dev:live');
    expect(document.querySelector('[data-preflight="load"] [data-figure]')?.getAttribute('data-nature')).toBe('real');
    expect(findUnprovenancedNumbers(document.body), JSON.stringify(findUnprovenancedNumbers(document.body))).toEqual([]);
    expect(findBannedWording(document.body).errors).toEqual([]);
  });

  it('turns red with its fix when the cached mode is expected on a live service', async () => {
    await open('/demo?expect=cached');
    expect(document.querySelector('[data-preflight-state]')?.getAttribute('data-preflight-state')).toBe('fail');
    await waitFor(() => expect(document.querySelector('[data-preflight="readiness"]')?.textContent).toContain('Open the cached URL'));
  });

  it('before a reset: the API words verbatim, the reset as the fix, and only the beats without a case', async () => {
    act(() => setScenario('empty'));
    await open();
    await waitFor(() => expect(document.querySelector('[data-no-demo-reset]')).not.toBeNull());
    const notice = document.querySelector('[data-no-demo-reset]') as HTMLElement;
    expect(notice.textContent).toContain('no demo reset yet: run python -m apps.api.reset');
    expect(notice.textContent).toContain('Run python -m apps.api.reset');
    await waitFor(() => expect(states().cases).toBe('fail'));
    expect(states()).toMatchObject({ cases: 'fail', marco: 'fail', rossi: 'fail' });
    expect(document.querySelector('[data-preflight="cases"]')?.textContent).toContain('no demo reset yet');
    expect(Array.from(document.querySelectorAll('[data-beat-id]')).map((b) => b.getAttribute('data-beat-id'))).toEqual(['net_benefit', 'amd']);
  });

  it('lists the six beats, then the Q&A beats, on the ids of the demo cases, and sets the persona of the beat', async () => {
    const { router } = await open();
    await waitFor(() => expect(document.querySelectorAll('[data-beat]')).toHaveLength(10));
    const ids = Array.from(document.querySelectorAll('[data-beat-id]')).map((b) => b.getAttribute('data-beat-id'));
    expect(ids).toEqual(['same_indicator', 'marco', 'rossi', 'elena', 'net_benefit', 'amd', 'shop_ring', 'shop_giulia', 'dispute', 'insufficient']);
    const amd = document.querySelector('[data-beat-id="amd"]');
    expect(amd).toHaveTextContent('Gemma via AMD Token Factory and separate AMD GPU training; this demo uses synthetic data.');
    expect(amd).not.toHaveTextContent('benchmark');
    expect(document.querySelectorAll('[data-off-path] [data-beat]')).toHaveLength(4);
    const marco = cases.cases.find((c) => c.key === 'marco')!;
    expect(document.querySelector('[data-beat-id="marco"]')?.textContent).toContain(`/returns/${marco.return_id}`);
    const beats = document.querySelectorAll('[data-beat]');
    expect(beats[4]?.textContent).toContain('as Luca');
    fireEvent.click(screen.getAllByRole('link', { name: 'Open' })[4] as HTMLElement);
    await waitFor(() => expect(router.state.location.pathname).toBe('/simulator'));
    expect(personaStore.get().id).toBe('luca');
  });

  it('plays the ring checkout after Marco, and says while Marco is still proposed', async () => {
    await open();
    const ring = await waitFor(() => {
      const el = document.querySelector('[data-beat-id="shop_ring"]');
      expect(el).not.toBeNull();
      expect(el?.querySelector('[data-after-marco]')).not.toBeNull();
      return el as HTMLElement;
    });
    expect(ring.textContent).toContain('After the Marco beat');
    expect(ring.querySelector('[data-after-marco]')?.getAttribute('data-after-marco')).toBe('proposed');
    expect(ring.textContent).toContain('Approve Marco first');
    expect(within(ring).getByRole('link', { name: 'Open' })).toHaveAttribute('href', '/shop?preset=ring');
  });

  it('opens the dispute with its link selected, and says "decision unchanged" when the dispute changes nothing', async () => {
    const unchanged: DemoCasesResponse = { ...cases, dispute: { ...cases.dispute!, changes_decision: false } };
    vi.spyOn(api, 'demoCases').mockResolvedValue(unchanged);
    await open();
    const dispute = await waitFor(() => {
      const el = document.querySelector('[data-beat-id="dispute"]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    const link = within(dispute).getByRole('link', { name: 'Open' });
    expect(link.getAttribute('href')).toBe(`/returns/${unchanged.dispute!.return_id}?link=${encodeURIComponent(unchanged.dispute!.link_id)}`);
    expect(dispute.querySelector('[data-dispute-changes]')?.getAttribute('data-dispute-changes')).toBe('no');
    expect(dispute.textContent).toContain('Decision unchanged');
    expect(dispute.textContent).toContain(unchanged.dispute!.cluster_id);
    expect(findUnprovenancedNumbers(dispute)).toEqual([]);
  });

  it('says the case is decided again when the dispute changes the decision (the example)', async () => {
    await open();
    const dispute = await waitFor(() => {
      const el = document.querySelector('[data-beat-id="dispute"] [data-dispute-changes]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(dispute.getAttribute('data-dispute-changes')).toBe('yes');
    expect(dispute.textContent).toContain('decided again');
  });

  it('says "no cluster" when one account is left, never "1 linked accounts", and pluralises a cluster that stays', async () => {
    // demo_cases.json: 3 linked accounts, 1 left after the dispute.
    await open();
    const cluster = await waitFor(() => {
      const el = document.querySelector('[data-beat-id="dispute"] [data-dispute-cluster]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(cluster.textContent).toContain('3 linked accounts → no cluster');
    expect(cluster.textContent).not.toMatch(/→ 1 linked/);
  });

  it('a cluster that stays a cluster after the dispute: before → after linked accounts', async () => {
    vi.spyOn(api, 'demoCases').mockResolvedValue({ ...cases, dispute: { ...cases.dispute!, cluster_size_before: 4, cluster_size_after: 3 } });
    await open();
    const cluster = await waitFor(() => {
      const el = document.querySelector('[data-beat-id="dispute"] [data-dispute-cluster]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(cluster.textContent).toContain('4 → 3 linked accounts');
  });

  it('on the read-only public demo, never asks for an approval the service refuses: ring checkout and dispute are not playable', async () => {
    const readyz = readExample<ReadyzResponse>('readyz_response.live.json');
    vi.spyOn(api, 'readyz').mockResolvedValue({ ...readyz, public_read_only: true, clock: { ...readyz.clock, frozen: true } });
    await open();
    const beat = (id: string) =>
      waitFor(() => {
        const el = document.querySelector(`[data-beat-id="${id}"]`);
        expect(el).not.toBeNull();
        return el as HTMLElement;
      });
    const ring = await beat('shop_ring');
    await waitFor(() => expect(ring.getAttribute('data-playable')).toBe('no'));
    expect(ring.querySelector('[data-read-only-beat="not-playable"]')).toHaveTextContent('Marco cannot be approved here');
    await waitFor(() => expect(ring.querySelector('[data-after-marco]')?.getAttribute('data-after-marco')).toBe('proposed'));
    expect(ring.textContent).not.toContain('Approve Marco first');
    expect(ring.textContent).toContain('the read-only demo cannot approve him');
    const dispute = await beat('dispute');
    expect(dispute.getAttribute('data-playable')).toBe('no');
    expect(dispute.querySelector('[data-read-only-beat="not-playable"]')).toHaveTextContent('Disputing a link');
    expect((await beat('marco')).querySelector('[data-read-only-beat="limited"]')).toHaveTextContent('The approval is refused here');
    expect((await beat('shop_giulia')).getAttribute('data-playable')).toBe('yes');
    expect(findBannedWording(document.body).errors).toEqual([]);
  });
});
