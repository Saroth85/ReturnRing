// Agent area (Eng 2, docs/ui-design.md 7). One public export, CaseAssistant, mounted by the
// operator case card; no page route except the dev harness in dev builds. Stories US-701, US-702.
import { lazy } from 'react';
import type { AppRoute, NavItem } from '@/app/types';
import { AGENT } from './copy';

export { CaseAssistant, type CaseAssistantProps } from './CaseAssistant';

export const routes: AppRoute[] = import.meta.env.DEV
  ? [{ path: '/dev/agent', title: AGENT.harnessTitle, Component: lazy(() => import('./dev/AgentHarness')), devOnly: true }]
  : [];

export const nav: NavItem[] = [];
