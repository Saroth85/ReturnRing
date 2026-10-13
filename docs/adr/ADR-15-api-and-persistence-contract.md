# ADR-15: API and persistence contract

Status: accepted, 13 Oct 2026. API contract version `1.0.0`. Covers spec section 9 (API table,
flows F1 to F7), the stories of E3, E4, E5, E6, E7 and E9, NFR-16 and CLAUDE.md rules 1, 3, 4, 5,
9, 10, 11 and 12. Changes `contracts/` (new `contracts/vocab.py`, `contracts/sql.py`,
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
| Endpoint table | `contracts/json/api/endpoints.json` | `ENDPOINTS` of the models module: method, path, query, request, response, status, media type, errors, stories |
| OpenAPI | `contracts/json/api/openapi.json` | Same models and routes, from a stub FastAPI app: for TypeScript codegen and mock servers |
| Examples | `contracts/examples/api/<model>.<variant>.json` | `python -m scripts.build_api_examples`: golden cases and an example for every endpoint |
| Mock routes | `contracts/examples/api/index.json` | Which example answers which request (method, concrete path, request, query, status) |
| Tables | `contracts/sql.py` | SQLAlchemy 2.1 Core, PostgreSQL (asyncpg) and SQLite (aiosqlite) |

Rules on these files:

- **Generated, never edited.** Tests fail when a committed schema or example differs from a fresh
  export (`--check` modes).
- **Structural and semantic checks.** The JSON Schemas are structural. The model validators add
  the semantic rules: the engine invariants, net benefit equal to `policy.simulator`, the legal
  clause, the draft echo, quadrant totals and the agent event order.
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
- **Figures (rule 11, US-906, NFR-16).**
  - Every figure the UI shows is a `Metric {value, unit, provenance}`. This covers scores, rates,
    counts, euros, net benefit, thresholds, KPIs, fairness gaps, benchmark and telemetry numbers.
  - `provenance` is `{origin, period_start, period_end, nature}`, with nature one of real,
    synthetic or simulated: the fields of `contracts.io.Provenance`.
  - Units: eur, usd, count (integer), expected_count, ratio, probability, percent,
    percentage_points, z_score, score, days, seconds, milliseconds, tokens_per_second, gib, watts.
    Ranges are checked per unit.
  - A plain number is allowed only as an attribute of a record (an order's price, a link's
    weight) inside a section that carries its own `provenance`, which then covers it.
- **Levers.**
  - Levers are the `{id, value, scope}` objects of `contracts/levers.py` (ADR-13). A list holds the
    differences from the standard levers on the merchant base, in catalogue order. The `Lever`
    schema is the catalogue schema, and a test checks it.
  - `levers_from_policy` and `to_policy_levers` convert between this list and `policy.types.Levers`.
  - `lever_ids` is the `"id:value"` form.
- **Headers.** Every response carries `X-ReturnRing-Mode: live|cached` (US-901) and
  `X-Request-Id`, which the audit log also stores (NFR-12).
- **Errors.** Every non-2xx body is `ErrorResponse {code, message, violations, request_id}`.

  | Status | `code` | When |
  | --- | --- | --- |
  | 422 | `invalid_request` | The body, path or query is invalid; pydantic errors become `violations` |
  | 422 | `legal_guard` | `policy.legal_guard` refused the decision; `violations` carries its codes |
  | 404 | `not_found` | |
  | 409 | `conflict` | Already decided, already confirmed, or "approve" sent with levers different from the proposal |
  | 409 | `stale_decision` | The `decision_id` sent is not the latest version of the case |
  | 403 | `forbidden_role` | See the role table |
  | 429 | `rate_limited` | Agent endpoint only |
  | 503 | `llm_unavailable` | Only where no template or cache exists |
- **Pages and order.**
  - Pages are 1-based (`page`, `page_size`); `total` counts after the filters.
  - The queue sorts by `-expected_net_benefit` by default, ties by `return_id`. List filters
    repeat the parameter (`?quadrant=Q3&quadrant=Q4`).
- **Concurrency.** Every action on a case names the `decision_id` it was shown. Only the latest
  version of a return can be acted on.

### 3. Endpoints

Spec 9 plus what the stories need: `GET /api/outbox` (spec 4, the visible outbox), catalogue-task
list and update (US-606 follow-up), coupon rules (US-402, US-607) and checkout confirm (US-403).

| Method and path | Query or body → response | Stories |
| --- | --- | --- |
| GET /healthz | → `healthz_response` | US-001 |
| GET /readyz | → `readyz_response` (mode, label "Precomputed on AMD MI300X" when cached, component checks, versions) | US-901 |
| GET /api/returns | `queue_query` → `queue_response` | US-301 |
| GET /api/returns/{return_id} | → `case_card` | US-302..305, 307..309 |
| POST /api/returns/{return_id}/decision | `decision_request` → `decision_result` | US-303, 306, 607 |
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

**Queue and case card (US-301, US-302, US-307, US-308).**

- A queue item shows path and quadrant badges, the state, `requires_approval`, the lever ids, risk,
  value, expected net benefit, the number of linked accounts, `variant_anomaly` and `draft_status`.
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

- **`DecisionView`** holds the engine inputs (`inputs`, re-runnable for US-604) and the engine
  output: path, evidence quality, quadrant, `proposed_levers`, `requires_approval`,
  `current_return_terms`, `refund_due_by` (notice + 14 days), `review_at` and the coupon rule
  application. It adds the human outcome.
- **States.** `proposed`, `approved`, `overridden`, `rejected`. The last three are the policy
  `DecisionState` values plus `overridden`.
- **`levers` are the levers of the current state.** They are the proposal while proposed or
  approved, the operator's choice when overridden, and none when rejected. `requires_approval` and
  `future_terms` refer to them.
- **`in_force`** is true when the levers apply to future orders now. That means approved or
  overridden, or proposed without a need for approval: the Q1 upgrade of Giulia applies by itself
  and stays in the log.
- **`POST .../decision`** takes the body of spec 19.B plus `decision_id` and `override_category`,
  and `message` is required.
  - `approve` confirms the proposal. Levers, if sent, must equal it. The approver also confirms
    the cluster coupon application (`policy.engine.approve`).
  - `override` sets other levers with a reason (at least 3 characters) and a category. The
    legal guard checks them, giving 422 `legal_guard` on a violation. Restrictive override levers
    need a review date.
  - `reject` discards the proposal, with a reason and a category: no lever applies, the order's
    terms stay, and the return itself is never refused (rule 3).
  - Every action writes one audit entry and one outbox message. The message has the chosen
    language. `message.text` optionally replaces the connective text; placeholders only. The API
    fills the template fields and appends the fixed legal clause.
  - The clause is the guarantee clause on the guarantee path, the withdrawal clause when the levers
    are restrictive, and none otherwise (`required_clause`).
  - An override never becomes training data: training uses the synthetic labels (spec 8.4).
- **Draft.** `DraftView` echoes the decision shown (rule 1). Its explanation is a list of
  statements labelled `OBSERVED`, `SCORE` or `HYPOTHESIS`, with entity, metric, value and period;
  there is no `SCORE` on the guarantee path.
- **Messages.** Each message carries its connective text (placeholders only, no digits, no
  accusatory term, at most 120 words), the template fields, the clause kind and the exact
  `llm.messages.render_customer_message` output.
- **`GenerationInfo`** gives the validator path: `llm`, `llm_regenerated` or `template`. It also
  gives `llm_mode` (vllm or mock) and `served_from` (live or cached).
- **Regeneration** returns plain JSON, not a stream: the validator checks the whole output before
  the UI sees it. It takes no free-text instruction (NFR-11).

**Recompute (US-310, F7).**

- Evidence of kinds `household_members`, `shared_payment_explained` and `collective_address`
  names the links it explains. Those links leave the cluster features.
- `customer_contact` and `other` are recorded but change no feature.
- A dispute marks the link (one dispute per link) and recomputes every open case of the cluster.
- The response holds the before and after `CaseSnapshot`, the new decision version (version + 1)
  and its regenerated draft.
- Earlier versions keep their state for the audit. Only the latest is actionable, and the
  in-force levers of a customer are those of its latest in-force version.

**Checkout (US-401..403, ADR-05, E2E-02).**

- **Evaluate** returns 19.B with these changes:
  - `size_advice` is structured: `specific` with a size, `measurement_guide`, or
    `no_reliable_recommendation`. It is present exactly when `p_return >= p_return_threshold`;
  - the terms carry `coupons_enabled`;
  - it adds `terms_source` (base or a decision id), `review_at`, `coupon_block`, `shown_at`,
    `model_version` and one `provenance` for its numbers.
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
  sent with its motivation.

**Matrix and simulator (US-601, US-602, ADR-04).**

- The matrix returns every scored customer as a `MatrixPoint`: risk, value, evidence, quadrant,
  cluster, returns and welcome coupons. It also returns the thresholds as Metrics with their basis,
  and quadrant totals computed on the points (the validator recomputes them with
  `policy.engine.quadrant_for`).
- Insufficient evidence stays outside the quadrants. About 20,000 points: the API gzips responses.
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
id and never `system` for a human act (rule 5). The API answers 403 `forbidden_role` otherwise.

| Action | Operator | Manager | Warehouse |
| --- | --- | --- | --- |
| Approve, override, reject; regenerate; evidence; dispute; ask the agent | yes | yes | no |
| Create and confirm inspections | no | no | yes |
| Create and update catalogue tasks | yes | yes | no |
| New coupon rule version; PUT simulator assumptions | no | yes | no |
| Reverse a coupon rule application | yes | yes | no |
| Every GET | yes | yes | yes |

### 6. Persistence (spec 7, `contracts/sql.py`)

Tables:

- `return_requests`;
- `decisions`: one row per version, with the engine inputs and outputs, the human outcome and the
  versions;
- `audit_log`, `outbox`;
- `terms_snapshots`: shown, then confirmed;
- `evidence_additions`, `link_disputes`, `inspections`, `catalog_tasks`;
- `simulator_assumptions` and `coupon_rules`: versioned, with a `seq` chain;
- `coupon_rule_applications`;
- `llm_generations`.

`tests/test_sql_contract.py` maps every `DecisionView` of the examples to a `decisions` row.

Named CHECK constraints (`ck_<table>_<rule>`) carry the invariants that SQL can see:

| Rule | Constraints |
| --- | --- |
| The guarantee path is unscored and unrestricted (rule 4) | `ck_decisions_guarantee_unscored` |
| Insufficient evidence has no quadrant (US-307) | `ck_decisions_insufficient_no_quadrant` |
| Q3 and Q4 need approval (rule 5) | `ck_decisions_q3_q4_need_approval` |
| A restriction has a review date after the decision (US-607) | `ck_decisions_restriction_has_review`, `ck_decisions_review_after_decision` |
| An approved restrictive decision has a person as approver and a review date | `ck_decisions_approved_restriction`, `ck_decisions_acted_has_approver` |
| An override or rejection has a reason and a category, an approval none | `ck_decisions_override_has_reason` |
| Applying the coupon rule to a cluster needs a person's confirmation and stays reversible (LEG-09) | `ck_coupon_rule_applications_confirmed_by_person`, `ck_coupon_rule_applications_always_reversible` |
| The general coupon rule is approved by a person | `ck_coupon_rules_approved_by_person` |
| Compensation needs a photo, a confirmation and a rule (LEG-05) | `ck_inspections_compensation_evidence` |
| No proposal when unsure (US-503) | `ck_inspections_unsure_no_proposal` |
| No window below 14 days (LEG-01) | `ck_terms_snapshots_window_legal` |
| A snapshot gets its order at confirmation, not before | `ck_terms_snapshots_confirmed_with_order` |
| Human actions are never logged as the system | `ck_audit_log_human_action` |
| Reasons where the log needs them | `ck_audit_log_reason_required` |
| Every simulator input is non-negative; there is no refund column (rule 9) | `ck_simulator_assumptions_non_negative` |

- **Append-only tables.** `audit_log`, `simulator_assumptions` and `coupon_rules` refuse UPDATE
  and DELETE through triggers, plus TRUNCATE on PostgreSQL.
- **What the database cannot see.** JSON content and cross-table rules (levers against the legal
  guard, the draft echo) are checked by the API with `policy.legal_guard` and the model validators.
- **SQLite.** `enable_sqlite_foreign_keys(engine)` runs before `create_all`.
- **Tests.** `tests/test_sql_contract.py` runs on SQLite. With `RETURNRING_TEST_POSTGRES_URL` it
  also runs on PostgreSQL; otherwise the PostgreSQL DDL is compiled and inspected.

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
- **The other demo examples.**
  - `case_card.insufficient`: an insufficient-evidence case (US-307).
  - The override, the legal-guard refusal, the evidence and dispute recomputes, both checkouts,
    the inspections and the agent streams (the three questions of US-702 and a refused write).
- **How they are built.** By code from `policy.engine`, `policy.simulator` and
  `llm.messages`. Every provenance is synthetic or simulated and starts with "Contract example: ".
  Golden cases are never accuracy (US-906).
- **`index.json`** maps each request to its example for a mock server. Drafts in examples are
  `llm_mode: mock`: hand-written stand-ins, not Gemma output.

### 8. Changes to earlier contracts

- **Removed v1 files.** `contracts/json/decision_request.schema.json`,
  `contracts/json/checkout_evaluate_response.schema.json` and their examples are removed. They
  are superseded by the generated `contracts/json/api/` files. The lever schema of
  `api/decision_request` stays the catalogue schema, so ADR-13's test now reads it from there.
- **Spec 19.B decision body.** Adds `decision_id` and `override_category`. `message` is required.
  The actions are approve, override and reject; "edit" is gone, because editing the message is
  `message.text` and editing levers is an override.
- **Spec 19.B checkout response.** See section 4.
- **Policy types.** `policy.types.DecisionState` is unchanged. The API state `overridden` maps to
  an approved policy decision with the operator's levers when the legal guard runs.

## Consequences

- **Phase 4 (API).**
  - Implement the routes of `ENDPOINTS` with these models (`response_model`, query models with
    `Annotated[Model, Query()]`). Map pydantic errors to `ErrorResponse`, and set the two headers.
  - Use `contracts/sql.py` for the state.
  - Replace the current shapes of `/readyz` and `/api/simulator/assumptions` in
    `apps/api/main.py`, and update `tests/test_api.py`.
  - A test can compare the app's OpenAPI paths to `ENDPOINTS`.
- **Phase 5 (UI).**
  - Generate types from `contracts/json/api/openapi.json` (or the per-model schemas). Serve
    `contracts/examples/api/` through `index.json`.
  - Show the provenance of every `Metric`, the cached label, and "Illustrative, not measured" on
    illustrative benchmarks.
- **Alignment with parallel work.**
  - The agent tool names and the statement structure (phase 3, `llm/`) must match `AgentTool` and
    `ExplanationStatement`. Otherwise the API parses `LABEL: text` segments of
    `operator_explanation`.
  - The golden account ids `C-ROSSI-1..3` and `C-ROSSI-CARD-1..3`, and the opaque cluster ids,
    must match the generator (phase 1) and the graph (phase 2).
  - The evidence-quality components and their thresholds (D-15) come from phase 2.
- **Regeneration after a model change.** Run `python -m scripts.export_api_schemas` and
  `python -m scripts.build_api_examples`; the tests say when.
- No new dependency. Persistence uses the pinned sqlalchemy, greenlet and asyncpg; the tests use
  aiosqlite and jsonschema from `requirements/dev.txt`.
