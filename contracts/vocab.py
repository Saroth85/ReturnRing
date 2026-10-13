"""Operational vocabularies shared by the HTTP API schemas and the SQL tables (ADR-15).

apps/api/schemas.py types its fields with these Literals and contracts/sql.py turns the same tuples
into CHECK constraints, so the API, the database and the UI speak one vocabulary. The policy
vocabularies (path, evidence quality, quadrant, return reason) stay in policy/types.py; the lever
vocabulary stays in contracts/levers.py.

This module must not import apps/ or llm/: both import contracts/.
"""

from __future__ import annotations

from typing import Literal, get_args

# ---- People and roles (spec 4: three demo users with fixed roles, no authentication) ----------

Role = Literal["operator", "manager", "warehouse", "system"]
ROLES: tuple[Role, ...] = get_args(Role)
SYSTEM_ACTOR = "system"  # the only actor with the role "system"; never an approver (rule 5)
DEMO_USERS: dict[str, Role] = {"sara": "operator", "luca": "manager", "ahmed": "warehouse"}
DEMO_ROLES: tuple[Role, ...] = ("operator", "manager", "warehouse")  # the roles people hold
ACTOR_PATTERN = r"^[a-z][a-z0-9._-]{1,31}$"

# ---- Languages and fixed legal text (llm/legal_clause.py holds the text) ----------------------

Lang = Literal["it", "en"]
LANGS: tuple[Lang, ...] = get_args(Lang)
ClauseKind = Literal["withdrawal", "guarantee"]
CLAUSE_KINDS: tuple[ClauseKind, ...] = get_args(ClauseKind)

# ---- Decisions (spec 8.6, US-303, US-306, US-307, US-607) -------------------------------------

# policy.types.DecisionState has proposed, approved and rejected; the API adds overridden: an
# operator replaced the proposed levers with others, with a reason (US-306).
DecisionStatus = Literal["proposed", "approved", "overridden", "rejected"]
DECISION_STATUSES: tuple[DecisionStatus, ...] = get_args(DecisionStatus)
# approve, override and reject act on a proposed version. lift and renew are the two outcomes of
# the review of a restriction that was approved or overridden (US-607): each creates the next
# version from policy.engine.decide() on the latest scores. lift (before or after the review
# date: a review, or a rectification on request) overrides it to the standard levers; renew (at
# or after the review date) approves the fresh restriction, with a new review date.
DecisionAction = Literal["approve", "override", "reject", "lift", "renew"]
DECISION_ACTIONS: tuple[DecisionAction, ...] = get_args(DecisionAction)
OverrideCategory = Literal[
    "evidence_disagreement",
    "customer_context",
    "linked_accounts_explained",
    "policy_exception",
    "periodic_review",  # a restriction reviewed at its review date (US-607)
    "other",
]
OVERRIDE_CATEGORIES: tuple[OverrideCategory, ...] = get_args(OverrideCategory)
# Why a new version of a decision exists (F7: every version stays in the log), and what makes it:
# initial, the intake function; evidence_added and link_disputed, the recompute of a case;
# review, a person's review of a restriction (action lift or renew).
RevisionTrigger = Literal["initial", "evidence_added", "link_disputed", "review"]
REVISION_TRIGGERS: tuple[RevisionTrigger, ...] = get_args(RevisionTrigger)
MIN_REASON_CHARS = 3  # an override, a rejection, a dispute or a reversal needs a real reason

# ---- LLM generations (spec 8.7, ADR-03, US-901) ------------------------------------------------

# llm: validated on the first attempt; llm_regenerated: validated on the second attempt;
# template: the deterministic fallback (validator failed twice, or the LLM was unavailable).
GenerationPath = Literal["llm", "llm_regenerated", "template"]
GENERATION_PATHS: tuple[GenerationPath, ...] = get_args(GenerationPath)
LlmMode = Literal["vllm", "mock"]
LLM_MODES: tuple[LlmMode, ...] = get_args(LlmMode)
ServedFrom = Literal["live", "cached"]  # DEMO_MODE: cached = precomputed on the MI300X
SERVED_FROM: tuple[ServedFrom, ...] = get_args(ServedFrom)
GenerationKind = Literal["case_draft", "regenerate", "agent_answer", "inspection_checklist"]
GENERATION_KINDS: tuple[GenerationKind, ...] = get_args(GenerationKind)
Tone = Literal["neutral", "warm", "formal"]
TONES: tuple[Tone, ...] = get_args(Tone)

# ---- Outbox (spec 4: simulated email, visible) --------------------------------------------------

OutboxStatus = Literal["queued", "sent_simulated"]
OUTBOX_STATUSES: tuple[OutboxStatus, ...] = get_args(OutboxStatus)

# ---- Case review: favourable evidence and link disputes (US-310, F7) --------------------------

EvidenceKind = Literal[
    "household_members",  # the linked accounts are distinct members of one household
    "shared_payment_explained",  # a shared card is explained (for example a family card)
    "collective_address",  # the shared address is a pickup point or a collective address
    "customer_contact",  # context from a contact with the customer
    "other",
]
EVIDENCE_KINDS: tuple[EvidenceKind, ...] = get_args(EvidenceKind)
# These kinds explain specific links: the explained links leave the cluster features (US-310).
LINK_EXPLAINING_EVIDENCE: frozenset[EvidenceKind] = frozenset(
    {"household_members", "shared_payment_explained", "collective_address"}
)

# ---- Checkout and coupons (US-401, US-402, US-403) --------------------------------------------

SizeAdviceKind = Literal["specific", "measurement_guide", "no_reliable_recommendation"]
SIZE_ADVICE_KINDS: tuple[SizeAdviceKind, ...] = get_args(SizeAdviceKind)
CouponApplicationState = Literal["proposed", "confirmed", "reversed"]
COUPON_APPLICATION_STATES: tuple[CouponApplicationState, ...] = get_args(CouponApplicationState)

# ---- Warehouse inspection (US-501..503) ---------------------------------------------------------

ChecklistItemId = Literal["tag_attached", "visible_damage", "photo_quality"]
CHECKLIST_ITEMS: tuple[ChecklistItemId, ...] = get_args(ChecklistItemId)
Observation = Literal["yes", "no", "not_observable"]  # "not observable" is a valid outcome
OBSERVATIONS: tuple[Observation, ...] = get_args(Observation)
InspectionState = Literal["awaiting_confirmation", "manual_review", "confirmed"]
INSPECTION_STATES: tuple[InspectionState, ...] = get_args(InspectionState)
PhotoMediaType = Literal["image/jpeg", "image/png", "image/webp"]
PHOTO_MEDIA_TYPES: tuple[PhotoMediaType, ...] = get_args(PhotoMediaType)

# ---- Catalogue tasks (spec 8.9, US-606) ---------------------------------------------------------

CatalogTaskKind = Literal["description", "size_guide", "batch_check"]
CATALOG_TASK_KINDS: tuple[CatalogTaskKind, ...] = get_args(CatalogTaskKind)
CatalogTaskStatus = Literal["open", "in_progress", "done", "cancelled"]
CATALOG_TASK_STATUSES: tuple[CatalogTaskStatus, ...] = get_args(CatalogTaskStatus)

# ---- Economic simulator (spec 8.10; policy.simulator.Nature) -----------------------------------

AssumptionBasis = Literal["simulated", "realized"]
ASSUMPTION_BASES: tuple[AssumptionBasis, ...] = get_args(AssumptionBasis)

# ---- Audit log (US-604, rule 5) -----------------------------------------------------------------

AuditAction = Literal[
    "decision_proposed",
    "decision_approved",
    "decision_overridden",
    "decision_rejected",
    "restriction_lifted",
    "restriction_renewed",
    "case_recomputed",
    "draft_generated",
    "draft_regenerated",
    "message_queued",
    "evidence_added",
    "link_disputed",
    "checkout_terms_shown",
    "checkout_confirmed",
    "inspection_created",
    "inspection_confirmed",
    "catalog_task_opened",
    "catalog_task_updated",
    "coupon_rule_approved",
    "coupon_rule_application_reversed",
    "assumptions_updated",
]
AUDIT_ACTIONS: tuple[AuditAction, ...] = get_args(AuditAction)
AuditEntityType = Literal[
    "decision",
    "llm_generation",
    "outbox_message",
    "evidence_addition",
    "link_dispute",
    "terms_snapshot",
    "inspection",
    "catalog_task",
    "coupon_rule",
    "coupon_rule_application",
    "simulator_assumptions",
]
AUDIT_ENTITY_TYPES: tuple[AuditEntityType, ...] = get_args(AuditEntityType)
# The actions about a decision version: exactly the entries with entity_type "decision", whose
# before and after are typed decision states (US-604).
DECISION_AUDIT_ACTIONS: frozenset[AuditAction] = frozenset(
    {
        "decision_proposed",
        "decision_approved",
        "decision_overridden",
        "decision_rejected",
        "restriction_lifted",
        "restriction_renewed",
        "case_recomputed",
    }
)
# Actions only a person may take (rule 5, GDPR art. 22): never logged with the system role.
HUMAN_ONLY_ACTIONS: frozenset[AuditAction] = frozenset(
    {
        "decision_approved",
        "decision_overridden",
        "decision_rejected",
        "restriction_lifted",
        "restriction_renewed",
        "evidence_added",
        "link_disputed",
        "inspection_confirmed",
        "catalog_task_opened",
        "catalog_task_updated",
        "coupon_rule_approved",
        "coupon_rule_application_reversed",
        "assumptions_updated",
    }
)
# Actions whose log entry must carry a reason of at least MIN_REASON_CHARS characters.
REASON_REQUIRED_ACTIONS: frozenset[AuditAction] = frozenset(
    {
        "decision_overridden",
        "decision_rejected",
        "restriction_lifted",
        "evidence_added",
        "link_disputed",
        "coupon_rule_application_reversed",
    }
)
