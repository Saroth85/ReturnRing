# data/

Everything in this folder is generated or downloaded and is not committed.

## Main folders

- `raw/asos/` — the six ASOS GraphReturns pickles, checked against pinned SHA-256 values
- `raw/asos_{events,customers,variants}.parquet` — raw contracts produced by `python -m pipeline.ingest`
- `store/` — the synthetic Navigli Fashion store generated from the ASOS calibration
- `truth/` — generator ground truth; running services never read it
- `ood/store/` and `truth/ood/truth/` — out-of-distribution scenario and its ground truth
- `features/` and `ood/features/` — decision-time features, graph links/clusters and anomalies
- `scores/` — batch scores and request scores

## Typical workflow

1. `python -m pipeline.ingest` writes raw ASOS contracts.
2. `python -m pipeline.generator --calibration <json> --out data --seed 42` builds the synthetic store.
3. `python -m pipeline.graph`, `python -m pipeline.features`, `python -m pipeline.anomalies`
   and `python -m pipeline.serving` build the downstream tables and serving snapshot.
4. `python -m ml.risk` and `python -m ml.scoring` create model artifacts and scores.

`make pipeline` runs the whole chain on the host; `make results` rewrites the offline reports in
`docs/results/`.

## Important rule

Running services (`api`, `ui`, `demo`) never read `data/truth/`. Only offline training and
evaluation jobs do.
