# GPU spikes SPK-G1..G7 (one MI300X droplet, one session)

Pass criteria and plan B come from docs/spec.md section 13. Record every result in
`docs/spike-results.md` as measured, failures included (CLAUDE.md rule 13). Every step is a `make`
target run from the repo root on the droplet; `make -n <target>` prints the exact `docker compose`
command. The budget is about 90 minutes of droplet time (about 3 USD at 1.99 USD/hour) plus the
snapshot. Billing runs from creation to destruction, powered off or not.

## How the setup fits together

- `infra/compose.gpu.yml` sets `DEVICE=gpu` and `LLM_MODE=vllm` for the api and refuses to start
  if a GPU image, the AMD index or the Gemma revision is missing from `.env`.
  `infra/compose.spike.yml` mounts `infra/spikes/` read-only into trainer-gbm and trainer-gnn, and
  adds `spike-client` (python:3.12-slim) on the compose network. vLLM never gets a published port:
  the spike clients reach it as `http://vllm:8001/v1`, and `vllm bench serve` runs inside the vllm
  container.
- Images are pinned by tag and digest in `.env.example`: vLLM 0.31.0 (ROCm 7.2.3), rocm/dgl
  2.4.0 (ROCm 7.0.0, PyTorch 2.8.0), and `rocm/dev-ubuntu-24.04:7.2.3-complete` for trainer-gbm.
  The Gemma weights are pinned to a Hugging Face commit (`GEMMA_REVISION`).
- trainer-gbm installs `amd_xgboost==3.2.0` from `https://pypi.amd.com/rocm-7.2.3/simple` only,
  with a pinned wheel hash, and everything else from PyPI only. The build fails if a ROCm library
  of `libxgboost.so` is missing.
- Each spike starts on its own. Nothing of the spikes is started with `make gpu-up`, which
  starts the product stack ("The demo export" below).
- vLLM keeps its weights (`hf-cache`) and compile cache (`vllm-cache`) in volumes, so a restart
  or a later session from the snapshot skips both. It runs with `HF_HUB_OFFLINE=1` (rule 2): only
  `spike-weights` talks to Hugging Face.
- G2 and G3 are timed in two halves. The GPU halves run while the weights download; the CPU
  baselines run after the download, alone on the CPU and before vLLM compiles, because a download
  or a compile on the same 20 vCPUs would slow the CPU baseline and inflate the speedup. Every
  output records its conditions in `context`.
- Outputs go to `artifacts/spikes/` (git-ignored, owned by root because the containers run as
  root).

## Before the session (laptop, no cost)

1. Branch with these changes merged or pushed; `make lint test` green.
2. A photo of a returned garment for SPK-G5 (JPEG or PNG, under 2 MB, no person, no personal data
   on labels).
3. A DigitalOcean Cloud Firewall that allows inbound 22 only (80 and 443 are not needed for the
   spikes). Docker-published ports bypass ufw, so ufw alone is not enough.
4. If the repository is private: an SSH agent with your GitHub key, used with `ssh -A`. Never put
   a GitHub token in `.env` or on the droplet disk, which ends up in the snapshot.

## Timeline

| Step | Target | Estimate (droplet time) | GPU |
| --- | --- | --- | --- |
| 1-3 | create droplet, clone, `.env`, photo, `spike-preflight` | 10 min | no |
| 4 | `spike-prep` (images, trainer-gbm build) | 5-15 min | no |
| 5 | `spike-weights` (Gemma weights, in the background) | seconds to start | no |
| 6 | `spike-gpu-check`, `spike-g2`, `spike-g2-load` (GPU halves) | 5 min | yes |
| 7 | `spike-g3` (GPU halves) | 3-5 min | yes |
| 8 | `spike-weights-wait`, then `spike-g2-cpu`, `spike-g3-cpu` | rest of the download + 5-10 min | no |
| 9 | `spike-vllm`, `spike-wait`, `spike-g7` | 5-15 min | yes |
| 10 | `spike-g1`, `spike-g1-bench` | 8 min | yes |
| 11-12 | `spike-g5`, `spike-g6` | 3 min | yes |
| 13 | `spike-g4` | 7 min | yes |
| 14 | copy artifacts, stop, snapshot, destroy | 5 min + snapshot | no |
| 15 | fill `docs/spike-results.md` | laptop, no cost | no |

All times are estimates: download speed, image extraction and vLLM compile time on ROCm are
unmeasured. G2 and G3 run before vLLM starts, because vLLM measures free GPU memory while it
starts and a training job running at that moment would skew its KV-cache sizing. Their GPU halves
run while the weights download, so the GPU does not wait on the network.

## Steps

### 1. Create the droplet (about 5 min)

AMD Developer Cloud, size `gpu-mi300x1-192gb` (1x MI300X, 20 vCPU, 240 GiB RAM, 720 GB boot
disk), Quick Start image with vLLM (spec section 11), the firewall above, your SSH key. Write down
the creation time: billing starts now.

### 2. Clone, configure, upload the photo (about 3 min)

```bash
ssh -A root@<droplet-ip>      # -A only if the repository is private
tmux new -s spikes            # the session survives an SSH drop; reattach with: tmux attach -t spikes
command -v make || apt-get install -y make
git clone <repo-url> ReturnRing && cd ReturnRing && git checkout <branch>
cp .env.example .env
sed -i "s/^VLLM_API_KEY=.*/VLLM_API_KEY=$(openssl rand -hex 24)/" .env
mkdir -p artifacts/spikes
```

From the laptop: `scp garment.jpg root@<droplet-ip>:ReturnRing/artifacts/spikes/garment.jpg`

Start from `.env.example`, not the laptop `.env`: a `.env` copied from the old example still has
`:latest` images and an empty `AMD_PYPI_INDEX_URL`. Keep `.env` values free of spaces and quotes
(the Makefile sources it with `sh`).

### 3. Preflight (about 2 min)

```bash
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}'
make spike-preflight
```

- If the Quick Start container (named `rocm` in the AMD guides) is running, write its image and
  ports in the results, then `docker stop rocm && docker update --restart=no rocm`. It may hold
  GPU memory and publish Jupyter or vLLM on the public IP.
- Expected: no `ERROR` line; `artifacts/spikes/host.json` written.
- Host driver: the amdgpu 6.4.x driver (module version 6.12.12.*) and the 30.x/31.x drivers
  support ROCm 7.0 user space (DGL) and 7.2 user space (vLLM, amd_xgboost) in AMD's
  compatibility matrix. An older driver is a risk for SPK-G1 and SPK-G2: note it and go on.
- `docker compose` missing: if `dpkg -l docker-ce` lists Docker's own packages,
  `apt-get install -y docker-compose-plugin`; otherwise
  `apt-get install -y --no-install-recommends docker-compose-v2`.
- Less than 250 GB free for Docker, or `/dev/kfd` missing: wrong image or size. Destroy the droplet
  and start again.

### 4. Pull and build (5-15 min, no GPU)

```bash
make spike-prep
```

Runs three jobs in parallel: pull the vLLM image, pull the DGL and client images, build
trainer-gbm. Expected: three lines `<job>: exit 0, took N s`; logs in `artifacts/spikes/logs/`,
disk use in `logs/docker-df.txt`.

| Failure | What to do |
| --- | --- |
| `build-gbm` pip error on the AMD index (unreachable, 403, hash mismatch) | Read `logs/build-gbm.log`. Do not debug on paid time: SPK-G2 plan B (XGBoost on CPU). Alternative, if the 7.2.3 wheel was replaced: switch index, wheel hash and base image together to ROCm 7.1.1 (see `infra/docker/requirements-gbm-amd.txt`). |
| `build-gbm` lists `librccl.so.1 => not found` | `ROCM_BASE_IMAGE` is not the `-complete` image: copy it again from `.env.example`. |
| `pull-dgl` or `pull-vllm` with `429 Too Many Requests` | `docker login` with your own Docker Hub account, then re-run. |

### 5. Start the weights download (seconds)

```bash
make spike-weights
```

Downloads the 62.6 GB of Gemma 4 31B at `GEMMA_REVISION` into the `hf-cache` volume, from inside
the vLLM image, in the background (`logs/download-weights.log`). Go on with step 6 at once.

### 6. SPK-G2: amd_xgboost on GPU (about 5 min, GPU half)

```bash
make spike-gpu-check             # 1 min: gfx942 from trainer-gbm, "gpu True" from trainer-gnn
make spike-g2
make spike-g2-load
```

Expected: an environment line with `"xgboost": "3.2.0", "use_hip": true` and `rocm` 7.2.3;
`official_verify` with `device_effective` other than `cpu` (the exact string, likely `cuda:0`, is
unconfirmed); the GPU result; `"pass": true` in `g2_gpu.json`. Then `g2_load_cpu.py` prints
`"pass": true` with `max_abs_diff` near 0: the GPU model loads in xgboost-cpu 3.2.0 (spec
section 10). The CPU baseline comes in step 8.

- `spike-gpu-check` fails for one image only: the other image's line still shows whether the host
  passes the GPU through; the failing image is the problem.
- Exit 2 (GPU not used): amd_xgboost falls back to CPU with only a warning. Check
  `make spike-gpu-check`.
- Plan B (spec 13): XGBoost on CPU for training, GPU only for Gemma and DGL.

### 7. SPK-G3: DGL GraphSAGE (3-5 min, GPU half)

```bash
make spike-g3
```

Three runs of `g3_graphsage_synthetic.py`: `--small` (smoke test, CPU and GPU), then on the GPU
the spec size (1.2M nodes, 12M edges) and `--hetero` (customer/product graph with brand, product
type and reason nodes). Model of spec 8.1: SAGEConv 16, max-pool, dropout 0.2, Adam 0.01;
synthetic seeded data, no download. Expected: `torch_hip` set, `dgl` 2.4.0, `gpu_visible: true`,
`gpu_arch` gfx942, `"pass": true`.

- Exit 2: GPU not visible in the DGL container: `make spike-gpu-check`.
- Optional upstream example (downloads Cora from data.dgl.ai; do not pass `--gpu 0`, the script
  has no such flag):

  ```bash
  docker compose -f infra/docker-compose.yml -f infra/compose.gpu.yml -f infra/compose.spike.yml \
    --env-file .env --profile gpu run --rm -T -w /src/dgl/examples/pytorch/graphsage \
    trainer-gnn python train_full.py --dataset cora
  ```
- Plan B (spec 13): GraphSAGE only as an upgrade; benchmark B2 postponed.

### 8. Wait for the weights, then the CPU baselines (SPK-G7 first half; 5-10 min after the download)

```bash
make spike-weights-wait
make spike-g2-cpu
make spike-g3-cpu
```

Expected: `download-weights: exit 0, took N s (google/gemma-4-31B-it@<revision>)`. N is the
SPK-G7 download time; it ran next to the GPU halves of G2 and G3, which use the GPU and little CPU.
A 401 or 403 is not expected (both Gemma 4 models are not gated): set `HF_TOKEN` in `.env`, then
`make spike-weights spike-weights-wait`.

The CPU runs use the same data and parameters as the GPU files and write the speedups:
`speedup_train` in `g2_cpu.json`, `speedup_median_epoch` in `g3_cpu.json` and
`g3_hetero_cpu.json`. They stop with an error if the GPU file was produced with a different
configuration.

### 9. Start vLLM, SPK-G7 (5-15 min)

```bash
make spike-vllm
make spike-wait
make spike-g7
```

The weights are already in `hf-cache`, so this is load and compile time. Expected:
`vllm healthy: started ..., healthy ...`, then the log lines `Loading weights took`,
`Available KV cache memory`, `GPU KV cache size: N tokens` and
`Maximum concurrency for 16,384 tokens per request: X`.

| Failure | What to do |
| --- | --- |
| `Free memory on device ... is less than desired GPU memory utilization` | Something else holds GPU memory (Quick Start container, a trainer still running): stop it, `make spike-vllm spike-wait`. |
| Chat template not found | The template path in `infra/docker-compose.yml` must stay absolute. |
| Compile or graph-capture error, or no healthy server after 15 min of compiling | On the droplet only, add `- --enforce-eager` to the vllm `command` in `infra/docker-compose.yml`, then `make spike-vllm spike-wait`. Record it: eager mode is slower, so G1 numbers are a lower bound. |
| Engine or kernel error in vLLM 0.31.0 | Set `VLLM_IMAGE=vllm/vllm-openai-rocm:v0.30.0@sha256:2e7da1ad1c66836802072588adea75f9f4991da5f9545b4318e91d422c22ce6a` in `.env` (another 11 GB pull), then `make spike-vllm spike-wait`. |
| Gemma 4 31B does not start at all | Plan B model (spec 13, R-02, D-12): see step 10. |

### 10. SPK-G1: guided JSON and tokens per second (about 8 min)

```bash
make spike-g1
make spike-g1-bench
```

`spike-g1` sends three requests with the `contracts/json/llm_output.schema.json` schema; the
first is warm-up (grammar compile). It also checks that a request without the API key gets HTTP
401 (NFR-11). Expected: `pass=True`, `finish_reason` `stop`, valid JSON. `spike-g1-bench` runs
`vllm bench serve` with 16 requests one at a time, then 64 requests 32 at a time (1500 input and
400 output tokens; change with `BENCH_IN` and `BENCH_OUT`); it fails unless every request
succeeds.

- `finish_reason` `length`: raise `--max-tokens` (default 1024) in the command from
  `make -n spike-g1`.
- Plan B (spec 13, R-02, D-12): single-stream below 15 tokens/s (1000 / median TPOT in ms) or a
  start error. In `.env` set `GEMMA_MODEL=google/gemma-4-26B-A4B-it` and
  `GEMMA_REVISION=4d7ae4984b7db7de8f8457170b3f1a419ee76d52` (both together), then
  `make spike-weights spike-weights-wait spike-vllm spike-wait` (another 51.6 GB download) and
  repeat this step.

### 11. SPK-G5: vision (about 1 min)

```bash
make spike-g5                    # or IMAGE=artifacts/spikes/<file>
```

Expected: a JSON checklist with `tag_attached`, `visible_damage`, `photo_quality_ok` (each
`yes`, `no` or `not_observable` with a low/medium/high confidence) and `notes`; `pass=True`.
Plan B (spec 13): inspection out of scope (it was Could).

### 12. SPK-G6: tool calling (about 1 min)

```bash
make spike-g6
make spike-g6 G6_MODE=named      # only if auto fails
```

Expected: step 1 calls `get_case_orders` with `case_id` R-1042; step 2 answers citing O-1001 and
O-1002; `"pass": true`. `named` forces the function through structured outputs and works without
the gemma4 tool parser. Plan B (spec 13): the agent's code picks the tool, Gemma writes only the
answer.

### 13. SPK-G4: coexistence (about 7 min)

```bash
make spike-g4                    # DURATION=300 by default
```

With vLLM up at 0.55, runs the DGL spike and the XGBoost spike on the GPU for at least 300 s
each, sends a guided-JSON request every 45 s, and samples `amd-smi metric --json` and
`amd-smi process` on the host every 5 s into `artifacts/spikes/g4_amdsmi.log`. Expected:
`SPK-G4: pass`. Plan B (spec 13): train with vLLM stopped.

### 14. Copy, stop, snapshot, destroy (about 5 min plus the snapshot)

From the laptop: `scp -r root@<droplet-ip>:ReturnRing/artifacts/spikes ./artifacts/`. Then on the
droplet:

```bash
df -h / && docker system df     # disk in use: the snapshot is about this size
make spike-down                  # keeps the volumes (hf-cache holds the weights)
shutdown -h now
```

In the control panel: take a snapshot of the droplet, wait until it is listed, destroy the
droplet, and check it is gone from the Droplets list. Snapshots cost 0.06 USD per GB per month
(expect roughly 170-220 GB, about 10-13 USD per month); the droplet keeps billing at 1.99 USD/hour
until it is destroyed, also while the snapshot runs. Whether snapshots work for AMD Developer
Cloud GPU droplets, and how long one takes, is unconfirmed: if the snapshot would take longer than
the download measured in step 8, destroy without it and note the decision.

### 15. Record (laptop, no cost)

Fill `docs/spike-results.md` from `artifacts/spikes/` (the field names match the JSON keys the
scripts write). Commit only `docs/spike-results.md`; artifacts stay out of git.

## The GPU session: US-202, A4 and benchmarks B1-B4 (one ordered list)

Run it once the spikes pass: in the same session after step 13 and before step 14, or in a later
session from the snapshot. Method and the four rules of spec 12: `docs/benchmarks.md`; the
GraphSAGE jobs: `docs/graphsage.md`. Every droplet step is a `make` target run from the repository
root (`make -n <target>` prints its commands); `$RUN` below is `artifacts/bench/$BENCH_RUN`.

Droplet time is unmeasured. B2's CPU baseline is the longest step: the full-data US-202 job took
92 minutes on this project's 4 shared vCPUs, and its time on the droplet's 20 vCPUs is what B2
measures. `BENCH_ARGS="--b2-samples 1"` skips the 25% and 50% sizes (growing loads) if the
session is short. B1 then B4 come next. After a failure, rerun from the failed step with the same
`BENCH_RUN`: the parts already written in `$RUN` stay.

| # | Where | Command | What it does | vLLM | Writes |
| --- | --- | --- | --- | --- | --- |
| 1 | laptop | `make lint test`, then `make asos pipeline` if `data/` is not built | the branch is green; the ASOS tables and the synthetic store | - | `data/raw/`, `data/store/`, `data/features/`, `data/scores/`, `data/ood/` (laptop) |
| 2 | laptop | `ssh root@<droplet-ip> mkdir -p ReturnRing/data/raw` then `scp data/raw/asos_*.parquet root@<droplet-ip>:ReturnRing/data/raw/` and `rsync -a data/store data/features data/ood root@<droplet-ip>:ReturnRing/data/` | copies the ASOS tables (about 70 MB, CC BY 4.0, used and not redistributed) and the store the A4 encoder reads (about 55 MB); `data/truth` is never copied (rule 12) | - | `data/raw/asos_*.parquet`, `data/store/`, `data/features/`, `data/ood/` (droplet) |
| 3 | droplet | `make spike-down` | stops vLLM and every trainer, keeps the volumes; B1, B2 and the GNN jobs refuse to run while vLLM is up (their CPU half alone on the 20 vCPUs, their GPU half alone on the GPU) | down | nothing |
| 4 | droplet | `export BENCH_RUN=$(date -u +%Y%m%dT%H%M%SZ)-gpu` | one run id for the whole session (`.env` as for the spikes: ROCM_BASE_IMAGE, AMD_PYPI_INDEX_URL, VLLM_API_KEY, GEMMA_MODEL, GEMMA_REVISION, VLLM_IMAGE, DGL_IMAGE) | down | nothing |
| 5 | droplet | `make bench-build DEVICE=gpu` | builds the bench image (ROCm 7.2.3, the amd_xgboost wheel of trainer-gbm, the pipeline and llm stack) | down | the `bench` image |
| 6 | droplet | `make gnn-image` | builds trainer-graphsage: rocm/dgl plus numpy 2.5.4, pandas 3.0.6 and what ml.gnn, B2 and the GPU tests import; the build fails if DGL breaks on numpy 2 | down | the `trainer-graphsage` image |
| 7 | droplet | `make gnn-test-gpu` | DGL against the PyTorch layers on the CPU and the GPU, the job tests pinned to the CPU, and the GPU job tests reading the GPU files | down | nothing (pytest temporary folders) |
| 8 | droplet | `make bench-b1 DEVICE=gpu` | B1: XGBoost on the CPU then the MI300X, protocols A and B, sizes 10, 25, 50, 100%, 200 fixed rounds (the speedups) and early stopping (shown apart), 3 repeats, scoring with the fixed-rounds model | down | `$RUN/B1.json` |
| 9 | droplet | `make bench-b2 DEVICE=gpu` | B2 in trainer-graphsage: `ml.gnn.b2_timing` runs the US-202 job (`python -m ml.gnn asos`) on the CPU and on the MI300X at 25%, 50% and 100% of the customers; the full-data runs are the US-202 runs | down | `$RUN/B2.json`; `artifacts/eval/gnn/us-202-asos-cpu.json`, `us-202-asos-gpu.json`; `artifacts/gnn/models/asos_{A,B}_graphsage_{cpu,gpu}.pt` and `.meta.json`; `$RUN/b2-sample-25/`, `$RUN/b2-sample-50/` |
| 10 | droplet | `make embeddings-gpu` | A4: the customer encoder trained on the GPU on the main store, applied to the OOD store, embeddings copied into `data/` | down | `artifacts/gnn/models/customer_encoder.pt` and `.meta.json`; `artifacts/eval/gnn/us-202-embeddings-main.json`, `-ood.json`; `data/features/customer_embeddings.parquet`, `data/ood/features/customer_embeddings.parquet` |
| 11 | droplet | `make bench-vllm DEVICE=gpu` | drops the page cache (`BENCH_DROP_CACHES=0` keeps it), starts vLLM, waits for `/health` (15 s polling), then times the first request before anything else uses vLLM | starts | `$RUN/vllm-start-conditions.txt`, `vllm-start.txt`, `vllm-healthy.txt`, `first-request.json` |
| 12 | droplet | `make bench-b4 DEVICE=gpu` | B4: the nightly pipeline (generate, graph, features, anomalies, risk, scoring on the CPU; batch generation on the MI300X through vLLM), amd-smi sampled on the host every 5 s | up | `$RUN/B4.json`, `$RUN/amdsmi.jsonl`, `$RUN/b4-logs/`, `bench-work/$BENCH_RUN/` (the generated store, truth deleted) |
| 13 | droplet | `make bench-b3 DEVICE=gpu` | records the disk the snapshot will hold, then B3: Gemma latency p50 and p95 and tokens per second, batches 1, 8, 32 and concurrency 4, 16, every load on the same case list from B4's store; cost per decision (cost-v2) | up | `$RUN/disk-used-gb.txt`, `$RUN/B3.json` |
| 14 | droplet | `make bench-merge DEVICE=gpu` | the run file, the pointers GET /api/benchmarks reads, a report | any | `bench/results/$BENCH_RUN.json`, `bench/results/latest.json`, `bench/results/latest-gpu.json`, `$RUN/report.md` |
| 15 | laptop | `scp root@<droplet-ip>:"ReturnRing/bench/results/*.json" ./bench/results/`; `scp -r root@<droplet-ip>:ReturnRing/artifacts/bench/$BENCH_RUN ./artifacts/bench/`; `scp root@<droplet-ip>:"ReturnRing/artifacts/eval/gnn/*.json" ./artifacts/eval/gnn/`; `scp root@<droplet-ip>:ReturnRing/data/features/customer_embeddings.parquet ./data/features/`; `scp root@<droplet-ip>:ReturnRing/data/ood/features/customer_embeddings.parquet ./data/ood/features/` | copies back what the laptop steps read | - | the same paths on the laptop |
| 16 | droplet | spike step 14: `make spike-down`, `shutdown -h now`, snapshot, destroy | stops billing | down | the snapshot |
| 17 | laptop | `make bench-report BENCH_RUN=$BENCH_RUN DEVICE=gpu` | the MI300X report | - | `docs/results/benchmarks-mi300x.md` |
| 18 | laptop | `make risk` | the A4 row with the GPU embeddings: an offline evaluation that reads `data/truth`, which stays on the laptop (rule 12; the droplet host has no project Python either) | - | `artifacts/eval/risk/metrics.json`, `docs/results/us-204-risk.md`, `data/scores/` |
| 19 | laptop | `make gnn-results` | the US-202 report from the droplet's CPU and GPU files (median-epoch speedup when their settings match) and the A4 row | - | `docs/results/us-202-graphsage.md` |

`make bench DEVICE=gpu` runs steps 5, 6, 8, 9, 11, 12, 13 and 14 in that order; run the steps one
by one instead, so that steps 7 and 10 come where the table puts them (step 10 refuses to start
once vLLM is up).

What to look at:

- Step 6: the build prints `numpy 2.5.4` and `dgl ... on numpy 2.5.4`. A failure there is the
  rocm/dgl image refusing numpy 2 (it ships numpy 1.26.4, pinned by amd-dgl): record it, plan B
  of spec 13 (GraphSAGE as an upgrade, B2 postponed), and go on with step 8.
- Step 8: the log line `B1 cuda: cold start ...` and no "GPU not used" error (amd_xgboost falls
  back to the CPU with only a warning; B1 refuses that run instead of reporting it).
- Step 9: `B2 A cuda 100%: median epoch ...` lines, and the environment of
  `us-202-asos-gpu.json` with `torch_hip` set, the dgl backend and `gpu_arch` gfx942. Exit 2 from
  the job means the GPU is not visible in the container (`make spike-gpu-check`).
- Step 11: `first-request.json` with `"ok": true`; `vllm-start-conditions.txt` says whether the
  page cache was dropped (it needs root on the host).
- Step 12: `amd-smi` must exist on the host (`/opt/rocm/bin` is added to the PATH); otherwise the
  target says so and samples inside the container. `batch_generation` exits 1 if vLLM was
  unavailable for any decision.
- Step 13: `failed` must be 0 in every load of `B3.json` (details); failures are kept out of the
  latencies and noted. To change the loads: `BENCH_ARGS="--b3-batches 1,8,32 --b3-concurrency
  4,16 --b3-repeats 2"`.
- Every part says `measured`, `skipped` (with the reason) or `failed` (with the error); a failed
  part does not stop the next one.

Commit `bench/results/$BENCH_RUN.json`, `latest.json`, `latest-gpu.json`,
`docs/results/benchmarks-mi300x.md`, `docs/results/us-202-graphsage.md` and
`docs/results/us-204-risk.md`. `artifacts/` (raw records, logs, amd-smi samples, models),
`data/` and `bench-work/` stay out of git.

## The demo export: MI300X drafts, the cached demo package and the live stack (US-901, US-903)

The cached demo (`DEMO_MODE=cached`, GPU off) serves the drafts Gemma wrote on the MI300X for the
open returns of one serving snapshot. The cache is keyed by the llm_case, so the snapshot the
batch reads must be the one the demo serves: build it once on the laptop and copy it, never
rebuild it on one side only. `data/truth` stays on the laptop (the snapshot holds no ground truth,
ADR-16). Every step is a `make` target from the repository root; `make -n <target>` prints it.

The droplet's `.env` also needs `PUBLIC_HOST` (the sslip.io name of its IP, for example
`203-0-113-10.sslip.io`) and `ADMIN_PASSWORD_HASH` between single quotes (`make admin-password`
on the laptop prints the hash, bcrypt cost 10): `make gpu-up` serves HTTPS on `PUBLIC_HOST` with
basic auth for the user `admin` on `/api/telemetry` and every write (infra/compose.public.yml;
the Caddyfile answers any other user name with a 401 before hashing). `.env.example` leaves
`PUBLIC_HOST` and the hash empty, so a public start without them stops.

| # | Where | Command | What it does | Writes |
| --- | --- | --- | --- | --- |
| 1 | laptop | `make demo-data` (Docker) or `make pipeline` (host Python) | the store, the models and the serving snapshot, seed 42 | `data/`, `artifacts/`, `serving/` |
| 2 | laptop | `rsync -a --delete serving/ root@<droplet-ip>:ReturnRing/serving/` | the snapshot only, no ground truth; before step 4 (`--delete` also removes `llm_cache/`) | `serving/` (droplet) |
| 3 | droplet | `make spike-vllm spike-wait` | vLLM alone first, healthy before anything else uses the GPU | - |
| 4 | droplet | `make batch-generate` | Gemma's draft of every open return through the local vLLM, golden cases first; stops (exit 2) unless vLLM serves `GEMMA_MODEL`; records the model and `DEVICE` (gpu) of each draft and makes again a cached draft of another model; exit 1 if vLLM failed any (not cached) | `serving/llm_cache/` |
| 5 | droplet | `make demo-export DEMO_GPU="AMD Instinct MI300X (AMD Developer Cloud)"` | checks the snapshot against its manifest, refuses an empty cache and drafts of a model other than `GEMMA_MODEL` or of a batch run off the GPU, packs snapshot, cache and `bench/results/` (pointers and runs) with `MANIFEST.json` (sha256 of every file, the model of each draft, `generated_on`, `rehearsal` false); `DEMO_GPU` has no default; host `python3` 3.8 or later, standard library only | `demo-cache/`, `demo-cache.tar.gz` |
| 6 | laptop | `scp root@<droplet-ip>:ReturnRing/demo-cache.tar.gz .` then `make demo-import demo-local` | checks every hash, then the cached demo on http://localhost:8080 | `demo-cache/` |
| 7 | demo server (CPU) | clone the repository, `cp .env.example .env` (set `POSTGRES_PASSWORD`, `DATABASE_URL`, `PUBLIC_HOST`, `ADMIN_PASSWORD_HASH`), copy `demo-cache.tar.gz`, then `make demo-import demo-up` | the public cached demo: HTTPS, read-only views, no GPU container; the label follows the package's `MANIFEST.json` (its model, not this server's `GEMMA_MODEL`) | - |
| 8 | droplet | `make gpu-up` (live pitch and video) | postgres, the reset with live Gemma drafts, the api, the telemetry sampler, the ui and the proxy on `https://PUBLIC_HOST` | database volume |

- Step 4 runs after the benchmarks of "The GPU session" or on its own; it never needs the
  trainers. Run `make bench-merge` before step 5 so `bench/results/latest.json` names the
  MI300X run the demo shows (a CPU run is packed too, labelled, but never served as latest).
- Step 8 resets the database and drafts every open return through vLLM, `LLM_CONCURRENCY` model
  calls at once (review H32, default 32; `VLLM_TIMEOUT_S` bounds each, default 20 s). The export
  (steps 3 to 5) does not need it.
- `make demo-walk API_URL=http://127.0.0.1:8000` on a host with `requirements/dev.txt` walks the
  pitch against the running API after a reset (`make reset`, `make gpu-reset`, `make demo-reset`);
  in cached mode the case assistant answers 503, as documented. On a read-only demo
  (`PUBLIC_READ_ONLY=true`, the default of steps 6 and 7) the walk reads the same cases and checks
  that every write of the pitch is refused (403) while the checkout evaluation still answers; it
  changes no decision, so it needs no reset.
- A CPU rehearsal of steps 4 to 6 without the GPU: `make demo-export ALLOW_EMPTY_CACHE=1` packs a
  snapshot without drafts (the demo then labels every draft as the fixed template), and
  `make demo-export REHEARSAL=1` packs the drafts of a fake vLLM server (`bench/fake_vllm.py`).
  The batch against the fake runs with `GEMMA_MODEL=fake/gemma-dry-run`, the model the fake
  serves (it refuses a server that does not serve `GEMMA_MODEL`), and its drafts are recorded
  under that name and `DEVICE`; step 4 on the MI300X makes them again. Both are recorded as a
  rehearsal in `MANIFEST.json`, and the demo shows "Precomputed in a rehearsal, not verified on
  AMD MI300X" instead of the MI300X label.
- After step 5, publish `demo-cache.tar.gz` as a GitHub release asset with its sha256 and add
  the download to the README's cached-demo section; until then the laptop path is for the team.

## Files to copy back (laptop)

```bash
scp root@<droplet-ip>:"ReturnRing/bench/results/*.json" ./bench/results/
scp -r root@<droplet-ip>:ReturnRing/artifacts/bench/$BENCH_RUN ./artifacts/bench/
make bench-report BENCH_RUN=$BENCH_RUN DEVICE=gpu      # writes docs/results/benchmarks-mi300x.md
```

Commit `bench/results/$BENCH_RUN.json`, `bench/results/latest.json`, `latest-gpu.json` and
`docs/results/benchmarks-mi300x.md`. `artifacts/bench/` (raw records, logs, amd-smi samples) and
`bench-work/` (the generated store) stay out of git. Then step 14 (stop, snapshot, destroy).

## Files

| File | Spike |
| --- | --- |
| `preflight.py` | host and `.env` checks |
| `weights.sh` | Gemma weights download (SPK-G7) |
| `g1_guided_json.py` | SPK-G1, SPK-G5 (`--image`) |
| `g2_xgboost_gpu.py`, `g2_load_cpu.py` | SPK-G2 |
| `g3_graphsage_synthetic.py` | SPK-G3 |
| `g4_coexistence.sh` | SPK-G4 |
| `g6_tool_call.py` | SPK-G6 |
