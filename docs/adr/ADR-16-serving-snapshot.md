# ADR-16: Serving snapshot and API core

Status: accepted, 13 Oct 2026; amended on 14 Oct 2026 by the phase 4 integration (section 9)
and the review of the running API (section 10). Covers the data access of the API (CLAUDE.md
rules 7, 11 and 12, NFR-03, DATA-03), the API core the three route builders extend (ADR-15 sections 2, 4 and 5), the
demo reset (US-903) and the cached mode (US-901, ADR-03). Changes `contracts/sql.py` (one nullable
column, below) and adds `pipeline/serving/`, `ml/reference/persist.py` and the API core under
`apps/api/`.

## Context

ADR-15 fixed what the API answers. It left open where the API reads its inputs. The API needs
the store (orders, lines, returns, the terms snapshot of an order), the decision-time features
(evidence quality, customer value, clusters and links, product anomalies), the batch scores
(`scores/scores`, `scores/request_scores`) and the models it runs at request time. All of that is
under `data/` and `artifacts/`, next to `data/truth/`.

Rule 12 says running services never read `data/truth/`. The compose file enforces it: the `api`
container never mounts `data/`. The API also must not drift from the offline jobs: a request
scored by the API must get the risk the batch would give it, and the engine must see the
thresholds the batch used.

## Decision

### 1. One read-only snapshot for the API: `serving/`

`python -m pipeline.serving --data data --artifacts artifacts --out serving` writes:

| Path | What | Why the API needs it |
| --- | --- | --- |
| `store/*.parquet` | The ten store tables, rows available at the snapshot instant | Case card (item, request, orders, terms snapshot), request features of a live request, the graph of a dispute |
| `features/customer_features`, `customer_value`, `evidence_quality`, `product_anomalies` | Every snapshot computed at or before the instant | The customer snapshot before a request (rule 7), evidence, value, residual, anomalies |
| `features/links`, `features/clusters` | At the instant and at every snapshot a request score read (`context_as_of`); built by `pipeline.graph` when the data root lacks one | The case card shows the cluster the engine was given; the cluster features of the score are from that snapshot |
| `scores/scores`, `scores/request_scores` | The batch scoring at the instant | The engine's risk for every open return (ADR-03) |
| `models/risk/` | `customer` and `request` models (booster, calibrator, meta) and the training manifest; not the ablation models A0..A3 | Scoring a request that arrives after the batch (the shop adapter) |
| `models/checkout/` | The personalized line predictor of `ml.reference`, fitted at the instant on the settled store lines | The checkout (US-401, `ml.reference.checkout`) |
| `manifest.json` | Instant, seed, contract version, per table rows, snapshots and provenance, model versions and thresholds, sha256 and size of every file | The API checks every file at startup |
| `llm_cache/` | Filled later by `python -m apps.api.batch_generate` on the MI300X | Cached mode (section 4) |

- **Instant.** By default the instant of the batch scoring (`scores/scores.as_of`); the scores
  must be at that instant or the export stops ("rescore first").
- **Writing.** Every table goes through `contracts.io.read_table` and `write_table`, with the
  provenance of its source (rule 11). The store tables are read with `as_of`, so no row known
  after the instant is served (rule 7). Files are deterministic: the same data root gives the same
  manifest (tested).
- **No ground truth.** A source path with a `truth` part, a `truth/` contract and any column only
  the truth tables have (`behaviour`, `ring_id`, `family_id`, `defective`, `base_return_rate`...)
  is refused. `tests/test_serving_snapshot.py` builds a store whose root holds `truth/`, exports
  it, and checks every served file: no truth path, no truth column, no truth ring or family id in
  any string value or JSON file. The risk models were trained offline on synthetic labels
  (rule 12 as amended by ADR-13); only their boosters, calibrators and metadata are served, never
  `metrics.json`.
- **Not exported.** The propensity models of `ml.propensity` (ASOS, US-201) are a benchmark; the
  API runs none of them. The reference model of the residual is not run at request time either
  (the residual is in `customer_features`).
- **Model files.** `ml/reference/persist.py` adds JSON persistence to the line predictor:
  booster (save_raw JSON), encoding and the isotonic thresholds with their dtype, so a loaded
  model predicts bit for bit what the fitted one did (tested).

### 2. The API reads the snapshot in memory

`apps.api.serving.Serving.load(SERVING_DIR)` verifies the manifest (a changed or added file stops
the load; `/readyz` reports it), then keeps every table as a pandas frame and computes, before
the app answers, the line frame, the lines by customer and the sales frame the card reads: 20,000
customers and 205,462 order lines load in about 8 s on the shared CPU (13 Oct 2026). It answers
three reads of flow F1:

- **The request's risk.** From `scores/request_scores` when the batch holds the return. Otherwise
  the request model of the snapshot scores it with the batch's own code
  (`ml.scoring.batch.score_requests`): request features at the request instant
  (`pipeline.features.request_features`), the customer snapshot strictly before it. A return of
  the shop adapter is added to the store snapshot in memory for that. Tested: for every open
  return of the tiny store, the live score equals the batch score, factors included. At full
  scale a live score takes about 3 s (rare: adapter returns only).
- **The customer snapshot strictly before the request** (MOD-08): evidence quality, value and its
  70th-percentile threshold, residual and the cluster (size 2 or more). An account with no
  snapshot before its request (a new account) gets evidence and value computed by
  `pipeline.features` from its own store rows at the request instant, and no cluster (the request
  model saw none). Tested: the thresholds give the batch's quadrant for every scored return.
- **The store rows** of a return, its line, order, delivery and terms snapshot.

Everything the API writes goes to PostgreSQL (`DATABASE_URL`; SQLite in tests): the tables of
`contracts/sql.py`, created with `create_all` and the append-only triggers.

### 3. Intake, decided day and the demo reset

- **Intake** (`apps.api.services.cases.intake`, ADR-15 section 4): the path from the reason
  before any score (rule 4: `request_score` is never called on the guarantee path, tested), the
  request's risk, the customer snapshot, `policy.engine.decide`, `policy.legal_guard` (in decide
  and in the `DecisionView` model), the alternatives, the version stored with its inputs,
  provenance, score factors and versions, the proposed cluster coupon application (Q4 with a
  cluster), the `decision_proposed` audit entry by the system, and the draft (section 4).
- **The decision day is the day of the intake** (ADR-15: "the decision day is the day of the
  act"): `decided_on` is the UTC day of `created_at`, and the review date of a restriction is 90
  days after it. A return that waited before the intake therefore never gets a proposal already
  past its review date. The scores are those known at the request.
- **Reset** (`python -m apps.api.reset`, US-903): drops and recreates every table, loads
  `assumptions-v1` (the illustrative example of spec 8.10, `created_by` system, as in the
  examples) and `coupon-rule-v1` (approved by luca, the demo manager; its audit entry says it was
  loaded by the reset), then runs the intake of every open return of the snapshot, golden cases
  first, in one transaction per return. Every timestamp is the snapshot instant (a fixed clock):
  two resets of the same snapshot write the same rows (tested, table by table), on SQLite and on
  PostgreSQL 16 alike. The mock records no latency, so a mock draft is deterministic too. Measured
  on the full synthetic store (13 Oct 2026, shared CPU, mock model, SQLite): 623 open returns
  decided and drafted in about 45 s, none refused; their 623 case cards all pass the model and the
  JSON Schema, p50 0.11 s and p95 0.33 s per card.

### 4. Drafts: mock, vLLM, cached

`apps.api.services.generation.Generator` builds the llm_case with `llm.case.build_case` of
`proposal_decision(view)` (the score factors shown on the card, `factor_entity`, the cluster the
engine was given) and runs `llm.generate.generate`:

| Settings | Client | `GenerationInfo` |
| --- | --- | --- |
| `LLM_MODE=mock` | `MockClient` | `llm_mode` mock, `served_from` live, model `mock`, no latency |
| `LLM_MODE=vllm` | `VllmClient` (local vLLM only, rule 2) | `llm_mode` vllm, live, `GEMMA_MODEL`, measured latency |
| `DEMO_MODE=cached`, entry found | none: the cache entry, validated again by `llm.validator` | `llm_mode` vllm, `served_from` cached, the model and latency of the MI300X run |
| `DEMO_MODE=cached`, no valid entry | a client that is unavailable | `template`, fallback `unavailable`, served live: labelled as such |

- **Cache.** `llm_cache/<sha256 of the canonical llm_case>.json`, format
  `returnring.llm_cache.v1`: digest, case id, model, path, fallback reason, model calls, validator
  codes, the output, latency, completion tokens, prompt version. `python -m
  apps.api.batch_generate` writes it on the MI300X (`LLM_MODE=vllm`) from the same intake at the
  same instant as the reset, so the demo reset finds every entry. An output that fell back because
  vLLM was unavailable is not cached. A broken entry is a miss.
- **Storage.** `llm_generations` keeps the llm_case, the output, the path, the codes, the input
  digest and `prompt_version` (`prompts-<sha256 of llm.prompts.SYSTEM_PROMPT>`); the draft is
  served again by `DraftView.build` on the stored output and case, so the messages are composed by
  `llm.phrasebook` every time.

### 5. The API core for the route builders

- **App factory** `apps.api.app.create_app(settings, clock, engine, serving, llm_client)`;
  `apps.api.main:app` reads the environment. `Runtime` (app.state) holds settings, clock, engine,
  serving snapshot and generator; routes use `runtime(request)` and `rt.transaction()`.
- **Settings**: `DEVICE`, `LLM_MODE`, `DEMO_MODE`, `SEED`, `DATABASE_URL` (normalized to asyncpg or
  aiosqlite), `SERVING_DIR`, `VLLM_BASE_URL`, `VLLM_API_KEY`, `GEMMA_MODEL`.
- **Errors**: `apps.api.errors` (`ApiError`, `not_found`, `conflict`, `stale_decision`,
  `forbidden_role`, `legal_guard`, `invalid_request`, `llm_unavailable`, `rate_limited`). Pydantic
  errors are 422 `invalid_request` with violations; an unknown path 404; an unexpected exception a
  logged 500 `internal`. Every error body is `ErrorResponse` with the request id.
- **Headers**: a plain ASGI middleware sets `X-Request-Id` (the caller's when valid) and
  `X-ReturnRing-Mode` on every response, errors and streams included.
- **Roles** (ADR-15 section 5): `apps.api.roles.authorize(request, method, path, body)`: the role
  of the person the body names (`ACTOR_FIELDS`: approver, author, inspector, requested_by,
  approved_by, asked_by), one of `DEMO_USERS`; 403 `forbidden_role` otherwise. **Contract
  addition**: the optional request header `X-ReturnRing-Actor` names the demo user of the UI
  session; when present it must be a demo user and equal the body's person. It is optional, so the
  generated contract (`contracts/json/api/`) is unchanged; it lands there with the next contract
  round.
- **Routers**: `routes/health.py`, `routes/operator.py`, `routes/manager.py`, `routes/other.py`.
  `routes.stubs.add_stubs(router, group)` registers a 501 stub for every endpoint of the group the
  router does not implement, with the contract's path, query and body models (422 as the real
  route), the role check (403) and the response model in OpenAPI. A builder adds a route above
  `add_stubs()` in its own module; a test checks that every endpoint of `ENDPOINTS` is registered
  exactly once. Implemented now: `/healthz`, `/readyz` and `GET /api/returns/{return_id}`.
- **Conformance harness** (`tests/api_harness.py`, fixtures in `tests/conftest.py`): a tiny
  generated store (scale 0.05, seed 7) with graph, features, anomalies, risk models and batch
  scores, its serving snapshot, a database after the reset, an app with a fixed clock and SQLite,
  and `validate_body(name, body)` against `contracts/json/api/<name>.schema.json` and the model.

### 6. Contract changes

- **`decisions.score_factors`** (nullable JSON, `contracts/sql.py`): the top 3 contributions to
  the risk given to the engine, as `ml.risk.explain` writes `shap_top3`. The case card shows them
  and the draft's facts cite them; a recompute version (US-310) stores its own. Null when the
  return is not scored. No CHECK: the content is checked by the models.
- **`model_versions` keys** of a decision: `risk` (the request model the engine read),
  `risk_customer`, `checkout`, `reference`, `value`, `evidence`, `anomalies`, from the snapshot
  manifest. `model_versions` is a free map in ADR-15.

### 7. Alternatives (`alternatives-v1-2026-10-13`)

The engine's proposal (recommended, with exactly its levers), then what fits the case: a size
exchange for a size return, a catalogue report for a guarantee return or a variant with an
anomaly, a check at the warehouse otherwise; the current terms when the proposal changes them; at
most three. Quantities over the 12 months after the decision are explicit hypotheses
(`ALTERNATIVE_ASSUMPTIONS`): Q1 adds 10% of the kept margin and spends a quarter of it on the
upgrade; Q3 and Q4 avoid 10% and 25% of the customer's returns (6 handling minutes each), lose
10% and 30% of the kept margin to abandonment and carry 6 EUR of expected repair cost; a Q4
cluster block avoids one undue discount per linked account; a size exchange keeps half the margin
of the item for a 4 EUR parcel; a check costs 12 operator minutes, a catalogue report 20, at the
hourly cost of the stored version. Every net benefit is `policy.simulator.net_benefit`, labelled
`<decision_id>/<kind>` with the unit costs of the stored version, nature simulated; no quantity
is a refund (rule 9).

### 8. Evidence components on the card

`features/evidence_quality` holds the counts the pipeline judged; the card's three components map
them as the pipeline judges them: observations are the linked-account observations when the graph
counted them; identifier completeness against 0.6; reason consistency against 0.5 once at least 2
withdrawal returns are judged, and against 0 (no requirement, said in its provenance) before, with
value 0 when there is no return at all.

### 9. Phase 4 integration (14 Oct 2026)

The operator (E3), manager (E6) and other (E4, E5, E7, E9) routes were merged onto phases 1 to 3
and walked end to end on a full-scale store (`scripts/demo_walk.py`). What changed in the core:

- **Evaluation report in the snapshot.** The export writes `reports/evaluation.json` (format
  `returnring.evaluation.v1`, `pipeline/serving/evaluation.py`) from the offline evaluation of
  `ml.risk` (`artifacts/eval/risk/metrics.json`, `ml.risk.run.metrics_path`): its FAIR-02
  aggregates only (counts and rates by dimension and segment, no customer), the evaluation
  instant, the customer model and the provenance. The manifest hashes it and records
  `reports.evaluation`; `metrics.json` itself is still never served. Without the offline
  evaluation the export writes no report and `/api/fairness` and `/api/kpi` answer 404 (rule
  12: no label at runtime, no invented rate).
- **Readiness of telemetry.** `/readyz` reports the telemetry sampler
  (`services.other.telemetry.component_check`): ok with an amd-smi sample of the last 60 s,
  disabled in cached mode and on a CPU, unavailable on a GPU whose sampler is silent (the
  status is then degraded). Its table is part of the contract (ADR-15 section 13).
- **The anomaly of a case card** skips cancelled catalogue tasks: the card and the manager's
  list show the same task, the latest one not cancelled.
- **`services.cases.propose` builds version 1 only.** Its `version` parameter is gone: a later
  version (a review or a recompute) is `services.operator.versions.next_version`, which also
  takes the person's outcome a review version needs.
- **Full-scale latency** (20,000 customers, 623 open returns, 374 product anomalies): the
  anomaly trend is `pipeline.anomalies.catalogue.weekly_counts` (numpy; equal to the `n` and
  `observed` of `weekly_series`, tested) on the variant's own lines (`Serving.variant_sales`),
  kept with the snapshot and computed at startup, so the anomaly list went from 82 s to under
  0.5 s; the queue reads only the decision columns it shows and computes the customer snapshots
  of new accounts at startup; and after the warm-up the service moves the snapshot out of the
  cyclic garbage collector (`gc.freeze`), whose full passes paused one request in seven for
  about 2 s. Tests pass their own snapshot and keep the collector as it is.
- **The demo walk** (`python -m scripts.demo_walk --base-url ...`, after the reset) walks the
  pitch over HTTP and checks every response against its JSON Schema and model, with the latency
  targets of US-301 (queue under 2 s) and US-403 (checkout under 1 s); `tests/test_demo_walk.py`
  (marker `e2e`) runs the same walk on the tiny snapshot in CI.
- **Data root.** The export reads a data root written by the current pipeline: a store, features
  and scores written before the phase 2 review (provenance origins over 300 characters, scores
  without their thresholds) are refused by `contracts.io`. Rebuild it with `make pipeline`.

### 10. Review of the running API (14 Oct 2026, ADR-15 section 15)

The review of the API (H1 to H40) changed the core of section 5 and the reset of section 3:

- **One clock, anchored at the reset.** `apps.api.clock.DemoClock` (`DEMO_CLOCK=on`, the
  default): 'now' is the snapshot instant plus the wall time elapsed since the demo reset. The
  reset still writes every row at the snapshot instant (two resets write the same rows), then
  stores the snapshot instant, the wall time the clock starts (its end) and the golden cases in
  the one row of `demo_reset`. `Runtime` anchors its clock there at startup and checks the row
  at most every 5 s before a transaction, so a reset made while the API runs restarts the
  clock; before any reset the clock starts at the snapshot instant when the service starts.
  `Runtime.wall_now()` keeps the wall clock for what is measured in real time (amd-smi samples).
  Tests inject a `FixedClock`, as before. On the read-only demo (public, or cached as
  `infra/compose.demo.yml` starts it) the demo clock is frozen at the snapshot instant (ADR-15
  sections 19 and 20, 14 Oct 2026); with writes on it runs.
- **The reset in three phases.** `services.cases.admit` decides every open return (one
  transaction each, golden first); `Generator.generate_each` makes the drafts outside any
  transaction with `LLM_CONCURRENCY` (32) model calls in flight; `services.generation.store`
  writes them in the same order, each with its id from the counters, so the ids stay
  deterministic. A case llm/ refuses is reported and stays draft_status pending; the others go
  on. Then `services.demo.record_reset` finds the golden cases (a link whose dispute changes a
  cluster is computed with `GraphView`, in the Rossi families first) and starts the clock. The
  summary adds `generation` (the paths and fallback reasons); the command exits 2 with the
  remedy when the snapshot cannot be read, and 1 when vLLM was unavailable for some drafts in
  `LLM_MODE=vllm`.
- **batch_generate** sends the cases with `LLM_CONCURRENCY` calls in flight and exits 2 when the
  vLLM configuration is refused (nothing could be cached).
- **Model calls** run on the runtime's own thread pool (`Runtime.model_call`, `LLM_CONCURRENCY`
  workers), never in a transaction and never on asyncio's default executor; `VLLM_TIMEOUT_S`
  (20 s) bounds a call; a vLLM configuration the client refuses is an unavailable client
  (`Generator.client_error`), so the template answers.
- **The price paid** (`services.prices`): the list price of a line less its share of the order's
  coupon, cached with the snapshot; the base of a claim and of the refund.
- **Readiness and logs.** `/readyz` is `not_ready` while the database, the scores or the models
  are missing, shows the clock and `public_read_only`, never a server path (the reason of a
  missing snapshot is a WARNING of the startup log), and checks that vLLM serves
  `GEMMA_MODEL` without following a redirect. The middleware logs one line per request
  (`returnring.api.access`: request id, method, path, status, duration) and refuses a body over
  8 MiB (413); `apps.api.main` configures logging with time and level; the engine hides SQL
  parameters from its errors.
- **Settings.** `DEMO_CLOCK`, `PUBLIC_READ_ONLY`, `LLM_CONCURRENCY`, `VLLM_TIMEOUT_S` (ADR-15
  section 15); `Settings.served_llm_mode` is `vllm` in cached mode.

Measured on the full synthetic store is reported with the commit of this round. The
infrastructure the round needs (the serving mount, the reset job, the healthcheck reading the
body of `/readyz`, `GEMMA_MODEL` and `PUBLIC_READ_ONLY` in compose, basic auth and the body
limit in Caddy, the telemetry sampler) is listed in ADR-15 section 15 for the infrastructure
round, done in [ADR-17](ADR-17-deployment-and-demo-package.md).

## Consequences

- **Infra** (not changed here; done in ADR-17): the `api` service mounts `serving/` read-only
  and sets `SERVING_DIR`; the `pipeline` job writes `serving/` after the scoring (`python -m
  pipeline.serving`); the reset runs once after the database is up (`python -m apps.api.reset`);
  the GPU profile runs `python -m apps.api.batch_generate` and copies `serving/` (with
  `llm_cache/`) to the demo server. `apps/api/Dockerfile` now copies `ml/` (the API runs the risk
  and checkout models).
- **Route builders** start from the stubs, `services/` (cases, decisions, generation,
  alternatives, audit, outbox, seeds) and the harness; a decision version is written with
  `services.decisions.insert_version` and served with `view_of`.
- **Known limits.** A live score of an adapter return takes about 3 s at full scale (the batch
  serves every store return). The diminished-value claim of an inspection is served with a
  provenance built from its rule and inspection (the inspection builder may refine it).
  Telemetry in `/readyz` comes from the sampler (section 9). The reason-consistency component
  shows a threshold of 0 before two returns are judged: the contract has no "not judged" state.
