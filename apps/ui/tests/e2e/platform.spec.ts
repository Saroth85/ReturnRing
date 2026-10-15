// Platform area in mock mode (docs/ui-design.md 9.1, 9.2, 9.4 and 14.2: E2E-02, PROV-UI, MODE-UI
// for these screens). Every screen renders with no console error, no number without provenance,
// no banned wording and no horizontal page scroll; the checkout binds the shown snapshot, the
// inspection runs from a photo to a confirmed claim, the system page shows B1..B4 and telemetry.
// PLATFORM_SCREENSHOT_DIR=<dir> saves each screen at 1440 × 900 and 390 × 844.
import { mkdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect as baseExpect, test, type Page } from '@playwright/test';
import { scanPage } from './scan';

// A ceiling for a loaded machine; assertions return as soon as they hold.
const expect = baseExpect.configure({ timeout: 15_000 });

const SHOTS = process.env.PLATFORM_SCREENSHOT_DIR;
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

const here = path.dirname(fileURLToPath(import.meta.url));
const EXAMPLES = path.resolve(here, '../../../../contracts/examples/api');

interface DemoCases {
  cases: { key: string; return_id: string; customer_id: string; cluster_id: string | null }[];
  dispute: { return_id: string; link_id: string; changes_decision: boolean } | null;
  carts: { key: string; request: { customer_id: string } }[];
}
const example = <T,>(name: string): T => JSON.parse(readFileSync(path.join(EXAMPLES, name), 'utf8')) as T;
/** The golden cases the mock serves (GET /api/demo/cases): the UI reads its ids from there. */
const CASES = example<DemoCases>('demo_cases.json');
const caseId = (key: string) => CASES.cases.find((c) => c.key === key)?.return_id ?? '';
interface Evaluation {
  lines: { size_advice: { kind: string; text: { it: string; en: string } } | null }[];
  coupon_block: { text: { it: string; en: string } } | null;
}
const GIULIA = example<Evaluation>('checkout_evaluate_response.giulia.json');
const RING = example<Evaluation>('checkout_evaluate_response.marco.json');

/**
 * The photo of an inspection request example: the mock routes on it (docs/ui-design.md 11.5). The
 * default example's photo reads as damaged, the manual review one's as unclear (contract 1.6.0).
 */
function examplePhoto(example = 'inspection_create_request.json'): Buffer {
  const request = JSON.parse(readFileSync(path.join(EXAMPLES, example), 'utf8')) as {
    photo: { data_base64: string };
  };
  return Buffer.from(request.photo.data_base64, 'base64');
}

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

async function expectClean(page: Page, errors: string[]): Promise<void> {
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
  const result = await scan(page);
  expect(result.unprovenanced, 'numbers without provenance').toEqual([]);
  expect(result.banned, 'banned wording').toEqual([]);
  expect(errors, 'console errors').toEqual([]);
}

/** Desktop and phone screenshots, with no horizontal page scroll at 390 px. */
async function shoot(page: Page, name: string): Promise<void> {
  const overflow = async () => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}-1440x900.png`), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(250);
  expect(await overflow(), 'no horizontal page scroll at 390 px').toBeLessThanOrEqual(0);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}-390x844.png`), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });
}

async function actAs(page: Page, persona: 'sara' | 'luca' | 'ahmed'): Promise<void> {
  await page.addInitScript((id) => window.localStorage.setItem('returnring.persona', id), persona);
}

test.describe('simulated checkout (US-401..403)', () => {
  test('Giulia: the measurement guide without a probability, upgraded terms, order bound to the shown snapshot (E2E-02)', async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto('/shop?preset=giulia');
    const shop = page.locator('[data-shop-pane]');
    const scenes = page.locator('[data-scenes-pane]');
    await expect(page.locator('[data-evaluation-status="ready"]')).toBeVisible();
    // Contract 1.6.0: no size is suggested; both sizes get the measurement guide, in Italian by default.
    await expect(shop.locator('[data-size-advice="measurement_guide"]')).toHaveCount(2);
    await expect(shop.locator('[data-size-advice] [lang="it"]').first()).toHaveText(GIULIA.lines[0]!.size_advice!.text.it);
    await expect(shop.getByRole('button', { name: /^Switch to/ })).toHaveCount(0);
    await expect(shop).not.toContainText('0.47');
    await expect(shop).toContainText('60-day return window');
    await expect(shop).toContainText('You can always withdraw within 14 days of delivery, without giving a reason.');
    await expect(scenes.locator('[data-line-advice="above"]')).toHaveCount(2);
    await expect(scenes.locator('[data-line-advice="above"]').first()).toContainText('0.47');
    await expect(scenes.locator('[data-latency] [data-figure][data-nature="real"]')).toBeVisible();
    await expectClean(page, errors);
    await shoot(page, 'shop-giulia');

    await page.getByRole('button', { name: 'English' }).click();
    await expect(shop.locator('[data-size-advice] [lang="en"]').first()).toHaveText(GIULIA.lines[0]!.size_advice!.text.en);

    await shop.getByRole('button', { name: 'Place order' }).click();
    await expect(shop.locator('[data-order-placed]')).toContainText('Order ORD-CHK-000001 placed. The terms above are saved with your order.');
    await expect(scenes.locator('[data-snapshot-receipt="ok"]')).toContainText('The terms shown are the terms stored (TS-CHK-000981).');
    await expectClean(page, errors);
    await shoot(page, 'shop-giulia-ordered');
  });

  test('the account of the Marco case (?preset=marco): terms for future orders with their review date, the coupon pause in the API words (US-402)', async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto('/shop?preset=marco');
    const shop = page.locator('[data-shop-pane]');
    const scenes = page.locator('[data-scenes-pane]');
    await expect(page.locator('[data-evaluation-status="ready"]')).toBeVisible();
    await expect(page.locator('[data-presets] [aria-current="page"]')).toContainText(CASES.carts.find((c) => c.key === 'ring')!.request.customer_id);
    await expect(shop.locator('[data-terms-until]')).toHaveText('These return terms apply to your account until 27 Dec 2026, when they are reviewed.');
    await expect(shop.locator('[data-coupon-paused]')).toHaveText(RING.coupon_block!.text.it);
    for (const word of ['Q4', 'cluster', 'linked', 'risk', 'model']) await expect(shop).not.toContainText(word);
    const pause = scenes.locator('[data-coupon-pause]');
    await expect(pause).toContainText('confirmed by sara');
    await expect(pause).toContainText('coupon-rule-v1');
    await expect(pause).toContainText('Reversible at any time');
    await expect(pause.locator('[data-coupon-text]')).toContainText('every other linked account reads a neutral text');
    await expect(scenes.locator('[data-term-row="return_window_days"]')).toContainText('legal minimum');
    await expect(scenes.locator('[data-term-row="coupons_enabled"]')).toContainText('less generous');
    await expectClean(page, errors);
    await shoot(page, 'shop-marco');
  });

  test('a lapsed snapshot (409) evaluates the cart again; the public limit (429) waits for Retry-After', async ({ page }) => {
    await page.goto('/shop?preset=ring&scenario=conflict');
    const shop = page.locator('[data-shop-pane]');
    await expect(page.locator('[data-evaluation-status="ready"]')).toBeVisible();
    await shop.getByRole('button', { name: 'Place order' }).click();
    await expect(shop.locator('[data-checkout-notice="terms_changed"]')).toContainText('can no longer be confirmed');
    await expect(page.locator('[data-evaluation-status="ready"]')).toBeVisible();
    await expect(shop.locator('[data-order-placed]')).toHaveCount(0);

    await page.goto('/shop?preset=giulia&scenario=rate_limited');
    await expect(page.locator('[data-checkout-notice="rate_limited"]')).toContainText(/the terms are checked again in \d+ s/);
    await expect(page.locator('[data-evaluation-status="waiting"]')).toBeVisible();
  });

  test('the read-only public demo shows the terms but places no order', async ({ page }) => {
    await page.goto('/shop?preset=giulia&scenario=read_only');
    const shop = page.locator('[data-shop-pane]');
    await expect(page.locator('[data-evaluation-status="ready"]')).toBeVisible();
    await expect(shop.getByRole('button', { name: 'Place order' })).toBeDisabled();
    await expect(shop.locator('[data-role-reason]')).toHaveText('Read-only public demo');
  });

  test('before a demo reset (404) the shop says to run it, in the API words', async ({ page }) => {
    await page.goto('/shop?preset=giulia&scenario=empty');
    await expect(page.locator('[data-no-demo-reset]')).toContainText('no demo reset yet: run python -m apps.api.reset');
  });

  test('a second evaluation of the same cart answers within the US-403 target', async ({ page }) => {
    await page.goto('/shop?preset=giulia');
    await expect(page.locator('[data-evaluation-status="ready"]')).toBeVisible();
    await page.getByLabel('Coupon code').fill('SPRING');
    await expect(page.locator('[data-evaluation-status="evaluating"]')).toBeVisible();
    await expect(page.locator('[data-evaluation-status="ready"]')).toBeVisible();
    await expect(page.locator('[data-latency]')).toHaveAttribute('data-latency', 'within');
  });
});

test.describe('warehouse inspection (US-501..503)', () => {
  test('the list of withdrawal-path returns, for the warehouse', async ({ page }) => {
    const errors = watchConsole(page);
    await actAs(page, 'ahmed');
    await page.goto('/inspection');
    await expect(page.locator('[data-inspection-row="RET-000110"]')).toBeVisible();
    await expect(page.locator(`[data-inspection-row="${caseId('elena')}"]`)).toHaveCount(0);
    await expectClean(page, errors);
    await shoot(page, 'inspection-list');
  });

  test('Valentina: photo, checklist answered by Ahmed, proposal applied, claim confirmed with Ctrl+Enter', async ({ page }) => {
    const errors = watchConsole(page);
    await actAs(page, 'ahmed');
    await page.goto('/inspection/RET-000110');
    await expect(page.getByText('Valentina Greco')).toBeVisible();
    await expectClean(page, errors);
    await shoot(page, 'inspection-detail');

    await page.locator('[data-photo-input]').setInputFiles({ name: 'item.jpg', mimeType: 'image/jpeg', buffer: examplePhoto() });
    await page.getByRole('button', { name: 'Suggest a checklist from the photo' }).click();
    const tag = page.locator('[data-checklist-row="tag_attached"]');
    await expect(tag).toContainText('Suggested: no · No tag visible');
    await expect(tag.locator('[data-confidence]')).toHaveText('model confidence 0.78 · declared by the model, not calibrated');
    await expect(page.locator('[data-checklist-row][data-answer="none"]')).toHaveCount(3);
    await expectClean(page, errors);
    await shoot(page, 'inspection-checklist');

    // Keys on the focused row: Y yes, N no, U not observable.
    await tag.getByLabel('Yes').focus();
    await page.keyboard.press('n');
    await expect(tag).toHaveAttribute('data-answer', 'no');
    await page.locator('[data-checklist-row="visible_damage"]').getByText('Yes', { exact: true }).click();
    await page.locator('[data-checklist-row="photo_quality"]').getByText('Yes', { exact: true }).click();
    const apply = page.locator('[data-apply-compensation]');
    await expect(apply).not.toBeChecked();
    await apply.check();
    await expectClean(page, errors);
    await shoot(page, 'inspection-proposal');

    await page.locator('[data-checklist-row="photo_quality"] input').first().focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('[data-inspection-result]')).toHaveCount(0);
    await page.keyboard.press('Control+Enter');
    const result = page.locator('[data-inspection-result="claim"]');
    await expect(result.locator('[data-claim]')).toContainText('€17.98');
    await expect(result.locator('[data-claim]')).toContainText('confirmed by ahmed');
    // The claim is on the price paid for the line; the message states the refund left and the deduction.
    await expect(result.locator('[data-line-price]')).toContainText('€89.90');
    await expect(result.locator('[data-template-field="amount"]')).toContainText('71,92 €');
    await expect(result.locator('[data-template-field="deduction"]')).toContainText('17,98 €');
    // The refund the message states, with its provenance (OutboxMessage.refund, rule 11).
    await expect(result.locator('[data-refund-stated] [data-figure]')).toContainText('€71.92');
    await expectClean(page, errors);
    await shoot(page, 'inspection-confirmed');
  });

  test('Simone: the system stops on a dark photo, no proposal, observations only', async ({ page }) => {
    const errors = watchConsole(page);
    await actAs(page, 'ahmed');
    await page.goto('/inspection/RET-000111');
    await expect(page.getByText('Simone Rizzo')).toBeVisible();
    await page
      .locator('[data-photo-input]')
      .setInputFiles({ name: 'dark.jpg', mimeType: 'image/jpeg', buffer: examplePhoto('inspection_create_request.manual_review.json') });
    await page.getByRole('button', { name: 'Suggest a checklist from the photo' }).click();
    await expect(page.locator('[data-manual-review]')).toContainText('The system stopped: verify by hand.');
    await expect(page.locator('[data-proposal="none"]')).toBeVisible();
    await expect(page.locator('[data-apply-compensation]')).toHaveCount(0);
    await expectClean(page, errors);
    await shoot(page, 'inspection-manual-review');
  });

  test('the operator sees the steps read-only with the reason', async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto('/inspection/RET-000110');
    await expect(page.getByRole('button', { name: 'Suggest a checklist from the photo' })).toBeDisabled();
    await expect(page.getByText('This action is for warehouses (operator can view).').first()).toBeVisible();
    await expectClean(page, errors);
  });
});

test.describe('system and benchmarks (US-901, US-902, US-905)', () => {
  test('B1..B4 with their status, the cost per decision, readiness and telemetry', async ({ page }) => {
    const errors = watchConsole(page);
    await actAs(page, 'luca');
    await page.goto('/system');
    await expect(page.locator('[data-benchmark]')).toHaveCount(4);
    await expect(page.locator('[data-benchmark="B1"] [data-benchmark-status]')).toHaveText('Illustrative, not measured');
    await expect(page.locator('[data-benchmark="B2"] [data-benchmark-status]')).toHaveText('Pending');
    await expect(page.locator('[data-cost-per-decision]')).toContainText('€0.01');
    await expect(page.locator('[data-cost-per-decision]')).toContainText('idle included');
    await expect(page.locator('[data-speedups]')).toHaveCount(0);
    // On the mock adapter every fact is labelled as the /readyz contract example (UI12).
    await expect(page.locator('[data-mode-facts]')).toContainText('ok · the mock adapter answers (no API)');
    await expect(page.locator('[data-readiness-pill]')).toHaveAttribute('data-readiness-pill', 'contract');
    await expect(page.locator('[data-header-agreement="agrees"]')).toBeVisible();
    await expect(page.locator('#telemetry [data-telemetry-illustrative]')).toBeVisible();
    await expect(page.locator('#telemetry')).toContainText('1,450 tok/s');
    await page.locator('[data-benchmark="B3"] summary').click();
    await expect(page.locator('[data-benchmark="B3"] [data-benchmark-details]')).toContainText('google/gemma-4-31b-it');
    await expectClean(page, errors);
    await shoot(page, 'system');
  });

  test('cached: the label of /readyz, the frozen clock, the GPU off and no telemetry numbers (MODE-UI)', async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto('/system?scenario=cached');
    await expect(page).toHaveTitle(/^\[Cached\]/);
    await expect(page.locator('[data-mode-facts] [data-mode]')).toHaveText('contract example: Cached');
    // The cached label is readyz.label's claim alone (ADR-15 sections 16, 17).
    await expect(page.locator('[data-mode-facts] [data-cached-label]')).toHaveText('Precomputed on AMD MI300X');
    await expect(page.locator('[data-clock-frozen="yes"] [data-clock-state]')).toHaveText('frozen at the snapshot instant: it does not move');
    await expect(page.locator('[data-component="vllm"]')).toContainText('GPU off in cached mode');
    await expect(page.locator('#telemetry [data-telemetry-cached]')).toContainText('GPU off: cached mode serves precomputed results');
    await expect(page.locator('#telemetry [data-figure]')).toHaveCount(0);
    await expect(page.locator('#telemetry [data-wall-time]')).toBeVisible();
    await expectClean(page, errors);
    await shoot(page, 'system-cached');
  });

  test('the GPU dock opens with T and shows utilisation and tokens per second', async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto('/system');
    await expect(page.locator('[data-benchmark]')).toHaveCount(4);
    await page.keyboard.press('t');
    const dock = page.getByRole('complementary', { name: 'GPU' });
    await expect(dock).toContainText('71%');
    await expect(dock).toContainText('tok/s');
    await expectClean(page, errors);
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'gpu-dock-1440x900.png') });
    await page.keyboard.press('t');
    await expect(dock).toHaveCount(0);
  });
});

test.describe('demo path and coupon rules (US-903, US-402)', () => {
  test('the preflight checks the run on the contract examples and a beat opens with its persona', async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto('/demo');
    // On contract examples the adapter line is red with its fix (UI12): never 'Ready for the pitch'.
    await expect(page.locator('[data-preflight="adapter"]')).toHaveAttribute('data-state', 'fail');
    await expect(page.locator('[data-preflight-state]')).toHaveAttribute('data-preflight-state', 'fail');
    // The load time is measured for real: a cold server may miss the target, the rest is green.
    for (const id of ['readiness', 'components', 'contract', 'cases', 'marco', 'rossi', 'assumptions']) {
      await expect(page.locator(`[data-preflight="${id}"]`)).toHaveAttribute('data-state', 'pass');
    }
    await expect(page.locator('[data-preflight="load"] [data-figure][data-nature="real"]')).toBeVisible();
    // The beats open the cases of GET /api/demo/cases; the ring checkout comes after Marco.
    await expect(page.locator('[data-beat-id="marco"]')).toContainText(`/returns/${caseId('marco')}`);
    await expect(page.locator('[data-beat-id="shop_ring"] [data-after-marco]')).toHaveAttribute('data-after-marco', 'proposed');
    await expect(page.locator('[data-beat-id="dispute"] a').first()).toHaveAttribute(
      'href',
      `/returns/${CASES.dispute!.return_id}?link=${encodeURIComponent(CASES.dispute!.link_id)}`,
    );
    await expectClean(page, errors);
    await shoot(page, 'demo');
    await page.locator('[data-beat="4"]').getByRole('link', { name: 'Open', exact: true }).click();
    await expect(page).toHaveURL(/\/simulator$/);
    await expect(page.getByRole('combobox', { name: 'Acting as' })).toHaveValue('luca');
  });

  test('before a demo reset (404) the demo page quotes the API and keeps the beats without a case', async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto('/demo?scenario=empty');
    await expect(page.locator('[data-no-demo-reset]')).toContainText('no demo reset yet: run python -m apps.api.reset');
    await expect(page.locator('[data-preflight="cases"]')).toHaveAttribute('data-state', 'fail');
    await expect(page.locator('[data-beat-id]')).toHaveCount(2);
    await expectClean(page, errors);
  });

  test('presenter mode shows the beat rail, and Alt+→ moves to the next beat', async ({ page }) => {
    const compare = `/compare?a=${caseId('giulia')}&b=${caseId('marco')}`;
    await page.goto(`${compare}&present=1`);
    const rail = page.getByRole('navigation', { name: 'Demo beats' });
    await expect(rail.locator('[aria-current="step"]')).toContainText('Same signal');
    await page.keyboard.press('Alt+ArrowRight');
    await expect(page).toHaveURL(new RegExp(`/returns/${caseId('marco')}$`));
    await expect(rail.locator('[aria-current="step"]')).toContainText('Marco');
  });

  test('a manager approves a new coupon rule version; the applications keep their reversal', async ({ page }) => {
    const errors = watchConsole(page);
    await actAs(page, 'luca');
    await page.goto('/coupons');
    await expect(page.locator('[data-current-rule="coupon-rule-v1"]')).toBeVisible();
    await expect(page.locator('[data-application-state]')).toHaveCount(2);
    await expectClean(page, errors);
    await shoot(page, 'coupons');
    await page.getByRole('button', { name: 'Start from the current rule' }).click();
    await page.getByRole('textbox', { name: 'Rule text' }).press('Control+Enter');
    await expect(page.locator('[data-rule-created="coupon-rule-v2"]')).toBeVisible();
    await expect(page.locator('[data-current-rule="coupon-rule-v2"]')).toBeVisible();
  });
});
