"""Return probability per purchase, learnt on ASOS GraphReturns (US-201, spec 8.1, ADR-10).

Two protocols on the same splits: A, the paper's features (benchmarks and the comparison with the
paper only, never product performance), and B, decision-time leave-one-out counts and static
product attributes (the model the product uses). Modules:

- features: joins, leave-one-out counts, populations, feature lists and matrices;
- train: train_booster(device="cpu"|"cuda") with the paper's hyperparameters (benchmark B1);
- calibration: isotonic calibration stored as JSON thresholds;
- baseline: the best single-feature leave-one-out rule (the ADR-10 go/no-go);
- metrics: F1, precision, recall, cross-entropy, ECE;
- run and report: the CLI `python -m ml.propensity --raw data/raw --out artifacts`.
"""
