"""Deterministic customer messages (ADR-08, CLAUDE.md rule 10, review findings LLM-8 and LLM-9).

Gemma writes connective text with placeholders; amounts, terms and deadlines are template fields
filled by code, and the legal clause is fixed text appended by code.

One placeholder grammar serves both the validator and `render`:
- the only tokens are {terms}, {amount}, {deadline} and {deduction};
- any other brace, bracket or angle character is rejected (also full-width forms, after NFKC);
- a placeholder is accepted only if its value is available for the decision;
- {terms} is required when the decision has restrictive levers and terms to show.

{deduction} (round 4 of the API review, finding E17) is the amount withheld for a
diminished-value claim. Only the mandatory body written by code uses it: the llm_case never offers
it, because the model chooses phrasebook ids and writes no customer text (ADR-14 decision 8).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from contracts.levers import LeverObject
from llm.legal_clause import append_clause, assert_clause, required_clause
from llm.text import Lang
from policy.legal_guard import is_restrictive
from policy.types import Decision, Path, ReturnTerms

Placeholder = Literal["terms", "amount", "deadline", "deduction"]
PLACEHOLDERS: tuple[Placeholder, ...] = ("terms", "amount", "deadline", "deduction")
_TOKEN_RE = re.compile(r"\{(terms|amount|deadline|deduction)\}")
_STRAY_RE = re.compile(r"[{}\[\]<>]")

PlaceholderProblemCode = Literal["PLACEHOLDER_SYNTAX", "PLACEHOLDER_UNAVAILABLE", "TERMS_MISSING"]


class MessageError(ValueError):
    """Text that breaks the placeholder grammar, or a field that cannot be filled."""


@dataclass(frozen=True)
class PlaceholderProblem:
    code: PlaceholderProblemCode
    detail: str


def placeholders_in(text: str) -> list[Placeholder]:
    """The allowed placeholders used in the text, in order of first use."""
    found: list[Placeholder] = []
    for name in _TOKEN_RE.findall(text):
        if name not in found:
            found.append(name)
    return found


def strip_placeholders(text: str) -> str:
    """The text with every allowed placeholder replaced by a space."""
    return _TOKEN_RE.sub(" ", text)


def placeholder_problems(
    text: str, available: Collection[str], requires_terms: bool
) -> list[PlaceholderProblem]:
    problems: list[PlaceholderProblem] = []
    rest = unicodedata.normalize("NFKC", strip_placeholders(text))
    stray = sorted(set(_STRAY_RE.findall(rest)))
    if stray:
        problems.append(PlaceholderProblem("PLACEHOLDER_SYNTAX", f"stray {''.join(stray)}"))
    unavailable = [name for name in placeholders_in(text) if name not in available]
    if unavailable:
        problems.append(PlaceholderProblem("PLACEHOLDER_UNAVAILABLE", ", ".join(unavailable)))
    if requires_terms and "terms" not in placeholders_in(text):
        problems.append(PlaceholderProblem("TERMS_MISSING", "{terms} is required"))
    return problems


# ---- Template fields -----------------------------------------------------------------------------

_WINDOW: dict[Lang, str] = {
    "it": "reso entro {days} giorni dalla consegna",
    "en": "returns within {days} days of delivery",
}
_BOOLEAN_TERMS: dict[tuple[str, bool], dict[Lang, str]] = {
    ("free_return_shipping", True): {
        "it": "spedizione del reso gratuita",
        "en": "free return shipping",
    },
    ("free_return_shipping", False): {
        "it": "spedizione del reso a tuo carico",
        "en": "return shipping at your expense",
    },
    ("instant_refund", True): {
        "it": "rimborso istantaneo quando il corriere prende in carico il pacco",
        "en": "instant refund when the carrier collects the parcel",
    },
    ("instant_refund", False): {
        "it": "rimborso nei tempi di legge, non istantaneo",
        "en": "refund within the legal time limit, not instant",
    },
    ("free_size_exchange", True): {"it": "cambio taglia gratuito", "en": "free size exchange"},
    ("free_size_exchange", False): {
        "it": "cambio taglia a tuo carico",
        "en": "size exchange at your expense",
    },
    ("coupons_enabled", True): {
        "it": "codici sconto utilizzabili",
        "en": "discount codes available",
    },
    ("coupons_enabled", False): {
        "it": "codici sconto non utilizzabili",
        "en": "discount codes not available",
    },
    ("cluster_coupons_disabled", True): {
        "it": "codici sconto non utilizzabili",
        "en": "discount codes not available",
    },
    ("priority_size_exchange", True): {
        "it": "cambio taglia con priorità",
        "en": "priority size exchange",
    },
}
# transparency_notice is the message itself; size_advice concerns the checkout of an order.
_MONTHS: dict[Lang, tuple[str, ...]] = {
    "it": (
        "gennaio",
        "febbraio",
        "marzo",
        "aprile",
        "maggio",
        "giugno",
        "luglio",
        "agosto",
        "settembre",
        "ottobre",
        "novembre",
        "dicembre",
    ),
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
}


# Lever values that restrict future orders (policy/legal_guard.is_restrictive): a shorter window
# is checked against the base.
_RESTRICTIVE_VALUES = frozenset(
    {
        ("free_return_shipping", False),
        ("instant_refund", False),
        ("coupons_enabled", False),
        ("cluster_coupons_disabled", True),
        ("transparency_notice", True),
    }
)


def restricts(levers: Sequence[LeverObject], base: ReturnTerms | None = None) -> bool:
    """The levers restrict future orders: a restrictive value, or a window shorter than the
    base (when the base is given)."""
    for lever in levers:
        if (lever["id"], lever["value"]) in _RESTRICTIVE_VALUES:
            return True
        window = base.return_window_days if base is not None else 0
        if lever["id"] == "return_window_days" and int(lever["value"]) < window:
            return True
    return False


def term_phrases(
    levers: Sequence[LeverObject], lang: Lang, base: ReturnTerms | None = None
) -> list[str]:
    """Fixed phrases for the future-order terms of the levers, without repeats. In a decision
    that restricts, a lever whose scope is all_orders (the free size exchange) holds for the
    current return too, so the current-return sentence states it and {terms} leaves it out; the
    cluster coupon block has no phrase when the base already has no discount codes (round 5,
    findings V7 and V8)."""
    restrictive = restricts(levers, base)
    phrases: list[str] = []
    for lever in levers:
        value = lever["value"]
        if restrictive and lever["scope"] == "all_orders":
            continue
        redundant = base is not None and not base.coupons_enabled
        if lever["id"] == "cluster_coupons_disabled" and redundant:
            continue
        if lever["id"] == "return_window_days":
            phrase: str | None = _WINDOW[lang].format(days=value)
        else:
            entry = _BOOLEAN_TERMS.get((lever["id"], bool(value)))
            phrase = entry[lang] if entry is not None else None
        if phrase is not None and phrase not in phrases:
            phrases.append(phrase)
    return phrases


def available_placeholders(
    path: str,
    levers: Sequence[LeverObject],
    refund_known: bool,
    base: ReturnTerms | None = None,
) -> frozenset[Placeholder]:
    """Placeholders whose value code can fill for a decision: {terms} when the levers change
    the terms of future orders, {deadline} on the withdrawal path, {amount} on the withdrawal
    path when the refund amount is known."""
    names: set[Placeholder] = set()
    if term_phrases(levers, "en", base):
        names.add("terms")
    if path == Path.WITHDRAWAL.value:
        names.add("deadline")
        if refund_known:
            names.add("amount")
    return frozenset(names)


def format_date(day: date, lang: Lang) -> str:
    return f"{day.day} {_MONTHS[lang][day.month - 1]} {day.year}"


# Days read with a vowel in Italian (uno, otto, undici) take the elided article: "l'11 ottobre".
_IT_ELIDED_DAYS = frozenset({1, 8, 11})


def format_deadline(day: date, lang: Lang) -> str:
    """The refund deadline as the {deadline} field: "12 October 2026" in English, and in Italian
    with its article, which depends on how the day is read ("il 12 ottobre 2026", "l'11 ottobre
    2026"), so that "al più tardi per {deadline}" is correct on every day (round 4, finding
    E22)."""
    text = format_date(day, lang)
    if lang == "en":
        return text
    return f"l'{text}" if day.day in _IT_ELIDED_DAYS else f"il {text}"


def has_deduction(decision: Decision) -> bool:
    """A diminished-value claim withholds part of the refund of a withdrawal: the {deduction}
    field states it (the guarantee path never has a claim, policy/legal_guard.py)."""
    return decision.diminished_value_claim is not None and decision.path is Path.WITHDRAWAL


def format_amount(amount: Decimal, lang: Lang) -> str:
    if not amount.is_finite() or amount < 0:
        raise MessageError(f"Refund amount must be a non-negative number, got {amount!r}")
    cents = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if lang == "it":
        return f"{str(cents).replace('.', ',')} €"
    return f"€{cents}"


def template_fields(
    decision: Decision, base: ReturnTerms, lang: Lang, refund_eur: Decimal | None = None
) -> dict[str, str]:
    """Values of the available placeholders, written by code (rule 10)."""
    levers = decision.levers.to_contract(base)
    available = available_placeholders(decision.path.value, levers, refund_eur is not None, base)
    fields: dict[str, str] = {}
    if "terms" in available:
        fields["terms"] = "; ".join(term_phrases(levers, lang, base))
    if "deadline" in available:
        fields["deadline"] = format_deadline(decision.refund_due_by, lang)
    if refund_eur is not None and "amount" in available:
        fields["amount"] = format_amount(refund_eur, lang)
    claim = decision.diminished_value_claim
    if claim is not None and has_deduction(decision):
        fields["deduction"] = format_amount(claim.amount_eur, lang)
    return fields


def requires_terms(decision: Decision, base: ReturnTerms) -> bool:
    levers = decision.levers.to_contract(base)
    return is_restrictive(decision.levers, base) and bool(term_phrases(levers, "en", base))


def render(
    llm_text: str,
    decision: Decision,
    base: ReturnTerms,
    lang: Lang,
    refund_eur: Decimal | None = None,
) -> str:
    """Fill the placeholders from code fields and append the clause the decision requires.

    Raises MessageError if the text breaks the placeholder grammar, and LegalClauseError if the
    final text does not end with exactly the required clause (a code bug)."""
    fields = template_fields(decision, base, lang, refund_eur)
    problems = placeholder_problems(llm_text, fields, requires_terms(decision, base))
    if problems:
        raise MessageError("; ".join(f"{p.code}: {p.detail}" for p in problems))
    filled = _TOKEN_RE.sub(lambda m: fields[m.group(1)], llm_text)
    kind = required_clause(decision, base)
    final = append_clause(filled, kind, lang)
    assert_clause(final, kind, lang)
    return final
