// Platform area (Eng 4, docs/ui-design.md 9): checkout, inspection, coupon rules, system and
// benchmarks, demo path, about. Also the shell slots GpuDock and DemoRail. Stories US-401..403,
// US-501..503, US-901, US-902, US-905, US-906.
import { Cpu, Info, PlayCircle, ScanSearch, ShoppingBag, Ticket } from 'lucide-react';
import { lazy } from 'react';
import type { AppRoute, NavItem, ShellSlots } from '@/app/types';
import { PLATFORM } from './copy';
import { DemoRail } from './demo/DemoRail';
import { GpuDock } from './system/GpuDock';

export const routes: AppRoute[] = [
  { path: '/shop', title: PLATFORM.titles.shop, Component: lazy(() => import('./shop/ShopPage')) },
  { path: '/inspection', title: PLATFORM.titles.inspection, Component: lazy(() => import('./inspection/InspectionPage')) },
  {
    path: '/inspection/:returnId',
    title: PLATFORM.titles.inspectionDetail,
    Component: lazy(() => import('./inspection/InspectionDetailPage')),
  },
  { path: '/coupons', title: PLATFORM.titles.coupons, Component: lazy(() => import('./coupons/CouponsPage')) },
  { path: '/system', title: PLATFORM.titles.system, Component: lazy(() => import('./system/SystemPage')) },
  { path: '/demo', title: PLATFORM.titles.demo, Component: lazy(() => import('./demo/DemoPage')) },
  { path: '/about', title: PLATFORM.titles.about, Component: lazy(() => import('./about/AboutPage')) },
];

export const nav: NavItem[] = [
  { to: '/inspection', label: PLATFORM.nav.inspection, group: 'work', icon: ScanSearch, order: 30 },
  { to: '/coupons', label: PLATFORM.nav.coupons, group: 'oversight', icon: Ticket, order: 60 },
  { to: '/shop?preset=giulia', label: PLATFORM.nav.shop, group: 'demo', icon: ShoppingBag, order: 20 },
  { to: '/demo', label: PLATFORM.nav.demo, group: 'demo', icon: PlayCircle, order: 30 },
  { to: '/system', label: PLATFORM.nav.system, group: 'system', icon: Cpu, order: 10 },
  { to: '/about', label: PLATFORM.nav.about, group: 'system', icon: Info, order: 20 },
];

/** Shell slots (docs/ui-design.md 3.4): the GPU dock (`T`) and the presenter's demo rail. */
export const shellSlots: ShellSlots = { GpuDock, DemoRail };
