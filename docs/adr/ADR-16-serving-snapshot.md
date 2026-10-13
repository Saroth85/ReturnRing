# ADR-16: Serving snapshot and API core

Status: accepted, 13 Oct 2026. Covers the data access of the API (CLAUDE.md rules 7, 11 and 12,
NFR-03, DATA-03), the API core the three route builders extend (ADR-15 sections 2, 4 and 5), the
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

## Consequences

- **Infra** (not changed here): the `api` service mounts `serving/` read-only and sets
  `SERVING_DIR`; the `pipeline` job writes `serving/` after the scoring (`python -m
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
  Telemetry is `disabled` in `/readyz` until its builder adds it. The reason-consistency component
  shows a threshold of 0 before two returns are judged: the contract has no "not judged" state.
