# GPU spike results

Fill in on the MI300X droplet (docs/spec.md section 13; runbook in infra/spikes/README.md). Report
numbers as measured, failures included. Raw outputs: `artifacts/spikes/` (not committed).

## Session

| Field | Value |
| --- | --- |
| Date, droplet created / destroyed (UTC) | |
| Droplet image and size | |
| Host OS, kernel | |
| Host amdgpu module version, driver packages, host ROCm | |
| Docker, Docker Compose | |
| vCPU (nproc), RAM | |
| Quick Start container found (name, image, ports), stopped? | |
| Total droplet time and cost | |
| Snapshot taken (name, size, minutes) or reason to skip | |

## Images

| Service | Tag | Digest (`docker image inspect --format '{{index .RepoDigests 0}}'`) | ROCm |
| --- | --- | --- | --- |
| vllm | vllm/vllm-openai-rocm:v0.31.0 | | 7.2.3 |
| trainer-gnn | rocm/dgl:dgl-2.4.0.amd0_rocm7.0.0_ubuntu24.04_py3.12_pytorch_2.8.0 | | 7.0.0 |
| trainer-gbm base | rocm/dev-ubuntu-24.04:7.2.3-complete | | 7.2.3 |
| spike-client | python:3.12-slim | | none |
| Gemma weights | GEMMA_MODEL | GEMMA_REVISION (Hugging Face commit) | n/a |

## Results

| ID | Result (pass/fail) | Measured | Output file | Plan B chosen |
| --- | --- | --- | --- | --- |
| SPK-G1 Gemma 4 31B, guided JSON | | tokens/s single request (median after warm-up): ; request without API key refused with 401 (nfr11_key_enforced): ; bench C=1 output tok/s, median TPOT ms, P95 TTFT ms: ; bench C=32 output tok/s: | g1.json, logs/g1-bench-c1.txt, logs/g1-bench-c32.txt | |
| SPK-G2 amd_xgboost on GPU | | official verify device: ; train s CPU / GPU: ; speedup: ; test logloss CPU / GPU: ; warm-up s: ; load in xgboost-cpu max abs diff: ; context of each half: | g2_gpu.json, g2_cpu.json, g2_load_cpu.json | |
| SPK-G3 DGL GraphSAGE | | epoch s CPU / GPU (homogeneous): ; speedup: ; hetero speedup: ; GPU peak GB: ; torch / HIP / DGL: ; context of each half: | g3_small.json, g3_gpu.json, g3_cpu.json, g3_hetero_gpu.json, g3_hetero_cpu.json | |
| SPK-G4 Coexistence | | peak VRAM used (amd-smi): ; DGL peak allocated GB: ; XGBoost train_s_median under load (g4_xgb.json) vs alone (g2_gpu.json): ; DGL epoch_s_median under load vs alone: ; LLM requests passed: | g4_*.json, g4_amdsmi.log | |
| SPK-G5 Gemma vision | | checklist valid: ; latency s: | g5.json | |
| SPK-G6 Tool calling | | mode that passed (auto/named): ; step 1 / step 2 s: | g6-*.json | |
| SPK-G7 Model download | | download s (weights-status.txt, ran next to G2 and G3): ; HF_TOKEN needed: ; vLLM start to healthy s: ; KV cache tokens, max concurrency at 16,384: | logs/weights-status.txt, logs/prep-status.txt, g7-vllm-log.txt | |

## Decisions to carry into the spec

- D-05 (token for Gemma 4):
- D-12 (31B or 26B-A4B):
- gpu-memory-utilization (spec section 11 memory table):
- Image digests to keep in `.env.example`:
