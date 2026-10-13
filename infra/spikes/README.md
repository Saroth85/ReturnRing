# GPU spikes (run on the MI300X droplet, Monday evening)

Run one at a time and write the results in `docs/spike-results.md`: pass or fail, numbers, image tags and
digests, ROCm versions, plan B. Spec: docs/spec.md section 13.

| ID | What | How |
| --- | --- | --- |
| SPK-G1 | Gemma 4 31B on vLLM, guided JSON, tokens/s | `make gpu-up` (vllm only), then `python infra/spikes/g1_guided_json.py` |
| SPK-G2 | amd_xgboost on GPU | `docker compose ... run trainer-gbm python infra/spikes/g2_xgboost_gpu.py` |
| SPK-G3 | DGL GraphSAGE example | `docker compose ... run trainer-gnn` |
| SPK-G4 | Coexistence on one GPU | G1 running while G2 and G3 run; record `amd-smi metric` |
| SPK-G5 | Gemma vision | `python infra/spikes/g1_guided_json.py --image path/to/photo.jpg` |
| SPK-G6 | Tool calling | add the tool-call flags for Gemma 4 to the vllm command, then a fake tool round trip |
| SPK-G7 | Model download | time the first start; note whether `HF_TOKEN` was needed |

Plan B if Gemma 4 31B is slower than 15 tokens/s: set `GEMMA_MODEL` to the 26B-A4B variant and re-run G1.
