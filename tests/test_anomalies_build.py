"""US-208: features/product_anomalies at each decision time, written through the contract
(ADR-13), from the store read at T (rule 7, MOD-08), with no customer and no ground truth (rule
12). Values are made up (nature: synthetic)."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from contracts.io import Provenance, read_table
from contracts.parquet import SCHEMAS
from pipeline.anomalies import __main__ as cli
from pipeline.anomalies.build import (
    TABLE,
    anomalies_at,
    build,
    load_sales,
    weekly_decision_times,
)
from tests.anomaly_store import Sale, at, batch, write_store

PACKAGE = Path(__file__).resolve().parents[1] / "pipeline" / "anomalies"


def store_rows() -> list[Sale]:
    """Three weeks of normal sales for B, C and D (coats), then a defect on A in week 3: 25 lines,
    12 returned as not as described within ten days."""
    rows: list[Sale] = []
    for week in range(3):
        for v in ("B", "C", "D"):
            returns = [("size", 4), ("changed_mind", 6), ("not_as_expected", 8)]
            rows += batch(f"{v}{week}", v, "coats", at(7 * week + 1), 30, returns)
    rows += batch(
        "A3", "A", "coats", at(22), 25, [("not_as_described", 5 + i % 5) for i in range(12)]
    )
    return rows


T = datetime(2026, 2, 9, tzinfo=UTC)  # Monday, two weeks after A's week


def test_the_flagged_cell_is_written_through_the_contract(tmp_path: Path) -> None:
    store = write_store(tmp_path / "in", store_rows())
    summary = build(store, tmp_path / "out", [T])
    table, prov = read_table(TABLE, tmp_path / "out", as_of=T)
    rows = table.to_pandas()
    assert rows[["variant_id", "reason"]].values.tolist() == [["A", "not_as_described"]]
    row = rows.iloc[0]
    assert (row["n"], row["observed"], row["status"]) == (25, 12, "open")
    assert row["week"].isoformat() == "2026-01-26"
    assert row["z"] > 3 and row["expected"] > 0
    assert pd.Timestamp(row["as_of"]) == pd.Timestamp(T)
    assert prov.nature == "synthetic"
    assert "anomalies-v1-2026-10-13" in prov.origin and "test fixture" in prov.origin
    assert summary["rows"] == 1 and summary["decision_times"] == 1


def test_one_file_holds_every_decision_time(tmp_path: Path) -> None:
    store = write_store(tmp_path / "in", store_rows())
    times = [at(21), at(28), T]  # before A's week, a week later, two weeks later
    build(store, tmp_path / "out", times)
    rows = read_table(TABLE, tmp_path / "out")[0].to_pandas()
    per_time = rows.groupby("as_of").size()
    assert pd.Timestamp(at(21)) not in per_time.index  # A had not sold yet
    assert per_time[pd.Timestamp(T)] == 1
    assert (pd.to_datetime(rows["week"]).dt.tz_localize(UTC) <= rows["as_of"]).all()


def test_future_rows_of_the_store_change_nothing_at_t(tmp_path: Path) -> None:
    """MOD-08 on files: the rows at T are the same with or without the facts known after T."""
    rows = store_rows()
    later = [
        *batch("A9", "A", "coats", T + timedelta(days=2), 40, [("not_as_described", 1)] * 40),
        *batch("B9", "B", "coats", at(23), 10, [("size", 30)] * 10),  # returns known after T
    ]
    known_then = [*rows, *batch("B9", "B", "coats", at(23), 10)]
    full = write_store(tmp_path / "full", [*rows, *later])
    cut = write_store(tmp_path / "cut", known_then)
    a = anomalies_at(load_sales(full, T), T)
    b = anomalies_at(load_sales(cut, T), T)
    pd.testing.assert_frame_equal(a, b)
    narrowed = anomalies_at(load_sales(full, T + timedelta(days=30)), T)
    pd.testing.assert_frame_equal(a, narrowed)


def test_weekly_decision_times() -> None:
    first = datetime(2025, 10, 1, tzinfo=UTC)  # a Wednesday
    end = datetime(2026, 10, 1, tzinfo=UTC)  # a Thursday
    times = weekly_decision_times(first, end)
    assert times[0] == datetime(2025, 10, 6, tzinfo=UTC)
    assert times[-2] == datetime(2026, 9, 28, tzinfo=UTC)
    assert times[-1] == end
    assert all(t.weekday() == 0 for t in times[:-1])
    monday = datetime(2026, 1, 5, tzinfo=UTC)
    assert weekly_decision_times(monday, monday + timedelta(days=14)) == [
        monday + timedelta(days=7),
        monday + timedelta(days=14),
    ]


def test_no_customer_column_and_no_ground_truth() -> None:
    """The anomaly is a catalogue matter (spec 8.9, E2E-04): its table names no customer, and
    only the offline evaluation reads data/truth (rule 12)."""
    assert not [c for c in SCHEMAS[TABLE].names if "customer" in c]
    reads_truth = re.compile(r"""read_table\(\s*["']truth/|["']truth["']|load_truth""")
    readers = {p.name for p in PACKAGE.glob("*.py") if reads_truth.search(p.read_text("utf-8"))}
    assert readers == {"evaluate.py"}


def test_customer_scoring_never_reads_the_anomalies() -> None:
    """Spec 8.9: the anomaly opens a catalogue task and is never a customer feature, so the
    customer features and the models (rule 1: scores come from ml/) never read it."""
    root = PACKAGE.parents[1]
    uses = re.compile(r"product_anomalies|pipeline\.anomalies|from pipeline import anomalies")
    readers = [
        p.relative_to(root).as_posix()
        for folder in ("ml", "pipeline/features", "pipeline/graph")
        for p in (root / folder).rglob("*.py")
        if uses.search(p.read_text(encoding="utf-8"))
    ]
    assert readers == []


def test_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = write_store(tmp_path / "in", store_rows())
    code = cli.main(
        ["--store", str(store), "--out", str(tmp_path / "out"), "--as-of", T.isoformat()]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["rows"] == 1 and summary["nature"] == "synthetic"
    assert (tmp_path / "out" / "features" / "product_anomalies.parquet").exists()
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--store", str(store), "--out", str(tmp_path), "--as-of", "2026-02-09"])
    assert exit_info.value.code == 2  # a naive instant is refused


def test_default_decision_times_follow_the_store_period(tmp_path: Path) -> None:
    period = Provenance("anomaly fixture", date(2026, 1, 1), date(2026, 2, 26), "synthetic")
    store = write_store(tmp_path / "in", store_rows(), period)
    code = cli.main(["--store", str(store), "--out", str(tmp_path / "out")])
    assert code == 0
    rows = read_table(TABLE, tmp_path / "out")[0].to_pandas()
    times = sorted(rows["as_of"].unique())
    assert times[-1] == pd.Timestamp("2026-02-27T00:00:00Z")  # the instant after the last day
    assert all(pd.Timestamp(t).weekday() == 0 for t in times[:-1])  # Mondays
    assert pd.Timestamp("2026-02-02T00:00:00Z") in times  # the first run after A's week
