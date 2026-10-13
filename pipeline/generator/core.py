"""Generate the Navigli Fashion store (US-102..US-105): the entry point of the generator.

generate() builds the store in memory from a calibration, a seed, a scale and a scenario, writes
every table through contracts.io.write_table (nature "synthetic") and returns a summary of what it
wrote. Determinism (DATA-04, NFR-10): only numpy Generator(PCG64) streams derived from the seed, no
wall clock, sorted outputs; the same seed and calibration give byte-identical files.

Layout (scenario_roots): <out>/store/*.parquet and <out>/truth/*.parquet for the main scenario.
The out-of-distribution scenario (US-105) writes its store to <out>/ood/store and its truth under
<out>/truth/ood (files <out>/truth/ood/truth/*.parquet), so one folder, <out>/truth, holds every
ground truth (rule 12). It is never used in training (MOD-12): only the evaluation reads it, and
reports it on a separate row.

Provenance (rule 11): nature synthetic; the origin names the calibration and the store period
(October 2025 to September 2026); the period of each file covers its rows, so store/customers and
store/variants start with the oldest account and the oldest listing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.compute as pc

from contracts.io import Provenance, write_table
from contracts.parquet import EVENT_TABLES
from pipeline.generator import golden, tables
from pipeline.generator.accounts import (
    COLLECTIVE_NAMES,
    PICKUP_NAMES,
    IdentityFactory,
    Plan,
    plan_generic_identifiers,
    resolve,
)
from pipeline.generator.calibration import Calibration, implied_overall_rate, type_return_targets
from pipeline.generator.catalogue import Catalogue, build_catalogue
from pipeline.generator.config import (
    BASE_ORIGIN,
    DEFECT_BOOST_WEEKS,
    END_US,
    MATURITY_DAYS,
    PERIOD_FIRST_DAY,
    PERIOD_LAST_DAY,
    SCENARIO_NAMES,
    SCENARIOS,
    US_PER_DAY,
    ScenarioConfig,
)
from pipeline.generator.identity import SALT_BYTES, Hasher
from pipeline.generator.orders import (
    Coupons,
    Deliveries,
    OrderPlan,
    add_scripted,
    boost_defect_windows,
    orders_by_customer,
    plan_coupons,
    plan_deliveries,
    plan_generic_orders,
    subtotals,
)
from pipeline.generator.population import Population, build_population
from pipeline.generator.returns import (
    Outcome,
    ReturnRows,
    calibrate,
    draw_outcomes,
    schedule_returns,
)
from pipeline.generator.scripts import script_golden
from pipeline.generator.timeline import Calendar

STORE_TABLES = (
    "store/customers",
    "store/identifiers",
    "store/variants",
    "store/terms_snapshots",
    "store/orders",
    "store/order_lines",
    "store/deliveries",
    "store/returns",
    "store/return_events",
    "store/coupons",
)
TRUTH_TABLES = ("truth/labels", "truth/variants")
STREAMS = (
    "catalogue",
    "population",
    "identity",
    "golden",
    "orders",
    "coupons",
    "deliveries",
    "returns",
    "states",
    "terms",
    "salt",
    "defects",  # appended last: the other streams keep their spawn keys
)
DETECTOR_MIN_WEEKLY_SALES = 20  # spec 8.9: a variant is assessed only with 20 sales in the week


def scenario_roots(out: Path | str, scenario: str) -> tuple[Path, Path]:
    """The contracts.io roots of a scenario: (store root, truth root). Main: <out> for both, so
    <out>/store and <out>/truth. OOD: <out>/ood/store and <out>/truth/ood/truth, so every ground
    truth lives under <out>/truth (rule 12)."""
    if scenario == "main":
        return Path(out), Path(out)
    return Path(out) / scenario, Path(out) / "truth" / scenario


def stream(seed: int, cfg: ScenarioConfig, name: str) -> np.random.Generator:
    """An independent PCG64 stream per component and scenario, derived from the seed only."""
    key = (cfg.index, STREAMS.index(name))
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence(seed, spawn_key=key)))


@dataclass
class Store:
    cfg: ScenarioConfig
    cat: Catalogue
    pop: Population
    plan: OrderPlan
    coupons: Coupons
    order_coupon: npt.NDArray[np.int64]
    deliveries: Deliveries
    outcome: Outcome
    rows: ReturnRows
    identifier_rows: tuple[list[int], list[str], list[str], list[int]]
    targets: npt.NDArray[np.float64]


def build_store(cal: Calibration, seed: int, scale: float, cfg: ScenarioConfig) -> Store:
    cat = build_catalogue(cal, scale, cfg, stream(seed, cfg, "catalogue"))
    pop = build_population(scale, cfg, stream(seed, cfg, "population"))

    factory = IdentityFactory(stream(seed, cfg, "identity"), pop.n)
    pickups = [factory.address(PICKUP_NAMES[p % len(PICKUP_NAMES)]) for p in range(pop.n_pickup)]
    collectives = [
        factory.address(COLLECTIVE_NAMES[c % len(COLLECTIVE_NAMES)])
        for c in range(pop.n_collective)
    ]
    ids = Plan()
    scripted = (
        script_golden(pop, cat, factory, ids, pickups, stream(seed, cfg, "golden"))
        if cfg.golden
        else []
    )
    plan_generic_identifiers(pop, cfg, factory, ids, pickups, collectives)

    plan = plan_generic_orders(pop, cat, cal, cfg, stream(seed, cfg, "orders"), Calendar())
    boost_defect_windows(plan, cat, stream(seed, cfg, "defects"))
    add_scripted(plan, scripted)
    coupons, order_coupon = plan_coupons(plan, pop, cat, cfg, stream(seed, cfg, "coupons"))
    deliveries = plan_deliveries(plan, stream(seed, cfg, "deliveries"))

    by_type = type_return_targets(cal)
    targets = np.asarray([by_type[name] for name in cat.type_names], dtype=np.float64)
    k, reachable = calibrate(plan, cat, pop, deliveries, targets, cfg)
    returns_rng = stream(seed, cfg, "returns")
    outcome = draw_outcomes(plan, cat, pop, deliveries, k, reachable, cfg, returns_rng)
    rows = schedule_returns(plan, deliveries, outcome, stream(seed, cfg, "states"))

    order_times = [
        [int(plan.placed_at[o]) for o in mine] for mine in orders_by_customer(plan, pop.n)
    ]
    salt_rng = stream(seed, cfg, "salt")
    hasher = Hasher(salt_rng.bytes(SALT_BYTES))
    identifier_rows = resolve(ids.uses, pop, order_times, hasher, salt_rng)
    return Store(
        cfg,
        cat,
        pop,
        plan,
        coupons,
        order_coupon,
        deliveries,
        outcome,
        rows,
        identifier_rows,
        targets,
    )


def to_tables(store: Store, seed: int) -> dict[str, pa.Table]:
    s = store
    numbering = tables.Numbering(s.plan, s.pop, s.rows, s.cfg)
    terms_rng = stream(seed, s.cfg, "terms")
    return {
        "store/customers": tables.customers(s.pop),
        "store/identifiers": tables.identifiers(s.pop, s.identifier_rows),
        "store/variants": tables.variants(s.cat),
        "store/terms_snapshots": tables.terms_snapshots(s.plan, numbering, terms_rng),
        "store/orders": tables.orders(
            s.plan, s.pop, numbering, s.coupons, s.order_coupon, subtotals(s.plan, s.cat)
        ),
        "store/order_lines": tables.order_lines(s.plan, s.cat, numbering),
        "store/deliveries": tables.deliveries(s.deliveries, numbering),
        "store/returns": tables.returns(s.rows, numbering),
        "store/return_events": tables.return_events(s.rows, numbering),
        "store/coupons": tables.coupon_log(s.coupons, s.pop, s.plan, numbering),
        "truth/labels": tables.labels(s.pop, s.cfg),
        "truth/variants": tables.truth_variants(s.cat),
    }


def provenance(cal: Calibration, seed: int, scale: float, cfg: ScenarioConfig) -> Provenance:
    """Origin names the calibration it was built from, so an illustrative calibration is never
    mistaken for ASOS measurements (rule 11). The period is the store period."""
    origin = (
        f"{BASE_ORIGIN} (calibration: {cal.origin}; calibration nature {cal.nature}; "
        f"sha256 {cal.sha256[:12] or 'n/a'}; seed {seed}, scale {scale:g}, scenario {cfg.name}; "
        f"store period {PERIOD_FIRST_DAY.isoformat()}..{PERIOD_LAST_DAY.isoformat()})"
    )
    return Provenance(
        origin=origin,
        period_start=PERIOD_FIRST_DAY,
        period_end=PERIOD_LAST_DAY,
        nature="synthetic",
    )


def table_provenance(prov: Provenance, name: str, table: pa.Table) -> Provenance:
    """The provenance of one file: its period starts with its oldest event, at the latest on
    the first day of the store (accounts and listings older than the store, rule 11)."""
    if name not in EVENT_TABLES or table.num_rows == 0:
        return prov
    first: date = pc.min(table.column("event_at").cast(pa.date32())).as_py()
    return replace(prov, period_start=min(first, prov.period_start))


def generate(
    cal: Calibration, out: Path | str, seed: int, scale: float, scenario: str
) -> dict[str, Any]:
    """Generate one scenario of the store under `out` and return its summary."""
    if scenario not in SCENARIO_NAMES:
        raise ValueError(f"scenario must be one of {SCENARIO_NAMES}, not {scenario!r}")
    if not (math.isfinite(scale) and scale > 0):
        raise ValueError(f"scale must be a positive number, not {scale!r}")
    if seed < 0:
        raise ValueError("seed must be a non-negative integer")
    cfg = SCENARIOS[scenario]
    store = build_store(cal, seed, scale, cfg)
    built = to_tables(store, seed)
    store_root, truth_root = scenario_roots(out, scenario)
    prov = provenance(cal, seed, scale, cfg)
    for name in (*STORE_TABLES, *TRUTH_TABLES):
        root = truth_root if name in TRUTH_TABLES else store_root
        write_table(name, built[name], root, table_provenance(prov, name, built[name]))
    return summarize(store, built, cal, seed, scale, prov, store_root, truth_root)


# ---- Summary ----------------------------------------------------------------------------------


def _rate(returned: npt.NDArray[np.bool_]) -> float | None:
    return round(float(returned.mean()), 4) if len(returned) else None


def defect_detectability(store: Store) -> dict[str, Any]:
    """Weekly sales of each defective variant in the full Monday-to-Sunday weeks of its window,
    against the spec 8.9 minimum of 20 sales a week (US-208 can evaluate only those weeks)."""
    cat, plan = store.cat, store.plan
    week = 7 * US_PER_DAY
    placed = plan.placed_at[plan.line_order]
    reaching = 0
    weeks_total = weeks_ok = boosted_total = boosted_ok = 0
    defective = np.flatnonzero(cat.defective)
    for v in defective:
        start = int(cat.defect_start[v])
        end = END_US if cat.defect_end[v] < 0 else int(cat.defect_end[v])
        mine = placed[plan.line_variant == v]
        sales = [
            int(np.count_nonzero((mine >= lo) & (mine < lo + week)))
            for lo in range(start, end - week + 1, week)
        ]
        ok = [n >= DETECTOR_MIN_WEEKLY_SALES for n in sales]
        reaching += any(ok)
        weeks_total, weeks_ok = weeks_total + len(ok), weeks_ok + sum(ok)
        first = ok[:DEFECT_BOOST_WEEKS]
        boosted_total, boosted_ok = boosted_total + len(first), boosted_ok + sum(first)
    return {
        "measured_on": "order lines of each full week (Monday to Sunday) of the defect window",
        "min_weekly_sales": DETECTOR_MIN_WEEKLY_SALES,
        "defective_variants": len(defective),
        "variants_reaching_the_minimum": reaching,
        "window_weeks": weeks_total,
        "window_weeks_reaching_the_minimum": weeks_ok,
        "boosted_weeks": boosted_total,
        "boosted_weeks_reaching_the_minimum": boosted_ok,
    }


def summarize(
    store: Store,
    built: dict[str, pa.Table],
    cal: Calibration,
    seed: int,
    scale: float,
    prov: Provenance,
    root: Path,
    truth_root: Path,
) -> dict[str, Any]:
    """What was written, with the measured return rates against the calibration (synthetic
    numbers measured on the generated store)."""
    s = store
    plan, cat, pop = s.plan, s.cat, s.pop
    delivered = s.deliveries.delivered_at[plan.line_order] >= 0
    observed = np.zeros(plan.n_lines, dtype=np.bool_)
    observed[s.rows.line] = True
    mature = delivered & (plan.placed_at[plan.line_order] < END_US - MATURITY_DAYS * US_PER_DAY)
    types = cat.type_index[plan.line_variant]
    by_type: dict[str, Any] = {}
    for t, name in enumerate(cat.type_names):
        lines = mature & (types == t)
        by_type[name] = {
            "target": round(float(s.targets[t]), 4),
            "mature_lines": int(lines.sum()),
            "rate": _rate(observed[lines]),
            "k": round(float(s.outcome.k_by_type[t]), 4),
            "target_reachable": bool(s.outcome.reachable[t]),
        }
    rate = _rate(observed[mature])
    target = cal.product_sales_weighted_return_rate
    price = cat.price_cents[plan.line_variant]
    customer = plan.customer[plan.line_order]
    abusive_customer = [b in ("wardrober", "promo_ring") for b in pop.behaviour]
    abusive = np.asarray(abusive_customer, dtype=np.bool_)[customer]
    returned_value = float(price[observed].sum())
    abuse_value = float(price[observed & abusive].sum())
    behaviours = {b: pop.behaviour.count(b) for b in sorted(set(pop.behaviour))}
    pickup_sizes = np.bincount(pop.pickup_of[pop.pickup_of >= 0], minlength=pop.n_pickup)
    return {
        "nature": "synthetic",
        "origin": prov.origin,
        "period": {"start": prov.period_start.isoformat(), "end": prov.period_end.isoformat()},
        "scenario": s.cfg.name,
        "seed": seed,
        "scale": scale,
        "root": str(root),
        "truth_root": str(truth_root),
        "calibration": {
            "origin": cal.origin,
            "nature": cal.nature,
            "sha256": cal.sha256,
            "product_sales_weighted_return_rate": target,
            "event_label_rate": cal.event_label_rate,
            "implied_by_type_quantiles": round(implied_overall_rate(cal), 4),
        },
        "rows": {name: built[name].num_rows for name in (*STORE_TABLES, *TRUTH_TABLES)},
        "customers_by_behaviour": behaviours,
        "rings": len(pop.rings),
        "families": len(pop.families),
        "card_sharing_families": sum(1 for f in pop.families if f.card_pair is not None),
        "pickup_points": pop.n_pickup,
        "pickup_customers_max": int(pickup_sizes.max()) if len(pickup_sizes) else 0,
        "collective_addresses": pop.n_collective,
        "defective_variants": int(cat.defective.sum()),
        "defect_detectability": defect_detectability(s),
        "return_rate": {
            "measured_on": f"delivered lines ordered at least {MATURITY_DAYS} days before the end",
            "mature_lines": int(mature.sum()),
            "rate": rate,
            "target": target,
            "relative_error": round(rate / target - 1, 4) if rate is not None and target else None,
            "all_delivered_lines_rate": _rate(observed[delivered]),
            "by_type": by_type,
        },
        "abuse_share_of_returned_value": round(abuse_value / returned_value, 4)
        if returned_value
        else None,
        "golden": list(golden.GOLDEN_CUSTOMERS) if s.cfg.golden else [],
    }
