"""Identifier normalisation and salted hashing (spec section 7, UNIT-01, NFR-03).

The store keeps identifiers only as salted hashes of their normalised form: two raw values that
normalise to the same string (an e-mail alias, an address written another way) give the same hash,
which is what lets the account graph (spec 8.3) see linked accounts. The raw values never leave the
generator. Hashes are pseudonymous personal data, not anonymous data.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

SALT_BYTES = 16
HASH_HEX_CHARS = 32  # 128 bits

_GMAIL_ALIASES = {"googlemail.com": "gmail.com"}
_INTERIOR = re.compile(r"\b(int|interno|scala|sc|piano|apt|c/o)\b.*$")
_CAP = re.compile(r"\b\d{5}\b")
_ABBREVIATIONS = (
    (re.compile(r"\bp\.?\s?zz?a\b\.?"), "piazza"),
    (re.compile(r"\bc\.\s?so\b\.?"), "corso"),
    (re.compile(r"\bv\.\s?le\b\.?"), "viale"),
    (re.compile(r"\bl\.\s?go\b\.?"), "largo"),
    (re.compile(r"\bv\.(?=\s|[a-z])"), "via "),
)
_NUMBER_MARK = re.compile(r"\bn\s*[.°º]\s*(?=\d)")  # "n. 12", "n° 12", "nº 12"


def _ascii_lower(raw: str) -> str:
    text = _NUMBER_MARK.sub(" ", raw.lower())
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return text.strip()


def normalize_email(raw: str) -> str:
    """Lower case; in the local part drop the "+alias" suffix and every dot (spec section 7)."""
    text = raw.strip().lower()
    local, at, domain = text.rpartition("@")
    local = local.split("+", 1)[0].replace(".", "")
    domain = _GMAIL_ALIASES.get(domain, domain)
    if not at or not local or not domain or "@" in local:
        raise ValueError(f"not an e-mail address: {raw!r}")
    return f"{local}@{domain}"


def normalize_address(raw: str) -> str:
    """Italian postal address in one canonical spelling: no accents, case or punctuation,
    abbreviations expanded (p.za, c.so, v.le, l.go, v.), no "n." before the number, and no
    interior detail (int., interno, scala, piano) after the postcode and city."""
    text = _ascii_lower(raw)
    cap = _CAP.search(text)
    if cap is not None:
        head, tail = text[: cap.end()], text[cap.end() :]
        text = head + _INTERIOR.sub("", tail)
    for pattern, replacement in _ABBREVIATIONS:
        text = pattern.sub(replacement, text)
    text = text.replace("'", "")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    if not text:
        raise ValueError(f"not an address: {raw!r}")
    return text


def normalize_phone(raw: str) -> str:
    """E.164-like: digits only with a leading +; Italian mobile numbers without country code get
    +39."""
    digits = re.sub(r"\D", "", raw)
    if raw.strip().startswith("00"):
        digits = digits[2:]
    elif len(digits) == 10 and digits.startswith("3"):
        digits = "39" + digits
    if len(digits) < 8:
        raise ValueError(f"not a phone number: {raw!r}")
    return "+" + digits


def ip_prefix24(raw: str) -> str:
    """The /24 network of an IPv4 address, for example 93.41.7.0/24."""
    parts = raw.strip().split(".")
    if len(parts) != 4 or not all(p.isdigit() and int(p) <= 255 for p in parts):
        raise ValueError(f"not an IPv4 address: {raw!r}")
    a, b, c = (int(p) for p in parts[:3])
    return f"{a}.{b}.{c}.0/24"


def normalize_token(raw: str) -> str:
    """Opaque identifiers (device fingerprint, card fingerprint): trimmed, lower case."""
    text = raw.strip().lower()
    if not text:
        raise ValueError("empty identifier")
    return text


class Hasher:
    """Salted SHA-256 of (kind, normalised value), truncated to 128 bits. The kind is part of the
    input, so equal strings of different kinds never collide."""

    def __init__(self, salt: bytes) -> None:
        if len(salt) < SALT_BYTES:
            raise ValueError(f"salt must have at least {SALT_BYTES} bytes")
        self._salt = salt

    def hash(self, kind: str, normalized: str) -> str:
        digest = hashlib.sha256(
            self._salt + b"\x1f" + kind.encode() + b"\x1f" + normalized.encode()
        )
        return digest.hexdigest()[:HASH_HEX_CHARS]
