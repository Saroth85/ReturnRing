# ADR-15: API and persistence contract

Status: accepted, 13 Oct 2026; amended the same day after two reviews (sections 9 and 10). API
contract version `1.2.0`. Covers spec section 9 (API table, flows F1 to F7), the stories of E3,
E4, E5, E6, E7 and E9, NFR-16 and CLAUDE.md rules 1, 3, 4, 5, 9, 10, 11 and 12. Changes
`contracts/` (new `contracts/vocab.py`, `contracts/sql.py`, `contracts/json/api/`,
`contracts/examples/api/`), removes two v1 API schemas, and adds `apps/api/schemas.py` and two
scripts.

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
    differences from the standard levers on the merchant base, in catalogue order. The `Lever`
    schema is the catalogue schema (`lever_json_schema()`), and a test checks it.
  - `levers_from_policy` and `to_policy_levers` convert between this list and `policy.types.Levers`.
  - `lever_ids` is the `"id:value"` form.
- **Assumption sets (US-602).** One label, one set of values. A stored version (`assumptions-v1`)
  has `based_on` null. Every other set has its own label and names in `based_on` the stored
  version whose unit costs (per return, per undue discount, per hour) it uses with exactly those
  values (`check_unit_costs`; `Assumptions.derive` builds one):
  - the alternatives of a decision version: `<decision_id>/<alternative kind>`; all the
    alternatives of a decision are based on its `assumptions_version`, with its unit costs and
    its basis;
  - the week of the KPIs: `kpi/<week>` (simulated), `kpi/<week>/realized` (observed);
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
  | 422 | `legal_guard` | `policy.legal_guard` refused the decision; `violations` carries its codes |
  | 404 | `not_found` | |
  | 409 | `conflict` | Already decided, already confirmed, "approve" sent with levers different from the proposal, "lift" or "renew" on a version that is not a restriction, "renew" before the review date or when the fresh proposal is not a restriction |
  | 409 | `stale_decision` | The `decision_id` sent is not the latest version of the case |
  | 403 | `forbidden_role` | The actor's role is not among the endpoint's `roles` (section 5) |
  | 429 | `rate_limited` | Agent endpoint only |
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
  `refund_due_by` (notice + 14 days), `review_at` and the coupon rule application. It adds the
  human outcome and the version chain: `version`, `revision_trigger`, `supersedes`,
  `superseded_by`, and `as_of`, the time the view was served.
- **States.** `proposed`, `approved`, `overridden`, `rejected`. The last three are the policy
  `DecisionState` values plus `overridden`.
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
- **`POST .../decision`** takes the body of spec 19.B plus `decision_id` and `override_category`,
  and `message` is required. `approve`, `override` and `reject` act on a proposed version:
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
  - Every action writes one audit entry and one outbox message, about the decision, its return
    and its customer; the entry carries the decision's reason. `DecisionResult.coupon_application`
    is the cluster coupon application the act changed: the decision's own, confirmed by approve,
    override or renew of levers that block the cluster's coupons, or the restriction's, reversed
    by a lift; otherwise null. The message has the chosen language. `message.text` optionally
    replaces the connective text; placeholders only. The API fills the template fields and
    appends the fixed legal clause.
  - The clause is the guarantee clause on the guarantee path, the withdrawal clause when the levers
    are restrictive, and none otherwise (`required_clause`).
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
- **Draft.** `DraftView` echoes the decision shown (rule 1). Its explanation is a list of
  statements labelled `OBSERVED`, `SCORE` or `HYPOTHESIS`. A statement whose text has a digit
  names its entity, metric, value and period, and the value appears in the text (US-305); there
  is no `SCORE` on the guarantee path.
- **Messages and template fields (rule 10, ADR-08).** The renderers live in the contract:
  `deadline_text`, `terms_text`, `amount_text` and `template_fields(decision, lang)`, which
  gives the terms of the decision's future orders, its refund deadline and the amount of its
  diminished-value claim. A draft message and an outbox message have exactly these fields. Each
  message carries its connective text (placeholders only, no digits, no accusatory term, at most
  120 words), the fields, the clause kind and the exact `llm.messages.render_customer_message`
  output (`rendered_text` in a draft, `body` in the outbox).
- **`GenerationInfo`** gives the validator path: `llm`, `llm_regenerated` or `template`. It also
  gives `llm_mode` (vllm or mock) and `served_from` (live or cached).
- **Regeneration** returns plain JSON, not a stream: the validator checks the whole output before
  the UI sees it. It takes no free-text instruction (NFR-11). `RegenerateResponse` holds the
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
- The response holds the before and after `CaseSnapshot`, the new decision version (version + 1,
  trigger `evidence_added` or `link_disputed`, superseding the one before) and its regenerated
  draft.
- Earlier versions keep their state for the audit. Only the latest is actionable, and the
  in-force levers of a customer are those of its latest in-force version.

**Checkout (US-401..403, ADR-05, E2E-02).**

- **Evaluate** returns 19.B with these changes:
  - `size_advice` is structured: `specific` with a size, `measurement_guide`, or
    `no_reliable_recommendation`. It is present exactly when `p_return >= p_return_threshold`;
  - the terms carry `coupons_enabled`;
  - it adds `base_terms`, `terms_source` (base or a decision id), `review_at`, `coupon_block`,
    `shown_at`, `model_version` and one `provenance` for its numbers.
- **Terms and their source.** `terms_source: base` shows exactly the base terms, with no review
  date and no quadrant. Terms less generous than the base come from a decision in force and carry
  its review date. A restriction lapses at its review date, so its terms are shown only before
  it: `review_at` is after the UTC day of `shown_at`, and so is the review date of
  `coupon_block`. The terms snapshot follows the same rules and stores the base terms and the
  review date; the database checks them (`ck_terms_snapshots_review_after_shown` compares
  `utc_date(shown_at)` with `review_at`).
- **The API stores the shown terms** before it answers, as a `terms_snapshots` row with no order.
  `/confirm` binds that row to the new order, so the terms shown are the terms stored.

**Inspection (US-501..503).**

- `POST /api/inspections` returns the suggested checklist (tag, visible damage, photo quality;
  "not observable" is valid). Each item has the model's confidence, flagged
  `confidence_is_calibrated: false`.
- When the system is unsure there is no proposal: `manual_review` with its reason.
- A compensation proposal holds only if Ahmed confirms the observations it rests on.
- `/confirm` recomputes the claim from the confirmed observations and an approved rule. A claim
  needs confirmed visible damage, never vision alone.
- The claim is attached to the decision (`diminished_value_claim`, checked by the legal guard) and
  sent with its motivation; the message's `amount` field is the claim amount.

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
- `net_benefit_realized` stays null until outcomes are observed.
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
- CSV export uses `AUDIT_CSV_COLUMNS`, with JSON cells for `before`, `after` and `model_versions`.

**Agent (US-701, US-702, spec 8.8).**

- `POST /api/agent/ask` streams server-sent events. Each event is written as `event: <type>`,
  `id: <seq>` and `data: <event JSON>`, followed by a blank line (`sse_frame`). The UI reads the
  stream with fetch, because EventSource only does GET.
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
  (`inputs_provenance`), the outputs, the alternatives, the human outcome, the version chain and
  the versions;
- `audit_log`, `outbox` (with the connective text, the template fields and the rendered body);
- `terms_snapshots`: shown, then confirmed; with the base terms and the review date;
- `evidence_additions`, `link_disputes`, `inspections`, `catalog_tasks`;
- `simulator_assumptions` and `coupon_rules`: versioned, with a `seq` chain;
- `coupon_rule_applications`;
- `llm_generations`.

Column types:

- **Timestamps** are `UtcDateTime`: TIMESTAMP WITH TIME ZONE that refuses a naive datetime,
  stores UTC and reads back an aware UTC datetime on both databases, so rows validate as API
  models.
- **JSON** columns are JSONB on PostgreSQL and store a Python `None` as SQL NULL
  (`none_as_null`), so the `IS NULL` rules of the CHECKs see it.

`tests/test_sql_contract.py` maps every `DecisionView` and `TermsSnapshotView` of the examples to
a row.

Named CHECK constraints (`ck_<table>_<rule>`) carry the invariants that SQL can see. Each CHECK
is one rule, and a rule on a state is written as an implication, so a row that breaks one rule
breaks exactly one CHECK. PostgreSQL reports the first violated CHECK in name order and SQLite in
table order; with one violated CHECK both name the same one. The tests prove it: each invalid row
is refused with its CHECK, and accepted once that one CHECK is removed.

| Rule | Constraints |
| --- | --- |
| The guarantee path is unscored and unrestricted (rule 4) | `ck_decisions_guarantee_unscored` |
| Insufficient evidence has no quadrant (US-307) | `ck_decisions_insufficient_no_quadrant` |
| Q3 and Q4 need approval (rule 5) | `ck_decisions_q3_q4_need_approval` |
| A restriction, and only a restriction, has a review date after the decision (US-607) | `ck_decisions_restriction_has_review`, `ck_decisions_review_only_restriction`, `ck_decisions_review_after_decision` |
| A person acted exactly when the state is not proposed; with the review rules, an approved or overridden restriction has a person as approver and a review date (rule 5) | `ck_decisions_acted_has_approver` |
| An override or rejection has a reason and a category, an approval none | `ck_decisions_override_has_reason` |
| A version created by a review lifts the restriction (overridden, not restrictive) or renews it (approved restriction) (US-607) | `ck_decisions_review_outcome` |
| Version 1 is initial; later versions supersede another | `ck_decisions_version_chain` |
| Applying the coupon rule to a cluster needs a person's confirmation and stays reversible (LEG-09) | `ck_coupon_rule_applications_confirmed_by_person`, `ck_coupon_rule_applications_always_reversible` |
| The general coupon rule is approved by a person | `ck_coupon_rules_approved_by_person` |
| Compensation needs a photo, a confirmation and a rule (LEG-05) | `ck_inspections_compensation_evidence` |
| No proposal when unsure (US-503) | `ck_inspections_unsure_no_proposal` |
| No window below 14 days (LEG-01) | `ck_terms_snapshots_window_legal` |
| A snapshot gets its order at confirmation, not before | `ck_terms_snapshots_confirmed_with_order` |
| Base terms are the base terms, with no review date; less generous terms come from a decision with a review date (US-403, US-607) | `ck_terms_snapshots_base_terms`, `ck_terms_snapshots_base_no_review`, `ck_terms_snapshots_restriction_has_review` |
| Terms from a restriction are shown before its review date, in UTC days (US-607) | `ck_terms_snapshots_review_after_shown` |
| Human actions are never logged as the system | `ck_audit_log_human_action` |
| Reasons where the log needs them | `ck_audit_log_reason_required` |
| Decision entries, and only they, are about a decision | `ck_audit_log_decision_entity` |
| Every simulator input is non-negative; there is no refund column (rule 9) | `ck_simulator_assumptions_non_negative` |

- **Append-only tables.** `audit_log`, `simulator_assumptions` and `coupon_rules` refuse UPDATE
  and DELETE through triggers, plus TRUNCATE on PostgreSQL.
- **What the database cannot see.** JSON content and cross-table rules (levers against the legal
  guard, the draft echo, the template fields) are checked by the API with `policy.legal_guard` and
  the model validators. Rules that depend on the time a row is served (`in_force` of a decision
  or a coupon application) are computed when served.
- **Dates against timestamps.** `utc_date(column)` compiles to `date(column)` on SQLite (UTC
  text) and to `(column AT TIME ZONE 'UTC')::date` on PostgreSQL, so a CHECK counts the UTC day
  whatever the session `TimeZone`.
- **SQLite.** `enable_sqlite_foreign_keys(engine)` runs before `create_all`.
- **Tests.** `tests/test_sql_contract.py` runs on SQLite. With `RETURNRING_TEST_POSTGRES_URL` it
  also runs on PostgreSQL (run on PostgreSQL 16.15 for both rounds of this ADR); otherwise the
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
  item: guarantee) and `lorenzo` (a Q3 restriction approved in June, past its review date:
  lapsed and due for review; lifted in `decision_result.lift`).
- **The other demo examples.** The override, the rejection, the lift, the legal-guard refusal,
  the evidence and dispute recomputes, the six versions of the three version chains (Rossi card,
  Chiara, Lorenzo) served by `GET /api/decisions/{decision_id}`, the queue filtered for approval
  and for review, both checkouts, the inspections and the agent streams (the three questions of
  US-702 and a refused write). Two review outcomes are built by `review_result` in the tests and
  not served by the mock: a renewal of Lorenzo (the other outcome of the review the lift already
  shows, with the same next version id) and a lift of Marco's Q4 restriction with its coupon
  application (in the demo story Marco's restriction stays in force, as the checkout shows).
- **How they are built.** By code from `policy.engine`, `policy.simulator`, `llm.messages` and
  the renderers of `apps/api/schemas.py`. Every provenance is synthetic or simulated and starts
  with "Contract example: ". Golden cases are never accuracy (US-906). A matrix point of a
  customer with a card is that card.
- **`index.json`** maps each request to its example for a mock server: method, concrete path,
  query example, request example and `scenario`. The scenario is `default` unless the request
  names another one in the `X-Mock-Scenario` header (for example `stale_decision`, `cached`). Drafts
  in examples are `llm_mode: mock`: hand-written stand-ins, not Gemma output.

### 8. Changes to earlier contracts

- **Removed v1 files.** `contracts/json/decision_request.schema.json`,
  `contracts/json/checkout_evaluate_response.schema.json` and their examples are removed. They
  are superseded by the generated `contracts/json/api/` files. The lever schema of
  `api/decision_request` stays the catalogue schema, so ADR-13's test now reads it from there;
  the tests that an override needs a reason and that the checkout window never goes below 14 days
  run against the generated schemas.
- **Spec 19.B decision body.** Adds `decision_id` and `override_category`. `message` is required.
  The actions are approve, override, reject, lift and renew; "edit" is gone, because editing the
  message is `message.text` and editing levers is an override.
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

Not in this round: the operator explanation statements (findings B2 and B3). The LLM layer
changes that contract on another branch (ADR-14: facts built by code, statements grounded on the
cited facts, the customer message assembled from reviewed phrasebook sentences); the API
models and the renderers are unified with it when both branches are merged.

## Consequences

- **Phase 4 (API).**
  - Implement the routes of `ENDPOINTS` with these models (`response_model`, query models with
    `Annotated[Model, Query()]`). Map pydantic errors to `ErrorResponse`, set the two headers,
    and check the role of the actor against `roles`.
  - Use `contracts/sql.py` for the state, `to_policy_decision` before writing a decision, and
    `template_fields` for every message. Store `inputs.provenance` with each decision version.
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
  - The agent tool names and the statement structure (phase 3, `llm/`) must match `AgentTool` and
    `ExplanationStatement`. Otherwise the API parses `LABEL: text` segments of
    `operator_explanation`.
  - The golden account ids `C-ROSSI-1..3` and `C-ROSSI-CARD-1..3`, and the opaque cluster ids,
    must match the generator (phase 1) and the graph (phase 2).
  - The evidence-quality components and their thresholds (D-15) come from phase 2.
  - The lever rules come from `contracts/levers.py` (`lever_json_schema()`, `format_lever_id`):
    a change there reaches the API schemas at the next export.
- **Regeneration after a model change.** Run `python -m scripts.export_api_schemas` and
  `python -m scripts.build_api_examples`; the tests say when.
- No new dependency. Persistence uses the pinned sqlalchemy, greenlet and asyncpg; the tests use
  aiosqlite and jsonschema from `requirements/dev.txt`.
