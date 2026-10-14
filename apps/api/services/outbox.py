"""The simulated outbox (spec 4: no real email) and its writer (US-304; rules 10 and 11).

A message is composed by OutboxMessage.compose: llm.phrasebook.render_selection of phrasebook ids
for the decision as it stands, the template fields of llm.messages and the fixed clause of
llm/legal_clause.py. queue() checks it against its decision (check_outbox_message), stores it and
writes the message_queued audit entry, in the caller's transaction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.errors import conflict
from apps.api.services.audit import record
from apps.api.services.ids import allocate, message_id
from contracts.sql import outbox
from contracts.vocab import Lang


def deadline_passed(view: s.DecisionView, now: datetime) -> bool:
    """Whether a message about `view` would state a refund deadline that has already passed
    (rule 3; 1.5.0, H1). A guarantee case states none (its message has no {deadline} field:
    rule 4, NFR-17), so its deadline never passes (1.6.0)."""
    return view.refund_due_by is not None and s.utc_day(now) > view.refund_due_by


def check_refund_due(view: s.DecisionView, now: datetime) -> None:
    """409 when a message about `view` would state a refund deadline that has already passed
    (deadline_passed): the refund is overdue and the case needs the merchant's attention, not
    a message promising a past date. The queue shows refund_overdue."""
    if deadline_passed(view, now):
        assert view.refund_due_by is not None
        raise conflict(
            f"the refund of {view.return_id} was due by {view.refund_due_by.isoformat()} (14 days "
            "from the notice, rule 3): it is overdue; no message states a past deadline"
        )


async def next_message_id(conn: AsyncConnection) -> str:
    """MSG-<n>: the next number of the outbox counter, in the caller's transaction (H10)."""
    return message_id(await allocate(conn, "MSG", outbox))


async def compose(
    conn: AsyncConnection,
    view: s.DecisionView,
    *,
    lang: Lang,
    created_by: str,
    created_at: datetime,
    sentence_ids: Sequence[str] | None = None,
    preferred: Sequence[str] | None = None,
    generation_id: str | None = None,
    refund: s.Metric | None = None,
) -> s.OutboxMessage:
    """The message about `view` as it stands: the ids chosen, else the draft's selection when it
    is allowed for the decision (`preferred`), else the fallback of llm.templates."""
    decision, _ = s.to_policy_decision(view)
    base = view.inputs.base_terms.to_policy()
    amount = Decimal(str(refund.value)) if refund is not None else None
    ids = (
        list(sentence_ids)
        if sentence_ids
        else s.default_selection_for(decision, base, preferred, amount)
    )
    return s.OutboxMessage.compose(
        view,
        message_id=await next_message_id(conn),
        lang=lang,
        sentence_ids=ids,
        created_by=created_by,
        created_at=created_at,
        generation_id=generation_id,
        refund=refund,
    )


async def queue(
    conn: AsyncConnection,
    message: s.OutboxMessage,
    view: s.DecisionView,
    request_id: str | None = None,
) -> s.OutboxMessage:
    """Store a message about `view` and log it (message_queued)."""
    s.check_outbox_message(message, view)
    refund = message.refund
    await conn.execute(
        insert(outbox).values(
            message_id=message.message_id,
            return_id=message.return_id,
            decision_id=message.decision_id,
            customer_id=message.customer_id,
            lang=message.lang,
            subject=message.subject,
            sentence_ids=list(message.sentence_ids),
            body=message.body,
            legal_clause_kind=message.legal_clause_kind,
            template_fields=message.fields.model_dump(),
            refund_eur=None if refund is None else Decimal(str(refund.value)),
            refund_provenance=None if refund is None else refund.provenance.model_dump(mode="json"),
            generation_id=message.generation_id,
            status=message.status,
            created_by=message.created_by,
            created_at=message.created_at,
        )
    )
    await record(
        conn,
        at=message.created_at,
        actor="system",
        action="message_queued",
        entity_type="outbox_message",
        entity_id=message.message_id,
        return_id=message.return_id,
        decision_id=message.decision_id,
        after={"lang": message.lang},
        request_id=request_id,
    )
    return message


def message_of(row: Mapping[str, Any]) -> s.OutboxMessage:
    """The API view of an outbox row."""
    refund = None
    if row["refund_eur"] is not None:
        refund = s.Metric(
            value=float(row["refund_eur"]),
            unit="eur",
            provenance=s.Provenance.model_validate(row["refund_provenance"]),
        )
    return s.OutboxMessage(
        message_id=row["message_id"],
        return_id=row["return_id"],
        decision_id=row["decision_id"],
        customer_id=row["customer_id"],
        lang=row["lang"],
        subject=row["subject"],
        sentence_ids=list(row["sentence_ids"]),
        fields=s.TemplateFields.model_validate(row["template_fields"]),
        refund=refund,
        body=row["body"],
        legal_clause_kind=row["legal_clause_kind"],
        generation_id=row["generation_id"],
        status=row["status"],
        created_by=row["created_by"],
        created_at=row["created_at"],
    )
