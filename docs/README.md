# docs/

This folder is the documentation hub for ReturnRing.

## Read first

- `spec.md` — source of truth for behavior and user stories
- `adr/` — architecture decisions and rationale
- `ui-design.md` — UI layout, screens and ownership split

## Project and product docs

- `synthetic-store.md` — synthetic store generator, calibration and golden cases
- `customer-features.md` — decision-time customer features and residual model
- `account-graph.md` — linked accounts, clusters and disputes
- `product-anomalies.md` — anomaly detection and catalogue task flow
- `risk-model.md` — abuse-risk model, ablations and fairness checks
- `graphsage.md` — GraphSAGE benchmark and embeddings
- `benchmarks.md` — B1–B4 benchmark definitions and results

## Results

`docs/results/` contains measured reports rewritten from data, not by hand. Examples:

- `us-201-propensity.md`
- `us-202-graphsage.md`
- `us-204-risk.md`
- `us-208-product-anomalies.md`
- `account-graph.md`
- `customer-quantities.md`
- `benchmarks-cpu.md`
- `benchmarks-mi300x.md`

## Release and operations

- `release-guide.md` — submission release checklist
- `amd-runbook.md` — AMD Developer Cloud runbook
- `amd-host-runbook.md` — no-Docker AMD host runbook
- `railway-demo.md` — public CPU demo packaging
- `tokenfactory-deploy.md` — AMD Token Factory deployment guide
- `amd-training-session-2026-10-16.md` — training/integration recap

## Review and backlog

- `review-backlog.md` — open findings
- `spike-results.md` — GPU spike results
- `main-review-2026-10-17.md` — current review note

## Suggested reading order

1. `spec.md`
2. `adr/README.md`
3. `ui-design.md`
4. the relevant results or runbook for the work you are doing
