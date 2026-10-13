"""Provenance of the figures the API serves (rule 11, US-906, NFR-16).

The serving snapshot carries the provenance of each table (contracts.io). The API shows it as
{origin, period_start, period_end, nature}; an origin longer than the 300 characters of the
contract is cut at a clause boundary (the whole text stays in the snapshot manifest).
"""

from __future__ import annotations

from datetime import date
from typing import Final

from apps.api import schemas as s
from contracts.io import Nature
from contracts.io import Provenance as FileProvenance

MAX_ORIGIN: Final = 300
ELLIPSIS: Final = "…"


def short_origin(text: str, limit: int = MAX_ORIGIN) -> str:
    """`text` in at most `limit` characters, cut after a clause ("; " or ", ") when it is
    longer, with an ellipsis."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[: limit - len(ELLIPSIS)]
    for mark in ("; ", ", ", " "):
        cut = head.rfind(mark)
        if cut > limit // 2:
            return head[:cut].rstrip(" ,;") + ELLIPSIS
    return head + ELLIPSIS


def provenance(origin: str, start: date, end: date, nature: Nature = "synthetic") -> s.Provenance:
    return s.Provenance(
        origin=short_origin(origin),
        period_start=min(start, end),
        period_end=end,
        nature=nature,
    )


def from_file(
    p: FileProvenance, prefix: str | None = None, end: date | None = None
) -> s.Provenance:
    """The provenance of a snapshot table, optionally named by `prefix` and ending at `end`."""
    origin = f"{prefix}; {p.origin}" if prefix else p.origin
    last = p.period_end if end is None else min(p.period_end, end)
    return provenance(origin, p.period_start, max(last, p.period_start), p.nature)
