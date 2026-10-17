// Presenter mode, provenance mode, focus and the pitch path behaviours of UI round 1 (docs/ui-design.md
// 3.4, 3.5, 5.2, 10.3, 12): the layout survives provenance mode, presenter mode keeps every page
// reachable and shows the decision rail's approval, sheets give focus back, the mode tag never
// covers content, and the system beat opens on the GPU line (the B1-B4 benchmark cards were
// retired by ADR-18).
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect as baseExpect, test, type Page } from '@playwright/test';

const expect = baseExpect.configure({ timeout: 15_000 });

const here = path.dirname(fileURLToPath(import.meta.url));
/** The golden cases the mock serves (GET /api/demo/cases): the beats open these ids. */
const CASES = JSON.parse(readFileSync(path.resolve(here, '../../../../contracts/examples/api/demo_cases.json'), 'utf8')) as {
  cases: { key: string; return_id: string }[];
};
const caseId = (key: string) => CASES.cases.find((c) => c.key === key)?.return_id ?? '';
const MARCO = `/returns/${caseId('marco')}`;
const ROSSI_CARD = `/returns/${caseId('rossi_card')}`;
const COMPARE = `/compare?a=${caseId('giulia')}&b=${caseId('marco')}`;

async function caseReady(page: Page): Promise<void> {
  await expect(page.locator('[data-decision-rail]')).toBeVisible();
  await expect(page.locator('[data-action-bar]')).toBeVisible();
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
}

/** Top-level figures whose boxes intersect (a nested threshold figure is part of its parent). */
async function overlappingFigures(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const boxes = Array.from(document.querySelectorAll<HTMLElement>('main [data-figure]'))
      .filter((el) => !el.parentElement?.closest('[data-figure]') && el.offsetParent !== null)
      .map((el) => ({ el, r: el.getBoundingClientRect() }))
      .filter(({ r }) => r.width > 0 && r.height > 0);
    const out: string[] = [];
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i]!.r;
        const b = boxes[j]!.r;
        const overlap = Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1;
        if (overlap) out.push(`${boxes[i]!.el.textContent?.trim()} × ${boxes[j]!.el.textContent?.trim()}`);
      }
    }
    return out;
  });
}

test.describe('provenance mode keeps the layout (UI21)', () => {
  for (const url of ['/simulator', COMPARE, MARCO, '/portfolio', '/queue', '/system']) {
    test(`P on ${url}: no horizontal scroll, no overlapping figures`, async ({ page }) => {
      await page.goto(url);
      await expect(page.locator('h1').first()).toBeVisible();
      await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
      await page.locator('body').press('p');
      await expect(page.locator('[data-badge-period]').first()).toBeVisible();
      const width = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(width, 'horizontal page scroll in provenance mode').toBeLessThanOrEqual(0);
      expect(await overlappingFigures(page)).toEqual([]);
      // The sign and the amount stay on one line.
      const split = await page.evaluate(() =>
        Array.from(document.querySelectorAll<HTMLElement>('[data-figure] button > span:first-child')).filter((s) => {
          const rects = s.getClientRects();
          return rects.length > 1;
        }).length,
      );
      expect(split).toBe(0);
      // The waterfall keeps readable bars next to the longer figures.
      const bars = await page.evaluate(() =>
        Array.from(document.querySelectorAll<HTMLElement>('[data-waterfall] li > span[aria-hidden="true"]')).map((b) => b.getBoundingClientRect().width),
      );
      expect(bars.every((w) => w >= 60), `waterfall bar widths ${bars.join(', ')}`).toBe(true);
    });
  }
});

test.describe('presenter mode (UI22, UI23, UI25, UI26, UI27, UI30)', () => {
  test.use({ viewport: { width: 1280, height: 720 } });

  test('Marco opens with the approval and the terms on screen, above the demo rail', async ({ page }) => {
    await page.goto(`${MARCO}?present=1`);
    await caseReady(page);
    const gate = page.locator('[data-approval-gate]');
    await expect(gate).toBeInViewport();
    const rail = await page.locator('[data-demo-rail]').boundingBox();
    const bar = await page.locator('[data-action-bar]').boundingBox();
    expect(rail && bar && bar.y + bar.height <= rail.y + 1, 'action bar above the demo rail').toBe(true);
    // The graph grows and its labels never go below the floor (presenter: 14 px at an 18 px root).
    const graph = page.locator('[data-graph]').first();
    await expect(graph).toHaveAttribute('data-graph', 'ready');
    expect(Number(await graph.getAttribute('data-label-floor'))).toBeGreaterThanOrEqual(14);
    expect((await graph.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(440);
  });

  test('every page stays one click away, and the rail leaves presenter mode', async ({ page }) => {
    await page.goto(`${COMPARE}&present=1`);
    const nav = page.locator('nav[data-side-nav="collapsed"]');
    await expect(nav).toBeVisible();
    await nav.getByRole('link', { name: 'Simulated checkout' }).click();
    await expect(page).toHaveURL(/\/shop/);
    const demoRail = page.getByRole('navigation', { name: 'Demo beats' });
    // The mode tag lives in the rail, so it covers nothing (UI30).
    await expect(demoRail.locator('[data-mode-corner="inline"]')).toContainText('Contract examples');
    await expect(page.locator('[data-mode-corner="fixed"]')).toHaveCount(0);
    await demoRail.getByRole('button', { name: 'Leave presenter mode' }).click();
    await expect(demoRail).toHaveCount(0);
    await expect(page.locator('nav[data-side-nav="full"]')).toBeVisible();
  });

  test('beat 3: the evidence sheet offers the demo note', async ({ page }) => {
    await page.goto(`${ROSSI_CARD}?present=1`);
    await caseReady(page);
    await page.locator('[data-explain-link]').first().click();
    const sheet = page.locator('[data-sheet="evidence"]');
    await expect(sheet).toBeVisible();
    await sheet.getByRole('button', { name: 'Use demo note' }).click();
    await expect(sheet.getByRole('textbox')).toHaveValue(/one household/);
    await expect(page.locator('[data-sheet-confirm="evidence"]')).toBeEnabled();
  });

  test('beat 6: the system page opens on the GPU line, above the demo rail', async ({ page }) => {
    await page.goto('/system?present=1');
    const gpu = page.locator('#gpu');
    await expect(gpu).toBeVisible();
    await expect(gpu).toBeInViewport();
    const rail = await page.locator('[data-demo-rail]').boundingBox();
    await expect.poll(async () => (await gpu.boundingBox())?.y ?? Infinity).toBeLessThan(rail?.y ?? 0);
  });
});

test.describe('focus and keyboard (UI24, UI35)', () => {
  test('a sheet gives focus back to the button that opened it, also when opened with A', async ({ page }) => {
    await page.goto(MARCO);
    await caseReady(page);
    const approve = page.locator('[data-action="approve-restriction"]');
    await approve.click();
    await expect(page.locator('[data-sheet="approve"]')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-sheet="approve"]')).toHaveCount(0);
    await expect(approve).toBeFocused();
    await page.locator('body').press('a');
    await expect(page.locator('[data-sheet="approve"]')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(approve).toBeFocused();
  });

  test('the case assistant gives focus back to its button', async ({ page }) => {
    await page.goto(MARCO);
    await caseReady(page);
    const ask = page.locator('[data-ask-button]');
    await ask.click();
    await expect(page.getByRole('dialog', { name: new RegExp(`Ask about ${caseId('marco')}`) })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(ask).toBeFocused();
  });

  test('? lists the shortcuts; under 1024 px the drawer toggle reads Menu', async ({ page }) => {
    await page.goto('/queue');
    await expect(page.locator('h1').first()).toBeVisible();
    await page.locator('body').press('?');
    const dialog = page.getByRole('dialog', { name: 'Keyboard shortcuts' });
    await expect(dialog).toContainText('Provenance mode');
    await page.keyboard.press('Escape');
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator('summary').filter({ hasText: 'Menu' })).toBeVisible();
  });
});

test.describe('mode on contract examples (UI1, UI12, UI20)', () => {
  test('the system page never reads live on the mock adapter', async ({ page }) => {
    await page.goto('/system');
    await expect(page.locator('[data-readiness-pill]')).toHaveAttribute('data-readiness-pill', 'contract');
    await expect(page.locator('[data-llm-status]')).toHaveAttribute('data-llm-status', 'contract');
    await expect(page.locator('[data-contract-mismatch]')).toHaveCount(0);
  });

  test('a different contract version on /readyz shows the mismatch notice', async ({ page }) => {
    await page.goto('/queue?scenario=contract_mismatch');
    await expect(page.locator('h1').first()).toBeVisible();
    const notice = page.locator('[data-contract-mismatch]');
    await expect(notice).toBeVisible();
    await expect(notice).toContainText('this UI was built for');
  });

  test('from 1024 px the corner tag sits over the navigation column, never over the page', async ({ page }) => {
    await page.goto(COMPARE);
    await expect(page.locator('[data-compare-closing]')).toBeVisible();
    const tag = await page.locator('[data-mode-corner]').boundingBox();
    const main = await page.locator('main').boundingBox();
    expect(tag && main && tag.x + tag.width <= main.x, 'tag left of the page').toBe(true);
  });
});
