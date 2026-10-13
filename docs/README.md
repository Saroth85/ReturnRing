# docs/

- `spec.md` — the full specification, exported as Markdown from the project document. It is the source of
  truth: when a decision changes, update it first.
- `adr/` — architecture decisions.
- `spike-results.md` — results of the GPU spikes SPK-G1..G7 (written on the droplet).
- `results/` — measured results, each file rewritten from the data by the command named at its top
  (never by hand): `us-201-propensity.md` by `python -m ml.propensity --raw data/raw --out
  artifacts`, `customer-quantities.md` by `python -m ml.reference.report`, `account-graph.md` by
  `python -m pipeline.graph.evaluate --data data`.
- `synthetic-store.md` — the Navigli Fashion generator (US-102..US-105): calibration, behaviours,
  golden cases, out-of-distribution scenario, measured numbers.
- `customer-features.md` — decision-time customer features, the product/cohort reference model, the
  return residual, customer value and evidence quality (US-205, US-206, US-209).
- `account-graph.md` — the account graph and clusters (US-203, US-310 support): links, hub pruning,
  clusters, features, disputes, evaluation definitions.
