# ReturnRing

ReturnRing helps a fashion e-commerce choose and explain the best action on every return. Statistical
models estimate return probability, abuse risk, customer value and product anomalies; a rules engine picks
admissible actions within EU consumer law; a simulator estimates the net benefit under explicit
assumptions; Gemma 4, served with vLLM on an AMD Instinct MI300X, explains each decision and drafts the
customer message. A human approves every restrictive action.

Built for the AMD Developer Hackathon ACT III (lablab.ai), Track 3 "Reinvent Commerce".

> Status: project skeleton. Policy engine, legal guard, economic simulator, LLM output validator and data
> contracts are implemented and tested. Data, models and UI arrive story by story (docs/spec.md, section 5).

## Quick start (CPU, no GPU needed)

```bash
cp .env.example .env
make dev-up
open http://localhost:8080   # UI placeholder; API on http://localhost:8000/readyz
```

## Development

```bash
pip install -r requirements/dev.txt
make test       # CPU tests, LLM mocked
make lint       # ruff + mypy (strict)
make licenses   # licence gate (CLAUDE.md rule 15)
```

GPU work happens on an AMD Developer Cloud droplet with one MI300X: see `infra/spikes/README.md` and
`docs/spec.md` section 11.

## How the risk is computed

To be completed with the models (US-904). Principles already enforced in code: the LLM never decides or
predicts; risk comes from XGBoost on account-graph and behaviour features; customer value never feeds the
risk score; gender and country are never features; every feature is computed only from data available
at decision time.

## What is real and what is simulated

- Return behaviour: real, from the ASOS GraphReturns dataset (see Data).
- Linked accounts and abuse patterns: simulated, because no public dataset contains identities. Abuse
  detection must be verified in a pilot with a real merchant.
- Economic figures: simulated under explicit assumptions, always labelled as such.

## Data

ASOS GraphReturns dataset, McGowan et al. (2023), "A Dataset for Learning Graph Representations to
Predict Customer Returns in Fashion Retail", arXiv:2302.14096, https://osf.io/c793h, licensed CC BY 4.0.
It is downloaded by `make asos` and never committed to this repository.

## Licences

Code: MIT (see LICENSE). Runtime dependencies are permissive (MIT, BSD, Apache-2.0), checked in CI.
Hypothesis (MPL-2.0) is used only as a development tool and is not distributed.

## Built with

AMD Developer Cloud, AMD Instinct MI300X, ROCm, AMD Finance toolkit (XGBoost), DGL on ROCm, vLLM,
Gemma 4, FastAPI, PostgreSQL. Developed with Claude Code as a coding assistant; no hosted model is
called by the product at runtime.

## Limits

This is a hackathon prototype. Legal constraints are encoded as explicit, testable rules and must be
validated by a consumer-law professional before any real use. The specification is in `docs/spec.md`.
