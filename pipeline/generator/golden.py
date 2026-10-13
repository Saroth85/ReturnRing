"""Fixed identifiers of the golden cases (US-104, DATA-05; spec sections 3 and 7).

The golden cases are scripted in pipeline/generator/scripts.py, so their signatures hold at any
--scale and seed: only the catalogue variants they buy change with the store. They exist in the main
scenario only. Names are fictional.

- C-GIULIA (size): brackets two sizes of one item and returns one for size; keeps more than half of
  what she spends.
- R-MARCO (promo ring): C-MARCO-1..7, new accounts within two weeks, one welcome coupon each,
  device, address and card shared with noise, almost every line returned.
- F-ROSSI (shared benign): three old accounts with the same address and household device, distinct
  cards, normal returns. F-ROSSI-CARD is the same situation where two accounts share one card.
- C-ELENA (normal): returns the jacket V-JKT-DEF, a variant with a fit defect, for that reason in
  its defect window; independent customers return it in the same window.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

GIULIA: Final = "C-GIULIA"
ELENA: Final = "C-ELENA"
MARCO_RING: Final = "R-MARCO"
MARCO_ACCOUNTS: Final = tuple(f"C-MARCO-{i}" for i in range(1, 8))
ROSSI_FAMILY: Final = "F-ROSSI"
ROSSI_ACCOUNTS: Final = ("C-ROSSI-1", "C-ROSSI-2", "C-ROSSI-3")
ROSSI_CARD_FAMILY: Final = "F-ROSSI-CARD"
ROSSI_CARD_ACCOUNTS: Final = ("C-ROSSI-CARD-1", "C-ROSSI-CARD-2", "C-ROSSI-CARD-3")
GOLDEN_CUSTOMERS: Final = (GIULIA, *MARCO_ACCOUNTS, *ROSSI_ACCOUNTS, *ROSSI_CARD_ACCOUNTS, ELENA)


def _at(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# Account creation and display name (UI only, a forbidden feature) of each golden account.
ACCOUNTS: Final = {
    GIULIA: (_at(2023, 4, 12, 18, 20), "Giulia Bianchi"),
    "C-MARCO-1": (_at(2026, 3, 2, 21, 14), "Marco Ferri"),
    "C-MARCO-2": (_at(2026, 3, 3, 22, 40), "Marco F."),
    "C-MARCO-3": (_at(2026, 3, 5, 20, 5), "M. Ferri"),
    "C-MARCO-4": (_at(2026, 3, 6, 23, 30), "Marco Ferri"),
    "C-MARCO-5": (_at(2026, 3, 9, 19, 55), "Mario Ferri"),
    "C-MARCO-6": (_at(2026, 3, 11, 22, 10), "Lucia Ferraro"),
    "C-MARCO-7": (_at(2026, 3, 13, 21, 45), "Marco Ferraro"),
    "C-ROSSI-1": (_at(2019, 5, 10, 17, 2), "Anna Rossi"),
    "C-ROSSI-2": (_at(2021, 11, 2, 20, 41), "Paolo Rossi"),
    "C-ROSSI-3": (_at(2023, 2, 20, 16, 30), "Sofia Rossi"),
    "C-ROSSI-CARD-1": (_at(2020, 9, 1, 12, 15), "Marta Rossi"),
    "C-ROSSI-CARD-2": (_at(2022, 3, 15, 21, 0), "Giorgio Rossi"),
    "C-ROSSI-CARD-3": (_at(2024, 1, 8, 19, 25), "Chiara Rossi"),
    ELENA: (_at(2024, 6, 3, 13, 50), "Elena Conti"),
}

# The jacket: a fit defect from a batch shipped from 3 August 2026, not fixed by the end of the
# period. "Not as described": the size chart does not match the garment (a non-conformity, so the
# return follows the guarantee path, spec 8.6; Elena's return stays regular, spec 8.9).
DEFECTIVE_JACKET: Final = "V-JKT-DEF"
JACKET_SIBLING: Final = "V-JKT-OK"  # same product, other colour, not defective
JACKET_PRODUCT: Final = "P-JKT"
JACKET_LISTED_AT: Final = datetime(2025, 9, 1, 9, 0, tzinfo=UTC)
JACKET_DEFECT_START: Final = datetime(2026, 8, 3, tzinfo=UTC)
JACKET_DEFECT_REASON: Final = "not_as_described"
JACKET_DEFECT_INTENSITY: Final = 0.5
INDEPENDENT_JACKET_RETURNERS: Final = 8  # scripted returns by unrelated customers in the window

# Out-of-distribution scenario (US-105): the harder family, five new accounts created together,
# each with a welcome coupon, sharing address, device and one card, with normal returns.
OOD_HARD_FAMILY: Final = "F-OOD-HARD"
OOD_HARD_FAMILY_SIZE: Final = 5
