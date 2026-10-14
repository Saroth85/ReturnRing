// Every route renders on the contract examples with its heading, no number without provenance
// (PROV-UI seed) and no banned wording; roles read on screen (ROLE-UI seed).
import { act, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { setPersona } from '@/app/persona';
import { findBannedWording, findUnprovenancedNumbers } from '@/test/scan';
import { renderApp } from '@/test/render';

const ROUTES = [
  '/queue',
  '/returns/RET-000102',
  '/returns/RET-000106/versions/DEC-000106-1',
  '/compare?a=RET-000101&b=RET-000102',
  '/outbox',
  '/portfolio',
  '/simulator',
  '/kpi?week=2026-09-21',
  '/fairness',
  '/products',
  '/products/tasks',
  '/reviews',
  '/audit',
  '/shop?preset=giulia',
  '/inspection',
  '/inspection/RET-000110',
  '/coupons',
  '/system',
  '/demo',
  '/about',
  '/dev/agent?return=RET-000102',
];

async function settle() {
  await waitFor(() => expect(document.querySelector('[data-data-check="pending"], [role="status"][aria-busy="true"]')).toBeNull());
}

afterEach(() => act(() => setPersona('sara')));

describe('routes', () => {
  it.each(ROUTES)('%s renders with provenance and clean wording', async (route) => {
    renderApp(route);
    expect(await screen.findByRole('heading', { level: 1 })).toBeInTheDocument();
    await settle();
    expect(document.querySelector('[data-error-state]')).toBeNull();
    expect(findUnprovenancedNumbers(document.body), JSON.stringify(findUnprovenancedNumbers(document.body))).toEqual([]);
    expect(findBannedWording(document.body).errors, JSON.stringify(findBannedWording(document.body).errors)).toEqual([]);
    expect(screen.getByRole('region', { name: 'Service mode' })).toBeInTheDocument();
  });

  it('redirects / to the persona landing page', async () => {
    act(() => setPersona('luca'));
    const { router } = renderApp('/');
    await waitFor(() => expect(router.state.location.pathname).toBe('/portfolio'));
  });

  it('shows not found for an unknown page and for an unknown return', async () => {
    renderApp('/nowhere');
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
  });

  it('reports a missing return with its request id', async () => {
    renderApp('/returns/RET-999999');
    const alert = await screen.findByRole('alert', {});
    expect(alert).toHaveAttribute('data-error-state', 'not_found');
    expect(alert.textContent).toContain('Return RET-999999 not found');
    expect(alert.querySelector('[data-id]')).not.toBeNull();
  });

  it('disables a write for a role that may not call it, with the reason', async () => {
    act(() => setPersona('ahmed'));
    renderApp('/returns/RET-000102');
    const button = await screen.findByRole('button', { name: /Ask about this case/ });
    expect(button).toBeDisabled();
    expect(screen.getByText('This action is for operators and managers (warehouse can view).')).toBeInTheDocument();
  });
});
