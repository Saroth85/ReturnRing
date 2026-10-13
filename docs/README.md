# docs/

- `spec.md` — the full specification, exported as Markdown from the project document. It is the source of
  truth: when a decision changes, update it first.
- `adr/` — architecture decisions.
- `spike-results.md` — results of the GPU spikes SPK-G1..G7 (written on the droplet).
- `results/` — measured model results; each file is rewritten by the command named at its top
  (`us-201-propensity.md`: `python -m ml.propensity --raw data/raw --out artifacts`).
- `synthetic-store.md` — the Navigli Fashion generator (US-102..US-105): calibration, behaviours,
  golden cases, out-of-distribution scenario, measured numbers.
- `customer-features.md` — decision-time customer features, the product/cohort reference model, the
  return residual, customer value and evidence quality (US-205, US-206, US-209).
- `results/` — measured results, each file rewritten from the data by the command named at its top
  (`results/customer-quantities.md`: `python -m ml.reference.report`).
