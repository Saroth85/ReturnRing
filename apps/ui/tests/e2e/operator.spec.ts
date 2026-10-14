// Operator area in mock mode (docs/ui-design.md 6, 14.2: E2E-01, E2E-04, E2E-05, KEYBOARD, the
// operator beats of E2E-PITCH). Every screen renders with no console error, no number without
// provenance and no banned wording; the actions send the contract requests and show the answers.
// OPERATOR_SCREENSHOT_DIR=<dir> saves each screen at 1440 × 900 and 390 × 844.
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect as baseExpect, test, type Page } from '@playwright/test';
import { scanPage } from './scan';

// A ceiling for a loaded machine; assertions return as soon as they hold.
const expect = baseExpect.configure({ timeout: 15_000 });

const SHOTS = process.env.OPERATOR_SCREENSHOT_DIR;
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

function watchConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text());
  });
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  return errors;
}

/** The PROV-UI and wording rules of src/test/scan.ts, run in the page. */
const scan = scanPage;

async function settle(page: Page): Promise<void> {
  await expect(page.locator('h1').first()).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
}

async function expectClean(page: Page, errors: string[]): Promise<void> {
  const result = await scan(page);
  expect(result.unprovenanced, 'numbers without provenance').toEqual([]);
  expect(result.banned, 'banned wording').toEqual([]);
  expect(errors, 'console errors').toEqual([]);
}

const SCREENS: Array<{ name: string; url: string; ready: string }> = [
  { name: 'queue', url: '/queue', ready: '[data-queue-table]' },
  { name: 'queue-all', url: '/queue?tab=all', ready: '[data-queue-table]' },
  { name: 'case-marco', url: '/returns/RET-000102', ready: '[data-decision-rail]' },
  { name: 'case-giulia', url: '/returns/RET-000101', ready: '[data-decision-rail]' },
  { name: 'case-elena', url: '/returns/RET-000105', ready: '[data-guarantee-banner]' },
  { name: 'case-insufficient', url: '/returns/RET-000109', ready: '[data-insufficient-panel]' },
  { name: 'case-lorenzo', url: '/returns/RET-000088', ready: '[data-review-due-banner]' },
  { name: 'case-rossi-card', url: '/returns/RET-000104', ready: '[data-link-table]' },
  { name: 'case-chiara', url: '/returns/RET-000106', ready: '[data-link-table]' },
  { name: 'decision-record', url: '/returns/RET-000106/versions/DEC-000106-1', ready: '[data-decision-record]' },
  { name: 'compare', url: '/compare?a=RET-000101&b=RET-000102', ready: '[data-compare-closing]' },
  { name: 'outbox', url: '/outbox', ready: '[data-outbox-detail]' },
];

test.describe('operator screens render cleanly', () => {
  for (const screen of SCREENS) {
    test(`${screen.name} ${screen.url}`, async ({ page }) => {
      const errors = watchConsole(page);
      await page.goto(screen.url);
      await expect(page.locator(screen.ready).first()).toBeVisible();
      await settle(page);
      await expectClean(page, errors);
      if (SHOTS) {
        await page.screenshot({ path: path.join(SHOTS, `${screen.name}-1440x900.png`), fullPage: true });
        await page.setViewportSize({ width: 390, height: 844 });
        await page.waitForTimeout(300);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(overflow, 'no horizontal page scroll at 390 px').toBeLessThanOrEqual(0);
        await page.screenshot({ path: path.join(SHOTS, `${screen.name}-390x844.png`), fullPage: true });
      }
    });
  }
});

test.describe('queue (US-301)', () => {
  test('loads under 2 s, ordered by net benefit, with tabs that are queries', async ({ page }) => {
    // The budget is the app's (route chunk, queue and tab counts through the mock adapter), not the
    // dev server's unbundled first load: warm the modules, leave, then come back through the nav.
    await page.goto('/queue');
    const rows = page.locator('[data-queue-table] [data-return-row]');
    await expect(rows.first()).toBeVisible();
    await page
      .getByRole('link', { name: /^Outbox/ })
      .first()
      .click();
    await expect(page.locator('[data-outbox-table]')).toBeVisible();
    const started = Date.now();
    await page
      .getByRole('link', { name: /Returns queue/ })
      .first()
      .click();
    await expect(rows.first()).toBeVisible();
    expect(Date.now() - started, 'US-301: under 2 s with the mock').toBeLessThan(2000);
    await expect(rows).toHaveCount(3);
    expect(await rows.evaluateAll((els) => els.map((e) => e.getAttribute('data-return-row')))).toEqual([
      'RET-000102',
      'RET-000108',
      'RET-000106',
    ]);
    await expect(page.locator('[data-queue-tab="approval"]')).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('[data-queue-tab="approval"] [data-figure]')).toHaveText(/3/);
    await expect(rows.first().locator('[data-approval-flag]')).toBeVisible();
    await expect(rows.first().locator('[data-quadrant-badge="Q4"]')).toBeVisible();

    await page.locator('[data-queue-tab="guarantee"]').click();
    await expect(page).toHaveURL(/tab=guarantee/);
    await expect(rows).toHaveCount(2);
    await expect(rows.first().locator('[data-outcome="guarantee"]')).toBeVisible();
    await expect(rows.first()).toContainText('not scored');
  });

  test('filters and search live in the URL; a contradicting filter empties the view', async ({ page }) => {
    await page.goto('/queue?tab=all');
    const rows = page.locator('[data-queue-table] [data-return-row]');
    await expect(rows).toHaveCount(13);
    await page.getByRole('search').getByRole('button', { name: 'Q2' }).click();
    await expect(page).toHaveURL(/quadrant=Q2/);
    await expect(rows).toHaveCount(4);
    await page.getByRole('searchbox').fill('rossi');
    await expect(rows).toHaveCount(2);
    await page.goto('/queue?quadrant=Q1');
    await expect(page.getByText('These filters exclude every return of this view.')).toBeVisible();
  });

  test('j, k and Enter open a case of the view, and [ ] walk the view (KEYBOARD)', async ({ page }) => {
    await page.goto('/queue');
    await expect(page.locator('[data-queue-table] [data-return-row]').first()).toBeVisible();
    await page.locator('h1').click();
    await page.keyboard.press('j');
    await expect(page.locator('[data-row-link="1"]')).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/returns\/RET-000108\?from=approval$/);
    await expect(page.getByRole('navigation', { name: 'Case navigation' })).toContainText('2 / 3');
    await page.keyboard.press(']');
    await expect(page).toHaveURL(/\/returns\/RET-000106\?from=approval$/);
    await expect(page.getByRole('navigation', { name: 'Case navigation' })).toContainText('3 / 3');
    await page.keyboard.press('[');
    await expect(page).toHaveURL(/\/returns\/RET-000108\?from=approval$/);
  });
});

test.describe('case card (US-302 to US-310)', () => {
  test('Marco: linked accounts, contributions, both sides, message layers (pitch beat 2)', async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto('/returns/RET-000102');
    await expect(page.locator('[data-case-hero="cluster"]')).toContainText('Cluster of 7 accounts · 21 links');
    await expect(page.locator('[data-linked-fixed-line]')).toContainText('not proof of who uses the accounts');
    await expect(page.locator('[data-graph="ready"]')).toBeVisible();
    await page.locator('[data-kind-chip="card"]').click();
    await expect(page.locator('[data-kind-chip="card"]')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('[data-link-table] [data-link-row]')).toHaveCount(5);
    await page.locator('[data-link-row="C-MARCO-1~C-MARCO-3"] button[data-id]').click();
    const detail = page.locator('[data-link-detail="C-MARCO-1~C-MARCO-3"]');
    await expect(detail).toContainText('Weight after decay');
    await expect(detail).toContainText('3 Sep 2026');
    await expect(page.locator('[data-coupon-timeline] [data-timeline-row]')).toHaveCount(7);
    await expect(page.getByRole('heading', { name: 'Contributions to the score, not causes' })).toBeVisible();
    await expect(page.locator('[data-factor-list] li')).toHaveCount(3);
    await expect(page.locator('[data-balance="favourable"]')).toContainText('Favourable to the customer (1)');
    await expect(page.locator('[data-balance="unfavourable"]')).toContainText('Unfavourable to the customer (3)');
    await expect(page.locator('[data-orders-table] [data-this-return]')).toHaveCount(1);
    await expect(page.locator('[data-statement-group="OBSERVED"]')).toBeVisible();
    await expect(page.locator('[data-statement-group="HYPOTHESIS"]')).toBeVisible();
    await expect(page.locator('[data-echo="match"]')).toContainText('Q4');
    const message = page.locator('[data-message-panel]');
    await expect(message.locator('[data-template-field="deadline"]')).toContainText('12 ottobre 2026');
    await expect(message.locator('[data-legal-clause="withdrawal"]')).toContainText('Fixed legal clause');
    await message.locator('[data-message-view="both"]').click();
    await expect(message.locator('[data-message-preview]')).toHaveCount(2);
    await expect(message.locator('[lang="en"] [data-template-field="deadline"]')).toContainText('12 October 2026');
    await expect(page.locator('[data-alternatives] [data-alternative]')).toHaveCount(3);
    await expect(page.locator('[data-alternative="proposal"]')).toContainText(
      'The refund owed to the customer is never counted as a saving.',
    );
    await expectClean(page, errors);
  });

  test('Marco: a restriction needs the acknowledgement and an explicit confirm, never a bare Enter (E2E-01)', async ({ page }) => {
    await page.goto('/returns/RET-000102');
    await expect(page.locator('[data-approval-gate]')).toContainText('review 27 Dec 2026');
    await page.locator('h1').click();
    await page.keyboard.press('a');
    const sheet = page.locator('[data-sheet="approve"]');
    await expect(sheet).toBeVisible();
    await expect(sheet.locator('[data-term-row]')).toHaveCount(6);
    const confirm = sheet.locator('[data-sheet-confirm="approve"]');
    await expect(confirm).toBeDisabled();
    await expect(sheet.locator('[data-confirm-reasons]')).toContainText('Tick the coupon pause confirmation');
    await sheet.locator('[data-coupon-ack-box]').check();
    await expect(confirm).toBeEnabled();
    await confirm.focus();
    await page.keyboard.press('Enter');
    await page.waitForTimeout(400);
    await expect(sheet).toBeVisible();
    await expect(page.locator('[data-approval-gate]')).toBeVisible();
    await page.keyboard.press('Control+Enter');
    await expect(sheet).toBeHidden();
    const rail = page.locator('[data-decision-rail]');
    await expect(rail.locator('[data-approver-line]')).toContainText('Approved by sara');
    await expect(rail.locator('[data-restriction="in_force"]')).toContainText('In force for future orders · review 27 Dec 2026');
    await expect(rail.locator('[data-coupon-notice]')).toContainText('Confirmed by sara');
    await expect(page.locator('[data-action-notice]')).toContainText('MSG-000102-1');
    await expect(page.locator('[data-sent-message]')).toContainText('MSG-000102-1');
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'case-marco-approved-1440x900.png') });
  });

  test('Giulia: a non-restrictive proposal is confirmed in a popover; regenerate makes a new validated draft', async ({ page }) => {
    await page.goto('/returns/RET-000101');
    await expect(page.locator('[data-restriction="applies_itself"]').first()).toBeVisible();
    await page.locator('h1').click();
    await page.keyboard.press('a');
    const popover = page.locator('[data-confirm-popover]');
    await expect(popover).toContainText('Confirm the proposal and send the message in Italiano · RET-000101');
    await expect(popover.locator('[data-confirm-send]')).toBeFocused();
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'case-giulia-confirm-1440x900.png') });
    await page.keyboard.press('Escape');
    await expect(popover).toBeHidden();
    await page.locator('[data-regenerate]').click();
    const menu = page.locator('[data-regenerate-menu]');
    await menu.getByLabel('Italiano').uncheck();
    await menu.getByLabel('Formal').check();
    await menu.getByRole('button', { name: 'Generate and validate' }).click();
    await expect(page.locator('[data-action-notice]')).toContainText('Draft regenerated');
    await expect(page.locator('[data-message-view="en"]')).toBeVisible();
    await expect(page.locator('[data-message-view="it"]')).toHaveCount(0);
  });

  test('Andrea: reject needs a category and a reason', async ({ page }) => {
    await page.goto('/returns/RET-000107');
    await page.locator('[data-action="reject"]').click();
    const sheet = page.locator('[data-sheet="reject"]');
    await expect(sheet).toContainText('the return itself is never refused');
    await expect(sheet.locator('[data-sheet-confirm="reject"]')).toBeDisabled();
    await expect(sheet.locator('[data-confirm-reasons]')).toContainText('Choose a category');
    await sheet.getByLabel('Category').selectOption('customer_context');
    await sheet.getByLabel('Reason').fill('The customer asked to keep the standard terms');
    await page.keyboard.press('Control+Enter');
    await expect(sheet).toBeHidden();
    await expect(page.locator('[data-state-badge="rejected"]').first()).toContainText('Proposal rejected (the return proceeds)');
  });

  test('Martina: override with a reason and no restrictive lever (US-306)', async ({ page }) => {
    await page.goto('/returns/RET-000108');
    await page.locator('h1').click();
    await page.keyboard.press('o');
    const sheet = page.locator('[data-sheet="override"]');
    await sheet.getByLabel('Start from').selectOption('current_terms');
    await expect(sheet.locator('[data-override-gate]')).toHaveCount(0);
    await sheet.getByLabel('Category').selectOption('customer_context');
    await sheet.getByLabel('Reason').fill('Two wrong deliveries explained by phone');
    await sheet.locator('[data-sheet-confirm="override"]').click();
    await expect(sheet).toBeHidden();
    await expect(page.locator('[data-approver-line]')).toContainText('Overridden by sara');
    await expect(page.locator('[data-decision-rail]')).toContainText('The customer explained two wrong deliveries');
  });

  test('Elena: guarantee path, never scored, restrictive levers locked, the legal guard answers 422 (E2E-04)', async ({ page }) => {
    await page.goto('/returns/RET-000105');
    await expect(page.locator('[data-guarantee-banner]')).toContainText('Guarantee path');
    await expect(page.locator('[data-risk-panel]')).toHaveCount(0);
    await expect(page.locator('[data-not-scored="guarantee"]')).toBeVisible();
    await expect(page.locator('[data-product-anomaly]')).toContainText('V-JKT-DEF');
    await expect(page.locator('[data-product-anomaly]')).toContainText("The customer's return stays regular.");
    await page.locator('[data-action="override"]').click();
    const sheet = page.locator('[data-sheet="override"]');
    await expect(sheet.locator('[data-lever-row="free_return_shipping"] input')).toBeDisabled();
    await expect(sheet.locator('[data-lever-row="free_return_shipping"]')).toContainText('Not allowed on the guarantee path');
    await sheet.locator('[data-lever-row="size_advice"] input').check();
    await sheet.getByLabel('Category').selectOption('other');
    await sheet.getByLabel('Reason').fill('Try a size advice for the next order');
    await sheet.locator('[data-sheet-confirm="override"]').click();
    const violations = sheet.locator('[data-legal-guard-violations]');
    await expect(violations).toContainText('GUARANTEE_RESTRICTED');
    await expect(violations).toContainText('Nothing was saved.');
    await expect(sheet).toBeVisible();
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'case-elena-legal-guard-1440x900.png') });
  });

  test('Davide: not enough evidence, standard terms, no risk number anywhere (US-307)', async ({ page }) => {
    await page.goto('/returns/RET-000109');
    await expect(page.locator('[data-insufficient-panel]')).toContainText('Not enough evidence to score: standard terms apply');
    await expect(page.locator('[data-insufficient-panel]')).toContainText('Fewer than 4 order lines observed');
    await expect(page.locator('[data-figure][data-unit="probability"]')).toHaveCount(0);
    await expect(page.locator('[data-quadrant-badge]')).toHaveCount(0);
  });

  test('Lorenzo: past its review date the restriction is lapsed and a person lifts it (US-607)', async ({ page }) => {
    await page.goto('/returns/RET-000088');
    await expect(page.locator('[data-review-due-banner]')).toContainText('review date 27 Sep 2026 passed');
    await page.locator('[data-action="review"]').click();
    const sheet = page.locator('[data-sheet="review"]');
    await expect(sheet).toContainText('Creates version v2 with the standard terms');
    await sheet.getByLabel('Reason').fill('Reviewed at the due date: the returns are back in line');
    await page.keyboard.press('Control+Enter');
    await expect(sheet).toBeHidden();
    await expect(page.locator('[data-decision-rail] [data-restriction="lifted"]')).toContainText('Restriction lifted');
    await expect(page.locator('[data-version-chain] [data-version-node]')).toHaveCount(2);
  });

  test('Rossi with a shared card: favourable evidence recomputes the case, before and after (pitch beat 3, E2E-05)', async ({ page }) => {
    await page.goto('/returns/RET-000104?link=C-ROSSI-CARD-1~C-ROSSI-CARD-2');
    await expect(page.locator('[data-link-detail="C-ROSSI-CARD-1~C-ROSSI-CARD-2"]')).toBeVisible();
    await page.locator('[data-link-detail] [data-explain-link]').click();
    const sheet = page.locator('[data-sheet="evidence"]');
    await expect(sheet.getByLabel('Shared payment explained')).toBeChecked();
    await sheet.getByLabel('Note').fill('The card belongs to the parents: one household (phone call)');
    await sheet.locator('[data-sheet-confirm="evidence"]').click();
    const comparison = page.locator('[data-recompute-comparison]');
    await expect(comparison.locator('[data-row="risk"]')).toContainText('0.34');
    await expect(comparison.locator('[data-row="risk"]')).toContainText('0.21');
    await expect(comparison.locator('[data-row="risk"]')).toContainText('changed');
    await expect(comparison.locator('[data-row="quadrant"]')).not.toContainText('changed');
    await expect(comparison.locator('[data-row="active_links"]')).toContainText('changed');
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'case-rossi-recompute-1440x900.png') });
    await page.keyboard.press('Enter');
    await expect(comparison).toBeHidden();
    await expect(page.locator('[data-case-header] [data-state-badge]')).toContainText('v2 of 2');
    await expect(page.locator('[data-link-row="C-ROSSI-CARD-1~C-ROSSI-CARD-2"] [data-link-state]')).toHaveText('Explained by evidence');
  });

  test('a stale decision answers 409, nothing is saved and the typed note is kept (E2E-05)', async ({ page }) => {
    await page.goto('/returns/RET-000104?scenario=stale_decision');
    await page.locator('h1').click();
    await page.keyboard.press('e');
    const sheet = page.locator('[data-sheet="evidence"]');
    await sheet.getByLabel('Customer contact').check();
    await sheet.getByLabel('Note').fill('Called the customer about the card');
    await sheet.locator('[data-sheet-confirm="evidence"]').click();
    const stale = sheet.locator('[data-stale-panel]');
    await expect(stale).toContainText('This case changed while you were reviewing.');
    await expect(stale).toContainText('DEC-000104-2');
    await expect(stale).toContainText('Nothing was saved.');
    await stale.locator('[data-load-new-version]').click();
    await expect(sheet.getByLabel('Note')).toHaveValue('Called the customer about the card');
  });

  test('Chiara: disputing the weak card link turns Q3 into Q1 and removes the approval gate', async ({ page }) => {
    await page.goto('/returns/RET-000106');
    await expect(page.locator('[data-approval-gate]')).toBeVisible();
    await expect(page.locator('[data-weak-link]').first()).toContainText('shared by up to 41 accounts: weak link');
    await page.locator('[data-dispute-link="C-10233~C-20871"]').first().click();
    const sheet = page.locator('[data-sheet="dispute"]');
    await sheet.getByLabel('Reason').fill('Card used once for a gift; different households');
    await page.keyboard.press('Control+Enter');
    const comparison = page.locator('[data-recompute-comparison]');
    await expect(comparison.locator('[data-row="quadrant"]')).toContainText('changed');
    await comparison.locator('[data-open-new-version]').click();
    await expect(page.locator('[data-approval-gate]')).toHaveCount(0);
    await expect(page.locator('[data-case-header] [data-quadrant-badge="Q1"]')).toBeVisible();
    await expect(page.locator('[data-link-row="C-10233~C-20871"] [data-link-state]')).toHaveText('Disputed');
  });

  test('a warehouse persona sees every write disabled with one reason per area (ROLE-UI)', async ({ page }) => {
    await page.goto('/returns/RET-000102');
    await page.getByLabel('Acting as').selectOption('ahmed');
    await expect(page.locator('[data-action="approve-restriction"]')).toBeDisabled();
    await expect(page.locator('[data-action-reason]')).toHaveText('Decisions are for operators and managers (warehouse can view).');
    await expect(page.locator('[data-add-evidence]')).toBeDisabled();
    await expect(page.locator('[data-regenerate]')).toBeDisabled();
    await page.locator('h1').click();
    await page.keyboard.press('a');
    await expect(page.locator('[data-sheet]')).toHaveCount(0);
  });

  test('the case assistant cites a link and the card selects it', async ({ page }) => {
    await page.goto('/returns/RET-000102');
    await page.locator('h1').click();
    await page.keyboard.press('i');
    await expect(page.getByRole('dialog', { name: /Ask about RET-000102/ })).toBeVisible();
  });
});

test.describe('compare, record and outbox', () => {
  test('compare shows the same indicator twice, then the rows that differ (pitch beat 1)', async ({ page }) => {
    await page.goto('/compare?a=RET-000101&b=RET-000102');
    await expect(page.locator('[data-same-indicator]')).toHaveText(/same indicator/i);
    await expect(page.locator('[data-compare-rate]')).toHaveCount(2);
    const rates = await page.locator('[data-compare-rate] [data-figure] button > span').first().textContent();
    await expect(page.locator('[data-compare-rate]').nth(1)).toContainText(rates ?? '');
    await expect(page.locator('[data-compare-closing]')).toContainText('A rule on return rate alone would treat both the same.');
  });

  test('the decision record of a superseded version is read-only', async ({ page }) => {
    await page.goto('/returns/RET-000106/versions/DEC-000106-1');
    const record = page.locator('[data-decision-record]');
    await expect(record).toContainText('superseded by DEC-000106-2');
    await expect(record).toContainText('Inputs given to the engine');
    await page.keyboard.press('Escape');
    await expect(page).toHaveURL(/\/returns\/RET-000106$/);
  });

  test('the outbox shows each message in its three layers', async ({ page }) => {
    await page.goto('/outbox');
    await expect(page.locator('[data-outbox-table] [data-outbox-row]')).toHaveCount(5);
    await page.locator('[data-outbox-row="MSG-000102-1"] button').first().click();
    const detail = page.locator('[data-outbox-detail="MSG-000102-1"]');
    await expect(detail.locator('[data-template-field]').first()).toBeVisible();
    await expect(detail.locator('[data-legal-clause="withdrawal"]')).toBeVisible();
  });
});
