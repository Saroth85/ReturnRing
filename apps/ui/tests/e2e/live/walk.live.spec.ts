// The golden demo walk through the UI against the live API (spec 16, US-903), step for step the
// walk of scripts/demo_walk.py: readiness, the demo cases, the queue, Giulia, Marco approved with
// his review date and the legal clause, the ring cart evaluated and confirmed in the shop, Rossi,
// Elena and her catalogue task, the case with insufficient evidence, the dispute, the three agent
// questions with the mock language model, and the manager's pages. Every id comes from GET
// /api/demo/cases; every page is scanned for numbers without provenance and banned wording.
//
// It writes (approvals, an order, a task, a dispute): run it once after `python -m apps.api.reset`
// against a writable API. On the read-only public demo it is skipped (readonly.live.spec.ts).
import { expect, test, type Page } from '@playwright/test';
import type { CaseCard, DemoCasesResponse, ReadyzResponse } from '../../../src/api/models';
import { apiGet, caseCard, caseOf, demoCases, expectClean, readyz, settle, switchPersona, watchConsole } from './live';

test.describe.configure({ mode: 'serial' });

let ready: ReadyzResponse;
let cases: DemoCasesResponse;
/** What the walk did, for the later steps (Marco's approval feeds the shop and the manager). */
const walked: { marcoApplication?: string; marcoReviewAt?: string } = {};

const id = (key: Parameters<typeof caseOf>[1]) => caseOf(cases, key).return_id;

test.beforeAll(async () => {
  ready = await readyz();
  cases = await demoCases();
});

test.beforeEach(() => {
  test.skip(ready.public_read_only, 'the walk writes: run it against a writable API (PUBLIC_READ_ONLY=off), after a fresh reset');
});

/** Open a page as the walk's persona and wait for its data. */
async function open(page: Page, path: string, expectedStatuses: number[] = []): Promise<string[]> {
  const errors = watchConsole(page, expectedStatuses);
  await page.goto(path);
  await settle(page);
  return errors;
}

/** Confirm a non-restrictive proposal from the rail's popover, with the message it sends. */
async function confirmProposal(page: Page, clause: 'withdrawal' | 'guarantee' | null): Promise<void> {
  await page.locator('[data-action-bar] [data-action="confirm"]').click();
  const popover = page.locator('[data-confirm-popover]');
  await expect(popover.locator('[data-compact-message]')).toBeVisible();
  if (clause) await expect(popover.locator(`[data-legal-clause="${clause}"]`)).toBeVisible();
  else await expect(popover.locator('[data-legal-clause]')).toHaveCount(0);
  await popover.locator('[data-confirm-send]').click();
  const notice = page.locator('[data-action-notice]');
  await expect(notice).toContainText('by sara', { timeout: 60_000 });
  await expect(notice).toContainText('MSG-');
}

test('readiness: the live service on the contract of this UI, with its demo clock', async ({ page }) => {
  const errors = await open(page, '/system');
  const kind = ready.mode === 'cached' ? 'cached' : ready.llm_mode === 'mock' ? 'live-cpu' : 'live';
  await expect(page.getByRole('region', { name: 'Service mode' })).toHaveAttribute('data-mode-banner', kind);
  await expect(page.locator('[data-contract-mismatch]')).toHaveCount(0);
  await expect(page.locator('[data-readiness-pill]')).toHaveAttribute('data-readiness-pill', ready.status);
  const clock = page.locator('[data-clock]');
  await expect(clock).toHaveAttribute('data-clock', ready.clock.kind);
  await expect(clock).toHaveAttribute('data-clock-frozen', ready.clock.frozen ? 'yes' : 'no');
  await expect(clock).toContainText(ready.clock.note);
  await expectClean(page, errors);
});

test('demo cases: the beats open the live ids of the reset, Marco still proposed', async ({ page }) => {
  const marco = await caseCard(id('marco'));
  expect(marco.decision.state, 'the walk needs a fresh reset: run python -m apps.api.reset').toBe('proposed');
  const errors = await open(page, '/demo');
  await expect(page.locator('[data-beat-id="marco"] [data-id]')).toHaveText(`/returns/${id('marco')}`);
  await expect(page.locator('[data-beat-id="elena"] [data-id]')).toHaveText(`/returns/${id('elena')}`);
  await expect(page.locator('[data-beat-id="shop_ring"] [data-after-marco="proposed"]')).toBeVisible();
  if (cases.dispute) {
    await expect(page.locator('[data-beat-id="dispute"] [data-dispute-changes]')).toHaveAttribute(
      'data-dispute-changes',
      cases.dispute.changes_decision ? 'yes' : 'no',
    );
  }
  await expectClean(page, errors);
});

test('queue: the open returns, Marco among the restrictions that need approval', async ({ page }) => {
  let errors = await open(page, '/queue');
  await expect(page.locator('[data-return-row]').first()).toBeVisible();
  await expectClean(page, errors);
  page.removeAllListeners('console');
  errors = await open(page, `/queue?tab=all&q=${caseOf(cases, 'marco').customer_id}`);
  const row = page.locator(`[data-return-row="${id('marco')}"]`);
  await expect(row.locator('[data-quadrant-badge="Q4"]')).toBeVisible();
  await expect(row).toContainText('Needs approval');
  await expectClean(page, errors);
});

test('Giulia (Q1): the upgrade is confirmed with a message and no legal clause', async ({ page }) => {
  const errors = await open(page, `/returns/${id('giulia')}`);
  await expect(page.locator('[data-quadrant-badge="Q1"]').first()).toBeVisible();
  await expectClean(page, errors);
  await confirmProposal(page, null);
  const after = await caseCard(id('giulia'));
  expect(after.decision.state).toBe('approved');
  expect(after.decision.approver).toBe('sara');
  expect(errors).toEqual([]);
});

test('Marco (Q4): approved by a person, with the review date, the coupon pause and the withdrawal clause', async ({ page }) => {
  const before = await caseCard(id('marco'));
  const reviewAt = before.decision.review_at;
  expect(reviewAt, 'a restriction carries its review date (rule 5)').not.toBeNull();
  const errors = await open(page, `/returns/${id('marco')}`);
  const gate = page.locator('[data-approval-gate]');
  await expect(gate.locator(`time[datetime="${reviewAt}"]`)).toBeVisible();
  await expectClean(page, errors);

  await page.locator('[data-action="approve-restriction"]').click();
  const sheet = page.locator('[data-sheet="approve"]');
  await expect(sheet).toBeVisible();
  await expect(sheet.locator(`time[datetime="${reviewAt}"]`).first()).toBeVisible();
  // Rule 3: the right of withdrawal stays, and the refund keeps its deadline.
  await expect(sheet.locator('[data-legal-ref]').first()).toBeVisible();
  await sheet.locator('summary', { hasText: /Preview/ }).first().click();
  await expect(sheet.locator('[data-message-preview] [data-legal-clause="withdrawal"]')).toBeVisible();
  // Rule 5: approving confirms the coupon pause of the linked accounts, acknowledged by the person.
  await expect(sheet.locator('[data-sheet-confirm="approve"]')).toBeDisabled();
  await sheet.locator('[data-coupon-ack-box]').check();
  await sheet.locator('[data-sheet-confirm="approve"]').click();
  await expect(sheet).toBeHidden({ timeout: 60_000 });
  const notice = page.locator('[data-action-notice]');
  await expect(notice).toContainText('Approved by sara');
  await expect(notice).toContainText('MSG-');
  await expect(notice.locator('[data-notice-coupon="confirmed"]')).toBeVisible();

  const after = await caseCard(id('marco'));
  expect(after.decision.state).toBe('approved');
  expect(after.decision.approver).toBe('sara');
  expect(after.decision.review_at).toBe(reviewAt);
  expect(after.decision.coupon_rule?.state).toBe('confirmed');
  walked.marcoApplication = after.decision.coupon_rule?.application_id;
  walked.marcoReviewAt = reviewAt ?? undefined;

  // The message in the outbox: three layers, the fixed withdrawal clause last (rule 10).
  page.removeAllListeners('console');
  const outboxErrors = await open(page, `/outbox?return_id=${id('marco')}`);
  const row = page.locator('[data-outbox-row]').first();
  await expect(row).toBeVisible();
  await row.locator('button').first().click();
  await expect(page.locator('[data-outbox-detail] [data-legal-clause="withdrawal"]')).toBeVisible();
  await expectClean(page, outboxErrors);
  expect(errors).toEqual([]);
});

test('shop: the ring cart shows the approved terms for future orders and the coupon pause; the order keeps them', async ({ page }) => {
  const cart = cases.carts.find((c) => c.key === 'ring');
  test.skip(!cart, 'the reset stored no ring cart');
  const errors = await open(page, '/shop?preset=ring');
  const shop = page.locator('[data-shop-pane]');
  const scenes = page.locator('[data-scenes-pane]');
  await expect(page.locator('[data-evaluation-status="ready"]')).toBeVisible({ timeout: 60_000 });
  await expect(page.locator('[data-presets] [aria-current="page"]')).toContainText(cart!.request.customer_id);
  // Rule 3: the right of withdrawal is stated on every order; the restriction holds until its review.
  await expect(shop).toContainText('You can always withdraw within 14 days of delivery, without giving a reason.');
  if (cart!.request.customer_id === caseOf(cases, 'marco').customer_id && walked.marcoReviewAt) {
    await expect(shop.locator(`[data-terms-until] time[datetime="${walked.marcoReviewAt}"]`)).toBeVisible();
  }
  const paused = shop.locator('[data-coupon-paused]');
  await expect(paused).toBeVisible();
  await expect(paused).toHaveAttribute('lang', 'it');
  for (const word of ['Q4', 'cluster', 'risk', 'model', 'same person']) await expect(shop).not.toContainText(word);
  await expect(scenes.locator('[data-coupon-pause]')).toContainText('confirmed by sara');
  await expectClean(page, errors);

  await shop.getByRole('button', { name: 'Place order' }).click();
  await expect(shop.locator('[data-order-placed]')).toContainText('ORD-', { timeout: 60_000 });
  await expect(scenes.locator('[data-snapshot-receipt="ok"]')).toBeVisible();
  await expectClean(page, errors);
});

test('Rossi: a family of linked accounts, never restricted', async ({ page }) => {
  for (const key of ['rossi', 'rossi_card'] as const) {
    const found = cases.cases.find((c) => c.key === key);
    if (!found) continue;
    page.removeAllListeners('console');
    const errors = await open(page, `/returns/${found.return_id}`);
    const card = await caseCard(found.return_id);
    expect(['Q3', 'Q4']).not.toContain(card.decision.quadrant);
    expect(card.decision.requires_approval).toBe(false);
    await expect(page.locator('[data-approval-gate]')).toHaveCount(0);
    await expect(page.getByText(/linked accounts/i).first()).toBeVisible();
    await expect(page.locator('body')).not.toContainText(/same person/i);
    await expectClean(page, errors);
  }
});

test('Elena: the guarantee path, no score and no refund deadline; her catalogue task changes no decision', async ({ page }) => {
  const card: CaseCard = await caseCard(id('elena'));
  expect(card.decision.refund_due_by).toBeNull();
  const errors = await open(page, `/returns/${id('elena')}`);
  await expect(page.locator('[data-guarantee-banner]')).toBeVisible();
  await expect(page.locator('[data-decision-rail]')).not.toContainText('Refund due by');
  await expectClean(page, errors);
  await confirmProposal(page, 'guarantee');
  const decided = await caseCard(id('elena'));
  expect(decided.decision.state).toBe('approved');

  // The card's anomaly opens the product page with her return, which a task may name.
  test.skip(!card.product_anomaly, 'no anomaly on the variant');
  await page.goto(`/returns/${id('elena')}`);
  await settle(page);
  await page.locator('[data-open-in-products]').click();
  await expect(page).toHaveURL(new RegExp(`\\?return=${id('elena')}$`));
  await switchPersona(page, 'luca');
  const form = page.locator('[data-catalog-task-form]');
  await expect(form.locator('[data-task-returns]')).toHaveValue(id('elena'));
  await form.getByLabel('Note (required)').fill('Check the size chart of the variant against the garment');
  await form.getByRole('button', { name: 'Open the task' }).click();
  const created = page.locator('[data-task-created]');
  await expect(created).toBeVisible({ timeout: 60_000 });
  const taskId = await created.getAttribute('data-task-created');
  const after = await caseCard(id('elena'));
  expect(after.product_anomaly?.catalog_task_id).toBe(taskId);
  // E2E-04: the task changes no decision, lever or message.
  expect({ ...after.decision, as_of: null }).toEqual({ ...decided.decision, as_of: null });
  await expectClean(page, errors);
  await switchPersona(page, 'sara');
});

test('insufficient evidence: no score, the missing evidence, the standard terms', async ({ page }) => {
  const card = await caseCard(id('insufficient'));
  const errors = await open(page, `/returns/${id('insufficient')}`);
  const panel = page.locator('[data-insufficient-panel]');
  await expect(panel).toBeVisible();
  for (const missing of card.evidence.missing_evidence.slice(0, 1)) await expect(panel).toContainText(missing);
  await expectClean(page, errors);
  // The standard terms restrict nothing: the message carries no legal clause.
  await confirmProposal(page, card.draft?.message.legal_clause_kind ?? null);
  const after = await caseCard(id('insufficient'));
  expect(after.decision.state).toBe('approved');
  expect(after.decision.levers).toEqual([]);
});

test('dispute: the link of the demo cases is recomputed; the decision changes only as announced', async ({ page }) => {
  const dispute = cases.dispute;
  test.skip(!dispute, 'the reset found no link to dispute');
  const errors = await open(page, `/returns/${dispute!.return_id}?link=${encodeURIComponent(dispute!.link_id)}`);
  await page.locator(`[data-dispute-link="${dispute!.link_id}"]`).first().click();
  const sheet = page.locator('[data-sheet="dispute"]');
  await sheet.getByLabel('Reason').fill('The accounts share a pickup point only, checked with the customer');
  await sheet.locator('[data-sheet-confirm="dispute"]').click();
  const comparison = page.locator('[data-recompute-comparison]');
  await expect(comparison).toBeVisible({ timeout: 90_000 });
  // "Decision unchanged" when the demo cases say the dispute changes nothing (ADR-15 section 18).
  await expect(comparison.locator('[data-decision-unchanged]')).toHaveAttribute(
    'data-decision-unchanged',
    dispute!.changes_decision ? 'false' : 'true',
  );
  await comparison.locator('[data-open-new-version]').click();
  const after = await caseCard(dispute!.return_id);
  expect(after.decision.version).toBeGreaterThan(1);
  expect(after.decision.supersedes).toBe(dispute!.decision_id);
  await expectClean(page, errors);
});

test('agent: the three suggested questions on the Marco case answer with the mock model', async ({ page }) => {
  test.skip(ready.mode === 'cached', 'the agent needs the live model (503 in the cached demo)');
  const errors = await open(page, `/returns/${id('marco')}`);
  await page.getByRole('button', { name: /Ask about this case/ }).click();
  const drawer = page.getByRole('dialog', { name: new RegExp(`Ask about ${id('marco')}`) });
  await expect(drawer.locator('[data-suggested]')).toHaveCount(3);
  await expect(drawer.locator('[data-suggested]').first()).toContainText(caseOf(cases, 'marco').customer_id);
  for (let i = 0; i < 3; i += 1) {
    const disclosure = drawer.getByRole('button', { name: 'Suggested questions' });
    if ((await disclosure.count()) > 0 && (await disclosure.getAttribute('aria-expanded')) === 'false') await disclosure.click();
    await drawer.locator('[data-suggested]').nth(i).click();
    await expect(drawer.locator('article[data-turn="done"]')).toHaveCount(i + 1, { timeout: 90_000 });
  }
  await expect(drawer.locator('[data-agent-stream="error"]')).toHaveCount(0);
  await expect(drawer).not.toContainText(/same person/i);
  await expectClean(page, errors);
});

test("manager: the matrix, the simulator's 368 €, KPIs, fairness, the audit of Marco's approval, reviews and coupons", async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('returnring.persona', 'luca'));
  const steps: [string, (p: Page) => Promise<void>][] = [
    ['/portfolio', async (p) => void (await expect(p.locator('[data-quadrant-tile]')).toHaveCount(4))],
    [
      '/simulator',
      async (p) => {
        await expect(p.locator('[data-shown-scenario] [data-net-total]')).toContainText('+€368.00');
        await expect(p.locator('[data-shown-scenario]')).toContainText('The refund owed to the customer is never counted as a saving.');
      },
    ],
    ['/kpi', async (p) => void (await expect(p.locator('[data-report-unavailable]')).toHaveCount(0))],
    ['/fairness', async (p) => void (await expect(p.locator('[data-report-unavailable]')).toHaveCount(0))],
    [
      `/audit?return_id=${id('marco')}`,
      async (p) => {
        const approval = p.locator('[data-audit-row]', { has: p.locator('[data-human-approval]') }).first();
        await expect(approval).toBeVisible();
        if (walked.marcoReviewAt) await expect(approval.locator(`time[datetime="${walked.marcoReviewAt}"]`)).toBeVisible();
        await expect(approval).toContainText('sara');
      },
    ],
    ['/reviews', async (p) => void (await expect(p.locator('[data-coupon-applications]')).toBeVisible())],
    [
      '/coupons',
      async (p) => {
        test.skip(!walked.marcoApplication, 'no coupon application from the Marco step');
        const row = p.locator(`[data-application="${walked.marcoApplication}"]`);
        await expect(row.locator('[data-application-state="confirmed"]')).toBeVisible();
        await expect(row.getByRole('button', { name: /^Reverse/ })).toBeEnabled();
      },
    ],
  ];
  for (const [path, check] of steps) {
    await test.step(path, async () => {
      page.removeAllListeners('console');
      page.removeAllListeners('pageerror');
      const errors = await open(page, path);
      await check(page);
      await expectClean(page, errors);
    });
  }
  // The outbox holds the messages of the walk's approvals.
  const outbox = await apiGet<{ total: number }>(`/api/outbox?return_id=${id('marco')}`, 'luca');
  expect(outbox.total).toBeGreaterThan(0);
});
