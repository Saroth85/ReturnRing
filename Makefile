.PHONY: dev-up dev-down dev-logs demo-data reset demo-walk \
	demo-export demo-import demo-verify demo-up demo-local demo-reset demo-down admin-password \
	test test-gpu test-asos lint licenses asos asos-verify \
	generate generate-ood graph features anomalies risk score results propensity serving pipeline bench \
	gnn-asos embeddings gnn-results gnn-test ui-install ui-types ui-check ui-e2e \
	bench-cpu

ENV_FILE ?= .env
# Data pipeline (phase 1): the committed real calibration (ASOS train-split aggregates, US-101).
CALIBRATION ?= pipeline/calibration/asos_calibration.json
SEED ?= 42
SCALE ?= 1.0
SOURCE_DIR ?=
# The pipeline job writes data/ as the owner of the checkout (infra/docker-compose.yml).
export HOST_UID ?= $(shell id -u)
export HOST_GID ?= $(shell id -g)
COMPOSE = docker compose -f infra/docker-compose.yml --env-file $(ENV_FILE)
# demo uses an override file, so the switches of .env (DEVICE, LLM_MODE, DEMO_MODE) cannot leak
# into the cached demo.
DEMO = docker compose -f infra/docker-compose.yml -f infra/compose.demo.yml --env-file $(ENV_FILE) --profile demo
# Optional, for a self-hosted server reachable from the Internet (make demo-up): HTTPS on
# PUBLIC_HOST and basic auth (spec 11). Never deployed publicly: the public demo runs on Railway
# (docs/railway-demo.md).
PUBLIC = -f infra/compose.public.yml
LOAD_ENV = set -a && . ./$(ENV_FILE) && set +a
# The proxy image of infra/docker-compose.yml, for `make admin-password`.
CADDY_IMAGE = caddy:2.11.7-alpine@sha256:d8542f48d34a9cf4e4c11a478865229840e87e4c96ea3f439101f31a5d35f75f
# The cached demo package (make demo-export): serving snapshot with llm_cache/, benchmark results,
# MANIFEST.json with the sha256 of every file. Read by compose.demo.yml.
DEMO_DIR = demo-cache
API_URL ?= http://127.0.0.1:8000

# ---- The judge's path (README, US-904): cp .env.example .env; make demo-data; make dev-up ----
# CPU and the mock LLM, no GPU. demo-data runs the whole pipeline in the pipeline job container
# (its commands took 9 min 39 s on 4 shared vCPUs on 14 Oct 2026, plus the image build) and
# writes data/, artifacts/ and the serving snapshot serving/. dev-up builds the images and starts
# postgres, the reset (every open return decided again, golden cases first), the api, the ui and
# the proxy on http://localhost:8080.
# The folders exist before Docker mounts them, so they belong to the host user, not to root.
$(ENV_FILE):
	@echo "$(ENV_FILE) is missing: run cp .env.example $(ENV_FILE) first (README, Quick start)"; exit 1

demo-data: $(ENV_FILE)
	mkdir -p data artifacts serving
	SEED=$(SEED) SCALE=$(SCALE) $(COMPOSE) run --rm --build pipeline

dev-up: $(ENV_FILE)
	@test -f serving/manifest.json || { echo "No serving snapshot in serving/: running make demo-data first"; \
	  $(MAKE) demo-data; }
	$(COMPOSE) --profile dev up -d --build
	@echo "ReturnRing (CPU, mock LLM): http://localhost:8080 - API http://127.0.0.1:8000/readyz"

dev-down:
	$(COMPOSE) --profile dev --profile demo down --remove-orphans

dev-logs:
	$(COMPOSE) --profile dev logs --tail 100 -f

# US-903: the demo again from the same state, in one command, on a running stack.
reset:
	$(COMPOSE) --profile dev run --rm reset

# The golden walk of the pitch over HTTP against the running API (scripts/demo_walk.py, after a
# reset; it approves cases). Runs on the host with requirements/dev.txt installed.
demo-walk:
	mkdir -p artifacts
	python -m scripts.demo_walk --base-url $(API_URL) --report artifacts/demo-walk.json

# ---- The cached demo package (US-901, optional): scripts/demo_export.py ----
# Packs serving/ (with the drafts of serving/llm_cache/, if any) and bench/results/, with the
# sha256 of every file. Runs with the host python3, 3.8 or later (standard library only). Only CPU
# rehearsal packages were ever built (14 and 15 Oct 2026, ADR-17); no MI300X package exists.
# REHEARSAL=1 packs drafts that are not from a GPU batch (a fake vLLM server on a CPU, another
# model) and ALLOW_EMPTY_CACHE=1 a package without drafts, each labelled as a rehearsal.
# DEMO_GPU has no default: it names the GPU only for a package made on one, where every draft is
# GEMMA_MODEL's (.env) from a batch run with DEVICE=gpu; only then does the demo show its MI300X
# label (rules 11 and 13).
DEMO_GPU ?=
demo-export: $(ENV_FILE)
	$(LOAD_ENV) && python3 -m scripts.demo_export build --serving serving --bench bench/results \
	  --out $(DEMO_DIR) --generated-on "$(DEMO_GPU)" --model "$$GEMMA_MODEL" \
	  $(if $(REHEARSAL),--rehearsal) $(if $(ALLOW_EMPTY_CACHE),--allow-empty-cache)
	tar -czf $(DEMO_DIR).tar.gz $(DEMO_DIR)
	@echo "$(DEMO_DIR).tar.gz: where the demo runs, make demo-import, then make demo-local"

demo-import:
	tar -xzf $(DEMO_DIR).tar.gz
	python3 -m scripts.demo_export verify $(DEMO_DIR)

demo-verify:
	python3 -m scripts.demo_export verify $(DEMO_DIR)

# ---- The cached demo (US-901, optional): DEMO_MODE=cached, mock LLM, read-only by default ----
# demo-local: a laptop, on http://localhost:8080 (the CPU rehearsals of ADR-17). demo-up: the same
# on a self-hosted server, with HTTPS on PUBLIC_HOST and basic auth (compose.public.yml; never
# deployed publicly). Both check every hash of the package first.
demo-up: $(ENV_FILE) demo-verify
	$(DEMO) $(PUBLIC) up -d --build

demo-local: $(ENV_FILE) demo-verify
	$(DEMO) up -d --build
	@echo "ReturnRing cached demo: http://localhost:8080"

demo-reset:
	$(DEMO) run --rm reset

demo-down:
	$(DEMO) down --remove-orphans

# The bcrypt hash for ADMIN_PASSWORD_HASH (basic auth of the user admin, make demo-up);
# prompts twice. Cost 10 (about 70 ms per check with Caddy 2.11.7 on 4 shared vCPUs, 14 Oct
# 2026), not Caddy's default 14 (about 1.1 s): a wrong password of admin costs one comparison;
# any other user name gets a 401 before any hash (infra/Caddyfile).
admin-password:
	docker run --rm -it $(CADDY_IMAGE) caddy hash-password --algorithm bcrypt --bcrypt-cost 10

test:
	python -m pytest -m "not gpu" -q

# US-101 on the real files (skipped in CI, where the dataset is not available).
test-asos:
	ASOS_DIR=data/raw/asos python -m pytest -m asos -q

# GPU-marked tests need PyTorch built for ROCm with a visible GPU (each skips without one).
# pytest exits 5 when no test is collected: that is not a failure.
test-gpu:
	python -m pytest -m gpu -q; status=$$?; if [ $$status -eq 5 ]; then echo "no gpu tests collected"; exit 0; fi; exit $$status

lint:
	ruff check .
	ruff format --check .
	mypy

licenses:
	python -m scripts.check_licenses

# US-101: download (or link from SOURCE_DIR), verify the pinned sha256, convert, profile.
# make asos SOURCE_DIR=/path/to/cache takes the six pickles from a local copy.
asos:
	python -m pipeline.ingest --dest data/raw/asos $(if $(SOURCE_DIR),--source-dir $(SOURCE_DIR))

asos-verify:
	python -m pipeline.ingest.asos_download --dest data/raw/asos --verify-only

# US-102..US-105: the Navigli Fashion store (docs/synthetic-store.md). Store in data/store, truth
# in data/truth; the OOD scenario in data/ood/store and data/truth/ood (never used in training).
generate:
	python -m pipeline.generator --calibration $(CALIBRATION) --out data --seed $(SEED) --scale $(SCALE) --scenario main

generate-ood:
	python -m pipeline.generator --calibration $(CALIBRATION) --out data --seed $(SEED) --scale $(SCALE) --scenario ood

# Phase 2 (docs/spec.md, sections 8.1 to 8.9). Each step needs the ones before it; graph and
# anomalies do not need features. Running jobs never read data/truth: only `risk` and `results`
# (offline evaluation) do, rule 12.
# US-203: features/links and features/clusters at the extraction instant (no --clusters for the
# features: the evidence counts each account's own lines, review F14).
graph:
	python -m pipeline.graph --store data
	python -m pipeline.graph --store data/ood

# US-205, US-206, US-209: monthly snapshots of customer features, value and evidence. The OOD
# reference model is trained on the main store (US-105: the OOD store is never trained on).
features:
	python -m pipeline.features --store data --out data
	python -m pipeline.features --store data/ood --out data/ood --source data

# US-208: product anomalies per variant, reason and sales week (weekly decision times).
anomalies:
	python -m pipeline.anomalies --store data --out data
	python -m pipeline.anomalies --store data/ood --out data/ood

# US-204: offline training and evaluation (reads data/truth). Models in artifacts/risk/models,
# metrics in artifacts/eval/risk/metrics.json (never mounted by a service), batch scores at the
# evaluation time in data/scores. Exits 1 when a golden check fails, so the pipeline stops.
risk:
	python -m ml.risk --data data --out artifacts --scores data --results docs/results/us-204-risk.md

# Batch scoring with the saved models at another decision time (never reads data/truth). A time
# before the models' training end is refused (rule 7); AS_OF is ISO 8601 with a time zone.
AS_OF ?=
score:
	python -m ml.scoring --data data --models artifacts/risk/models --out data $(if $(AS_OF),--as-of $(AS_OF))

# Offline evaluation reports of US-203, US-205/206/209 and US-208 (read data/truth). US-201 runs
# on the real ASOS tables of `make asos`: `make propensity`.
results:
	python -m pipeline.graph.evaluate --data data --report docs/results/account-graph.md --json artifacts/eval/graph/evaluation.json
	python -m ml.reference.report --data data --results docs/results/customer-quantities.md
	python -m pipeline.anomalies.evaluate --data data --results docs/results/us-208-product-anomalies.md

propensity:
	python -m ml.propensity --raw data/raw --out artifacts --doc docs/results/us-201-propensity.md

# ---- US-202 and A4 (phase 6): GraphSAGE, python -m ml.gnn (docs/graphsage.md) ----
# Needs PyTorch (infra/requirements-gnn-cpu.txt); DGL optional, used when importable. The 16 Oct
# MI300X embed run: docs/amd-training-session-2026-10-16.md. Not part of `make pipeline`, which
# needs no torch; `make risk` runs the A4 row when data/features/customer_embeddings.parquet exists.
GNN_SAMPLE ?= 1.0
gnn-asos:
	python -m ml.gnn asos --raw data/raw --out artifacts --sample $(GNN_SAMPLE)

# The encoder is fitted on the main store at the risk job's train end and applied, frozen, to the
# OOD store (never trained on, US-105).
embeddings:
	python -m ml.gnn embed --store data --out data --models artifacts
	python -m ml.gnn embed --store data/ood --out data/ood --models artifacts \
	  --encoder artifacts/gnn/models/customer_encoder

gnn-results:
	python -m ml.gnn report --artifacts artifacts --doc docs/results/us-202-graphsage.md

gnn-test:
	python -m pytest -m torch -q

# ADR-16: the serving snapshot the API reads (store, features, scores and models at the scoring
# instant, sha256 of every file, no ground truth). Keeps serving/llm_cache/ (keyed by the case).
serving:
	python -m pipeline.serving --data data --artifacts artifacts --out serving

# Phase 1 and 2 on the host: the synthetic store, the account graph, the features, the product
# anomalies, the risk models with their batch scores (docs/spec.md, section 15) and the serving
# snapshot. `make demo-data` runs the same chain in the pipeline job container.
pipeline: generate generate-ood graph features anomalies risk serving

# ---- Benchmarks B1-B4 (US-902, docs/benchmarks.md): CPU harness check ----
# The CPU against MI300X comparison was retired by ADR-18 (16 Oct 2026). make bench runs the
# harness on the CPU: B3 is a dry run against a local fake server, B2 trains only where PyTorch is
# installed. Parts go to artifacts/bench/<run-id>/, the merged run to bench/results/<run-id>.json;
# make bench never moves latest.json, the pointer GET /api/benchmarks serves.
BENCH_RAW ?= data/raw
BENCH_DATA ?=
BENCH_ARGS ?=
ifndef BENCH_RUN
BENCH_RUN := $(shell date -u +%Y%m%dT%H%M%SZ)-cpu
endif
BENCH_DOC ?= docs/results/benchmarks-cpu.md
export BENCH_GIT_COMMIT ?= $(shell git rev-parse HEAD 2>/dev/null)
# Untracked files count as changes (review G8): a new module not yet added is still code that ran.
export BENCH_GIT_DIRTY ?= $(shell git status --porcelain 2>/dev/null | grep -q . && echo 1 || echo 0)

bench: bench-cpu

bench-cpu:
	python -m bench run --device cpu --run-id $(BENCH_RUN) --raw $(BENCH_RAW) $(if $(BENCH_DATA),--data $(BENCH_DATA)) $(BENCH_ARGS)
	python -m bench report --run bench/results/$(BENCH_RUN).json --out $(BENCH_DOC)

# ---- UI (apps/ui, docs/ui-design.md) ----
UI = npm --prefix apps/ui

ui-install:
	$(UI) ci

# Regenerates the TypeScript types and constants after a contract change (ADR-15).
ui-types:
	$(UI) run gen:types

# Generated files fresh, typecheck, lint, unit tests, licences, production build.
ui-check:
	$(UI) run check

# Playwright smoke in mock mode (Chromium from PLAYWRIGHT_BROWSERS_PATH, never `playwright install`).
ui-e2e:
	$(UI) run test:e2e
