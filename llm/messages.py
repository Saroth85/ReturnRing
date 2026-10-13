"""Deterministic rendering of customer messages (ADR-08, CLAUDE.md rule 10).

Gemma writes connective text with placeholders; amounts, terms and deadlines come from code.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from llm.legal_clause import ClauseKind, Lang, clause

PLACEHOLDERS = frozenset({"terms", "amount", "deadline"})
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


class MessageError(ValueError):
    pass


def placeholders_in(text: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(text))


def render_customer_message(
    llm_text: str,
    fields: Mapping[str, str],
    lang: Lang,
    clause_kind: ClauseKind | None,
) -> str:
    """Fill placeholders from code-provided fields and append the fixed legal clause."""
    used = placeholders_in(llm_text)
    unknown = used - PLACEHOLDERS
    if unknown:
        raise MessageError(f"Unknown placeholders: {sorted(unknown)}")
    missing = used - set(fields)
    if missing:
        raise MessageError(f"Missing values for placeholders: {sorted(missing)}")
    text = llm_text
    for name in used:
        text = text.replace("{" + name + "}", fields[name])
    if clause_kind is not None:
        text = f"{text.rstrip()}\n\n{clause(clause_kind, lang)}"
    return text
