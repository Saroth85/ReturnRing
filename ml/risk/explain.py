"""Top 3 contributions to a risk score, in fixed phrases (spec 8.4; MOD-05; US-302).

Contributions are XGBoost's native TreeSHAP values (Booster.predict(pred_contribs=True)): exact
Shapley values of the trees on the log-odds scale, before the isotonic calibration. Calibration is
monotone, so the order and the sign of the contributions hold for the calibrated score too.

A contribution is a contribution to the score, not a cause of the customer's behaviour: every
phrase says so. Phrases come from a fixed table feature -> (English, Italian) label, plus the
direction of the contribution; the model never writes them, and they hold no number, so the
number shown with a factor is always its value field (llm number anchoring, spec 8.7). The phrases
hold no verdict word and no decision word (checked by tests with llm.text).

The top 3 are the features with the largest absolute contribution among those whose value is
known: a factor shows a value, and a missing value has none to show. The bias term is never a
factor. An account with no linked account has its cluster features computed on itself
(pipeline.graph): their phrases then speak of this account, never of linked accounts.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

import numpy as np
import numpy.typing as npt

from contracts.parquet import is_forbidden_feature
from ml.risk.features import EMBEDDING_PREFIX, all_feature_names

TOP: Final = 3
Lang = Literal["en", "it"]
Direction = Literal["up", "down", "flat"]

# feature -> (English label, Italian label). Labels hold no number and no verdict.
LABELS: Final[dict[str, tuple[str, str]]] = {
    "return_rate_to_date": (
        "Share of the delivered order lines returned so far",
        "Quota delle righe d'ordine consegnate restituite finora",
    ),
    "median_days_to_return": (
        "Median days from delivery to the return request",
        "Giorni mediani tra la consegna e la richiesta di reso",
    ),
    "mean_line_price_eur": (
        "Mean price of the items ordered",
        "Prezzo medio degli articoli ordinati",
    ),
    "n_orders": ("Orders placed", "Ordini effettuati"),
    "n_lines": ("Order lines placed", "Righe d'ordine effettuate"),
    "n_delivered_lines": ("Order lines delivered", "Righe d'ordine consegnate"),
    "n_observations": (
        "Order lines whose return window has closed",
        "Righe d'ordine con la finestra di reso chiusa",
    ),
    "n_returns": ("Return requests on the withdrawal path", "Richieste di reso per recesso"),
    "return_rate": (
        "Share of the order lines with a closed return window that were returned",
        "Quota delle righe d'ordine con la finestra chiusa che sono state restituite",
    ),
    "size_share": ("Share of returns for size", "Quota di resi per la taglia"),
    "changed_mind_share": (
        "Share of returns because the customer changed their mind",
        "Quota di resi per ripensamento",
    ),
    "not_as_expected_share": (
        "Share of returns because the item was not as expected",
        "Quota di resi perché l'articolo non era come atteso",
    ),
    "other_reason_share": ("Share of returns for another reason", "Quota di resi per altri motivi"),
    "tenure_days": ("Days since the account was opened", "Giorni dall'apertura dell'account"),
    "n_welcome_coupons_used": ("Welcome coupons used", "Coupon di benvenuto usati"),
    "n_promo_coupons_used": (
        "Promotional and referral codes used",
        "Codici promozionali e di presentazione usati",
    ),
    "coupon_order_share": ("Share of orders with a coupon", "Quota di ordini con un coupon"),
    "weekend_order_share": (
        "Share of orders placed at the weekend",
        "Quota di ordini effettuati nel fine settimana",
    ),
    "cluster_size": (
        "Accounts in the group of linked accounts",
        "Account nel gruppo di account collegati",
    ),
    "shared_strong_ids": (
        "Card, e-mail and device values shared among the linked accounts",
        "Carte, e-mail e dispositivi in comune tra gli account collegati",
    ),
    "welcome_coupons": (
        "Welcome coupons used by the linked accounts",
        "Coupon di benvenuto usati dagli account collegati",
    ),
    "n_distinct_cards": (
        "Distinct payment cards of the linked accounts",
        "Carte di pagamento distinte degli account collegati",
    ),
    "creation_spread_days": (
        "Days between the first and the last opening among the linked accounts",
        "Giorni tra la prima e l'ultima apertura degli account collegati",
    ),
    "welcome_coupons_per_account": (
        "Welcome coupons used per linked account",
        "Coupon di benvenuto usati per account collegato",
    ),
    "cluster_return_rate": (
        "Share of the delivered order lines of the linked accounts returned",
        "Quota delle righe d'ordine consegnate agli account collegati restituite",
    ),
    "strong_id_share": (
        "Share of card, e-mail and device values among the values the linked accounts share",
        "Quota di carte, e-mail e dispositivi tra i valori in comune degli account collegati",
    ),
    "expected_returns": (
        "Returns expected from the items bought, by the product reference",
        "Resi attesi dagli articoli acquistati, secondo il riferimento di prodotto",
    ),
    "residual": (
        "Returns beyond those the items bought would predict",
        "Resi oltre quelli che gli articoli acquistati farebbero prevedere",
    ),
    "residual_z": (
        "Standardised return residual against the product reference",
        "Residuo di reso standardizzato rispetto al riferimento di prodotto",
    ),
    "days_since_delivery": (
        "Days from delivery to this return request",
        "Giorni tra la consegna e questa richiesta di reso",
    ),
    "item_price_eur": ("Price of the item returned", "Prezzo dell'articolo reso"),
    "variant_return_rate": (
        "Return rate of this item among other customers",
        "Tasso di reso di questo articolo tra gli altri clienti",
    ),
    "variant_lines": (
        "Order lines of this item with a closed return window, other customers",
        "Righe d'ordine di questo articolo con la finestra chiusa, altri clienti",
    ),
    "category_return_rate": (
        "Return rate of the item's category",
        "Tasso di reso della categoria dell'articolo",
    ),
    "reason_size": ("Reason given as size", "Motivo indicato come taglia"),
    "reason_changed_mind": (
        "Reason given as a change of mind",
        "Motivo indicato come ripensamento",
    ),
    "reason_not_as_expected": (
        "Reason given as an item not as expected",
        "Motivo indicato come articolo non come atteso",
    ),
    "reason_other": ("Reason given as another reason", "Motivo indicato come altro motivo"),
}
# An account without a linked account has its cluster features computed on itself
# (pipeline.graph.clusters): its phrases say so, never "the linked accounts" (rule 11).
ALONE_LABELS: Final[dict[str, tuple[str, str]]] = {
    "cluster_size": (
        "Size of the group of linked accounts (this account has none)",
        "Dimensione del gruppo di account collegati (questo account non ne ha)",
    ),
    "shared_strong_ids": (
        "Card, e-mail and device values shared with linked accounts (this account has none)",
        "Carte, e-mail e dispositivi in comune con account collegati (questo account non ne ha)",
    ),
    "welcome_coupons": (
        "Welcome coupons used by this account (it has no linked account)",
        "Coupon di benvenuto usati da questo account (non ha account collegati)",
    ),
    "n_distinct_cards": (
        "Distinct payment cards of this account (it has no linked account)",
        "Carte di pagamento distinte di questo account (non ha account collegati)",
    ),
    "creation_spread_days": (
        "Days between the openings of linked accounts (this account has none)",
        "Giorni tra le aperture degli account collegati (questo account non ne ha)",
    ),
    "welcome_coupons_per_account": (
        "Welcome coupons used per account, this account alone (it has no linked account)",
        "Coupon di benvenuto usati per account, solo questo account (non ha account collegati)",
    ),
    "cluster_return_rate": (
        "Share of the delivered order lines of this account returned (it has no linked account)",
        "Quota delle righe d'ordine consegnate a questo account restituite (non ha account "
        "collegati)",
    ),
    "strong_id_share": (
        "Share of card, e-mail and device values among the values shared with linked accounts "
        "(this account has none)",
        "Quota di carte, e-mail e dispositivi tra i valori in comune con account collegati "
        "(questo account non ne ha)",
    ),
}
# A4 (phase 6): every GraphSAGE embedding component shares one label.
EMBEDDING_LABEL: Final = (
    "Position in the graph of accounts and identifiers (embedding component)",
    "Posizione nel grafo di account e identificativi (componente dell'embedding)",
)
DIRECTIONS: Final[dict[Direction, tuple[str, str]]] = {
    "up": ("raises the score", "alza il punteggio"),
    "down": ("lowers the score", "abbassa il punteggio"),
    "flat": ("leaves the score unchanged", "lascia invariato il punteggio"),
}
NOT_A_CAUSE: Final = (
    "a contribution to the score, not a cause",
    "un contributo al punteggio, non una causa",
)
_missing = sorted(set(all_feature_names()) - set(LABELS))
if _missing:  # pragma: no cover - a guard on the constants
    raise RuntimeError(f"features without a phrase: {_missing}")


def direction_of(contribution: float) -> Direction:
    if contribution > 0:
        return "up"
    if contribution < 0:
        return "down"
    return "flat"


def labels_of(feature: str, alone: bool = False) -> tuple[str, str]:
    """The (English, Italian) label of a feature; alone: the account has no linked account.
    KeyError for a feature without a label."""
    if feature.startswith(EMBEDDING_PREFIX):
        return EMBEDDING_LABEL
    if alone and feature in ALONE_LABELS:
        return ALONE_LABELS[feature]
    return LABELS[feature]


def phrase(feature: str, contribution: float, lang: Lang, alone: bool = False) -> str:
    """The fixed phrase of a contribution, for example "Welcome coupons used: raises the score
    (a contribution to the score, not a cause)"."""
    i = 0 if lang == "en" else 1
    label = labels_of(feature, alone)[i]
    return f"{label}: {DIRECTIONS[direction_of(contribution)][i]} ({NOT_A_CAUSE[i]})"


@dataclass(frozen=True)
class Factor:
    feature: str
    value: float
    contribution: float  # TreeSHAP, log-odds of the uncalibrated score
    alone: bool = False  # the account has no linked account (cluster_size 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "value": round(self.value, 6),
            "contribution": round(self.contribution, 6),
            "direction": direction_of(self.contribution),
            "phrase_en": phrase(self.feature, self.contribution, "en", self.alone),
            "phrase_it": phrase(self.feature, self.contribution, "it", self.alone),
        }


def top_factors(
    features: Sequence[str],
    values: npt.ArrayLike,
    contributions: npt.ArrayLike,
    k: int = TOP,
) -> list[list[Factor]]:
    """Per row, the k features with the largest |contribution| among the known values (ties: the
    feature order). contributions has one column per feature plus the bias, last."""
    x = np.asarray(values, dtype=np.float64)
    c = np.asarray(contributions, dtype=np.float64)
    n_features = len(features)
    if x.ndim != 2 or x.shape[1] != n_features or c.shape != (x.shape[0], n_features + 1):
        raise ValueError(
            f"expected values (n, {n_features}) and contributions (n, {n_features + 1})"
        )
    bad = [f for f in features if is_forbidden_feature(f)]
    if bad:
        raise ValueError(f"forbidden features cannot be shown (rule 6): {bad}")
    unknown = [f for f in features if not f.startswith(EMBEDDING_PREFIX) and f not in LABELS]
    if unknown:
        raise ValueError(f"features without a fixed phrase: {unknown}")
    magnitude = np.where(np.isnan(x), -1.0, np.abs(c[:, :n_features]))
    size = list(features).index("cluster_size") if "cluster_size" in features else None
    out: list[list[Factor]] = []
    for row in range(x.shape[0]):
        order = np.argsort(-magnitude[row], kind="stable")
        chosen = [j for j in order if magnitude[row, j] >= 0][:k]
        alone = size is not None and x[row, size] == 1.0
        out.append([Factor(features[j], float(x[row, j]), float(c[row, j]), alone) for j in chosen])
    return out


def factors_json(factors: Sequence[Factor]) -> str:
    """The shap_top3 column of scores/scores and scores/request_scores (canonical JSON)."""
    return json.dumps(
        [f.as_dict() for f in factors], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


@dataclass(frozen=True)
class FactorCheck:
    """MOD-05: rows, rows with exactly 3 factors, and forbidden features found (none)."""

    rows: int
    with_three: int
    forbidden: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.rows > 0 and self.with_three == self.rows and not self.forbidden


def check_factors(rows: Sequence[Sequence[Factor]]) -> FactorCheck:
    forbidden = sorted({f.feature for r in rows for f in r if is_forbidden_feature(f.feature)})
    return FactorCheck(len(rows), sum(len(r) == TOP for r in rows), tuple(forbidden))
