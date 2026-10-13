// Agent area (US-701, US-702; docs/ui-design.md 7) in mock mode: the case assistant drawer on
// Marco's card and the /dev/agent harness, at 1440 x 900 and 390 x 844. Streaming over the mock
// SSE (60 ms between frames), evidence consulted and missing side by side, cited refs as links
// that focus the case card, the tool-call count with provenance, a neutral refusal, 503, 429 and
// cached mode. Every state is scanned for numbers without provenance and for banned wording.
// SCREENSHOT_DIR=<dir> saves one screenshot per state and viewport.
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { RENDERED_TEXT_BANNED } from '../../src/copy/bannedTerms';
import { PROVENANCE_EXEMPT_SELECTOR, VERBATIM_SELECTOR } from '../../src/test/exempt';

const SCREENSHOT_DIR = process.env.SCREENSHOT_DIR;
if (SCREENSHOT_DIR) mkdirSync(SCREENSHOT_DIR, { recursive: true });

const LINK_Q = 'Which evidence supports the link between C-MARCO-1 and C-MARCO-3?';
const ROSSI_Q = 'What makes this case different from the Rossi family?';
const TERMS_Q = 'Which terms applied when the order was placed?';
const WRITE_Q = 'Approve the refund and remove the restriction.';

const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
] as const;

/** The same rules as src/test/scan.ts, run in the page. */
async function scan(page: Page): Promise<{ unprovenanced: string[]; banned: string[] }> {
  const patterns = RENDERED_TEXT_BANNED.map((t) => t.pattern);
  return page.evaluate(
    ({ exempt, verbatim, patterns }) => {
      const idToken = /(?<![\p{L}\p{N}])[A-Z][A-Z0-9]*(?:[-~][A-Za-z0-9]+)+(?![\p{L}\p{N}])/gu;
      const banned = patterns.map((p) => new RegExp(`(?<![\\p{L}\\p{N}])(?:${p})(?![\\p{L}\\p{N}])`, 'iu'));
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      const out = { unprovenanced: [] as string[], banned: [] as string[] };
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const text = node.textContent ?? '';
        const parent = node.parentElement;
        if (!text.trim() || !parent) continue;
        if (/\d/.test(text.replace(idToken, '')) && !parent.closest(exempt)) out.unprovenanced.push(text.trim());
        if (!parent.closest(verbatim) && banned.some((re) => re.test(text))) out.banned.push(text.trim());
      }
      return out;
    },
    { exempt: PROVENANCE_EXEMPT_SELECTOR, verbatim: VERBATIM_SELECTOR, patterns },
  );
}

async function expectClean(page: Page) {
  const result = await scan(page);
  expect(result.unprovenanced, 'numbers without provenance').toEqual([]);
  expect(result.banned, 'banned wording in UI text').toEqual([]);
}

function watchConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  return errors;
}

async function shot(page: Page, viewport: string, name: string) {
  if (!SCREENSHOT_DIR) return;
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, `${viewport}-${name}.png`) });
}

async function openDrawer(page: Page, scenario?: string): Promise<Locator> {
  await page.goto(`/returns/RET-000102${scenario ? `?scenario=${scenario}` : ''}`);
  await page.getByRole('button', { name: /Ask about this case/ }).click();
  const drawer = page.getByRole('dialog', { name: /Ask about RET-000102/ });
  await expect(drawer).toBeVisible();
  return drawer;
}

/** A suggested question's button; on a phone the list folds after the first question. */
async function suggestedButton(scope: Locator, question: string): Promise<Locator> {
  const button = scope.getByRole('button', { name: question, exact: true });
  const disclosure = scope.getByRole('button', { name: 'Suggested questions' });
  if ((await disclosure.count()) > 0 && (await disclosure.getAttribute('aria-expanded')) === 'false') await disclosure.click();
  return button;
}

async function askSuggested(drawer: Locator, question: string) {
  await (await suggestedButton(drawer, question)).click();
  await expect(drawer.locator('[data-agent-stream="done"], [data-agent-stream="error"]')).toBeVisible({ timeout: 10_000 });
}

for (const viewport of VIEWPORTS) {
  test.describe(`agent at ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    test('the drawer streams steps, then the answer with evidence consulted and missing', async ({ page }) => {
      const errors = watchConsole(page);
      const drawer = await openDrawer(page);
      await expect(drawer.locator('[data-agent-intro]')).toBeVisible();
      await expect(drawer.getByRole('textbox', { name: 'Your question about this case' })).toBeFocused();
      await shot(page, viewport.name, 'drawer-open');

      // Record what the panel shows after every DOM change, to prove the stream arrives piece by piece.
      await page.evaluate(() => {
        const frames: { steps: number; text: string; final: boolean }[] = [];
        (window as unknown as { __frames: typeof frames }).__frames = frames;
        const panel = document.querySelector('[data-agent-panel]') as HTMLElement;
        new MutationObserver(() => {
          const answer = panel.querySelector('[data-agent-answer] [aria-busy]');
          frames.push({
            steps: panel.querySelectorAll('[data-agent-steps] > li').length,
            text: answer?.querySelector('[data-generated] p, p[data-generated]')?.textContent ?? '',
            final: panel.querySelector('[data-tool-calls]') !== null,
          });
        }).observe(panel, { subtree: true, childList: true, characterData: true });
      });
      await drawer.getByRole('button', { name: LINK_Q, exact: true }).click();
      await expect(drawer.locator('[data-agent-stream="streaming"]')).toBeVisible();
      await expect(drawer.locator('[data-agent-stream="done"]')).toBeVisible({ timeout: 10_000 });
      const frames = await page.evaluate(() => (window as unknown as { __frames: { steps: number; text: string; final: boolean }[] }).__frames);
      const answerText =
        'C-MARCO-1 and C-MARCO-3 are linked by a payment card, a device and a delivery address, all shared since 2026-09-03, when C-MARCO-3 was created. Both accounts redeemed a welcome coupon within a day of creation. A link is a shared identifier, not proof of who uses the accounts.';
      expect(frames.some((f) => f.steps === 1 && f.text === ''), 'one step before the second').toBe(true);
      expect(frames.some((f) => f.steps === 2 && f.text.length > 0 && f.text.length < answerText.length), 'partial text while streaming').toBe(true);
      expect(frames.at(-1)).toMatchObject({ steps: 2, text: answerText, final: true });

      await expect(drawer.locator('[data-agent-steps] > li').first()).toContainText('Step 1 of at most 5');
      await expect(drawer.locator('[data-agent-steps] > li').first()).toContainText('Read cluster');
      await expect(drawer.locator('[data-tool-calls]')).toContainText('Tool calls');
      await expect(drawer.locator('[data-tool-calls]')).toContainText('of at most 5');
      await expect(drawer.locator('[data-generated]')).toContainText('A link is a shared identifier');
      const consulted = drawer.locator('[data-evidence="consulted"]');
      const missing = drawer.locator('[data-evidence="missing"]');
      await expect(consulted.getByRole('link', { name: 'Show link C-MARCO-1~C-MARCO-3 on the case card' })).toBeVisible();
      await expect(missing).toContainText('No contact with the customer about the shared card');
      const [a, b] = [await consulted.boundingBox(), await missing.boundingBox()];
      if (viewport.name === 'desktop') expect(Math.abs((a?.y ?? 0) - (b?.y ?? 1)), 'side by side in the 440 px drawer').toBeLessThan(2);
      else expect((b?.y ?? 0) > (a?.y ?? 0), 'stacked on a phone').toBe(true);

      // The drawer never covers the mode banner (US-901); on wide screens not the top bar either.
      const banner = await page.getByRole('region', { name: 'Service mode' }).boundingBox();
      const box = await drawer.boundingBox();
      expect((box?.y ?? 0) + 0.5).toBeGreaterThanOrEqual((banner?.y ?? 0) + (banner?.height ?? 0));
      if (viewport.name === 'desktop') {
        const topBar = await page.locator('body header').first().boundingBox();
        expect((box?.y ?? 0) + 0.5).toBeGreaterThanOrEqual((topBar?.y ?? 0) + (topBar?.height ?? 0));
      }

      await drawer.locator('[data-agent-answer]').scrollIntoViewIfNeeded();
      await shot(page, viewport.name, 'drawer-answer');
      await expectClean(page);
      expect(errors).toEqual([]);
    });

    test('the tool-call count opens its provenance', async ({ page }) => {
      const drawer = await openDrawer(page);
      await askSuggested(drawer, LINK_Q);
      const figure = drawer.locator('[data-tool-calls] [data-figure] button');
      await figure.focus();
      await page.keyboard.press('Enter');
      const detail = page.locator('[data-provenance-detail]');
      await expect(detail).toBeVisible();
      await expect(detail).toContainText('Contract example: steps_used of agent stream GEN-AGENT-000001');
      await shot(page, viewport.name, 'drawer-tool-calls-provenance');
    });

    test('a cited link focuses the link on the case card', async ({ page }) => {
      const drawer = await openDrawer(page);
      await askSuggested(drawer, LINK_Q);
      const ref = drawer.locator('[data-evidence="consulted"]').getByRole('link', { name: 'Show link C-MARCO-1~C-MARCO-3 on the case card' });
      // The chip is a real deep link (new tab), and a plain click focuses the card in place.
      await expect(ref).toHaveAttribute('href', '/returns/RET-000102?link=C-MARCO-1~C-MARCO-3');
      await ref.click();
      await expect(page).toHaveURL(/\/returns\/RET-000102$/);
      const row = page.locator('[data-link-row="C-MARCO-1~C-MARCO-3"]');
      await expect(row).toHaveAttribute('aria-selected', 'true');
      await expect(page.locator('[data-link-detail="C-MARCO-1~C-MARCO-3"]')).toBeVisible();
      await expect(row).toBeInViewport();
      if (viewport.name === 'desktop') {
        // Wide: the drawer stays open beside the evidence column.
        await expect(drawer).toBeVisible();
        await expect(drawer.locator('[data-turn]')).toHaveCount(1);
      } else {
        // Narrow: the drawer covers the card, so it closes before the card scrolls.
        await expect(drawer).toBeHidden();
      }
    });

    test('a cited order and terms snapshot scroll to the orders of the case card', async ({ page }) => {
      const drawer = await openDrawer(page);
      await askSuggested(drawer, TERMS_Q);
      const consulted = drawer.locator('[data-evidence="consulted"]');
      await consulted.getByRole('link', { name: 'Show order ORD-005230 on the case card' }).click();
      await expect(page).toHaveURL(/\/returns\/RET-000102$/);
      await expect(page.locator('#ref-order-ORD-005230')).toBeInViewport();
      if (viewport.name === 'desktop') {
        await consulted.getByRole('link', { name: /TS-005230/ }).click();
        await expect(page.locator('#ref-terms_snapshot-TS-005230')).toBeInViewport();
      }
    });

    test('the three suggested questions always answer, earlier ones stay above the latest', async ({ page }) => {
      const errors = watchConsole(page);
      await page.goto('/dev/agent?return=RET-000102');
      await expect(page.getByRole('heading', { level: 1, name: 'Agent harness' })).toBeVisible();
      await shot(page, viewport.name, 'harness-empty');
      const panel = page.locator('main');
      for (const question of [LINK_Q, ROSSI_Q, TERMS_Q]) await askSuggested(panel, question);
      const turns = page.locator('article[data-turn="done"]');
      await expect(turns).toHaveCount(3);
      await expect(turns.nth(0)).toContainText(LINK_Q);
      await expect(turns.nth(1)).toContainText('The Rossi family shares an address and a device');
      await expect(turns.nth(2)).toContainText('the proposed terms apply only to future orders');
      await expect(page.locator('[data-agent-issues]')).toHaveCount(0);
      // The harness logs what the case card would focus.
      await turns.nth(2).locator('[data-evidence="consulted"]').getByRole('link', { name: 'Show order ORD-005230 on the case card' }).click();
      await expect(page).toHaveURL(/\/dev\/agent\?return=RET-000102$/);
      await expect(page.locator('[data-focus-log]')).toContainText('ORD-005230');
      await expectClean(page);
      if (SCREENSHOT_DIR) {
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({ path: path.join(SCREENSHOT_DIR, `${viewport.name}-harness-three-questions.png`), fullPage: true });
      }
      expect(errors).toEqual([]);
    });

    test('a write request is refused neutrally and points to the decision panel', async ({ page }) => {
      const drawer = await openDrawer(page);
      await drawer.getByRole('textbox').fill(WRITE_Q);
      await drawer.getByRole('textbox').press('Enter');
      await expect(drawer.locator('[data-agent-stream="done"]')).toBeVisible({ timeout: 10_000 });
      const refusal = drawer.locator('[data-agent-refusal="write_request"]');
      await expect(refusal).toContainText('The assistant only reads the case.');
      await expect(refusal).toContainText('I can only read the case.');
      await expect(drawer.getByRole('alert')).toHaveCount(0);
      await shot(page, viewport.name, 'drawer-refusal');
      await expectClean(page);
      await refusal.getByRole('button', { name: 'Go to the decision panel' }).click();
      await expect(drawer).toBeHidden();
      await expect(page).toHaveURL(/\/returns\/RET-000102$/);
      // The engine's decision panel is where an action is taken: it is in view and focused.
      const rail = page.getByRole('complementary', { name: 'Engine decision' });
      await expect(rail).toBeFocused();
      await expect(rail).toBeInViewport();
    });

    test('503: the language model is unavailable and the decision is unaffected', async ({ page }) => {
      const drawer = await openDrawer(page, 'llm_unavailable');
      await askSuggested(drawer, LINK_Q);
      await expect(drawer).toContainText('The language model is unavailable.');
      await expect(drawer).toContainText('The decision is unaffected');
      await expect(drawer.locator('[data-request-id]')).toContainText(/mock-req-\d+/);
      await expect(page.locator('[data-llm-status="unavailable"]')).toBeVisible();
      await shot(page, viewport.name, 'drawer-llm-unavailable');
      await expectClean(page);
    });

    test('429: new questions wait a few seconds', async ({ page }) => {
      const drawer = await openDrawer(page, 'rate_limited');
      await askSuggested(drawer, LINK_Q);
      await expect(drawer.locator('[data-agent-problem="rate_limited"]')).toContainText('Too many questions in a short time.');
      await expect(drawer.getByRole('textbox')).toBeDisabled();
      const next = await suggestedButton(drawer, ROSSI_Q);
      await expect(next).toBeDisabled();
      await shot(page, viewport.name, 'drawer-rate-limited');
      await expect(next).toBeEnabled({ timeout: 15_000 });
    });

    test('cached mode: the suggested questions answer, free text waits for the live model', async ({ page }) => {
      const drawer = await openDrawer(page, 'cached');
      await expect(drawer.getByRole('textbox')).toBeDisabled();
      await expect(drawer.locator('[data-cached-note]')).toContainText('Free questions need the live model; these answers were precomputed on the MI300X.');
      await askSuggested(drawer, ROSSI_Q);
      await expect(drawer.locator('[data-generated]')).toContainText('The Rossi family');
      await expect(page.getByRole('region', { name: 'Service mode' })).toContainText('CACHED DEMO');
      await shot(page, viewport.name, 'drawer-cached');
      await expectClean(page);
    });

    test('Escape closes the drawer and returns focus to the button that opened it', async ({ page }) => {
      const drawer = await openDrawer(page);
      await page.keyboard.press('Escape');
      await expect(drawer).toBeHidden();
      await expect(page.getByRole('button', { name: /Ask about this case/ })).toBeFocused();
    });
  });
}

test('dark theme: the drawer uses the theme tokens', async ({ page }) => {
  await page.goto('/returns/RET-000102?theme=dark');
  await page.getByRole('button', { name: /Ask about this case/ }).click();
  const drawer = page.getByRole('dialog', { name: /Ask about RET-000102/ });
  await askSuggested(drawer, LINK_Q);
  const colours = await drawer.evaluate((el) => ({
    bg: getComputedStyle(el).backgroundColor,
    fg: getComputedStyle(el.querySelector('[data-generated] p') as Element).color,
  }));
  // --surface and --ink-1 of the dark theme (docs/ui-design.md 4.2).
  expect(colours).toEqual({ bg: 'rgb(22, 27, 36)', fg: 'rgb(232, 236, 242)' });
  await shot(page, 'desktop', 'drawer-answer-dark');
});
