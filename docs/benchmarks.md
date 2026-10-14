# Benchmarks B1-B4: method (US-902)

How much of ReturnRing runs on the AMD Instinct MI300X, and what it buys, measured the same way on
the CPU and on the GPU (spec section 2, V1; section 12, "Benchmark AMD"). The harness is the
`bench/` package; `make bench DEVICE=cpu|gpu` runs it. Numbers are collected, not promised: the
pitch shows the measured ones, also when they differ from the estimates of the spec.

Results: `bench/results/<run-id>.json` (one versioned file per run) with `latest.json`, the run
that GET /api/benchmarks serves. Reports: `docs/results/benchmarks-cpu.md` (this container, no
GPU) and `docs/results/benchmarks-mi300x.md` (the droplet, written after the GPU session).

## What each benchmark measures

| ID | Measures | On what | How |
| --- | --- | --- | --- |
| B1 | XGBoost training time and scoring time, CPU against MI300X | ASOS GraphReturns train split (real, CC BY 4.0), protocol A (paper, 59 features, every train event: the heavier, published configuration) and protocol B (decision time, ADR-10) | `ml.propensity.run.training_data` gives the exact matrices US-201 trains on; `ml.propensity.train.train_booster` trains them with the paper's parameters, the same seed and threads, `device="cpu"` or `"cuda"` (amd_xgboost on ROCm) |
| B2 | GraphSAGE training time and F1, CPU against MI300X, F1 against the paper (0.792) | ASOS GraphReturns, protocols A and B | the hook `ml.gnn.b2_timing` (below); skipped until US-202 provides it |
| B3 | Gemma latency p50 and p95, output tokens per second for single requests, batches of 1, 8 and 32 and concurrency levels 1, 8 and 32; cost per decision | the llm_case inputs of the open returns of the synthetic store (Navigli Fashion), built by `llm.case.build_case` from the engine decisions, golden cases first (`bench/cases.py`) | `llm.generate.generate` (guided JSON, validator, one regeneration) through `llm.client.VllmClient` against `VLLM_BASE_URL` only (rule 2) |
| B4 | Nightly pipeline duration per step and GPU utilisation | the synthetic store generated from the committed ASOS calibration, seed 42, scale 1.0, main and OOD | the commands of `make pipeline`, `make score` and the batch generation, timed one by one; `amd-smi metric --json` every 5 s |

### B1 in detail

- Sizes: nested seeded subsets of 10, 25, 50 and 100% of the fit and validation rows (the
  smaller is inside the larger), the same subsets on both devices.
- Two variants: early stopping (the product's configuration, at most 2,000 rounds, patience 5)
  and exactly 200 rounds without early stopping, so that both devices do identical work even if
  their early stopping ends at a different round.
- Next to every time: rounds run, seconds per 100 rounds, DMatrix build time, the validation log
  loss and AUC of the kept trees, the threads, and the 1-minute load average before and after
  (a CPU time measured on a busy machine is an upper bound).
- Cold start: the first training call on a device in the process (20,000 rows, 10 rounds),
  device initialisation included, reported apart from the warm runs.
- Scoring: one batch over every fit row with the full-size model, and 200 single-row calls
  (DMatrix build included, the cost an API call would see), p50 and p95.
- A run that asked for the GPU and trained on the CPU stops B1 (`train.check_device`): amd_xgboost
  falls back with only a warning, and such a number is never reported as the MI300X.

### B3 in detail

- Unit of work: one decision's draft, as the product makes it (one or two model calls). A
  decision whose model call failed (HTTP error, timeout) is counted as failed and kept out of the
  latencies: the template fallback is fast and would flatter them.
- Batches: N decisions sent at once (a barrier releases N threads), repeated; latency p50 and p95
  inside the batch, batch wall time, aggregate output tokens per second (ADR-03, the nightly
  pre-generation).
- Concurrency levels: a closed loop with C requests in flight until max(16, 2C) decisions are
  done; latency p50 and p95, decisions per second, aggregate output tokens per second and the
  median output tokens per second of one request (prefill included).
- Cold start: vLLM container start to `/health` answering (the start and healthy files written by
  `make bench-vllm`, 15 s polling), and the first decision after it, outside the percentiles.
- Output tokens are vLLM's `usage.completion_tokens`. Consecutive jobs get distinct cases; the
  system prompt is the same for every case, so vLLM's prefix cache works as it would in
  production.
- Dry run (`DEVICE=cpu`): the same harness against `bench/fake_vllm.py`, a local fake
  OpenAI-compatible server on 127.0.0.1 that answers with the template output after a declared toy
  delay. Its numbers are simulated: they check the harness and say nothing about Gemma.

### B4 in detail

Steps, in order: `generate` (main and OOD stores: the stand-in for the nightly ingest), `graph`,
`features`, `anomalies`, `risk` (offline training and evaluation), `scoring` (saved models at the
last snapshot), `batch_generation` (`bench/batch_generation.py`: every open return's draft, 32 in
flight; the mock model on a laptop, vLLM on the droplet). The copy lives in
`bench-work/<run-id>/`, outside `artifacts/` (which running services mount); its generated ground
truth is deleted when B4 ends (rule 12). The run stops at the first failing command, as make
does, and keeps what it measured. GPU utilisation of a step: the amd-smi samples inside its
window (mean and max activity, max VRAM used by every process, vLLM's reservation included, mean
socket power). A step shorter than 5 s may have no sample, and then has no GPU number.

When the API's own batch (`apps/api/batch_generate`, phase 4) is merged, the last step can time
it instead: one line in `bench/b4_pipeline.py`.

## The four rules of spec 12

| Rule | What it asks | How the harness keeps it |
| --- | --- | --- |
| Comparable quality | Same data, version and configuration for CPU and GPU; no speedup from different workloads | B1 runs both devices in one process on the same matrices, subsets, parameters, seed and threads, with the same amd_xgboost build on the droplet; it reports quality next to time, adds a fixed-rounds variant with identical work, and refuses a silent CPU fallback. B2 requires the same of `ml.gnn.b2_timing`. Speedups are computed only between runs of the same size and variant |
| Separate times | Cold start, training, scoring and generation measured apart; p50 and p95 always with concurrency and throughput | Every measurement has a phase (`cold_start`, `training`, `scoring`, `generation`, `pipeline`, `cost`); B3 latencies carry their batch size or concurrency, next to the throughput of the same load |
| Full cost | The cost per decision includes idle time in the declared operating scenario; development experiments stay apart | `bench/cost.py`, versioned assumptions (`cost-v1`), the droplet billed while it exists; spikes, benchmarks and failed runs are in no scenario |
| Growing loads | Several volumes and batch sizes, to see where the GPU adds value | B1 at four data sizes; B3 at batches 1, 8, 32 and concurrency 1, 8, 32 (changeable with `BENCH_ARGS`) |

## Cost per decision (NFR-13)

Target: at most 0.01 EUR per decision with the LLM on the MI300X, idle time included.

Assumptions `cost-v1` (`bench/cost.py`; a change makes a new version):

| Assumption | Value | Nature |
| --- | --- | --- |
| Droplet price | 1.99 USD per hour (spec 10 and 11) | declared price |
| Exchange rate | 0.86 EUR per USD | assumption: a round figure near the ECB EUR/USD reference rates of 2025-2026; replace it with the ECB rate of the day before quoting a cost |
| Billing | per second while the droplet exists, powered on or off | assumption |
| Droplet boot from the snapshot to compose up | 600 s | assumption, unmeasured |
| Idle margin between and after the jobs | 600 s | assumption |
| vLLM start to healthy | measured by B3; 600 s if not measured | measured or assumed, said in the breakdown |
| Decisions per day | return requests per day in the 28 days before the scoring instant of the store B3 read | synthetic (Navigli Fashion: about 150 a day at scale 1.0) |

Scenarios:

- `nightly-batch` (the declared operating scenario): every night the droplet is created from the
  snapshot, starts vLLM, runs the nightly pipeline (B4's measured duration without its generation
  step) and generates the day's drafts at the measured batch-32 rate (B3), then is destroyed.
  Droplet seconds = boot + vLLM start + pipeline + decisions x seconds per decision + idle margin.
- `business-hours`: the droplet on 12 hours a day for live generation, busy or idle.
- `always-on`: 24 hours a day.

Each scenario gives EUR per decision, EUR per 1,000 decisions, EUR per day, whether it meets the
target, and the break-even volume (the smallest number of decisions per day that meets it). The
cost is a computation under assumptions: its nature is simulated, even when its inputs are
measured. `nightly-batch` needs a live B3 and a B4 of the same run; on the CPU run it says so
instead of guessing.

## Outputs

`python -m bench run` writes one part per benchmark to `artifacts/bench/<run-id>/<B>.json`
(status `measured`, `dry_run`, `skipped` with the reason, or `failed` with the error; a failed
part keeps what it measured and does not stop the next one), plus raw records and logs. `python -m
bench merge` assembles `bench/results/<run-id>.json`:

| Member | Content |
| --- | --- |
| `run_id`, `device`, `created_at`, `git` | `<UTC time>-<cpu|gpu>`, the commit and whether the tree had uncommitted changes |
| `rules` | the four rules and how they are applied |
| `hardware` | per part: CPU model, logical CPUs, RAM, kernel, container flag; GPU name, architecture and VRAM from `amd-smi static --json` on the droplet |
| `software` | per part: Python, ROCm (`/opt/rocm/.info/version`), xgboost version and HIP build, numpy, polars, scikit-learn, torch and DGL (B2), vLLM version and served model (`/version`, `/v1/models`), the image references with digest (`VLLM_IMAGE`, `DGL_IMAGE`, `ROCM_BASE_IMAGE`) and the model revision (`GEMMA_REVISION`) |
| `benchmarks` | every part: title, dataset, configuration, notes, measurements and details (per-run tables, speedups, case summary, cost breakdown, step logs, the amd-smi timeline) |
| `measurements` | every number: metric, label, value, unit, device, phase, provenance (origin, period, nature), data nature, protocol, concurrency, batch size, rows, cold or warm, repeats |
| `api` | the projection served by GET /api/benchmarks (below) |

`latest.json` names the run the API serves; `latest-cpu.json` and `latest-gpu.json` the last run
of each kind. A CPU run never replaces a GPU run as `latest.json` (`--force-latest` to override).

Natures (rule 11): a time measured on real hardware is `real`, also when the data it ran on is
synthetic (the `data_nature` field says so: B4, B3 inputs); dry-run figures and costs are
`simulated`.

## The API contract

The `api` member matches `BenchmarksResponse` of `apps/api/schemas.py` as it is today (tests
validate every projection against the pydantic model):

- a `measured` part becomes a `measured` benchmark with its real measurements; `dry_run`,
  `skipped` and `failed` parts become `pending` with the reason in the notes (a dry run is never
  served: "illustrative" is for contract examples, ADR-15);
- `rows` and cold or warm have no field: they are in the metric name (`train_seconds_a_es_25pct`)
  and the label;
- the cost per decision is served as EUR per 1,000 decisions in the notes, because `eur` allows
  two decimals (0.0034 EUR would read 0.00) and a measured benchmark may hold only real results;
- image references longer than 64 characters are shortened to the tag and 12 digest characters.

Contract change proposed (needs an ADR-15 amendment, `apps/` is not changed here):

1. `Benchmark._status`: a `measured` benchmark may hold results of phase `cost` whose nature is
   `simulated` (computed from measured numbers under declared assumptions); every other result
   stays `real`. The cost then moves from the notes to the results.
2. `BenchmarkResult`: two optional fields, `rows: int | None` (data size, spec 12 "Carichi
   crescenti") and `warm: bool | None` (cold start or after a warm-up), now only in the metric name
   and the label.
3. A unit for a speedup (for example `times`, non-negative), or the UI computes it from the CPU and
   MI300X results of the same metric; `ratio` is bounded to [0, 1].

## The B2 interface (US-202)

B2 calls one function and nothing else of `ml/gnn`:

```python
ml.gnn.b2_timing(raw_dir: Path, *, protocol: "A" | "B", device: "cpu" | "cuda", seed: int)
    -> Mapping[str, object]  # bench.b2_gnn.GnnTiming
```

It trains the model of spec 8.1 on the raw ASOS tables with the same data, parameters and seed on
both devices and returns: `protocol`, `device_requested`, `device_effective`, `nodes`, `edges`,
`epochs`, `cold_start_seconds` (first epoch, device initialisation included), `epoch_seconds`
(every later epoch), `train_seconds`, `inference_seconds`, `f1`, `threshold`, `population`,
`data_origin`, `versions` (torch, DGL, HIP), and optionally `peak_memory_gib`,
`graph_build_seconds`, `precision`, `recall`. `bench.b2_gnn.check_timing` refuses a record off
this shape, and a GPU run whose effective device is the CPU.

## How to run

Here (CPU, no GPU):

```bash
make bench BENCH_RAW=data/raw BENCH_DATA=<a data root with store/, features/ and scores/>
# optional: BENCH_ARGS="--b1-repeats 1 --b1-fractions 0.1,0.5,1"; writes docs/results/benchmarks-cpu.md
```

On the droplet: `make bench DEVICE=gpu`, in the order and with the checks of
`infra/spikes/README.md` ("Benchmarks B1-B4"). `make -n bench DEVICE=gpu` prints every command.

## Limitations

- CPU numbers of this container are measured on a shared development machine (4 vCPU) under the
  load average recorded next to each run; they are not the droplet's CPU baseline, which B1
  measures on the droplet itself with vLLM down.
- B4's `generate` step builds the synthetic store from scratch: a stand-in for the nightly ingest
  of a real shop, whose duration would differ.
- B3 inputs have no customer free text (the store has none), so the adversarial cases of the LLM
  evaluation set are not in B3; their latency is the same path.
- amd-smi activity is an instantaneous busy share sampled every 5 s: bursts shorter than the
  interval can be missed.
- vLLM start to healthy depends on the compile cache in the `vllm-cache` volume: a first start
  compiles, a restart from the snapshot does not; the run records only the time.
