"""The customer phrasebook: reviewed connective sentences chosen by id (ADR-14, adversarial review
of 13 Oct, round 2, finding R1).

Gemma does not write the customer message. It picks the ids of fixed sentences, and code writes
the message in Italian and in English:

    opening + mandatory body + up to three extras (two in Q3 and Q4) + closing (+ legal clause)

- Each sentence has a stable id, fixed Italian and English texts, a slot (opening, extra or
  closing) and the decision kinds where it is allowed (guarantee, insufficient, Q1 to Q4), with
  lever conditions: the size sentence is not offered when the free size exchange is withdrawn.
- The sentences are warm and neutral, never a judgement (spec 16). They hold no number, amount,
  deadline, term, legal wording or decision word: those come from code. The tests run every
  text check of llm/text.py on each sentence, in both languages (`phrase_problems`).
- The mandatory body is built by code from the engine decision and is never chosen by the
  model: the return registration with {deadline} (and {amount} when known), in Q3 and Q4 the
  sentence that this return keeps the terms of its order (rule 3, spec 8.6; round 4, finding
  U2), with the free size exchange when the decision grants it, since it holds for the current
  return too (round 5, findings V7 and V8), the {terms} sentence when the terms of future orders
  change, the standard-terms sentence for insufficient evidence, the transparency notice (Q3,
  Q4) and the guarantee registration. In Q3 and Q4, {terms} lists only restrictions (the free
  size exchange is in the current-return sentence), introduced as "these return terms", not
  "new" ones: code compares with the merchant base, not with the customer's own order (finding
  V9). The {terms} sentence presents new terms as a consequence of past returns only when a
  restrictive lever has a phrase (round 4, finding U4). The notice always refers to future
  orders: after the restrictive {terms} sentence it says "these terms", otherwise it names the
  next orders, so it never reads as if the current refund depended on past returns (rule 3;
  round 3, finding T1).
- `selection_problems` lists why a selection is refused: an unknown or repeated id, an id in the
  wrong slot, an id not allowed for the decision, two sentences that ask for a reply, a "thanks
  again" closing without a thanks opening (round 3, finding T4), or more than two extras in Q3
  and Q4 (round 4, finding U2). The validator reports
  them as SENTENCE_ID.
- `render_selection` composes the message and fills it through llm/messages.render, which
  appends the legal clause the decision requires and checks it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from contracts.levers import format_lever_id
from llm.decision import ExpectedDecision, serialize_decision, template_key
from llm.messages import MessageError, render, strip_placeholders
from llm.text import (
    Lang,
    banned_terms,
    foreign_characters,
    language_of,
    legal_wording,
    literal_numbers,
    literal_units,
    terms_wording,
)
from policy.types import Decision, ReturnTerms

Slot = Literal["opening", "extra", "closing"]
SLOTS: tuple[Slot, ...] = ("opening", "extra", "closing")
MIN_SENTENCES = 2  # one opening and one closing
MAX_EXTRAS = 3
MAX_SENTENCES = MIN_SENTENCES + MAX_EXTRAS
# Q3 and Q4 carry one more body sentence (this return keeps its terms), so they take at most two
# extras and stay within 120 words before the clause (round 4, finding U2).
RESTRICTIVE_KINDS = frozenset({"Q3", "Q4"})
RESTRICTIVE_MAX_EXTRAS = 2

WITHDRAWAL_KINDS = frozenset({"insufficient", "Q1", "Q2", "Q3", "Q4"})
GUARANTEE_KINDS = frozenset({"guarantee"})
ALL_KINDS = WITHDRAWAL_KINDS | GUARANTEE_KINDS


@dataclass(frozen=True)
class Sentence:
    id: str
    slot: Slot
    it: str
    en: str
    kinds: frozenset[str] = ALL_KINDS
    unless_levers: frozenset[str] = frozenset()  # lever ids that rule the sentence out
    needs: frozenset[str] = frozenset()  # placeholders the decision must have
    group: str = ""  # at most one sentence of a group in a selection
    after: frozenset[str] = frozenset()  # one of these ids must come before the sentence

    def text(self, lang: Lang) -> str:
        return self.it if lang == "it" else self.en


def _sentence(
    id: str,
    slot: Slot,
    it: str,
    en: str,
    kinds: frozenset[str] = ALL_KINDS,
    *,
    unless: str = "",
    needs: str = "",
    group: str = "",
    after: str = "",
) -> Sentence:
    return Sentence(
        id,
        slot,
        it,
        en,
        kinds,
        frozenset(unless.split()),
        frozenset(needs.split()),
        group,
        frozenset(after.split()),
    )


REPLY = "reply"  # the group of the sentences that ask the customer to reply
THANKS_OPENINGS = "OPEN_THANKS OPEN_MESSAGE OPEN_CHOICE"


SENTENCES: tuple[Sentence, ...] = (
    # ---- openings
    _sentence(
        "OPEN_THANKS",
        "opening",
        "Ciao, grazie per averci scritto.",
        "Hello, thank you for writing to us.",
    ),
    _sentence(
        "OPEN_MESSAGE",
        "opening",
        "Ciao, grazie per il tuo messaggio.",
        "Hello, thank you for your message.",
    ),
    _sentence(
        "OPEN_CHOICE",
        "opening",
        "Ciao, grazie per aver scelto i nostri capi.",
        "Hello, thank you for choosing our clothes.",
        WITHDRAWAL_KINDS,
    ),
    _sentence(
        "OPEN_SORRY_FIT",
        "opening",
        "Ciao, ci dispiace che il capo non sia andato come speravi.",
        "Hello, we are sorry the item did not work out for you.",
        WITHDRAWAL_KINDS,
    ),
    _sentence(
        "OPEN_SORRY_ITEM",
        "opening",
        "Ciao, ci dispiace per il problema con il prodotto che hai ricevuto.",
        "Hello, we are sorry about the problem with the item you received.",
        GUARANTEE_KINDS,
    ),
    # ---- extras
    _sentence(
        "HELP_SIZE",
        "extra",
        "Se ti serve un'altra taglia, rispondi pure a questo messaggio.",
        "If you need a different size, just reply to this message.",
        WITHDRAWAL_KINDS,
        unless="free_size_exchange:false",
        group=REPLY,
    ),
    _sentence(
        "HELP_ADVICE",
        "extra",
        "Quando acquisti, trovi anche un consiglio sulla taglia.",
        "When you shop, you will also find advice on your size.",
        WITHDRAWAL_KINDS,
        unless="size_advice:false",
    ),
    _sentence(
        "CONTACT_REPLY",
        "extra",
        "Per qualsiasi domanda, rispondi pure a questo messaggio.",
        "For any questions, just reply to this message.",
        group=REPLY,
    ),
    _sentence(
        "CONTACT_TEAM",
        "extra",
        "Il nostro team ti risponderà volentieri.",
        "Our team will be glad to help you.",
    ),
    _sentence(
        "INFO_TERMS",
        "extra",
        "Per domande su queste condizioni, rispondi pure a questo messaggio.",
        "Questions about these terms? Just reply to this message.",
        WITHDRAWAL_KINDS,
        needs="terms",
        group=REPLY,
    ),
    _sentence(
        "GUARANTEE_CARE",
        "extra",
        "Seguiremo la tua segnalazione con attenzione.",
        "We will follow up on your report with care.",
        GUARANTEE_KINDS,
    ),
    _sentence(
        "GUARANTEE_PHOTO",
        "extra",
        "Se puoi, rispondi con una foto del problema: ci aiuta a seguirti meglio.",
        "If you can, reply with a photo of the problem: it helps us assist you.",
        GUARANTEE_KINDS,
        group=REPLY,
    ),
    _sentence(
        "THANKS_FEEDBACK",
        "extra",
        "I tuoi commenti ci aiutano a migliorare i nostri capi.",
        "Your comments help us improve our clothes.",
    ),
    # ---- closings
    _sentence(
        "CLOSE_REGARDS", "closing", "Un caro saluto dal nostro team.", "Warm regards from our team."
    ),
    _sentence("CLOSE_VALUED", "closing", "Teniamo molto a te.", "You matter to us."),
    _sentence(
        "CLOSE_HAPPY",
        "closing",
        "Siamo felici di averti tra i nostri clienti.",
        "We are happy to have you with us.",
        WITHDRAWAL_KINDS,
    ),
    _sentence(
        "CLOSE_DAY",
        "closing",
        "Grazie ancora e buona giornata.",
        "Thanks again, and have a nice day.",
        after=THANKS_OPENINGS,
    ),
    _sentence("CLOSE_SOON", "closing", "A presto!", "See you soon!", WITHDRAWAL_KINDS),
    _sentence(
        "CLOSE_PATIENCE",
        "closing",
        "Grazie per la pazienza e per averci avvisato.",
        "Thank you for your patience and for letting us know.",
        GUARANTEE_KINDS,
    ),
)
PHRASEBOOK: dict[str, Sentence] = {s.id: s for s in SENTENCES}
SENTENCE_IDS: tuple[str, ...] = tuple(PHRASEBOOK)

# ---- The mandatory body, written by code from the engine decision ------------------------------

# The deadline is a limit in both languages: "al più tardi per il", "by" (round 3, finding T2).
_REGISTERED: dict[Lang, str] = {
    "it": (
        "Abbiamo registrato la tua richiesta di reso e il rimborso è previsto al più tardi per il "
        "{deadline}."
    ),
    "en": "We have registered your return request and your refund is due by {deadline}.",
}
_REGISTERED_AMOUNT: dict[Lang, str] = {
    "it": (
        "Abbiamo registrato la tua richiesta di reso e il rimborso di {amount} è previsto al più "
        "tardi per il {deadline}."
    ),
    "en": (
        "We have registered your return request and your refund of {amount} is due by {deadline}."
    ),
}
_GUARANTEE_REGISTERED: dict[Lang, str] = {
    "it": "Abbiamo registrato la tua segnalazione e ti scriveremo presto con i prossimi passi.",
    "en": "We have registered your report and we will write to you soon with the next steps.",
}
_TERMS: dict[str, dict[Lang, str]] = {
    "Q1": {
        "it": "Per i tuoi prossimi ordini ti offriamo condizioni di reso migliori: {terms}.",
        "en": "For your next orders we are glad to offer you better return terms: {terms}.",
    },
    # The decision, without a comparison code does not make: {terms} is computed against the
    # merchant base, not the customer's own order, so the terms are "these", not "new" (round 5,
    # finding V9).
    "restrictive": {
        "it": (
            "I tuoi prossimi ordini avranno queste condizioni di reso, mostrate prima "
            "dell'acquisto: {terms}."
        ),
        "en": "Your next orders will have these return terms, shown before each purchase: {terms}.",
    },
    "other": {
        "it": "Per i tuoi prossimi ordini le condizioni di reso sono queste: {terms}.",
        "en": "For your next orders the return terms are these: {terms}.",
    },
}
# US-307: insufficient evidence means standard terms, neither safe nor suspicious. The message
# states the engine decision, not a comparison with the order's snapshot that code does not make:
# "have", never "keep" (round 3, finding T3; round 4, finding U3).
_STANDARD: dict[Lang, str] = {
    "it": "I tuoi prossimi ordini hanno le nostre condizioni di reso standard.",
    "en": "Your next orders have our standard return terms.",
}
# Q3 and Q4: the current return keeps the terms shown when the order was bought (rule 3, spec
# 8.6, LEG-04), so the new terms are never read as applying to it (round 4, finding U2).
_KEEPS: dict[Lang, str] = {
    "it": "Questo reso mantiene le condizioni del tuo ordine.",
    "en": "This return keeps the terms of your order.",
}
# The free size exchange of a Q3 or Q4 decision holds for every order, the current one included
# (scope all_orders, policy/engine.with_all_orders_levers), so the current-return sentence says
# so and {terms} leaves it out: it is never read as a next-order term caused by past returns, nor
# as missing from this return (round 5, findings V7 and V8). This clause is code text, like a
# template field: `phrase_problems` reads it as one.
SIZE_EXCHANGE_NOW: dict[Lang, str] = {
    "it": ", e il cambio taglia è gratuito",
    "en": ", and the size exchange is free",
}
_KEEPS_EXCHANGE: dict[Lang, str] = {
    lang: _KEEPS[lang].removesuffix(".") + SIZE_EXCHANGE_NOW[lang] + "." for lang in _KEEPS
}
_FREE_SIZE_EXCHANGE = "free_size_exchange:true"
# Levers whose {terms} phrase restricts. In a decision that requires terms (Q3, Q4) the window
# lever only shortens the window (policy/engine.levers_for); a free or priority size exchange is a
# benefit that holds for every order (round 4, finding U4).
_RESTRICTIVE_TERMS = (
    "return_window_days:",
    "free_return_shipping:false",
    "instant_refund:false",
    "coupons_enabled:false",
    "cluster_coupons_disabled:true",
)
# The transparency notice of spec 8.6 (Q3, Q4): neutral, never a judgement, and about future
# orders only. Right after the {terms} sentence, "these terms" are the terms of the next orders;
# alone (the base already lacks what Q3 or Q4 would withdraw) it names the next orders, so it
# never reads as if the current refund depended on past returns (rule 3; round 3, finding T1).
# Round 5: "your previous returns", shorter, so Q3 and Q4 keep 120 words with the free size
# exchange in the current-return sentence.
_NOTICE_AFTER_TERMS: dict[Lang, str] = {
    "it": "Queste condizioni tengono conto dei tuoi resi precedenti.",
    "en": "These terms take into account your previous returns.",
}
_NOTICE: dict[Lang, str] = {
    "it": "Le condizioni di reso dei tuoi prossimi ordini tengono conto dei tuoi resi precedenti.",
    "en": "The return terms of your next orders take into account your previous returns.",
}


@dataclass(frozen=True)
class MessageContext:
    """What the phrasebook needs to know about the engine decision."""

    kind: str  # guarantee, insufficient, Q1, Q2, Q3 or Q4
    lever_ids: frozenset[str]
    placeholders: frozenset[str]
    requires_terms: bool


def context_of(expected: ExpectedDecision) -> MessageContext:
    return MessageContext(
        expected.template_key,
        frozenset(expected.lever_ids),
        frozenset(expected.placeholders),
        expected.requires_terms,
    )


def context_of_case(case: Mapping[str, Any]) -> MessageContext:
    """The same context read from an llm_case (the mock model and the prompt only see the case;
    the validator checks first that the case says what the engine decided)."""
    return MessageContext(
        template_key(case["path"], case["evidence_quality"], case["quadrant"]),
        frozenset(format_lever_id(x["id"], x["value"]) for x in case["levers"]),
        frozenset(case["placeholders"]["available"]),
        "terms" in case["placeholders"]["required"],
    )


def allowed(sentence: Sentence, ctx: MessageContext) -> bool:
    return (
        ctx.kind in sentence.kinds
        and not sentence.unless_levers & ctx.lever_ids
        and sentence.needs <= ctx.placeholders
    )


def allowed_sentences(ctx: MessageContext) -> list[Sentence]:
    """The sentences the model may choose for this decision, in phrasebook order."""
    return [s for s in SENTENCES if allowed(s, ctx)]


def _slot_at(index: int, count: int) -> Slot:
    if index == 0:
        return "opening"
    return "closing" if index == count - 1 else "extra"


def max_extras(ctx: MessageContext) -> int:
    """The number of extras a selection may have: two in Q3 and Q4, three otherwise."""
    return RESTRICTIVE_MAX_EXTRAS if ctx.kind in RESTRICTIVE_KINDS else MAX_EXTRAS


def selection_problems(ids: Sequence[str], ctx: MessageContext) -> list[str]:
    """Why a selection of sentence ids cannot be used; empty when it can."""
    problems: list[str] = []
    if not MIN_SENTENCES <= len(ids) <= MAX_SENTENCES:
        problems.append(f"{len(ids)} ids, expected {MIN_SENTENCES} to {MAX_SENTENCES}")
    elif len(ids) - MIN_SENTENCES > max_extras(ctx):
        problems.append(f"{len(ids) - MIN_SENTENCES} extras, at most {max_extras(ctx)} here")
    for index, sentence_id in enumerate(ids):
        sentence = PHRASEBOOK.get(sentence_id)
        if sentence is None:
            problems.append(f"{sentence_id}: unknown")
            continue
        if sentence_id in ids[:index]:
            problems.append(f"{sentence_id}: repeated")
        slot = _slot_at(index, len(ids))
        if sentence.slot != slot:
            problems.append(f"{sentence_id}: a {sentence.slot}, used as the {slot}")
        if not allowed(sentence, ctx):
            problems.append(f"{sentence_id}: not allowed for this decision")
        problems += _coherence_problems(sentence, ids[:index])
    return problems


def _coherence_problems(sentence: Sentence, before: Sequence[str]) -> list[str]:
    """At most one sentence of a group (one request to reply), and a sentence that needs another
    one before it ("Thanks again" after a thanks opening): round 3, finding T4."""
    problems: list[str] = []
    if sentence.group:
        same = [
            i
            for i in before
            if i != sentence.id and i in PHRASEBOOK and PHRASEBOOK[i].group == sentence.group
        ]
        if same:
            problems.append(
                f"{sentence.id}: one {sentence.group} sentence only, {same[0]} is there"
            )
    if sentence.after and not sentence.after & set(before):
        problems.append(f"{sentence.id}: needs {' or '.join(sorted(sentence.after))} before it")
    return problems


def selection_rules(ctx: MessageContext) -> list[str]:
    """The coherence rules of `selection_problems` for the sentences allowed in a context, in
    words, for the prompt."""
    ids = [s.id for s in allowed_sentences(ctx)]
    rules: list[str] = []
    for group in sorted({PHRASEBOOK[i].group for i in ids} - {""}):
        members = [i for i in ids if PHRASEBOOK[i].group == group]
        if len(members) > 1:
            rules.append(f"At most one of {', '.join(members)}.")
    for sentence_id in ids:
        after = [i for i in ids if i in PHRASEBOOK[sentence_id].after]
        if PHRASEBOOK[sentence_id].after:
            rules.append(f"{sentence_id} only after {' or '.join(after)}.")
    return rules


def _terms_style(ctx: MessageContext) -> str:
    """The {terms} sentence: restrictive only when a restrictive lever has a phrase."""
    restricts = any(lever.startswith(_RESTRICTIVE_TERMS) for lever in ctx.lever_ids)
    if ctx.requires_terms and restricts:
        return "restrictive"
    return "Q1" if ctx.kind == "Q1" else "other"


def mandatory_body(ctx: MessageContext, lang: Lang) -> list[str]:
    """The sentences code writes for the decision, with placeholders; never chosen by the
    model."""
    if ctx.kind == "guarantee":
        return [_GUARANTEE_REGISTERED[lang]]
    registered = _REGISTERED_AMOUNT if "amount" in ctx.placeholders else _REGISTERED
    body = [registered[lang]]
    if ctx.kind in RESTRICTIVE_KINDS:
        keeps = _KEEPS_EXCHANGE if _FREE_SIZE_EXCHANGE in ctx.lever_ids else _KEEPS
        body.append(keeps[lang])
    style = _terms_style(ctx) if "terms" in ctx.placeholders else ""
    if style:
        body.append(_TERMS[style][lang])
    elif ctx.kind == "insufficient":
        body.append(_STANDARD[lang])
    if "transparency_notice:true" in ctx.lever_ids:
        notice = _NOTICE_AFTER_TERMS if style == "restrictive" else _NOTICE
        body.append(notice[lang])
    return body


def compose(ids: Sequence[str], ctx: MessageContext, lang: Lang) -> str:
    """The message text with placeholders: opening, mandatory body, extras and closing. Raises
    MessageError for a selection that `selection_problems` refuses."""
    problems = selection_problems(ids, ctx)
    if problems:
        raise MessageError("; ".join(problems))
    opening, *extras, closing = (PHRASEBOOK[i].text(lang) for i in ids)
    return " ".join([opening, *mandatory_body(ctx, lang), *extras, closing])


def render_selection(
    ids: Sequence[str],
    decision: Decision,
    base: ReturnTerms,
    lang: Lang,
    refund_eur: Decimal | None = None,
) -> str:
    """The final customer message: the composed text, its placeholders filled from code fields
    and the legal clause the decision requires (llm/messages.render)."""
    _, expected = serialize_decision(decision, base, refund_eur)
    return render(compose(ids, context_of(expected), lang), decision, base, lang, refund_eur)


# ---- The review gate: every text check of llm/text.py ------------------------------------------


def phrase_problems(text: str, lang: Lang) -> list[tuple[str, str]]:
    """The checks a customer text must pass, as (check, found) pairs: no number or unit, no legal,
    terms or decision wording, no banned term, no character outside the allowlist, not the other
    language. Placeholders, and the code clause of the free size exchange of the current return
    (`SIZE_EXCHANGE_NOW`), are removed first. Run by the tests on every phrasebook sentence,
    every body sentence and every pair of parts that can stand side by side in a message."""
    body = strip_placeholders(text)
    for clause in SIZE_EXCHANGE_NOW.values():
        body = body.replace(clause, " ")
    checks = (
        ("LITERAL_NUMBER", literal_numbers(body, lang)),
        ("LITERAL_UNIT", literal_units(body)),
        ("LEGAL_WORDING", legal_wording(body)),
        ("TERMS_WORDING", terms_wording(body)),
        ("BANNED_TERM", banned_terms(body)),
        ("SCRIPT", foreign_characters(body)),
    )
    problems = [(check, ", ".join(found)) for check, found in checks if found]
    detected = language_of(body)
    if detected not in (lang, None):
        problems.append(("WRONG_LANGUAGE", f"detected {detected}"))
    return problems
