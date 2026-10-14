# GraphSAGE: the ASOS benchmark (US-202, B2) and the A4 customer embeddings

Code in `ml/gnn/`; results in `docs/results/us-202-graphsage.md`, rewritten by
`make gnn-results` from the metrics files (never by hand); contract in ADR-13 amendment 6.

## Two jobs

| Job | Command | Data | What it measures |
| --- | --- | --- | --- |
| ASOS benchmark | `python -m ml.gnn asos --raw data/raw --out artifacts` | ASOS GraphReturns, real | GraphSAGE on the customer-variant purchase graph, protocols B then A of ADR-10, side by side with XGBoost (the US-201 code path) and the paper. A benchmark of the ASOS task, never evidence about rings (spec US-202) |
| A4 embeddings | `python -m ml.gnn embed --store data --out data --models artifacts` | the synthetic store, no truth | GraphSAGE trained by link prediction on the customer-identifier graph; `features/customer_embeddings`, read by `python -m ml.risk` for the A4 row of the ablation |

Both run on the CPU and on the MI300X with the same code: `--device cpu|gpu` (default from
`DEVICE`), `--backend auto|dgl|torch` (default: DGL when importable). On ROCm the GPU is
`torch.device("cuda")`; a GPU run without a visible GPU exits 2, it never falls back to the CPU.

## The model

- **Layers** (`ml/gnn/model.py`): with DGL, `dgl.nn.SAGEConv` per relation in
  `dgl.nn.HeteroGraphConv` (sum over relations); without DGL, `TorchSAGEConv` and
  `TorchHeteroConv`, written line by line after DGL 2.4: same submodules and parameter names, same
  initialisation order (the same seed gives the same weights), same mean and max-pool maths, zeros
  for a node without neighbours. A state dict moves from one backend to the other;
  `tests/test_gnn_model.py` compares the outputs where DGL is installed (CPU, and GPU with the gpu
  marker). The PyTorch mean is a sparse product with the row-normalised adjacency and its
  precomputed transpose (no atomic adds).
- **ASOS**: one SAGE layer of 16 units, aggregator mean (`--aggregator pool` for the paper's
  max-pool), dropout 0.2, Adam 0.01, the paper's 2-layer MLP head (64 units) on the two node
  representations and the event's own features; mini-batches of 131,072 events, the node
  representations recomputed on the whole graph at each step; early stopping after 5 epochs
  without a better validation loss (at most 60); isotonic calibration and the F1 threshold on the
  validation split, as for XGBoost.
- **A4**: two SAGE layers of 16 units, mean, dropout 0.2, Adam 0.01, full-graph steps. Each
  epoch hides 20% of the (account, identifier value) uses and scores them, by the dot product of
  the two embeddings, against a random value of the same kind; a fixed 10% of the uses is held out
  for early stopping (patience 20, at most 300 epochs).

## Protocol B: no label travels along the graph

A node's return count holds the label of every edge of that node; with one layer, a customer's
own count would come back to it through any neighbour. So under protocol B the nodes carry
label-free features only (leave-one-out sales, which are the same for every event of the node,
degree, row flags, and for variants price, discount, type and brand), and the leave-one-out return
counts of the event's customer and variant enter the edge head, per event: the 30 protocol B
features of `ml.propensity`. `tests/test_gnn_data.py` flips every label and checks that the node
inputs do not change. Training, calibration and evaluation use the decision-time population of
ADR-10 amendment 1, the US-201 splits and the US-201 validation split; the XGBoost row is
`ml.propensity`'s own model on the same rows (`tests/test_gnn_jobs.py` checks it equals US-201's).

Protocol A (the paper's, benchmarks only) puts the paper's node features on the nodes, return
rates and reason shares included, and nothing on the edge. Neither protocol uses gender, country
or age (rule 6): the paper's country virtual nodes are not built.

## Times (spec 12, 'Tempi distinti')

Every run records, apart: the cold start (torch and DGL import, device context, first kernels),
the data loading (reading `raw/asos_*` through the contracts; per protocol the join, graph,
features and copy to the device), the training (per model: seconds, epochs, median epoch) and the
inference on the test split, the GPU synchronised at the edges of each phase, with the machine's
load average, versions and hardware. A CPU-to-GPU speedup is reported only for two runs with the
same data, configuration and seed.

## Tests and the CI job with PyTorch

The main CI installs no PyTorch: `tests/test_gnn_data.py` (graphs, features, contract, the
EmbeddingSource, the report, rule 12) and the A4 hook test in `tests/test_risk_generated.py`
(noise embeddings) run there. `tests/test_gnn_model.py` and `tests/test_gnn_jobs.py` carry the
`torch` marker and skip without PyTorch. A job to add to `.github/workflows/ci.yml` (left to the
platform session, which owns `.github/`):

```yaml
  gnn-cpu:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements/dev.txt -r infra/requirements-gnn-cpu.txt
      - run: python -m pytest -m torch -q
      - run: mypy  # ml/gnn type-checks with the real torch types too
```

`infra/requirements-gnn-cpu.txt` pins the CPU build of PyTorch 2.8.0, the version of the rocm/dgl
image. PyTorch (BSD-3-Clause) and its dependencies are permissive (rule 15); it is not a runtime
dependency of the product.

## On the MI300X droplet

The `trainer-graphsage` service (`infra/docker/trainer-gnn.Dockerfile`) is the trainer-gnn base
(`rocm/dgl`, pinned in `.env.example`) plus this code and the pinned packages of
`infra/docker/requirements-gnn.txt`; the build imports every module of the jobs, so a broken
image fails the build, not the session. It mounts `data/raw`, `data/store`, `data/features` and
`data/ood` read-only (never `data/truth`) and writes `artifacts/`. After `make spike-prep` (or
with the DGL image already pulled), from the repository root:

```bash
make gnn-image                 # build trainer-graphsage on top of the pulled rocm/dgl
make gnn-test-gpu              # DGL against the PyTorch layers on the GPU, and the job tests
make gnn-asos-gpu              # B2 on the MI300X: artifacts/eval/gnn/us-202-asos-gpu.json
make gnn-asos-cpu-container    # the same in the same image on the droplet's CPU (alone on the
                               # CPU: before vLLM starts or after it is stopped)
make embeddings-gpu            # A4 encoder and embeddings on the GPU, copied into data/
make risk gnn-results          # host: the A4 row (docs/results/us-204-risk.md) and this report
```

Each GPU target runs `docker compose ... run --rm -T trainer-graphsage python -m ml.gnn ...`
(`make -n <target>` prints the exact commands). Expected: an environment line with `torch_hip` set, the dgl
backend and `gpu_arch` gfx942 in the metrics; a `GpuUnavailable` exit 2 means the GPU is not
visible in the container (`make spike-gpu-check`). The spec 11 memory budget for the ASOS graph
is under 8 GB, unmeasured so far; each run records its peak (`peak_allocated_gb`).
`GNN_SAMPLE=0.25` runs a seeded customer sample, reported as such; the B2 numbers come from
full-data runs on both devices.

On a shared CPU, `--threads 1` can be faster than every core. On this project's 4-vCPU
development machine under a load average of 15 to 25 (other jobs), one training step on the ASOS
graph took about 4.5 s with one thread and 7.5 s with four, and an epoch of the A4 encoder about
0.5 s against 18 s. Each run records its thread count and the load average next to its timings.
