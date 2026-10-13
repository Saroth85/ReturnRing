"""UNIT-01: normalisation of e-mails, addresses, phones and IP prefixes, and salted hashing.

Every identifier in this module is made up (nature: synthetic test fixture).
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.generator.accounts import IdentityFactory
from pipeline.generator.identity import (
    Hasher,
    ip_prefix24,
    normalize_address,
    normalize_email,
    normalize_phone,
    normalize_token,
)


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("mario.rossi@gmail.com", "mariorossi@gmail.com"),
        ("Mario.Rossi+promo@Gmail.com", "mariorossi@gmail.com"),
        ("  m.a.r.i.o.rossi+1+2@googlemail.com ", "mariorossi@gmail.com"),
        ("mariorossi@libero.it", "mariorossi@libero.it"),
        ("mario.rossi+shop@libero.it", "mariorossi@libero.it"),
        ("ANNA_BIANCHI@HOTMAIL.IT", "anna_bianchi@hotmail.it"),
    ],
)
def test_normalize_email_removes_dots_and_aliases(raw: str, normalized: str) -> None:
    assert normalize_email(raw) == normalized


@pytest.mark.parametrize("raw", ["", "no-at-sign", "@gmail.com", "+alias@gmail.com", "a@"])
def test_normalize_email_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_email(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "Via Roma 12, 20121 Milano",
        "via roma, 12 - 20121 MILANO",
        "V. Roma n. 12, 20121 Milano",
        "Via Roma, n° 12, 20121 Milano, int. 5",
        "VIA  ROMA 12 20121 Milano scala B",
        "Via Roma 12, 20121 Milano, interno 7",
        "via Roma 12, 20121 Milano, piano 3",
    ],
)
def test_normalize_address_collapses_renderings(raw: str) -> None:
    assert normalize_address(raw) == "via roma 12 20121 milano"


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("P.zza Duca d'Aosta 1, 20124 Milano", "piazza duca daosta 1 20124 milano"),
        ("C.so Buenos Aires 33, 20124 Milano", "corso buenos aires 33 20124 milano"),
        ("V.le Monza 45, 20127 Milano", "viale monza 45 20127 milano"),
        ("L.go Augusto 3, 20122 Milano", "largo augusto 3 20122 milano"),
        ("Piazza Città di Lombardia 1, 20124 Milano", "piazza citta di lombardia 1 20124 milano"),
    ],
)
def test_normalize_address_expands_abbreviations_and_accents(raw: str, normalized: str) -> None:
    assert normalize_address(raw) == normalized


def test_normalize_address_rejects_blank() -> None:
    with pytest.raises(ValueError):
        normalize_address(" ,  ")


@pytest.mark.parametrize(
    "raw",
    ["+39 347 123 4567", "0039 347-123-4567", "347 1234567", "+39 (347) 123.45.67", "3471234567"],
)
def test_normalize_phone(raw: str) -> None:
    assert normalize_phone(raw) == "+393471234567"


def test_normalize_phone_keeps_foreign_numbers_and_rejects_short_ones() -> None:
    assert normalize_phone("+44 20 7946 0958") == "+442079460958"
    with pytest.raises(ValueError):
        normalize_phone("12-34")


@pytest.mark.parametrize(
    ("raw", "prefix"),
    [
        ("93.41.7.188", "93.41.7.0/24"),
        (" 2.228.0.1 ", "2.228.0.0/24"),
        ("10.0.0.255", "10.0.0.0/24"),
    ],
)
def test_ip_prefix24(raw: str, prefix: str) -> None:
    assert ip_prefix24(raw) == prefix


@pytest.mark.parametrize("raw", ["93.41.7", "256.1.1.1", "a.b.c.d", "1.2.3.4.5", ""])
def test_ip_prefix24_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        ip_prefix24(raw)


def test_normalize_token() -> None:
    assert normalize_token("  Dev-ABC123 ") == "dev-abc123"
    with pytest.raises(ValueError):
        normalize_token("   ")


def test_every_rendering_of_a_generated_value_normalises_to_one_key() -> None:
    """The generator writes shared addresses and ring e-mails in many ways; each must hash to the
    same value, or a sharing pattern would silently disappear."""
    factory = IdentityFactory(np.random.Generator(np.random.PCG64(1)), n_customers=100)
    rng = np.random.Generator(np.random.PCG64(2))
    for _ in range(300):
        address = factory.address()
        assert {normalize_address(address.render(rng)) for _ in range(8)} == {address.normalized()}
    base = factory.gmail_base("Marco", "D'Angelo")
    aliases = {normalize_email(factory.alias(base, k)) for k in range(12)}
    assert aliases == {normalize_email(f"{base}@gmail.com")}
    phones = {normalize_phone(factory.phone()) for _ in range(200)}
    assert len(phones) == 200 and all(p.startswith("+393") for p in phones)
    emails = [factory.email("Anna", "Rossi") for _ in range(200)]
    assert len({normalize_email(e) for e in emails}) == 200  # unique unless shared on purpose


def test_hasher_is_salted_deterministic_and_kind_aware() -> None:
    a, b = Hasher(b"salt-one-1234567"), Hasher(b"salt-two-1234567")
    value = normalize_email("mario.rossi@gmail.com")
    assert a.hash("email", value) == Hasher(b"salt-one-1234567").hash("email", value)
    assert a.hash("email", value) != b.hash("email", value)
    assert a.hash("email", value) != a.hash("device", value)
    digest = a.hash("email", value)
    assert len(digest) == 32 and all(c in "0123456789abcdef" for c in digest)
    assert value not in digest
    with pytest.raises(ValueError):
        Hasher(b"short")
