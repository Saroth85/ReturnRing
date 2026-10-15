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
  AssumptionInputs,
  Assumptions,
  AssumptionVersion,
  AuditEntry,
  AuditPage,
  AuditQuery,
  Benchmark,
  BenchmarksResponse,
  CartLine,
  CaseCard,
  CaseSnapshot,
  CheckoutLine,
  CatalogTask,
  CatalogTaskCreate,
  CatalogTaskUpdate,
  CheckoutConfirmRequest,
  CheckoutConfirmResponse,
  CheckoutEvaluateRequest,
  CheckoutEvaluateResponse,
  CitedFact,
  ClaimView,
  ClockView,
  CohortRate,
  ComponentCheck,
  CouponBlock,
  CouponRule,
  CouponRuleApplication,
  CouponRuleRef,
  CouponRulesResponse,
  CustomerMessageView,
  CustomerText,
  DecisionAuditState,
  DecisionInputs,
  DecisionRecord,
  DecisionRequest,
  DecisionResult,
  DecisionView,
  DemoCart,
  DemoCase,
  DemoCasesResponse,
  DemoDispute,
  DraftView,
  EntityRef,
  ErrorResponse,
  EvidenceRequest,
  ExplanationStatement,
  FairnessDimensionReport,
  FairnessResponse,
  FairnessSegment,
  FollowUp,
  GenerationAuditState,
  GenerationInfo,
  InspectionView,
  KpiResponse,
  LinkDisputeResponse,
  LinkedAccountsView,
  MatrixPoint,
  MessageChoice,
  Metric,
  NetBenefitComponent,
  NetBenefitView,
  OutboxMessage,
  OutboxResponse,
  PortfolioMatrix,
  ProductAnomaliesResponse,
  ProductAnomaly,
  Provenance,
  QuadrantSummary,
  QueueItem,
  QueueQuery,
  QueueResponse,
  ReadyzResponse,
  RecomputeResponse,
  RegenerateRequest,
  RegenerateResponse,
  RenderedMessage,
  RevisionSummary,
  SensitivityScenario,
  SimulatorAssumptionsResponse,
  SizeAdvice,
  TelemetryResponse,
  TemplateFields,
  Terms,
  WeeklyReturnRate,
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
/**
 * A lever of a decision: the contract inlines the lever into each list (no `Lever` definition
 * since 1.2.0), so the type is the item of `DecisionView.levers`. A lever of scope all_orders is
 * only ever true (contracts/levers.py): src/domain/levers.ts never builds one with false.
 */
export type Lever = Gen.DecisionView['levers'][number];
export type LeverScope = Lever['scope'];
export type Lang = Gen.MessageChoice['lang'];
export type LegalClauseKind = NonNullable<Gen.CustomerMessageView['legal_clause_kind']>;
export type TemplateFieldName = keyof Gen.TemplateFields;
/** A phrasebook sentence id (llm/phrasebook.py): the operator chooses reviewed sentences, never free text. */
export type SentenceId = Gen.CustomerMessageView['sentence_ids'][number];
export type Mode = Gen.ReadyzResponse['mode'];
export type CachedLabel = NonNullable<Gen.ReadyzResponse['label']>;
export type ClockKind = Gen.ClockView['kind'];
export type RevisionTrigger = Gen.DecisionView['revision_trigger'];
export type GenerationPath = Gen.GenerationInfo['generation_path'];
export type FallbackReason = NonNullable<Gen.GenerationInfo['fallback_reason']>;
export type ValidatorCode = Gen.GenerationInfo['validator_codes'][number];
export type AuditAction = Gen.AuditEntry['action'];
export type QueueSort = NonNullable<Gen.QueueQuery['sort']>;
export type DemoCaseKey = Gen.DemoCase['key'];
export type DemoCartKey = Gen.DemoCart['key'];
export type FactEntity = Gen.CitedFact['entity'];

export type Role = (typeof DEMO_USERS)[number]['role'];
export type DemoUserId = (typeof DEMO_USERS)[number]['id'];

/** Capability flags from the generated enums (docs/ui-design.md 11.1, 11.4). */
export const HAS_RENEW = (DECISION_ACTIONS as readonly string[]).includes('renew');
