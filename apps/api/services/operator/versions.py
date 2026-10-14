"""The next version of a case: policy.engine.decide on the latest scores, at the day of the act
(ADR-15 section 4: "a review version is policy.engine.decide() on the latest scores at the day
of the review"; a recompute likewise, on the account graph as people left it).

services.cases.propose computes the engine's proposal, its inputs and their provenance, the
alternatives and the draft inputs, but builds a version-1 view (trigger initial, no version
before it): the model refuses any other version with that trigger. NextVersion takes its
proposal and gives it the identity of version n: the decision id, the id of the cluster coupon
application it proposes and the labels of its alternatives (<decision_id>/<kind>, US-602). The
values are the engine's and the simulator's, unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from apps.api import schemas as s
from apps.api.services.cases import Proposal, propose
from apps.api.services.ids import application_id, decision_id
from apps.api.serving import ReturnRecord, Serving


@dataclass(frozen=True)
class NextVersion:
    proposal: Proposal  # the engine's proposal, as services.cases.propose made it
    version: int
    decision_id: str
    alternatives: list[s.Alternative]  # labelled for this version

    @property
    def coupon_rule(self) -> dict[str, Any] | None:
        """The proposed cluster coupon application of this version, if its levers block."""
        rule = self.proposal.view.coupon_rule
        if rule is None:
            return None
        return {
            **rule.model_dump(),
            "application_id": application_id(self.proposal.view.return_id, self.version),
        }

    def view(self, **updates: Any) -> s.DecisionView:
        """The view of this version with `updates` (trigger, supersedes, the person's outcome),
        validated: the engine invariants and the legal guard run on it."""
        fresh = self.proposal.view
        values = {
            **fresh.model_dump(),
            "decision_id": self.decision_id,
            "version": self.version,
            "coupon_rule": self.coupon_rule,
            **updates,
        }
        return s.DecisionView.model_validate(values)


def next_version(
    serving: Serving,
    record: ReturnRecord,
    *,
    created_at: datetime,
    assumptions: s.Assumptions,
    coupon_rule_version: str,
    version: int,
) -> NextVersion:
    proposal = propose(
        serving,
        record,
        created_at=created_at,
        assumptions=assumptions,
        coupon_rule_version=coupon_rule_version,
    )
    did = decision_id(record.return_id, version)
    alternatives = []
    for a in proposal.alternatives:
        data = a.model_dump()
        data["net_benefit"]["assumptions"]["version"] = s.alternative_label(did, a.kind)
        alternatives.append(s.Alternative.model_validate(data))
    return NextVersion(proposal, version, did, alternatives)
