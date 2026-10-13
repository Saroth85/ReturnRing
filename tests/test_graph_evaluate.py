"""US-203 evaluation (MOD-03, MOD-11, MOD-12): metric definitions by hand, and the CLI end to end on
tiny generated stores. Every number here is synthetic (hand-built or generated test fixtures).
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from pipeline.generator.calibration import load_calibration
from pipeline.generator.core import generate
from pipeline.graph.evaluate import (
    common_addresses,
    family_metrics,
    main,
    mixing,
    ring_metrics,
    ring_pair_weights,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "contracts" / "examples" / "calibration.json"


def assigned(rows: list[tuple[str, str | None, str | None, str | None]]) -> pl.DataFrame:
    """(customer, cluster, ring, family) -> the frame the metrics read."""
    return pl.DataFrame(
        rows,
        schema={
            "customer_id": pl.String,
            "cluster_id": pl.String,
            "ring_id": pl.String,
            "family_id": pl.String,
        },
        orient="row",
    ).with_columns(
        pl.when(pl.col("ring_id").is_not_null())
        .then(pl.lit("promo_ring"))
        .when(pl.col("family_id").is_not_null())
        .then(pl.lit("shared_benign"))
        .otherwise(pl.lit("normal"))
        .alias("behaviour")
    )


# Ring R1 (4 accounts) found as cluster X plus one outsider; ring R2 (3 accounts) split between
# cluster Y (2) and nothing (1); family F (2) is cluster Z; U is alone.
CASE = assigned(
    [
        ("a1", "X", "R1", None),
        ("a2", "X", "R1", None),
        ("a3", "X", "R1", None),
        ("a4", "X", "R1", None),
        ("o1", "X", None, None),
        ("b1", "Y", "R2", None),
        ("b2", "Y", "R2", None),
        ("b3", None, "R2", None),
        ("f1", "Z", None, "F"),
        ("f2", "Z", None, "F"),
        ("u1", None, None, None),
    ]
)


def test_ring_pairs_by_hand() -> None:
    m = ring_metrics(CASE)
    # Same-cluster pairs touching a ring member: X has C(5,2) = 10, Y has 1; Z has none.
    assert m["pairs_predicted"] == 11
    # Same ring and same cluster: C(4,2) = 6 in X, 1 in Y.
    assert m["pairs_true_positive"] == 7
    # Same ring: C(4,2) + C(3,2) = 9.
    assert m["pairs_actual"] == 9
    assert m["pairwise_precision"] == pytest.approx(7 / 11)
    assert m["pairwise_recall"] == pytest.approx(7 / 9)


def test_ring_clusters_by_hand() -> None:
    m = ring_metrics(CASE)
    # Clusters with a ring member: X (4 of R1 out of 5) and Y (2 of 2).
    assert m["member_precision"] == pytest.approx(6 / 7)
    # Best cluster per ring: R1 4 of 4, R2 2 of 3.
    assert m["member_recall"] == pytest.approx(6 / 7)
    assert m["rings"] == 2
    assert m["rings_exact"] == 0  # X holds an outsider; R2 is split
    assert m["rings_whole"] == 1  # all of R1 in one cluster
    assert m["ring_members_unclustered"] == 1
    assert m["rings_without_cluster"] == 0


def test_families_and_mixing_by_hand() -> None:
    f = family_metrics(CASE)
    assert (f["families"], f["families_exact"], f["families_whole"]) == (1, 1, 1)
    mx = mixing(CASE)
    # X joins 4 ring members with o1: 4 unrelated pairs. Nothing else is mixed.
    assert mx["unrelated_pairs"] == 4
    assert mx["mixed_clusters"] == 1


def test_no_ring_member_clustered_gives_undefined_precision() -> None:
    m = ring_metrics(assigned([("a", None, "R", None), ("b", None, "R", None)]))
    assert m["pairwise_precision"] is None
    assert m["pairwise_recall"] == 0.0


def test_common_addresses_by_hand() -> None:
    """A pickup point used by a ring pair, a family pair and two strangers; only the ring and the
    family are clusters, so no unrelated pair is joined."""
    who = assigned(
        [
            ("r1", "X", "R", None),
            ("r2", "X", "R", None),
            ("f1", "Z", None, "F"),
            ("f2", "Z", None, "F"),
            ("s1", None, None, None),
            ("s2", None, None, None),
        ]
    )
    ids = pl.DataFrame(
        {
            "customer_id": ["r1", "r2", "f1", "f2", "s1", "s2", "f1", "f2"],
            "kind": ["address"] * 6 + ["address"] * 2,
            "value_hash": ["pickup"] * 6 + ["home"] * 2,
        }
    )
    got = common_addresses(ids, who, hub_degree=3)
    assert got["common_addresses"] == 1  # "home" is used by one family only
    assert got["largest_sharers"] == 6
    assert got["above_hub_degree"] == 1
    assert got["unrelated_pairs_joined"] == 0
    assert got["top"][0]["groups"] == 4
    assert got["top"][0]["sharers_in_clusters"] == 4
    assert got["top"][0]["clusters"] == 2


def test_ring_pair_weights_by_hand() -> None:
    links = pl.DataFrame(
        {
            "customer_a": ["a1", "a1", "a2", "b1"],
            "customer_b": ["a2", "a3", "a3", "b2"],
            "weight": [1.0, 0.6, 0.2, 0.8000000000000002],
        }
    )
    got = ring_pair_weights(links, CASE)
    # R1: 6 pairs (3 linked: 1.0, 0.6, 0.2); R2: 3 pairs (1 linked at 0.8).
    assert got == {"no link": 3 + 2, "below 0.6": 1, "0.6 to 0.8": 1, "0.8 or more": 2}


@pytest.fixture(scope="module")
def tiny_data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("graph-data")
    cal = load_calibration(EXAMPLE)
    generate(cal, out, seed=11, scale=0.02, scenario="main")
    generate(cal, out, seed=11, scale=0.02, scenario="ood")
    return out


def test_cli_writes_the_report_and_the_json(tiny_data: Path, tmp_path: Path) -> None:
    report, out_json = tmp_path / "graph.md", tmp_path / "graph.json"
    assert main(["--data", str(tiny_data), "--report", str(report), "--json", str(out_json)]) == 0
    data = json.loads(out_json.read_text())
    assert [s["scenario"] for s in data["scenarios"]] == ["main", "ood"]
    main_rows = data["scenarios"][0]
    product = main_rows["variants"][0]
    assert product["variant"] == "spec 8.3 (product)"
    assert product["ring"]["pairwise_precision"] is not None
    golden = {g["group"]: g for g in main_rows["golden"]}
    for family in ("F-ROSSI", "F-ROSSI-CARD"):
        assert golden[family]["cluster_equals_group"], family
    assert golden["R-MARCO"]["cluster_equals_group"]
    assert main_rows["pruning"]["clusters_identical"]
    assert main_rows["pruning"]["kept_links_identical"]
    text = report.read_text()
    assert "synthetic" in text
    assert "MOD-03" in text and "MOD-11" in text and "MOD-12" in text
    assert "same person" not in text.replace("never the same person", "")
    # The OOD scenario has its own rows.
    assert "| ood |" in text
    # Rerunning gives the same file: the report holds no wall-clock value.
    again = tmp_path / "again.md"
    assert main(["--data", str(tiny_data), "--report", str(again), "--json", str(out_json)]) == 0
    assert again.read_text() == text


def test_cli_runs_without_an_ood_store(tmp_path: Path) -> None:
    out = tmp_path / "data"
    generate(load_calibration(EXAMPLE), out, seed=5, scale=0.01, scenario="main")
    report = tmp_path / "graph.md"
    assert (
        main(["--data", str(out), "--report", str(report), "--json", str(tmp_path / "g.json")]) == 0
    )
    assert "not generated" in report.read_text()
