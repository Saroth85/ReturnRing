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
- Each spike starts on its own. Nothing is started with `make gpu-up`.
- vLLM keeps its weights (`hf-cache`) and compile cache (`vllm-cache`) in volumes, so a restart
  or a later session from the snapshot skips both.
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
| 6 | `spike-gpu-check`, `spike-g2`, `spike-g2-load` | 6 min | yes |
| 7 | `spike-g3` | 5-10 min | yes |
| 8 | `spike-weights-wait` | whatever is left of the download | no |
| 9 | `spike-vllm`, `spike-wait`, `spike-g7` | 5-15 min | yes |
| 10 | `spike-g1`, `spike-g1-bench` | 8 min | yes |
| 11-12 | `spike-g5`, `spike-g6` | 3 min | yes |
| 13 | `spike-g4` | 7 min | yes |
| 14 | copy artifacts, stop, snapshot, destroy | 5 min + snapshot | no |
| 15 | fill `docs/spike-results.md` | laptop, no cost | no |

All times are estimates: download speed, image extraction and vLLM compile time on ROCm are
unmeasured. G2 and G3 run before vLLM starts, because vLLM measures free GPU memory while it
starts and a training job running at that moment would skew its KV-cache sizing. They run while
the weights download, so the GPU does not wait on the network.

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

### 6. SPK-G2: amd_xgboost on GPU (about 6 min)

```bash
make spike-gpu-check             # 1 min: gfx942 from trainer-gbm, "gpu True" from trainer-gnn
make spike-g2
make spike-g2-load
```

Expected: an environment line with `"xgboost": "3.2.0", "use_hip": true` and `rocm` 7.2.3;
`official_verify` with `device_effective` other than `cpu` (the exact string, likely `cuda:0`, is
unconfirmed); CPU and GPU results with the same `test_logloss`; `"pass": true`. Then
`g2_load_cpu.py` prints `"pass": true` with `max_abs_diff` near 0: the GPU model loads in
xgboost-cpu 3.2.0 (spec section 10).

- `spike-gpu-check` fails for one image only: the other image's line still shows whether the host
  passes the GPU through; the failing image is the problem.
- Exit 2 (GPU not used): amd_xgboost falls back to CPU with only a warning. Check
  `make spike-gpu-check`.
- Plan B (spec 13): XGBoost on CPU for training, GPU only for Gemma and DGL.

### 7. SPK-G3: DGL GraphSAGE (5-10 min)

```bash
make spike-g3
```

Three runs of `g3_graphsage_synthetic.py`: `--small` (smoke test), the spec size (1.2M nodes, 12M
edges) and `--hetero` (customer/product graph with brand, product type and reason nodes). Model of
spec 8.1: SAGEConv 16, max-pool, dropout 0.2, Adam 0.01; synthetic seeded data, no download.
Expected: `torch_hip` set, `dgl` 2.4.0, `gpu_visible: true`, `gpu_arch` gfx942, `"pass": true`.

- Exit 2: GPU not visible in the DGL container: `make spike-gpu-check`.
- Optional upstream example (downloads Cora from data.dgl.ai; do not pass `--gpu 0`, the script
  has no such flag):

  ```bash
  docker compose -f infra/docker-compose.yml -f infra/compose.gpu.yml -f infra/compose.spike.yml \
    --env-file .env --profile gpu run --rm -T -w /src/dgl/examples/pytorch/graphsage \
    trainer-gnn python train_full.py --dataset cora
  ```
- Plan B (spec 13): GraphSAGE only as an upgrade; benchmark B2 postponed.

### 8. Wait for the weights (SPK-G7, first half)

```bash
make spike-weights-wait
```

Expected: `download-weights: exit 0, took N s (google/gemma-4-31B-it@<revision>)`. N is the
SPK-G7 download time; it ran next to G2 and G3, which use the GPU, not the network. A 401 or 403
is not expected (both Gemma 4 models are not gated): set `HF_TOKEN` in `.env`, then
`make spike-weights spike-weights-wait`.

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
