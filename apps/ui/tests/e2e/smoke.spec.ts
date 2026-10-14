// Smoke test in mock mode (contract examples): every route renders without console errors, with
// no number lacking provenance (PROV-UI baseline) and no banned wording (spec 16). In the cached
// scenario the banner, the corner tag and the title say so (MODE-UI baseline).
// SCREENSHOT_DIR=<dir> saves one full-page screenshot per route.
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { SMOKE_ROUTES } from './routes';
import { scanPage } from './scan';

const SCREENSHOT_DIR = process.env.SCREENSHOT_DIR;
if (SCREENSHOT_DIR) mkdirSync(SCREENSHOT_DIR, { recursive: true });

function watchConsole(page: Page, expectedStatuses: number[] = []): string[] {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const text = message.text();
    // On the live adapter the browser logs an expected API error (a 404 page) as a failed load.
    const status = /Failed to load resource: the server responded with a status of (\d{3})/.exec(text)?.[1];
    if (status && expectedStatuses.includes(Number(status))) return;
    errors.push(text);
  });
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  return errors;
}

async function settle(page: Page): Promise<void> {
  await expect(page.locator('h1').first()).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('[data-data-check="pending"], [aria-busy="true"]')).toHaveCount(0);
  await expect(page.locator('[data-mode-banner="checking"]')).toHaveCount(0);
}

for (const route of SMOKE_ROUTES) {
  test(`${route.area} ${route.path} renders without console errors`, async ({ page }) => {
    const errors = watchConsole(page, route.expectedStatuses);
    await page.goto(route.path);
    await settle(page);
    await expect(page.getByRole('region', { name: 'Service mode' })).toBeVisible();
    const scan = await scanPage(page);
    expect(scan.unprovenanced, 'numbers without provenance').toEqual([]);
    expect(scan.banned, 'banned wording in UI text').toEqual([]);
    if (scan.bannedVerbatim.length > 0) {
      test.info().annotations.push({ type: 'warning', description: `banned words in contract text: ${scan.bannedVerbatim.join(' | ')}` });
    }
    expect(errors, 'console errors').toEqual([]);
    if (SCREENSHOT_DIR) {
      await page.screenshot({ path: path.join(SCREENSHOT_DIR, `${route.name}.png`), fullPage: true });
    }
  });
}

test('/ leads Sara to the queue', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveURL(/\/queue$/);
  // The title follows the route once the page is on screen: wait for its heading first (UI39).
  await expect(page.getByRole('heading', { level: 1, name: 'Returns queue' })).toBeVisible();
  await expect(page).toHaveTitle('[Mock] ReturnRing · Returns queue');
});

test('the cached scenario shows the banner, the corner tag and the [Cached] title on every page', async ({ page }) => {
  const errors = watchConsole(page);
  await page.goto('/queue?scenario=cached');
  const banner = page.getByRole('region', { name: 'Service mode' });
  await expect(banner).toContainText('CACHED DEMO');
  await expect(banner).toContainText('Precomputed on AMD MI300X');
  await expect(page.locator('[data-mode-corner]')).toContainText('Cached demo');
  await expect(page).toHaveTitle(/^\[Cached\] \[Mock\] ReturnRing · Returns queue$/);
  await page.getByRole('link', { name: 'System and benchmarks' }).first().click();
  await expect(banner).toContainText('Precomputed on AMD MI300X');
  await expect(page).toHaveTitle(/^\[Cached\]/);
  if (SCREENSHOT_DIR) await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'system-cached.png'), fullPage: true });
  expect(errors).toEqual([]);
});

test('the case card opens the case assistant drawer', async ({ page }) => {
  await page.goto('/returns/RET-000102');
  await page.getByRole('button', { name: /Ask about this case/ }).click();
  const drawer = page.getByRole('dialog', { name: /Ask about RET-000102/ });
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText('it cannot change it');
  if (SCREENSHOT_DIR) await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'case-marco-assistant.png') });
  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
});

test('the agent stream arrives event by event over the mock SSE', async ({ page }) => {
  const errors = watchConsole(page);
  await page.goto('/dev/agent?return=RET-000102');
  await page.getByRole('button', { name: /Which evidence supports the link/ }).click();
  await expect(page.locator('[data-agent-stream="streaming"]')).toBeVisible();
  await expect(page.locator('[data-agent-stream="done"]')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('[data-generated]')).toContainText('A link is a shared identifier');
  expect(errors).toEqual([]);
});

test('a warehouse persona sees writes disabled with the reason', async ({ page }) => {
  await page.goto('/returns/RET-000102');
  // Switch persona once the case is on screen, not while its chunk and data are loading.
  await expect(page.getByRole('button', { name: /Ask about this case/ })).toBeEnabled({ timeout: 15_000 });
  await page.getByLabel('Acting as').selectOption('ahmed');
  await expect(page.getByRole('button', { name: /Ask about this case/ })).toBeDisabled();
  await expect(page.getByText('This action is for operators and managers (warehouse can view).')).toBeVisible();
  await page.getByLabel('Acting as').selectOption('sara');
});

test('provenance popover shows the origin verbatim', async ({ page }) => {
  await page.goto('/returns/RET-000102');
  const figure = page.locator('[data-figure] button').first();
  await figure.focus();
  await page.keyboard.press('Enter');
  const detail = page.locator('[data-provenance-detail]');
  await expect(detail).toBeVisible();
  await expect(detail).toContainText('Contract example:');
  await expect(detail.getByRole('link', { name: 'What do natures mean?' })).toBeVisible();
  if (SCREENSHOT_DIR) await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'provenance-popover.png') });
});
