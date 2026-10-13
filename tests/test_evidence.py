"""US-209 and US-307: evidence quality, separate from risk, with the list of missing evidence.

The thresholds of these tests are explicit test values, not the versioned project thresholds,
so the tests hold whatever D-15 decides. Every value is a made-up fixture (nature: synthetic).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from contracts.io import validate_table
from contracts.parquet import IDENTIFIER_KINDS
from pipeline.features.evidence import (
    EVIDENCE_THRESHOLDS,
    IDENTIFIER_WEIGHTS,
    EvidenceThresholds,
    evidence_quality,
    evidence_table,
)
from pipeline.features.lines import line_frame
from pipeline.features.snapshot import load_snapshot
from policy.types import EvidenceQuality
from tests.feature_store import Line, MiniStore, at

TEST = EvidenceThresholds(
    version="test",
    min_observations=3,
    min_identifier_completeness=0.6,
    min_reason_coherence=0.5,
    min_reasons=2,
)
ALL_KINDS = IDENTIFIER_KINDS


def customer(
    s: MiniStore,
    cid: str,
    n_lines: int,
    kinds: tuple[str, ...] = ALL_KINDS,
    reasons: tuple[str, ...] = (),
    size: str | None = "M",
) -> None:
    s.customer(cid, at(-300))
    for kind in kinds:
        s.identifier(cid, kind, at(-300))
    for i in range(n_lines):
        line = f"L-{cid}-{i}"
        s.order(f"O-{cid}-{i}", cid, at(i), [Line(line, "V-1", "30", size)], at(i + 1))
        if i < len(reasons):
            s.ret(f"R-{cid}-{i}", line, reasons[i], at(i + 3), 2)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    s = MiniStore().variant("V-1", "P-1").variant("V-2", "P-2", size_range=None)
    customer(s, "C-OK", 4, reasons=("size", "changed_mind"))
    customer(s, "C-FEW", 1)
    customer(s, "C-IDS", 4, kinds=("email", "address", "phone", "ip_prefix"))
    customer(s, "C-VAGUE", 4, reasons=("other", "other", "size"))
    customer(s, "C-ONE-VAGUE", 4, reasons=("other",))
    customer(s, "C-DEFECT", 4, reasons=("defective", "defective", "other"))
    customer(s, "C-NOSIZE", 4, reasons=("size", "size"), size=None)
    s.customer("C-NEW", at(150))
    return s.write(tmp_path / "store")


def evidence(root: Path, days: float = 200, **kwargs: object) -> pd.DataFrame:
    snap = load_snapshot(root, at(days))
    out = evidence_quality(snap, line_frame(snap), TEST, **kwargs)  # type: ignore[arg-type]
    return out.set_index("customer_id")


def test_sufficient_evidence_has_nothing_missing(root: Path) -> None:
    row = evidence(root).loc["C-OK"]
    assert row["evidence_quality"] == EvidenceQuality.SUFFICIENT
    assert row["missing_evidence"] == []
    assert row["n_observations"] == 4
    assert row["identifier_completeness"] == pytest.approx(1.0)
    assert row["missing_identifier_kinds"] == []
    assert row["n_reasons"] == 2 and row["reason_coherence"] == pytest.approx(1.0)


def test_few_observations_are_insufficient_with_the_reason(root: Path) -> None:
    out = evidence(root)
    for cid, n in (("C-FEW", 1), ("C-NEW", 0)):
        row = out.loc[cid]
        assert row["evidence_quality"] == EvidenceQuality.INSUFFICIENT
        assert row["n_observations"] == n
        assert len(row["missing_evidence"]) >= 1
        assert row["missing_evidence"][0].startswith("observations:")
        assert f"{n} order lines" in row["missing_evidence"][0]


def test_unsettled_lines_are_not_yet_observations(root: Path) -> None:
    """At day 30 no line of C-OK has settled (delivered day 1 + 31): insufficient evidence."""
    row = evidence(root, days=30).loc["C-OK"]
    assert row["n_observations"] == 0
    assert row["evidence_quality"] == EvidenceQuality.INSUFFICIENT


def test_identifier_completeness_is_weighted_and_lists_missing_kinds(root: Path) -> None:
    row = evidence(root).loc["C-IDS"]
    total = sum(IDENTIFIER_WEIGHTS.values())
    expected = (total - IDENTIFIER_WEIGHTS["card"] - IDENTIFIER_WEIGHTS["device"]) / total
    assert row["identifier_completeness"] == pytest.approx(expected)
    assert row["missing_identifier_kinds"] == ["card", "device"]
    assert row["evidence_quality"] == EvidenceQuality.INSUFFICIENT
    assert any(m.startswith("identifiers: no card, device") for m in row["missing_evidence"])


def test_vague_reasons_are_incoherent(root: Path) -> None:
    out = evidence(root)
    vague = out.loc["C-VAGUE"]
    assert vague["n_reasons"] == 3 and vague["reason_coherence"] == pytest.approx(1 / 3)
    assert vague["evidence_quality"] == EvidenceQuality.INSUFFICIENT
    assert any(m.startswith("return reasons: 2 of 3") for m in vague["missing_evidence"])
    one = out.loc["C-ONE-VAGUE"]  # a single return is too few to judge the reasons
    assert one["reason_coherence"] == pytest.approx(0.0)
    assert one["evidence_quality"] == EvidenceQuality.SUFFICIENT


def test_defect_returns_are_not_judged_as_reasons(root: Path) -> None:
    """Rule 4: non-conformity returns follow the guarantee path; only the 'other' is judged."""
    row = evidence(root).loc["C-DEFECT"]
    assert row["n_reasons"] == 1
    assert row["n_observations"] == 2  # two lines returned as defective are not observations


def test_a_size_reason_on_an_item_without_size_is_incoherent(root: Path) -> None:
    row = evidence(root).loc["C-NOSIZE"]
    assert row["reason_coherence"] == pytest.approx(0.0)
    assert row["evidence_quality"] == EvidenceQuality.INSUFFICIENT


def test_linked_accounts_add_observations_when_the_graph_is_known(root: Path) -> None:
    out = evidence(root)
    assert out["n_observations_linked"].isna().all()
    clusters = pd.DataFrame(
        {"customer_id": ["C-FEW", "C-OK", "C-NEW"], "cluster_id": ["K-1", "K-1", None]}
    )
    linked = evidence(root, clusters=clusters)
    assert linked.loc["C-FEW", "n_observations_linked"] == 1 + 4
    assert linked.loc["C-FEW", "evidence_quality"] == EvidenceQuality.SUFFICIENT
    assert linked.loc["C-NEW", "n_observations_linked"] == 0
    assert linked.loc["C-IDS", "n_observations_linked"] == 4  # no cluster row: own only


def test_insufficient_exactly_when_something_is_missing(root: Path) -> None:
    out = evidence(root)
    insufficient = out["evidence_quality"] == EvidenceQuality.INSUFFICIENT
    assert (insufficient == out["missing_evidence"].map(bool)).all()


def test_evidence_table_is_a_valid_contract_table(root: Path) -> None:
    snap = load_snapshot(root, at(200))
    frame = evidence_quality(snap, line_frame(snap), TEST)
    table = evidence_table(frame, snap.as_of)
    assert validate_table("features/evidence_quality", table) == []


def test_versioned_thresholds_are_valid() -> None:
    t = EVIDENCE_THRESHOLDS
    assert t.version and t.min_observations >= 1 and t.min_reasons >= 1
    assert 0 < t.min_identifier_completeness <= 1 and 0 < t.min_reason_coherence <= 1
    with pytest.raises(ValueError):
        replace(t, min_identifier_completeness=1.5)
    with pytest.raises(ValueError):
        replace(t, min_observations=0)
    assert set(IDENTIFIER_WEIGHTS) == set(IDENTIFIER_KINDS)
