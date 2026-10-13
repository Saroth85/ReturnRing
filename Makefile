.PHONY: dev-up dev-down gpu-up gpu-down demo-up test test-gpu lint licenses asos pipeline bench demo-export \
	spike-preflight spike-prep spike-weights spike-weights-wait spike-vllm spike-wait spike-g1 \
	spike-g1-bench spike-g2 spike-g2-load spike-g3 spike-g4 spike-g5 spike-g6 spike-g7 spike-down \
	spike-gpu-check spike-logs

ENV_FILE ?= .env
COMPOSE = docker compose -f infra/docker-compose.yml --env-file $(ENV_FILE)
# gpu and demo use override files so the laptop .env (DEVICE=cpu, LLM_MODE=mock) cannot leak in.
GPU = docker compose -f infra/docker-compose.yml -f infra/compose.gpu.yml --env-file $(ENV_FILE) --profile gpu
DEMO = docker compose -f infra/docker-compose.yml -f infra/compose.demo.yml --env-file $(ENV_FILE) --profile demo
SPIKE = docker compose -f infra/docker-compose.yml -f infra/compose.gpu.yml -f infra/compose.spike.yml --env-file $(ENV_FILE) --profile gpu
SPIKES = artifacts/spikes
LOAD_ENV = set -a && . ./$(ENV_FILE) && set +a

dev-up:
	$(COMPOSE) --profile dev up -d --build

dev-down:
	$(COMPOSE) --profile dev --profile gpu --profile demo down

# vLLM first, without a build, and healthy before anything else touches the GPU: vLLM sizes its
# KV cache from the free memory it sees while it starts.
gpu-up: spike-vllm spike-wait
	$(GPU) up -d --build

# Keeps the volumes: never add -v here, hf-cache holds the 62 GB of Gemma weights.
gpu-down:
	$(GPU) down

demo-up:
	$(DEMO) up -d --build

test:
	python -m pytest -m "not gpu" -q

# pytest exits 5 when no test is collected: no GPU tests yet is not a failure.
test-gpu:
	python -m pytest -m gpu -q; status=$$?; if [ $$status -eq 5 ]; then echo "no gpu tests collected"; exit 0; fi; exit $$status

lint:
	ruff check .
	ruff format --check .
	mypy

licenses:
	python -m scripts.check_licenses

asos:
	python -m pipeline.ingest.asos_download

pipeline:
	@echo "pipeline: implemented in sessions wt-data and wt-ml (docs/spec.md, section 15)"

bench:
	@echo "bench: implemented in session wt-ml (benchmarks B1-B4)"

demo-export:
	@echo "demo-export: implemented in session E9 (US-901)"

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

spike-g2:
	$(SPIKE) run --rm -T trainer-gbm python infra/spikes/g2_xgboost_gpu.py --out $(SPIKES)/g2.json

# The GPU model loads in xgboost-cpu 3.2.0 on CPU (spec section 10). Installs from PyPI in a
# throwaway container; the product never does this at runtime.
spike-g2-load:
	$(SPIKE) run --rm -T spike-client sh -c "pip install --no-cache-dir --root-user-action=ignore --only-binary=:all: xgboost-cpu==3.2.0 numpy==2.5.4 scipy==1.18.1 && python infra/spikes/g2_load_cpu.py"

spike-g3:
	$(SPIKE) run --rm -T trainer-gnn python infra/spikes/g3_graphsage_synthetic.py --small --out $(SPIKES)/g3_small.json
	$(SPIKE) run --rm -T trainer-gnn python infra/spikes/g3_graphsage_synthetic.py --out $(SPIKES)/g3.json
	$(SPIKE) run --rm -T trainer-gnn python infra/spikes/g3_graphsage_synthetic.py --hetero --out $(SPIKES)/g3_hetero.json

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
