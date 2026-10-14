# docs/

- `spec.md` — the full specification, exported as Markdown from the project document. It is the source of
  truth: when a decision changes, update it first.
- `adr/` — architecture decisions.
- `ui-design.md` — the UI design for phase 5 (`apps/ui`): screens, shared components, tokens, data
  layer, demo path, and the ownership split between the four view areas.
- `spike-results.md` — results of the GPU spikes SPK-G1..G7 (written on the droplet).
- `results/` — measured results, each file rewritten from the data by the command named at its top
  (never by hand): `us-201-propensity.md` by `python -m ml.propensity --raw data/raw --out
  artifacts`, `customer-quantities.md` by `python -m ml.reference.report`, `account-graph.md` by
  `python -m pipeline.graph.evaluate --data data`, `us-208-product-anomalies.md` by
  `python -m pipeline.anomalies.evaluate --data data`, `us-204-risk.md` by `python -m ml.risk
  --data data --out artifacts --scores data`, `us-202-graphsage.md` by `python -m ml.gnn report`
  (`make gnn-results`, from the metrics of `make gnn-asos` and `make embeddings`, which need
  PyTorch), `benchmarks-cpu.md` and `benchmarks-mi300x.md` by `python -m bench report` from a run
  of `make bench` (US-902). `make pipeline` then `make results` rewrite the store-based files;
  US-201 needs `make asos` and `make propensity`, and the GraphSAGE and benchmark files their runs.
- `graphsage.md` — GraphSAGE (US-202, A4): the ASOS benchmark under both protocols, the customer
  embeddings by link prediction, the same model on DGL and on plain PyTorch, the CI job with
  PyTorch and the commands on the MI300X.
- `benchmarks.md` — benchmarks B1-B4 (US-902): what each measures, the four rules of spec 12, the
  cost per decision and its versioned assumptions, the run file served by GET /api/benchmarks.
- Deployment (ADR-17): the compose profiles, the proxy, the reset and the cached demo package are
  described in the README ("Quick start", "The cached demo", "Architecture") and, for the
  droplet, in `infra/spikes/README.md` ("The demo export").
- `synthetic-store.md` — the Navigli Fashion generator (US-102..US-105): calibration, behaviours,
  golden cases, out-of-distribution scenario, measured numbers.
- `customer-features.md` — decision-time customer features, the product/cohort reference model, the
  return residual, customer value and evidence quality (US-205, US-206, US-209).
- `account-graph.md` — the account graph and clusters (US-203, US-310 support): links, hub pruning,
  clusters, features, disputes, evaluation definitions.
- `product-anomalies.md` — product anomalies per variant, reason and sales week, the catalogue task
  and the follow-up after a change (US-208, US-606 data side).
- `risk-model.md` — the abuse-risk model per customer and per return request (US-204): feature sets
  and ablation, split, calibration and thresholds, explanations, batch scoring, FAIR-02 and FAIR-03.
- `review-backlog.md` — open findings of the last review round, the starting list of the next
  session (including the UI migration to API contract 1.5.0).
