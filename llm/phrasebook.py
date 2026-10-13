"""The customer phrasebook: reviewed connective sentences chosen by id (ADR-14, adversarial review
of 13 Oct, round 2, finding R1).

Gemma does not write the customer message. It picks the ids of fixed sentences, and code writes
the message in Italian and in English:

    opening + mandatory body + up to three extras + closing (+ the fixed legal clause)

- Each sentence has a stable id, fixed Italian and English texts, a slot (opening, extra or
  closing) and the decision kinds where it is allowed (guarantee, insufficient, Q1 to Q4), with
  lever conditions: the size sentence is not offered when the free size exchange is withdrawn.
- The sentences are warm and neutral, never a judgement (spec 16). They hold no number, amount,
  deadline, term, legal wording or decision word: those come from code. The tests run every
  text check of llm/text.py on each sentence, in both languages (`phrase_problems`).
- The mandatory body is built by code from the engine decision and is never chosen by the
  model: the return registration with {deadline} (and {amount} when known), the {terms}
  sentence when the terms of future orders change, the unchanged-terms sentence for
  insufficient evidence, the transparency notice (Q3, Q4) and the guarantee registration.
- `selection_problems` lists why a selection is refused: an unknown or repeated id, an id in the
  wrong slot, or an id not allowed for the decision. The validator reports them as SENTENCE_ID.
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
    foreign_letters,
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
) -> Sentence:
    return Sentence(id, slot, it, en, kinds, frozenset(unless.split()), frozenset(needs.split()))


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

_REGISTERED: dict[Lang, str] = {
    "it": "Abbiamo registrato la tua richiesta di reso e il rimborso è previsto per il {deadline}.",
    "en": "We have registered your return request and your refund is due by {deadline}.",
}
_REGISTERED_AMOUNT: dict[Lang, str] = {
    "it": (
        "Abbiamo registrato la tua richiesta di reso e il rimborso di {amount} è previsto per il "
        "{deadline}."
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
    "restrictive": {
        "it": (
            "I tuoi prossimi ordini avranno nuove condizioni di reso, mostrate prima di ogni "
            "acquisto: {terms}."
        ),
        "en": "Your next orders will have new return terms, shown before each purchase: {terms}.",
    },
    "other": {
        "it": "Per i tuoi prossimi ordini le condizioni di reso sono queste: {terms}.",
        "en": "For your next orders the return terms are these: {terms}.",
    },
}
# US-307: insufficient evidence means standard terms, neither safe nor suspicious.
_UNCHANGED: dict[Lang, str] = {
    "it": "Le condizioni di reso dei tuoi ordini restano quelle di sempre.",
    "en": "The return terms of your orders stay the same as always.",
}
# The transparency notice of spec 8.6 (Q3, Q4): neutral, never a judgement.
_NOTICE: dict[Lang, str] = {
    "it": "Queste condizioni tengono conto dei resi dei tuoi ordini precedenti.",
    "en": "These terms take into account the returns on your previous orders.",
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


def selection_problems(ids: Sequence[str], ctx: MessageContext) -> list[str]:
    """Why a selection of sentence ids cannot be used; empty when it can."""
    problems: list[str] = []
    if not MIN_SENTENCES <= len(ids) <= MAX_SENTENCES:
        problems.append(f"{len(ids)} ids, expected {MIN_SENTENCES} to {MAX_SENTENCES}")
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
    return problems


def mandatory_body(ctx: MessageContext, lang: Lang) -> list[str]:
    """The sentences code writes for the decision, with placeholders; never chosen by the
    model."""
    if ctx.kind == "guarantee":
        return [_GUARANTEE_REGISTERED[lang]]
    registered = _REGISTERED_AMOUNT if "amount" in ctx.placeholders else _REGISTERED
    body = [registered[lang]]
    if "terms" in ctx.placeholders:
        style = "restrictive" if ctx.requires_terms else "Q1" if ctx.kind == "Q1" else "other"
        body.append(_TERMS[style][lang])
    elif ctx.kind == "insufficient":
        body.append(_UNCHANGED[lang])
    if "transparency_notice:true" in ctx.lever_ids:
        body.append(_NOTICE[lang])
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
    terms or decision wording, no banned term, no letter of another script, not the other
    language. Placeholders are removed first. Run by the tests on every phrasebook sentence,
    every body sentence and every pair of parts that can stand side by side in a message."""
    body = strip_placeholders(text)
    checks = (
        ("LITERAL_NUMBER", literal_numbers(body, lang)),
        ("LITERAL_UNIT", literal_units(body)),
        ("LEGAL_WORDING", legal_wording(body)),
        ("TERMS_WORDING", terms_wording(body)),
        ("BANNED_TERM", banned_terms(body)),
        ("SCRIPT", foreign_letters(body)),
    )
    problems = [(check, ", ".join(found)) for check, found in checks if found]
    detected = language_of(body)
    if detected not in (lang, None):
        problems.append(("WRONG_LANGUAGE", f"detected {detected}"))
    return problems
