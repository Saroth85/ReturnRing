"""Navigli Fashion synthetic store: US-102, US-103, US-104, US-105 (DATA-01..DATA-05, MOD-12).

The stores are generated at small scales from two calibrations:

- contracts/examples/calibration.json, an illustrative calibration whose numbers are not measured;
- pipeline/calibration/asos_calibration.json, the committed aggregates of the real ASOS train split
  (origin: ASOS GraphReturns, OSF c793h; nature: real aggregates), so CI covers the real file.

Every rate checked here is synthetic, measured on the generated store against its calibration
(origin: Navigli Fashion generator; nature: synthetic).
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from contracts.io import Provenance, read_table, validate_table
from contracts.parquet import SCHEMAS
from pipeline.generator import golden
from pipeline.generator.calibration import Calibration, load_calibration, type_return_targets
from pipeline.generator.catalogue import SIZE_RANGE, SizeKind, size_kind_for
from pipeline.generator.config import (
    BASE_ORIGIN,
    DEFECT_BOOST_WEEKS,
    MATURITY_DAYS,
    PERIOD_END,
    PERIOD_LAST_DAY,
    PERIOD_START,
    SCENARIOS,
    SURGE_CONTROLS_PER_DEFECT,
)
from pipeline.generator.core import (
    STORE_TABLES,
    TRUTH_TABLES,
    build_store,
    generate,
    scenario_roots,
)
from pipeline.generator.timeline import weekday
from policy.engine import NAVIGLI_BASE_TERMS
from policy.legal_guard import LEGAL_REFUND_DAYS
from policy.types import NON_CONFORMITY_REASONS, ReturnReason

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "contracts" / "examples" / "calibration.json"
REAL = ROOT / "pipeline" / "calibration" / "asos_calibration.json"
CAL = load_calibration(EXAMPLE)
REAL_CAL = load_calibration(REAL)
DAY = pd.Timedelta(days=1)
START = pd.Timestamp(PERIOD_START)
END = pd.Timestamp(PERIOD_END)
NON_CONFORMITY = {r.value for r in NON_CONFORMITY_REASONS}
STRONG = ("card", "email", "device", "address", "phone")


@dataclass
class Store:
    root: Path  # store tables: <root>/store/*.parquet
    truth_root: Path  # ground truth: <truth_root>/truth/*.parquet
    scale: float
    scenario: str
    cal: Calibration
    t: dict[str, pd.DataFrame]
    provenance: dict[str, Provenance]

    def root_of(self, name: str) -> Path:
        return self.truth_root if name.startswith("truth/") else self.root

    def __getitem__(self, name: str) -> pd.DataFrame:
        return self.t[name]

    # ---- derived views -------------------------------------------------------------------
    def lines(self) -> pd.DataFrame:
        """Order lines with order, customer, delivery, return and truth label columns."""
        lines = self["store/order_lines"].rename(columns={"event_at": "ordered_at"})
        orders = self["store/orders"][["order_id", "customer_id"]]
        out = lines.merge(orders, on="order_id", validate="many_to_one")
        deliveries = self["store/deliveries"][["order_id", "event_at"]].rename(
            columns={"event_at": "delivered_at"}
        )
        out = out.merge(deliveries, on="order_id", how="left", validate="many_to_one")
        returns = self["store/returns"][["line_id", "return_id", "reason", "event_at"]].rename(
            columns={"event_at": "requested_at"}
        )
        out = out.merge(returns, on="line_id", how="left", validate="one_to_one")
        out["returned"] = out["return_id"].notna()
        out["price"] = out["price_eur"].astype(float)
        variants = self["store/variants"][["variant_id", "category", "product_id"]]
        out = out.merge(variants, on="variant_id", validate="many_to_one")
        labels = self["truth/labels"][["customer_id", "behaviour", "ring_id", "family_id"]]
        return out.merge(labels, on="customer_id", validate="many_to_one")

    def identifiers_of(self, customers: list[str]) -> pd.DataFrame:
        ids = self["store/identifiers"]
        return ids[ids["customer_id"].isin(customers)]


def _load(out: Path, scale: float, scenario: str, cal: Calibration) -> Store:
    store_root, truth_root = scenario_roots(out, scenario)
    tables: dict[str, pd.DataFrame] = {}
    provenance: dict[str, Provenance] = {}
    for name in (*STORE_TABLES, *TRUTH_TABLES):
        table, prov = read_table(name, truth_root if name.startswith("truth/") else store_root)
        tables[name] = table.to_pandas()
        provenance[name] = prov
    return Store(store_root, truth_root, scale, scenario, cal, tables, provenance)


def _generated(
    factory: pytest.TempPathFactory, cal: Calibration, seed: int, scale: float, scenario: str
) -> Store:
    out = factory.mktemp(f"store-{scenario}")
    generate(cal, out, seed=seed, scale=scale, scenario=scenario)
    return _load(out, scale, scenario, cal)


@pytest.fixture(scope="session")
def main_store(tmp_path_factory: pytest.TempPathFactory) -> Store:
    return _generated(tmp_path_factory, CAL, 42, 0.1, "main")


@pytest.fixture(scope="session")
def real_store(tmp_path_factory: pytest.TempPathFactory) -> Store:
    """The committed real calibration (ASOS train-split aggregates) at scale 0.25: its types
    are smaller than the example's, so a quarter of the store gives every DATA-02 check enough
    lines."""
    return _generated(tmp_path_factory, REAL_CAL, 42, 0.25, "main")


@pytest.fixture(scope="session")
def tiny_store(tmp_path_factory: pytest.TempPathFactory) -> Store:
    return _generated(tmp_path_factory, CAL, 7, 0.02, "main")


@pytest.fixture(scope="session")
def ood_store(tmp_path_factory: pytest.TempPathFactory) -> Store:
    return _generated(tmp_path_factory, CAL, 42, 0.1, "ood")


@pytest.fixture(scope="session")
def real_ood_store(tmp_path_factory: pytest.TempPathFactory) -> Store:
    return _generated(tmp_path_factory, REAL_CAL, 42, 0.1, "ood")


@pytest.fixture(params=["main_store", "real_store"])
def calibrated(request: pytest.FixtureRequest) -> Store:
    """Stores large enough to compare rates with their calibration (scale 0.1 and 0.25)."""
    store: Store = request.getfixturevalue(request.param)
    return store


@pytest.fixture(params=["main_store", "real_store", "tiny_store"])
def any_main(request: pytest.FixtureRequest) -> Store:
    store: Store = request.getfixturevalue(request.param)
    return store


@pytest.fixture(params=["main_store", "real_store", "tiny_store", "ood_store", "real_ood_store"])
def any_store(request: pytest.FixtureRequest) -> Store:
    store: Store = request.getfixturevalue(request.param)
    return store


def mature(lines: pd.DataFrame) -> pd.DataFrame:
    """Delivered lines ordered at least MATURITY_DAYS before the end of the period: every return
    they will have is already in the data."""
    cutoff = END - pd.Timedelta(days=MATURITY_DAYS)
    return lines[lines["delivered_at"].notna() & (lines["ordered_at"] < cutoff)]


# ---- DATA-01: every output passes its contract ------------------------------------------------


def test_data01_every_table_is_written_and_valid(any_store: Store) -> None:
    for name in (*STORE_TABLES, *TRUTH_TABLES):
        table, prov = read_table(name, any_store.root_of(name))
        assert validate_table(name, table) == [], name
        assert table.num_rows > 0, name
        assert prov.nature == "synthetic"
        assert prov.origin.startswith(BASE_ORIGIN)
        assert any_store.cal.origin in prov.origin  # the calibration says what it is
        assert "store period 2025-10-01..2026-09-30" in prov.origin
        assert prov.period_end == (PERIOD_END - timedelta(days=1)).date() == PERIOD_LAST_DAY
        # The period covers every event of the file (C11): accounts and listings start earlier.
        if name.startswith("store/"):
            first = any_store[name]["event_at"].min().date()
            assert prov.period_start == min(first, PERIOD_START.date()), name
        else:
            assert prov.period_start == PERIOD_START.date(), name
    assert any_store.provenance["store/orders"].period_start == PERIOD_START.date()
    assert any_store.provenance["store/customers"].period_start < PERIOD_START.date()


def test_data01_the_store_holds_exactly_the_contract_tables(any_store: Store) -> None:
    found = sorted(str(p.relative_to(any_store.root)) for p in any_store.root.rglob("*.parquet"))
    if any_store.scenario == "main":  # <out>/store next to <out>/truth and <out>/ood
        found = [f for f in found if f.startswith("store/")]
    assert found == sorted(f"{name}.parquet" for name in STORE_TABLES)
    truth = sorted(
        str(p.relative_to(any_store.truth_root)) for p in any_store.truth_root.glob("truth/*")
    )
    assert truth == sorted(f"{name}.parquet" for name in TRUTH_TABLES)
    assert set(STORE_TABLES) == {n for n in SCHEMAS if n.startswith("store/")}
    assert set(TRUTH_TABLES) == {n for n in SCHEMAS if n.startswith("truth/")}


def test_g5_ood_truth_lives_under_the_truth_folder(tmp_path: Path) -> None:
    """Rule 12: one folder, <out>/truth, holds every ground truth, so a mount that leaves out
    data/truth leaves out the OOD truth too. The OOD store goes to <out>/ood/store."""
    generate(CAL, tmp_path, seed=3, scale=0.005, scenario="ood")
    files = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*.parquet"))
    assert files == sorted(
        [f"ood/{name}.parquet" for name in STORE_TABLES]
        + [f"truth/ood/{name}.parquet" for name in TRUTH_TABLES]
    )
    assert scenario_roots(tmp_path, "ood") == (tmp_path / "ood", tmp_path / "truth" / "ood")
    assert scenario_roots(tmp_path, "main") == (tmp_path, tmp_path)
    assert all("truth" not in Path(f).parts for f in files if not f.startswith("truth/"))


def test_referential_integrity(any_store: Store) -> None:
    s = any_store
    customers = set(s["store/customers"]["customer_id"])
    orders = s["store/orders"]
    assert set(orders["customer_id"]) <= customers
    assert set(s["store/order_lines"]["order_id"]) == set(orders["order_id"])
    assert set(s["store/order_lines"]["variant_id"]) <= set(s["store/variants"]["variant_id"])
    assert set(s["store/deliveries"]["order_id"]) <= set(orders["order_id"])
    assert set(s["store/returns"]["line_id"]) <= set(s["store/order_lines"]["line_id"])
    assert set(s["store/return_events"]["return_id"]) == set(s["store/returns"]["return_id"])
    assert set(s["store/identifiers"]["customer_id"]) <= customers
    assert set(s["store/coupons"]["customer_id"]) <= customers
    terms = s["store/terms_snapshots"]
    assert set(terms["order_id"]) == set(orders["order_id"])
    merged = orders.merge(terms, on="order_id", suffixes=("", "_terms"))
    assert (merged["terms_snapshot_id"] == merged["terms_snapshot_id_terms"]).all()
    # Truth covers exactly the store, one row each (rule 12 keeps it in its own folder).
    assert set(s["truth/labels"]["customer_id"]) == customers
    assert set(s["truth/variants"]["variant_id"]) == set(s["store/variants"]["variant_id"])
    # Order totals: lines minus the redeemed coupon.
    lines = s["store/order_lines"].assign(price=lambda d: d["price_eur"].astype(float))
    subtotal = lines.groupby("order_id")["price"].sum()
    coupons = s["store/coupons"]
    redeemed = coupons[coupons["order_id"].notna()].set_index("order_id")["discount_eur"]
    discount = redeemed.astype(float).reindex(subtotal.index, fill_value=0.0)
    total = orders.set_index("order_id")["total_eur"].astype(float).reindex(subtotal.index)
    assert (total - (subtotal - discount)).abs().max() < 0.005
    with_code = orders[orders["coupon_code"].notna()].set_index("order_id")["coupon_code"]
    assert (
        with_code.to_dict()
        == coupons[coupons["order_id"].notna()].set_index("order_id")["coupon_code"].to_dict()
    )


def test_terms_snapshots_hold_the_navigli_base_policy(any_store: Store) -> None:
    terms = any_store["store/terms_snapshots"]
    assert (terms["source"] == "base").all()
    assert terms["quadrant_at_purchase"].isna().all()
    base = NAVIGLI_BASE_TERMS
    assert (terms["return_window_days"] == base.return_window_days).all()
    assert (terms["free_return_shipping"] == base.free_return_shipping).all()
    assert (terms["instant_refund"] == base.instant_refund).all()
    assert (terms["free_size_exchange"] == base.free_size_exchange).all()
    assert (terms["coupons_enabled"] == base.coupons_enabled).all()


# ---- DATA-02: calibration ----------------------------------------------------------------------


def test_data02_overall_return_rate_within_10_percent(calibrated: Store) -> None:
    lines = mature(calibrated.lines())
    rate = lines["returned"].mean()
    target = calibrated.cal.product_sales_weighted_return_rate
    assert abs(rate / target - 1) <= 0.10, (rate, target)


def test_data02_return_rate_per_product_type_within_10_percent(calibrated: Store) -> None:
    """Every type with at least 2,000 mature lines is within 10% of its target. A smaller type
    is within 10% or 3 binomial standard errors, whichever is wider."""
    lines = mature(calibrated.lines())
    targets = type_return_targets(calibrated.cal)
    strict = 0
    for category, group in lines.groupby("category"):
        rate, target, n = group["returned"].mean(), targets[str(category)], len(group)
        if n >= 2000:
            assert abs(rate / target - 1) <= 0.10, (category, rate, target)
            strict += 1
        elif n >= 200:
            se = (target * (1 - target) / n) ** 0.5
            assert abs(rate - target) <= max(0.10 * target, 3 * se), (category, rate, target, n)
    assert strict >= min(3, len(calibrated.cal.product_types))


def _weighted_quantile(values: pd.Series, weights: pd.Series, q: float) -> float:
    order = values.argsort()
    v, w = values.to_numpy()[order], weights.to_numpy()[order]
    cum = w.cumsum() / w.sum()
    return float(v[(cum >= q).argmax()])


def test_data02_catalogue_follows_the_type_distributions(calibrated: Store) -> None:
    variants = calibrated["store/variants"]
    truth = calibrated["truth/variants"].merge(variants, on="variant_id")
    lines = calibrated.lines()
    sold = lines.groupby("variant_id").size()
    for t in calibrated.cal.product_types:
        share = (variants["category"] == t.name).mean()
        assert share == pytest.approx(t.variant_share, abs=0.01), t.name
        sales = (lines["category"] == t.name).mean()
        assert sales == pytest.approx(t.sales_share, abs=0.04), t.name
        of_type = truth[truth["category"] == t.name]
        prices = of_type.drop_duplicates("product_id")["price_eur"].astype(float)
        p50_eur = t.price_gbp.p50 * 1.17
        assert prices.median() == pytest.approx(p50_eur, rel=0.10), t.name
        # The calibration's rate quantiles are weighted by sales: so are the store's, on the
        # types that sell enough lines for a stable weighted quantile. The defective variants are
        # left out: their window lift (G6) adds sales on purpose.
        if (lines["category"] == t.name).sum() < 2000:
            continue
        rates = of_type[~of_type["defective"]].set_index("variant_id")["base_return_rate"]
        weights = sold.reindex(rates.index, fill_value=0)
        for q, expected in zip(
            (0.25, 0.5, 0.75),
            (t.return_rate.p25, t.return_rate.p50, t.return_rate.p75),
            strict=True,
        ):
            found = _weighted_quantile(rates, weights, q)
            assert found == pytest.approx(expected, abs=0.04), (t.name, q)


def test_data02_volumes_scale_with_the_spec(calibrated: Store) -> None:
    """Spec section 7 at scale 1: 20,000 customers, ~5,000 variants, ~80,000 orders,
    ~200,000 lines, ~60,000 returns. Scale 0.1 here."""
    s, k = calibrated, calibrated.scale
    assert len(s["store/customers"]) == round(20_000 * k)
    assert len(s["store/variants"]) == round(5_000 * k)
    assert 0.75 * 80_000 * k <= len(s["store/orders"]) <= 1.25 * 80_000 * k
    assert 0.75 * 200_000 * k <= len(s["store/order_lines"]) <= 1.25 * 200_000 * k
    assert 0.6 * 60_000 * k <= len(s["store/returns"]) <= 1.4 * 60_000 * k


# ---- DATA-03: ground truth separated -----------------------------------------------------------

LABEL_COLUMNS = {
    "behaviour",
    "ring_id",
    "family_id",
    "scenario",
    "base_return_rate",
    "defective",
    "defect_reason",
    "defect_start",
    "defect_end",
}


def test_data03_no_store_table_carries_labels(any_store: Store) -> None:
    for name in STORE_TABLES:
        assert not set(SCHEMAS[name].names) & LABEL_COLUMNS, name
        assert not set(any_store[name].columns) & LABEL_COLUMNS, name
    assert all(name.startswith("truth/") for name in TRUTH_TABLES)
    truth_files = {p.parent.name for p in (any_store.truth_root / "truth").glob("*.parquet")}
    assert truth_files == {"truth"}
    assert not list(any_store.root.glob("truth/*.parquet")) or any_store.scenario == "main"


def test_data03_no_ring_or_family_id_leaks_into_store_values(any_store: Store) -> None:
    labels = any_store["truth/labels"]
    secret = set(labels["ring_id"].dropna()) | set(labels["family_id"].dropna())
    assert secret
    for name in STORE_TABLES:
        table, _ = read_table(name, any_store.root)
        values = set(_iter_strings(table))
        assert values, name
        assert not any(s in v for s in secret for v in values), name


def _python_strings(path: Path) -> Iterator[str]:
    """String literals of a module, docstrings excluded: paths, not prose."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        is_text = isinstance(node, ast.Constant) and isinstance(node.value, str)
        if is_text and id(node) not in docstrings:
            yield node.value


def test_data03_running_services_never_reference_truth() -> None:
    """Rule 12: api, ui and demo code never read data/truth/ (only offline jobs do)."""
    checked = 0
    for folder in ("apps", "llm", "policy"):
        for path in (ROOT / folder).rglob("*"):
            if not path.is_file():
                continue
            if path.suffix == ".py":
                strings = list(_python_strings(path))
                assert not [s for s in strings if "truth" in s.lower()], path
                checked += 1
            elif path.suffix in {".ts", ".tsx", ".js", ".jsx", ".html"}:
                text = path.read_text(encoding="utf-8")
                assert "truth/" not in text and "truth\\" not in text, path
                checked += 1
    assert checked > 0


def test_data03_customer_ids_do_not_encode_behaviour(main_store: Store) -> None:
    labels = main_store["truth/labels"]
    generic = labels[labels["customer_id"].str.fullmatch(r"C-\d{6}")]
    assert len(generic) > 0.95 * len(labels)
    # Sequential ids follow account creation, not behaviour: every behaviour spans the id range.
    numbered = generic.assign(n=generic["customer_id"].str[2:].astype(int))
    low, high = numbered["n"].quantile(0.25), numbered["n"].quantile(0.75)
    for behaviour, group in numbered.groupby("behaviour"):
        if behaviour == "promo_ring":
            continue
        assert group["n"].min() < low and group["n"].max() > high, behaviour
    # Ring accounts are new, so their ids are recent, but interleaved with other new accounts.
    ring = numbered[numbered["behaviour"] == "promo_ring"]
    between = numbered[numbered["n"].between(ring["n"].min(), ring["n"].max())]
    assert (between["behaviour"] == "promo_ring").mean() < 0.2


# ---- DATA-04: determinism ----------------------------------------------------------------------


def _run_cli(out: Path, seed: int, hash_seed: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pipeline.generator",
            "--calibration",
            str(EXAMPLE),
            "--out",
            str(out),
            "--seed",
            str(seed),
            "--scale",
            "0.01",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _digests(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*.parquet"))
    }


def test_data04_same_seed_gives_byte_identical_files(tmp_path: Path) -> None:
    """Two processes with different string-hash seeds: no set or dict order leaks into a file."""
    first, second, other = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for out, seed, hash_seed in ((first, 42, "1"), (second, 42, "2"), (other, 43, "1")):
        done = _run_cli(out, seed, hash_seed)
        assert done.returncode == 0, done.stderr
    a, b = _digests(first), _digests(second)
    assert len(a) == len(STORE_TABLES) + len(TRUTH_TABLES)
    assert a == b
    assert _digests(other) != a
    summary = json.loads(_run_cli(first, 42, "3").stdout)
    assert summary["nature"] == "synthetic" and summary["seed"] == 42
    assert _digests(first) == a  # overwriting gives the same bytes


# ---- DATA-05: golden cases ---------------------------------------------------------------------


def _rate(lines: pd.DataFrame) -> float:
    delivered = lines[lines["delivered_at"].notna()]
    return float(delivered["returned"].mean())


def test_data05_golden_ids_are_present(any_main: Store) -> None:
    labels = any_main["truth/labels"].set_index("customer_id")
    expected = {
        golden.GIULIA: ("size", None, None),
        golden.ELENA: ("normal", None, None),
        **{c: ("promo_ring", golden.MARCO_RING, None) for c in golden.MARCO_ACCOUNTS},
        **{c: ("shared_benign", None, golden.ROSSI_FAMILY) for c in golden.ROSSI_ACCOUNTS},
        **{
            c: ("shared_benign", None, golden.ROSSI_CARD_FAMILY) for c in golden.ROSSI_CARD_ACCOUNTS
        },
    }
    for customer, (behaviour, ring, family) in expected.items():
        row = labels.loc[customer]
        assert row["behaviour"] == behaviour, customer
        assert (row["ring_id"] if pd.notna(row["ring_id"]) else None) == ring, customer
        assert (row["family_id"] if pd.notna(row["family_id"]) else None) == family, customer
        assert row["scenario"] == "main"
    assert tuple(f"C-MARCO-{i}" for i in range(1, 8)) == golden.MARCO_ACCOUNTS
    assert (labels["ring_id"] == golden.MARCO_RING).sum() == 7
    assert (labels["family_id"] == golden.ROSSI_FAMILY).sum() == 3
    assert (labels["family_id"] == golden.ROSSI_CARD_FAMILY).sum() == 3
    variants = any_main["store/variants"].set_index("variant_id")
    assert variants.loc[golden.DEFECTIVE_JACKET, "product_id"] == golden.JACKET_PRODUCT
    assert variants.loc[golden.JACKET_SIBLING, "product_id"] == golden.JACKET_PRODUCT


def _giulia_signature(store: Store) -> None:
    lines = store.lines()
    mine = lines[lines["customer_id"] == golden.GIULIA]
    bracketed = 0
    for _, order in mine.groupby("order_id"):
        for _, same in order.groupby("variant_id"):
            assert len(same) <= 2, "an item at most twice in one order: a bracketing pair"
            if len(same) == 2:
                assert same["size"].nunique() == 2
                assert same["returned"].sum() == 1
                assert (same.loc[same["returned"], "reason"] == "size").all()
                bracketed += 1
    assert bracketed == 3  # the three scripted pairs, and no accidental one
    returned = mine[mine["returned"]]
    assert (returned["reason"] == "size").all()
    delivered = mine[mine["delivered_at"].notna()]
    kept = delivered.loc[~delivered["returned"], "price"].sum()
    assert kept >= 0.5 * delivered["price"].sum()


def test_data05_giulia_brackets_sizes_and_keeps_half(any_main: Store) -> None:
    _giulia_signature(any_main)


def _golden_orders_hold_distinct_items(store: Store) -> None:
    """Every golden order holds distinct variants, except a bracketing pair (two sizes)."""
    lines = store.lines()
    mine = lines[lines["customer_id"].isin(golden.GOLDEN_CUSTOMERS)]
    per_item = mine.groupby(["order_id", "variant_id"]).agg(
        n=("line_id", "size"), sizes=("size", "nunique")
    )
    assert (per_item["n"] <= 2).all()
    pairs = per_item[per_item["n"] == 2]
    assert (pairs["sizes"] == 2).all()
    paired = set(pairs.index.get_level_values("order_id"))
    owners = set(mine.loc[mine["order_id"].isin(paired), "customer_id"])
    assert owners <= {golden.GIULIA}


@pytest.mark.parametrize(("seed", "scale"), [(s, 0.005) for s in range(1, 11)] + [(3, 0.25)])
def test_g1_golden_signatures_hold_over_seeds(tmp_path: Path, seed: int, scale: float) -> None:
    """DATA-05 over seeds 1..10 at the smallest scale, and at the scale where the review saw
    Giulia's signature break (0.25, seed 3): scripted orders never repeat an item by chance."""
    generate(CAL, tmp_path, seed=seed, scale=scale, scenario="main")
    store = _load(tmp_path, scale, "main", CAL)
    _golden_orders_hold_distinct_items(store)
    _giulia_signature(store)
    _family_signature(store, golden.ROSSI_ACCOUNTS, shared_cards=0)
    _family_signature(store, golden.ROSSI_CARD_ACCOUNTS, shared_cards=1)


def test_data05_marco_ring_signature(any_main: Store) -> None:
    accounts = list(golden.MARCO_ACCOUNTS)
    customers = any_main["store/customers"].set_index("customer_id").loc[accounts]
    created = customers["event_at"]
    assert (created >= START).all()  # new accounts
    assert created.max() - created.min() <= pd.Timedelta(days=14)
    coupons = any_main["store/coupons"]
    welcome = coupons[(coupons["kind"] == "welcome") & coupons["customer_id"].isin(accounts)]
    issued = welcome[welcome["order_id"].isna()]
    redeemed = welcome[welcome["order_id"].notna()]
    assert sorted(issued["customer_id"]) == sorted(accounts)  # one welcome coupon each
    assert sorted(redeemed["customer_id"]) == sorted(accounts)
    orders = any_main["store/orders"]
    first = orders[orders["customer_id"].isin(accounts)].sort_values("event_at")
    first = first.groupby("customer_id").head(1).set_index("customer_id")["order_id"]
    assert set(redeemed["order_id"]) == set(first)
    ids = any_main.identifiers_of(accounts)
    for kind in ("device", "address", "card"):
        sharers = ids[ids["kind"] == kind].groupby("value_hash")["customer_id"].nunique()
        assert 3 <= sharers.max() < 7, kind  # shared with noise: not by every account
    strong = ids[ids["kind"].isin(["card", "email", "device"])]
    shared = strong.groupby("value_hash")["customer_id"].nunique()
    linked = set(strong[strong["value_hash"].isin(shared[shared >= 2].index)]["customer_id"])
    assert linked == set(accounts)
    lines = any_main.lines()
    assert _rate(lines[lines["customer_id"].isin(accounts)]) >= 0.85


def _family_signature(store: Store, accounts: tuple[str, ...], shared_cards: int) -> None:
    ids = store.identifiers_of(list(accounts))
    for kind in ("address", "device"):
        sharers = ids[ids["kind"] == kind].groupby("value_hash")["customer_id"].nunique()
        assert sharers.max() == len(accounts), kind
    cards = ids[ids["kind"] == "card"].groupby("value_hash")["customer_id"].nunique()
    assert (cards >= 2).sum() == shared_cards
    assert cards.max() <= 2
    assert ids[ids["kind"] == "card"]["customer_id"].nunique() == len(accounts)
    customers = store["store/customers"].set_index("customer_id").loc[list(accounts)]
    assert (customers["event_at"] < START).all()  # old accounts
    coupons = store["store/coupons"]
    assert not coupons["customer_id"].isin(accounts).any()
    lines = store.lines()
    for account in accounts:
        mine = lines[lines["customer_id"] == account]
        assert mine["delivered_at"].notna().sum() >= 3
        assert _rate(mine) <= 0.5, account


def test_data05_rossi_family_signature(any_main: Store) -> None:
    _family_signature(any_main, golden.ROSSI_ACCOUNTS, shared_cards=0)


def test_data05_rossi_card_variant_signature(any_main: Store) -> None:
    _family_signature(any_main, golden.ROSSI_CARD_ACCOUNTS, shared_cards=1)


def test_data05_elena_returns_the_defective_jacket_in_its_window(any_main: Store) -> None:
    truth = any_main["truth/variants"].set_index("variant_id").loc[golden.DEFECTIVE_JACKET]
    assert truth["defective"]
    assert truth["defect_reason"] in NON_CONFORMITY
    start = truth["defect_start"]
    end = truth["defect_end"] if pd.notna(truth["defect_end"]) else END
    lines = any_main.lines()
    jacket = lines[
        (lines["variant_id"] == golden.DEFECTIVE_JACKET)
        & lines["returned"]
        & (lines["reason"] == truth["defect_reason"])
        & (lines["ordered_at"] >= start)
        & (lines["ordered_at"] < end)
        & (lines["requested_at"] < end)
    ]
    returners = sorted(set(jacket["customer_id"]))
    assert golden.ELENA in returners
    # Independent customers: no ring, no family, no strong identifier shared with another
    # returner of the jacket (Elena included).
    ids = any_main.identifiers_of(returners)
    strong = ids[ids["kind"].isin(STRONG)]
    sharers = strong.groupby("value_hash")["customer_id"].nunique()
    linked = set(strong.loc[strong["value_hash"].isin(sharers[sharers >= 2].index), "customer_id"])
    labels = any_main["truth/labels"].set_index("customer_id").loc[returners]
    grouped = set(labels.index[labels["ring_id"].notna() | labels["family_id"].notna()])
    assert golden.ELENA not in linked
    independent = set(returners) - linked - grouped - {golden.ELENA}
    assert len(independent) >= 5
    sibling = any_main["truth/variants"].set_index("variant_id").loc[golden.JACKET_SIBLING]
    assert not sibling["defective"]


def test_data05_demo_cases_have_a_pending_return(any_main: Store) -> None:
    """The demo queue: a return requested in the last week, notified and nothing else yet."""
    events = any_main["store/return_events"]
    states = events.groupby("return_id")["state"].agg(set)
    lines = any_main.lines()
    for customer in (
        golden.GIULIA,
        golden.MARCO_ACCOUNTS[-1],
        golden.ROSSI_ACCOUNTS[0],
        golden.ELENA,
    ):
        mine = lines[(lines["customer_id"] == customer) & lines["returned"]]
        pending = [
            r
            for r, at in zip(mine["return_id"], mine["requested_at"], strict=True)
            if states[r] == {"notified"} and at >= END - pd.Timedelta(days=7)
        ]
        assert pending, customer


# ---- Temporal consistency (rule 7, CON-01) -----------------------------------------------------


def test_every_row_is_available_before_the_extraction(any_store: Store) -> None:
    for name in STORE_TABLES:
        frame = any_store[name]
        assert (frame["available_at"] < END).all(), name
        assert (frame["event_at"] <= frame["available_at"]).all(), name


def test_orders_follow_account_creation_and_terms(any_store: Store) -> None:
    s = any_store
    created = s["store/customers"].set_index("customer_id")["event_at"]
    orders = s["store/orders"]
    assert (orders["event_at"] >= orders["customer_id"].map(created)).all()
    assert (orders["event_at"] >= START).all()
    placed = orders.set_index("order_id")["event_at"]
    lines = s["store/order_lines"]
    assert (lines["event_at"] == lines["order_id"].map(placed)).all()
    terms = s["store/terms_snapshots"]
    shown = terms["event_at"]
    assert (shown <= terms["order_id"].map(placed)).all()
    assert (shown >= terms["order_id"].map(placed) - pd.Timedelta(hours=1)).all()
    listed = s["store/variants"].set_index("variant_id")["event_at"]
    assert (lines["event_at"] >= lines["variant_id"].map(listed)).all()


def test_deliveries_and_returns_are_in_order(any_store: Store) -> None:
    s = any_store
    placed = s["store/orders"].set_index("order_id")["event_at"]
    deliveries = s["store/deliveries"].set_index("order_id")
    assert (deliveries["event_at"] > deliveries.index.map(placed)).all()
    assert (deliveries["event_at"] - deliveries.index.map(placed) <= pd.Timedelta(days=8)).all()
    assert (deliveries["event_at"].dt.dayofweek < 6).all()  # no Sunday deliveries
    returns = s["store/returns"].merge(
        s["store/order_lines"][["line_id", "order_id"]], on="line_id", validate="one_to_one"
    )
    delivered = returns["order_id"].map(deliveries["event_at"])
    known = returns["order_id"].map(deliveries["available_at"])
    assert delivered.notna().all()  # no return before a delivery
    assert (returns["event_at"] >= known).all()
    days = (returns["event_at"] - delivered) // DAY
    assert (days == returns["days_since_delivery"]).all()
    withdrawal = ~returns["reason"].isin(NON_CONFORMITY)
    window = NAVIGLI_BASE_TERMS.return_window_days
    assert (returns.loc[withdrawal, "days_since_delivery"] < window).all()
    assert returns["reason"].isin({r.value for r in ReturnReason}).all()


def test_return_states_follow_the_request(any_store: Store) -> None:
    s = any_store
    events = s["store/return_events"].pivot(index="return_id", columns="state", values="event_at")
    requests = s["store/returns"].set_index("return_id")
    assert (events["notified"] == requests["event_at"].reindex(events.index)).all()
    assert "rejected" not in events.columns
    shipped = events["shipped_or_proof"].dropna()
    assert (shipped > events.loc[shipped.index, "notified"]).all()
    received = events["received"].dropna()
    assert events.loc[received.index, "shipped_or_proof"].notna().all()
    assert (received > events.loc[received.index, "shipped_or_proof"]).all()
    refunded = events["refunded"].dropna()
    assert events.loc[refunded.index, "shipped_or_proof"].notna().all()
    assert (refunded >= events.loc[refunded.index, "shipped_or_proof"]).all()
    # Rule 3: refunds within 14 days of the notice.
    late = refunded - events.loc[refunded.index, "notified"]
    assert (late <= pd.Timedelta(days=LEGAL_REFUND_DAYS)).all()
    guarantee = requests.index[requests["reason"].isin(NON_CONFORMITY)]
    paid = refunded.index.intersection(guarantee)
    assert (refunded[paid] > events.loc[paid, "received"]).all()  # after inspection


def test_coupons_and_identifiers_follow_account_creation(any_store: Store) -> None:
    s = any_store
    created = s["store/customers"].set_index("customer_id")["event_at"]
    ids = s["store/identifiers"]
    assert (ids["event_at"] >= ids["customer_id"].map(created)).all()
    coupons = s["store/coupons"]
    issued = coupons[coupons["order_id"].isna()].set_index("coupon_code")
    assert issued.index.is_unique
    assert (issued["event_at"] >= issued["customer_id"].map(created)).all()
    redeemed = coupons[coupons["order_id"].notna()]
    assert redeemed["coupon_code"].is_unique  # one use per coupon
    assert set(redeemed["coupon_code"]) <= set(issued.index)
    assert (redeemed["event_at"] >= redeemed["coupon_code"].map(issued["event_at"])).all()
    orders = s["store/orders"].set_index("order_id")
    assert (redeemed["event_at"] == redeemed["order_id"].map(orders["event_at"])).all()
    assert (redeemed["customer_id"] == redeemed["order_id"].map(orders["customer_id"])).all()
    welcome = coupons[coupons["kind"] == "welcome"]
    assert welcome[welcome["order_id"].isna()].groupby("customer_id").size().max() == 1


def test_defect_windows_lie_in_the_period(any_store: Store) -> None:
    truth = any_store["truth/variants"]
    defective = truth[truth["defective"]]
    assert (defective["defect_start"] >= START).all()
    assert (defective["defect_start"] < END).all()
    ends = defective["defect_end"].dropna()
    assert (ends <= END).all()


# ---- Behaviour shares and signatures (US-103) --------------------------------------------------

SHARES = {"normal": 0.80, "size": 0.12, "wardrober": 0.03, "shared_benign": 0.035}


def test_behaviour_shares(main_store: Store) -> None:
    labels = main_store["truth/labels"]
    shares = labels["behaviour"].value_counts(normalize=True)
    for behaviour, share in SHARES.items():
        assert shares[behaviour] == pytest.approx(share, abs=0.004), behaviour
    assert shares["promo_ring"] == pytest.approx(0.015, abs=0.003)
    rings = labels.dropna(subset=["ring_id"]).groupby("ring_id").size()
    assert len(rings) == round(40 * main_store.scale)
    assert rings.between(5, 9).all()
    families = labels.dropna(subset=["family_id"]).groupby("family_id").size()
    assert families.between(2, 4).all()
    assert (labels["scenario"] == "main").all()
    truth = main_store["truth/variants"]
    assert truth["defective"].mean() == pytest.approx(0.02, abs=0.004)


def test_ring_signatures(main_store: Store) -> None:
    labels = main_store["truth/labels"]
    lines = main_store.lines()
    created = main_store["store/customers"].set_index("customer_id")["event_at"]
    coupons = main_store["store/coupons"]
    for ring, members in labels.dropna(subset=["ring_id"]).groupby("ring_id")["customer_id"]:
        accounts = list(members)
        spread = created[accounts].max() - created[accounts].min()
        assert spread <= pd.Timedelta(days=14), ring
        assert (created[accounts] >= START).all(), ring
        welcome = coupons[(coupons["kind"] == "welcome") & coupons["customer_id"].isin(accounts)]
        assert welcome[welcome["order_id"].notna()]["customer_id"].nunique() == len(accounts)
        assert _rate(lines[lines["ring_id"] == ring]) >= 0.8, ring
        ids = main_store.identifiers_of(accounts)
        strong = ids[ids["kind"].isin(["card", "email", "device"])]
        sharers = strong.groupby("value_hash")["customer_id"].nunique()
        linked = strong[strong["value_hash"].isin(sharers[sharers >= 2].index)]
        assert set(linked["customer_id"]) == set(accounts), ring


def test_wardrober_signature(calibrated: Store) -> None:
    lines = calibrated.lines()
    normal = lines[lines["behaviour"] == "normal"]
    wardrobe = lines[lines["behaviour"] == "wardrober"]
    assert wardrobe["price"].mean() > 1.5 * normal["price"].mean()
    assert _rate(wardrobe) > 2 * _rate(normal)
    returns = calibrated["store/returns"].set_index("line_id")
    returned = wardrobe[wardrobe["returned"]]
    days = returned["line_id"].map(returns["days_since_delivery"])
    assert days.between(3, 7).mean() >= 0.9
    assert (returned["reason"] == "changed_mind").mean() >= 0.6


def _orders_with_weekdays(store: Store) -> pd.DataFrame:
    orders = store["store/orders"][["order_id", "customer_id", "event_at"]]
    deliveries = store["store/deliveries"][["order_id", "event_at"]].rename(
        columns={"event_at": "delivered_at"}
    )
    out = orders.merge(deliveries, on="order_id", how="left")
    out = out.merge(store["truth/labels"][["customer_id", "behaviour"]], on="customer_id")
    out["weekday"] = out["event_at"].dt.dayofweek
    out["lag"] = (out["delivered_at"].dt.normalize() - out["event_at"].dt.normalize()) // DAY
    return out


def test_g2_wardrober_timing_is_a_tendency_not_a_rule(calibrated: Store) -> None:
    """Wardrobers lean to Tuesday-Thursday orders (in hand for the weekend), relative to normal
    customers, but neither weekday nor delivery lag gives the label away."""
    orders = _orders_with_weekdays(calibrated)
    wardrobe = orders[orders["behaviour"] == "wardrober"]
    normal = orders[orders["behaviour"] == "normal"]
    mid_week = [1, 2, 3]
    share_w = wardrobe["weekday"].isin(mid_week).mean()
    assert share_w > normal["weekday"].isin(mid_week).mean() + 0.15
    assert share_w < 0.9
    assert wardrobe["weekday"].nunique() == 7  # every day of the week occurs
    # Delivery lag: the shop's distribution, the same for everyone.
    lags_w = wardrobe["lag"].dropna().value_counts(normalize=True)
    lags_n = normal["lag"].dropna().value_counts(normalize=True)
    assert lags_w.get(2, 0.0) < 0.6
    assert abs(lags_w.get(2, 0.0) - lags_n.get(2, 0.0)) < 0.1


def label_free_wardrober_rule(store: Store) -> pd.Series:
    """The review's label-free rule: at least two orders, all placed on Tuesday or Wednesday and
    all delivered two days later. Returns the customers it flags."""
    orders = _orders_with_weekdays(store).dropna(subset=["lag"])
    per_customer = orders.groupby("customer_id").agg(
        n=("order_id", "size"),
        tue_wed=("weekday", lambda w: w.isin([1, 2]).all()),
        two_days=("lag", lambda lag: (lag == 2).all()),
    )
    flagged = per_customer[(per_customer["n"] >= 2) & per_customer["tue_wed"]]
    return flagged[flagged["two_days"]].index.to_series()


def test_g2_label_free_rule_precision_is_low(calibrated: Store) -> None:
    """Before the fix this rule found wardrobers with precision 0.98 (review G2); the base rate
    of wardrobers is 3%. The value at scale 1.0 is in docs/synthetic-store.md."""
    flagged = label_free_wardrober_rule(calibrated)
    labels = calibrated["truth/labels"].set_index("customer_id")["behaviour"]
    precision = (labels[flagged] == "wardrober").mean() if len(flagged) else 0.0
    assert precision < 0.5, (precision, len(flagged))


def test_size_signature(main_store: Store) -> None:
    lines = main_store.lines()
    size_lines = lines[lines["behaviour"] == "size"]
    for customer, mine in size_lines.groupby("customer_id"):
        pairs = mine.groupby(["order_id", "variant_id"]).filter(
            lambda g: len(g) == 2 and g["size"].nunique() == 2
        )
        assert len(pairs) >= 2, customer
        delivered = mine[mine["delivered_at"].notna()]
        kept = delivered.loc[~delivered["returned"], "price"].sum()
        assert kept >= 0.5 * delivered["price"].sum(), customer
    returned = size_lines[size_lines["returned"]]
    normal = lines[(lines["behaviour"] == "normal") & lines["returned"]]
    assert (returned["reason"] == "size").mean() > (normal["reason"] == "size").mean() + 0.2


def test_shared_benign_signature(main_store: Store) -> None:
    labels = main_store["truth/labels"]
    lines = main_store.lines()
    families = labels.dropna(subset=["family_id"]).groupby("family_id")["customer_id"]
    card_sharing = 0
    for family, members in families:
        accounts = list(members)
        ids = main_store.identifiers_of(accounts)
        for kind in ("address", "device"):
            sharers = ids[ids["kind"] == kind].groupby("value_hash")["customer_id"].nunique()
            assert sharers.max() == len(accounts), (family, kind)
        cards = ids[ids["kind"] == "card"].groupby("value_hash")["customer_id"].nunique()
        card_sharing += int((cards >= 2).any())
    assert card_sharing >= 2  # the Rossi-like hard situation exists beyond the golden family
    benign = lines[lines["behaviour"] == "shared_benign"]
    normal = lines[lines["behaviour"] == "normal"]
    assert abs(_rate(benign) - _rate(normal)) < 0.08


def weekly_window_sales(store: Store) -> pd.DataFrame:
    """Sales (order lines placed) of each defective variant in each full Monday-to-Sunday week of
    its defect window: what the spec 8.9 detector sees (minimum 20 sales a week)."""
    truth = store["truth/variants"]
    lines = store["store/order_lines"]
    rows = []
    for row in truth[truth["defective"]].itertuples():
        end = row.defect_end if pd.notna(row.defect_end) else END
        mine = lines[lines["variant_id"] == row.variant_id]
        week = row.defect_start
        while week + 7 * DAY <= end:
            sold = mine["event_at"].between(week, week + 7 * DAY, inclusive="left").sum()
            rows.append({"variant_id": row.variant_id, "week": week, "sales": int(sold)})
            week += 7 * DAY
    return pd.DataFrame(rows, columns=["variant_id", "week", "sales"])


def test_g6_defective_variants_reach_the_detector_minimum(calibrated: Store) -> None:
    """Spec 8.9 assesses a variant only with at least 20 sales in the week: almost every defective
    variant must reach it in its window, or the detector cannot be evaluated (US-208). The lift
    covers the first DEFECT_BOOST_WEEKS weeks; the jacket's type may be too small at these
    scales, so it is checked at scale 1.0 (docs/synthetic-store.md)."""
    weekly = weekly_window_sales(calibrated)
    defective = calibrated["truth/variants"]["defective"].sum()
    assert weekly["variant_id"].nunique() == defective  # every window holds a full week
    generic = weekly[weekly["variant_id"] != golden.DEFECTIVE_JACKET]
    best = generic.groupby("variant_id")["sales"].max()
    assert (best >= 20).mean() >= 0.9, best.sort_values().head()
    first_weeks = generic.groupby("variant_id").head(DEFECT_BOOST_WEEKS)
    assert (first_weeks["sales"] >= 20).mean() >= 0.75
    # Without the lift (review G6) only about 3 of 100 variants reached 20 sales in a week.
    later = generic.groupby("variant_id").nth(slice(DEFECT_BOOST_WEEKS, None))
    assert (later["sales"] >= 20).mean() < (first_weeks["sales"] >= 20).mean()


# ---- P1: a sales surge is not a defect label ---------------------------------------------------

# The review's label-free rule, which reads no return and no label: a variant whose best week
# sells at least SURGE_RULE_MIN_SALES and at least SURGE_RULE_RATIO times its median week.
SURGE_RULE_MIN_SALES = 15
SURGE_RULE_RATIO = 4


@pytest.fixture(params=["main_store", "real_store", "ood_store", "real_ood_store"])
def lifted(request: pytest.FixtureRequest) -> Store:
    """Stores whose types can lift a best seller to the spec 8.9 minimum: the calibrated ones
    (scale 0.1 and 0.25) and the two OOD stores at scale 0.1."""
    store: Store = request.getfixturevalue(request.param)
    return store


def weekly_variant_sales(store: Store) -> tuple[pd.DataFrame, pd.Series]:
    """Sales of every variant in every Monday-to-Sunday week of the period, from the week it is
    listed (NaN before), as the detector of spec 8.9 counts them; and whether it is defective.
    Only store/order_lines and store/variants are read, plus the truth for the label."""
    lines = store["store/order_lines"]
    variants = store["store/variants"].set_index("variant_id")

    def monday(t: pd.Series) -> pd.Series:
        day = t.dt.floor("D")
        return day - pd.to_timedelta(day.dt.weekday, unit="D")

    weeks = pd.date_range(monday(pd.Series([START]))[0], END - DAY, freq="7D")
    counts = lines.assign(week=monday(lines["event_at"])).groupby(["variant_id", "week"]).size()
    table = counts.unstack(fill_value=0).reindex(index=variants.index, columns=weeks, fill_value=0)
    listed = monday(variants["event_at"].clip(lower=START))
    before = table.columns.values[None, :] < listed.values[:, None]
    weekly = table.astype(float).mask(before)
    defective = store["truth/variants"].set_index("variant_id")["defective"].reindex(weekly.index)
    return weekly, defective.astype(bool)


def test_p1_reaching_the_sales_gate_does_not_identify_a_defect(lifted: Store) -> None:
    """Review P1: at 472d798 the lift went to defective variants only, so 100 of the 106
    variants that ever sold 20 in a week were defective (real calibration, scale 1.0, synthetic):
    the spec 8.9 gate alone was a defect flag. Matched non-defective best sellers now get the
    same surge, so most variants the detector can assess are not defective."""
    weekly, defective = weekly_variant_sales(lifted)
    reach = weekly.max(axis=1) >= 20
    share = defective[reach].mean()
    assert share <= 0.3, (share, int(reach.sum()), int(defective[reach].sum()))
    assert (reach & ~defective).sum() >= 3 * (reach & defective).sum()
    assessable = weekly >= 20  # variant-weeks the detector can assess
    weeks_defective = assessable[defective].to_numpy().sum() / assessable.to_numpy().sum()
    assert weeks_defective <= 0.4, weeks_defective


def test_p1_the_label_free_surge_rule_has_low_precision(lifted: Store) -> None:
    """Review P1: "best week >= 15 sales and >= 4 times the median week" flagged 92 variants,
    91 of them defective, at 472d798 (scale 1.0). A surge is now no evidence of a defect: the
    rule's precision is about the share of defects among the surging best sellers."""
    weekly, defective = weekly_variant_sales(lifted)
    best, median = weekly.max(axis=1), weekly.median(axis=1)
    flagged = (best >= SURGE_RULE_MIN_SALES) & (best >= SURGE_RULE_RATIO * median)
    assert flagged.sum() >= 3 * defective.sum()  # the surges are there, not hidden
    precision = defective[flagged].mean()
    assert precision <= 0.3, (precision, int(flagged.sum()))


def test_p1_defects_and_matched_controls_surge_alike() -> None:
    """The controls are drawn with the defects from one pool of best sellers, so they have the
    same types and popularity; both get the same lift, and the lift processes them in random
    order, so they reach the spec 8.9 minimum alike (real calibration, scale 0.25)."""
    store = build_store(REAL_CAL, 42, 0.25, SCENARIOS["main"])
    cat, plan = store.cat, store.plan
    jacket = cat.index_of(golden.DEFECTIVE_JACKET)
    surging = cat.surge_start >= 0
    surging[jacket] = False  # the golden jacket's dates are scripted
    generic = np.flatnonzero(surging & cat.defective)
    controls = np.flatnonzero(surging & ~cat.defective)
    assert len(generic) == cat.defective.sum() - 1
    assert len(controls) == SURGE_CONTROLS_PER_DEFECT * len(generic)
    assert set(cat.type_index[generic]) <= set(cat.type_index[controls])
    ratio = np.median(cat.popularity[generic]) / np.median(cat.popularity[controls])
    assert 0.5 < ratio < 2.0
    assert (cat.defect_start[generic] == cat.surge_start[generic]).all()
    assert (weekday(cat.surge_start[cat.surge_start >= 0]) == 0).all()  # Mondays
    week = 7 * 24 * 3600 * 1_000_000
    placed = plan.placed_at[plan.line_order]

    def reach(variants: np.ndarray) -> float:
        hits = []
        for v in variants:
            mine = placed[plan.line_variant == v]
            for w in range(DEFECT_BOOST_WEEKS):
                lo = cat.surge_start[v] + w * week
                hits.append(np.count_nonzero((mine >= lo) & (mine < lo + week)) >= 20)
        return float(np.mean(hits))

    assert reach(generic) >= 0.75 and reach(controls) >= 0.75
    assert abs(reach(generic) - reach(controls)) <= 0.1


def test_g4_size_range_matches_the_category(any_main: Store) -> None:
    """Every variant's sizes come from its category; the golden jacket is apparel (XS-XL)."""
    variants = any_main["store/variants"].set_index("variant_id")
    expected = variants["category"].map(lambda c: SIZE_RANGE[size_kind_for(c)])
    assert (variants["size_range"].fillna("-") == expected.fillna("-")).all()
    jacket = variants.loc[golden.DEFECTIVE_JACKET]
    assert size_kind_for(jacket["category"]) is SizeKind.APPAREL
    assert jacket["size_range"] == "XS-XL"
    lines = any_main["store/order_lines"]
    sold = lines[lines["variant_id"] == golden.DEFECTIVE_JACKET]
    assert set(sold["size"]) <= {"XS", "S", "M", "L", "XL"}


def test_defective_variants_concentrate_returns(main_store: Store) -> None:
    """One variant, one reason, one period (spec section 7)."""
    truth = main_store["truth/variants"]
    lines = main_store.lines()
    pooled_in: list[pd.Series] = []
    pooled_out: list[pd.Series] = []
    for row in truth[truth["defective"]].itertuples():
        end = row.defect_end if pd.notna(row.defect_end) else END
        mine = lines[(lines["variant_id"] == row.variant_id) & lines["delivered_at"].notna()]
        mine = mine[mine["ordered_at"] < END - pd.Timedelta(days=MATURITY_DAYS)]
        window = (mine["ordered_at"] >= row.defect_start) & (mine["ordered_at"] < end)
        hit = mine["reason"] == row.defect_reason
        pooled_in.append(hit[window])
        pooled_out.append(hit[~window])
        if window.sum() >= 40 and (~window).sum() >= 40:
            assert hit[window].mean() > hit[~window].mean() + 0.10, row.variant_id
    inside, outside = pd.concat(pooled_in), pd.concat(pooled_out)
    assert len(inside) >= 100
    assert inside.mean() > outside.mean() + 0.2
    defect_returns = lines[
        lines["variant_id"].isin(truth.loc[truth["defective"], "variant_id"])
        & lines["reason"].isin(NON_CONFORMITY)
    ]
    assert defect_returns["customer_id"].nunique() >= 20  # independent customers


def test_hard_situations_pickup_points_and_collective_addresses(main_store: Store) -> None:
    ids = main_store["store/identifiers"]
    labels = main_store["truth/labels"].set_index("customer_id")
    addresses = ids[ids["kind"] == "address"]
    sharers = addresses.groupby("value_hash")["customer_id"].agg(list)
    crowded = [c for c in sharers if len(c) >= 15]
    assert len(crowded) >= round(10 * main_store.scale)  # pickup points
    for customers in crowded:
        for group in ("ring_id", "family_id"):
            counts = labels.loc[customers, group].dropna().value_counts()
            assert counts.empty or counts.max() < len(customers) / 2
    medium = [c for c in sharers if 4 <= len(c) < 15 and labels.loc[c, "family_id"].isna().all()]
    assert len(medium) >= round(30 * main_store.scale) // 2  # collective addresses


def test_identifiers_are_salted_hashes(main_store: Store) -> None:
    ids = main_store["store/identifiers"]
    assert ids["value_hash"].str.fullmatch(r"[0-9a-f]{32}").all()
    assert set(ids["kind"]) == {"card", "email", "device", "address", "phone", "ip_prefix"}
    # The normalised e-mail is unique per account unless accounts are linked.
    emails = ids[ids["kind"] == "email"]
    shared = emails.groupby("value_hash")["customer_id"].nunique()
    labels = main_store["truth/labels"].set_index("customer_id")
    for value in shared[shared >= 2].index:
        owners = emails.loc[emails["value_hash"] == value, "customer_id"]
        assert labels.loc[owners, "ring_id"].nunique() == 1


# ---- US-105: out-of-distribution scenario ------------------------------------------------------


def test_ood_is_a_separate_scenario(ood_store: Store) -> None:
    labels = ood_store["truth/labels"]
    assert (labels["scenario"] == "ood").all()
    assert labels["customer_id"].str.startswith("C-OOD-").all()
    assert ood_store.root.name == "ood"
    for customer in (golden.GIULIA, golden.ELENA, *golden.MARCO_ACCOUNTS):
        assert customer not in set(labels["customer_id"])
    variants = set(ood_store["store/variants"]["variant_id"])
    assert golden.DEFECTIVE_JACKET not in variants


def test_ood_rings_are_smaller_and_slower(ood_store: Store, main_store: Store) -> None:
    labels = ood_store["truth/labels"]
    rings = labels.dropna(subset=["ring_id"]).groupby("ring_id")["customer_id"].agg(list)
    assert rings.map(len).between(3, 5).all()
    created = ood_store["store/customers"].set_index("customer_id")["event_at"]
    spreads = rings.map(lambda c: created[c].max() - created[c].min())
    assert spreads.median() > pd.Timedelta(days=21)
    ood_rate = _rate(ood_store.lines().query("behaviour == 'promo_ring'"))
    main_rate = _rate(main_store.lines().query("behaviour == 'promo_ring'"))
    assert ood_rate < main_rate - 0.1


def test_ood_identifiers_are_missing_more_often(ood_store: Store, main_store: Store) -> None:
    def coverage(store: Store, kind: str) -> float:
        ids = store["store/identifiers"]
        have = ids.loc[ids["kind"] == kind, "customer_id"].nunique()
        return have / len(store["store/customers"])

    for kind in ("device", "card", "phone", "ip_prefix"):
        assert coverage(ood_store, kind) < coverage(main_store, kind) - 0.1, kind
    assert coverage(ood_store, "email") == 1.0


def test_ood_hard_family_looks_like_a_ring_but_returns_normally(ood_store: Store) -> None:
    labels = ood_store["truth/labels"]
    members = list(labels.loc[labels["family_id"] == golden.OOD_HARD_FAMILY, "customer_id"])
    assert len(members) == 5
    assert (labels.set_index("customer_id").loc[members, "behaviour"] == "shared_benign").all()
    created = ood_store["store/customers"].set_index("customer_id")["event_at"][members]
    assert created.min() >= START
    assert created.max() - created.min() <= pd.Timedelta(days=10)
    coupons = ood_store["store/coupons"]
    welcome = coupons[(coupons["kind"] == "welcome") & coupons["customer_id"].isin(members)]
    assert welcome[welcome["order_id"].notna()]["customer_id"].nunique() == 5
    ids = ood_store.identifiers_of(members)
    for kind in ("address", "device", "card"):
        sharers = ids[ids["kind"] == kind].groupby("value_hash")["customer_id"].nunique()
        assert sharers.max() == 5, kind
    lines = ood_store.lines()
    assert _rate(lines[lines["customer_id"].isin(members)]) <= 0.5


# ---- CLI ---------------------------------------------------------------------------------------


def test_cli_rejects_bad_arguments(tmp_path: Path) -> None:
    from pipeline.generator.__main__ import main

    with pytest.raises(SystemExit) as exit_info:
        main(["--calibration", str(EXAMPLE), "--out", str(tmp_path), "--scale", "0"])
    assert exit_info.value.code == 2
    with pytest.raises(SystemExit) as exit_info:
        main(["--calibration", str(EXAMPLE), "--out", str(tmp_path), "--scenario", "test"])
    assert exit_info.value.code == 2
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"contract_version": "2.0.0"}))
    assert main(["--calibration", str(bad), "--out", str(tmp_path)]) == 1
    assert not (tmp_path / "store").exists()


def test_generate_refuses_unknown_scenario(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        generate(CAL, tmp_path, seed=1, scale=0.01, scenario="test")
    with pytest.raises(ValueError):
        generate(CAL, tmp_path, seed=1, scale=-1.0, scenario="main")


def _iter_strings(table: pa.Table) -> Iterator[str]:
    for column in table.columns:
        if pa.types.is_string(column.type):
            yield from (v for v in column.to_pylist() if v is not None)


def test_store_never_holds_raw_identifiers(main_store: Store) -> None:
    """Only salted hashes: no e-mail address, phone number or IP address in any store value."""
    for name in STORE_TABLES:
        table, _ = read_table(name, main_store.root)
        for value in _iter_strings(table):
            assert "@" not in value, name
            assert not value.startswith("+39"), name
            assert value.count(".") < 3, name
    names = main_store["store/customers"]["display_name"]
    assert names.notna().all() and (names.str.len() > 0).all()


def test_period_constants() -> None:
    assert datetime(2025, 10, 1, tzinfo=UTC) == PERIOD_START
    assert datetime(2026, 10, 1, tzinfo=UTC) == PERIOD_END
