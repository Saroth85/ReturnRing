// Agent area (Eng 2, docs/ui-design.md 7). One public export, CaseAssistant, mounted by the
// operator case card as a 440 px drawer; no page route except the dev harness /dev/agent, a full
// page with the same panel, in dev builds only. Stories US-701, US-702.
import { lazy } from 'react';
import type { AppRoute, NavItem } from '@/app/types';
import { AGENT } from './copy';

export { CaseAssistant, type CaseAssistantProps } from './CaseAssistant';

export const routes: AppRoute[] = import.meta.env.DEV
  ? [{ path: '/dev/agent', title: AGENT.harnessTitle, Component: lazy(() => import('./dev/AgentHarness')), devOnly: true }]
  : [];

export const nav: NavItem[] = [];
