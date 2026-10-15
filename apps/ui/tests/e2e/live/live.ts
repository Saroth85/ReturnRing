// Shared helpers of the live specs: the API read directly (to learn the live ids and states the
// UI must show), the console watch, the page settle, the PROV-UI and wording scan of every page,
// and the acting persona.
import { expect, type Page } from '@playwright/test';
import type { CaseCard, DemoCase, DemoCaseKey, DemoCasesResponse, ReadyzResponse } from '../../../src/api/models';
import { scanPage } from '../scan';
import { API_URL } from './env';

export { API_URL };

/** GET a JSON body of the live API, as the given demo user (the actor header the UI sends too). */
export async function apiGet<T>(path: string, actor = 'sara'): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { headers: { accept: 'application/json', 'X-ReturnRing-Actor': actor } });
  if (!response.ok) throw new Error(`GET ${path}: ${response.status} ${await response.text()}`);
  return (await response.json()) as T;
}

export const readyz = () => apiGet<ReadyzResponse>('/readyz');
export const demoCases = () => apiGet<DemoCasesResponse>('/api/demo/cases');
export const caseCard = (returnId: string) => apiGet<CaseCard>(`/api/returns/${encodeURIComponent(returnId)}`);

export function caseOf(cases: DemoCasesResponse, key: DemoCaseKey): DemoCase {
  const found = cases.cases.find((c) => c.key === key);
  if (!found) throw new Error(`the reset stored no ${key} demo case`);
  return found;
}

/**
 * Console errors and uncaught page errors, except the HTTP statuses a step expects: the browser
 * logs an expected API answer (a 404 page, a 409 on a lapsed snapshot) as a failed load.
 */
export function watchConsole(page: Page, expectedStatuses: readonly number[] = []): string[] {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const status = /Failed to load resource: the server responded with a status of (\d{3})/.exec(message.text())?.[1];
    if (status && expectedStatuses.includes(Number(status))) return;
    errors.push(message.text());
  });
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  return errors;
}

/** The page has its heading, its data and the service mode (never "checking"). */
export async function settle(page: Page): Promise<void> {
  await expect(page.locator('h1').first()).toBeVisible({ timeout: 60_000 });
  await expect(page.locator('[data-data-check="pending"], [aria-busy="true"]')).toHaveCount(0, { timeout: 60_000 });
  await expect(page.locator('[data-mode-banner="checking"]')).toHaveCount(0);
}

/** No number without provenance, no banned wording, no console error (PROV-UI, spec 16). */
export async function expectClean(page: Page, errors: readonly string[]): Promise<void> {
  const scan = await scanPage(page);
  expect(scan.unprovenanced, 'numbers without provenance').toEqual([]);
  expect(scan.banned, 'banned wording in UI text').toEqual([]);
  expect(errors, 'console errors').toEqual([]);
}

/** The demo user the next page loads act as (the persona switcher's stored choice). */
export async function actAs(page: Page, persona: 'sara' | 'luca' | 'ahmed'): Promise<void> {
  await page.addInitScript((id) => window.localStorage.setItem('returnring.persona', id), persona);
}

/** Switch the persona on the open page, through the switcher in the top bar. */
export async function switchPersona(page: Page, persona: 'sara' | 'luca' | 'ahmed'): Promise<void> {
  await page.getByLabel('Acting as').selectOption(persona);
}
