# ReturnRing

ReturnRing is a return-decision assistant for EU fashion merchants.
It combines:

- statistical models for return propensity, abuse risk, customer value and product anomalies
- a rules engine that selects only admissible actions
- a simulator that estimates the economic impact of those actions
- Gemma 4 for explanations and message drafting
- human approval for every restrictive action

The product is built for the AMD Developer Hackathon ACT III, Track 3.
The source of truth for behavior is [docs/spec.md](docs/spec.md); architecture decisions live in [docs/adr/](docs/adr/README.md).

## Live demo

**[Open the live demo](https://returnring-demo-production.up.railway.app/demo)** — read-only,
with 1,000 fictional customers and a live Gemma investigator via AMD Token Factory.
The demo uses a cached serving snapshot and resets on restart.

## What the system does

ReturnRing helps a merchant:

- decide the best action for each return
- explain the decision in clear language
- keep the legal guarantee path separate from abuse scoring
- show customer-facing terms at checkout and store the snapshot that was shown
- support warehouse inspection, catalogue tasks, audit, KPI, fairness and portfolio views

The system is intentionally split into separate concerns:

- `apps/api` — FastAPI application and business services
- `apps/ui` — React + Vite browser app
- `pipeline` — offline generation, features, graph and serving export
- `ml` — training, evaluation and batch scoring
- `policy` — decision engine, legal guard and simulator
- `llm` — prompts, client, validator and message generation
- `contracts` — SQL schema, JSON schemas, IO and shared contract helpers

## Key guarantees

- The LLM never decides or predicts.
- Scores come from `ml/`; admissible actions come from `policy/engine.py`.
- The 14-day right of withdrawal is never denied.
- Defect/non-conformity returns go through the guarantee path before scoring.
- Every restrictive action needs a human approver and a review date.
- Running services never read `data/truth/`; offline training/evaluation jobs do.
- Customer value never feeds the risk score.
- All numbers shown in the UI and docs carry provenance, period and nature.

## Repository layout

```text
apps/api   FastAPI app, routes, services, runtime, settings, reset
apps/ui    React UI, routes, shared shell, mock/live adapters
contracts  SQL schema, JSON schemas, examples and IO helpers
pipeline   synthetic store, ASOS ingest, features, graph, anomalies, serving export
ml         propensity, risk, GraphSAGE and batch scoring
policy     engine, legal guard, simulator
llm        prompts, generator, client, validator, legal clause
infra      compose files, Dockerfiles, Caddy, training images and runbooks
docs       spec, ADRs, results and project documentation
tests      automated test suite
```

## Quick start

You need Docker with Compose v2, GNU make and roughly 8 GB of RAM.

```bash
cp .env.example .env
make demo-data
make dev-up
```

Then open:

- http://localhost:8080 for the UI
- http://127.0.0.1:8000 for the API

This starts the CPU demo with the deterministic mock LLM (`DEVICE=cpu`, `LLM_MODE=mock`).

Useful commands:

| Command | What it does |
| --- | --- |
| `make demo-data` | builds the serving snapshot and offline artifacts |
| `make dev-up` | starts PostgreSQL, reset, API, UI and proxy |
| `make reset` / `make demo-reset` | resets the demo state |
| `make demo-walk` | walks the demo over HTTP and checks the contract |
| `make test` | runs the CPU test suite |
| `make lint` | runs Ruff and mypy |
| `make licenses` | checks runtime/license compliance |

## Demo modes

### Cached demo

The public demo runs in cached mode (`DEMO_MODE=cached`) without a GPU.
The API serves precomputed outputs and the UI marks them as produced on AMD.
This mode is read-only.

### Live AMD inference

For live AMD inference, use:

- `LLM_MODE=tokenfactory`
- `RADEON_API_KEY`
- `DEMO_MODE=live`

Token Factory receives synthetic demo inputs only.

### Offline GPU training

Training and benchmark jobs run separately from the API and UI:

```bash
python -m scripts.train_amd risk --data data/gpu-synthetic --nature synthetic
python -m scripts.train_amd graphsage-embed --data data/gpu-synthetic --nature synthetic
python -m scripts.train_amd propensity --data data --nature real
python -m scripts.train_amd graphsage-asos --data data --nature real
```

## Architecture at a glance

- The API reads a verified `serving/` snapshot, not raw ground truth.
- Decision-time features are built before request scoring.
- The UI is split into four areas: operator, agent, manager and platform.
- The contract registry lives in `apps/api/schemas.py`.
- The generated API/UI contract files in `contracts/json/` keep the browser and server aligned.

## Documentation map

- [docs/spec.md](docs/spec.md) — product specification and user stories
- [docs/adr/](docs/adr/README.md) — architecture decisions
- [docs/ui-design.md](docs/ui-design.md) — UI ownership and screens
- [docs/results/](docs/results) — measured outputs
- [docs/release-guide.md](docs/release-guide.md) — submission release guide
- [docs/amd-runbook.md](docs/amd-runbook.md) — AMD Developer Cloud runbook

## Development

```bash
pip install -r requirements/dev.txt
make test
make lint
make licenses
make pipeline
```

The UI has its own local workflow in [apps/ui/README.md](apps/ui/README.md).

## Licences

- Code: MIT
- Data: CC BY 4.0 for ASOS GraphReturns
- Runtime dependencies are restricted by the licence gate documented in this repo

See the licence details and the dependency table in the main README for the current pinned stack.

## Limits, declared

- Abuse is simulated; linked-account behavior is a design hypothesis calibrated on real ASOS aggregates.
- GraphSAGE and GPU benchmarks are reported as measured results only when a run is available.
- Legal constraints are encoded as rules and tests and still need professional review before real use.

## Team and hackathon

Built by Rosario for the AMD Developer Hackathon ACT III (lablab.ai, October 2026), Track 3.
The project uses AMD Developer Cloud, AMD Instinct MI300X, ROCm, AMD XGBoost, DGL on ROCm, vLLM,
Gemma 4, FastAPI, React and PostgreSQL.
