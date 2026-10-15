// Operator area (Eng 1, docs/ui-design.md 6): queue, case card, decisions, evidence, disputes,
// alternatives, compare, decision record, outbox. Stories US-301..310, US-604, US-607.
import { Columns2, ListChecks, Mail } from 'lucide-react';
import { lazy } from 'react';
import type { AppRoute, NavItem } from '@/app/types';
import { OPERATOR } from './copy';

export const routes: AppRoute[] = [
  { path: '/queue', title: OPERATOR.titles.queue, Component: lazy(() => import('./queue/QueuePage')) },
  { path: '/returns/:returnId', title: OPERATOR.titles.case, Component: lazy(() => import('./case/CasePage')) },
  {
    path: '/returns/:returnId/versions/:decisionId',
    title: OPERATOR.titles.record,
    Component: lazy(() => import('./record/DecisionRecordPage')),
  },
  { path: '/compare', title: OPERATOR.nav.compare, Component: lazy(() => import('./compare/ComparePage')) },
  { path: '/outbox', title: OPERATOR.titles.outbox, Component: lazy(() => import('./outbox/OutboxPage')) },
];

export const nav: NavItem[] = [
  { to: '/queue', label: OPERATOR.nav.queue, group: 'work', icon: ListChecks, order: 10, badge: 'needsApproval' },
  { to: '/outbox', label: OPERATOR.nav.outbox, group: 'work', icon: Mail, order: 40 },
  // The pair comes from the demo cases (ComparePage): Giulia and Marco by their live ids.
  { to: '/compare', label: OPERATOR.nav.compare, group: 'demo', icon: Columns2, order: 10 },
];
