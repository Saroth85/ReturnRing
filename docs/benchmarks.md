# Benchmarks B1-B4: method and status (US-902)

The `bench/` package is the harness of the four benchmarks of spec section 12 ("Benchmark AMD"),
written to measure the same work on the CPU and on an AMD Instinct MI300X. Results:
`bench/results/<run-id>.json` (one versioned file per run); report:
[`docs/results/benchmarks-cpu.md`](results/benchmarks-cpu.md), generated from the run file.

## Status (17 October 2026)

- **Retired.** ADR-18 (16 October 2026) removed the CPU against MI300X benchmarks from the
  delivery plan and from the System screen. None of B1-B4 ran on an MI300X: the AMD Developer
  Cloud droplet stayed blocked by an account GPU limit
  ([AMD session report](amd-training-session-2026-10-16.md)). The code stays as the historical
  B1-B4 code that ADR-18 keeps; it is not a release gate.
- **Served as pending.** GET /api/benchmarks serves all four benchmarks as `pending`: there is no
  `bench/results/latest.json`, the pointer the API reads, and a CPU run writes
  `latest-cpu.json` only. The System screen shows no benchmark.
- **What ran on AMD instead.** XGBoost and GraphSAGE training on an MI300X VF and Gemma through
  AMD Token Factory, on 16 October 2026, with no speedup computed:
  [AMD session report](amd-training-session-2026-10-16.md). Token Factory is a shared inference
  service: never point the old live B3 path at it, which labels its hardware as the MI300X.
- **One run on file**, a CPU harness check (below). The detailed protocol written for the MI300X
  session (B1 sizes and variants, B3 loads and cold start, B4 steps, cost scenarios) is in the
  history of this file (`git show dac3a3a:docs/benchmarks.md`) and in the docstrings of `bench/`.

## What each benchmark measures

| ID | Measures | On what | How |
| --- | --- | --- | --- |
| B1 | XGBoost training time and scoring time, per device | ASOS GraphReturns train split (real, CC BY 4.0), protocol A (paper, 59 features, every train event) and protocol B (decision time, 30 features, ADR-10) | `ml.propensity.run.training_data` gives the exact matrices US-201 trains on; `ml.propensity.train.train_booster` trains them with the paper's parameters, the same seed and threads, `device="cpu"` or `"cuda"` (AMD XGBoost on ROCm); seeded nested subsets of the fit rows; an early-stopping and a fixed-rounds variant |
| B2 | GraphSAGE training time and F1 as US-202 states it | ASOS GraphReturns, protocols A and B, seeded customer shares | `ml.gnn.b2_timing` runs the US-202 job (`python -m ml.gnn asos`) in a fresh process per device and data size and reads its metrics file; `ml.gnn.b2_unavailable()` gives the reason to skip where PyTorch is missing |
| B3 | Gemma latency p50 and p95, output tokens per second, batches and closed-loop concurrency; the cost per decision | one seeded list of llm_case inputs drawn from the open returns of the synthetic store (Navigli Fashion), built by `llm.case.build_case` from the engine decisions (`bench/cases.py`) | `llm.generate.generate` (guided JSON, validator, one regeneration) through `llm.client.VllmClient` against `VLLM_BASE_URL` only (rule 2); on the CPU, a dry run against `bench/fake_vllm.py`, a local fake OpenAI-compatible server that answers after a declared toy delay |
| B4 | Nightly pipeline duration per step, the device each step ran on, GPU utilisation when a GPU is present | the synthetic store generated from the committed ASOS calibration, seed 42, scale 1.0, main and OOD | the commands of `make pipeline`, `make score` and the batch generation (`bench/batch_generation.py`), timed one by one in a copy under `bench-work/<run-id>/`; `amd-smi metric --json` every 5 s on a GPU host |

## The four rules of spec 12

| Rule | What it asks | How the harness keeps it |
| --- | --- | --- |
| Comparable quality | Same data, version and configuration on both devices; no speedup from different workloads | B1 runs both devices in one process on the same matrices, subsets, parameters, seed and threads and computes a speedup from fixed-rounds runs only; B2 divides median epochs of runs with identical settings; B3 runs every load on one case list; B4 labels each step with the device it ran on; a GPU run that trained on the CPU is refused |
| Separate times | Cold start, training, scoring and generation measured apart; p50 and p95 with concurrency and throughput | Every measurement has a phase (`cold_start`, `training`, `scoring`, `generation`, `pipeline`, `cost`); B3 latencies carry their batch size or concurrency, next to the throughput of the same load |
| Full cost | The cost per decision includes idle time in a declared operating scenario | `bench/cost.py`: versioned assumptions, development experiments in no scenario |
| Growing loads | Several volumes and batch sizes | B1 at several data sizes, B2 at several customer shares, B3 at several batch sizes and concurrency levels (`BENCH_ARGS` changes them) |

The cost per decision (NFR-13, target at most 0.01 EUR) is a computation under the versioned
assumptions of `bench/cost.py` (now `cost-v2`: the 1.99 USD an hour MI300X droplet price of spec
10 and 11, 0.86 EUR per USD, per-second billing and the snapshot, three operating scenarios); its
nature is simulated, and no version of it was computed from a live B3.

## The only run: 20261014T002158Z-cpu

Report: [`docs/results/benchmarks-cpu.md`](results/benchmarks-cpu.md); run file:
`bench/results/20261014T002158Z-cpu.json`.

- **When and where.** Created 2026-10-14T00:57:55Z from commit e4119dc with uncommitted changes,
  on a development machine with 4 shared vCPUs (Intel Xeon @ 2.80GHz, 15.7 GiB) under a load
  average of about 11 to 14 from other jobs. No GPU.
- **B1, measured (real).** The CPU only, on ASOS GraphReturns, protocols A and B, at 2%, 5% and
  10% of the fit rows, one repeat, early stopping and 50 fixed rounds. The times are upper bounds
  on a busy machine.
- **B2, skipped.** `ml.gnn` was not yet in that checkout (US-202 was built in parallel).
- **B3, dry run (simulated).** Against `bench/fake_vllm.py`: the latencies are the fake server's
  toy delays, not Gemma. Its two cost figures (`cost-v1`, always-on and business-hours) are the
  declared droplet price divided by the synthetic store's 151.43 decisions a day: no latency
  enters them, and they say nothing about Gemma or the MI300X. The declared nightly-batch
  scenario was not computed (it needs a live B3 and B4).
- **B4, measured (real time, synthetic data).** 517.3 s on the CPU for every step, with the mock
  model (`LLM_MODE=mock`) in the batch generation and no GPU sampling.
- **Before the rework.** The run predates the benchmark rework of bc6f2e8 (14 Oct 2026, 03:01
  UTC: B2 through `ml.gnn.b2_timing`, cost-v2, review fixes G1-G15), so the current defaults
  never produced a run. It shows that the harness works, not CPU performance, and is not served.

## How to run the CPU check

```bash
make bench BENCH_RAW=data/raw BENCH_DATA=<a data root with store/, features/ and scores/>
# optional: BENCH_ARGS="--b1-repeats 1 --b1-fractions 0.1,0.5,1 --b2-samples 1"
# writes bench/results/<run-id>.json, latest-cpu.json and docs/results/benchmarks-cpu.md
```

`make bench` is `python -m bench run --device cpu` followed by `python -m bench report`. B1 and
B2 need the ASOS files in `data/raw` (`make asos`); B2 trains only where PyTorch is installed
(`infra/requirements-gnn-cpu.txt`); B3 is a dry run; B4 writes its copy under `bench-work/`
(outside `artifacts/`, which running services mount) and deletes its generated ground truth when
it ends (rule 12). `--device gpu` exists in the code but never ran.

## Natures and the API contract

Natures (rule 11): a time measured on real hardware is `real`, also when the data it ran on is
synthetic (the `data_nature` field says so: B4, B3 inputs); dry-run figures and costs are
`simulated`.

The `api` member of a run file matches `BenchmarksResponse` of `apps/api/schemas.py` as it is today
(tests validate every projection against the pydantic model):

- a `measured` part becomes a `measured` benchmark with its real measurements; `dry_run`,
  `skipped` and `failed` parts become `pending` with the reason in the notes (a dry run is never
  served: "illustrative" is for contract examples, ADR-15);
- `rows` and cold or warm have no field: they are in the metric name (`train_seconds_a_es_25pct`)
  and the label;
- the cost per decision is served as EUR per 1,000 decisions in the notes, because `eur` allows
  two decimals (0.0034 EUR would read 0.00) and a measured benchmark may hold only real results;
- speedups are in the part's details and the report, not in the results: `ratio` is bounded to
  [0, 1];
- image references longer than 64 characters are shortened to the tag and 12 digest characters.

A simulated cost result inside a measured benchmark would need a contract change (an ADR-15
amendment). None was made: the benchmarks were retired first.

## Limitations

- CPU numbers of a development machine are measured on shared vCPUs under the load average
  recorded next to each run. The committed CPU run was measured under a load of about 11 to 14 on
  4 vCPU, from a tree with uncommitted changes, before the code hashes existed: it shows that the
  harness works, not CPU performance, and is not served.
- B4's `generate` step builds the synthetic store from scratch: a stand-in for the nightly ingest
  of a real shop, whose duration would differ.
- B3 inputs have no customer free text (the store has none), so the adversarial cases of the LLM
  evaluation set are not in B3; their latency is the same path. B3's latencies are those of cases
  already in vLLM's prefix cache (one untimed pass first), a lower bound for a night of new cases.
- amd-smi activity is an instantaneous busy share sampled every 5 s: bursts shorter than the
  interval can be missed.
