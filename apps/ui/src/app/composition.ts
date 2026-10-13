// The only place that imports the view areas (through their index), docs/ui-design.md 13.3.
import * as agent from '@/views/agent';
import * as manager from '@/views/manager';
import * as operator from '@/views/operator';
import * as platform from '@/views/platform';
import type { AppRoute, NavItem, ShellSlots } from './types';

export const VIEW_ROUTES: AppRoute[] = [...operator.routes, ...agent.routes, ...manager.routes, ...platform.routes];

export const NAV_ITEMS: NavItem[] = [...operator.nav, ...agent.nav, ...manager.nav, ...platform.nav];

export const SHELL_SLOTS: ShellSlots = platform.shellSlots;
