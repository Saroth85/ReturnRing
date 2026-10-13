# data/

Everything here is generated or downloaded and is not committed.

- `raw/asos/` — ASOS GraphReturns, downloaded by `make asos` (CC BY 4.0).
- `store/` — synthetic store Navigli Fashion: `python -m pipeline.generator --calibration <json>
  --out data --seed 42` (docs/synthetic-store.md).
- `truth/` — ground truth of the generator. Running services must never read it (CLAUDE.md rule 12);
  only offline training and evaluation jobs do.
- `ood/store/`, `ood/truth/` — out-of-distribution scenario (`--scenario ood`, US-105). Never used in
  training: the evaluation reads it and reports it on a separate row.
