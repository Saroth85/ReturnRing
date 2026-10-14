"""Services of the operator's area (E3: US-301..US-310, US-604, US-607; ADR-15 section 4).

- graph: the account graph after the disputes and explanations people made (US-310): the
  serving snapshot seen with those links out of the cluster features;
- queue: the return queue (GET /api/returns) and the decision history (GET /api/decisions);
- shown: a case as the person saw it, the latest version checked (404, 409 stale_decision);
- actions: approve, override, reject, lift and renew (POST .../decision);
- versions: the next version of a case, policy.engine.decide at the day of the act;
- recompute: evidence and link disputes, a new version from policy.engine (US-310, flow F7);
- drafts: a new draft of the latest version through llm/ (POST .../regenerate);
- coupons: the cluster coupon application of a decision as served (US-402, US-607).

Nothing here decides or predicts on its own (rule 1): scores come from the serving snapshot and
its models (ml/), decisions from policy.engine and policy.legal_guard, drafts from llm/.
"""
