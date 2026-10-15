// The read-only public demo against the live API (PUBLIC_READ_ONLY=on; ADR-15 sections 15 and 19):
// the UI learns it from /readyz `public_read_only`, not from its build. Every write control is on
// screen, disabled, with "Read-only public demo"; the two writes the public pages keep (the agent,
// which only reads the case, and the checkout evaluation) still answer; the demo clock is frozen
// at the snapshot, so no refund deadline passes while the demo stays up; nothing changes in the
// store. Skipped unless the API says it is read-only.
import { expect, test, type Locator, type Page } from '@playwright/test';
import type { CaseCard, DemoCasesResponse, ReadyzResponse } from '../../../src/api/models';
import { actAs, apiGet, caseCard, caseOf, demoCases, expectClean, readyz, settle, watchConsole } from './live';

const READ_ONLY = 'Read-only public demo';

let ready: ReadyzResponse;
let cases: DemoCasesResponse;
/** Marco's case when the spec starts: the read-only demo must leave it as it is. */
let marcoBefore: CaseCard;

test.beforeAll(async () => {
  ready = await readyz();
  cases = await demoCases();
  marcoBefore = await caseCard(caseOf(cases, 'marco').return_id);
});

test.beforeEach(() => {
  test.skip(!ready.public_read_only, 'the API is writable: start it with PUBLIC_READ_ONLY=on for this spec');
});

async function open(page: Page, path: string): Promise<string[]> {
  const errors = watchConsole(page);
  await page.goto(path);
  await settle(page);
  return errors;
}

/** A write control: on screen, disabled, with the read-only reason under it (never hidden). */
async function expectReadOnlyGate(control: Locator): Promise<void> {
  await expect(control).toBeVisible();
  await expect(control).toBeDisabled();
  await expect(control.locator('xpath=ancestor::*[@data-role-gate][1]').locator('[data-role-reason]')).toHaveText(READ_ONLY);
}

test('the shell says read-only and the clock stands at the snapshot', async ({ page }) => {
  expect(ready.clock.frozen, 'the read-only demo runs on a frozen clock (ADR-15 section 19)').toBe(true);
  const errors = await open(page, '/about');
  await expect(page.getByRole('region', { name: 'Service mode' })).toContainText('read-only public demo');
  const clock = page.locator('[data-banner-clock]');
  await expect(clock).toHaveAttribute('data-clock-frozen', 'yes');
  await expect(clock).toContainText('frozen at the snapshot');
  await expect(page.locator('[data-top-clock] time')).toHaveAttribute('datetime', new Date(ready.clock.now).toISOString());
  await expectClean(page, errors);
});

test('the case cards: every act, the evidence, the dispute and the regeneration are off, with the reason', async ({ page }) => {
  for (const key of ['marco', 'giulia', 'rossi_card', 'elena', 'insufficient'] as const) {
    await test.step(key, async () => {
      page.removeAllListeners('console');
      page.removeAllListeners('pageerror');
      const found = caseOf(cases, key);
      const errors = await open(page, `/returns/${found.return_id}`);
      await expect(page.locator('[data-read-only-banner="public"]')).toContainText(READ_ONLY);
      const actions = page.locator('[data-action-bar] [data-action]');
      const count = await actions.count();
      for (let i = 0; i < count; i += 1) await expect(actions.nth(i)).toBeDisabled();
      if (count > 0) await expect(page.locator('[data-action-reason]')).toContainText(READ_ONLY);
      for (const button of await page.locator('[data-add-evidence], [data-regenerate], [data-dispute-link], [data-explain-link]').all()) {
        await expect(button).toBeDisabled();
      }
      await expectClean(page, errors);
    });
  }
  // Nothing changed in the store; and rule 5 holds without anyone acting: a proposal is not in force.
  const marco = await caseCard(caseOf(cases, 'marco').return_id);
  expect(marco.decision.decision_id).toBe(marcoBefore.decision.decision_id);
  expect(marco.decision.state).toBe(marcoBefore.decision.state);
  if (marco.decision.state === 'proposed') expect(marco.decision.in_force).toBe(false);
});

test('the agent still answers: it only reads the case', async ({ page }) => {
  test.skip(ready.mode === 'cached', 'the agent needs the live model (503 in the cached demo)');
  const marco = caseOf(cases, 'marco');
  const errors = await open(page, `/returns/${marco.return_id}`);
  await page.getByRole('button', { name: /Ask about this case/ }).click();
  const drawer = page.getByRole('dialog', { name: new RegExp(`Ask about ${marco.return_id}`) });
  await drawer.locator('[data-suggested]').first().click();
  await expect(drawer.locator('article[data-turn="done"]')).toHaveCount(1, { timeout: 90_000 });
  await expectClean(page, errors);
});

test('the shop evaluates the cart and shows the terms; placing the order is off', async ({ page }) => {
  const errors = await open(page, '/shop?preset=ring');
  await expect(page.locator('[data-evaluation-status="ready"]')).toBeVisible({ timeout: 60_000 });
  const shop = page.locator('[data-shop-pane]');
  await expect(shop).toContainText('You can always withdraw within 14 days of delivery, without giving a reason.');
  await expectReadOnlyGate(shop.getByRole('button', { name: 'Place order' }));
  await expectClean(page, errors);
});

test("the manager's writes are off: assumptions, coupon rules and reversals, catalogue tasks", async ({ page }) => {
  await actAs(page, 'luca');
  let errors = await open(page, '/simulator');
  await expectReadOnlyGate(page.getByRole('button', { name: /Save as/ }));
  await expectClean(page, errors);

  page.removeAllListeners('console');
  errors = await open(page, '/coupons');
  await expectReadOnlyGate(page.getByRole('button', { name: 'Approve as the new version' }));
  for (const reverse of await page.getByRole('button', { name: /^Reverse/ }).all()) await expectReadOnlyGate(reverse);
  await expectClean(page, errors);

  const elena = await caseCard(caseOf(cases, 'elena').return_id);
  if (elena.product_anomaly) {
    page.removeAllListeners('console');
    errors = await open(page, `/products/${encodeURIComponent(elena.product_anomaly.anomaly_id)}?return=${elena.return_id}`);
    const form = page.locator('[data-catalog-task-form]');
    if ((await form.count()) > 0) await expectReadOnlyGate(form.getByRole('button', { name: 'Open the task' }));
    await expectClean(page, errors);
  }

  page.removeAllListeners('console');
  errors = await open(page, '/reviews');
  await expectClean(page, errors);
  const tasks = await apiGet<{ items: unknown[] }>('/api/catalog-tasks', 'luca');
  if (tasks.items.length > 0) {
    page.removeAllListeners('console');
    errors = await open(page, '/products/tasks');
    const moves = page.locator('[data-move]');
    for (let i = 0; i < (await moves.count()); i += 1) await expect(moves.nth(i)).toBeDisabled();
    await expectClean(page, errors);
  }
});

test('the warehouse cannot send a photo on the read-only demo', async ({ page }) => {
  await actAs(page, 'ahmed');
  let errors = await open(page, '/inspection');
  const row = page.locator('[data-inspection-row]').first();
  test.skip((await row.count()) === 0, 'no return awaits inspection');
  const returnId = await row.getAttribute('data-inspection-row');
  page.removeAllListeners('console');
  errors = await open(page, `/inspection/${returnId}`);
  await expect(page.locator('[data-inspection-read-only]')).toBeVisible();
  const send = page.locator('[data-send-photo]');
  if ((await send.count()) > 0) await expect(send).toBeDisabled();
  await expectClean(page, errors);
});
