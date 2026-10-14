# Architecture decisions

| ID | Decision | Why |
| --- | --- | --- |
| ADR-01 | One container per ROCm stack | vLLM, amd_xgboost and DGL need different ROCm versions |
| ADR-02 | Models trained in jobs, artifacts on a shared volume | The API never depends on the GPU to serve scores |
| ADR-03 | Queue messages pre-generated in batch on vLLM | The operator never waits for the model |
| ADR-04 | Policy simulator computed in the browser | 20,000 risk-value pairs recompute instantly |
| ADR-05 | Terms snapshot stored with every order | The applicable terms are those shown at purchase |
| ADR-06 | Legal clause is fixed text | Compliance must not depend on a model |
| ADR-07 | React instead of Streamlit | Presentation is a quarter of the score; Streamlit is plan B |
| ADR-08 | Amounts, terms and deadlines from templates | A wrong number in a customer message is costly |
| ADR-09 | Guarantee path decided before scoring | A defect is not customer behaviour and has its own legal regime |
| ADR-10 | [ASOS evaluated with two protocols](ADR-10-asos-two-protocols.md) | ASOS aggregates contain the label; only decision-time features may measure the product. Amendment 1: protocol B's decision-time population, risk R-07 |
| ADR-11 | [Age is a forbidden feature](ADR-11-age-excluded.md) | Protected ground; the ASOS column is also dirty |
| ADR-12 | [Rule 15 covers the repository, not pulled images](ADR-12-container-licences.md) | ROCm and vLLM images ship GPL and other licences and are never redistributed |
| ADR-13 | [Data contracts v2](ADR-13-data-contracts-v2.md) | A row holds only facts known at its available_at; one lever vocabulary; every file carries contract version and provenance. Amendment 3: customer features, value and evidence tables. Amendment 4: scores/request_scores and two checks on scores/scores. Amendment 5: thresholds with every quadrant, short origin and lineage, no model version on the guarantee path, labels and keys never features |
| ADR-14 | [LLM contracts v2](ADR-14-llm-contracts-v2.md) | Numbers in an explanation are grounded on code-built facts the statement cites; one placeholder grammar; no scores on guarantee or insufficient-evidence cases |
| ADR-15 | [API and persistence contract](ADR-15-api-and-persistence-contract.md) | One generated contract for the API and the UI: pydantic models, JSON Schemas, OpenAPI, tested examples and PostgreSQL tables with named CHECKs |
| ADR-16 | [Serving snapshot and API core](ADR-16-serving-snapshot.md) | The API never mounts data/: it loads a hashed snapshot of the store, features, scores and models, with no ground truth; the API core the route builders extend (errors, headers, roles, stubs, intake, drafts in mock, vLLM and cached modes, reset) |
