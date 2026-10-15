// Smoke test against the live API (docs/ui-design.md 14.2): every route, with the live ids of the
// demo cases (GET /api/demo/cases), renders without console errors, with no number lacking
// provenance (PROV-UI) and no banned wording; the mode banner tells the live service from the
// contract examples and shows the service's clock. Reads only: it runs on a writable API and on
// the read-only public demo alike.
import { expect, test } from '@playwright/test';
import type { CaseCard, DemoCasesResponse, ReadyzResponse } from '../../../src/api/models';
import { caseCard, caseOf, demoCases, expectClean, readyz, settle, watchConsole } from './live';

interface LiveContext {
  readyz: ReadyzResponse;
  cases: DemoCasesResponse;
  elena: CaseCard;
}

let context: Promise<LiveContext> | null = null;
/** The live store once per worker: the demo cases, and Elena's card for her anomaly. */
function live(): Promise<LiveContext> {
  context ??= (async () => {
    const cases = await demoCases();
    return { readyz: await readyz(), cases, elena: await caseCard(caseOf(cases, 'elena').return_id) };
  })();
  return context;
}

interface LiveRoute {
  name: string;
  area: 'operator' | 'agent' | 'manager' | 'platform' | 'shell';
  /** The URL on the live store, null when the store holds nothing to open. */
  path(ctx: LiveContext): string | null;
  /** HTTP error statuses the page expects from the API (the browser logs them as failed loads). */
  expectedStatuses?: number[];
}

const id = (ctx: LiveContext, key: Parameters<typeof caseOf>[1]) => caseOf(ctx.cases, key).return_id;

const ROUTES: LiveRoute[] = [
  { name: 'queue', area: 'operator', path: () => '/queue' },
  { name: 'queue by refund deadline', area: 'operator', path: () => '/queue?tab=all&sort=refund_due_by' },
  { name: 'case Giulia', area: 'operator', path: (c) => `/returns/${id(c, 'giulia')}` },
  { name: 'case Marco', area: 'operator', path: (c) => `/returns/${id(c, 'marco')}` },
  { name: 'case Rossi', area: 'operator', path: (c) => `/returns/${id(c, 'rossi')}` },
  { name: 'case Rossi shared card', area: 'operator', path: (c) => `/returns/${id(c, 'rossi_card')}` },
  { name: 'case Elena', area: 'operator', path: (c) => `/returns/${id(c, 'elena')}` },
  { name: 'case insufficient evidence', area: 'operator', path: (c) => `/returns/${id(c, 'insufficient')}` },
  {
    name: 'case of the demo dispute',
    area: 'operator',
    path: (c) => (c.cases.dispute ? `/returns/${c.cases.dispute.return_id}?link=${encodeURIComponent(c.cases.dispute.link_id)}` : null),
  },
  { name: 'case not found', area: 'operator', path: () => '/returns/RET-999999', expectedStatuses: [404] },
  {
    name: 'decision record of Marco',
    area: 'operator',
    path: (c) => `/returns/${id(c, 'marco')}/versions/${caseOf(c.cases, 'marco').decision_id}`,
  },
  { name: 'compare (demo pair)', area: 'operator', path: () => '/compare' },
  { name: 'outbox', area: 'operator', path: () => '/outbox' },
  { name: 'agent harness', area: 'agent', path: (c) => `/dev/agent?return=${id(c, 'marco')}` },
  { name: 'portfolio', area: 'manager', path: () => '/portfolio' },
  { name: 'simulator', area: 'manager', path: () => '/simulator' },
  { name: 'kpi', area: 'manager', path: () => '/kpi' },
  { name: 'fairness', area: 'manager', path: () => '/fairness' },
  { name: 'products', area: 'manager', path: () => '/products' },
  {
    name: "Elena's product anomaly",
    area: 'manager',
    path: (c) =>
      c.elena.product_anomaly ? `/products/${encodeURIComponent(c.elena.product_anomaly.anomaly_id)}?return=${c.elena.return_id}` : null,
  },
  { name: 'catalogue tasks', area: 'manager', path: () => '/products/tasks' },
  { name: 'reviews', area: 'manager', path: () => '/reviews' },
  { name: 'audit', area: 'manager', path: () => '/audit' },
  { name: 'shop Giulia', area: 'platform', path: () => '/shop?preset=giulia' },
  { name: 'shop ring', area: 'platform', path: () => '/shop?preset=ring' },
  { name: 'inspection', area: 'platform', path: () => '/inspection' },
  { name: 'coupons', area: 'platform', path: () => '/coupons' },
  { name: 'system', area: 'platform', path: () => '/system' },
  { name: 'demo path', area: 'platform', path: () => '/demo' },
  { name: 'about', area: 'platform', path: () => '/about' },
  { name: 'not found', area: 'shell', path: () => '/nowhere' },
];

for (const route of ROUTES) {
  test(`${route.area}: ${route.name} renders the live data cleanly`, async ({ page }) => {
    const ctx = await live();
    const path = route.path(ctx);
    test.skip(path === null, 'the live store holds nothing to open here');
    test.info().annotations.push({ type: 'path', description: path ?? '' });
    const errors = watchConsole(page, route.expectedStatuses);
    await page.goto(path as string);
    await settle(page);
    // The live service, never the contract examples: no mock tag, the mode and clock of /readyz.
    const banner = page.getByRole('region', { name: 'Service mode' });
    await expect(banner).toBeVisible();
    await expect(banner).not.toHaveAttribute('data-mode-banner', 'contract');
    await expect(page.locator('[data-banner-clock]')).toHaveAttribute('data-banner-clock', ctx.readyz.clock.kind);
    await expect(page.locator('[data-demo-reset-missing]')).toHaveCount(0);
    await expect(page.locator('[data-contract-mismatch]')).toHaveCount(0);
    await expectClean(page, errors);
  });
}

test('the banner and the top bar follow /readyz: mode, label, read-only flag and clock', async ({ page }) => {
  const ctx = await live();
  const r = ctx.readyz;
  await page.goto('/about');
  await settle(page);
  const banner = page.getByRole('region', { name: 'Service mode' });
  const kind = r.mode === 'cached' ? 'cached' : r.llm_mode === 'mock' ? 'live-cpu' : 'live';
  await expect(banner).toHaveAttribute('data-mode-banner', kind);
  // The cached label exactly as /readyz gives it (MI300X or rehearsal), never a fixed GPU name.
  if (r.label) await expect(banner).toContainText(r.label);
  else await expect(banner).not.toContainText('Precomputed');
  if (r.public_read_only) await expect(banner).toContainText('read-only public demo');
  else await expect(banner).not.toContainText('read-only public demo');
  const clock = page.locator('[data-banner-clock]');
  await expect(clock).toHaveAttribute('data-clock-frozen', r.clock.frozen ? 'yes' : 'no');
  await expect(clock).toHaveAttribute('title', r.clock.note);
  // The top bar shows the service's 'now': a frozen clock exactly, a running one from its reading on.
  const now = page.locator('[data-top-clock] time');
  const shown = Date.parse((await now.getAttribute('datetime')) ?? '');
  const served = Date.parse(r.clock.now);
  if (r.clock.frozen) expect(shown).toBe(served);
  else expect(shown).toBeGreaterThanOrEqual(served);
});
