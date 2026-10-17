# data/

Everything here is generated or downloaded and is not committed.

- `raw/asos/` — the six ASOS GraphReturns pickles (CC BY 4.0), checked against the sha256
  values pinned in `pipeline/ingest/asos_download.py` (ADR-10). `MANIFEST.json` is a log only.
- `raw/asos_{events,customers,variants}.parquet` — the same data under the raw contracts of
  ADR-13, written by `python -m pipeline.ingest` with provenance (nature: real).
- `raw/asos_profile.txt` — the plain-text profile of the raw tables (US-101).
- `store/` — synthetic store Navigli Fashion: `python -m pipeline.generator --calibration <json>
  --out data --seed 42` or `make generate` (docs/synthetic-store.md).
- `truth/` — ground truth of the generator. Running services (api, ui, demo) never read it; only
  offline training and evaluation jobs do (CLAUDE.md rule 12).
- `ood/store/` and `truth/ood/truth/` — out-of-distribution scenario (`--scenario ood`, US-105): its
  store, and its ground truth inside `truth/` with every other ground truth. Never used in training:
  the evaluation reads it and reports it on a separate row.

- `features/` and `ood/features/` — as-of tables of phase 2 (ADR-13): `links` and `clusters`
  (`make graph`, US-203), `customer_features`, `customer_value` and `evidence_quality`
  (`make features`, US-205/206/209), `product_anomalies` (`make anomalies`, US-208). Each file's
  lineage (`returnring.lineage`) ends with the origin of the store it was computed from; a job
  refuses a table built from another store.
- `scores/` — `scores` and `request_scores` of the batch scoring (`make risk`, or `make score`
  with the saved models), with the thresholds of every quadrant (US-204).

`make pipeline` writes the generated tables in dependency order:
`generate generate-ood graph features anomalies risk serving`; `make asos` writes `raw/` (US-101).
When running individual jobs, use their required store and output arguments, then export serving
only after risk has produced the models and scores:

```bash
python -m pipeline.graph --store data
python -m pipeline.graph --store data/ood
python -m pipeline.features --store data --out data
python -m pipeline.features --store data/ood --out data/ood --source data
python -m pipeline.anomalies --store data --out data
python -m pipeline.anomalies --store data/ood --out data/ood
python -m ml.risk --data data --out artifacts --scores data --results docs/results/us-204-risk.md
python -m pipeline.serving --data data --artifacts artifacts --out serving
```

The models go to `artifacts/risk/models`; offline evaluation outputs (metrics, graph evaluation,
fairness) go to `artifacts/eval/`, which holds counts and metrics, never a customer listed with its
ground truth. `make results` rewrites the offline reports in `docs/results/`.

The generator calibration made from the ASOS train split holds aggregates only, so it is
committed outside this folder: `pipeline/calibration/asos_calibration.json`.
