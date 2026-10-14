"""The golden demo walk over HTTP (spec 16, US-903; E2E-01, E2E-02, E2E-04, E2E-05, US-307,
US-310, US-702, ECO-01, US-603, US-605, US-604).

    python -m scripts.demo_walk --base-url http://127.0.0.1:8000 [--report walk.json]

Run it once after `python -m apps.api.reset` (the walk approves cases, so a second run needs a
new reset). It talks to the API only, never to data/ (rule 12), and walks the path of the pitch:

1. readiness (with the demo clock), the golden cases of the reset (GET /api/demo/cases: the
   live ids of Giulia, a Marco ring account, the Rossi families, Elena, an insufficient case and
   a link to dispute), then the queue (US-301: under 2 s; the whole queue page by page too);
2. Giulia (Q1): the upgrade applies by itself; Sara confirms it, a message without a legal clause;
3. a Marco ring account (Q4): Sara approves, with the review date of the version (rule 5), the
   cluster coupon block confirmed, the withdrawal clause in the message, the audit entries and
   the outbox; then the checkout of another ring account shows the coupon block and its terms
   (US-402, US-403: under 1 s) and the order is confirmed with the terms shown (E2E-02);
4. the Rossi family: linked accounts, never Q3 or Q4 (E2E-05);
5. Elena: the guarantee path, no score, no restriction; the anomaly of the variant opens a
   catalogue task that changes no decision (E2E-04, US-606);
6. a case with insufficient evidence: standard terms and the missing evidence (US-307);
7. a link dispute from a case: before and after, a new version, the audit entry (US-310), and
   whether the decision changed, as /api/demo/cases announced (changes_decision);
8. the three questions of the agent on the ring case, streamed (US-702); in cached mode the
   GPU is off and the agent answers 503 llm_unavailable, as the contract says (H35);
9. the manager's matrix, the simulator (ECO-01: 368 EUR), the KPIs of the reset week, the
   fairness report, the audit log as CSV; telemetry and benchmarks.

On the public read-only demo (`/readyz` says `public_read_only`: PUBLIC_READ_ONLY=true, the
default of the cached demo; spec 11) the same steps read the same cases and check that every
write of the pitch (the approvals, the checkout confirmation, the catalogue task, the dispute)
is refused with 403 forbidden_role and changes nothing, while the two writes the public pages
keep, the checkout evaluation and the agent, still answer. A read-only walk changes no decision
(the evaluation only records the terms it showed, an audit entry and a terms snapshot without an
order), so it can run again without a reset.

Every JSON response is checked against its JSON Schema (contracts/json/api) and its pydantic
model; the agent stream frame by frame. The report lists each call with its status and time.
Exit codes: 0 every check passed, 1 a check failed (the report says which), 2 usage.

The same walk runs in CI on the tiny snapshot of the test harness (tests/test_demo_walk.py),
through the ASGI app instead of a socket: `walk(client)` takes any httpx.Client.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import statistics
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from typing import Any, Final

import httpx
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter

from apps.api import schemas as s
from apps.api.roles import ACTOR_HEADER
from llm.legal_clause import clause
from pipeline.generator.golden import (
    ELENA,
    GIULIA,
    MARCO_ACCOUNTS,
    ROSSI_ACCOUNTS,
    ROSSI_CARD_ACCOUNTS,
)

ROOT: Final = Path(__file__).resolve().parents[1]
SCHEMAS: Final = ROOT / "contracts" / "json" / "api"
QUEUE_LIMIT_S: Final = 2.0  # US-301
CHECKOUT_LIMIT_S: Final = 1.0  # US-403
QUESTIONS: Final = (  # US-702: the three questions that always work
    "Which evidence supports the link between these linked accounts?",
    "What makes this case different from the Rossi family?",
    "Which terms applied when the order was placed?",
)
RESTRICTED: Final = ("Q3", "Q4")
_EVENT: Final[TypeAdapter[s.AgentEventModel]] = TypeAdapter(s.AgentEvent)


class WalkError(AssertionError):
    """A check of the walk failed."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise WalkError(message)


@cache
def _validator(name: str) -> Draft202012Validator:
    document = json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(document)
    return Draft202012Validator(document)


def validate(name: str, body: Any) -> None:
    """`body` is a valid `name` of the contract: its JSON Schema and its pydantic model."""
    _validator(name).validate(body)
    s.API_MODELS[name].model_validate(body)


def parse_sse(text: str) -> list[dict[str, Any]]:
    """The events of a text/event-stream body; each frame is exactly sse_frame of its event."""
    check(text.endswith("\n\n"), "every SSE frame ends with a blank line")
    events: list[dict[str, Any]] = []
    for frame in text.split("\n\n")[:-1]:
        lines = frame.split("\n")
        check([x.split(": ", 1)[0] for x in lines] == ["event", "id", "data"], f"frame {frame!r}")
        event = json.loads(lines[2].removeprefix("data: "))
        check(frame + "\n\n" == s.sse_frame(_EVENT.validate_python(event)), "sse_frame")
        events.append(event)
    return events


@dataclass(frozen=True)
class Call:
    step: str
    method: str
    path: str
    status: int
    seconds: float


@dataclass
class Report:
    calls: list[Call] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    passed: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    def latency(self, step: str, path_prefix: str | None = None) -> dict[str, float]:
        times = [
            c.seconds
            for c in self.calls
            if c.step == step and (path_prefix is None or c.path.startswith(path_prefix))
        ]
        if not times:
            return {}
        return {
            "calls": len(times),
            "p50_s": round(statistics.median(times), 3),
            "max_s": round(max(times), 3),
        }

    def summary(self) -> dict[str, Any]:
        steps = sorted({c.step for c in self.calls}, key=[c.step for c in self.calls].index)
        return {
            "passed": self.passed,
            "failed": self.failed,
            "latency": {step: self.latency(step) for step in steps},
            "facts": self.facts,
            "calls": [asdict(c) for c in self.calls],
        }


class Walk:
    """The demo path on one API (an httpx.Client with its base URL, or a TestClient)."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client
        self.report = Report()
        self.step = "setup"
        self.ring: dict[str, Any] = {}
        self.cases: dict[str, Any] = {}
        self.mode = "live"
        self.read_only = False

    # ---- HTTP -----------------------------------------------------------------------------------

    def call(
        self,
        method: str,
        path: str,
        model: str | None,
        *,
        expect: int = 200,
        actor: str | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        if actor is not None:
            headers[ACTOR_HEADER] = actor
        started = time.perf_counter()
        response = self.client.request(method, path, headers=headers, **kwargs)
        seconds = time.perf_counter() - started
        self.report.calls.append(Call(self.step, method, path, response.status_code, seconds))
        check(
            response.status_code == expect,
            f"{method} {path}: {response.status_code} (expected {expect}): {response.text[:500]}",
        )
        if model is not None:
            validate(model, response.json())
        return response

    def get(self, path: str, model: str | None, **kwargs: Any) -> Any:
        return self.call("GET", path, model, **kwargs).json()

    def post(self, path: str, model: str | None, body: Mapping[str, Any], **kwargs: Any) -> Any:
        return self.call("POST", path, model, json=dict(body), **kwargs).json()

    def queue(self, **params: Any) -> list[dict[str, Any]]:
        """Every item of the queue for the filters, page by page."""
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            body = self.get(
                "/api/returns",
                "queue_response",
                params={**params, "page": page, "page_size": 100},
                actor="sara",
            )
            items += body["items"]
            if len(items) >= body["total"] or not body["items"]:
                return items
            page += 1

    def card(self, return_id: str) -> dict[str, Any]:
        card: dict[str, Any] = self.get(f"/api/returns/{return_id}", "case_card", actor="sara")
        return card

    def decide(self, card: Mapping[str, Any], action: str, lang: str, **fields: Any) -> Any:
        body = {
            "decision_id": card["decision"]["decision_id"],
            "action": action,
            "approver": "sara",
            "message": {"lang": lang},
            **fields,
        }
        validate("decision_request", body)
        return self.post(
            f"/api/returns/{card['return_id']}/decision", "decision_result", body, actor="sara"
        )

    def decision_body(self, card: Mapping[str, Any], action: str, lang: str) -> dict[str, Any]:
        body = {
            "decision_id": card["decision"]["decision_id"],
            "action": action,
            "approver": "sara",
            "message": {"lang": lang},
        }
        validate("decision_request", body)
        return body

    def refuse(
        self, path: str, request: str, body: Mapping[str, Any], actor: str | None = "sara"
    ) -> None:
        """On the public read-only demo, a write of the pitch is a 403 forbidden_role that names
        the read-only mode (spec 11; ADR-15 section 15, H21)."""
        validate(request, dict(body))
        refused = self.call("POST", path, "error", json=dict(body), expect=403, actor=actor)
        error = refused.json()
        check(error["code"] == "forbidden_role", f"POST {path}: {error['code']}")
        check("read-only" in error["message"], f"POST {path}: the read-only mode is named")
        self.report.facts.setdefault("refused_writes", []).append(f"POST {path}")

    def unchanged(self, card: Mapping[str, Any]) -> dict[str, Any]:
        """The case after a refused write: the decision shown before, unchanged."""
        after = self.card(card["return_id"])
        check(_stored(after["decision"]) == _stored(card["decision"]), "nothing changed")
        return after

    def audit_actions(self, return_id: str) -> list[str]:
        page = self.get(
            "/api/audit", "audit_page", params={"return_id": return_id, "page_size": 500}
        )
        return [e["action"] for e in reversed(page["items"])]  # oldest first

    # ---- Steps ----------------------------------------------------------------------------------

    def readiness(self) -> None:
        body = self.get("/readyz", "readyz_response")
        checks = {c["component"]: c["status"] for c in body["checks"]}
        check(checks["database"] == "ok", f"database not ready: {body['checks']}")
        check(body["status"] in ("ready", "degraded"), f"status {body['status']}")
        self.mode = body["mode"]
        self.read_only = bool(body["public_read_only"])
        self.report.facts["mode"] = {
            "mode": body["mode"],
            "public_read_only": self.read_only,
            "llm_mode": body["llm_mode"],
            "device": body["device"],
            "checks": checks,
            "model_versions": body["model_versions"],
            "clock": body["clock"],
        }

    def demo_cases(self) -> None:
        """GET /api/demo/cases: the live ids of the golden cases, each served by its card."""
        body = self.get("/api/demo/cases", "demo_cases")
        self.cases = {c["key"]: c for c in body["cases"]}
        self.cases["_dispute"] = body["dispute"]
        self.cases["_carts"] = {c["key"]: c["request"] for c in body["carts"]}
        for case in body["cases"]:
            card = self.card(case["return_id"])
            check(card["customer_id"] == case["customer_id"], f"{case['key']}: the customer")
            versions = [r["decision_id"] for r in card["revisions"]]
            check(versions[:1] == [case["decision_id"]], f"{case['key']}: the reset's version")
        self.report.facts["demo_cases"] = {c["key"]: c["return_id"] for c in body["cases"]} | {
            "dispute": None if body["dispute"] is None else body["dispute"]["link_id"]
        }

    def queue_load(self) -> None:
        first = self.get("/api/returns", "queue_response", actor="sara")
        check(first["total"] > 0, "the queue is empty: run python -m apps.api.reset first")
        values = [i["expected_net_benefit"]["value"] for i in first["items"]]
        check(values == sorted(values, reverse=True), "sorted by expected net benefit (US-301)")
        everything = self.queue()
        check(len(everything) == first["total"], "every page of the queue")
        check(
            any(i["state"] == "proposed" and i["requires_approval"] for i in everything),
            "a restriction awaits approval: run python -m apps.api.reset first",
        )
        self.report.facts["queue"] = {
            "total": first["total"],
            "by_path": _count(everything, "path"),
            "by_quadrant": _count(everything, "quadrant"),
            "by_evidence": _count(everything, "evidence_quality"),
        }

    def giulia(self) -> None:
        (item,) = [i for i in self.queue(q=GIULIA) if i["customer_id"] == GIULIA]
        if "giulia" in self.cases:
            check(self.cases["giulia"]["return_id"] == item["return_id"], "the demo case")
        card = self.card(item["return_id"])
        d = card["decision"]
        check(d["quadrant"] == "Q1" and d["in_force"], f"Giulia is Q1 in force: {d['quadrant']}")
        check(not d["requires_approval"], "Q1 needs no approval")
        if self.read_only:
            path = f"/api/returns/{card['return_id']}/decision"
            self.refuse(path, "decision_request", self.decision_body(card, "approve", "it"))
            self.unchanged(card)
            self.report.facts["giulia"] = {
                "return_id": card["return_id"],
                "quadrant": d["quadrant"],
                "levers": d["lever_ids"],
                "decided_on": d["decided_on"],
            }
            return
        result = self.decide(card, "approve", "it")
        check(result["decision"]["state"] == "approved", "Giulia approved")
        message = result["outbox_message"]
        check(message is not None and message["legal_clause_kind"] is None, "no legal clause")
        self.report.facts["giulia"] = {
            "return_id": card["return_id"],
            "quadrant": d["quadrant"],
            "levers": d["lever_ids"],
            "decided_on": d["decided_on"],
        }

    def marco(self) -> None:
        items = [
            i
            for i in self.queue(q="C-MARCO-")
            if i["customer_id"] in MARCO_ACCOUNTS
            and i["quadrant"] == "Q4"
            and i["state"] == "proposed"
            and i["requires_approval"]
        ]
        check(bool(items), "a ring account in Q4 awaiting approval")
        item = sorted(items, key=lambda i: str(i["return_id"]))[0]
        card = self.card(item["return_id"])
        d = card["decision"]
        check(not d["in_force"], "a Q4 proposal is not in force before its approval (rule 5)")
        linked = card["linked_accounts"]
        check(linked is not None and linked["cluster_size"] >= 2, "the ring is a cluster")
        check(d["coupon_rule"] is not None, "Q4 proposes the cluster coupon block (US-402)")
        if self.read_only:
            self.marco_read_only(card)
            return
        result = self.decide(card, "approve", "it")
        new = result["decision"]
        check(new["state"] == "approved" and new["in_force"], "approved and in force")
        check(new["approver"] == "sara", "the approver is recorded (rule 5)")
        check(new["review_at"] is not None, "a restriction has a review date (US-607)")
        review = date.fromisoformat(new["review_at"])
        check(review > date.fromisoformat(new["decided_on"]), "the review date is ahead")
        application = result["coupon_application"]
        check(application is not None, "the coupon application comes with the approval")
        check(application["state"] == "confirmed", "the cluster block is confirmed")
        check(application["confirmed_by"] == "sara", "confirmed by the approver")
        affected = sorted(application["affected_customer_ids"])
        check(card["customer_id"] in affected and len(affected) >= 2, "the linked accounts")
        message = result["outbox_message"]
        check(message["legal_clause_kind"] == "withdrawal", "the withdrawal clause (rule 10)")
        check(message["body"].endswith(clause("withdrawal", "it")), "the fixed clause text")
        actions = self.audit_actions(card["return_id"])
        expected = ["decision_proposed", "draft_generated", "decision_approved", "message_queued"]
        check(actions[: len(expected)] == expected, f"audit trail {actions}")
        outbox = self.get(
            "/api/outbox", "outbox", params={"return_id": card["return_id"]}, actor="luca"
        )
        check(
            [m["message_id"] for m in outbox["items"]] == [message["message_id"]],
            "the message is in the outbox",
        )
        record = self.get(f"/api/decisions/{new['decision_id']}", "decision_record", actor="luca")
        check(record["decision"]["coupon_rule"]["state"] == "confirmed", "the history (US-604)")
        self.ring = {
            "return_id": card["return_id"],
            "customer_id": card["customer_id"],
            "variant_id": card["item"]["variant_id"],
            "size": card["item"]["size"] or "M",
            "decision_id": new["decision_id"],
            "application_id": application["application_id"],
            "affected": affected,
            "review_at": new["review_at"],
        }
        self.report.facts["marco"] = {
            "return_id": card["return_id"],
            "customer_id": card["customer_id"],
            "cluster_size": linked["cluster_size"],
            "levers": new["lever_ids"],
            "review_at": new["review_at"],
            "affected_accounts": len(affected),
            "audit": actions,
        }

    def marco_read_only(self, card: Mapping[str, Any]) -> None:
        """The ring account's proposal stays a proposal: the approval is refused (rule 5 still
        holds: nothing restrictive is in force without a person)."""
        d = card["decision"]
        path = f"/api/returns/{card['return_id']}/decision"
        self.refuse(path, "decision_request", self.decision_body(card, "approve", "it"))
        after = self.unchanged(card)["decision"]
        check(not after["in_force"], "still a proposal, not in force (rule 5)")
        check(after["coupon_rule"]["state"] == "proposed", "the coupon block stays proposed")
        actions = self.audit_actions(card["return_id"])
        check(actions[:2] == ["decision_proposed", "draft_generated"], f"audit trail {actions}")
        check("decision_approved" not in actions, "no approval in the audit log")
        linked = card["linked_accounts"]
        self.ring = {
            "return_id": card["return_id"],
            "customer_id": card["customer_id"],
            "variant_id": card["item"]["variant_id"],
            "size": card["item"]["size"] or "M",
            "decision_id": d["decision_id"],
            "application_id": None,
            "affected": sorted(d["coupon_rule"]["affected_customer_ids"]),
            "review_at": None,
        }
        self.report.facts["marco"] = {
            "return_id": card["return_id"],
            "customer_id": card["customer_id"],
            "cluster_size": linked["cluster_size"],
            "proposed_levers": d["lever_ids"],
            "state": after["state"],
            "audit": actions,
        }

    def checkout_read_only(self, lines: list[dict[str, Any]]) -> None:
        """The evaluation answers (a public page keeps it); a proposal never reaches it (rule 5);
        the confirmation, which stores an order and its terms snapshot, is refused."""
        customer = self.ring["customer_id"]
        shown = self.evaluate(customer, lines)
        check(shown["terms_source"] != self.ring["decision_id"], "a proposal is not in force")
        check(shown["terms"]["return_window_days"] >= 14, "never under 14 days (rule 3)")
        confirm = {
            "terms_snapshot_id": shown["terms_snapshot_id"],
            "customer_id": customer,
            "lines": lines,
            "coupon_code": None,
        }
        self.refuse("/api/checkout/confirm", "checkout_confirm_request", confirm, actor=None)
        self.report.facts["checkout"] = {
            "customer_id": customer,
            "terms_source": shown["terms_source"],
            "quadrant": shown["quadrant"],
            "terms": shown["terms"],
            "coupon_block": None if shown["coupon_block"] is None else "shown",
            "confirm": "403 forbidden_role (read-only)",
        }

    def evaluate(self, customer: str, lines: list[dict[str, Any]]) -> Any:
        request = {"customer_id": customer, "lines": lines, "coupon_code": None}
        validate("checkout_evaluate_request", request)
        return self.post("/api/checkout/evaluate", "checkout_evaluate_response", request)

    def checkout(self) -> None:
        lines = [{"variant_id": self.ring["variant_id"], "size": self.ring["size"], "quantity": 1}]
        if self.read_only:
            self.checkout_read_only(lines)
            return
        # the approved account: the Q4 terms of its decision, with their review date (rule 3:
        # future orders only, shown before the purchase)
        own = self.evaluate(self.ring["customer_id"], lines)
        check(own["quadrant"] == "Q4", "the approved restriction reaches the checkout")
        check(own["terms_source"] == self.ring["decision_id"], "the terms of the approval")
        check(own["review_at"] == self.ring["review_at"], "with the review date of the version")
        restricted = own["terms"]
        check(restricted["return_window_days"] == 14, "14 days, the legal minimum (rule 3)")
        check(not restricted["free_return_shipping"] and not restricted["instant_refund"], "Q4")
        check(not restricted["coupons_enabled"] and not own["coupon_eligible"], "no coupon")
        # another account of the ring: the confirmed cluster coupon block (US-402)
        others = [c for c in self.ring["affected"] if c != self.ring["customer_id"]]
        check(bool(others), "another account of the ring")
        customer = others[0]
        shown = self.evaluate(customer, lines)
        block = shown["coupon_block"]
        check(block is not None, "the confirmed cluster block reaches every linked account")
        check(block["application_id"] == self.ring["application_id"], "the approved block")
        check(not shown["coupon_eligible"], "no coupon for a blocked account (US-402)")
        if shown["terms_source"] != "base":
            check(shown["quadrant"] in RESTRICTED, "an own decision in force is a restriction")
            check(shown["review_at"] is not None, "restrictive terms carry their review date")
        else:  # the base terms, with the coupons the block turns off (H12)
            expected = {**shown["base_terms"], "coupons_enabled": False}
            check(shown["terms"] == expected, "no decision of its own: base terms, no coupons")
        check(shown["terms"]["return_window_days"] >= 14, "never under 14 days (rule 3)")
        confirm = {
            "terms_snapshot_id": shown["terms_snapshot_id"],
            "customer_id": customer,
            "lines": lines,
            "coupon_code": None,
        }
        validate("checkout_confirm_request", confirm)
        done = self.post("/api/checkout/confirm", "checkout_confirm_response", confirm, expect=201)
        snapshot = done["terms_snapshot"]
        check(snapshot["terms_snapshot_id"] == shown["terms_snapshot_id"], "the snapshot shown")
        check(snapshot["coupon_application_id"] == block["application_id"], "the block stored")
        check(snapshot["coupon_review_at"] == block["review_at"], "with its review date")
        check(snapshot["order_id"] == done["order_id"], "bound to the new order (E2E-02)")
        check(snapshot["terms"] == shown["terms"], "the terms shown are the terms stored")
        check(not done["coupon_applied"], "no coupon applied")
        again = self.call("POST", "/api/checkout/confirm", "error", json=confirm, expect=409)
        check(again.json()["code"] == "conflict", "a second confirmation is a conflict")
        self.report.facts["checkout"] = {
            "approved_account_terms": restricted,
            "customer_id": customer,
            "terms_source": shown["terms_source"],
            "quadrant": shown["quadrant"],
            "terms": shown["terms"],
            "coupon_block": block["application_id"],
            "order_id": done["order_id"],
        }

    def rossi(self) -> None:
        family = ROSSI_ACCOUNTS + ROSSI_CARD_ACCOUNTS
        items = [i for i in self.queue(q="C-ROSSI") if i["customer_id"] in family]
        check(bool(items), "an open return of the Rossi family")
        linked = 0
        for item in items:
            check(item["quadrant"] not in RESTRICTED, f"{item['return_id']} is never Q3 or Q4")
            check(not item["requires_approval"], "no restriction for the family (MOD-11)")
            card = self.card(item["return_id"])
            linked += card["linked_accounts"] is not None
        check(linked > 0, "the family is linked accounts")
        self.report.facts["rossi"] = {
            "returns": len(items),
            "quadrants": _count(items, "quadrant"),
            "with_linked_accounts": linked,
        }

    def elena(self) -> None:
        (item,) = [i for i in self.queue(q=ELENA) if i["customer_id"] == ELENA]
        check(item["path"] == "guarantee" and item["risk"] is None, "guarantee, unscored")
        card = self.card(item["return_id"])
        d = card["decision"]
        check(card["risk"] is None and d["quadrant"] is None, "no score (rule 4)")
        check(d["levers"] == [] and not d["requires_approval"], "no restrictive lever (rule 4)")
        anomaly = card["product_anomaly"]
        check(anomaly is not None, "the anomaly of the variant is on the card (US-606)")
        if self.read_only:
            path = f"/api/returns/{card['return_id']}/decision"
            self.refuse(path, "decision_request", self.decision_body(card, "approve", "it"))
            task = {
                "anomaly_id": anomaly["anomaly_id"],
                "kind": "size_guide",
                "note": "Check the size chart of the variant against the garment",
                "author": "luca",
                "return_id": card["return_id"],
            }
            self.refuse("/api/catalog-tasks", "catalog_task_create", task, actor="luca")
            listed = self.get("/api/products/anomalies", "product_anomalies", actor="luca")
            ids = [a["anomaly_id"] for a in listed["items"]]
            check(anomaly["anomaly_id"] in ids, "the anomaly is listed for the manager")
            self.unchanged(card)
            self.report.facts["elena"] = {
                "return_id": card["return_id"],
                "anomaly_id": anomaly["anomaly_id"],
                "task_id": None,
            }
            return
        result = self.decide(card, "approve", "it")
        message = result["outbox_message"]
        check(message["legal_clause_kind"] == "guarantee", "the guarantee clause")
        check(message["body"].endswith(clause("guarantee", "it")), "the fixed clause text")
        task = {
            "anomaly_id": anomaly["anomaly_id"],
            "kind": "size_guide",
            "note": "Check the size chart of the variant against the garment",
            "author": "luca",
            "return_id": card["return_id"],
        }
        validate("catalog_task_create", task)
        created = self.post("/api/catalog-tasks", "catalog_task", task, expect=201, actor="luca")
        listed = self.get("/api/products/anomalies", "product_anomalies", actor="luca")
        flagged = {a["anomaly_id"]: a for a in listed["items"]}
        check(anomaly["anomaly_id"] in flagged, "the anomaly is listed for the manager")
        shown = flagged[anomaly["anomaly_id"]]
        check(shown["catalog_task_id"] == created["task_id"], "with its catalogue task")
        after = self.card(card["return_id"])
        check(
            _stored(after["decision"]) == _stored(result["decision"]),
            "the task changes no decision, lever or message (E2E-04)",
        )
        check(after["product_anomaly"]["catalog_task_id"] == created["task_id"], "card task")
        self.report.facts["elena"] = {
            "return_id": card["return_id"],
            "anomaly_id": anomaly["anomaly_id"],
            "task_id": created["task_id"],
        }

    def insufficient(self) -> None:
        items = [
            i
            for i in self.queue(evidence_quality="insufficient", state=["proposed"])
            if i["path"] == "withdrawal"
        ]
        check(bool(items), "a withdrawal case with insufficient evidence")
        card = self.card(items[0]["return_id"])
        d = card["decision"]
        check(card["risk"] is None and d["quadrant"] is None, "neither safe nor suspect")
        check(bool(card["evidence"]["missing_evidence"]), "the missing evidence is listed")
        check(d["proposed_levers"] == [], "the standard terms (US-307)")
        if self.read_only:
            path = f"/api/returns/{card['return_id']}/decision"
            self.refuse(path, "decision_request", self.decision_body(card, "approve", "en"))
            self.report.facts["insufficient"] = {
                "return_id": card["return_id"],
                "missing": card["evidence"]["missing_evidence"],
            }
            return
        result = self.decide(card, "approve", "en")
        check(result["decision"]["levers"] == [], "approved with the standard terms")
        check(result["coupon_application"] is None, "no coupon block")
        self.report.facts["insufficient"] = {
            "return_id": card["return_id"],
            "missing": card["evidence"]["missing_evidence"],
        }

    def dispute(self) -> None:
        """The link of the demo cases whose dispute changes a cluster (US-310), else a link of
        the Rossi shared-card family."""
        chosen = self.cases.get("_dispute")
        card = None
        link_id = None
        if chosen is not None:
            found = self.card(chosen["return_id"])
            if found["decision"]["decision_id"] == chosen["decision_id"]:
                card, link_id = found, chosen["link_id"]
        if card is None:
            for item in self.queue(q="C-ROSSI-CARD"):
                if item["customer_id"] in ROSSI_CARD_ACCOUNTS and item["state"] == "proposed":
                    found = self.card(item["return_id"])
                    if found["linked_accounts"] is not None:
                        card = found
                        link_id = next(
                            x["link_id"] for x in found["linked_accounts"]["links"]
                            if not x["disputed"]
                        )  # fmt: skip
                        break
        check(card is not None and link_id is not None, "a link to dispute from a case")
        assert card is not None and link_id is not None
        link = {"link_id": link_id}
        body = {
            "reason": "The card is the parents' card: one household, confirmed by phone",
            "author": "sara",
            "return_id": card["return_id"],
            "decision_id": card["decision"]["decision_id"],
        }
        if self.read_only:
            self.refuse(f"/api/links/{link_id}/dispute", "link_dispute_request", body)
            again = self.unchanged(card)
            links = (again["linked_accounts"] or {}).get("links", [])
            check(
                all(not x["disputed"] for x in links if x["link_id"] == link_id),
                "the link is not disputed",
            )
            self.report.facts["dispute"] = {"return_id": card["return_id"], "link_id": link_id}
            return
        validate("link_dispute_request", body)
        result = self.post(
            f"/api/links/{link['link_id']}/dispute", "link_dispute_response", body, actor="sara"
        )
        check(result["link"]["disputed"], "the link is disputed")
        recompute = result["recompute"]
        check(recompute is not None and recompute["trigger"] == "link_disputed", "recomputed")
        before, after = recompute["before"], recompute["after"]
        decision = ("quadrant", "lever_ids")
        changed = [before[k] for k in decision] != [after[k] for k in decision]
        if card["customer_id"] in ROSSI_CARD_ACCOUNTS:
            check(after["quadrant"] not in RESTRICTED, "the family is never restricted")
        if chosen is not None and link_id == chosen["link_id"]:
            check(
                (after["cluster_size"] or 1) == chosen["cluster_size_after"],
                "the cluster the demo cases announced",
            )
            check(changed == chosen["changes_decision"], "the decision change it announced")
        new = recompute["decision"]
        check(new["version"] == card["decision"]["version"] + 1, "a new version (F7)")
        check(new["supersedes"] == card["decision"]["decision_id"], "it supersedes the shown one")
        shown = self.card(card["return_id"])
        check(shown["decision"]["decision_id"] == new["decision_id"], "the card shows it")
        check("link_disputed" in self.audit_actions(card["return_id"]), "in the audit log")
        self.report.facts["dispute"] = {
            "return_id": card["return_id"],
            "link_id": link["link_id"],
            "affected_return_ids": result["affected_return_ids"],
            "before": {k: before[k] for k in ("quadrant", "cluster_size", "requires_approval")},
            "after": {k: after[k] for k in ("quadrant", "cluster_size", "requires_approval")},
            "decision_changed": changed,
        }

    def agent(self) -> None:
        answers = []
        if self.mode == "cached":  # the GPU is off: the contract's 503 (H35)
            body = {
                "return_id": self.ring["return_id"],
                "question": QUESTIONS[0],
                "lang": "en",
                "asked_by": "sara",
            }
            off = self.call("POST", "/api/agent/ask", "error", json=body, expect=503, actor="sara")
            check(off.json()["code"] == "llm_unavailable", "llm_unavailable in cached mode")
            self.report.facts["agent"] = "503 llm_unavailable: the GPU is off in cached mode"
            return
        for question in QUESTIONS:
            body = {
                "return_id": self.ring["return_id"],
                "question": question,
                "lang": "en",
                "asked_by": "sara",
            }
            validate("agent_ask_request", body)
            response = self.call("POST", "/api/agent/ask", None, json=body, actor="sara")
            check(response.headers["content-type"].startswith("text/event-stream"), "an SSE stream")
            events = parse_sse(response.text)
            validate("agent_stream", events)
            kinds = [e["type"] for e in events]
            check(kinds.count("answer") == 1 and "refusal" not in kinds, f"answered: {kinds}")
            check(kinds[-1] == "done", "the stream ends with done")
            check(kinds.count("tool_call") <= 5, "at most five read-only steps (US-701)")
            answer = next(e for e in events if e["type"] == "answer")
            check("same person" not in answer["text"].lower(), "linked accounts (rule 11)")
            answers.append(
                {
                    "question": question,
                    "tools": [e["tool"] for e in events if e["type"] == "tool_call"],
                    "refs": len(answer["refs"]),
                }
            )
        self.report.facts["agent"] = answers

    def manager(self) -> None:
        matrix = self.get(
            "/api/portfolio/matrix",
            "portfolio_matrix",
            actor="luca",
            headers={"Accept-Encoding": "gzip"},
        )
        inside = sum(q["customers"]["value"] for q in matrix["quadrants"])
        check(inside > 0 and len(matrix["points"]) >= inside, "every scored customer a point")
        ring_point = next(
            p for p in matrix["points"] if p["customer_id"] == self.ring["customer_id"]
        )
        check(ring_point["quadrant"] == "Q4", "the ring account is in Q4 on the matrix")
        assumptions = self.get("/api/simulator/assumptions", "simulator_assumptions", actor="luca")
        current = assumptions["current"]
        check(current["net_benefit"]["total"]["value"] == 368.0, "ECO-01: 368 EUR")
        scenarios = {x["id"]: x["net_benefit"]["total"]["value"] for x in current["sensitivity"]}
        expected = {"base": 368.0, "logistics_halved": 48.0, "abandonment_doubled": 128.0}
        check(scenarios == {**expected, "both": -192.0}, f"ECO-01 scenarios {scenarios}")
        decided = date.fromisoformat(self.report.facts["giulia"]["decided_on"])
        week = decided - timedelta(days=decided.weekday())  # the week of the reset
        kpi = self.get("/api/kpi", "kpi", params={"week_start": week.isoformat()}, actor="luca")
        check(kpi["decisions"]["total"]["value"] > 0, "the reset week has decisions")
        fairness = self.get("/api/fairness", "fairness", actor="luca")
        check(bool(fairness["dimensions"]), "the fairness report has dimensions (US-605)")
        page = self.get("/api/audit", "audit_page", params={"page_size": 1}, actor="luca")
        exported = self.call("GET", "/api/audit", None, params={"format": "csv"}, actor="luca")
        check(exported.headers["content-type"].startswith("text/csv"), "a CSV export")
        rows = list(csv.reader(io.StringIO(exported.text)))
        check(tuple(rows[0]) == s.AUDIT_CSV_COLUMNS, "the CSV columns of the contract")
        check(len(rows) - 1 == page["total"], "the CSV holds the whole log")
        telemetry = self.get("/api/telemetry", "telemetry", actor="luca")
        benchmarks = self.get("/api/benchmarks", "benchmarks", actor="luca")
        self.report.facts["manager"] = {
            "matrix_points": len(matrix["points"]),
            "quadrants": {q["quadrant"]: q["customers"]["value"] for q in matrix["quadrants"]},
            "simulator": scenarios,
            "kpi_week": week.isoformat(),
            "kpi_decisions": kpi["decisions"]["total"]["value"],
            "fairness_dimensions": [
                (d["dimension"], d["max_gap"]["value"], d["warning"])
                for d in fairness["dimensions"]
            ],
            "audit_rows": len(rows) - 1,
            "telemetry_mode": telemetry["mode"],
            "benchmarks": {b["id"]: b["status"] for b in benchmarks["items"]},
        }

    def steps(self) -> Iterator[tuple[str, Callable[[], None]]]:
        yield "readiness", self.readiness
        yield "demo_cases", self.demo_cases
        yield "queue", self.queue_load
        yield "giulia", self.giulia
        yield "marco", self.marco
        yield "checkout", self.checkout
        yield "rossi", self.rossi
        yield "elena", self.elena
        yield "insufficient", self.insufficient
        yield "dispute", self.dispute
        yield "agent", self.agent
        yield "manager", self.manager

    def run(self, *, stop_on_failure: bool = False) -> Report:
        for name, step in self.steps():
            self.step = name
            try:
                step()
            except (WalkError, KeyError, StopIteration, ValueError, httpx.HTTPError) as exc:
                self.report.failed[name] = f"{type(exc).__name__}: {exc}"[:1000]
                if stop_on_failure or name in ("readiness", "queue", "marco"):
                    break  # the later steps need these
                continue
            self.report.passed.append(name)
        self.latency_checks()
        return self.report

    def latency_checks(self) -> None:
        queue = self.report.latency("queue", "/api/returns")
        checkout = self.report.latency("checkout", "/api/checkout/evaluate")
        self.report.facts["targets"] = {
            "queue_first_page_s": _first_call(self.report, "queue", "/api/returns"),
            "queue_limit_s": QUEUE_LIMIT_S,
            "checkout_evaluate_s": checkout.get("max_s"),
            "checkout_limit_s": CHECKOUT_LIMIT_S,
            "queue_pages": queue,
        }
        first = self.report.facts["targets"]["queue_first_page_s"]
        if first is not None and first >= QUEUE_LIMIT_S:
            self.report.failed["queue_latency"] = f"first page in {first:.3f} s (US-301: < 2 s)"
        if checkout and checkout["max_s"] >= CHECKOUT_LIMIT_S:
            self.report.failed["checkout_latency"] = (
                f"evaluate in {checkout['max_s']:.3f} s (US-403: < 1 s)"
            )


def _stored(decision: Mapping[str, Any]) -> dict[str, Any]:
    """A decision view as stored: without as_of, the instant it was served at."""
    return {k: v for k, v in decision.items() if k != "as_of"}


def _first_call(report: Report, step: str, prefix: str) -> float | None:
    for c in report.calls:
        if c.step == step and c.path.startswith(prefix):
            return round(c.seconds, 3)
    return None


def _count(items: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        name = str(item[key]) if item[key] is not None else "none"
        out[name] = out.get(name, 0) + 1
    return dict(sorted(out.items()))


def walk(client: httpx.Client, *, stop_on_failure: bool = False) -> Report:
    """Walk the golden demo path on the API behind `client`; the report says what passed."""
    return Walk(client).run(stop_on_failure=stop_on_failure)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m scripts.demo_walk", description=__doc__)
    p.add_argument("--base-url", default="http://127.0.0.1:8000", help="the running API")
    p.add_argument("--report", type=Path, help="write the JSON report here")
    p.add_argument("--timeout", type=float, default=60.0, help="seconds per request")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    # trust_env=False: the API is local, never behind the egress proxy (rule 2)
    with httpx.Client(base_url=args.base_url, timeout=args.timeout, trust_env=False) as client:
        report = walk(client)
    summary = report.summary()
    text = json.dumps(summary, indent=2, sort_keys=False, default=str)
    if args.report is not None:
        args.report.write_text(text + "\n", encoding="utf-8")
    brief = {k: summary[k] for k in ("passed", "failed")}
    brief["targets"] = summary["facts"].get("targets")
    print(json.dumps(brief, indent=2, default=str))
    return 0 if not report.failed else 1


if __name__ == "__main__":
    sys.exit(main())
