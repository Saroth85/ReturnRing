// Manager area (Eng 3, docs/ui-design.md 8): matrix, simulator, KPIs, fairness, products and
// catalogue tasks, reviews, audit. Stories US-601..607, US-208.
import { BarChart3, CalendarClock, Calculator, Grid2x2, ScrollText, Scale, Shirt } from 'lucide-react';
import { lazy } from 'react';
import type { AppRoute, NavItem } from '@/app/types';
import { MANAGER } from './copy';

export const routes: AppRoute[] = [
  { path: '/portfolio', title: MANAGER.nav.portfolio, Component: lazy(() => import('./portfolio/PortfolioPage')) },
  { path: '/simulator', title: MANAGER.titles.simulator, Component: lazy(() => import('./simulator/SimulatorPage')) },
  { path: '/kpi', title: MANAGER.titles.kpi, Component: lazy(() => import('./kpi/KpiPage')) },
  { path: '/fairness', title: MANAGER.titles.fairness, Component: lazy(() => import('./fairness/FairnessPage')) },
  { path: '/products', title: MANAGER.titles.products, Component: lazy(() => import('./products/ProductsPage')) },
  { path: '/products/tasks', title: MANAGER.titles.tasks, Component: lazy(() => import('./products/TasksPage')) },
  { path: '/products/:anomalyId', title: MANAGER.titles.anomaly, Component: lazy(() => import('./products/AnomalyPage')) },
  { path: '/reviews', title: MANAGER.titles.reviews, Component: lazy(() => import('./reviews/ReviewsPage')) },
  { path: '/audit', title: MANAGER.titles.audit, Component: lazy(() => import('./audit/AuditPage')) },
];

export const nav: NavItem[] = [
  { to: '/reviews', label: MANAGER.nav.reviews, group: 'work', icon: CalendarClock, order: 20, badge: 'reviewDue' },
  { to: '/portfolio', label: MANAGER.nav.portfolio, group: 'oversight', icon: Grid2x2, order: 10 },
  { to: '/simulator', label: MANAGER.nav.simulator, group: 'oversight', icon: Calculator, order: 20 },
  { to: '/kpi', label: MANAGER.nav.kpi, group: 'oversight', icon: BarChart3, order: 30 },
  { to: '/fairness', label: MANAGER.nav.fairness, group: 'oversight', icon: Scale, order: 40 },
  { to: '/products', label: MANAGER.nav.products, group: 'oversight', icon: Shirt, order: 50 },
  { to: '/audit', label: MANAGER.nav.audit, group: 'oversight', icon: ScrollText, order: 70 },
];
