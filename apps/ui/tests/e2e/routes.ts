// Every placeholder route of the scaffold with the URL that exercises it on the contract examples
// (docs/ui-design.md 3.2). Owners extend their area's specs; this list is the smoke baseline.
export interface SmokeRoute {
  name: string;
  path: string;
  area: 'operator' | 'agent' | 'manager' | 'platform' | 'shell';
  /** HTTP error statuses the page expects from the API (the browser logs them on the live adapter). */
  expectedStatuses?: number[];
}

export const SMOKE_ROUTES: SmokeRoute[] = [
  { name: 'queue', path: '/queue', area: 'operator' },
  { name: 'case-marco', path: '/returns/RET-000102', area: 'operator' },
  { name: 'case-elena', path: '/returns/RET-000105', area: 'operator' },
  { name: 'case-insufficient', path: '/returns/RET-000109', area: 'operator' },
  { name: 'case-not-found', path: '/returns/RET-999999', area: 'operator', expectedStatuses: [404] },
  { name: 'decision-record', path: '/returns/RET-000106/versions/DEC-000106-1', area: 'operator' },
  { name: 'compare', path: '/compare?a=RET-000101&b=RET-000102', area: 'operator' },
  { name: 'outbox', path: '/outbox', area: 'operator' },
  { name: 'agent-harness', path: '/dev/agent?return=RET-000102', area: 'agent' },
  { name: 'portfolio', path: '/portfolio', area: 'manager' },
  { name: 'simulator', path: '/simulator', area: 'manager' },
  { name: 'kpi', path: '/kpi?week=2026-09-21', area: 'manager' },
  { name: 'fairness', path: '/fairness', area: 'manager' },
  { name: 'products', path: '/products', area: 'manager' },
  { name: 'product-anomaly', path: '/products/V-JKT-DEF~defective~2026-09-21', area: 'manager' },
  { name: 'catalog-tasks', path: '/products/tasks', area: 'manager' },
  { name: 'reviews', path: '/reviews', area: 'manager' },
  { name: 'audit', path: '/audit', area: 'manager' },
  { name: 'shop-giulia', path: '/shop?preset=giulia', area: 'platform' },
  { name: 'shop-marco', path: '/shop?preset=marco', area: 'platform' },
  { name: 'inspection', path: '/inspection', area: 'platform' },
  { name: 'inspection-detail', path: '/inspection/RET-000110', area: 'platform' },
  { name: 'coupons', path: '/coupons', area: 'platform' },
  { name: 'system', path: '/system', area: 'platform' },
  { name: 'demo', path: '/demo', area: 'platform' },
  { name: 'about', path: '/about', area: 'platform' },
  { name: 'not-found', path: '/nowhere', area: 'shell' },
];
