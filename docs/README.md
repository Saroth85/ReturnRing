# docs/

- `spec.md` — the full specification, exported as Markdown from the project document. It is the source of
  truth: when a decision changes, update it first.
- `adr/` — architecture decisions.
- `spike-results.md` — results of the GPU spikes SPK-G1..G7 (written on the droplet).
- `synthetic-store.md` — the Navigli Fashion generator (US-102..US-105): calibration, behaviours,
  golden cases, out-of-distribution scenario, measured numbers.
- `account-graph.md` — the account graph and clusters (US-203, US-310 support): links, hub pruning,
  clusters, features, disputes, evaluation definitions.
- `results/` — measured results, each file rewritten by one command from the data (never by hand):
  `results/account-graph.md` by `python -m pipeline.graph.evaluate --data data`.
