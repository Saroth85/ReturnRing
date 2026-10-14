"""Services of the API: the code between the routes and policy/, llm/, ml/ and pipeline/.

- cases: the intake of a return (flow F1) and the case card (US-302);
- decisions: decision versions in the database and their API views;
- generation: drafts through llm/ (mock, vLLM or the MI300X cache), stored in llm_generations;
- alternatives: the admissible actions of a version, priced by policy.simulator (US-308);
- audit and outbox: the append-only log and the simulated outbox;
- seeds: the stored assumption version v1 and the general coupon rule v1 (US-903);
- ids and provenance: identifiers and the provenance of every figure (rule 11).

Nothing here decides or predicts on its own (rule 1): scores come from the serving snapshot,
decisions from policy.engine, every decision passes policy.legal_guard.
"""
