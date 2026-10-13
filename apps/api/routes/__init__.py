"""Routers of the API, one module per area, so the route builders never edit the same file:

- health: /healthz and /readyz;
- operator: returns (queue, case card, decision, regenerate, evidence), decision records and
  link disputes;
- manager: portfolio, simulator assumptions, KPI, fairness, audit and its CSV, outbox,
  anomalies, catalogue tasks and coupon rules;
- other: checkout, inspections, the agent, benchmarks and telemetry.

Every endpoint of ADR-15 (apps.api.schemas.ENDPOINTS) is registered exactly once: a module
registers its implemented routes, then routes.stubs.add_stubs() answers 501 for the rest of its
group, with the contract's body, path and query validation and role check already in place.
"""
