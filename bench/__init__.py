"""Benchmarks B1-B4 of docs/spec.md section 12 (US-902): how much AMD does, measured.

Status: the CPU against MI300X benchmarks B1-B4 were retired by ADR-18 (16 Oct 2026); none ran
on an MI300X. This is the historical code ADR-18 keeps. `make bench` runs the CPU harness check
(the one committed run: bench/results/20261014T002158Z-cpu.json). `--device gpu` exists in the
code but never ran. The GPU launchers and droplet runbooks that the comments of these modules
mention were removed on 17 Oct 2026.

- B1 (b1_xgboost): XGBoost training and scoring time on ASOS GraphReturns, protocols A and B of
  ADR-10, CPU against the MI300X, several data sizes; same data, version and parameters on both.
- B2 (b2_gnn): GraphSAGE timings and F1 as US-202 states them, through the hook
  ml.gnn.b2_timing (the US-202 job, once per device and data size); skipped where it cannot run
  (no PyTorch), with the reason.
- B3 (b3_llm): Gemma on the local vLLM, through llm.client.VllmClient only (rule 2): latency
  p50 and p95, tokens per second for single requests, batches of 1, 8 and 32 and concurrency
  levels, every load on one seeded case list built by llm.case from the synthetic store; a
  dry run against a local fake OpenAI-compatible server (fake_vllm); the cost per decision in a
  declared, versioned operating scenario (cost).
- B4 (b4_pipeline): the nightly pipeline duration per step, with GPU utilisation sampled from
  `amd-smi metric --json` every 5 s (amdsmi).

Every measurement carries origin, period and nature (rule 11). The parts write
artifacts/bench/<run-id>/<B>.json; `python -m bench merge` assembles bench/results/<run-id>.json
with a projection on the API contract (GET /api/benchmarks) and moves the latest pointer.
Method and the four rules of spec 12: docs/benchmarks.md.

This package and bench.record, bench.amdsmi, bench.stats, bench.api_view and bench.report use the
standard library only, so the sampler runs on the droplet host and the merge in any container.
"""

SCHEMA = "returnring.bench/1"
STORY = "US-902"
