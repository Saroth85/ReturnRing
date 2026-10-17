# ReturnRing — instructions for Claude Code

## What we are building

An e-commerce return decision assistant for EU fashion merchants.
Statistical models estimate return probability, abuse risk, customer value and product anomalies;
the rules engine chooses admissible actions; the simulator estimates net benefit; Gemma 4 drafts
messages and explanations; a human approves every restrictive action.

Full spec: [docs/spec.md](docs/spec.md) (source of truth).

## Non-negotiable rules

1. The LLM never decides or predicts. Scores come from `ml/`; actions come from `policy/engine.py`.
2. Model calls use either local vLLM or the explicitly selected AMD Token Factory endpoint.
3. Never deny the 14-day right of withdrawal; refunds within 14 days.
4. Defect or non-conformity returns go to the guarantee path before scoring.
5. Every restrictive action needs a human approver in the audit log and a review date.
6. Forbidden features: gender, country, age, names and raw identifiers.
7. Every feature must be computable at decision time (`available_at <= decision_time`).
8. Residuals use the product/cohort reference model, never the personalized predictor.
9. Never count the refund owed to the consumer as a saving.
10. Amounts, terms and deadlines in messages come from template fields; the LLM only writes connective text.
11. Say "linked accounts", never "same person". Every number shown carries origin, period and nature.
12. Running services (`api`, `ui`, `demo`) never read `data/truth/`.
13. Report every experiment as it is, regressions included. Use the same splits for every model.
14. UI, README and code comments must be in English. Customer messages are in Italian and English.
15. Allowed licences: MIT, BSD, Apache-2.0, ISC, PSF, PostgreSQL; data CC BY 4.0. No secrets in git.

## Layout

`contracts/` schemas · `pipeline/` data/features/anomalies · `ml/` models and scoring ·
`policy/` engine/legal guard/simulator · `llm/` prompts/client/validator/agent ·
`apps/api` FastAPI · `apps/ui` React · `infra/` compose, Docker and runbooks · `tests/` ·
`docs/` spec, ADRs and results

## Commands

`make dev-up` · `make test` · `make test-gpu` · `make pipeline` · `make bench` ·
`make demo-export` · `make lint`

## Environment flags

`DEVICE=cpu|gpu` · `LLM_MODE=mock|vllm|tokenfactory` · `DEMO_MODE=live|cached` · `SEED=42`

## How to work in a session

- Read this file, the spec section for your story and `contracts/` before writing code.
- One user story per session (IDs `US-xxx` in `docs/spec.md`, section 5).
- Plan first, wait for approval, then implement.
- Tests first for policy, legal guard, simulator and validator.
- Respect `contracts/`. Change a contract only with a note in `docs/adr/`.
- Do not add a dependency without checking its licence against rule 15.
- Done = tests green, Ruff and mypy clean, PR description with the story ID and what to review.
