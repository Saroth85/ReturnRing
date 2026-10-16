# ReturnRing

ReturnRing helps a fashion e-commerce choose and explain the best action on every return, within
EU consumer law. Statistical models estimate return probability, abuse risk, customer value and
product anomalies; a rules engine picks the admissible actions; a simulator estimates their net
benefit under explicit assumptions; Gemma 4 through AMD Token Factory or local vLLM explains
each decision and drafts the customer message. A person approves every restrictive action.

Built for the AMD Developer Hackathon ACT III (lablab.ai), Track 3 "Reinvent Commerce". The full
specification is [docs/spec.md](docs/spec.md) (in Italian); the decisions are in
[docs/adr/](docs/adr/README.md).

The current delivery plan is [ADR-18](docs/adr/ADR-18-amd-api-and-training.md): integrate Gemma
through AMD Token Factory and train XGBoost and GraphSAGE on an AMD GPU. CPU versus MI300X
benchmarks are retired from the submission and the System screen; older sections below preserve
the original self-hosted deployment recipe and historical experiments, not completed GPU claims.

For live AMD inference, set `LLM_MODE=tokenfactory`, `RADEON_API_KEY` and `DEMO_MODE=live` in the
server environment. The adapter uses the `Gemma-4-31B-IT` catalog ID, one in-flight call and shared
quota backoff. Keep `DEVICE=cpu` when the API runs on a CPU: the remote GPU is managed by AMD.
Use synthetic demo inputs only. Photo inspections go to manual review because this endpoint is
text-only. Existing local-vLLM and mock modes remain available.

`python -m scripts.check_tokenfactory` exercises the real HTTP regeneration route on a small
generated store, checks the contract and unchanged decisions, and writes an integration report.
It asks for the API key with a hidden prompt; it never writes the key to a file.

Offline training runs independently of inference, on the GPU notebook:

```bash
python -m scripts.train_amd risk --data data/gpu-synthetic --nature synthetic
python -m scripts.train_amd graphsage-embed --data data/gpu-synthetic --nature synthetic
# With the original verified ASOS tables available:
python -m scripts.train_amd propensity --data data --nature real
python -m scripts.train_amd graphsage-asos --data data --nature real
```

Each command requires its AMD-compatible Python environment, refuses CPU fallback and writes a
new directory under `artifacts/amd-training/`. Quality metrics and training duration are retained;
no CPU/GPU speedup is calculated. These commands are recipes, not evidence that a run completed.

> **How to read the numbers.** Every figure in this README names its origin, its period and its
> nature: *real* (measured on real data or real hardware), *synthetic* (measured on the generated
> store, whose abuse behaviours are design hypotheses) or *simulated* (computed under stated
> assumptions). The demo's golden cases are demo cases, never a measure of accuracy.

## Quick start: three commands, CPU only, no GPU

You need Docker with Compose v2, GNU make and about 8 GB of RAM. The 8 GB is an estimate; what
was measured: the API keeps the serving snapshot in memory, and its process held about 1.6 GB
after the warm-up; the peak during the demo walk varied between runs, up to about 2.2 GB
(resident memory of the API process run on the host, not in a container, full synthetic store,
14 Oct 2026; real, measured once and not repeated in Docker).

```bash
cp .env.example .env
make demo-data
make dev-up
```

Then open **http://localhost:8080**. The API answers on http://127.0.0.1:8000 (`/readyz` says
what is running). Everything runs on the CPU with the deterministic mock LLM (`DEVICE=cpu`,
`LLM_MODE=mock`): no GPU, no key, no call outside your machine.

- `make demo-data` builds the API image and runs the whole pipeline in the `pipeline` job
  container: the synthetic Navigli Fashion store (20,000 customers, 12 months) generated from the
  committed real ASOS calibration, the account graph, the features, the product anomalies, the risk
  models with their batch scores, and the serving snapshot `serving/` the API reads. The pipeline
  commands took 9 min 39 s on 4 shared vCPUs (Intel Xeon 2.8 GHz, 15.7 GiB RAM), measured on
  14 Oct 2026 outside Docker (nature: real timing of a run on synthetic data); the first image
  build adds a few minutes.
- `make dev-up` starts PostgreSQL, the **reset** (every open return of the snapshot decided again,
  golden cases first: 623 returns in 42 s on the same machine and day, PostgreSQL 16, real timing),
  then the API (healthy 38 s after its start there), the UI and the Caddy proxy. Every
  `make dev-up` starts from the same state; `make dev-up` alone also runs `make demo-data` when
  `serving/` is missing.

In the UI, pick who you are under "Acting as": **sara** (returns operator), **luca** (operations
manager) or **ahmed** (warehouse). The pitch follows four golden cases of the synthetic store:
Giulia (a good customer who often gets the size wrong), Marco's ring of linked accounts, the
Rossi family (benign linked accounts: a shared address and, in one variant, a shared card) and Elena (a defective jacket: guarantee path and a
catalogue task, no restriction).

| Command | What it does |
| --- | --- |
| `make reset` (dev) or `make demo-reset` (cached) | the demo again from the same state, on the running stack (US-903); run one before `make demo-walk` |
| `make demo-walk` | walks the pitch over HTTP and checks every response against the contract (host Python: `pip install -r requirements/dev.txt`); it approves cases, so run `make reset` (dev) or `make demo-reset` (cached) before it; on a read-only demo it checks that every write of the pitch is refused |
| `make dev-logs`, `make dev-down` | logs, stop (the database volume is kept) |
| `make test`, `make lint`, `make licenses` | the CPU test suite, ruff and mypy, the licence gate |

On the same machine and day (14 Oct 2026), `make demo-walk`'s checks passed on the dev profile:
first queue page in 0.578 s (US-301 target: under 2 s) and the simulated checkout in 0.092 s
(US-403 target: under 1 s); real timings of one run on synthetic data.

## The cached demo, with the GPU off (US-901)

The public demo runs without a GPU: `DEMO_MODE=cached`, the GPU containers off, and the API serving
the outputs Gemma produced on the MI300X, labelled **"Precomputed on AMD MI300X"** on every
screen. Its views are read-only (spec 11). The case assistant needs the live model, so in cached
mode it answers that the GPU is off; the case card keeps the precomputed explanation.

The demo package comes from one GPU session: `make batch-generate` writes Gemma's draft of every
open return into `serving/llm_cache/`, then `make demo-export DEMO_GPU="AMD Instinct MI300X (AMD
Developer Cloud)"` packs the snapshot, the cache and the benchmark results into `demo-cache/`
with a `MANIFEST.json` holding the sha256 of every file, the hardware and the model of every
draft (`demo-cache.tar.gz` to copy). The batch stops unless the local vLLM serves `GEMMA_MODEL`
and records the device of each draft. `DEMO_GPU` has no default, and the export refuses drafts
of a model other than `GEMMA_MODEL`, or of a batch that did not run with `DEVICE=gpu`, unless
the package is declared a rehearsal (`REHEARSAL=1`). The API shows the MI300X label only when
the package's `MANIFEST.json` says its drafts were made on the MI300X by the model it names;
any other package, such as a CPU rehearsal, is labelled **"Precomputed in a rehearsal, not
verified on AMD MI300X"**.

The package is published as a GitHub release asset after the GPU session; until then the laptop
path below is for the team, who copy `demo-cache.tar.gz` from the droplet. The `demo-*` targets
run the packaging script with the host `python3` (Python 3.8 or later, standard library only).
On the demo server, or on a laptop:

```bash
make demo-import     # unpacks demo-cache.tar.gz and checks every hash
make demo-local      # http://localhost:8080 (a laptop), or: make demo-up (public, HTTPS)
make demo-reset      # the cached demo again from the same state (before make demo-walk)
make demo-down       # stops it (the database volume is kept)
```

Status on 14 Oct 2026: the MI300X session that produces the package has not run yet, so no public
URL is listed here; it is added with the submission. The packaging, the cached reset and the walk
were rehearsed on the CPU with a fake vLLM server standing in for Gemma: 623 cached drafts were
served and every step of `make demo-walk` passed. On the read-only demo (the default) the walk
reads the same cases, the seven writes of the pitch are refused with 403 and the simulated
checkout still evaluates; with `PUBLIC_READ_ONLY=false` the writes pass. In both the case
assistant answered the 503 documented for cached mode.

## The GPU session (AMD MI300X)

The droplet runbook is [infra/spikes/README.md](infra/spikes/README.md): the spikes, "The GPU
session" (benchmarks B1 to B4, GraphSAGE) and "The demo export" (live stack, batch generation,
package). On the droplet, `make gpu-up` starts vLLM first, then the whole stack with HTTPS on
`PUBLIC_HOST` and basic auth on the telemetry data and the writes, for the user `admin` (`make
admin-password` prints the hash for `.env`; `PUBLIC_HOST` and the hash are required,
`.env.example` leaves both empty).

## Architecture

One docker-compose file, three profiles: `dev` (laptop, CPU and mock LLM), `gpu` (the MI300X
droplet) and `demo` (cached, GPU off). Compose files: [infra/](infra/docker-compose.yml).

| Container | Profiles | What it does |
| --- | --- | --- |
| proxy (Caddy) | all | one origin for the UI and `/api`; HTTPS and basic auth on a public server ([infra/Caddyfile](infra/Caddyfile)) |
| ui (React, Vite, TypeScript, nginx) | all | operator, manager, warehouse and platform views; simulated checkout; case assistant |
| api (FastAPI) | all | REST and streaming endpoints, policy engine, legal guard, validator, drafts ([ADR-15](docs/adr/ADR-15-api-and-persistence-contract.md)) |
| reset (one-shot) | all | recreates the database and decides every open return of the snapshot ([ADR-16](docs/adr/ADR-16-serving-snapshot.md)) |
| postgres | all | decisions, audit log, outbox, terms snapshots, inspections, telemetry |
| pipeline (job) | `make demo-data` | store, graph, features, anomalies, risk models, serving snapshot |
| vllm (Gemma 4 31B-it) | gpu | explanations, customer messages, case assistant, photo checklist; internal network only |
| batch-generate (job) | `make batch-generate` | Gemma's drafts of the open returns, for the cached demo |
| trainer-gbm, trainer-graphsage | gpu | XGBoost (AMD Finance toolkit) and GraphSAGE (DGL on ROCm) |
| telemetry, telemetry-sampler | gpu | amd-smi every 5 s into the database, for the admin page (US-905) |

```text
ASOS GraphReturns (real, CC BY 4.0) -> calibration (committed)
  -> generator: synthetic store + ground truth (data/, data/truth/)        offline jobs only
  -> graph, features, anomalies -> risk models + batch scores (training reads data/truth)
  -> serving/ snapshot: store, features, scores, models; hashed; no ground truth
  -> reset -> PostgreSQL <- api <- proxy <- ui
                             api -> vLLM (Gemma 4): explanation and draft -> validator -> template fallback
```

The rules the code enforces (CLAUDE.md):

- **The LLM never decides or predicts.** Scores come from `ml/`, actions from
  `policy/engine.py`; an output that changes the decision is rejected by `llm/validator.py`.
- **No external model or API at runtime**: the only model endpoint is the local vLLM.
- **No running service reads the ground truth**: the API, the reset, the UI and the proxy never
  mount `data/` or `artifacts/`; only offline jobs do (tested in `tests/test_infra.py`).
- **Decision-time features only** (`available_at <= decision_time`); gender, country, age, names
  and raw identifiers are never features; customer value never feeds the risk score.
- **Linked accounts, never "same person"**; every number carries origin, period and nature.
- **Net benefit, not savings**: the refund owed to the consumer is never counted as a saving
  (`policy/simulator.py`, versioned assumptions).

## How the risk is computed

Method: [docs/risk-model.md](docs/risk-model.md). Measured results:
[docs/results/us-204-risk.md](docs/results/us-204-risk.md), rewritten from the data by one
command, regressions included.

1. **Path first.** A defect or non-conformity return goes to the guarantee path before any
   score (rule 4): no risk, no restrictive lever (the Elena case).
2. **Evidence quality.** Observations, identifier completeness and reason consistency; below the
   threshold the case is "insufficient evidence": standard terms and the missing evidence listed
   (US-307, [docs/customer-features.md](docs/customer-features.md)).
3. **Abuse risk.** XGBoost per customer and per return request on decision-time features:
   behaviour (return rate, speed, reasons, coupons), the account graph (accounts linked by shared
   devices, addresses and cards, with the evidence of each link, common nodes down-weighted:
   [docs/account-graph.md](docs/account-graph.md)) and the return residual against a product and
   cohort reference model, never the personalized predictor (rule 8). Isotonic calibration; the
   threshold is the lowest calibrated risk with precision 0.8 or more on validation. The three
   SHAP factors shown are contributions to the score, not causes.
4. **Customer value** (12-month net margin, reconciled with the orders) places the case in a
   quadrant with the risk, and the rules engine maps quadrant and evidence to admissible levers;
   value is never an abuse signal.
5. **Return probability per purchase** (checkout, US-201) is trained on real ASOS data.

| Figure | Value | Origin | Period | Nature |
| --- | --- | --- | --- | --- |
| Customer risk model A3, AUPRC on held-out test customers | 0.980 (6,009 customers, 271 abusive) | main synthetic store, [us-204-risk.md](docs/results/us-204-risk.md) | features at 2026-10-01, store 2025-10-01..2026-09-30 | synthetic |
| The same model on the out-of-distribution store | AUPRC 0.840; promo-ring recall 0.363 | OOD store (US-105), never used to train | same | synthetic |
| Rules-only baseline A0 (returnguard-like), same rows | AUPRC 0.633 | main synthetic store | same | synthetic |
| Return probability, decision-time protocol B | F1 0.739 against 0.721 for the best one-feature rule | ASOS GraphReturns test split, [us-201-propensity.md](docs/results/us-201-propensity.md) | 2021-10-01..2021-11-30 | real |
| GraphSAGE embeddings added to A3 (A4) | AUPRC 0.977, a regression of 0.003 | main synthetic store, [us-202-graphsage.md](docs/results/us-202-graphsage.md) | features at 2026-10-01 | synthetic |

The main store measures consistency with the generator, which plants the behaviours the features
look for; the out-of-distribution store changes the ring, family and identifier rules and is the
stress test. Product anomalies (variant, reason, sales week against the category base) are in
[docs/results/us-208-product-anomalies.md](docs/results/us-208-product-anomalies.md).

## What Gemma does, and what it does not

Gemma 4 31B-it, served by vLLM on the MI300X, writes text around decisions that code has already
made ([ADR-14](docs/adr/ADR-14-llm-contracts-v2.md)):

- the operator **explanation**: observed data, contributions to the score and hypotheses to
  check, each number cited from a fact built by code;
- the **customer message** in Italian and English: Gemma chooses among reviewed sentences of the
  phrasebook (`llm/phrasebook.py`); amounts, terms and deadlines come from template fields and the
  legal clause is fixed text (`llm/legal_clause.py`), never generated (rule 10);
- the **case assistant**: up to five read-only tool calls per question, the evidence consulted
  and missing beside the answer, no write tool;
- the **inspection checklist** from a returned item's photo; a compensation is proposed only from
  observations a person confirms.

It never decides, predicts, scores or sets an amount. Every output goes through
`llm/validator.py`: JSON shape, the decision echoed unchanged, every number grounded on a cited
fact, banned and accusatory terms. A failed output is regenerated once, then replaced by the
template, labelled as such. In `LLM_MODE=mock` a deterministic mock follows the same path.

## The legal guard

`policy/legal_guard.py` checks every decision of the engine, with 100% line and branch coverage
in CI (NFR-14):

- the 14-day right of withdrawal is never denied, and the refund stays within 14 days (rule 3);
- restrictive levers apply only to **future** orders and are shown at checkout, saved with the
  order as a terms snapshot: the terms of an order are those shown when it was placed;
- defect and non-conformity returns follow the legal guarantee, outside abuse scoring (rule 4);
- every restrictive action (quadrants Q3 and Q4) needs a person who approves it, recorded in the
  audit log, and a review date: 90 days by default (a design hypothesis), never more than 180
  (rule 5, GDPR article 22). The database refuses a restrictive decision without both.

These constraints are encoded as explicit, testable rules; they must be validated by a
consumer-law professional before any real use.

## AMD benchmarks

Method and the four rules (same data and configuration on both devices, separate times for cold
start, training, scoring and generation, the full cost per decision, growing loads):
[docs/benchmarks.md](docs/benchmarks.md).

| Benchmark | What | MI300X result |
| --- | --- | --- |
| B1 | XGBoost training and scoring on ASOS, CPU against MI300X | pending (GPU session) |
| B2 | GraphSAGE on ASOS, CPU against MI300X, F1 against the paper | pending |
| B3 | Gemma latency p50 and p95, tokens per second, batch 1 to 32, cost per decision | pending |
| B4 | nightly pipeline duration and GPU utilisation (amd-smi) | pending |

The only run on file, [docs/results/benchmarks-cpu.md](docs/results/benchmarks-cpu.md) (run
`20261014T002158Z-cpu`, 14 Oct 2026, 4 shared vCPUs, no GPU), checks the harness: B1 on the CPU
only, B2 skipped, B3 a dry run against a fake server (no Gemma), B4 on the CPU. It is not a GPU
comparison and `GET /api/benchmarks` does not serve it: the API serves GPU runs only and shows
every benchmark as pending until then.

## Licences

- **Code**: MIT ([LICENSE](LICENSE)).
- **Licence gate** (US-002): `make licenses` (`scripts/check_licenses.py`, pip-licenses) fails CI
  on GPL, AGPL, LGPL or an unknown licence in the Python dependencies, and `npm run
  check:licenses` on the UI's npm tree; only MIT, BSD, Apache-2.0, ISC, PSF and PostgreSQL are
  allowed at runtime (rule 15). Hypothesis (MPL-2.0) is a development tool, never distributed.
- **Container images** pulled unmodified and never redistributed
  ([ADR-12](docs/adr/ADR-12-container-licences.md)). The GPU images, PostgreSQL and Caddy are
  pinned by tag and digest; the build bases of the API and of the UI by version tag only
  (`python:3.12-slim`, `node:22.22.0-bookworm-slim`, `nginx:1.27-alpine`). vLLM
  (`vllm/vllm-openai-rocm`, Apache-2.0), DGL on ROCm (`rocm/dgl`, Apache-2.0, PyTorch
  BSD-3-Clause), the ROCm base (`rocm/dev-ubuntu-24.04`: ROCm components under MIT, NCSA and other
  licences, including GPL-3.0 tools), amd_xgboost (Apache-2.0), PostgreSQL (PostgreSQL License),
  Caddy (Apache-2.0), nginx (BSD-2-Clause), the Python base of the API image (`python`: Python
  under the PSF License, on Debian packages under their own licences), Node.js (MIT; the UI's
  build stage only, not in the served image). Gemma 4 weights: Apache-2.0 on Hugging Face.

Runtime Python dependencies (`requirements/base.txt`), read with pip-licenses 5.5.5 on
14 Oct 2026:

| Package | Version | Licence |
| --- | --- | --- |
| fastapi | 0.143.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| pydantic | 2.14.1 | MIT |
| sqlalchemy | 2.1.4 | MIT |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |
| asyncpg | 0.32.0 | Apache-2.0 |
| pyarrow | 26.0.0 | Apache-2.0 |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| pandas | 3.0.6 | BSD-3-Clause |
| polars | 2.0.0 | MIT |
| narwhals | 2.27.1 | MIT |
| xgboost-cpu | 3.2.0 | Apache-2.0 |
| scipy | 1.18.1 | BSD-3-Clause |
| scikit-learn | 1.9.1 | BSD-3-Clause |
| joblib | 1.6.0 | BSD-3-Clause |
| threadpoolctl | 3.7.0 | BSD-3-Clause |
| networkx | 3.7 | BSD-3-Clause |

The UI's runtime packages (React, React Router, TanStack Query, Radix UI, Cytoscape.js, Recharts,
clsx) are MIT and lucide-react is ISC, checked by `npm run check:licenses` in CI.

**Data.** ASOS GraphReturns: McGowan et al. (2023), "A Dataset for Learning Graph Representations
to Predict Customer Returns in Fashion Retail", arXiv:2302.14096, https://osf.io/c793h, licensed
CC BY 4.0. It is downloaded by `make asos`, used for the calibration and the ASOS benchmarks, and
never committed or redistributed; the committed calibration holds aggregates of its train split.

## Limits, declared

- **Abuse is simulated.** No public dataset holds linked accounts, so rings, families and abuse
  behaviours are generated (design hypotheses calibrated on real ASOS aggregates). Abuse detection
  must be verified in a pilot with a real merchant; ASOS supports the return model only.
- **Out of distribution, rings are mostly missed**: promo-ring recall 0.363 for the customer
  model on the OOD store, whose rings are smaller, slower and share fewer identifiers (synthetic,
  features at 2026-10-01, [us-204-risk.md](docs/results/us-204-risk.md)). The account graph finds
  few of them ([account-graph.md](docs/results/account-graph.md)).
- **FAIR-02 fails**: the false-positive gap between main product categories is 6.78 points,
  above the 5-point limit (synthetic, held-out test customers at 2026-10-01). FAIR-03 passes at
  the evaluation time but not at every month end: 1 benign shared account in Q3 or Q4 on
  2025-12-01 (in-sample backtest). Both are reported as measured (rule 13).
- **GraphSAGE adds nothing here**: A4 is slightly worse than A3 on the same rows (table above).
- **The LLM checks are blocklists for free text** ([ADR-14](docs/adr/ADR-14-llm-contracts-v2.md),
  residual risks): a paraphrase can slip past the validator in an operator explanation or an
  assistant answer. That text reaches the operator only, labelled as a Gemma draft beside the
  engine's decision; customer messages are built from reviewed sentences and code fields, and a
  person approves every restrictive action. Some correct statements are over-blocked and fall
  back to the template.
- **GPU numbers are pending** until the MI300X session; the cached demo shows the outputs of that
  session only.
- **Legal**: constraints encoded as rules and tests, to be validated by a professional; not legal
  advice.

## Development

```bash
pip install -r requirements/dev.txt
make test       # CPU tests, LLM mocked (US-003)
make lint       # ruff + mypy (strict)
make licenses   # licence gate (rule 15)
make pipeline   # the same chain as make demo-data, on the host
```

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs the Python suite with the 100%
coverage gate on `policy/` and `llm/`, the generated-contract checks, the UI checks (lint,
typecheck, unit tests, generated types, licences, build), the GraphSAGE tests with PyTorch on the
CPU, and gitleaks (US-004: no secret in the repository; only `.env.example` is versioned).

The UI's end-to-end tests against a running API are not in CI, which has no API server: after
`python -m apps.api.reset` and with the API up, `cd apps/ui && RETURNRING_API_URL=http://127.0.0.1:8000
npm run test:e2e:live` walks the demo path through the UI, and its read-only spec runs against an
API started with `PUBLIC_READ_ONLY=on` ([apps/ui/README.md](apps/ui/README.md), "Live end-to-end tests").

## Team and hackathon

Built by Rosario for the AMD Developer Hackathon ACT III (lablab.ai, October 2026), Track 3, with
the Google Gemma prize in mind. AMD Developer Cloud, AMD Instinct MI300X, ROCm, AMD Finance toolkit
(XGBoost), DGL on ROCm, vLLM, Gemma 4, FastAPI, React, PostgreSQL. Developed with Claude Code as a
coding assistant; no hosted model is called by the product at runtime.
