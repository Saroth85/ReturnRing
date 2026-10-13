// The types components use (docs/ui-design.md 11.1). Components import from here, never from
// types.gen.ts, so a contract bump breaks the compile in this file and in the adapters, not in
// forty components.
import { DECISION_ACTIONS, type DEMO_USERS } from './contract.gen';
import type * as Gen from './types.gen';

export type {
  AccountLink,
  AccountNode,
  AgentAskRequest,
  Alternative,
  AuditEntry,
  AuditPage,
  AuditQuery,
  Benchmark,
  BenchmarksResponse,
  CaseCard,
  CaseSnapshot,
  CatalogTask,
  CheckoutConfirmRequest,
  CheckoutConfirmResponse,
  CheckoutEvaluateRequest,
  CheckoutEvaluateResponse,
  ComponentCheck,
  CouponRuleApplication,
  CouponRulesResponse,
  CustomerMessageView,
  DecisionRecord,
  DecisionRequest,
  DecisionResult,
  DecisionView,
  DraftView,
  EntityRef,
  ErrorResponse,
  EvidenceRequest,
  ExplanationStatement,
  FairnessResponse,
  GenerationInfo,
  InspectionView,
  KpiResponse,
  Lever,
  LinkedAccountsView,
  Metric,
  NetBenefitView,
  OutboxMessage,
  OutboxResponse,
  PortfolioMatrix,
  ProductAnomaliesResponse,
  Provenance,
  QueueItem,
  QueueQuery,
  QueueResponse,
  ReadyzResponse,
  RecomputeResponse,
  RegenerateResponse,
  SimulatorAssumptionsResponse,
  TelemetryResponse,
  TemplateFields,
  Terms,
} from './types.gen';
export type { AgentEvent } from './sse';

export type Unit = Gen.Metric['unit'];
export type Nature = Gen.Provenance['nature'];
/** Nature as shown: the contract's three plus illustrative (origin "Illustrative…", or a benchmark status). */
export type DisplayNature = Nature | 'illustrative';
export type QuadrantId = Gen.Quadrant;
export type PathId = Gen.Path;
export type EvidenceQualityId = Gen.EvidenceQuality;
export type DecisionState = Gen.DecisionView['state'];
export type DecisionAction = Gen.DecisionRequest['action'];
export type Lang = Gen.CustomerMessageView['lang'];
export type LegalClauseKind = NonNullable<Gen.CustomerMessageView['legal_clause_kind']>;
export type TemplateFieldName = keyof Gen.TemplateFields;
export type Mode = Gen.ReadyzResponse['mode'];

export type Role = (typeof DEMO_USERS)[number]['role'];
export type DemoUserId = (typeof DEMO_USERS)[number]['id'];

/** Capability flags from the generated enums (docs/ui-design.md 11.1, 11.4). */
export const HAS_RENEW = (DECISION_ACTIONS as readonly string[]).includes('renew');
