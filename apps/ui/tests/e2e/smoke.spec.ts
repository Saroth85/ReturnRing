// Smoke test in mock mode (contract examples): every route renders without console errors, with
// no number lacking provenance (PROV-UI baseline) and no banned wording (spec 16). In the cached
// scenario the banner, the corner tag and the title say so (MODE-UI baseline).
// SCREENSHOT_DIR=<dir> saves one full-page screenshot per route.
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { RENDERED_TEXT_BANNED } from '../../src/copy/bannedTerms';
import { PROVENANCE_EXEMPT_SELECTOR, VERBATIM_SELECTOR } from '../../src/test/exempt';
import { SMOKE_ROUTES } from './routes';

const SCREENSHOT_DIR = process.env.SCREENSHOT_DIR;
if (SCREENSHOT_DIR) mkdirSync(SCREENSHOT_DIR, { recursive: true });

interface PageScan {
  unprovenanced: string[];
  banned: string[];
  bannedVerbatim: string[];
}

/** Runs in the page: the same rules as src/test/scan.ts, self-contained. */
async function scanPage(page: Page): Promise<PageScan> {
  const patterns = RENDERED_TEXT_BANNED.map((t) => t.pattern);
  return page.evaluate(
    ({ exempt, verbatim, patterns }) => {
      const idToken = /(?<![\p{L}\p{N}])[A-Z][A-Z0-9]*(?:[-~][A-Za-z0-9]+)+(?![\p{L}\p{N}])/gu;
      const banned = patterns.map((p) => new RegExp(`(?<![\\p{L}\\p{N}])(?:${p})(?![\\p{L}\\p{N}])`, 'iu'));
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      const result = { unprovenanced: [] as string[], banned: [] as string[], bannedVerbatim: [] as string[] };
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const text = node.textContent ?? '';
        const parent = node.parentElement;
        if (!text.trim() || !parent) continue;
        if (/\d/.test(text.replace(idToken, '')) && !parent.closest(exempt)) result.unprovenanced.push(text.trim());
        for (const re of banned) {
          if (re.test(text)) (parent.closest(verbatim) ? result.bannedVerbatim : result.banned).push(text.trim());
        }
      }
      return result;
    },
    { exempt: PROVENANCE_EXEMPT_SELECTOR, verbatim: VERBATIM_SELECTOR, patterns },
  );
}

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
