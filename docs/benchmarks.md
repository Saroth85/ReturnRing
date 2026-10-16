# Benchmarks B1-B4: method (US-902)

How much of ReturnRing runs on the AMD Instinct MI300X, and what it buys, measured the same way on
the CPU and on the GPU (spec section 2, V1; section 12, "Benchmark AMD"). The harness is the
`bench/` package; `make bench DEVICE=cpu|gpu` runs it. Numbers are collected, not promised: the
pitch shows the measured ones, also when they differ from the estimates of the spec.

Results: `bench/results/<run-id>.json` (one versioned file per run). `latest.json` names the run
GET /api/benchmarks serves, and only a GPU run moves it: a CPU run checks the harness on a
development machine and updates `latest-cpu.json` only (`--force-latest` overrides). Until the GPU
session there is no `latest.json`, and every benchmark is pending. Reports:
`docs/results/benchmarks-cpu.md` (a development machine, no GPU) and
`docs/results/benchmarks-mi300x.md` (the droplet, written after the GPU session).

> Delivery scope, 16 October 2026: CPU versus MI300X comparisons are retired from the submission
> and the System screen (ADR-18). This document preserves the historical research protocol.
> Current training runs use `python -m scripts.train_amd`; Token Factory is evaluated as a shared
> inference service. Never pass it to the old live B3 path, which labels hardware as MI300X.

## What each benchmark measures

| ID | Measures | On what | How |
| --- | --- | --- | --- |
| B1 | XGBoost training time and scoring time, CPU against MI300X | ASOS GraphReturns train split (real, CC BY 4.0), protocol A (paper, 59 features, every train event: the heavier, published configuration) and protocol B (decision time, ADR-10) | `ml.propensity.run.training_data` gives the exact matrices US-201 trains on; `ml.propensity.train.train_booster` trains them with the paper's parameters, the same seed and threads, `device="cpu"` or `"cuda"` (amd_xgboost on ROCm) |
| B2 | GraphSAGE training time and F1, CPU against MI300X, F1 as US-202 states it | ASOS GraphReturns, protocols A and B, 25%, 50% and 100% of the customers | `ml.gnn.b2_timing` (below) runs the US-202 job in the trainer-graphsage image |
| B3 | Gemma latency p50 and p95, output tokens per second for single requests, batches of 1, 8 and 32 and concurrency levels 4 and 16; the first request after vLLM starts; cost per decision | one list of llm_case inputs drawn from the open returns of the synthetic store (Navigli Fashion), built by `llm.case.build_case` from the engine decisions (`bench/cases.py`) | `llm.generate.generate` (guided JSON, validator, one regeneration) through `llm.client.VllmClient` against `VLLM_BASE_URL` only (rule 2) |
| B4 | Nightly pipeline duration per step, the device each step ran on, GPU utilisation | the synthetic store generated from the committed ASOS calibration, seed 42, scale 1.0, main and OOD | the commands of `make pipeline`, `make score` and the batch generation, timed one by one; `amd-smi metric --json` every 5 s |

### B1 in detail

- Sizes: nested seeded subsets of 10, 25, 50 and 100% of the fit and validation rows (the
  smaller is inside the larger), the same subsets on both devices.
- Two variants. Exactly 200 rounds without early stopping: identical work on both devices, the
  only runs a CPU-over-GPU speedup is computed from, with the rounds of both devices next to it.
  Early stopping (the product's configuration, at most 2,000 rounds, patience 5): the CPU and GPU
  `hist` methods build different quantile cuts and sum in a different order, so each device
  stops at its own round (review G1). These runs are shown side by side with their rounds and
  the ratio of seconds per 100 rounds, never as a speedup, unless every repeat stopped at the
  same round on both devices.
- Next to every time: rounds run, seconds per 100 rounds, DMatrix build time, the validation log
  loss and AUC of the kept trees, the threads, and the 1-minute load average before and after
  (a CPU time measured on a busy machine is an upper bound).
- Cold start: the first training call on a device in the process (20,000 rows, 10 rounds),
  device initialisation included, reported apart from the warm runs.
- Scoring: with the fixed-rounds model of the largest size, all its trees kept (the same model
  size on both devices; review G9): one batch over every fit row and 200 single-row calls
  (DMatrix build included, the cost an API call would see), p50 and p95. A CPU-over-GPU scoring
  ratio only between models with the same number of trees; the report shows the trees.
- A run that asked for the GPU and trained on the CPU stops B1 (`train.check_device`): amd_xgboost
  falls back with only a warning, and such a number is never reported as the MI300X.

### B2 in detail

- One function of `ml/gnn`, `ml.gnn.b2_timing`, which runs `python -m ml.gnn asos` (the US-202
  job) in a fresh process per device and data size and reads the metrics file it writes. The
  full-data runs are the US-202 runs (both protocols, with the MLP and XGBoost rows), written to
  `artifacts/eval/gnn/us-202-asos-<cpu|gpu>.json`, which `make gnn-results` reads: the droplet
  trains each device once for B2 and US-202 (reviews G2, G12).
- Speedup: CPU median epoch over MI300X median epoch (every epoch is the same work; the US-202
  definition) and CPU over MI300X inference, only between two records whose settings
  (`comparable`: data sample, seed, backend, aggregator, layers, epochs cap, ...) are identical.
  The training loops are reported but not divided: early stopping ends at another epoch on each
  device. The epochs of both devices are shown next to the speedup.
- F1 as US-202 states it, on the full data: protocol B on the decision-time population, with no
  published reference (ADR-10); protocol A on the full test set, next to the paper's GNN F1 0.792
  (paper protocol, never product performance: NFR-16). At the validation threshold, with F1 at
  0.5 next to it.
- Cold start: the job's own (torch and DGL import, device context, first kernels, in a fresh
  process), once per device and data size.
- Growing loads: seeded customer samples of 25% and 50% (GraphSAGE alone, no MLP or XGBoost
  control), written under `artifacts/bench/<run-id>/b2-sample-*`, so they never replace the
  US-202 files. `--b2-samples 1` runs the full data only.
- The model is the run's own configuration, read from its metrics (one heterogeneous SAGE layer of
  16 units, mean aggregator, dropout 0.2, Adam 0.01, MLP head of 64; `docs/graphsage.md`). The
  paper's max-pool exists (`--aggregator pool`) but is not what B2 measures by default.
- B2 runs in `trainer-graphsage` (rocm/dgl plus what ml.gnn imports), never in the bare rocm/dgl
  service. Where PyTorch is missing, `ml.gnn.b2_unavailable()` says so and B2 is skipped with
  that reason.

### B3 in detail

- Unit of work: one decision's draft, as the product makes it (one or two model calls). A
  decision whose model call failed (HTTP error, timeout) is counted as failed and kept out of the
  latencies: the template fallback is fast and would flatter them.
- One case list for every load (review G4): N decisions drawn once with the seed, stratified by
  decision type (Q1-Q4, insufficient evidence, guarantee): one case of every type present, the
  rest in the store's proportions, golden cases included and counted. N is the largest batch times its repeats (64 by default), at least
  twice the largest concurrency, rounded up to a multiple of every batch size. Every batch size
  and every concurrency level runs these N decisions: a batch of 1 and a batch of 32 time the
  same work, with the same number of latencies.
- Before the loads, one untimed pass sends every case of the list once, so that each load finds
  the same prefixes in vLLM's cache (a later load would otherwise reuse what an earlier one
  computed). The latencies are therefore those of known cases, the prefill mostly cached; the
  report says so.
- Batches: N/size batches sent at once (a barrier releases them together); latency p50 and p95,
  batch wall time, aggregate output tokens per second (ADR-03, the nightly pre-generation).
- Concurrency levels: a closed loop with C requests in flight until the N decisions are done;
  latency p50 and p95, decisions per second, aggregate output tokens per second and the median
  output tokens per second of one request (prefill included). Concurrency 1 is the batch of 1.
- A p95 is reported only from 20 latencies or more (`n/a (n < 20)` otherwise).
- Cold start (reviews G7, G15): `make bench-vllm` drops the page cache (root on the host;
  `BENCH_DROP_CACHES=0` keeps it), starts vLLM and writes the start and healthy times (15 s
  polling) and how the start was taken (`vllm-start-conditions.txt`); then `python -m bench
  first-request` times one guided-JSON completion of `contracts/examples/llm_case.json` before
  anything else uses vLLM (`first-request.json`). B3 reports these two as the cold start. The
  compile cache volume is kept and a droplet created from a snapshot reads a cold disk, so the
  start is labelled "on a running droplet, not a start from a snapshot". The first decision of B3
  itself comes after B4's batch generation: it is reported as a warm generation
  (`first_b3_request_ms`), never as a cold start.
- Output tokens are vLLM's `usage.completion_tokens`; the system prompt is the same for every
  case.
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
does, and keeps what it measured.

- Where each step ran (review G3): every step but `batch_generation` runs on the CPU, also on the
  droplet (pipeline/, ml.risk and ml.scoring take no device; XGBoost trains with the CPU `hist`
  method), so its time is a CPU number ("droplet CPU (<model>)"). Only `batch_generation` runs on
  the MI300X, through vLLM, and only with LLM_MODE=vllm. The pipeline total says how many of its
  seconds ran on the GPU, and `pipeline_seconds_gpu_steps` gives them apart.
- Nightly seconds (review G6), the cost's input: what a production night runs before the drafts,
  the main store's commands of `generate`, `graph`, `features`, `anomalies` and `scoring`. The
  OOD store (evaluation only), `risk` (offline training and evaluation against the generated
  truth) and the generation (the cost adds it at the B3 rate) are timed and listed, but left out.
- GPU utilisation of a step: the amd-smi samples inside its window (mean and max activity, max
  VRAM used by every process, vLLM's reservation included, mean socket power), MI300X readings
  whatever the step ran on. A step shorter than 5 s may have no sample, and then has no GPU
  number.

When the API's own batch (`apps/api/batch_generate`, phase 4) is merged, the last step can time
it instead: one line in `bench/b4_pipeline.py`.

## The four rules of spec 12

| Rule | What it asks | How the harness keeps it |
| --- | --- | --- |
| Comparable quality | Same data, version and configuration for CPU and GPU; no speedup from different workloads | B1 runs both devices in one process on the same matrices, subsets, parameters, seed and threads, with the same amd_xgboost build on the droplet, and computes speedups from fixed-rounds runs only, early stopping shown apart with its rounds; scoring compares models with the same trees. B2 divides median epochs of runs with identical settings. B3 runs every load on one case list. B4 labels each step with the device it ran on. A requested GPU run that ran on the CPU is refused |
| Separate times | Cold start, training, scoring and generation measured apart; p50 and p95 always with concurrency and throughput | Every measurement has a phase (`cold_start`, `training`, `scoring`, `generation`, `pipeline`, `cost`); the vLLM start and the first request are taken before anything else uses vLLM; B3 latencies carry their batch size or concurrency, next to the throughput of the same load |
| Full cost | The cost per decision includes idle time in the declared operating scenario; development experiments stay apart | `bench/cost.py`, versioned assumptions (`cost-v2`, each with its value and source), the droplet billed while it exists and the snapshot it is recreated from billed every day; the OOD store, the risk evaluation, spikes, benchmarks and failed runs are in no scenario |
| Growing loads | Several volumes and batch sizes, to see where the GPU adds value | B1 at four data sizes; B2 at three customer shares; B3 at batches 1, 8, 32 and concurrency 4, 16 (changeable with `BENCH_ARGS`) |

## Cost per decision (NFR-13)

Target: at most 0.01 EUR per decision with the LLM on the MI300X, idle time included.

Assumptions `cost-v2` (`bench/cost.py`; a change makes a new version; cost-v1 had no snapshot and
priced the whole B4 pipeline, reviews G5 and G6). Every one is in the B3 part's details next to
the result:

| Assumption | Value | Nature |
| --- | --- | --- |
| Droplet price | 1.99 USD per hour (spec 10 and 11) | declared price |
| Exchange rate | 0.86 EUR per USD | assumption: a round figure near the ECB EUR/USD reference rates of 2025-2026; replace it with the ECB rate of the day before quoting a cost |
| Billing | per second while the droplet exists, powered on or off | assumption |
| Droplet boot from the snapshot to compose up | 600 s | assumption, unmeasured |
| Idle margin between and after the jobs | 600 s | assumption |
| vLLM start to healthy | measured by `make bench-vllm` on a running droplet (a lower bound for a start from a snapshot); 600 s if not measured | measured or assumed, said in the breakdown |
| Snapshot size | the GB used on the droplet's root disk when B3 runs (`disk-used-gb.txt`, an upper bound of the billed size); 220 GB if not measured (the upper end of the runbook's 170-220 GB) | measured or assumed, said in the breakdown |
| Snapshot price | 0.06 USD per GB-month (DigitalOcean's snapshot price; unconfirmed for AMD Developer Cloud GPU droplets), over 365/12 days a month | declared price, to check in the control panel |
| Decisions per day | return requests per day in the 28 days before the scoring instant of the store B3 read | synthetic (Navigli Fashion: about 150 a day at scale 1.0) |
| Seconds per decision | median wall time of a batch of 32 decisions sent at once over 32 | measured (B3) |
| Nightly pipeline | B4's nightly seconds (main store generate as the ingest stand-in, graph, features, anomalies, scoring) | measured (B4) |

Scenarios:

- `nightly-batch` (the declared operating scenario): every night the droplet is created from its
  snapshot, starts vLLM, runs the nightly pipeline (B4's nightly seconds) and generates the day's
  drafts at the measured batch-32 rate (B3), then is destroyed. Cost per day = (boot + vLLM start
  + pipeline + decisions x seconds per decision + idle margin) x the hourly price + the daily share
  of the snapshot.
- `business-hours`: the droplet on 12 hours a day for live generation, busy or idle; a
  powered-off droplet is still billed, so it is recreated from the snapshot every day too.
- `always-on`: 24 hours a day; no snapshot.

Each scenario gives EUR per decision, EUR per 1,000 decisions, EUR per day, whether it meets the
target, and the break-even volume (the smallest number of decisions per day that meets it). The
cost is a computation under assumptions: its nature is simulated, even when its inputs are
measured. `nightly-batch` needs a live B3 and a B4 of the same run; on the CPU run it says so
instead of guessing. At about 150 decisions a day the snapshot alone is a noticeable share of the
target (220 GB x 0.06 USD over a month is about 0.43 USD a day, 0.0025 EUR a decision).

## Outputs

`python -m bench run` writes one part per benchmark to `artifacts/bench/<run-id>/<B>.json`
(status `measured`, `dry_run`, `skipped` with the reason, or `failed` with the error; a failed
part keeps what it measured and does not stop the next one), plus raw records and logs. `python -m
bench merge` assembles `bench/results/<run-id>.json`:

| Member | Content |
| --- | --- |
| `run_id`, `device`, `created_at`, `git` | `<UTC time>-<cpu|gpu>`, the commit and whether the tree had changes, untracked files included (review G8) |
| `rules` | the four rules and how they are applied |
| `hardware` | per part: CPU model, logical CPUs, RAM, kernel, container flag; GPU name, architecture and VRAM from `amd-smi static --json` on the droplet |
| `software` | per part: Python, ROCm (`/opt/rocm/.info/version`), xgboost version and HIP build, numpy, polars, scikit-learn, torch and DGL (B2), vLLM version and served model (`/version`, `/v1/models`), the image references with digest (`VLLM_IMAGE`, `DGL_IMAGE`, `ROCM_BASE_IMAGE`), the model revision (`GEMMA_REVISION`), and a content hash of each code folder the part could see (`code_bench`, `code_ml`, `code_pipeline`, ...: containers have no `.git`, and a dirty tree is not a version) |
| `benchmarks` | every part: title, dataset, configuration, notes, measurements and details (per-run tables, speedups, the B3 case list, cost breakdown and assumptions, step logs, the amd-smi timeline) |
| `measurements` | every number: metric, label, value, unit, device, phase, provenance (origin, period, nature), data nature, protocol, concurrency, batch size, rows, cold or warm, repeats |
| `api` | the projection served by GET /api/benchmarks (below) |

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
- speedups are in the part's details and the report, not in the results: `ratio` is bounded to
  [0, 1];
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

```python
ml.gnn.b2_timing(raw_dir: Path, *, protocol: "A" | "B", device: "cpu" | "cuda", seed: int,
                 sample: float = 1.0, out: Path = Path("artifacts"), controls: bool = True,
                 extra_args: Sequence[str] = ()) -> Mapping[str, object]  # GnnTiming
ml.gnn.b2_unavailable() -> str | None  # why it cannot run here (no PyTorch), or None
```

The record (`bench.b2_gnn.GnnTiming`, projected by `ml.gnn.b2.timing_from_metrics` from the
US-202 metrics file): `protocol`, `device_requested`, `device_effective`, `model` (from the run's
configuration), `comparable` (the settings both devices must share), `sample`, `nodes`, `edges`
(training events), `epochs`, `cold_start_seconds` (the job's), `epoch_seconds` (every epoch),
`epoch_seconds_median` (the speedup basis), `train_seconds`, `inference_seconds`, `f1` and
`f1_at_half` on `population` (`decision_time` for protocol B, `all` for protocol A),
`threshold`, `population_events`, `data_origin`, `versions` (torch, HIP, DGL, backend),
`hardware`, and optionally `peak_memory_gib`, `graph_build_seconds`, `precision`, `recall`,
`best_epoch`, `measured_at` and `comparisons` (the MLP and XGBoost F1 on the same events).
`bench.b2_gnn.check_timing` refuses a record off this shape, a population other than the one
US-202 states, and a GPU run whose effective device is the CPU.

## How to run

Here (CPU, no GPU):

```bash
make bench BENCH_RAW=data/raw BENCH_DATA=<a data root with store/, features/ and scores/>
# optional: BENCH_ARGS="--b1-repeats 1 --b1-fractions 0.1,0.5,1 --b2-samples 1"
# writes docs/results/benchmarks-cpu.md; B2 trains only where PyTorch is installed
```

On the droplet, one ordered list with what each command writes: `infra/spikes/README.md` ("The
GPU session"). `make bench DEVICE=gpu` runs its benchmark steps in order; `make -n bench
DEVICE=gpu` prints every command.

## Limitations

- CPU numbers of a development machine are measured on shared vCPUs under the load average
  recorded next to each run; they are not the droplet's CPU baseline, which B1 and B2 measure on
  the droplet itself with vLLM down. The committed CPU run (20261014T002158Z-cpu) was measured
  under a load of about 11 to 14 on 4 vCPU, from a tree with uncommitted changes, before the code
  hashes existed: it shows that the harness works, not CPU performance, and is not served.
- B4's `generate` step builds the synthetic store from scratch: a stand-in for the nightly ingest
  of a real shop, whose duration would differ.
- B3 inputs have no customer free text (the store has none), so the adversarial cases of the LLM
  evaluation set are not in B3; their latency is the same path. B3's latencies are those of cases
  already in vLLM's prefix cache (one untimed pass first), a lower bound for a night of new cases.
- amd-smi activity is an instantaneous busy share sampled every 5 s: bursts shorter than the
  interval can be missed.
- vLLM start to healthy is measured on a running droplet with the compile cache kept (and the page
  cache dropped when the host allows it): a start from a snapshot reads a cold disk and is slower.
- The snapshot size is the disk used on the droplet, an upper bound; its price is DigitalOcean's,
  unconfirmed for AMD Developer Cloud GPU droplets.
