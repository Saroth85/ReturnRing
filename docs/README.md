# docs/

The documents of ReturnRing as of 17 October 2026. The front door is the
[README](../README.md); this page says what each document is for.

## Read first

- [spec.md](spec.md): the full specification, in Italian. It is the source of truth: when a
  decision changes, it is updated first.
- [adr/README.md](adr/README.md): the architecture decisions.
  [ADR-18](adr/ADR-18-amd-api-and-training.md) is the current delivery scope: Gemma through AMD
  Token Factory, model training on an AMD GPU, no CPU against GPU benchmark.
- [ui-design.md](ui-design.md): the UI of `apps/ui`: screens, shared components, tokens, data
  layer, demo path and the view areas.

## Method

- [synthetic-store.md](synthetic-store.md): the Navigli Fashion generator (US-102..US-105):
  calibration, behaviours, golden cases, out-of-distribution scenario, measured numbers.
- [customer-features.md](customer-features.md): decision-time customer features, the
  product/cohort reference model, the return residual, customer value and evidence quality
  (US-205, US-206, US-209).
- [account-graph.md](account-graph.md): the account graph and clusters (US-203, US-310 support):
  links, hub pruning, clusters, features, disputes, evaluation definitions.
- [product-anomalies.md](product-anomalies.md): product anomalies per variant, reason and sales
  week, the catalogue task and the follow-up after a change (US-208, US-606 data side).
- [risk-model.md](risk-model.md): the abuse-risk model per customer and per return request
  (US-204): feature sets and ablation, split, calibration and thresholds, explanations, batch
  scoring, FAIR-02 and FAIR-03.
- [graphsage.md](graphsage.md): GraphSAGE (US-202, A4): the ASOS benchmark under both protocols
  on the CPU, the customer embeddings by link prediction, and the embedding run on an AMD
  Instinct MI300X VF on 16 October 2026.
- [benchmarks.md](benchmarks.md): benchmarks B1-B4 (US-902): what each one measures. The CPU
  against MI300X comparison was retired by ADR-18 on 16 October 2026 and never ran on a GPU; only
  the CPU harness check of 14 October 2026 ran.

## Delivery

- [railway-demo.md](railway-demo.md): the public demo on Railway (one CPU service, read-only,
  Gemma through AMD Token Factory): the running revision, its variables, the archive, the
  deployment and its checks.
- [amd-training-session-2026-10-16.md](amd-training-session-2026-10-16.md): the AMD session of
  16 October 2026: the Token Factory integration check, and XGBoost and GraphSAGE trained on an
  AMD Instinct MI300X VF on the synthetic store.
  [results/amd-session-2026-10-16.json](results/amd-session-2026-10-16.json) is its evidence,
  assembled by hand from the session outputs: it is not a generated file.

## Review

- [review-backlog.md](review-backlog.md): the open findings, accepted as limits of the
  submission.

## Results

Each generated file of `results/` is rewritten from the data by the command named at its top,
never by hand, regressions included (rule 13):

| File | Rewritten by |
| --- | --- |
| [us-201-propensity.md](results/us-201-propensity.md) | `make propensity` (needs the ASOS tables of `make asos`) |
| [us-202-graphsage.md](results/us-202-graphsage.md) | `make gnn-results`, from the metrics of `make gnn-asos` and `make embeddings` (both need PyTorch) |
| [us-204-risk.md](results/us-204-risk.md) | `make risk` |
| [account-graph.md](results/account-graph.md), [customer-quantities.md](results/customer-quantities.md), [us-208-product-anomalies.md](results/us-208-product-anomalies.md) | `make results` |
| [benchmarks-cpu.md](results/benchmarks-cpu.md) | `python -m bench report` from the committed run `bench/results/20261014T002158Z-cpu.json`; `make bench` makes a new CPU run and its report |

`make pipeline` then `make results` rewrite the files based on the store.

Some generated files still carry wording of the plan before ADR-18 (16 October 2026). It comes
from the report templates of that time, from inputs that are not in the repository (the ASOS
tables, the GraphSAGE evaluation files) or from a committed run file, so it is not edited by
hand:

- us-201-propensity.md: "The MI300X run of B1 uses the same data" and "serve benchmarks B1, B2
  and the comparison with the paper";
- us-202-graphsage.md: the section "CPU against MI300X" and, under "Reproduce", the MI300X
  droplet commands, whose make targets were removed on 17 October 2026;
- us-204-risk.md: "The same code runs on the MI300X with device=cuda in phase 6";
- benchmarks-cpu.md: the B1 and B2 titles ("MI300X pending", "CPU against MI300X") and the note
  "the MI300X half of B1 runs on the droplet (make bench DEVICE=gpu)", which come from the
  committed run file `bench/results/20261014T002158Z-cpu.json`; B1 to B4 were retired by ADR-18,
  and `make bench` now runs on the CPU only.

The MI300X run of B1 and every GPU run on the ASOS tables never happened (B1 ran on the CPU only,
14 October 2026). The risk code did run with device=cuda, once: `python -m scripts.train_amd risk`
on an AMD Instinct MI300X VF on 16 October 2026, on the session's synthetic store and without
rewriting us-204, which reports the CPU run (see [risk-model.md](risk-model.md)). The GPU
training that ran is in [the session report](amd-training-session-2026-10-16.md).

Removed on 17 October 2026, still in the git history: the MI300X droplet runbooks, the release
guide, the empty template of the GPU spike results and the guide of the Token Factory compose
overlay, whose procedures never ran (ADR-18); and the main review note of 17 October, whose open
limit moved to review-backlog.md.

## Suggested reading order

1. `spec.md`
2. `adr/README.md`
3. `ui-design.md`
4. the method document or the results of the work you are doing
