.PHONY: dev-up dev-down dev-logs demo-data reset demo-walk gpu-up gpu-down gpu-reset batch-generate serving-check \
	demo-export demo-import demo-verify demo-up demo-local demo-reset demo-down admin-password \
	test test-gpu test-asos lint licenses asos asos-verify \
	generate generate-ood graph features anomalies risk score results propensity serving pipeline bench \
	gnn-asos embeddings gnn-results gnn-test gnn-image gnn-asos-gpu gnn-asos-cpu-container \
	embeddings-gpu gnn-test-gpu ui-install ui-types ui-check ui-e2e \
	spike-preflight spike-prep spike-weights spike-weights-wait spike-vllm spike-wait spike-g1 \
	spike-g1-bench spike-g2 spike-g2-cpu spike-g2-load spike-g3 spike-g3-cpu spike-g4 spike-g5 \
	spike-g6 spike-g7 spike-down \
	spike-gpu-check spike-logs \
	bench-cpu bench-gpu bench-build bench-b1 bench-b2 bench-vllm bench-vllm-start bench-b3 bench-b4 \
	bench-merge bench-report

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
# gpu and demo use override files so the laptop .env (DEVICE=cpu, LLM_MODE=mock) cannot leak in.
GPU = docker compose -f infra/docker-compose.yml -f infra/compose.gpu.yml --env-file $(ENV_FILE) --profile gpu
DEMO = docker compose -f infra/docker-compose.yml -f infra/compose.demo.yml --env-file $(ENV_FILE) --profile demo
# A server reachable from the Internet adds HTTPS on PUBLIC_HOST and basic auth (spec 11).
PUBLIC = -f infra/compose.public.yml
SPIKE = docker compose -f infra/docker-compose.yml -f infra/compose.gpu.yml -f infra/compose.spike.yml --env-file $(ENV_FILE) --profile gpu
SPIKES = artifacts/spikes
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
# writes data/, artifacts/ and the serving snapshot serving/. dev-up builds the images and starts postgres, the reset (every open return decided
# again, golden cases first), the api, the ui and the proxy on http://localhost:8080.
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
	$(COMPOSE) --profile dev --profile gpu --profile demo down --remove-orphans

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

# ---- MI300X droplet (infra/spikes/README.md, "The demo export") ----
# vLLM first, without a build, and healthy before anything else touches the GPU: vLLM sizes its
# KV cache from the free memory it sees while it starts.
gpu-up: serving-check spike-vllm spike-wait
	$(GPU) $(PUBLIC) up -d --build

serving-check:
	@test -f serving/manifest.json || { echo "No serving snapshot in serving/: make demo-data, \
	  or copy serving/ from the laptop (infra/spikes/README.md, The demo export)"; exit 1; }

# Keeps the volumes: never add -v here, hf-cache holds the 62 GB of Gemma weights.
gpu-down:
	$(GPU) down

gpu-reset:
	$(GPU) run --rm reset

# The draft of every open return through vLLM into serving/llm_cache/ (vLLM must be up).
batch-generate:
	$(GPU) run --rm batch-generate

# The cached demo package from serving/ (after batch-generate) and bench/results/. Runs with the
# host python3 (standard library only: the droplet host has no project Python).
# ALLOW_EMPTY_CACHE=1 packs a snapshot without MI300X drafts (a CPU rehearsal: templates only).
DEMO_GPU ?= AMD Instinct MI300X (AMD Developer Cloud)
demo-export:
	python3 -m scripts.demo_export build --serving serving --bench bench/results --out $(DEMO_DIR) \
	  --generated-on "$(DEMO_GPU)" $(if $(ALLOW_EMPTY_CACHE),--allow-empty-cache)
	tar -czf $(DEMO_DIR).tar.gz $(DEMO_DIR)
	@echo "$(DEMO_DIR).tar.gz: copy it to the demo server, then make demo-import demo-up"

demo-import:
	tar -xzf $(DEMO_DIR).tar.gz
	python3 -m scripts.demo_export verify $(DEMO_DIR)

demo-verify:
	python3 -m scripts.demo_export verify $(DEMO_DIR)

# ---- The cached demo (US-901): DEMO_MODE=cached, GPU containers off, read-only by default ----
# demo-up: the public server (HTTPS on PUBLIC_HOST, basic auth); demo-local: a laptop, on
# http://localhost:8080. Both check every hash of the package first.
demo-up: $(ENV_FILE) demo-verify
	$(DEMO) $(PUBLIC) up -d --build

demo-local: $(ENV_FILE) demo-verify
	$(DEMO) up -d --build
	@echo "ReturnRing cached demo: http://localhost:8080"

demo-reset:
	$(DEMO) run --rm reset

demo-down:
	$(DEMO) down --remove-orphans

# The bcrypt hash for ADMIN_PASSWORD_HASH (basic auth on a public server); prompts twice.
admin-password:
	docker run --rm -it $(CADDY_IMAGE) caddy hash-password

test:
	python -m pytest -m "not gpu" -q

# US-101 on the real files (skipped in CI, where the dataset is not available).
test-asos:
	ASOS_DIR=data/raw/asos python -m pytest -m asos -q

# pytest exits 5 when no test is collected: no GPU tests yet is not a failure.
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
# Needs PyTorch: infra/requirements-gnn-cpu.txt on a laptop, the trainer-graphsage image on the
# droplet (DGL is used when importable). Not part of `make pipeline`, which needs no torch; `make
# risk` runs the A4 row when data/features/customer_embeddings.parquet exists.
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

# On the MI300X droplet, in the trainer-graphsage image (rocm/dgl + this code). On the droplet
# `make bench-b2 DEVICE=gpu` runs the US-202 job on both devices (it writes the same files as
# gnn-asos-gpu and gnn-asos-cpu-container): use these two for a run outside the benchmark. Each
# refuses to start while vLLM is up: the CPU baseline runs alone on the CPU, the GPU run alone on
# the GPU (infra/spikes/README.md, "The GPU session").
GNN = $(GPU) run --rm -T trainer-graphsage
gnn-image:
	$(GPU) build trainer-graphsage

gnn-asos-gpu:
	@$(VLLM_DOWN)
	$(GNN) python -m ml.gnn asos --raw data/raw --out artifacts --device gpu --sample $(GNN_SAMPLE)

gnn-asos-cpu-container:
	@$(VLLM_DOWN)
	$(GNN) python -m ml.gnn asos --raw data/raw --out artifacts --device cpu --sample $(GNN_SAMPLE)

embeddings-gpu:
	@$(VLLM_DOWN)
	$(GNN) python -m ml.gnn embed --store data --out artifacts/gnn/data --models artifacts --device gpu
	$(GNN) python -m ml.gnn embed --store data/ood --out artifacts/gnn/data/ood --models artifacts \
	  --encoder artifacts/gnn/models/customer_encoder --device gpu
	mkdir -p data/features data/ood/features
	cp artifacts/gnn/data/features/customer_embeddings.parquet data/features/
	cp artifacts/gnn/data/ood/features/customer_embeddings.parquet data/ood/features/

gnn-test-gpu:
	$(GNN) python -m pytest -m "torch or gpu" -q -p no:cacheprovider tests/test_gnn_model.py \
	  tests/test_gnn_jobs.py

# ADR-16: the serving snapshot the API reads (store, features, scores and models at the scoring
# instant, sha256 of every file, no ground truth). Keeps serving/llm_cache/ (keyed by the case).
serving:
	python -m pipeline.serving --data data --artifacts artifacts --out serving

# Phase 1 and 2 on the host: the synthetic store, the account graph, the features, the product
# anomalies, the risk models with their batch scores (docs/spec.md, section 15) and the serving
# snapshot. `make demo-data` runs the same chain in the pipeline job container.
pipeline: generate generate-ood graph features anomalies risk serving

# ---- Benchmarks B1-B4 (US-902): method in docs/benchmarks.md, GPU session order in
# infra/spikes/README.md ("The GPU session"). Parts go to artifacts/bench/<run-id>/, the merged run
# to bench/results/<run-id>.json; only a GPU run moves the latest pointer (GET /api/benchmarks).
# make bench (DEVICE=cpu): here, CPU only; B3 is a dry run against a local fake server, B2 trains
# only where PyTorch is installed.
# make bench DEVICE=gpu: on the droplet, B1 in the bench container and B2 in trainer-graphsage with
# vLLM down first, then vLLM, B4 and B3.
DEVICE ?= cpu
BENCH_RAW ?= data/raw
BENCH_DATA ?=
BENCH_ARGS ?=
ifndef BENCH_RUN
BENCH_RUN := $(shell date -u +%Y%m%dT%H%M%SZ)-$(DEVICE)
endif
BENCH_DIR = artifacts/bench/$(BENCH_RUN)
BENCH_DOC ?= docs/results/benchmarks-$(if $(filter gpu,$(DEVICE)),mi300x,cpu).md
BENCH_COMPOSE = docker compose -f infra/docker-compose.yml -f infra/compose.gpu.yml -f infra/compose.bench.yml --env-file $(ENV_FILE) --profile gpu
BENCH_RUN_GPU = $(BENCH_COMPOSE) run --rm -T
VLLM_DOWN = if [ -n "$$($(GPU) ps -q --status running vllm 2>/dev/null)" ]; then \
	  echo "vLLM is running: B1 and B2 run alone on the GPU and the CPU (make gpu-down first)"; exit 1; fi
export BENCH_GIT_COMMIT ?= $(shell git rev-parse HEAD 2>/dev/null)
# Untracked files count as changes (review G8): a new module not yet added is still code that ran.
export BENCH_GIT_DIRTY ?= $(shell git status --porcelain 2>/dev/null | grep -q . && echo 1 || echo 0)

bench: bench-$(DEVICE)

bench-cpu:
	python -m bench run --device cpu --run-id $(BENCH_RUN) --raw $(BENCH_RAW) $(if $(BENCH_DATA),--data $(BENCH_DATA)) $(BENCH_ARGS)
	python -m bench report --run bench/results/$(BENCH_RUN).json --out $(BENCH_DOC)

bench-gpu: bench-build gnn-image bench-b1 bench-b2 bench-vllm bench-b4 bench-b3 bench-merge

bench-build:
	$(BENCH_COMPOSE) build bench

bench-b1:
	@$(VLLM_DOWN)
	$(BENCH_RUN_GPU) bench python -m bench run --device gpu --parts B1 --run-id $(BENCH_RUN) --raw $(BENCH_RAW) --no-merge $(BENCH_ARGS)

# B2 runs in trainer-graphsage (rocm/dgl plus what ml.gnn imports; `make gnn-image` builds it):
# ml.gnn.b2_timing runs the US-202 job on the CPU and on the MI300X, at every --b2-samples size.
bench-b2:
	@$(VLLM_DOWN)
	$(BENCH_RUN_GPU) trainer-graphsage python -m bench run --device gpu --parts B2 --run-id $(BENCH_RUN) --raw $(BENCH_RAW) --no-merge $(BENCH_ARGS)

# Starts vLLM and records start and healthy times for the B3 cold start (15 s polling), then times
# the first request right after it, before B4's batch generation uses vLLM (reviews G7, G15):
# first-request.json. If vLLM was already up, B3 reports no cold start. The page cache is dropped
# before the start (BENCH_DROP_CACHES=0 keeps it), so the weights are read from disk; the compile
# cache volume is kept, and a droplet created from a snapshot reads a cold disk: the measured
# start is a lower bound of the nightly one, and vllm-start-conditions.txt says how it was taken.
BENCH_DROP_CACHES ?= 1
bench-vllm: bench-vllm-start spike-wait
	@if [ -f $(BENCH_DIR)/vllm-start.txt ]; then \
	  cp $(SPIKES)/logs/vllm-healthy.txt $(BENCH_DIR)/vllm-healthy.txt; \
	  $(BENCH_RUN_GPU) bench python -m bench first-request --run-id $(BENCH_RUN); fi

bench-vllm-start:
	@mkdir -p $(BENCH_DIR) $(SPIKES)/logs
	@if [ -n "$$($(GPU) ps -q --status running vllm 2>/dev/null)" ]; then \
	  echo "vLLM already running: B3 reports no cold start"; rm -f $(BENCH_DIR)/vllm-start.txt; \
	else \
	  if [ "$(BENCH_DROP_CACHES)" = 1 ] && sync && { echo 3 > /proc/sys/vm/drop_caches; } 2>/dev/null; then \
	    echo "page cache dropped before the start" > $(BENCH_DIR)/vllm-start-conditions.txt; \
	  else echo "page cache not dropped (a warm restart)" > $(BENCH_DIR)/vllm-start-conditions.txt; fi; \
	  cat $(BENCH_DIR)/vllm-start-conditions.txt; \
	  date -u +%Y-%m-%dT%H:%M:%SZ | tee $(BENCH_DIR)/vllm-start.txt > $(SPIKES)/logs/vllm-start.txt; \
	  $(GPU) up -d --no-build vllm; fi

# The nightly pipeline in the bench container, amd-smi sampled every 5 s on the host (SPK-G4:
# the host amd-smi matches the driver), or inside the container when the host has none.
bench-b4:
	@mkdir -p $(BENCH_DIR) bench-work
	@export PATH="$$PATH:/opt/rocm/bin"; sampler=""; \
	if command -v amd-smi >/dev/null && command -v python3 >/dev/null; then \
	  python3 -m bench.amdsmi --out $(BENCH_DIR)/amdsmi.jsonl & sampler=$$!; \
	  samples="--b4-samples $(BENCH_DIR)/amdsmi.jsonl"; \
	else echo "no amd-smi on the host: sampling inside the container"; samples="--b4-sampler inprocess"; fi; \
	$(BENCH_RUN_GPU) bench python -m bench run --device gpu --parts B4 --run-id $(BENCH_RUN) --no-merge $$samples $(BENCH_ARGS); \
	rc=$$?; if [ -n "$$sampler" ]; then kill $$sampler; fi; exit $$rc

# disk-used-gb.txt: the GB used on the droplet's root disk (images, weights, volumes), the size
# the snapshot of step 14 will hold; B3's cost reads it instead of the cost-v2 assumption.
bench-b3:
	@mkdir -p $(BENCH_DIR)
	df -BG --output=used / | tail -1 | tr -dc '0-9' > $(BENCH_DIR)/disk-used-gb.txt
	$(BENCH_RUN_GPU) bench python -m bench run --device gpu --parts B3 --run-id $(BENCH_RUN) --no-merge $(if $(BENCH_DATA),--data $(BENCH_DATA)) $(BENCH_ARGS)

bench-merge:
	$(BENCH_RUN_GPU) bench python -m bench merge --run-id $(BENCH_RUN)
	$(BENCH_RUN_GPU) bench python -m bench report --run bench/results/$(BENCH_RUN).json --out $(BENCH_DIR)/report.md

# On the laptop, after copying bench/results/ back: make bench-report BENCH_RUN=<run-id> DEVICE=gpu
bench-report:
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

# ---- GPU spikes SPK-G1..G7 on the MI300X droplet: infra/spikes/README.md ----

spike-preflight:
	python3 infra/spikes/preflight.py --env-file $(ENV_FILE)

# Network phase, no GPU, in parallel: the vLLM image, the DGL and client images, the trainer-gbm
# build (pulls the ROCm base). Each job reports its own duration; logs in artifacts/spikes/logs/.
spike-prep:
	mkdir -p $(SPIKES)/logs
	@$(LOAD_ENV) || exit 1; rm -f $(SPIKES)/logs/prep-status.txt; \
	job() { name=$$1; shift; s=$$(date +%s); "$$@" > $(SPIKES)/logs/$$name.log 2>&1; rc=$$?; \
	  echo "$$name: exit $$rc, took $$(( $$(date +%s) - s )) s" | tee -a $(SPIKES)/logs/prep-status.txt; return $$rc; }; \
	job pull-vllm $(SPIKE) pull vllm & \
	job pull-dgl $(SPIKE) pull trainer-gnn spike-client & \
	job build-gbm $(SPIKE) build trainer-gbm & \
	wait; docker system df > $(SPIKES)/logs/docker-df.txt 2>&1; \
	! grep -v 'exit 0,' $(SPIKES)/logs/prep-status.txt

# SPK-G7: Gemma weights into the hf-cache volume in the background (after spike-prep), so G2 and
# G3 use the GPU meanwhile. spike-weights-wait prints the download time.
spike-weights:
	COMPOSE="$(SPIKE)" ENV_FILE=$(ENV_FILE) bash infra/spikes/weights.sh start

spike-weights-wait:
	COMPOSE="$(SPIKE)" ENV_FILE=$(ENV_FILE) bash infra/spikes/weights.sh wait

spike-vllm:
	mkdir -p $(SPIKES)/logs
	date -u +%Y-%m-%dT%H:%M:%SZ > $(SPIKES)/logs/vllm-start.txt
	$(GPU) up -d --no-build vllm

# Waits up to 60 min for /health; prints the last log lines if the container stops.
spike-wait:
	@for i in $$(seq 1 240); do \
	  if $(GPU) exec -T vllm curl -fsS -o /dev/null http://127.0.0.1:8001/health 2>/dev/null; then \
	    date -u +%Y-%m-%dT%H:%M:%SZ > $(SPIKES)/logs/vllm-healthy.txt; \
	    echo "vllm healthy: started $$(cat $(SPIKES)/logs/vllm-start.txt), healthy $$(cat $(SPIKES)/logs/vllm-healthy.txt)"; exit 0; fi; \
	  if [ -z "$$($(GPU) ps -q --status running vllm)" ]; then \
	    echo "vllm is not running"; $(GPU) logs --tail 100 vllm; exit 1; fi; \
	  sleep 15; \
	done; echo "vllm not healthy after 60 min"; $(GPU) logs --tail 100 vllm; exit 1

spike-g1:
	$(SPIKE) run --rm -T spike-client python infra/spikes/g1_guided_json.py --out $(SPIKES)/g1.json

# vllm bench serve inside the running vllm container: 16 prompts one at a time, then 64 prompts
# 32 at a time (B3 preview). Single-stream tokens/s is about 1000 / median TPOT in ms.
BENCH_IN ?= 1500
BENCH_OUT ?= 400
spike-g1-bench:
	mkdir -p $(SPIKES)/logs
	$(LOAD_ENV) && tok=/root/.cache/huggingface/hub/models--$$(echo "$$GEMMA_MODEL" | sed 's|/|--|g')/snapshots/$$GEMMA_REVISION; \
	for c in 1 32; do \
	  if [ $$c -eq 1 ]; then n=16; else n=64; fi; \
	  log=$(SPIKES)/logs/g1-bench-c$$c.txt; \
	  $(GPU) exec -T -e OPENAI_API_KEY="$$VLLM_API_KEY" vllm vllm bench serve --backend openai-chat \
	    --base-url http://127.0.0.1:8001 --endpoint /v1/chat/completions --model "$$GEMMA_MODEL" \
	    --tokenizer "$$tok" \
	    --dataset-name random --random-input-len $(BENCH_IN) --random-output-len $(BENCH_OUT) \
	    --num-prompts $$n --max-concurrency $$c --num-warmups 2 --ignore-eos --temperature 0.2 \
	    --percentile-metrics ttft,tpot,itl,e2el --metric-percentiles 50,95 > $$log 2>&1; rc=$$?; \
	  cat $$log; [ $$rc -eq 0 ] || exit $$rc; \
	  grep -Eq "Successful requests: +$$n\b" $$log || { echo "not all $$n requests succeeded"; exit 1; }; \
	done

# Is the GPU visible inside both trainer images? Expected: gfx942, then "gpu True". Both checks
# always run; the target fails if either does.
spike-gpu-check:
	@rc=0; \
	$(SPIKE) run --rm -T trainer-gbm sh -c 'ls -l /dev/kfd && /opt/rocm/bin/rocminfo | grep -m1 -o "gfx[0-9a-f]*"' || rc=1; \
	$(SPIKE) run --rm -T trainer-gnn || rc=1; \
	exit $$rc

spike-logs:
	$(GPU) logs --no-color --tail 200 vllm

# G2 and G3 are timed in two halves: the GPU runs while the weights download (the download uses
# CPU, which would slow a CPU baseline), the CPU baselines after spike-weights-wait, alone on the
# CPU and before vLLM starts compiling. The CPU file holds the speedup against the GPU file.
CONTEXT ?= Gemma weights downloading in the background
spike-g2:
	$(SPIKE) run --rm -T trainer-gbm python infra/spikes/g2_xgboost_gpu.py --device gpu \
	  --context "$(CONTEXT)" --out $(SPIKES)/g2_gpu.json

spike-g2-cpu:
	$(SPIKE) run --rm -T trainer-gbm python infra/spikes/g2_xgboost_gpu.py --device cpu \
	  --context "alone on the CPU" --gpu-result $(SPIKES)/g2_gpu.json --out $(SPIKES)/g2_cpu.json

# The GPU model loads in xgboost-cpu 3.2.0 on CPU (spec section 10). Installs from PyPI in a
# throwaway container; the product never does this at runtime.
spike-g2-load:
	$(SPIKE) run --rm -T spike-client sh -c "pip install --no-cache-dir --root-user-action=ignore --only-binary=:all: xgboost-cpu==3.2.0 numpy==2.5.4 scipy==1.18.1 && python infra/spikes/g2_load_cpu.py --check $(SPIKES)/g2_gpu_check.json"

G3 = $(SPIKE) run --rm -T trainer-gnn python infra/spikes/g3_graphsage_synthetic.py
spike-g3:
	$(G3) --small --context "$(CONTEXT)" --out $(SPIKES)/g3_small.json
	$(G3) --device gpu --context "$(CONTEXT)" --out $(SPIKES)/g3_gpu.json
	$(G3) --device gpu --hetero --context "$(CONTEXT)" --out $(SPIKES)/g3_hetero_gpu.json

spike-g3-cpu:
	$(G3) --device cpu --context "alone on the CPU" --gpu-result $(SPIKES)/g3_gpu.json \
	  --out $(SPIKES)/g3_cpu.json
	$(G3) --device cpu --hetero --context "alone on the CPU" \
	  --gpu-result $(SPIKES)/g3_hetero_gpu.json --out $(SPIKES)/g3_hetero_cpu.json

spike-g4:
	COMPOSE="$(SPIKE)" bash infra/spikes/g4_coexistence.sh

# IMAGE: a photo of a returned garment, copied under artifacts/ (mounted in spike-client).
IMAGE ?= $(SPIKES)/garment.jpg
spike-g5:
	$(SPIKE) run --rm -T spike-client python infra/spikes/g1_guided_json.py --image $(IMAGE) --out $(SPIKES)/g5.json

G6_MODE ?= auto
spike-g6:
	$(SPIKE) run --rm -T spike-client python infra/spikes/g6_tool_call.py --mode $(G6_MODE) --out $(SPIKES)/g6-$(G6_MODE).json

spike-g7:
	@cat $(SPIKES)/logs/weights-status.txt $(SPIKES)/logs/vllm-start.txt $(SPIKES)/logs/vllm-healthy.txt 2>/dev/null || true
	$(GPU) logs --no-color vllm 2>&1 | grep -E 'Time spent downloading weights|Loading weights took|Available KV cache memory|KV cache size|Maximum concurrency' | tee $(SPIKES)/g7-vllm-log.txt

spike-down:
	$(SPIKE) --profile spike down
