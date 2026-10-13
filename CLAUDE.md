# ReturnRing — instructions for Claude Code

## What we are building
An e-commerce return decision assistant for EU fashion merchants (AMD Developer Hackathon ACT III, Track 3).
Statistical models estimate return probability, abuse risk, customer value and product anomalies; a rules
engine chooses admissible actions; a simulator estimates net benefit; Gemma 4 on vLLM (AMD MI300X) explains
and drafts messages; a human approves restrictive actions. Full spec: docs/spec.md (source of truth).

## Non-negotiable rules
1. The LLM never decides or predicts. Scores come from ml/; actions come from policy/engine.py. Output that
   changes the decision is rejected by llm/validator.py.
2. No external model or API calls at runtime. Only the local vLLM endpoint.
3. Never deny the 14-day right of withdrawal; refunds within 14 days. Restrictive levers apply only to future
   orders and are shown at checkout (terms snapshot). Enforced by policy/legal_guard.py, 100% test coverage.
4. Defect or non-conformity returns go to the guarantee path before scoring. No restrictive lever applies.
5. Every restrictive action (Q3, Q4) needs a human approver in the audit log, and a review date.
6. Forbidden features: gender, country, age, names, raw identifiers. Customer value never feeds the risk score.
7. Every feature must be computable at decision time: filter available_at <= decision_time. No future events.
8. The residual uses the product/cohort reference model, never the personalized predictor.
9. Never count the refund owed to the consumer as a saving. Economic outputs are net benefit with explicit,
   versioned assumptions (policy/simulator.py).
10. Amounts, terms and deadlines in messages come from template fields; the LLM writes only connective text.
    The legal clause is fixed text from llm/legal_clause.py.
11. Say "linked accounts", never "same person". Every number shown carries origin, period and nature
    (real, synthetic, simulated).
12. Running services never read data/truth/. Ground truth is for evaluation only.
13. Report every experiment as it is, regressions included. Same splits for every model.
14. UI, README and code comments in English. Customer messages in Italian and English.
15. Allowed licenses: MIT, BSD, Apache-2.0, ISC, PSF, PostgreSQL; data CC BY 4.0. No secrets in git.

## Layout
contracts/ schemas · pipeline/ data, features, anomalies · ml/gbm, ml/gnn models ·
policy/ engine, legal guard, simulator · llm/ prompts, client, validator, agent ·
apps/api FastAPI · apps/ui React · infra/ compose, Caddy · tests/ · docs/ spec, ADR

## Commands
make dev-up · make test · make test-gpu · make pipeline · make bench · make demo-export · make lint

## Environment flags
DEVICE=cpu|gpu · LLM_MODE=mock|vllm · DEMO_MODE=live|cached · SEED=42

## How to work in a session
- Read this file, the spec section for your story and contracts/ before writing code.
- One user story per session (IDs US-xxx in docs/spec.md, section 5).
- Plan first, wait for approval, then implement.
- Tests first for policy, legal guard, simulator and validator.
- Respect contracts/. Change a contract only with a note in docs/adr/.
- Do not add a dependency without checking its license against rule 15.
- Done = tests green, ruff and mypy clean, PR description with the story ID and what to review.
