# data/

Everything here is generated or downloaded and is not committed.

- `raw/asos/` — the six ASOS GraphReturns pickles (CC BY 4.0), checked against the sha256
  values pinned in `pipeline/ingest/asos_download.py` (ADR-10). `MANIFEST.json` is a log only.
- `raw/asos_{events,customers,variants}.parquet` — the same data under the raw contracts of
  ADR-13, written by `python -m pipeline.ingest` with provenance (nature: real).
- `raw/asos_profile.txt` — the plain-text profile of the raw tables (US-101).
- `store/` — synthetic store Navigli Fashion: `python -m pipeline.generator --calibration <json>
  --out data --seed 42` (docs/synthetic-store.md).
- `truth/` — ground truth of the generator. Running services (api, ui, demo) never read it; only
  offline training and evaluation jobs do (CLAUDE.md rule 12).
- `ood/store/`, `ood/truth/` — out-of-distribution scenario (`--scenario ood`, US-105). Never used in
  training: the evaluation reads it and reports it on a separate row.

The generator calibration made from the ASOS train split holds aggregates only, so it is
committed outside this folder: `pipeline/calibration/asos_calibration.json`.
