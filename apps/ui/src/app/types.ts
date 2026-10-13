// The contract between the shell and the view areas (docs/ui-design.md 13.3): each view's
// index.ts exports `routes: AppRoute[]` and `nav: NavItem[]`; src/app/router.tsx composes them.
import type { LucideIcon } from 'lucide-react';
import type { ComponentType, LazyExoticComponent } from 'react';

export interface AppRoute {
  /** A react-router path, e.g. `/returns/:returnId`. */
  path: string;
  /** The page name in the document title: `ReturnRing · <title>`. */
  title: string;
  Component: ComponentType | LazyExoticComponent<ComponentType>;
  /** Only in dev builds (the agent harness). */
  devOnly?: boolean;
}

export type NavGroup = 'work' | 'oversight' | 'demo' | 'system';
/** Badge counts from `page_size=1` queue queries (docs/ui-design.md 3.3). */
export type NavBadge = 'needsApproval' | 'reviewDue';

export interface NavItem {
  to: string;
  label: string;
  group: NavGroup;
  icon: LucideIcon;
  /** Order inside the group; lower first. */
  order: number;
  badge?: NavBadge;
}

export interface GpuDockProps {
  open: boolean;
  onClose(): void;
}

/** Shell slots exported by the platform area (docs/ui-design.md 3.4, 13.3). */
export interface ShellSlots {
  GpuDock: ComponentType<GpuDockProps>;
  DemoRail: ComponentType;
}
