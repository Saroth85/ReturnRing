"""Returns of Navigli Fashion: who returns what, why, when, and the states that follow (US-102).

Return probability of a line (spec section 7 signatures):

- normal, shared benign and size customers (outside their bracketing pairs): k_type x
  base_return_rate x propensity, at most 0.95. k_type is fitted per product type so that the mature
  lines of the store return at the calibration rate, once the abusive behaviours are included;
- bracketing pairs: exactly one of the two sizes is returned, for size;
- wardrobers: 0.8 on high-value lines (price at or above the catalogue median), "changed mind",
  3 to 6 days after delivery;
- ring accounts: almost every line (ScenarioConfig.ring_return_probability);
- defective variants: in their window, a share of the buyers return the line for the defect reason,
  whoever they are (independent customers).

Return states (NFR-17, spec 8.6) under the Navigli base terms (instant refund at the carrier scan):
withdrawal: notified -> shipped_or_proof -> refunded (minutes later) -> received; guarantee
(non-conformity reasons): notified -> shipped_or_proof -> received -> refunded after inspection.
Every refund comes within 14 days of the notice (rule 3). Each state is a row known at its
available_at; rows not available before the extraction are not in the store.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from pipeline.generator.catalogue import Catalogue
from pipeline.generator.config import (
    END_US,
    MATURITY_DAYS,
    MAX_LINE_RETURN_PROBABILITY,
    US_PER_DAY,
    US_PER_HOUR,
    WARDROBE_RETURN_PROBABILITY,
    ScenarioConfig,
)
from pipeline.generator.orders import (
    MODE_NORMAL,
    MODE_PAIR,
    MODE_RING,
    MODE_SCRIPTED,
    MODE_SIZE,
    MODE_WARDROBE,
    RETURN,
    Deliveries,
    OrderPlan,
)
from pipeline.generator.population import Population
from pipeline.generator.timeline import days, minutes, uniform_us
from policy.legal_guard import LEGAL_REFUND_DAYS
from policy.types import NON_CONFORMITY_REASONS, ReturnReason

I64 = npt.NDArray[np.int64]
F64 = npt.NDArray[np.float64]
B = npt.NDArray[np.bool_]

REASONS = tuple(r.value for r in ReturnReason)
_GUARANTEE = np.asarray([ReturnReason(r) in NON_CONFORMITY_REASONS for r in REASONS])


def _mix(**shares: float) -> F64:
    out = np.asarray([shares.get(r, 0.0) for r in REASONS], dtype=np.float64)
    return out / out.sum()


# Reasons of the returns that are not defect returns, by mode (design hypotheses; the ASOS reason
# codes are masked and cannot be mapped to these reasons).
REASON_MIX: dict[int, F64] = {
    MODE_NORMAL: _mix(
        size=0.36, changed_mind=0.24, not_as_expected=0.24, other=0.08, defective=0.04,
        damaged_in_shipping=0.02, wrong_item=0.01, not_as_described=0.01,
    ),
    MODE_SIZE: _mix(
        size=0.62, changed_mind=0.14, not_as_expected=0.14, other=0.04, defective=0.03,
        damaged_in_shipping=0.01, wrong_item=0.01, not_as_described=0.01,
    ),
    MODE_WARDROBE: _mix(changed_mind=0.78, not_as_expected=0.14, size=0.06, other=0.02),
    MODE_RING: _mix(
        changed_mind=0.42, not_as_expected=0.25, size=0.2, other=0.08, not_as_described=0.05
    ),
}  # fmt: skip
K_MAX = 10.0


@dataclass
class Outcome:
    returned: B  # per line
    reason: I64  # index into REASONS, -1 when kept
    defect: B  # returned for the variant's defect
    k_by_type: F64
    reachable: B  # per type: the calibration rate could be reached


@dataclass
class ReturnRows:
    line: I64
    reason: I64
    requested_at: I64
    available_at: I64
    days_since_delivery: I64
    pending: B
    # states: one row each
    state_return: I64  # index into the rows above
    state: list[str]
    state_at: I64
    state_available: I64


def _probabilities(
    plan: OrderPlan, cat: Catalogue, pop: Population, cfg: ScenarioConfig
) -> tuple[F64, F64, F64, B]:
    """Per line: defect probability d, base probability for k = 1 (scaled by k), fixed
    probability (NaN when it scales with k), and whether the line's outcome is drawn."""
    v = plan.line_variant
    placed = plan.placed_at[plan.line_order]
    customer = plan.customer[plan.line_order]
    d = cat.defect_probability(v, placed)
    scaled = cat.base_rate[v] * pop.propensity[customer]
    fixed = np.full(plan.n_lines, np.nan)
    dear = cat.price_cents[v] >= np.median(cat.price_cents)
    fixed[(plan.line_mode == MODE_WARDROBE) & dear] = WARDROBE_RETURN_PROBABILITY
    fixed[plan.line_mode == MODE_RING] = cfg.ring_return_probability
    drawn = np.isin(plan.line_mode, (MODE_NORMAL, MODE_SIZE, MODE_WARDROBE, MODE_RING))
    return d, scaled, fixed, drawn


def _total(k: float | F64, d: F64, scaled: F64, fixed: F64) -> F64:
    """Return probability of drawn lines: the defect first, then the behaviour's probability."""
    p = np.where(np.isnan(fixed), np.minimum(k * scaled, MAX_LINE_RETURN_PROBABILITY), fixed)
    return np.asarray(d + (1 - d) * p, dtype=np.float64)


def calibrate(
    plan: OrderPlan,
    cat: Catalogue,
    pop: Population,
    deliveries: Deliveries,
    targets: F64,
    cfg: ScenarioConfig,
) -> tuple[F64, B]:
    """k per product type such that the expected return rate of the mature delivered lines of the
    type equals its target (bisection; the expectation is monotone in k)."""
    d, scaled, fixed, drawn = _probabilities(plan, cat, pop, cfg)
    delivered = deliveries.delivered_at[plan.line_order] >= 0
    mature = delivered & (plan.placed_at[plan.line_order] < END_US - MATURITY_DAYS * US_PER_DAY)
    forced = np.where(plan.line_outcome == RETURN, 1.0, 0.0)
    expected_fixed = np.where(plan.line_mode == MODE_PAIR, 0.5, forced)
    types = cat.type_index[plan.line_variant]
    k_by_type = np.ones(len(cat.type_names))
    reachable = np.ones(len(cat.type_names), dtype=np.bool_)
    for t in range(len(cat.type_names)):
        lines = mature & (types == t)
        if not lines.any():
            continue
        rand = lines & drawn
        constant = expected_fixed[lines & ~drawn].sum()
        goal = targets[t] * lines.sum()

        def excess(
            k: float, rand: B = rand, constant: float = constant, goal: float = goal
        ) -> float:
            return float(_total(k, d[rand], scaled[rand], fixed[rand]).sum() + constant - goal)

        if excess(0.0) >= 0:
            k_by_type[t], reachable[t] = 0.0, excess(0.0) <= 0
            continue
        if excess(K_MAX) <= 0:
            k_by_type[t], reachable[t] = K_MAX, False
            continue
        lo, hi = 0.0, K_MAX
        for _ in range(60):
            mid = (lo + hi) / 2
            if excess(mid) < 0:
                lo = mid
            else:
                hi = mid
        k_by_type[t] = (lo + hi) / 2
    return k_by_type, reachable


def draw_outcomes(
    plan: OrderPlan,
    cat: Catalogue,
    pop: Population,
    deliveries: Deliveries,
    k_by_type: F64,
    reachable: B,
    cfg: ScenarioConfig,
    rng: np.random.Generator,
) -> Outcome:
    n = plan.n_lines
    d, scaled, fixed, drawn = _probabilities(plan, cat, pop, cfg)
    k = k_by_type[cat.type_index[plan.line_variant]]
    delivered = deliveries.delivered_at[plan.line_order] >= 0
    u = rng.random(n)
    returned = delivered & drawn & (u < _total(k, d, scaled, fixed))
    defect = returned & (u < d)

    # Bracketing pairs: one of the two sizes goes back.
    pair_lines = np.flatnonzero(plan.line_mode == MODE_PAIR)
    pairs = plan.line_pair[pair_lines]
    order = np.lexsort((pair_lines, pairs))
    first, second = pair_lines[order][0::2], pair_lines[order][1::2]
    pick = np.where(rng.random(len(first)) < 0.5, first, second)
    returned[pick] = delivered[pick]

    forced = (plan.line_outcome == RETURN) & delivered
    returned |= forced

    reason = np.full(n, -1, dtype=np.int64)
    for mode, mix in REASON_MIX.items():
        idx = np.flatnonzero(returned & (plan.line_mode == mode) & ~defect)
        reason[idx] = rng.choice(len(REASONS), size=len(idx), p=mix)
    reason[pick] = REASONS.index("size")
    for i in np.flatnonzero(defect):
        defect_reason = cat.defect_reason[int(plan.line_variant[i])]
        assert defect_reason is not None
        reason[i] = REASONS.index(defect_reason)
    for i in np.flatnonzero(forced):
        forced_reason = plan.line_reason[int(i)]
        if forced_reason is None:
            raise ValueError("a scripted return needs a reason")
        reason[i] = REASONS.index(ReturnReason(forced_reason).value)
    reason[~returned] = -1
    out = Outcome(returned, reason, defect, k_by_type, reachable)
    # The signatures must hold in the extracted store: on the deliveries known before its end.
    known = deliveries.available_at[plan.line_order]
    observable = (known >= 0) & (known < END_US)
    _keep_half_of_spend(plan, cat, pop, observable, out, rng)
    _cap_hard_family(plan, pop, observable, out, rng)
    return out


def _unreturn(out: Outcome, lines: I64) -> None:
    out.returned[lines] = False
    out.reason[lines] = -1
    out.defect[lines] = False


def _keep_half_of_spend(
    plan: OrderPlan,
    cat: Catalogue,
    pop: Population,
    delivered: B,
    out: Outcome,
    rng: np.random.Generator,
) -> None:
    """Size customers keep at least half of what they spend (spec section 7)."""
    customer = plan.customer[plan.line_order]
    size_customer = np.asarray([b == "size" for b in pop.behaviour], dtype=np.bool_)
    mask = size_customer[customer] & delivered & (plan.line_mode != MODE_SCRIPTED)
    price = cat.price_cents[plan.line_variant].astype(np.int64)
    spend = np.bincount(customer[mask], weights=price[mask], minlength=pop.n)
    kept_mask = mask & ~out.returned
    kept = np.bincount(customer[kept_mask], weights=price[kept_mask], minlength=pop.n)
    for c in np.flatnonzero(size_customer & (kept * 2 < spend)):
        movable = np.flatnonzero(
            (customer == c) & out.returned & (plan.line_mode != MODE_PAIR) & mask
        )
        for i in rng.permutation(movable):
            if kept[c] * 2 >= spend[c]:
                break
            _unreturn(out, np.asarray([i]))
            kept[c] += price[i]


def _cap_hard_family(
    plan: OrderPlan, pop: Population, delivered: B, out: Outcome, rng: np.random.Generator
) -> None:
    """The OOD hard family returns normally: at most 40% of its delivered lines."""
    hard = [f for f in pop.families if f.hard]
    customer = plan.customer[plan.line_order]
    for family in hard:
        lines = np.flatnonzero(np.isin(customer, family.members) & delivered)
        returned = lines[out.returned[lines]]
        excess = len(returned) - int(0.4 * len(lines))
        if excess > 0:
            _unreturn(out, rng.permutation(returned)[:excess])


def _return_days(plan: OrderPlan, out: Outcome, rng: np.random.Generator) -> I64:
    n = plan.n_lines
    mode = plan.line_mode
    normal = np.minimum(np.floor(rng.gamma(2.0, 3.0, n)), 29)
    quick = np.minimum(np.floor(rng.gamma(2.0, 2.0, n)), 29)
    pair = np.minimum(1 + rng.poisson(1.5, n), 29)
    wardrobe = rng.integers(3, 7, n)
    ring = rng.integers(1, 10, n)
    defect = np.minimum(1 + np.floor(rng.gamma(2.0, 4.0, n)), 40)
    out_days = np.select(
        [
            mode == MODE_SCRIPTED,
            out.defect,
            mode == MODE_PAIR,
            mode == MODE_WARDROBE,
            mode == MODE_RING,
            mode == MODE_SIZE,
        ],
        [plan.line_days, defect, pair, wardrobe, ring, quick],
        default=normal,
    )
    return np.asarray(out_days, dtype=np.int64)


def schedule_returns(
    plan: OrderPlan,
    deliveries: Deliveries,
    out: Outcome,
    rng: np.random.Generator,
) -> ReturnRows:
    """Request instants and states of the returned lines; rows not available before the
    extraction are dropped."""
    n_days = _return_days(plan, out, rng)
    lines = np.flatnonzero(out.returned)
    o = plan.line_order[lines]
    delivered = deliveries.delivered_at[o]
    known = deliveries.available_at[o]
    k = len(lines)
    requested = delivered + n_days[lines] * US_PER_DAY + uniform_us(rng, 0, US_PER_DAY, k)
    requested = np.maximum(requested, known + minutes(rng, 1, 30, k))
    since = (requested - delivered) // US_PER_DAY
    available = requested + minutes(rng, 0, 5, k)
    pending = plan.line_pending[lines]
    reason = out.reason[lines]
    guarantee = _GUARANTEE[reason]
    ring = plan.line_mode[lines] == MODE_RING

    shipped = requested + np.where(ring, days(rng, 0.1, 3, k), days(rng, 0.2, 7, k))
    refund_cap = requested + LEGAL_REFUND_DAYS * US_PER_DAY - US_PER_HOUR
    received_w = shipped + days(rng, 2, 6, k)
    refunded_w = shipped + minutes(rng, 1, 30, k)  # instant refund at the carrier scan
    received_g = shipped + days(rng, 2, 5, k)
    refunded_g = np.minimum(received_g + days(rng, 0.5, 2, k), refund_cap)  # after inspection
    received = np.where(guarantee, received_g, received_w)
    refunded = np.where(guarantee, refunded_g, refunded_w)

    lag = [minutes(rng, 1, 120, k) for _ in range(3)]
    shipped_av = np.maximum(shipped + lag[0], available)
    first_after = np.where(guarantee, received, refunded)
    second_after = np.where(guarantee, refunded, received)
    first_av = np.maximum(first_after + lag[1], shipped_av)
    second_av = np.maximum(second_after + lag[2], first_av)
    first_name = np.where(guarantee, "received", "refunded")
    second_name = np.where(guarantee, "refunded", "received")

    keep = available < END_US
    idx = np.flatnonzero(keep)
    remap = np.full(k, -1, dtype=np.int64)
    remap[idx] = np.arange(len(idx))
    state_return: list[I64] = [remap[idx]]
    state: list[str] = ["notified"] * len(idx)
    state_at: list[I64] = [requested[idx]]
    state_av: list[I64] = [available[idx]]
    for at, av, names in (
        (shipped, shipped_av, np.full(k, "shipped_or_proof")),
        (first_after, first_av, first_name),
        (second_after, second_av, second_name),
    ):
        rows = np.flatnonzero(keep & ~pending & (av < END_US))
        state_return.append(remap[rows])
        state.extend(str(s) for s in names[rows])
        state_at.append(at[rows])
        state_av.append(av[rows])
    return ReturnRows(
        line=lines[idx],
        reason=reason[idx],
        requested_at=requested[idx],
        available_at=available[idx],
        days_since_delivery=since[idx],
        pending=pending[idx],
        state_return=np.concatenate(state_return),
        state=state,
        state_at=np.concatenate(state_at),
        state_available=np.concatenate(state_av),
    )
