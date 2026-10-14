// Manager area in mock mode (docs/ui-design.md 8, 12 beat 5, 14.2): the matrix, the simulator
// (pitch beat 5: +€368, +€48, −€192), KPIs, fairness, product issues and catalogue tasks (E2E-04
// part), reviews and the audit log with CSV export. Every screen is scanned for numbers without
// provenance and banned wording, at 1440 × 900 and 390 × 844.
// SCREENSHOT_DIR=<dir> saves manager-<screen>-<width>.png for each screen.
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { scanPage } from './scan';

const SCREENSHOT_DIR = process.env.SCREENSHOT_DIR;
if (SCREENSHOT_DIR) mkdirSync(SCREENSHOT_DIR, { recursive: true });

const SCREENS = [
  { name: 'portfolio', path: '/portfolio' },
  { name: 'portfolio-q4-list', path: '/portfolio?quadrant=Q4' },
  { name: 'simulator', path: '/simulator' },
  { name: 'simulator-both', path: '/simulator?scenario=both' },
  { name: 'kpi', path: '/kpi?week=2026-09-21' },
  { name: 'fairness', path: '/fairness' },
  { name: 'products', path: '/products' },
  { name: 'product-anomaly', path: '/products/V-JKT-DEF~defective~2026-09-21' },
  { name: 'catalog-tasks', path: '/products/tasks' },
  { name: 'reviews', path: '/reviews' },
  { name: 'audit', path: '/audit' },
] as const;

async function actAs(page: Page, persona: 'sara' | 'luca' | 'ahmed') {
  await page.addInitScript((p) => window.localStorage.setItem('returnring.persona', p), persona);
}

function watchConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  return errors;
}

async function settle(page: Page) {
  // A ceiling for a busy machine (the E2E runs on a mock build, UI39); assertions return as soon as they hold.
  await expect(page.locator('h1').first()).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('[aria-busy="true"], [data-data-check="pending"]')).toHaveCount(0, { timeout: 15_000 });
}

/** The PROV-UI and wording rules of the smoke test, in the page. */
const scan = scanPage;

for (const viewport of [
  { width: 1440, height: 900 },
  { width: 390, height: 844 },
]) {
  test.describe(`manager screens at ${viewport.width} px`, () => {
    test.use({ viewport });
    for (const screen of SCREENS) {
      test(`${screen.path} renders with provenance, clean wording and no horizontal page scroll`, async ({ page }) => {
        await actAs(page, 'luca');
        const errors = watchConsole(page);
        await page.goto(screen.path);
        await settle(page);
        const result = await scan(page);
        expect(result.unprovenanced, 'numbers without provenance').toEqual([]);
        expect(result.banned, 'banned wording in UI text').toEqual([]);
        expect(result.scrollWidth, 'horizontal page scroll').toBeLessThanOrEqual(result.width);
        expect(errors, 'console errors').toEqual([]);
        if (SCREENSHOT_DIR) {
          await page.screenshot({ path: path.join(SCREENSHOT_DIR, `manager-${screen.name}-${viewport.width}.png`), fullPage: true });
        }
      });
    }
  });
}

test.describe('portfolio matrix (US-601)', () => {
  test('annotates the demo cases, shows a customer on hover and opens the quadrant list', async ({ page }) => {
    await actAs(page, 'luca');
    await page.goto('/portfolio');
    await settle(page);
    await expect(page.locator('[data-demo-cluster="CL-000417"]')).toContainText('CL-000417');
    await expect(page.locator('[data-value-legend]')).toHaveText(
      'Customer value orients benefits and service. It is never a signal of abuse and never feeds the risk score.',
    );
    const giulia = page.locator('[data-demo-customer="C-GIULIA"] circle').first();
    const box = await giulia.boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.move((box?.x ?? 0) + (box?.width ?? 0) / 2, (box?.y ?? 0) + (box?.height ?? 0) / 2);
    await expect(page.locator('[data-matrix-tooltip]')).toContainText('Giulia Conti');
    await page.locator('[data-open-quadrant="Q4"]').click();
    const drawer = page.getByRole('dialog', { name: 'Q4 customers' });
    await expect(drawer.locator('[data-customer]')).toHaveCount(7);
    await expect(drawer.locator('[data-customer="C-MARCO-1"]')).toContainText('CL-000417');
    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden();
    await expect(page).toHaveURL(/\/portfolio$/);
  });
});

test.describe('economic simulator (US-602, pitch beat 5)', () => {
  test('Base +€368, then Logistics halved +€48, then Both −€192 with the words', async ({ page }) => {
    await actAs(page, 'luca');
    await page.goto('/simulator');
    await settle(page);
    const total = page.locator('[data-shown-scenario] [data-net-total]');
    await expect(total).toContainText('+€368.00');
    const scenarios = page.getByRole('list', { name: 'Sensitivity scenarios' });
    await scenarios.getByRole('button', { name: 'Logistics halved' }).click();
    await expect(total).toContainText('+€48.00');
    await scenarios.getByRole('button', { name: 'Both' }).click();
    await expect(total).toContainText('−€192.00');
    await expect(page.locator('[data-shown-scenario="both"]')).toContainText('Net benefit is negative under these assumptions');
    await expect(page.locator('[data-assumption-label="assumptions-v1/both"]')).toBeVisible();
    await expect(page.locator('[data-shown-scenario]')).toContainText('The refund owed to the customer is never counted as a saving.');
  });

  test('an edit is computed in the browser and saved as a new version by the manager', async ({ page }) => {
    await actAs(page, 'luca');
    await page.goto('/simulator');
    await settle(page);
    await page.getByLabel('Logistics cost per return').fill('6.5');
    await expect(page.getByText('Unsaved scenario · computed in your browser · simulated')).toBeVisible();
    await expect(page.locator('[data-shown-scenario] [data-net-total]')).toContainText('+€248.00');
    await expect(page.locator('[data-shown-scenario]')).toHaveAttribute('data-source', 'browser');
    await page.getByLabel('Note for the new version (required)').fill('Carrier contract renegotiated: 6.50 EUR per return');
    await page.getByRole('button', { name: /Save as/ }).click();
    await expect(page.locator('[data-saved-version="assumptions-v2"]')).toBeVisible();
    await expect(page.locator('[data-history-version="assumptions-v2"]')).toBeVisible();
    if (SCREENSHOT_DIR) await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'manager-simulator-saved-1440.png'), fullPage: true });
  });

  test('an operator sees the save disabled with the reason', async ({ page }) => {
    await actAs(page, 'sara');
    await page.goto('/simulator');
    await settle(page);
    await page.getByLabel('Logistics cost per return').fill('6.5');
    await expect(page.getByRole('button', { name: /Save as/ })).toBeDisabled();
    await expect(page.getByText('This action is for managers (operator can view).')).toBeVisible();
  });
});

test.describe('fairness and KPIs (US-603, US-605)', () => {
  test('the little-data gap is above the threshold and needs review; realised benefit is not observed yet', async ({ page }) => {
    await actAs(page, 'luca');
    await page.goto('/fairness');
    await settle(page);
    await expect(page.locator('[data-fairness-dimension="low_data"]')).toContainText('Gap above the');
    await expect(page.locator('[data-warning]')).toHaveCount(1);
    await page.goto('/kpi?week=2026-09-21');
    await settle(page);
    await expect(page.locator('[data-kpi-net-benefit]')).toContainText('+€140.83');
    await expect(page.locator('[data-realised]')).toContainText('Not observed yet');
  });
});

test.describe('product issues and catalogue tasks (US-208, US-606, E2E-04 part)', () => {
  test("Elena's jacket: z = 6.86, regular returns, no restriction, and the trend", async ({ page }) => {
    await actAs(page, 'luca');
    await page.goto('/products/V-JKT-DEF~defective~2026-09-21');
    await settle(page);
    await expect(page.locator('[data-anomaly-summary]')).toContainText('z = 6.86');
    await expect(page.locator('[data-linked-returns]')).toContainText('RET-000105');
    await expect(page.getByText(/No restriction applies to these customers/)).toBeVisible();
    await expect(page.locator('[data-trend-chart] svg').first()).toBeVisible();
  });

  test('a catalogue task is created from an open anomaly and moved forward', async ({ page }) => {
    await actAs(page, 'luca');
    await page.goto('/products/V-SNK-2290~defective~2026-09-21');
    await settle(page);
    const form = page.locator('[data-catalog-task-form]');
    await form.getByLabel('Note (required)').fill('Check the sole glue of the September batch');
    await form.getByRole('button', { name: 'Open the task' }).click();
    await expect(page.locator('[data-task-created]')).toContainText('opened');
    await page.goto('/products/tasks');
    await settle(page);
    const task = page.locator('[data-task="CT-000012"]');
    await task.getByRole('button', { name: 'Start' }).click();
    await expect(task.locator('[data-task-updated]')).toContainText('In progress');
    await expect(page.locator('[data-task="CT-000007"] [data-caveat]')).toBeVisible();
  });
});

test.describe('reviews and audit (US-607, US-604)', () => {
  test('a lapsed restriction opens the operator review sheet by URL', async ({ page }) => {
    await actAs(page, 'luca');
    await page.goto('/reviews');
    await settle(page);
    const row = page.locator('[data-review-row="RET-000088"]');
    await expect(row.locator('[data-lapsed]')).toContainText('Lapsed on 27 Sep 2026');
    await expect(row).toContainText('luca');
    await row.locator('[data-review-link]').click();
    await expect(page).toHaveURL(/\/returns\/RET-000088\?action=review$/);
  });

  test('the audit log filters, expands a row and exports the CSV', async ({ page }) => {
    await actAs(page, 'luca');
    await page.goto('/audit');
    await settle(page);
    await expect(page.locator('[data-audit-row="3"] [data-human-approval]')).toContainText('27 Dec 2026');
    await page.locator('[data-audit-row="3"]').getByRole('button', { name: /Details/ }).click();
    await expect(page.locator('[data-audit-details="3"]')).toContainText('policy-2026-10-12');
    await page.getByLabel('Return', { exact: true }).fill('RET-000102');
    await page.getByRole('button', { name: 'Apply' }).click();
    await expect(page).toHaveURL(/return_id=RET-000102/);
    await expect(page.locator('[data-audit-row]')).toHaveCount(4);
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Export CSV' }).click();
    expect((await download).suggestedFilename()).toBe('returnring-audit-RET-000102.csv');
  });
});
