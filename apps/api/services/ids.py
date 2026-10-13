"""Identifiers the API writes (ADR-16). Deterministic: the same reset gives the same ids."""

from __future__ import annotations

from typing import Final

RETURN_PREFIX: Final = "RET-"


def return_number(return_id: str) -> str:
    """The number of a return id: 0003008 of RET-0003008 (another id is kept whole)."""
    return return_id.removeprefix(RETURN_PREFIX)


def decision_id(return_id: str, version: int) -> str:
    """DEC-<return>-<version>, as in the contract examples (DEC-000102-1)."""
    return f"DEC-{return_number(return_id)}-{version}"


def application_id(return_id: str, version: int) -> str:
    """The cluster coupon application proposed with a decision version."""
    return f"CRA-{return_number(return_id)}-{version}"


def generation_id(return_id: str, seq: int) -> str:
    """The seq-th generation of a return's drafts (GEN-000101-1)."""
    return f"GEN-{return_number(return_id)}-{seq}"


def message_id(seq: int) -> str:
    return f"MSG-{seq:06d}"
