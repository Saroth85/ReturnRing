# GraphSAGE: the ASOS benchmark (US-202, B2) and the A4 customer embeddings

Code in `ml/gnn/`; results in `docs/results/us-202-graphsage.md`, rewritten by
`make gnn-results` from the metrics files (never by hand); contract in ADR-13 amendment 6.

## Two jobs

| Job | Command | Data | What it measures |
| --- | --- | --- | --- |
| ASOS benchmark | `python -m ml.gnn asos --raw data/raw --out artifacts` | ASOS GraphReturns, real | GraphSAGE on the customer-variant purchase graph, protocols B then A of ADR-10, side by side with XGBoost (the US-201 code path) and the paper. A benchmark of the ASOS task, never evidence about rings (spec US-202) |
| A4 embeddings | `python -m ml.gnn embed --store data --out data --models artifacts` | the synthetic store, no truth | GraphSAGE trained by link prediction on the customer-identifier graph; `features/customer_embeddings`, read by `python -m ml.risk` for the A4 row of the ablation |

Both jobs ran on the CPU with the torch backend (the runs of `docs/results/us-202-graphsage.md`).
`embed` also ran once on an AMD Instinct MI300X VF, on 16 October 2026, with the torch backend on
the synthetic store (below); the ASOS job never ran on a GPU. The same code takes
`--device cpu|gpu` (default from `DEVICE`) and `--backend auto|dgl|torch` (default: DGL when
importable; DGL is optional and no recorded run used it). On ROCm the GPU is
`torch.device("cuda")`; a GPU run without a visible GPU exits 2, it never falls back to the CPU.

## The model

- **Layers** (`ml/gnn/model.py`): with DGL, `dgl.nn.SAGEConv` per relation in
  `dgl.nn.HeteroGraphConv` (sum over relations); without DGL, `TorchSAGEConv` and
  `TorchHeteroConv`, written line by line after DGL 2.4: same submodules and parameter names, same
  initialisation order (the same seed gives the same weights), same mean and max-pool maths, zeros
  for a node without neighbours. A state dict moves from one backend to the other;
  `tests/test_gnn_model.py` compares the outputs where DGL is installed (CPU, and GPU with the gpu
  marker) and skips that comparison without DGL, as in CI, which installs none
  (`tests/test_gnn_model.py:98-109`). The PyTorch mean is a sparse product with the
  row-normalised adjacency and its precomputed transpose (no atomic adds).
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
`torch` marker and skip without PyTorch. The `gnn-cpu` job of `.github/workflows/ci.yml` runs
them:

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

`infra/requirements-gnn-cpu.txt` pins the CPU build of PyTorch 2.8.0, the version of
torch 2.8.0+rocm6.4 of the MI300X session. PyTorch (BSD-3-Clause) and its dependencies are
permissive (rule 15); it is not a runtime dependency of the product. The tests marked `gpu` need
a ROCm PyTorch with a visible GPU (`make test-gpu` on a ROCm host); no run of them is recorded.

## On the AMD GPU (16 October 2026)

The A4 encoder was trained once on an AMD Instinct MI300X VF, in the AMD One-Click Tutorials
notebook (one MI300X VF exposed to PyTorch; Ubuntu 22.04.5, ROCm 6.4.3, Python 3.12.11,
torch 2.8.0+rocm6.4 from the PyTorch ROCm 6.4 index), on the synthetic store generated with
seed 42 and scale 1.0. The command, after the store, graph and features steps of the session
report:

```bash
python -m ml.gnn embed --store data/gpu-synthetic \
  --out artifacts/gpu-session/graphsage --models artifacts/gpu-session \
  --device gpu --backend torch --seed 42
```

Results (data nature synthetic; timings measured on that GPU, one run): 300 epochs (best epoch
300), self-supervised validation AUC 0.948373 (a held-out use against a random value of the same
kind, not return prediction or abuse detection), 77,703 embedding rows over four snapshot
instants (2026-07-01 to 2026-10-01). The environment recorded the torch backend, `gpu_arch`
gfx942 and `torch_hip` 6.4.43482; the epoch loop took 8.414 s (median epoch 0.0207 s, peak
allocated 0.186 GB). These are observations of one run, not a CPU against GPU comparison. Evidence:
the [AMD session report](amd-training-session-2026-10-16.md) and its
[results JSON](results/amd-session-2026-10-16.json); the encoder and the embeddings stay in the
session archive, not in the repository, and no A4 metric was recorded from them.

Not run on a GPU: the ASOS job (the ASOS files could not be downloaded, OSF HTTP 503), B2
(retired, ADR-18; `docs/benchmarks.md`), the DGL backend, any container image, and the
gpu-marked tests.

The GPU plan of 12 Oct 2026 budgeted the ASOS graph under 8 GB of MI300X memory (spec section 11
before the cleanup: `git show 119b4b7:docs/spec.md`); it was never measured on a GPU. Each run
records its peak (`peak_allocated_gb`). Every timing carries its machine (CPU model, threads, load
average before and after), the UTC day it was measured and its nature (real, rule 11).
`GNN_SAMPLE=0.25 make gnn-asos` runs a seeded customer sample, reported as such.

On a shared CPU, `--threads 1` can be faster than every core. On this project's 4-vCPU
development machine under a load average of 15 to 25 (other jobs), one training step on the ASOS
graph took about 4.5 s with one thread and 7.5 s with four, and an epoch of the A4 encoder about
0.5 s against 18 s. Each run records its thread count and the load average next to its timings.
