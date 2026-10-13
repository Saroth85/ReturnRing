// Demo path on the contract examples (US-903; docs/ui-design.md 9.5 and 12).
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { personaStore, setPersona } from '@/app/persona';
import { renderApp } from '@/test/render';
import { findBannedWording, findUnprovenancedNumbers } from '@/test/scan';

afterEach(() => act(() => setPersona('sara')));

async function open(route = '/demo') {
  const view = renderApp(route);
  await waitFor(() => expect(['pass', 'fail']).toContain(document.querySelector('[data-preflight-state]')?.getAttribute('data-preflight-state')), {
    timeout: 8000,
  });
  return view;
}

describe('/demo (US-903)', () => {
  it('is green on the contract examples for a live run, with the load as a real figure', async () => {
    await open();
    expect(document.querySelector('[data-preflight-state]')?.getAttribute('data-preflight-state')).toBe('pass');
    const states = Array.from(document.querySelectorAll('[data-preflight]')).map((li) => li.getAttribute('data-state'));
    expect(states).toEqual(['pass', 'pass', 'pass', 'pass', 'pass', 'pass']);
    expect(document.querySelector('[data-preflight="load"] [data-figure]')?.getAttribute('data-nature')).toBe('real');
    expect(findUnprovenancedNumbers(document.body), JSON.stringify(findUnprovenancedNumbers(document.body))).toEqual([]);
    expect(findBannedWording(document.body).errors).toEqual([]);
  });

  it('turns red with its fix when the cached mode is expected on a live service', async () => {
    await open('/demo?expect=cached');
    expect(document.querySelector('[data-preflight-state]')?.getAttribute('data-preflight-state')).toBe('fail');
    expect(document.querySelector('[data-preflight="readiness"]')?.textContent).toContain('Open the cached URL');
  });

  it('lists the six beats in order and sets the persona of the beat', async () => {
    const { router } = await open();
    const beats = document.querySelectorAll('[data-beat]');
    expect(beats).toHaveLength(6);
    expect(beats[4]?.textContent).toContain('as Luca');
    fireEvent.click(screen.getAllByRole('link', { name: 'Open' })[4] as HTMLElement);
    await waitFor(() => expect(router.state.location.pathname).toBe('/simulator'));
    expect(personaStore.get().id).toBe('luca');
  });
});
