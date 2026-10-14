# ADR-15: API and persistence contract

Status: accepted, 13 Oct 2026; amended the same day after four reviews (sections 9 to 12), and on
14 Oct 2026 by the phase 4 integration (section 13), the merge of the LLM layer's closing
rounds (section 14), the review of the running API (section 15), the two reviews of the
public deployment (sections 16 and 17), the examples checked against the live service (section
18), and the rules and legal items of the review backlog (section 19) and the review of that
section (section 20). API contract version `1.8.0`. Covers spec section 9 (API table, flows F1
to F7), the stories of E3, E4, E5, E6, E7 and E9, NFR-16 and CLAUDE.md rules 1, 3, 4, 5, 9, 10,
11 and 12. Changes `contracts/` (new `contracts/vocab.py`, `contracts/sql.py`,
`contracts/json/api/`, `contracts/examples/api/`), removes two v1 API schemas, and adds
`apps/api/schemas.py` and two scripts.

## Context

The API (phase 4) and the React UI (phase 5) are built at the same time by sessions that do not
talk to each other. Before this ADR they shared:

- the spec 9 table: a list of paths with one line of intent each;
- three bodies in spec 19.B;
- two hand-written JSON Schemas (`decision_request`, `checkout_evaluate_response`) that no code
  produced.

Many questions had no answer. What a case card holds. How a number carries its provenance.
What "override" does to the levers. Where a checkout snapshot lives before the order exists. How
the agent streams. Which invariants the database enforces. Two teams guessing differently would
meet only at integration, two days before the deadline.

## Decision

### 1. One source, generated files, tested examples

| What | Where | Role |
| --- | --- | --- |
| Models | `apps/api/schemas.py` | Pydantic v2, `extra="forbid"` everywhere; the source of truth |
| Vocabularies | `contracts/vocab.py` | Literals shared by the models and the SQL CHECKs (contracts/ must not import apps/) |
| JSON Schemas | `contracts/json/api/<model>.schema.json` | `python -m scripts.export_api_schemas`: requests and queries in validation mode, responses in serialization mode (every response field required) |
| Endpoint table | `contracts/json/api/endpoints.json` | `ENDPOINTS` of the models module: method, path, query, request, response, status, media type, errors, roles, stories |
| OpenAPI | `contracts/json/api/openapi.json` | Same models and routes, from a stub FastAPI app: for TypeScript codegen and mock servers |
| Examples | `contracts/examples/api/<model>.<variant>.json` | `python -m scripts.build_api_examples`: golden cases and an example for every endpoint |
| Mock routes | `contracts/examples/api/index.json` | Which example answers which request (method, concrete path, query, request, scenario, status) |
| Tables | `contracts/sql.py` | SQLAlchemy 2.1 Core, PostgreSQL (asyncpg) and SQLite (aiosqlite) |

Rules on these files:

- **Generated, never edited.** Tests fail when a committed schema or example differs from a fresh
  export (`--check` modes).
- **Structural and semantic checks.** The JSON Schemas are structural, with `if`/`then` rules
  where a field depends on another (a reason for override, reject and lift; the typed audit
  payload of a decision entry; the `decision_id` of a dispute from a case). The model validators
  add the semantic rules: the engine invariants, `policy.legal_guard` on every decision, net
  benefit equal to `policy.simulator`, the legal clause, the draft echo, the template fields,
  quadrant totals and the agent event order.
- **Every example passes both** its model and its JSON Schema. Each mutation in
  `tests/test_api_schemas.py` breaks one rule and must be rejected.

### 2. Conventions

- **JSON.** Datetimes are timezone-aware ISO 8601 in UTC (`...Z`) and dates are ISO dates. Ids
  match `^[A-Za-z0-9][A-Za-z0-9._:~-]*$`.
- **Link and anomaly ids.**
  - A link id is `customer_a~customer_b` with `customer_a < customer_b` (`link_id()`).
  - An anomaly id is `variant_id~reason~week` (`anomaly_id()`).
- **Money.** A JSON number in EUR with at most 2 decimals. The API computes in `Decimal`.
  `Metric` accepts a `Decimal` and converts it.
- **Reasons.** A reason (override, rejection, lift, dispute, reversal, notes) has at least 3
  characters once trimmed; the JSON Schema says it with a pattern.
- **Figures (rule 11, US-906, NFR-16).**
  - Every figure the UI shows is a `Metric {value, unit, provenance}`. This covers scores, rates,
    counts, euros, net benefit, thresholds, KPIs, fairness gaps, benchmark and telemetry numbers,
    the risk of each decision revision and the risk in the audit payload of a decision.
  - `provenance` is `{origin, period_start, period_end, nature}`, with nature one of real,
    synthetic or simulated: the fields of `contracts.io.Provenance`.
  - Units: eur, usd, count (integer), expected_count, ratio, probability, percent,
    percentage_points, z_score, score, days, seconds, milliseconds, tokens_per_second, gib, watts.
    Ranges are checked per unit.
  - A plain number is allowed only as an attribute of a record (an order's price, a link's
    weight, a SHAP value, a model confidence) inside a section that carries its own `provenance`,
    which then covers it:

    | Section | Its `provenance` covers |
    | --- | --- |
    | `QueueResponse` | `linked_account_count` of the items |
    | `CaseCard` | item, request and `product_anomaly.trend` (the shop's weekly sales and returns) |
    | `RiskView` | `top_factors`: feature values and SHAP contributions |
    | `LinkedAccountsView`, `OrderHistory`, `ProductAnomaliesResponse` | accounts and links, orders, trends |
    | `RecomputeResponse` | the before and after snapshots |
    | `LinkDisputeResponse` | the disputed link |
    | `InspectionView` | the declared model confidences of the checklist |
    | `DecisionInputs` (in every `DecisionView`) | the engine inputs: risk, value and thresholds, as scored at decision time |
    | `NetBenefitView` (the provenance of `total`, which every component shares) | the assumption values |
    | `CheckoutEvaluateResponse`, `PortfolioMatrix` (`points_provenance`) | line probabilities, points |

  - The other plain numbers are not figures: counters, pages and sequence numbers, versions,
    run settings (`attempts`, `latency_ms`, a benchmark's `config`), and policy terms (a lever
    value, `return_window_days`). `test_every_plain_number_of_a_response_has_a_covering_provenance`
    walks every response model and lists them.
- **Levers.**
  - Levers are the `{id, value, scope}` objects of `contracts/levers.py` (ADR-13). A list holds the
    differences from the standard levers on the merchant base, in catalogue order. Every lever
    array is `lever_list_json_schema()` (each lever at most once, at most one object per
    catalogue lever, `maxItems` for guided decoding, ADR-14), whose items are the catalogue
    schema (`lever_json_schema()`); the models check the same with
    `contracts.levers.check_lever_objects`, and a test checks every exported schema.
  - `levers_from_policy` and `to_policy_levers` convert between this list and `policy.types.Levers`.
  - `lever_ids` is the `"id:value"` form.
- **Assumption sets (US-602).** One label, one set of values. A stored version (`assumptions-v1`)
  has `based_on` null. Every other set has its own label and names in `based_on` the stored
  version whose unit costs (per return, per undue discount, per hour) it uses with exactly those
  values (`check_unit_costs`; `Assumptions.derive` builds one):
  - the alternatives of a decision version: `<decision_id>/<alternative kind>`; all the
    alternatives of a decision are based on its `assumptions_version`, with its unit costs and
    its basis;
  - the week of the KPIs: `kpi/<week>` (simulated), `kpi/<week>/realized` (observed). The
    realized set names the same stored version as the simulated one and uses its unit costs; it
    keeps its own basis (realized), which `check_unit_costs` accepts for a `/realized` set only;
  - a sensitivity scenario: `<version>/<scenario>`, whose unit costs change on purpose.

  The models check these labels and the shared unit costs and basis; `check_unit_costs` also
  compares each set with the stored version itself.
- **Headers.** Every response carries `X-ReturnRing-Mode: live|cached` (US-901) and
  `X-Request-Id`, which the audit log also stores (NFR-12).
- **Errors.** Every non-2xx body is `ErrorResponse {code, message, violations, request_id}`.
  `endpoints.json` lists the error statuses of each endpoint.

  | Status | `code` | When |
  | --- | --- | --- |
  | 422 | `invalid_request` | The body, path or query is invalid; pydantic errors become `violations` |
  | 400 | `invalid_request` | The body cannot be parsed (not UTF-8, nested too deep; section 15, H28) |
  | 413 | `invalid_request` | The body is larger than 8 MiB, on every endpoint with a body (section 15, H24) |
  | 422 | `legal_guard` | `policy.legal_guard` refused the decision; `violations` carries its codes |
  | 404 | `not_found` | |
  | 409 | `conflict` | Already decided, already confirmed, "approve" sent with levers different from the proposal, "lift" or "renew" on a version that is not a restriction, "renew" before the review date or when the fresh proposal is not a restriction, a restriction approved on or after its review date (a stale proposal: the case is recomputed), `/checkout/confirm` of terms whose decision is no longer in force; an act whose message would state a refund deadline already past (section 15, H1): approve, or an override to levers other than the standard ones (a rejection or an override to the standard levers goes on without a message, section 19, on a case with a diminished-value claim too since section 20; a guarantee case has no refund deadline); a restriction proposal at or past its review date, decided again first (trigger `stale_proposal`, section 15, H16) |
  | 409 | `stale_decision` | The `decision_id` sent is not the latest version of the case |
  | 403 | `forbidden_role` | The actor's role is not among the endpoint's `roles` (section 5); a write on the public read-only demo (section 15, H21) |
  | 429 | `rate_limited` | The agent; on the public read-only demo, the checkout evaluation too |
  | 503 | `llm_unavailable` | Regeneration and the agent only: no template or cache can answer them |
- **Pages and order.**
  - Pages are 1-based (`page`, `page_size`); `total` counts after the filters.
  - The queue sorts by `-expected_net_benefit` by default, ties by `return_id`. List filters
    repeat the parameter (`?quadrant=Q3&quadrant=Q4`). `QueueQuery.matches` is the reference
    meaning of the filters.
- **Concurrency.** Every action on a case names the `decision_id` it was shown, a dispute from a
  case included. Only the latest version of a return can be acted on.

### 3. Endpoints

Spec 9 plus what the stories need: `GET /api/outbox` (spec 4, the visible outbox), catalogue-task
list and update (US-606 follow-up), coupon rules (US-402, US-607), checkout confirm (US-403) and
the decision history (US-604).

| Method and path | Query or body → response | Stories |
| --- | --- | --- |
| GET /healthz | → `healthz_response` | US-001 |
| GET /readyz | → `readyz_response` (mode, label "Precomputed on AMD MI300X" when cached, component checks, versions) | US-901 |
| GET /api/returns | `queue_query` → `queue_response` | US-301, 607 |
| GET /api/returns/{return_id} | → `case_card` | US-302..305, 307..309 |
| POST /api/returns/{return_id}/decision | `decision_request` → `decision_result` | US-303, 306, 607 |
| GET /api/decisions/{decision_id} | → `decision_record` | US-604 |
| POST /api/returns/{return_id}/regenerate | `regenerate_request` → `regenerate_response` | US-304, 305 |
| POST /api/returns/{return_id}/evidence | `evidence_request` → `recompute_response` | US-310 |
| POST /api/links/{link_id}/dispute | `link_dispute_request` → `link_dispute_response` | US-310, 203 |
| POST /api/checkout/evaluate | `checkout_evaluate_request` → `checkout_evaluate_response` | US-401..403 |
| POST /api/checkout/confirm (201) | `checkout_confirm_request` → `checkout_confirm_response` | US-403 |
| POST /api/inspections (201) | `inspection_create_request` → `inspection` | US-501, 503 |
| POST /api/inspections/{inspection_id}/confirm | `inspection_confirm_request` → `inspection_confirm_response` | US-502 |
| GET /api/portfolio/matrix | → `portfolio_matrix` | US-601, 602 |
| GET, PUT /api/simulator/assumptions | PUT `simulator_assumptions_update` → `simulator_assumptions` | US-602 |
| GET /api/kpi | `kpi_query` → `kpi` | US-603 |
| GET /api/fairness | → `fairness` | US-605 |
| GET /api/audit | `audit_query` → `audit_page`, or `text/csv` with `format=csv` | US-604 |
| GET /api/outbox | `outbox_query` → `outbox` | US-304 |
| GET /api/products/anomalies | `anomalies_query` → `product_anomalies` | US-208, 606 |
| GET, POST (201) /api/catalog-tasks | POST `catalog_task_create` → `catalog_task`; GET → `catalog_tasks` | US-606 |
| PATCH /api/catalog-tasks/{task_id} | `catalog_task_update` → `catalog_task` | US-606 |
| GET, POST (201) /api/coupon-rules | POST `coupon_rule_create` → `coupon_rules` | US-402 |
| POST /api/coupon-rules/applications/{application_id}/reverse | `coupon_application_reverse` → `coupon_rule_application` | US-402, 607 |
| POST /api/agent/ask | `agent_ask_request` → `text/event-stream` of `agent_stream` events | US-701, 702 |
| GET /api/benchmarks | → `benchmarks` | US-902 |
| GET /api/telemetry | → `telemetry` | US-905 |
| GET /api/demo/cases | → `demo_cases` (the golden cases of the reset with their live ids; 404 before a reset) | US-903 |

### 4. Semantics by area

**Return intake (flow F1, step 1; ADR-03).** There is no HTTP endpoint for a new return. The
simulated shop adapter is a module of the API process: it writes the `return_requests` row
(source `adapter`) and calls the API's intake function in the same process. The demo reset
(US-903) calls the same function for the open returns of the synthetic store (source `store`).
The intake function, and only it, triggers the decision:

1. it chooses the path, reads the latest scores and the evidence quality, runs
   `policy.engine.decide()` and `policy.legal_guard`;
2. it writes version 1 of the decision (trigger `initial`) and the `decision_proposed` audit
   entry with the system actor;
3. it puts the case in the generation queue. In live mode a worker of the API sends the queue to
   vLLM in batches (ADR-03); the nightly pipeline (F4) pre-generates the drafts of every open
   return; in cached mode the drafts come from the demo cache. Until a draft passes the validator
   the case shows `draft_status: pending`.

**Queue and case card (US-301, US-302, US-307, US-308).**

- A queue item shows path and quadrant badges, the state, `requires_approval`, the lever ids,
  `review_at` and `review_due`, risk, value, expected net benefit, the number of linked
  accounts, `variant_anomaly` and `draft_status`.
- Every queued return has a case card. The card shows the latest version of the decision.
- The case card holds:
  - the returned item and the request (`customer_text` is data, never instructions);
  - the evidence quality: three components (`observations`, `identifier_completeness`,
    `reason_consistency`) against their thresholds. It is insufficient exactly when a component
    fails, and `missing_evidence` is non-empty exactly then;
  - risk and value (below);
  - linked accounts: every account and link of the cluster, with kinds, dates, frequency and
    dispute state. The cluster id is an opaque graph id (`CL-...`), never a truth ring or family
    id (rule 12);
  - the complete 12-month order history with its return rate;
  - the terms snapshot of the order;
  - the decision, up to three alternatives, the draft, the product anomaly of the returned variant
    and the decision revisions.
- **Risk.** Shown only on the withdrawal path with sufficient evidence (rule 4, US-307). It has
  exactly 3 SHAP contributions labelled `SCORE`. They are contributions, not causes, and never a
  forbidden feature or customer value. Favourable and unfavourable evidence are shown side by side.
- **Value.** The 12-month net margin with its three parts (spec 8.5), and a fixed legend saying it
  is never a signal of abuse.
- **Expected net benefit.** The total of the recommended alternative. Exactly one alternative is
  recommended, and its levers are the engine's proposal.
- **Every net benefit** is `NetBenefitView {assumptions, components, total}`, recomputed by
  `policy.simulator.net_benefit` in the validator. There is no refund component (rule 9).

**Decisions (spec 8.6, US-303, US-306, US-607).**

- **`DecisionView`** is one version of a case's decision. It holds the engine inputs (`inputs`,
  with the order and the customer: re-runnable for US-604) and the engine output: path, evidence
  quality, quadrant, `proposed_levers`, `requires_approval`, `current_return_terms`,
  `refund_due_by` (notice + 14 days on the withdrawal path, null on the guarantee path:
  section 19), `review_at` and the coupon rule application. It adds the
  human outcome and the version chain: `version`, `revision_trigger`, `supersedes`,
  `superseded_by`, and `as_of`, the time the view was served.
- **States.** `proposed`, `approved`, `overridden`, `rejected`. The last three are the policy
  `DecisionState` values plus `overridden`.
- **The decision day is the day of the act.** `decided_on` is no later than the UTC day of
  `created_at`; every later version (a review or a recompute) is decided on that day, and the
  person's act creates a review version (`approved_at` equals `created_at`). The review date,
  the no-early-renewal rule and the 180-day maximum all count from `decided_on`, so moving it
  cannot stretch a restriction (section 11, D1; section 12, E4).
- **A restriction is approved before its review date** (UTC days). A proposal still waiting on
  its review day is stale: the API answers 409 `conflict` and the case is recomputed. A cluster
  coupon application is likewise confirmed before its review date, which is after the day it is
  proposed (section 12, E4).
- **The cluster coupon block** (`coupon_rule`) applies to the cluster given to the engine
  (`inputs.cluster_id`), and its linked accounts include the case customer; on the case card
  they are linked accounts shown in `linked_accounts`. The policy `CouponRuleApplication` names
  the cluster too, and `policy.legal_guard` refuses a block without one
  (`COUPON_CLUSTER_MISSING`). The block carries the `state` and `in_force` of its application
  when the decision is served: proposed with a proposed decision, confirmed by the person who
  acted or reversed afterwards, in force while confirmed, before the review date and while the
  decision is in force (section 12, E2 and E26).
- **`levers` are the levers of the current state.** They are the proposal while proposed or
  approved, the operator's choice when overridden, and none when rejected. `requires_approval`,
  `review_at` and `future_terms` refer to them: `requires_approval` is exactly "restrictive
  levers, or Q3 and Q4 while the engine's levers stand", and a review date exists exactly for a
  restriction.
- **`in_force`** is true when the levers apply to future orders at `as_of`: approved or
  overridden, or proposed without a need for approval (the Q1 upgrade of Giulia applies by
  itself and stays in the log). It is false once a later version supersedes the view, and from
  the review date on, counted in UTC days (`utc_day`): a restriction lapses unless a person
  renews it (US-607: no permanent label).
- **The legal guard runs on every view** (rules 3 to 5). `to_policy_decision(view)` gives the
  policy `Decision` and `ReturnRequest` the view stands for, with this exact mapping:
  - `proposed` and `approved` stay; `overridden` becomes `APPROVED` (a person took it);
    `rejected` stays `REJECTED`;
  - the quadrant is the engine's while its levers stand (proposed, approved) and `None` once a
    person replaced them (overridden, rejected): the guard judges the levers chosen;
  - levers, `requires_approval`, review date and approver are those of the current state; when
    approved or overridden, the approver has confirmed the coupon rule application.
- **`POST .../decision`** takes the body of spec 19.B plus `decision_id` and `override_category`;
  `message` is required for approve, override and reject and null for lift and renew.
  `approve`, `override` and `reject` act on a proposed version:
  - `approve` confirms the proposal. Levers, if sent, must equal it. The approver also confirms
    the cluster coupon application (`policy.engine.approve`).
  - `override` sets other levers with a reason (at least 3 characters) and a category. The
    legal guard checks them, giving 422 `legal_guard` on a violation. Restrictive override levers
    need a review date.
  - `reject` discards the proposal, with a reason and a category: no lever applies, the order's
    terms stay, and the return itself is never refused (rule 3).
  - `lift` and `renew` (US-607) are the two outcomes of the review of a restriction: the latest
    version, approved or overridden, with restrictive levers (409 `conflict` otherwise). Each
    creates the next version (trigger `review`), which supersedes the restriction; the earlier
    version stays in the log. **A review version is `policy.engine.decide()` on the latest scores
    at the day of the review**: its `inputs`, `decided_on`, `quadrant` and `proposed_levers` are
    the engine's fresh output, never a copy of the earlier version. Then:
    - `lift` ends the restriction, before or after its review date: at the review (category
      `periodic_review`) or as a rectification the customer asked for. It takes a reason and a
      category and no levers; the new version is overridden to the standard levers. If the
      restriction had a cluster coupon application, the lift reverses it (the person who
      lifted, at the time of the lift, with the lift's reason) and the result carries it.
    - `renew` keeps a restriction, at or after its review date (409 before: no early renewal,
      so no permanent label). It takes no levers, no reason and no category, like `approve`: the
      person approves the fresh proposal, which gets a new review date and, if its levers block
      the cluster's coupons, a new application confirmed by that person. 409 if the fresh
      proposal is not a restriction: there is nothing to renew, and the person lifts it.
  - Every action writes one audit entry, about the decision and its return, at the time of the
    act (`approved_at`), with the decision's reason and its rules, model and assumption versions.
    For approve, override and reject its `before` is the engine's proposal of the version
    (`proposal_audit_state`). `DecisionResult.coupon_application` is the cluster coupon
    application the act changed: the decision's own, confirmed by approve, override or renew of
    levers that block the cluster's coupons, or the restriction's, reversed by a lift; otherwise
    null.
  - A lift or a renewal result carries `superseded`: the restriction it supersedes, as served
    after the act (superseded, not in force, its coupon application reversed by a lift). The
    `before` of its audit entry is that version, so the early-renewal rule rests on a verified
    review date (section 12, E5).
  - approve, override and reject also send one outbox message to the customer, at the time of
    the act, in the chosen language (`message.lang`). `message.sentence_ids` optionally chooses
    other phrasebook sentences (ADR-14 decision 8): never free text. Without them the API uses
    the draft's selection when it is allowed for the decision acted on, otherwise the fallback
    selection of `llm.templates` (`default_selection_for`). The API composes the message with
    `llm.phrasebook.render_selection` for the decision as it stands, and refuses a selection
    that is not allowed for it (422). When the decision carries a diminished-value claim, the
    message states the refund it leaves and the deduction (below, Inspection).
  - **lift and renew send no customer message** (`message` null). Decided on 13 Oct 2026
    (section 12, E27; spec 19.B): the customer sees the terms of future orders at checkout,
    before each purchase (US-403), so a lift or a renewal reaches the customer there.
  - The clause is the guarantee clause on the guarantee path, the withdrawal clause when the levers
    are restrictive, and none otherwise (`llm.legal_clause.required_clause`).
  - An override never becomes training data: training uses the synthetic labels (spec 8.4).
- **Review (US-607).** `QueueQuery.review_due=true` lists the cases whose latest decision is an
  approved or overridden restriction at or past its review date; `QueueItem.review_due` marks
  them. A person renews the restriction (action `renew`) or lifts it (action `lift`).
- **Cluster coupon applications (US-402, LEG-09, US-607).** A `CouponRuleApplication` is served
  with `as_of` and `in_force`: in force when confirmed, not reversed, and before its review date
  (UTC days), which is the review date of the decision that applied it. It lapses at that date
  like the restriction. The API reverses it, in the same transaction, when the version that
  applied it is lifted or superseded by a recompute (the person who added the evidence or
  disputed the link, with their note or reason); a person also reverses it on request
  (`POST /api/coupon-rules/applications/{application_id}/reverse`). `as_of` and `in_force` are
  computed when served, not stored.
- **Draft (ADR-14 inside the API).** A `DraftView` is the output of `llm.generate` for the
  engine's decision of its version (`proposal_decision`: path, quadrant, proposed levers), made
  at intake or on request: `llm.case.build_case` builds the llm_case with its code-built facts,
  the model (or the template) answers, `llm.validator` accepts the answer, and
  `DraftView.build` stores it:
  - the echo of the decision (rule 1);
  - `case_facts`: every fact of the llm_case `{id, entity, metric, value, period, nature}` as
    stored at generation time (section 12, E10 and E11);
  - the operator statements, each `{label, fact_ids, text, facts}`: `facts` are the cited
    facts, equal to those of `case_facts`, so entity, metric, value and period come from code,
    never from the text;
  - the customer message `{sentence_ids, legal_clause_kind, texts}`: the phrasebook ids the
    model chose, the fixed clause appended (or none), and per language the template fields and
    the text `llm.phrasebook.render_selection` composed;
  - `provenance`: the provenance of the engine inputs the facts were built from.

  `_check_draft` checks that `case_facts` are the facts `llm.case.build_case` writes for the
  version: ids in order, one fact per metric, the nature of the inputs, and each metric with
  the entity, period and value build_case gives it (value, factors and cluster "as of" and "12
  months to" the day of the customer snapshot the engine read, `inputs.snapshot_on`, section
  19; the risk "as of" the day it was scored at the request, `inputs.scored_on`, section 20;
  the decision day before 1.7.0), the days since delivery over the delivery and
  notice days, the cluster only for a customer in a cluster, no lever (ADR-14 decision 9;
  section 14),
  score factors that may be shown and are never customer value, at most three). Where the
  response shows the score factors and the cluster size (the case card, a recompute), the facts
  must equal a rebuild with `llm.case.build_case` (`expected_case_facts`). It then runs
  `llm.validator.validate` on the stored output against exactly the case `llm.generate`
  validated (all its facts, the case and cluster ids, the decision), so the stored draft and
  the generation get one verdict, and renders the messages again. A case that is not scored
  has only the days since delivery and no `SCORE` statement (rule 4, US-307). A score factor
  of the cluster is a fact of the cluster only for a customer in a cluster
  (`llm.case.factor_entity`): an account alone has its cluster features computed on itself.
- **Messages and template fields (rule 10, ADR-08).** There is one renderer, in `llm/`: the
  fields are `llm.messages.template_fields` of the decision ({terms} when the levers change
  the terms of future orders, {deadline} on the withdrawal path, {amount} when the refund is
  known; null otherwise), the text is `llm.phrasebook.render_selection` and the clause
  `llm.legal_clause.required_clause`. `check_rendering` compares a stored message with them;
  the API has no renderer of its own. {deadline} carries its article in Italian ("il 12
  ottobre 2026", "l'11 ottobre 2026", section 12, E22). An outbox message carries its subject
  (`llm.phrasebook.subject`, a fixed text with the return id), its sentence ids, the fields of
  its language, the refund the {amount} field states (a `Metric`) and the body. It states a
  refund exactly when the decision carries a diminished-value claim: the price of the line less
  the claim, with the provenance of the claim, and {deduction} with its reason (section 12,
  E12, E13 and E17).
- **`GenerationInfo`** gives the path of `llm.generate`: `model`, `regenerated` or `template`,
  the fallback reason of a template (`unavailable`, `decision_echo`, `invalid_twice`), the model
  calls and the `llm.validator` codes found on the way. It also gives `llm_mode` (vllm or mock)
  and `served_from` (live or cached). The audit entry of a draft generation (`draft_generated`,
  `draft_regenerated`, entity type `llm_generation`) records the same in a typed
  `GenerationAuditState`, and `llm_generations` stores the llm_case sent with its facts.
- **Regeneration** returns plain JSON, not a stream: the validator checks the whole output before
  the UI sees it. It takes no free-text instruction (NFR-11) and no tone: `llm/` has none to
  apply (section 12, E14). `RegenerateResponse` holds the
  latest decision version with the draft, and the model checks the draft against it as on the
  case card: the echo, the legal clause and the template fields.

**Decision history (US-604).** `GET /api/decisions/{decision_id}` serves any version, the latest
or an earlier one, as `DecisionRecord {decision, alternatives}`: the inputs with their
provenance, the alternatives with their net benefits, the outcome, the versions of rules, models
and assumptions, and `superseded_by`. The audit entries about a decision name these ids, and the
mock routes serve every version a version chain of the examples points to.

**Recompute (US-310, F7).**

- Evidence of kinds `household_members`, `shared_payment_explained` and `collective_address`
  names the links it explains. Those links leave the cluster features.
- `customer_contact` and `other` are recorded but change no feature.
- A dispute marks the link (one dispute per link) and recomputes every open case of the cluster.
  A dispute sent from a case card names the case and the `decision_id` shown.
- The response holds the before and after `CaseSnapshot` (with their state), the new decision
  version (version + 1, trigger `evidence_added` or `link_disputed`, superseding the one
  before, decided on the day of the recompute), its regenerated draft and
  `coupon_application`. `after` shows the figures of the new version: the risk given to the
  engine with the provenance of its inputs, the approval flag, the evidence quality and a
  cluster size exactly when the engine had a cluster; a dispute removes an active link and
  evidence never adds one (section 12, E6). The draft's facts are rebuilt from `after`. The
  coupon application is the cluster coupon application of
  the version superseded, reversed, when that version blocked the cluster's coupons with a
  person's confirmation (approved or overridden); otherwise null.
- Earlier versions keep their state for the audit. Only the latest is actionable, and the
  in-force levers of a customer are those of its latest in-force version.

**Checkout (US-401..403, ADR-05, E2E-02).**

- **Evaluate** returns 19.B with these changes:
  - `size_advice` is structured: `specific` with a size, `measurement_guide`, or
    `no_reliable_recommendation`. It is present exactly when `p_return >= p_return_threshold`;
  - the terms carry `coupons_enabled`;
  - it adds `base_terms`, `terms_source` (base or a decision id), `review_at`, `coupon_block`,
    `shown_at`, `model_version` and one `provenance` for its numbers;
  - the customer texts (the size advice, the coupon block) are `{it, en}` pairs of fixed,
    reviewed texts of `llm/phrasebook.py`; the coupon block uses the neutral wording of the
    transparency notice and never names the linked accounts or the cluster (rule 11, section
    12, E25). Only the customer of the case sees that wording ("your previous returns"); every
    other account of the cluster sees a text that gives no cause, with the review date of the
    block (section 19).
- **Terms and their source.** `terms_source: base` shows exactly the base terms, with no review
  date and no quadrant. Terms less generous than the base come from a decision in force and carry
  its review date. A restriction lapses at its review date, so its terms are shown only before
  it: `review_at` is after the UTC day of `shown_at`, and so is the review date of
  `coupon_block`. Both review dates are at most 180 days (`MAX_REVIEW_DAYS`) after that day. The
  terms snapshot follows the same rules and stores the base terms and the review date; the
  database checks them (`ck_terms_snapshots_review_after_shown` compares `utc_date(shown_at)`
  with `review_at`, `ck_terms_snapshots_review_within_max` bounds it).
- **The API stores the shown terms** before it answers, as a `terms_snapshots` row with no order.
  `/confirm` binds that row to the new order, so the terms shown are the terms stored. A
  restriction lapses at its review date also between evaluate and confirm: `/confirm` on or
  after the UTC review day answers 409 `conflict` and the client evaluates again
  (`ck_terms_snapshots_review_after_confirmed`). `/confirm` also answers 409 `conflict` when
  the decision the terms come from (`terms_snapshots.source_decision_id`) is no longer the
  version in force for the customer: superseded by a lift (possibly the rectification the
  customer asked for) or by a recompute between evaluate and confirm. The API checks it in the
  confirm transaction (section 12, E8).

**Inspection (US-501..503).**

- `POST /api/inspections` returns the suggested checklist (tag, visible damage, photo quality;
  "not observable" is valid). Each item has the model's confidence, flagged
  `confidence_is_calibrated: false`.
- When the system is unsure there is no proposal: `manual_review` with its reason.
- A compensation proposal holds only if Ahmed confirms the observations it rests on.
- `/confirm` recomputes the claim from the confirmed observations and an approved rule. A claim
  needs confirmed visible damage, never vision alone.
- The claim is attached to the decision (`diminished_value_claim`, checked by the legal guard),
  on the price of the returned line (`line_price`). The message to the customer states the
  refund left after the claim ({amount}: the price of the line less the claim) and the amount
  withheld with its reason ({deduction}: "Abbiamo trattenuto {deduction} per il minor valore
  rilevato al controllo."; section 12, E9, E12 and E17; section 14). It offers no other size.
- **No unapproved restriction reaches the customer (rule 5).** The confirmation sends its
  message exactly when a claim applies and the decision does not await a person's approval.
  While a Q3 or Q4 proposal waits for Sara, the message would state its restrictive terms, so
  there is none: the refund and the deduction are stated by the message of the approval, the
  override or the rejection, which carries the claim (section 12, E1).

**Matrix and simulator (US-601, US-602, ADR-04).**

- The matrix returns every scored customer as a `MatrixPoint`: risk, value, evidence, quadrant,
  cluster, returns and welcome coupons. It also returns the thresholds as Metrics with their basis,
  and quadrant totals computed on the points (the validator recomputes them with
  `policy.engine.quadrant_for`).
- Insufficient evidence stays outside the quadrants: such a point has no risk and no quadrant.
  A customer with a case card has the risk, value, cluster, returns and coupons of the card.
  About 20,000 points: the API gzips responses.
- Assumption sets are versioned. A PUT creates the next version from `base_version` (409 if it is
  not current). The response holds the net benefit, the four sensitivity scenarios of
  `policy.simulator.sensitivity`, and the history.

**KPI and fairness (US-603, US-605, rule 12).**

- False positives need labels. They come from the offline evaluation report
  (`evaluation_report_id`, nature synthetic); the API never reads `data/truth/`.
- `net_benefit_realized` stays null until outcomes are observed; then it prices the week's
  observed quantities with the stored version and the unit costs of `net_benefit_simulated`.
- Fairness reports false positives by segment for each dimension, with `warning` exactly when
  the gap exceeds `max_gap_threshold` (NFR-04: 5 points).

**Audit (US-604).**

- `audit_log` is append-only.
- Actions in `HUMAN_ONLY_ACTIONS` are never logged with the system role. The actions in
  `REASON_REQUIRED_ACTIONS` carry a reason.
- The entries about a decision version have `entity_type: decision`, name the return and have
  an action of `DECISION_AUDIT_ACTIONS`: `decision_proposed`, `decision_approved`,
  `decision_overridden`, `decision_rejected`, `restriction_lifted`, `restriction_renewed` and
  `case_recomputed`. Their `before` and `after` are `DecisionAuditState` (id, version, state,
  quadrant, risk as a `Metric`, lever ids, approval flag, review date, approver, category,
  coupon application): approve, override and reject change a proposed version; a lift, a
  renewal and a recompute create the next version, so `before` and `after` have different ids.
  A state has a review date exactly when it is a restriction, and a coupon application exactly
  when its levers block the cluster's coupons. A renewal entry is made at or after the review
  date of the version it renews, and sets a new one after its day, within the maximum.
- The entries about a draft generation (`draft_generated`, `draft_regenerated`) have
  `entity_type: llm_generation` and a typed `after` (`GenerationAuditState`): the path of
  `llm.generate`, the fallback reason, the model calls and the validator codes.
- A decision state is a state a version can have: a person acted exactly when it is not
  proposed, a category exactly for an override or a rejection, no lever after a rejection. A
  human act is by the approver of its `after`, approve, override and reject keep the version
  number, a review date is within the maximum of the day of the entry, and a lift ends the
  restriction (section 12, E5).
- Any other `before` and `after` is a free JSON object whose numbers are `Metric`s (an
  inspection's compensation in euros, for example): a plain number, a `Decimal` or a number
  written as text is refused (rule 11, section 12, E7).
- CSV export uses `AUDIT_CSV_COLUMNS`, with JSON cells for `before`, `after` and `model_versions`.

**Agent (US-701, US-702, spec 8.8).**

- `POST /api/agent/ask` streams server-sent events. Each event is written as `event: <type>`,
  `id: <seq>` and `data: <event JSON>`, followed by a blank line (`sse_frame`). The UI reads the
  stream with fetch, because EventSource only does GET. The examples hold each stream twice:
  `agent_stream.<name>.json`, the events for the model, and `agent_stream.<name>.sse`, the
  bytes on the wire, which the mock routes serve.
- **Order.**
  1. `tool_call` and `tool_result` pairs for steps 1..n, with n <= 5 and the five read-only tools
     `get_case`, `get_orders`, `get_cluster`, `get_score_factors` and `get_policy_text`.
  2. `token` deltas.
  3. Exactly one `answer`, `refusal` or `error`. The answer carries the evidence consulted, the
     evidence missing and the references.
  4. `done`.
- A request to change something gets a `refusal` with the reason `write_request`.

**Benchmarks and telemetry (US-902, US-905).**

- A benchmark is one of:
  - `measured`: real results with a measurement time;
  - `pending`: no result;
  - `illustrative`: simulated results whose origin says "Illustrative". It exists only for
    examples and mock servers, and the UI must label it.
- Telemetry in cached mode has no GPU or LLM numbers and states why.

### 5. Roles (spec 4: three demo users, no authentication)

`DEMO_USERS`: sara (operator), luca (manager), ahmed (warehouse). Actor fields take a lowercase
id and never `system` for a human act (rule 5). Each endpoint lists its `roles`; the API answers
403 `forbidden_role` otherwise. The checkout is called by the simulated shop, with no role check.
On the public read-only demo (`PUBLIC_READ_ONLY=on`, section 15) every write answers 403 but the
agent and the checkout evaluation, which are rate limited per client.

| Action | Operator | Manager | Warehouse |
| --- | --- | --- | --- |
| Approve, override, reject, lift, renew; regenerate; evidence; dispute; ask the agent | yes | yes | no |
| Create and confirm inspections | no | no | yes |
| Create and update catalogue tasks | yes | yes | no |
| New coupon rule version; PUT simulator assumptions | no | yes | no |
| Reverse a coupon rule application | yes | yes | no |
| Every GET | yes | yes | yes |

### 6. Persistence (spec 7, `contracts/sql.py`)

Tables:

- `return_requests`;
- `decisions`: one row per version, with the engine inputs and their provenance
  (`inputs_provenance`), the day of the customer snapshot the engine read (`snapshot_on`, 1.7.0),
  the day the risk was scored (`scored_on`, 1.8.0), the outputs (`refund_due_by` null on the guarantee path, 1.7.0), the alternatives, the human
  outcome, the version chain and the versions;
- `audit_log`, `outbox` (with the phrasebook ids, the template fields, the refund stated with
  its provenance, and the rendered body);
- `terms_snapshots`: shown, then confirmed; with the base terms and the review date;
- `evidence_additions`, `link_disputes`, `inspections`, `catalog_tasks`;
- `simulator_assumptions` and `coupon_rules`: versioned, with a `seq` chain;
- `coupon_rule_applications`;
- `llm_generations`: the llm_case sent (with its code-built facts), the output, the path of
  `llm.generate`, the fallback reason, the model calls and the validator codes (no tone);
- `demo_reset` (1.5.0): one row, the snapshot instant of the reset, the wall time the demo clock
  started and the golden cases of `GET /api/demo/cases`;
- `id_counters` (1.5.0): the next number of each kind of sequential id, incremented in the
  transaction that writes the id (section 15).

Column types:

- **Timestamps** are `UtcDateTime`: TIMESTAMP WITH TIME ZONE that refuses a naive datetime,
  stores UTC and reads back an aware UTC datetime on both databases, so rows validate as API
  models.
- **JSON** columns are JSONB on PostgreSQL and store a Python `None` as SQL NULL
  (`none_as_null`), so the `IS NULL` rules of the CHECKs see it.

`tests/test_sql_contract.py` maps every `DecisionView`, `TermsSnapshotView` and `OutboxMessage`
of the examples to a row.

Named CHECK constraints (`ck_<table>_<rule>`) carry the invariants that SQL can see. Each CHECK
is one rule, and a rule on a state is written as an implication, so a row that breaks one rule
breaks exactly one CHECK. PostgreSQL reports the first violated CHECK in name order and SQLite in
table order; with one violated CHECK both name the same one. The tests prove it: each invalid row
is refused with its CHECK, and accepted once that one CHECK is removed.

| Rule | Constraints |
| --- | --- |
| The guarantee path is unscored and unrestricted (rule 4) | `ck_decisions_guarantee_unscored` |
| The 14-day refund deadline is a withdrawal's, and only a withdrawal's (rules 3 and 4, 1.7.0) | `ck_decisions_refund_due_on_withdrawal` |
| The engine reads a customer snapshot made by the decision day (rule 7, 1.7.0) | `ck_decisions_snapshot_by_decision` |
| A risk, and only a risk, is scored at the request: after that snapshot, by the decision day (rule 7, 1.8.0) | `ck_decisions_risk_scored_by_decision` |
| Insufficient evidence has no quadrant (US-307) | `ck_decisions_insufficient_no_quadrant` |
| Q3 and Q4 need approval (rule 5) | `ck_decisions_q3_q4_need_approval` |
| A restriction, and only a restriction, has a review date after the decision, within 180 days (US-607) | `ck_decisions_restriction_has_review`, `ck_decisions_review_only_restriction`, `ck_decisions_review_after_decision`, `ck_decisions_review_within_max` |
| The decision day is no later than the UTC day of the version's creation; every later version is decided on that day (D1, E4) | `ck_decisions_decided_by_creation`, `ck_decisions_new_version_decided_on_creation` |
| A person acts on a version after it exists; the act creates a review version; a restriction is approved before its review date (E4) | `ck_decisions_approved_after_creation`, `ck_decisions_review_approved_at_creation`, `ck_decisions_approved_before_review` |
| A person acted exactly when the state is not proposed; with the review rules, an approved or overridden restriction has a person as approver and a review date (rule 5) | `ck_decisions_acted_has_approver` |
| An override or rejection has a reason and a category, an approval none | `ck_decisions_override_has_reason` |
| A version created by a review lifts the restriction (overridden, not restrictive) or renews it (approved restriction) (US-607) | `ck_decisions_review_outcome` |
| Version 1 is initial; later versions supersede another | `ck_decisions_version_chain` |
| Applying the coupon rule to a cluster needs a person's confirmation and stays reversible (LEG-09) | `ck_coupon_rule_applications_confirmed_by_person`, `ck_coupon_rule_applications_always_reversible` |
| A coupon block names the cluster of its decision, and an application blocks that cluster only (LEG-09, E2) | `ck_decisions_coupon_application_has_cluster`; the composite foreign key `(decision_id, cluster_id)` of `coupon_rule_applications` to `decisions` (unique key `uq_decisions_decision_id`) |
| A coupon application's review date is at most 180 days after it is confirmed, or proposed (D2), after the day it is proposed, and it is confirmed before it (E4) | `ck_coupon_rule_applications_review_within_max`, `ck_coupon_rule_applications_review_after_proposed`, `ck_coupon_rule_applications_confirmed_before_review` |
| The general coupon rule is approved by a person | `ck_coupon_rules_approved_by_person` |
| Compensation needs a photo, a confirmation and a rule (LEG-05) | `ck_inspections_compensation_evidence` |
| No proposal when unsure (US-503) | `ck_inspections_unsure_no_proposal` |
| No window below 14 days (LEG-01) | `ck_terms_snapshots_window_legal` |
| A coupon block shown turns the coupons of the terms off, carries its review date, shown before it and within 180 days (1.5.0, H12) | `ck_terms_snapshots_coupon_block_review`, `ck_terms_snapshots_coupon_block_off`, `ck_terms_snapshots_coupon_review_after_shown`, `ck_terms_snapshots_coupon_review_within_max` |
| One diminished-value claim per return (1.5.0, H13, H19) | the partial unique index `uq_inspections_one_claim_per_return` |
| One demo reset; counters never below 0 (1.5.0) | `ck_demo_reset_single`, `ck_id_counters_non_negative` |
| A snapshot gets its order at confirmation, not before | `ck_terms_snapshots_confirmed_with_order` |
| Base terms are the base terms, with no review date; less generous terms come from a decision with a review date (US-403, US-607) | `ck_terms_snapshots_base_terms`, `ck_terms_snapshots_base_no_review`, `ck_terms_snapshots_restriction_has_review` |
| Terms from a restriction are shown before its review date, in UTC days, within 180 days of the day shown, and bound to an order only before it (US-607, D2, D3) | `ck_terms_snapshots_review_after_shown`, `ck_terms_snapshots_review_within_max`, `ck_terms_snapshots_review_after_confirmed` |
| Human actions are never logged as the system | `ck_audit_log_human_action` |
| Reasons where the log needs them | `ck_audit_log_reason_required` |
| Decision entries, and only they, are about a decision | `ck_audit_log_decision_entity` |
| Every simulator input is non-negative; there is no refund column (rule 9) | `ck_simulator_assumptions_non_negative` |
| A refund stated in a message carries its provenance and is not negative (rule 11) | `ck_outbox_refund_provenance`, `ck_outbox_refund_non_negative` |
| A generation follows the paths of `llm.generate`: model on the first call, regenerated on the second, template with its fallback reason (spec 8.7) | `ck_llm_generations_path_calls`, `ck_llm_generations_template_fallback`, `ck_llm_generations_calls_range` |

- **Append-only tables.** `audit_log`, `simulator_assumptions` and `coupon_rules` refuse UPDATE
  and DELETE through triggers, plus TRUNCATE on PostgreSQL.
- **What the database cannot see.** JSON content and cross-table rules (levers against the legal
  guard, the draft echo, the template fields) are checked by the API with `policy.legal_guard` and
  the model validators. Rules that depend on the time a row is served (`in_force` of a decision
  or a coupon application) are computed when served.
- **Dates against timestamps.** `utc_date(column)` compiles to `date(column)` on SQLite (UTC
  text) and to `(column AT TIME ZONE 'UTC')::date` on PostgreSQL, so a CHECK counts the UTC day
  whatever the session `TimeZone`. `days_after(date, n)` compiles to `date(date, '+n days')` on
  SQLite and to `(date + n)` on PostgreSQL.
- **SQLite.** `enable_sqlite_foreign_keys(engine)` runs before `create_all`.
- **Tests.** `tests/test_sql_contract.py` runs on SQLite. With `RETURNRING_TEST_POSTGRES_URL` it
  also runs on PostgreSQL (run on PostgreSQL 16.15 for every round of this ADR); otherwise the
  PostgreSQL DDL is compiled and inspected.

### 7. Examples (mock data of the UI)

- **Demo clock and the four cases.** The examples use 2026-09-30, the end of the synthetic year.
  The four cases of spec 4 are:
  - `case_card.giulia`: Q1 upgrade without approval;
  - `case_card.marco`: C-MARCO-1, Q4 with exactly the 6 levers of `decide()`, cluster CL-000417 of
    7 linked accounts with 21 links weighted by the formulas of 8.3, and the coupon application to
    confirm;
  - `case_card.rossi` and `case_card.rossi_card`: Q2, a card shared only in the second;
  - `case_card.elena`: guarantee path on V-JKT-DEF, no quadrant, free return shipping, an
    anomaly with its catalogue task.
- **A card for every other queued return.** `case_card.insufficient` (US-307), `chiara` (Q3
  through a card shared once; disputed), `andrea` (Q1; rejected), `martina` (Q3; overridden),
  `valentina` (Q2; inspected with a claim), `simone` (Q2; manual review), `francesca` (wrong
  item: guarantee), `lorenzo` (a Q3 restriction approved in June, past its review date:
  lapsed and due for review; lifted in `decision_result.lift`, renewed in the scenario
  `renewed`) and `earlier` (RET-000077, DEC-000077-1: a Q4 of cluster CL-000655 approved on 29
  September, whose coupon block Luca reversed on request: the decision is in force, its block
  is not).
- **The other demo examples.** The override, the rejection, the lift, the legal-guard refusal,
  the evidence and dispute recomputes, the six versions of the three version chains (Rossi card,
  Chiara, Lorenzo) and every decision the audit log and the coupon applications name (Marco
  approved, Andrea rejected, Martina overridden, Valentina with her claim, and DEC-000077-1, an
  earlier Q4 of cluster CL-000655 whose coupon block was reversed on request) served by
  `GET /api/decisions/{decision_id}`, the queue filtered for approval
  and for review, both checkouts, the inspections and the agent streams (the three questions of
  US-702 and a refused write). Every version a card, a request, a terms source or an audit
  entry names is served by `GET /api/decisions/{decision_id}`. The renewal of Lorenzo (the
  other outcome of the review the lift shows, with the same next version id) is served under
  the scenario `renewed`: the request, the result and the record of DEC-000088-2. A lift of
  Marco's Q4 restriction with its coupon application is built by `review_result` in the tests
  and not served (in the demo story Marco's restriction stays in force, as the checkout shows).
- **Errors.** An `ErrorResponse` example for every error code, and a mock route for every
  error status the endpoints declare: 403 (Ahmed posting a decision), 404, 409 (approving
  DEC-000102-1 again under `conflict`, renewing it before its review date, confirming Marco's
  checkout under `conflict` once its terms no longer apply, a stale decision under
  `stale_decision`), 422 (`invalid_request`, `legal_guard`), 429 (the agent under
  `rate_limited`) and 503 (regeneration and the agent under `cached`).
- **The log and the outbox are whole.** `audit_page` is every act the examples show, in time
  order (the intake and the draft of every case, approvals, overrides, rejections, the lift,
  recomputes, the regeneration, messages, checkouts, inspections, catalogue tasks, coupon rules
  and the reversal, the assumption update); `outbox` holds every message sent.
- **The anomaly of Elena** is open at the demo clock; the scenario `task_opened` serves the
  list once Luca has opened CT-000012 from it.
- **How they are built.** By code from `policy.engine`, `policy.simulator` and `llm/`. Every
  draft goes through `llm/` as the API makes it: `llm.case.build_case` of the engine decision,
  `llm.generate.generate` with the mock model (`MockClient`, given a scripted answer before its
  default one for some cases), `llm.validator`, then `DraftView.build`, which composes the
  messages with `llm.phrasebook`. The examples hold every path: `model` (Giulia and Marco with
  richer scripted statements, the others with the mock's default answer), `regenerated`
  (Elena: a first answer with ungrounded numbers) and `template` (the insufficient case and the
  dispute recompute: an answer that misreads the decision). Outbox messages are composed by
  `OutboxMessage.compose`. Every provenance is synthetic or simulated and starts with
  "Contract example: ". Golden cases are never accuracy (US-906). A matrix point of a customer
  with a card is that card.
- **`index.json`** maps each request to its example for a mock server: method, concrete path,
  query example, request example and `scenario`. The scenario is `default` unless the request
  names another one in the `X-Mock-Scenario` header (for example `stale_decision`, `cached`). A route's
  `media_type` is the type of its response: an error is `application/json`, an agent answer
  the `.sse` file. Drafts in examples are `llm_mode: mock`: outputs of the mock model, not
  Gemma output.

### 8. Changes to earlier contracts

- **Removed v1 files.** `contracts/json/decision_request.schema.json`,
  `contracts/json/checkout_evaluate_response.schema.json` and their examples are removed. They
  are superseded by the generated `contracts/json/api/` files. The lever array of
  `api/decision_request` is the catalogue's `lever_list_json_schema()`, so the ADR-13 and ADR-14
  tests now read it from there;
  the tests that an override needs a reason and that the checkout window never goes below 14 days
  run against the generated schemas.
- **Spec 19.B decision body.** Adds `decision_id` and `override_category`. `message` is required
  for approve, override and reject, null for lift and renew.
  The actions are approve, override, reject, lift and renew; "edit" is gone, because editing the
  message is a choice of phrasebook ids (`message.sentence_ids`, section 11) and editing levers
  is an override.
- **Spec 19.B checkout response.** See section 4.
- **Policy types.** `policy.types.DecisionState` is unchanged. The API state `overridden` maps to
  an approved policy decision on the operator's levers without the engine's quadrant, and
  `rejected` to a rejected one without it (`to_policy_decision`, section 4). The first version of
  this ADR kept the quadrant, and the guard then refused the contract's own override of a Q3
  proposal to the standard levers (`APPROVAL_MISSING`).

### 9. Amendments after review (13 Oct 2026, contract 1.1.0)

| Finding | Change |
| --- | --- |
| JSON null in nullable JSON columns broke the inspection CHECKs | `none_as_null` on JSON and JSONB |
| SQLite read timestamps back naive | `UtcDateTime` |
| The documented override mapping failed the legal guard | `to_policy_decision`; the guard runs in `DecisionView` |
| Restrictions never lapsed or came up for review (US-607) | trigger `review`, action `lift`, `review_at` and `review_due` in the queue, lapse at the review date |
| Earlier decision versions could not be served (US-604) | `GET /api/decisions/{decision_id}`; version chain on `DecisionView`; typed audit payloads |
| Some CHECKs failed together, and PostgreSQL named another one | one rule per CHECK, implications; tests prove exactly one CHECK per invalid row |
| Plain numbers without a covering provenance | provenance on the queue, recompute, dispute, inspection, risk view and decision record; revision risk is a Metric |
| Restrictive checkout terms without a decision or review | `base_terms`, `review_at` on snapshots; model rules and CHECKs |
| Insufficient matrix points carried a risk | `risk` null exactly then |
| Rossi matrix values differed from their cards | matrix points of carded customers come from the cards |
| Statements with numbers but no entity, metric, value or period | the digit rule |
| Template fields not tied to the decision | the renderers in the contract; fields checked in drafts, results and the outbox |
| 403 and 503 missing from the endpoint table | `roles` per endpoint; 403 and 503 declared |
| Assumption labels did not identify the values | labelled derived sets with `based_on` |
| Ambiguous mock routes | `scenario` key; a card for every queued return; manual review routed |
| The decision schema accepted an override without a reason | JSON Schema `if`/`then` |
| A dispute did not name the decision shown | `decision_id` with `return_id` |
| Flow F1 step 1 had no contract | section 4, "Return intake" |

### 10. Amendments after the second review (13 Oct 2026, contract 1.2.0)

| Finding | Change |
| --- | --- |
| B1: restrictions never lapsed at checkout, in terms snapshots or in cluster coupon applications | `review_at` after the UTC day of `shown_at` for checkout terms, `coupon_block` and snapshots (model rules and `ck_terms_snapshots_review_after_shown`); `CouponRuleApplication.as_of` and `in_force`, lapsing at the review date; `CouponRulesResponse.as_of`; a lift reverses the application and `DecisionResult.coupon_application` carries it |
| B4: the review flow was incomplete and contradicted itself | lift "before or after" the review date; action `renew` and audit action `restriction_renewed` (human only, no reason, like approve); a review version is `decide()` on the latest scores at the day of the review; `ck_decisions_review_lifts` becomes `ck_decisions_review_outcome`; `scores_refreshed` removed from `RevisionTrigger`: nothing produced it |
| B5: results and audit entries not tied to their decision; regenerated draft unchecked | `DecisionResult` checks the return and customer of the message, the return and the reason of the entry, and for a lift or a renewal that `before` is the version superseded; decision entries name their return, and a new version has a new id; the inspection confirmation and the recompute are tied to their decision and return the same way; `RegenerateResponse {return_id, decision, draft}` runs the draft checks |
| B6: decision inputs and audit risks without a covering provenance | `DecisionInputs.provenance` (stored as `decisions.inputs_provenance`), `DecisionRecord.provenance` removed; `DecisionAuditState.risk` is a `Metric`; a test walks every response model |
| B7: derived assumption labels and basis not enforced | `_check_alternatives` checks `<decision_id>/<kind>` and one basis; `KpiResponse` checks `kpi/<week>` and `kpi/<week>/realized` |
| B8: mock routes missing for versions named by `superseded_by` | `decision_record` examples and routes for every version of the three chains; a test resolves every `supersedes` and `superseded_by` |

The operator explanation statements (findings B2 and B3) were left to the merge with the LLM
layer (ADR-14); section 11 resolves them.

### 11. Amendments after the third review (13 Oct 2026, contract 1.3.0)

The API branch was merged with the branch of the LLM layer (ADR-14), and the drafts and messages
of the API now use `llm/` as their one source.

| Finding | Change |
| --- | --- |
| B2: the digit rule of `ExplanationStatement` made the live LLM path impossible; the API re-implemented message rendering | The API stores `llm_output` as `llm/` makes it: statements `{label, fact_ids, text, facts}` with the cited facts of the llm_case as stored at generation time; the customer message as phrasebook ids with the texts `llm.phrasebook.render_selection` composes in Italian and English, the template fields of `llm.messages` and the clause of `llm.legal_clause`. `deadline_text`, `terms_text`, `amount_text`, `template_fields`, `check_connective_text` and `required_clause` are gone from `apps/api/schemas.py`, and so is the "parse `LABEL: text`" fallback. `GenerationInfo` and the new typed audit record `GenerationAuditState` carry the path of `llm.generate` (`model`, `regenerated`, `template`), the fallback reason, the model calls and the validator codes; `llm_generations` stores them with the llm_case. The examples are built through `llm/`, and a test validates every example draft with `llm.validator` against its case and renders it again |
| B3: the cited value was trivially satisfiable | No value is parsed from text: `llm.validator` grounds every number of a statement on the facts it cites, and `_check_draft` checks the facts of risk, value and days since delivery against the engine inputs |
| D1: `decided_on` was not tied to the time of the act | `decided_on` is no later than the UTC day of `created_at`; a review version is decided on that day and created by the person's act. CHECKs `ck_decisions_decided_by_creation`, `ck_decisions_review_decided_on_creation` and `ck_decisions_review_within_max` |
| D2: unbounded review dates at checkout, in snapshots and in coupon applications | At most 180 days after the day shown (terms, coupon block, snapshot) or after the confirmation (application); CHECKs `ck_terms_snapshots_review_within_max`, `ck_coupon_rule_applications_review_within_max` |
| D3: a snapshot could be confirmed after its review date | Confirmed only before the review date (UTC day); `/confirm` answers 409 otherwise; `ck_terms_snapshots_review_after_confirmed` |
| D4: a lift could drop the coupon reversal | `DecisionAuditState` names a coupon application exactly when its levers block the cluster's coupons; `RecomputeResponse.coupon_application` carries the reversal a recompute makes, and `CaseSnapshot` has the state |
| D5: renewal entries were not checked on their own | A state has a review date exactly for a restriction; a `restriction_renewed` entry is made at or after the review date it renews and sets a new one after its day, within the maximum |
| D6: the time, the versions and the `before` of a result's entry were free | The entry and the message are dated `approved_at`; the entry has the decision's rules, model and assumption versions; for approve, override and reject `before` is the engine's proposal (`proposal_audit_state`) |
| D7: euro amounts in untyped audit payloads had no provenance | Every number of an untyped payload is a `Metric` (the inspection's `compensation`); a test scans the example payloads |
| D8: the realized KPI set was unchecked and contradicted `check_unit_costs` | The realized set names the simulated set's stored version and has its unit costs; `check_unit_costs` keeps the basis of a `/realized` set |
| D9: audit entries named versions the mock did not serve | `decision_record` examples and routes for DEC-000102-1, DEC-000107-1, DEC-000108-1, DEC-000110-1 and DEC-000077-1; the test resolves every decision id of the audit payloads and of the coupon applications |

Other changes of the merge: every lever array of the API schemas is
`contracts.levers.lever_list_json_schema()` (ADR-13, ADR-14), and the models check it with
`check_lever_objects`.

Known limits, reported as they are (section 12 decides the first, E27, and resolves the second,
E12 and E17, and the third, E10 and E11):

- **Lift and renew send no customer message.** The phrasebook has no reviewed sentence about a
  review, and its mandatory body starts with the return registration, whose refund deadline
  is that of a return closed long ago. A lift or a renewal therefore reaches the customer as the
  terms shown at checkout before each purchase (US-403). A review sentence needs the phrasebook
  review of ADR-14 decision 8.
- **The message after a diminished-value claim states the refund after the deduction** (the
  {amount} field, `OutboxMessage.refund`), not why it was deducted: the phrasebook has no
  reviewed sentence for it either. The claim, its rule and its photo are on the decision.
  `InspectionConfirmResponse` cannot check the refund against the price of the line, which it
  does not hold.
- **A stored draft is validated against the facts it cites.** The model check rebuilds a case
  from the cited facts, the return and cluster ids and the decision; metric names the
  statement mentions without citing them are not removed before numbers are read, so such a
  statement can be refused here although `llm.validator` accepted it against the whole case.
  Phase 4 runs the same check before it stores a draft, and uses the template when it fails
  (Consequences).
- **Lever arrays in the JSON Schemas carry a pydantic `title`** next to the catalogue schema;
  the tests compare them without it.

### 12. Round 4 (13 Oct 2026, contract 1.4.0)

The third re-review found no blocker and 27 findings (E1 to E27). Every major is fixed and so
are the minors, except E15, left open (below). Each probe of the review is a test that must be
refused (`tests/test_api_round4.py`, plus rows of `tests/test_sql_contract.py`,
`tests/test_legal_guard.py` and the `llm/` tests).

| Finding | Change |
| --- | --- |
| E1 (major): an inspection could announce an unapproved Q3 or Q4 to the customer (rule 5) | `InspectionConfirmResponse` sends a message exactly when a claim applies and the decision does not await a person's approval; the message of the approval, override or rejection then states the refund and the deduction (section 4, Inspection) |
| E2 (major): a coupon block was not tied to the case cluster | `DecisionView`: the block is on `inputs.cluster_id` and its accounts include the case customer; `CaseCard`: they are linked accounts shown; `policy.types.CouponRuleApplication.cluster_id`, set by `policy.engine.decide`, and the legal-guard code `COUPON_CLUSTER_MISSING`; SQL `ck_decisions_coupon_application_has_cluster` and the composite foreign key `(decision_id, cluster_id)` of `coupon_rule_applications` |
| E3: a cluster of one was stated as linked accounts; cluster facts unchecked | `llm.case.CLUSTER_FEATURES` and `factor_entity` (a cluster feature of an account alone is a fact of the customer); `build_case` refuses a cluster fact without a cluster; the template states a cluster only for a case with one, "this customer included"; the facts are checked as below (E10) |
| E4: approvals and confirmations after the review date; recompute versions on any day | A restriction is approved, and an application confirmed, before its review date (409 for a stale proposal); an application's review date is after the day it is proposed; every later version is decided on the day it is created. CHECKs `ck_decisions_approved_after_creation`, `ck_decisions_review_approved_at_creation`, `ck_decisions_approved_before_review`, `ck_decisions_new_version_decided_on_creation` (it replaces `ck_decisions_review_decided_on_creation`), `ck_coupon_rule_applications_confirmed_before_review`, `ck_coupon_rule_applications_review_after_proposed` |
| E5: audit states a view refuses; the `before` of a lift or a renewal unverified | `DecisionAuditState`: approver exactly when acted, category exactly for an override or a rejection, no lever after a rejection; `AuditEntry`: a human act by the approver of its `after`, approve, override and reject keep the version, a review date within the maximum of the entry's day, a lift not restrictive; `DecisionResult.superseded` (the restriction a lift or a renewal supersedes, as served) and `before` equal to it |
| E6: the recompute `after` was free beyond levers, state and quadrant | `after` has the risk given to the engine with its provenance, the approval flag, the evidence quality and a cluster size exactly with a cluster; a dispute removes an active link, evidence adds none |
| E7: `Decimal` and numeric strings in untyped payloads | Refused like plain numbers |
| E8: a snapshot shown before a lift could be confirmed after it | `/confirm` answers 409 when the terms' decision is no longer in force (section 4, Checkout; Consequences); a mock scenario `conflict` |
| E9: stale documentation | Section 4 (Inspection) and `InspectionConfirmResponse` say what the message states; ADR-13 points to `contracts/json/api/decision_request.schema.json` |
| E10 (major) and E11: draft facts not tied to code-built facts; two verdicts | `DraftView.case_facts`: every fact of the llm_case; `_check_draft` checks them against the version (entity, period, values, forbidden and value features, the cluster) and, on the case card and the recompute, equal to `llm.case.build_case` rebuilt from the factors and the cluster shown (`expected_case_facts`); `llm.validator` runs on exactly the case `llm.generate` validated, so the known limit and the template fallback of section 11 are gone |
| E12: the refund stated was not tied to the claim | A refund exactly with a claim, the price of the line less the claim (`ClaimView.line_price`), with the claim's provenance |
| E13: the subject was free text | `llm.phrasebook.subject(return_id, lang)`; `OutboxMessage` requires it and `compose` writes it |
| E14: a tone nothing applied | Removed from `RegenerateRequest`, `DraftView`, `llm_generations`, `contracts/vocab.py` and spec 9 |
| E15: generation records allow paths `llm.generate` cannot take | **Open**: not part of this round. `check_generation_path`, the `llm_generations` CHECKs and the link of a generation to its decision stay as in section 11 |
| E16 (major): no 403, 409 conflict, 429 or 503 scenario, no renew outcome | Error examples for every code and routes for every declared status (section 7, Errors); the scenario `renewed`; a test requires a route for every declared error status |
| E17 (major): the deduction was not explained | A reviewed sentence written by code with the field {deduction} ("Abbiamo trattenuto {deduction} per la diminuzione di valore rilevata al controllo." / "We have withheld {deduction} for the loss of value found at our check."; the Italian text since section 14: "per il minor valore rilevato al controllo"), no size sentence, at most two extras, one in Q3 and Q4 since section 14 (ADR-14 decision 12) |
| E18: decision and return ids not served | A record for every version the examples name, a card for RET-000077; the test resolves every `DEC-` id of the examples and the return of every record |
| E19: CL-000655 missing from the matrix | Its three accounts are Q4 points; a test checks every customer of a decision in force |
| E20: the audit page left out acts | The whole log of the examples, numbered in time order; results and recomputes name its ids |
| E21: agent routes served JSON arrays | `agent_stream.<name>.sse` for every stream, routed; `media_type` is the type of the response |
| E22: "per il 11 ottobre" | {deadline} carries its Italian article; tested on every day of a month |
| E23: Elena's anomaly showed a task created later | Open at 18:00; the scenario `task_opened` after 18:45 |
| E24: the Rossi answer stated facts it never consulted | A `get_cluster` step for C-ROSSI-2; CL-000233 in the refs and the evidence consulted; a test that an answer cites every record it names |
| E25: English-only checkout texts | `{it, en}` fixed texts of `llm/phrasebook.py` (`CustomerText`), the coupon block neutral |
| E26: a record could not show a reversed block | `CouponRuleRef.state` and `in_force` |
| E27: lift and renew messages | **Decided** by the maintainers: lift and renew send no customer message; the customer sees the terms at checkout (section 4; spec 19.B) |

Examples whose shape changed (a key added or removed):

- `case_facts` added and `tone` removed in every draft: the 13 earlier case cards,
  `regenerate_response`, `recompute_response.rossi_card` and `link_dispute_response`; their
  rendered texts gain `fields.deduction`;
- `fields.deduction` in every message: `outbox`, `decision_result.marco_approved`,
  `decision_result.override`, `decision_result.reject` and `inspection_confirm_response`;
- `coupon_rule.state` and `coupon_rule.in_force`: `case_card.marco`,
  `decision_result.marco_approved`, `decision_record.marco_v1` and
  `decision_record.earlier_q4_v1`;
- `superseded`: every `decision_result` (a full view in `decision_result.lift`, null in the
  others);
- `diminished_value_claim.line_price`: `inspection_confirm_response` and
  `decision_record.valentina_v1`;
- `size_advice.text` and `coupon_block.text` become `{it, en}`:
  `checkout_evaluate_response.giulia` and `checkout_evaluate_response.marco`;
- `tone` removed: `regenerate_request`;
- `audit_page`: new untyped payloads (checkouts, catalogue tasks, coupon rules).

`index.json` keeps its shape, with new routes and `media_type` set to `application/json` on
every error route. New files: `case_card.earlier`,
`decision_record.{giulia,rossi,elena,insufficient,simone,francesca}_v1`,
`decision_record.lorenzo_v2_renewed`, `decision_request.{renew,renew_early,forbidden_role}`,
`decision_result.renew`, `checkout_confirm_{request,response}.marco`,
`product_anomalies.task_opened`, `error.{forbidden_role,conflict,conflict_early_renewal,
conflict_terms_lapsed,rate_limited,llm_unavailable,internal}` and
`agent_stream.{marco_rossi,marco_terms,write_refused}.sse`.

Known limits, reported as they are:

- **E15 is open**: a generation record can still claim a path `llm.generate` cannot take, and
  nothing ties `decisions.generation_path` to `llm_generations`.
- **A regenerated draft** is checked against its version but not rebuilt: the response does not
  show the score factors and the cluster size, so their values are checked on the card that
  shows the draft.
- **Lift and renew send no message**: decided (E27), not a limit. The phrasebook still has no
  reviewed sentence about a review.

### 13. Phase 4 integration (14 Oct 2026, contract 1.4.0 unchanged)

Merging the route builders onto phases 1 to 3 changed two things of this contract. Neither
changes a generated file (`contracts/json/api/`, `contracts/examples/api/`): the version stays
1.4.0. ADR-16 section 9 has the rest of the integration.

- **`LinkedAccountsView` no longer refuses `n_distinct_cards` above `cluster_size`.** Since the
  phase 2 generator, one account can use several cards, so a cluster can hold more distinct
  cards than accounts (3 cards of the tiny test store, 2 of 40 sampled at full scale): the
  model refused real clusters and their case cards answered 500. The check was a model
  validator only; the JSON Schema never had it.
- **`telemetry_samples` is a table of `contracts/sql.py`** (section 6): one row per GPU and
  amd-smi sample (`sampled_at`, `device`, `gpu_index`, utilization, memory used and total in
  GiB, power, `source`, `error`), CHECKs `ck_telemetry_samples_utilization_range` (0 to 100)
  and `ck_telemetry_samples_non_negative`, an index on `sampled_at`. The demo reset creates and
  empties it with the other tables; the sampler (`python -m apps.api.services.other.telemetry`)
  creates it when it starts first, keeps 24 hours and skips a sample it cannot store. It was
  defined in the telemetry service with its own metadata; `tests/test_sql_contract.py` covers
  it now like every table.


### 14. Merge of the LLM layer's closing rounds (14 Oct 2026, contract 1.4.1)

The API branch was merged with the LLM layer of rounds 3 to 6 of the adversarial review (ADR-14
decisions 9 to 11) and the number-free missing-evidence sentences of `pipeline/features/
evidence.py`. Both branches had changed `llm/`; ADR-14 decision 12 records the merged customer
texts. The merge changes generated files, so the version is 1.4.1.

- **No lever fact (contract change).** ADR-14 decision 9 removes the `policy` entity of the
  llm_case: operator statements do not talk about levers, so the window of the proposal is no
  longer a fact. `CitedFact.entity` (`llm.case.Entity`) loses `policy`, and `_check_case_facts`
  no longer expects a `return_window_days` fact "orders from" the decision day (a lever is
  never a fact nor a score factor). Every draft of a decision with a window lever has one fact
  fewer.
- **`TERMS_TOPIC` (contract change).** `ValidatorCode` gains the code of an operator statement
  that talks about levers, terms, refunds, restrictions, decisions or levels (regenerate,
  ADR-14 decision 9), in every schema that lists validator codes. The mock generation of the
  insufficient-evidence example now records it next to `DECISION_ECHO`.
- **Customer texts (text only).** The registration says the refund is due "al più tardi per
  {deadline}", with the Italian article of E22 in the field ("al più tardi per l'11 ottobre
  2026"); Q3 and Q4 say that this return keeps the terms of its order (with the free size
  exchange when granted), the restrictive terms are "these return terms", the notice is "These
  terms take into account your previous returns" after them and names the next orders
  otherwise, and insufficient evidence states the standard terms of the next orders. The coupon
  block at checkout uses the notice that follows a stated term. The deduction sentence says "il
  minor valore" in Italian, and Q3 and Q4 with a deduction take one extra, so every message
  stays within 120 words before the clause (ADR-14 decision 12).
- **Template statements (text only).** The fallback explanation names no lever ("the case view
  shows the decision"; no "standard terms apply" or "no restrictive levers").
- **Missing evidence (text only).** The items hold no number (ADR-14 decision 11), so the model
  can restate them: "Too few order lines with a settled outcome for this customer", "Only an
  email on file: no device, address or card".
- **Agent and inspection checks.** `answer_codes` records the codes of
  `llm.agent.answer_problems`, the checks `VllmAgent` runs on an answer (list markers removed,
  identifiers of the tool results stripped before the banned terms, the comparison references
  in grounding), and the notes of a vision checklist are checked with
  `llm.text.foreign_characters`, the character allowlist that replaced `foreign_letters`.

Schemas whose shape changed: the `entity` enum of a cited fact in `case_card`,
`link_dispute_response`, `recompute_response`, `regenerate_response` and `openapi.json`; the
validator codes in `agent_stream`, `audit_page`, `case_card`, `decision_result`, `inspection`,
`inspection_confirm_response`, `link_dispute_response`, `recompute_response`,
`regenerate_response` and `openapi.json` (and `llm_case.schema.json` of ADR-14). Examples whose
shape changed: the drafts that lost their `policy` fact (`case_card.andrea`,
`case_card.earlier`, `case_card.giulia`, `case_card.marco`, `link_dispute_response`,
`regenerate_response`) and the generation records with `TERMS_TOPIC` (`case_card.insufficient`,
`audit_page`). The other changed examples change texts only.


### 15. Review of the running API (14 Oct 2026, contract 1.5.0)

A four-lens review (rules, contract, security, operations) ran the merged API on the full
synthetic store, on PostgreSQL 16 and in a browser, and found 40 issues (H1 to H40): two
blockers, fourteen majors. This round fixes both blockers on the API side (the compose file,
the Makefile and the README belong to the infrastructure round: H29 and H40 are listed for it
below), every major, and the minors that were cheap and right; the other minors are reported
as they are. Each finding is a test: `tests/test_api_round5.py` on SQLite and
`tests/test_api_round5_postgres.py`, which fires concurrent requests at PostgreSQL. ADR-16
section 10 has the serving and operations side. The version is 1.5.0: a new endpoint and new
fields.

**The demo clock (H1, H11, H31).** The reset decides every open return at the snapshot instant
(2026-10-01), and the service ran on the wall clock: every message promised a refund date
already past, the default KPI week was empty and the decision time read 13 days. Decided: one
clock for the whole API. With `DEMO_CLOCK=on` (the default, for the cached demo, the GPU demo
and the reset alike) 'now' is the snapshot instant plus the time elapsed since the reset
started the clock (`apps.api.clock.DemoClock`); `off` runs on the wall clock. The reset writes
its start in the one row of the new table `demo_reset`; the service reads it at startup and at
most every 5 s (a reset while the API runs restarts its clock), and before any reset starts at
the snapshot instant when the service starts. Every 'now' of a response (an `as_of`, the time of
an act, the period of a figure that ends now, the KPI week) comes from that clock; GPU samples
keep the wall clock (they are measured in real time). `/readyz` shows it as `clock`
(`ClockView`: kind demo, system or fixed, now, the snapshot instant and the wall time it
started). The KPI week by default is the week of the latest decision (the reset week in a demo),
no longer the last complete week before now. Beyond the clock: an act whose message would
state a refund deadline already past answers 409 instead of promising a past date (rule 3),
and the queue shows `refund_due_by` and `refund_overdue` (a proposed case past the day; sort
`refund_due_by` brings them first). A reviewed "your refund is overdue" sentence would need the
phrasebook review of ADR-14 decision 8: not done, the 409 stands.

**The golden cases of the demo (H9, blocker).** The UI hard-coded the example ids
(RET-000101..., V-DRS-2500, CL-000417); the live store has other ids. Decided: option B of the
finding, with one source. The reset finds the golden cases from the golden customer ids of the
store (`pipeline.generator.golden`) and stores them in `demo_reset`; `GET /api/demo/cases`
(`demo_cases`, every role, 404 before a reset) serves `DemoCasesResponse`:

- `cases`: one `DemoCase {key, return_id, customer_id, decision_id, cluster_id}` per key of
  `giulia`, `marco` (a ring account with an open return, the Q4 awaiting approval when there is
  one), `rossi`, `rossi_card` (a case with linked accounts when there is one), `elena` and
  `insufficient` (the first withdrawal case with insufficient evidence); a key the store does
  not hold (a small `--scale`) is missing;
- `dispute`: a link whose dispute makes the cluster of the case named smaller (`link_id`,
  `return_id`, `decision_id`, `cluster_id`, `cluster_size_before`, `cluster_size_after`), looked
  for in the Rossi families first, then in the clusters of the other cases (the Marco ring and
  the other golden cases keep their story); the Rossi families are held together by several
  identifiers, so a single dispute rarely splits them;
- `carts`: `giulia` (the lines of her order: the two sizes) and `ring` (the returned item of the
  ring account, with an unredeemed coupon of the account when it has one), each a
  `CheckoutEvaluateRequest`.

The example `demo_cases.json` names the ids of the examples (RET-000101 to RET-000105,
RET-000109, the dispute of C-10233~C-20871 on RET-000106, V-DRS-2500 and V-JKT-2207), so the
mock serves them and the ids of the examples stay as they were. The UI reads the live ids from
this endpoint instead of constants.

**Ids (H10, H18).** `count(*) + 1` gave two concurrent writers the same id (500 on PostgreSQL).
Every sequential id (MSG, TS-CHK, ORD-CHK, INS with its PH and GEN-INS, EV, LD, CT, GEN-AGENT and
the drafts of each return, GEN-<return>-<n>) now comes from the new table `id_counters`
(`apps.api.services.ids.allocate`): the counter row is incremented in the transaction that writes
the id, stays locked until it ends and gives its number back on a rollback, so ids stay
sequential and deterministic (two resets still write the same rows). A database without a
counter row starts the kind after its existing rows. Decision and application ids are per
version of a return, under the lock of the return, as before.

**Transactions (H19, H20, H22).** No model call runs inside an open database transaction: the
agent, the vision checklist, regeneration, the recomputes of evidence and disputes and the
reset read in one transaction, call the model on the runtime's own pool (`LLM_CONCURRENCY`
workers, apart from asyncio's default executor) and write in a second one; a draft gets its id
when it is stored. The inspection confirmation takes the lock of the decision acts (the return
row, `SELECT ... FOR UPDATE`), confirms with a conditional UPDATE (409 when it was confirmed
meanwhile) and a return has at most one claim (the partial unique index
`uq_inspections_one_claim_per_return`). A dispute locks the named case and every case it
recomputes in return id order before it reads the link (no lock-order deadlock), and a second
dispute of one link is a 409, also when two arrive together (the unique key of
`link_disputes.link_id` maps to 409).

**Public read-only mode (H21).** Spec 11: the demo views are public and read only, admin and
telemetry behind basic auth. `PUBLIC_READ_ONLY=on` answers 403 `forbidden_role` to every write
route but `POST /api/agent/ask` and `POST /api/checkout/evaluate`, which stay for the public
pages and are rate limited per client (30 per minute, the first `X-Forwarded-For` hop the proxy
sets); `/readyz` says `public_read_only`. The proxy protects the admin data, `/api/telemetry`,
with basic auth (Caddy `basic_auth` on that path, ADR-17; the client route `/system` of the admin
page was protected too until section 16) and limits request
bodies to 10 MB (`request_body max_size`); the API refuses bodies over 8 MiB itself (413). Checkout
`/confirm` declares 403 now; the checkout evaluation declares 429.

**The language model (H15, H30, H32, H33, H34, H36, H8).** `GEMMA_MODEL` reaches the vLLM client,
the vision and the agent, and every label; `/readyz` checks that vLLM serves it. A vLLM
configuration the client refuses (no key, a URL that is not local) is an unavailable model: the
drafts use the template, labelled, the reset fills the queue (and exits 1 with a warning), and
regeneration and the agent answer 503 `llm_unavailable`, never a 500. `VLLM_TIMEOUT_S` (20 s)
bounds a call. The reset and `batch_generate` send the drafts with `LLM_CONCURRENCY` (32) calls
in flight and store them in their order. Labels: an output no model call produced is labelled
with the model `template`; in cached mode the outputs are labelled `vllm` (`/readyz.llm_mode`
too), a cache miss being the template served live, and the cached label is shown only while the
precomputed outputs are there (H6). The agent's refusal of a write request is decided by code
before any call: path `template`, the new fallback reason `write_request`, no model call
(`check_generation_path` accepts it with no call and no code; `AgentStream` requires it exactly
with a `write_request` refusal).

**Money (H2).** A refund and a diminished-value claim are on the price paid for the line: its
list price less its share of the order's coupon, pro rata of the list prices, to the cent
(`apps.api.services.prices`). `ClaimView.line_price` is that price, with its own provenance.

**Coupon block, claims, stale proposals (H12, H13, H16, H3).**

- A confirmed cluster coupon block turns the coupons of the terms shown off, base terms included
  (`terms.coupons_enabled` false, `coupon_eligible` false), and the stored snapshot names it:
  `TermsSnapshotView.coupon_application_id` and `coupon_review_at` (new, null without a block).
  The terms rules let base terms differ from the base only by the coupons a block turns off;
  CHECKs `ck_terms_snapshots_coupon_block_review`, `_coupon_block_off`,
  `_coupon_review_after_shown`, `_coupon_review_within_max`, and `_base_terms` and
  `_restriction_has_review` read the block.
- A claim belongs to the return: confirmed on a version, it stays on the later ones (a
  recompute keeps it, so the approval states the refund and the deduction), and a second claim
  on the return is a 409.
- A restriction proposal at or past its review date is stale: the act answers 409 after the
  case is decided again on the day of the attempt (a new version, trigger `stale_proposal`, by
  the system, `case_recomputed` in the log, with its draft); the next attempt acts on it. Its
  refund is then always overdue (14 days from the notice come before a review date 90 days
  after the decision), so in practice the case moves on by evidence or the merchant's refund.
- Checkout shows the engine's quadrant only while its levers stand (proposed, approved): an
  override shows its own levers with no quadrant (`quadrant` null, `terms_source` the decision),
  and an override to the standard levers (a lift) leaves the base terms. The rule "a decision
  source has a quadrant" of `CheckoutEvaluateResponse`, `TermsSnapshotView` and
  `ck_terms_snapshots_source` becomes "base has no quadrant".

**Other changes.**

| Finding | Change |
| --- | --- |
| H4 | A recompute of a guarantee case says in its provenance that no request model ran |
| H5 | The KPI counts no proposal a lift or a recompute superseded |
| H7 | The matrix provenance says a guarantee case shows the customer's batch risk |
| H14 | `ProductAnomaly.queued_return_ids`: the returns of the anomaly a catalogue task can name; another one is a 404 that says so |
| H17 | `GET /api/kpi` and `GET /api/fairness` declare 404 (no offline report), with an error example and a mock route; the message has no stray suffix |
| H23 | The vLLM probe follows no redirect (a 3xx is an unreachable vLLM) |
| H25 | Pages are at most `MAX_PAGE` (100,000) in the queue, the audit log and the outbox |
| H26 | No server path in a public body: the reason of a missing snapshot is in the server log |
| H27 | SQL errors log no parameter (`hide_parameters`) |
| H28 | An unparseable body is a 4xx `invalid_request`, not `internal` |
| H35 | The demo walk reads the golden cases from `/api/demo/cases` and expects the agent's 503 in cached mode |
| H37, H38, H39 | `/readyz` is `not_ready` without scores or models; the startup log says why; the reset CLI exits 2 with the remedy on a missing snapshot and reports generation paths; one log line per request (id, method, path, status, duration) |

**Contract changes for the UI (1.5.0).**

- New endpoint `GET /api/demo/cases` (`demo_cases.schema.json`): use its ids for the demo
  flows, the compare page, the shop presets and the matrix highlights (cluster ids of the
  cases). Mock: `demo_cases.json`, scenario `empty` for the 404.
- `ReadyzResponse`: `clock` (show the demo clock), `public_read_only` (hide the write actions),
  `label` only while precomputed outputs exist, `llm_mode` `vllm` in cached mode, `not_ready`
  also without scores or models.
- `QueueItem`: `refund_due_by`, `refund_overdue`; `QueueQuery.sort` gains `refund_due_by`.
- `TermsSnapshotView` (case card `order_terms`, checkout confirm): `coupon_application_id`,
  `coupon_review_at`. `CheckoutEvaluateResponse.terms.coupons_enabled` is false under a coupon
  block, base terms included; `quadrant` can be null with a decision as `terms_source` (an
  override).
- `ProductAnomaly` (case card, anomaly list): `queued_return_ids` for the catalogue task form.
- `RevisionTrigger` gains `stale_proposal`; `FallbackReason` gains `write_request` (the agent's
  refusal: `model_calls` 0, model `template`).
- Errors: 403 on checkout confirm (scenario `read_only`), 429 on checkout evaluate (scenario
  `rate_limited`), 404 on KPI and fairness (scenario `no_report`), 409 examples for a stale
  proposal and an overdue refund (scenarios `stale_proposal`, `refund_overdue`), 413 and 400
  `invalid_request` on any body; pages above 100,000 are 422.

Examples whose shape changed: every case card (`order_terms` coupon fields;
`product_anomaly.queued_return_ids` for Elena), the three queue responses, both checkout
confirm responses, both readyz responses, both product anomaly lists and
`agent_stream.write_refused`. New files: `demo_cases.json`, `error.read_only`,
`error.rate_limited_public`, `error.report_unavailable`, `error.no_demo_reset`,
`error.conflict_stale_proposal`, `error.conflict_refund_overdue`, `demo_cases.schema.json`.
Tables: `demo_reset` and `id_counters` (section 6), the column `terms_snapshots.coupon_review_at`
and the index `uq_inspections_one_claim_per_return`.

**For the infrastructure round (not changed here: infra/, Makefile, README).** Done in
[ADR-17](ADR-17-deployment-and-demo-package.md), merged with this contract: compose passes every
setting below under these names and `tests/test_infra.py` checks that the API reads them.

- H29 (blocker): mount `serving/` read-only in the api and reset services with
  `SERVING_DIR=/app/serving`, build it in the pipeline job (`python -m pipeline.serving`), run
  `python -m apps.api.reset` once the database is up, and give the api a healthcheck on
  `/readyz` that fails while the body says `not_ready` (the status stays 200, so the probe reads
  the body), with a start period of 60 s or more.
- H30: pass `GEMMA_MODEL` (and `VLLM_TIMEOUT_S`, `LLM_CONCURRENCY` if changed) to the api, the
  reset and `batch_generate`.
- H21: the demo profile sets `PUBLIC_READ_ONLY=on`; Caddy protects `/api/telemetry` (the admin
  page's data; section 16) with `basic_auth`, sets `X-Forwarded-For`, and limits request bodies
  (`request_body { max_size 10MB }`).
- H40: a telemetry sampler service on the GPU profile (`python -m
  apps.api.services.other.telemetry`).
- `DEMO_CLOCK` stays on (the default) on every profile; the reset starts the clock.

Known limits, reported as they are:

- **H4, the periods of the draft facts.** The value, cluster and factor facts of a draft are
  labelled "as of" the decision day while the card dates the customer snapshot (a month
  earlier). Fixing it needs the snapshot day in `DecisionInputs` (a column of `decisions` and a
  field of every decision example) and in `_check_case_facts`: not cheap, left for a later
  round. *Fixed in section 19.*
- **An overdue refund blocks the acts that send a message** (409) until the merchant pays it;
  the queue flags it. A reviewed sentence for a late refund would let the act go on. *Section
  19 lets a rejection or an override to the standard levers go on without a message.*
- **The demo clock of a long-running public demo** moves past the refund deadlines two days
  after the reset (the earliest is 2 October at full scale); the public demo is read-only, so
  no message is composed there, and a reset restarts the clock. *Section 19 freezes it.*
- **E15 is still open** (section 12).

### 16. Review of the public deployment (14 Oct 2026, contract 1.5.1)

A review of the deployment, the README and the public demo (docs/review-backlog.md, "A judge
following the README" and "Security of the public deployment") found two contract-visible gaps.
The version is 1.5.1: one more value of an existing field and one documented header, no new
endpoint or field. The infrastructure side is in ADR-17 (amendments of 14 Oct 2026).

- **The cached label is a claim about hardware (rules 11 and 13).** `/readyz` showed
  "Precomputed on AMD MI300X" whenever the cache had entries, so a CPU rehearsal package with a
  fake vLLM server was labelled MI300X. `CachedLabel` gains "Precomputed in a rehearsal, not
  verified on AMD MI300X" (`REHEARSAL_LABEL`). The MI300X label is shown only when the demo
  package's `MANIFEST.json`, next to the serving snapshot as in `demo-cache/`
  (`SERVING_DIR/../MANIFEST.json`), is not a rehearsal and names the MI300X in `generated_on`,
  and every entry of `llm_cache/` was written by `GEMMA_MODEL`; otherwise the rehearsal label,
  with the reason in the `demo_cache` check (`apps.api.services.demo_package`). `make
  demo-export` has no default hardware, refuses drafts of another model unless the package is
  declared a rehearsal, and records `rehearsal` and `gemma_model` in the manifest.
- **Retry-After on every 429 `rate_limited`.** The public limit (checkout evaluation, agent) and
  the agent's limit per person answer 429 with `Retry-After`: the whole seconds to wait, rounded
  up, at least 1, also stated in the message. `endpoints.json` lists the header with the two of
  every response, and the OpenAPI document declares it on each 429. The error body is unchanged.
- **Not contract changes, recorded here because they change behaviour.** On the public
  read-only API the public limit is checked before the agent's, and the agent's limit counts per
  client and person (`roles.client_key`, the first `X-Forwarded-For` hop as before), so one
  anonymous client cannot lock the agent for every visitor. The limiters forget the keys whose
  calls have all left the window (swept at most once per window) and count an IPv6 client by its
  /64. A refused `VLLM_BASE_URL` is named by the refused part (a scheme, a user, a query, a
  fragment, a host that is not local), never echoed: the message reaches `/readyz` and the 503
  bodies. The access log writes the path percent-encoded, so an encoded newline cannot forge a
  line. `GET /api/portfolio/matrix` serializes and gzips its body in a worker thread; the encoded
  bytes are not cached, because the matrix depends on the clock's 'now' (the 12-month return
  counts, the periods of its figures) and no key short of the full instant is provably correct.
  The reset retries its first database connection for about 30 s.

Schemas whose shape changed: `readyz_response.schema.json` (the `label` enum),
`endpoints.json` (`headers`) and `openapi.json` (the label, the `Retry-After` header of the 429
of `POST /api/checkout/evaluate` and `POST /api/agent/ask`). Examples: the contract version of
both readyz responses.

### 17. Second review of the public deployment (14 Oct 2026, contract 1.5.2)

A second review of the same branch found the MI300X claim still made, or refused, where section
16 did not look. The version is 1.5.2: one example text changes, no schema.

- **The cached telemetry note.** In cached mode the telemetry check of `/readyz` and the `note`
  of `GET /api/telemetry` said "GPU off: cached mode serves results precomputed on the AMD
  MI300X", also beside the rehearsal label. The note is now "GPU off: cached mode serves
  precomputed results" (`telemetry.cached.json`): where the outputs were made is the claim of
  the label alone. The UI's own badges must follow `readyz.label` (docs/review-backlog.md, UI
  migration).
- **Not contract changes, recorded here because they change behaviour.** The label compares the
  drafts with the model the package's `MANIFEST.json` names (`llm_cache.gemma_model`, which the
  export checked against `GEMMA_MODEL` of the droplet), not with `GEMMA_MODEL` of the demo
  server: in cached mode the API calls no model, and the runbook's demo server keeps the
  `.env.example` default while the package may hold the plan-B model's drafts. A manifest that
  names no model is a rehearsal. `python -m apps.api.batch_generate` stops before the first case
  unless the local vLLM lists `GEMMA_MODEL` (the fake server of `bench/fake_vllm.py` answers any
  model name with its own drafts), records the `device` of the batch in each cache entry, and
  makes again a cached draft of another model instead of keeping it; `make demo-export` packs
  only `device` gpu drafts as the MI300X's. `GET /api/portfolio/matrix` reads the database on
  the event loop and builds, serializes and gzips the matrix in one worker thread. On the full
  snapshot with the database of a reset (623 decided returns, 4 shared vCPUs): before, 0.36 to
  0.95 s on the loop per request (the longest stall of a 5 ms ticker 0.34 to 0.91 s); after,
  0.015 to 0.024 s on the loop and 0.48 to 1.21 s in the thread, during which the loop's longest
  stall was 0.031 to 0.044 s (the thread holds the interpreter lock in turns). Nature: real
  timings of three runs each, measured once.

Schemas whose shape changed: none (`endpoints.json` and `openapi.json` carry the version).
Examples: `telemetry.cached.json` (the note) and the contract version of both readyz responses.

### 18. The examples against the live service (14 Oct 2026, contract 1.6.0)

The review of 14 Oct 2026 (`docs/review-backlog.md`, API side of "UI readiness" and "Examples
vs live") found places where the contract or its examples show what the running service never
does; the UI, built on the examples, would rehearse beats the live demo cannot play. Each is a
test in `tests/test_api_examples_live.py` (the schema clash in `tests/test_api_schemas.py`).
The version is 1.6.0: a new field (`DemoDispute.changes_decision`) and changed schemas.

- **One schema per model.** `CartLine` and `CheckoutEvaluateRequest` are requests that a
  response also carries (`DemoCart.request`). The serialization export made their defaults
  required there, so `demo_cases.schema.json` defined them differently from the request
  schemas, `openapi.json` split them into `-Input` and `-Output`, and the UI's type generation
  failed. Both keep their defaults optional in both modes
  (`json_schema_serialization_defaults_required=False`; the API sends `quantity` and
  `coupon_code` anyway). `test_a_model_has_one_schema_in_every_file_that_defines_it` requires a
  byte-identical body for every `$def` and root model in every `contracts/json/api/*.schema.json`
  that defines it, and no split in `openapi.json`; it found no other clash.
- **Size advice at checkout (US-401).** The store holds no measurements or exchange outcomes, so
  the live checkout never suggests a size (`checkout.advice_for`: the size guide, or no reliable
  recommendation), and the personalized predictor has no size feature: two sizes of one
  variant in one cart get one probability. `checkout_evaluate_response.giulia` is built through
  `advice_for` and the live threshold: both sizes of V-DRS-2500 at 0.47, each with the
  measurement guide. `specific` stays in `SizeAdviceKind` (a shop with measurement data could
  use it), but no example and no live path produce it.
- **Catalogue tasks (H14, decided).** A task may name any queued return of the anomaly's
  variant, sold in the anomaly's week or not: the case that led to it (Elena's return is
  requested after the anomaly's snapshot, so it is never among the anomaly's `return_ids`). A
  return outside the queue is a 404, one of another variant a 422; section 15's H14 row
  ("another one is a 404") is superseded. `queued_return_ids` is what the UI offers: in the
  anomaly list the queued returns of the anomaly's week (`ProductAnomaliesResponse` keeps them
  among `return_ids`), on a case card those and the case's own return (`CaseCard` requires it
  and allows nothing else). The live Elena card now offers her return.
- **Inspections (US-501..503).** The examples are built through the mock vision of
  `LLM_MODE=mock` (`MockVision`, `assess` and `generation_of` of
  `apps/api/services/other/inspections.py`). The request of `inspection.awaiting` carries a tiny
  JPEG the mock reads as damaged (the DV-RULE-1 proposal), the one of `inspection.manual_review`
  a JPEG it reads as unclear (manual review: "The photo is not clear enough to check the item:
  verify by hand"); checklists, reasons and generation records are the mock's (`latency_ms`
  null: the mock records none). Before, both requests carried a photo the mock reads as clean,
  so the claim beat answered 422 `no_proposal` live. **The claim beat needs `LLM_MODE=mock`
  (the photo of the example request, or any photo the mock reads as damaged) or
  `LLM_MODE=vllm` (a photo with visible damage).** With `DEMO_MODE=cached`, or vLLM down, the
  checklist is the template: every item not observable, manual review, no proposal, no claim.
- **The demo dispute (US-310).** `find_dispute` dry-runs the recompute of each candidate link
  (the engine's proposal on the graph without the link, compared with version 1; at most 12
  candidates, nothing written, about 0.3 s each on the tiny test store) and takes the first
  that changes the quadrant or the levers, else the first candidate as before.
  `DemoDispute.changes_decision` (new) says which: on the tiny test store no candidate changes
  the decision (a Q1 case of two accounts stays Q1); the example's dispute (Chiara, Q3) does.
  The demo walk checks the flag against the recompute. The evidence beat on a Rossi case
  (never Q3 or Q4, E2E-05) changes the cluster and the risk shown, rarely the decision; the
  `before` and `after` of its recompute response say so. The reset stores the demo cases, so a
  database reset before 1.6.0 needs a new reset (`make reset`) before `/api/demo/cases` answers.
- **Model versions.** Every `model_versions` of the examples (decisions, audit entries,
  `/readyz`) has the keys of the serving manifest (`pipeline.serving.manifest.
  MODEL_VERSION_KEYS`: risk, risk_customer, checkout, reference, value, evidence, anomalies),
  which the export writes and the live API records; before, the examples had `propensity` and
  `graph`, keys the service never sends. The checkout examples' `model_version` is the
  `checkout` one.
- **Marco before the shop.** A proposal awaiting approval is not in force (rule 5), so the ring
  checkout shows the restriction and the coupon block only once Marco's Q4 is approved:
  `checkout_evaluate_response.marco` is that state, and the shop beat comes after the Marco
  beat (spec 16; `scripts/demo_walk.py` already approves Marco, step 3, before the checkout).

**For the UI migration (contract 1.6.0).**

- Shop beat: show the measurement-guide text of each line (`size_advice.text`, in the
  customer's language) and drop "Switch to M"; both sizes show the same probability, so no
  size is presented as the safer one. Run it after Marco's approval.
- Elena beat: the catalogue task form offers the card's `queued_return_ids`, which hold her
  return.
- Dispute and evidence beats: when `dispute.changes_decision` is false, or the recompute's
  `before` and `after` have the same quadrant and `lever_ids`, say "decision unchanged" and
  show what did change (the cluster, the active links, the risk).
- Inspection beat: with `LLM_MODE=mock` upload the photo of `inspection_create_request` to get
  the claim; in cached mode show the manual review, with no claim to confirm.

Schemas whose shape changed: `demo_cases` (`CartLine.quantity` and
`CheckoutEvaluateRequest.coupon_code` optional, `DemoDispute.changes_decision`), `openapi.json`
(one `CartLine` and one `CheckoutEvaluateRequest` component, `changes_decision`) and the
version in `endpoints.json`. Examples whose shape changed: `demo_cases` and every example with
`model_versions` (case cards, decision records and results, the recomputes, the dispute, the
regeneration, the audit page, both readyz). Examples whose values changed:
`checkout_evaluate_response.giulia` and the six inspection examples. The rule
`queued_return_ids` ⊆ `return_ids` moved from `ProductAnomaly` to the anomaly list, and the
case card has its own (models only). No table changes.

**Amendment (14 Oct 2026, contract 1.6.1).** The examples of the Elena beat still put her
return `RET-000105` among the jacket anomaly's `return_ids`, and the anomaly list offered it
in `queued_return_ids`. Live, her return is requested after the anomaly's snapshot: it is
never among `return_ids`, and the list offers nothing for this anomaly. The examples now
show that: `return_ids` are the week's returns only, the list item's `queued_return_ids` is
empty and the card's anomaly offers her return alone (`scripts/build_api_examples.py`
`jacket_anomaly`; `test_a_card_anomaly_lists_the_case_return_among_the_returns_a_task_can_name`
requires it). Examples whose values changed: `case_card.elena`, `product_anomalies`,
`product_anomalies.task_opened`, and the version in both readyz examples, `endpoints.json` and
`openapi.json`. No model or table changes.

### 19. Rules and legal behaviour after the review backlog (14 Oct 2026, contract 1.7.0)

The review of the merged API, deployment and README (`docs/review-backlog.md`, 14 Oct 2026)
left six items on rules and legal behaviour. Each is a test in
`tests/test_api_legal_backlog.py`, written to fail on 1.5.0 (one more guards the 409 that stays
for a case with a claim); `tests/test_api_round5.py` (the stale proposal can now be rejected),
`tests/test_api_examples.py`, `tests/test_api_services.py` and `tests/test_sql_contract.py`
follow the changes. The version is 1.7.0: new fields
(`ClockView.frozen`, `DecisionInputs.snapshot_on`) and a field that becomes nullable
(`refund_due_by`).

- **The public and the cached demo run on a frozen clock.** With `PUBLIC_READ_ONLY=on` or
  `DEMO_MODE=cached` (`Settings.frozen_clock`) the demo clock stands at the snapshot instant
  instead of running from the reset, so no open return of the reset drifts into an overdue
  refund however long the public demo stays up (the reset is one-shot at `make demo-up`). Those
  demos write nothing (the public one refuses every write; the cached one serves the outputs
  precomputed for the reset's decisions). *Corrected in section 20: a cached demo with writes
  on (`PUBLIC_READ_ONLY=false`) keeps a running clock; acts at one instant cannot follow one
  another.* `/readyz` still tells the truth:
  `clock.kind` demo, `now` equal to `snapshot_as_of`, `started_at` the wall time of the reset,
  and the new `clock.frozen` true, with a note that says so; a client never advances a frozen
  clock. `DEMO_CLOCK=off` keeps the wall clock, as before.
- **A guarantee case has no refund deadline (rule 4, NFR-17).** The 14 days of the withdrawal
  are not a deadline of the guarantee path, and its message has no `{deadline}` field.
  `DecisionView.refund_due_by` and `QueueItem.refund_due_by` are null exactly when the message
  of the path states no deadline (`states_refund_deadline`, from
  `llm.messages.available_placeholders`): on the guarantee path. Such a case is never
  `refund_overdue`, its acts are never refused for a deadline, and `sort=refund_due_by` puts it
  last. `policy.engine` still dates the refund on every path for its own checks:
  `to_policy_decision` gives it back from the notice day. SQL: `decisions.refund_due_by` is
  nullable, CHECK `ck_decisions_refund_due_on_withdrawal`.
- **After the refund deadline, a proposal can still be rejected or set to the standard
  terms.** Before 1.7.0 every act on a proposal past `refund_due_by` answered 409, so a pending
  Q3 or Q4 restriction could be neither approved nor rejected, and the stale-proposal recompute
  always ended there. Now a rejection, or an override to the standard levers, of a case without
  a diminished-value claim goes on with no message (`outbox_message` null,
  `late_without_message`): it sets no terms to announce, and its message would state a past
  date. The request still carries its `message` choice, unused. The refund is a separate legal
  obligation the merchant honours all the same. Approve, an override to other levers (terms to
  announce) and any act on a case with a claim (the message must state the deduction and its
  reason) still answer 409 until a reviewed late-refund sentence exists. `DecisionResult`
  accepts a null message exactly for lift, renew and such a late act. *Section 20: a case with
  a claim may be rejected or set to the standard terms late too, and every such case stays
  `refund_overdue`.*
- **Draft and agent facts are dated with the customer snapshot (rule 11).** The engine reads
  the last customer snapshot before the request (value, evidence, cluster); the facts of a
  draft and the agent's case summary said "as of" the decision day (*section 20: the risk score
  is dated with the day it was scored*). `DecisionInputs.snapshot_on`
  (new, a column of `decisions`, CHECK `ck_decisions_snapshot_by_decision`) is the UTC day of
  that snapshot (`Proposal.context.as_of`; the request day when no snapshot preceded it). The
  API builds the llm_case with `build_case(as_of=snapshot_on)`, `_check_case_facts` and
  `expected_case_facts` expect that day, and `ApiCaseReader`'s case says "as of" it. The day
  travels with the version, so `DraftInputs` needs no field of its own. The llm_case digest
  changes for every scored case: **a batch cache made before 1.7.0 (`python -m
  apps.api.batch_generate`, `make demo-export`) must be regenerated**; none is committed.
- **The refund of a message has its own provenance (rule 11).** A message stated the refund
  left after a diminished-value claim with the provenance of the claim. `ClaimView.refund()` is
  now the refund figure: the price paid for the returned line less the claim, with the
  provenance of the line price (period and nature) and the claim named in its origin ("price
  paid for the returned line: its list price less its share of the order's coupon
  (store/order_lines, store/orders), less the diminished-value claim of INS-...").
  `check_outbox_message` requires it; the approval and the inspection confirmation use it.
- **The coupon block of a linked account gives no cause (rule 11).** The checkout coupon
  block told every account of the cluster that its own previous returns caused it. The
  customer of the case keeps that wording (`COUPON_BLOCK_TEXT`); every other account sees the
  new reviewed text `CLUSTER_COUPON_BLOCK_TEXT` of `llm/phrasebook.py`, "Discount codes cannot
  be used on orders placed before {date}." / "I codici sconto non sono utilizzabili sugli
  ordini effettuati prima {date}.", with the review date of the block written by code (rule
  10; in Italian with its article joined to "di", "del 30 dicembre 2026", "dell'11 dicembre
  2026"). It passes the checks of the other checkout texts (language, digits, banned terms,
  legal wording, script) and names neither the accounts nor the cluster. `CouponBlock` accepts
  exactly one of the two texts, the second with its own review date.

Schemas whose shape changed: `readyz_response` (`clock.frozen`); every schema with a
`DecisionView` (`snapshot_on` in `inputs`, `refund_due_by` nullable: `case_card`,
`decision_record`, `decision_result`, `inspection_confirm_response`, `link_dispute_response`,
`recompute_response`, `regenerate_response`); `queue_response` (`refund_due_by` nullable);
`checkout_evaluate_response` (its description); `endpoints.json` and `openapi.json`. Examples
whose values changed: both readyz responses (the cached one frozen), every decision view
(`snapshot_on`, the first of the month of the notice; null `refund_due_by` for the guarantee
cases Elena and Francesca), the draft facts of the scored cases (dated with the snapshot), the
queue response, and the refund of `inspection_confirm_response` and `outbox`.

For the UI migration: show `clock.frozen` and never advance a frozen clock; show no refund
date for a guarantee case; after the deadline offer reject and the standard terms, and expect
`outbox_message` null; date the score facts as they come.

### 20. Review of section 19 (14 Oct 2026, contract 1.8.0)

An adversarial review of the section 19 branch found four defects, each now a test in
`tests/test_api_legal_backlog.py` that failed on 1.7.0 (`tests/test_llm_case.py`,
`tests/test_sql_contract.py` and `tests/test_api_round5.py` follow). The version is 1.8.0: a new
field (`DecisionInputs.scored_on`) and a new column and CHECK.

- **The clock is frozen only where writes are refused.** 1.7.0 froze it on the cached demo
  too, so with writes on every act had the same instant: once someone reversed the cluster
  coupon application of a Q4 restriction, its lift answered 500 (`DecisionResult` read the
  earlier reversal as one made by the lift, `reversed_at >= approved_at`). Now
  `Settings.frozen_clock` is `PUBLIC_READ_ONLY` alone. `infra/compose.demo.yml` starts the
  cached demo read-only by default, so its clock stays frozen; with `PUBLIC_READ_ONLY=false`
  the demo clock runs from the reset, as on 1.5.0, and `make demo-reset` restarts it. The note
  of a frozen clock says "the read-only demo".
- **A late act without a message keeps the refund flagged.** A rejection or the standard
  terms after the refund deadline sends no message (section 19), and the queue then cleared
  `refund_overdue`, though no message had stated the refund. `refund_overdue` is now: past
  `refund_due_by` and no message has stated the refund, that is a proposed case, or one a
  person rejected or set to the standard terms after that day (`acted_late`: not a lift or a
  renewal, whose restriction stated the refund in time). The case keeps its place in
  `sort=refund_due_by`. `QueueResponse` accepts either flag for a rejected or standard-terms
  item past its deadline (the item does not carry the time of the act), and only false for
  an approved one or one with levers.
- **A case with a claim is not stuck after the deadline.** A claim confirmed while a Q3 or Q4
  proposal awaits approval sends no message; after `refund_due_by` every act on it answered
  409, the stale-proposal recompute included (the claim carries over), so the case stayed
  proposed for good. A rejection or the standard terms now go on without a message on such a
  case too; the claim stays on the decision, the case stays `refund_overdue`, and the merchant
  states the refund and the deduction outside ReturnRing. Approve and other levers still
  answer 409. A reviewed late-refund body (with `{amount}`, no `{deadline}`) is left for a
  later round: it is new customer wording for `llm/phrasebook.py`.
- **The risk score is dated with the day it was scored (rule 11).** Section 19 dated every
  score fact with the customer snapshot, the risk score included; but the risk is computed at
  the request (request features as the shop knew them, and that snapshot:
  `ml.scoring.batch.score_requests`), so its fact said "as of" a day before the return
  existed, while the engine-input provenance of the same card says "at the request".
  `DecisionInputs.scored_on` (new, nullable column `decisions.scored_on`, CHECK
  `ck_decisions_risk_scored_by_decision`) is the UTC day of the score's `decided_at`, null
  exactly when there is no risk (the guarantee path). `llm.case.build_case(risk_as_of=...)`
  dates the `risk_score` fact with it; value, factors and cluster keep `snapshot_on`.
  `_check_case_facts` and `expected_case_facts` expect it. The agent's case summary states no
  risk figure (its numbers are the factors), so it keeps the snapshot day. The llm_case digest
  changes again for every scored case: **a batch cache made before 1.8.0 must be
  regenerated**.

Schemas whose shape changed: every schema with a `DecisionView` (`scored_on` in `inputs`);
`readyz_response` (the description of `ClockView`); `endpoints.json` and `openapi.json`.
Examples whose values changed: every decision view (`scored_on`, the notice day of a
withdrawal), the `risk_score` facts of the scored drafts (dated with it), and the frozen
clock's note in `readyz_response.cached`.

For the UI migration: a rejected or overridden case may be `refund_overdue`; date the risk
score fact as it comes.

## Consequences

- **Phase 4 (API).**
  - Implement the routes of `ENDPOINTS` with these models (`response_model`, query models with
    `Annotated[Model, Query()]`). Map pydantic errors to `ErrorResponse`, set the two headers,
    and check the role of the actor against `roles`.
  - Use `contracts/sql.py` for the state and `to_policy_decision` before writing a decision.
    Store `inputs.provenance` with each decision version.
  - Make drafts with `llm/` only: `llm.case.build_case` of `proposal_decision(view)` (score
    factors with `llm.case.factor_entity`, the cluster of the engine inputs),
    `llm.generate.generate`, `GenerationInfo.from_generation` and `DraftView.build`, which
    stores every fact of the case; validate the draft with the models before storing it (the
    same verdict as `llm.generate`), and store the llm_case in `llm_generations`. Compose every
    outbox message with `OutboxMessage.compose` (phrasebook ids from `MessageChoice` or
    `default_selection_for`).
  - `/confirm` answers 409 when the review date of the shown terms has been reached, or when
    their decision is no longer the version in force (superseded or lifted).
  - Answer 409 to an approval of a restriction on or after its review date, and recompute.
  - Send no inspection message while the decision awaits a person's approval; the message of
    the act then states the refund and the deduction.
  - Implement `lift` and `renew` by running `policy.engine.decide()` on the latest scores, and
    reverse the cluster coupon application of a version that a lift or a recompute supersedes,
    in the same transaction. Compute `in_force` of decisions and coupon applications when
    serving them.
  - Implement the intake function of section 4 for the shop adapter and the demo reset.
  - Replace the current shapes of `/readyz` and `/api/simulator/assumptions` in
    `apps/api/main.py`, and update `tests/test_api.py`.
  - A test can compare the app's OpenAPI paths to `ENDPOINTS`.
- **Phase 5 (UI).**
  - Generate types from `contracts/json/api/openapi.json` (or the per-model schemas). Serve
    `contracts/examples/api/` through `index.json`.
  - Show the provenance of every `Metric`, the cached label, and "Illustrative, not measured" on
    illustrative benchmarks. Show `review_due` and the lapsed state of a restriction, with the
    two review actions (lift, renew), and `in_force` of each cluster coupon application.
- **Alignment with parallel work.**
  - The agent tool names (phase 3, `llm/agent.py`) must match `AgentTool`. The statement and
    message structure is the one of `llm/` (section 11): the API stores `llm_output` as it is,
    with the cited facts and the composed messages.
  - The golden account ids `C-ROSSI-1..3` and `C-ROSSI-CARD-1..3`, and the opaque cluster ids,
    must match the generator (phase 1) and the graph (phase 2).
  - The evidence-quality components and their thresholds (D-15) come from phase 2.
  - The lever rules come from `contracts/levers.py` (`lever_json_schema()`, `format_lever_id`):
    a change there reaches the API schemas at the next export.
- **Regeneration after a model change.** Run `python -m scripts.export_api_schemas` and
  `python -m scripts.build_api_examples`; the tests say when. A change of the phrasebook, the
  case builder or the mock model of `llm/` changes the example drafts too.
- No new dependency. Persistence uses the pinned sqlalchemy, greenlet and asyncpg; the tests use
  aiosqlite and jsonschema from `requirements/dev.txt`.
