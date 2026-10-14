"""The offline evaluation report in the serving snapshot and GET /api/fairness (US-605, NFR-04,
FAIR-02; rule 12).

False positives need labels, so they come from the offline evaluation (ml.risk, FAIR-02 on the
held-out test customers), never from data/truth/ at runtime. The serving snapshot carries the
report as reports/evaluation.json, covered by the manifest: pipeline.serving writes the FAIR-02
aggregates of artifacts/eval/risk/metrics.json, nothing else (pipeline.serving.evaluation), so the
harness snapshot holds it. The fixtures below give a copy of the snapshot with a chosen report
(evaluated_*) and one without any (unevaluated_*), where both routes answer 404.

The fixtures of this module (metrics, evaluated_*, unevaluated_*) serve the KPI tests too: import
their *_fixture functions.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, get_args

import pytest

from apps.api import schemas as s
from apps.api.roles import ACTOR_HEADER
from apps.api.services.manager.evaluation import (
    GAP_THRESHOLD_PP,
    REPORT_FILE,
    REPORT_FORMAT,
    ReportUnavailable,
    contract_dimension,
    fairness,
    load_report,
    parse_report,
)
from apps.api.serving import Serving
from ml.risk.fairness import GAP_LIMIT_PP
from ml.risk.run import metrics_path
from pipeline.serving.evaluation import evaluation_document
from pipeline.serving.manifest import Manifest, write_manifest
from tests.api_harness import Api, open_api, settings_for, validate_body


def with_report(serving_dir: Path, document: Mapping[str, Any] | None, out: Path) -> Path:
    """A copy of a serving snapshot with reports/evaluation.json (or none), manifest rewritten."""
    shutil.copytree(serving_dir, out)
    target = out / REPORT_FILE
    target.unlink(missing_ok=True)
    if document is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", "utf-8")
    raw = Manifest.read(out).raw
    write_manifest(out, {k: v for k, v in raw.items() if k not in ("format", "files")})
    return out


@pytest.fixture(scope="session", name="metrics")
def metrics_fixture(serving_data: Path) -> dict[str, Any]:
    found: dict[str, Any] = json.loads(metrics_path(serving_data / "artifacts").read_text("utf-8"))
    return found


@pytest.fixture(scope="session", name="evaluated_dir")
def evaluated_dir_fixture(
    serving_dir: Path, serving: Serving, metrics: dict[str, Any], tmp_path_factory: Any
) -> Path:
    document = evaluation_document(metrics, serving.models.customer.version)
    return with_report(serving_dir, document, tmp_path_factory.mktemp("evaluated") / "serving")


@pytest.fixture(scope="session", name="evaluated_serving")
def evaluated_serving_fixture(evaluated_dir: Path) -> Serving:
    return Serving.load(evaluated_dir)


@pytest.fixture(name="evaluated_api")
def evaluated_api_fixture(
    evaluated_serving: Serving,
    evaluated_dir: Path,
    seeded_db: tuple[Path, dict[str, Any]],
    tmp_path: Path,
) -> Iterator[Api]:
    database = tmp_path / "evaluated.sqlite"
    shutil.copyfile(seeded_db[0], database)
    yield from open_api(evaluated_serving, settings_for(evaluated_dir, database), database)


@pytest.fixture(scope="session", name="unevaluated_dir")
def unevaluated_dir_fixture(serving_dir: Path, tmp_path_factory: Any) -> Path:
    return with_report(serving_dir, None, tmp_path_factory.mktemp("unevaluated") / "serving")


@pytest.fixture(scope="session", name="unevaluated_serving")
def unevaluated_serving_fixture(unevaluated_dir: Path) -> Serving:
    return Serving.load(unevaluated_dir)


@pytest.fixture(name="unevaluated_api")
def unevaluated_api_fixture(
    unevaluated_serving: Serving,
    unevaluated_dir: Path,
    seeded_db: tuple[Path, dict[str, Any]],
    tmp_path: Path,
) -> Iterator[Api]:
    database = tmp_path / "unevaluated.sqlite"
    shutil.copyfile(seeded_db[0], database)
    yield from open_api(unevaluated_serving, settings_for(unevaluated_dir, database), database)


# ---- The report ----------------------------------------------------------------------------------


def test_the_gap_threshold_is_the_offline_one() -> None:
    assert GAP_THRESHOLD_PP == GAP_LIMIT_PP == 5.0  # NFR-04, FAIR-02


def test_the_report_is_read_from_the_snapshot(
    evaluated_serving: Serving, metrics: dict[str, Any]
) -> None:
    report = load_report(evaluated_serving)
    assert report.report_id == f"eval-{metrics['eval_end'][:10]}"
    assert report.scenario == "main"
    assert report.model_version == evaluated_serving.models.customer.version
    assert report.provenance.nature == "synthetic"
    assert report.non_abusers == metrics["fair02"]["non_abusers"]
    rows = metrics["fair02"]["rows"]
    assert [(x.dimension, x.segment) for x in report.segments] == [
        (r["dimension"], r["segment"]) for r in rows
    ]
    assert [x.rate for x in report.segments] == [r["fpr"] for r in rows]


def test_every_offline_dimension_has_its_contract_dimension(metrics: dict[str, Any]) -> None:
    """The six dimensions of FAIR-02 that the contract names (spec 12): the offline report's
    linked accounts have no contract dimension and are not served."""
    names = {r["dimension"] for r in metrics["fair02"]["rows"]}
    mapped = {name: contract_dimension(name) for name in names}
    assert mapped.pop("Linked accounts") is None
    assert set(mapped.values()) == set(get_args(s.FairnessDimension))


def test_the_harness_snapshot_holds_the_report_the_export_wrote(
    serving: Serving, metrics: dict[str, Any]
) -> None:
    report = load_report(serving)
    assert report.report_id == f"eval-{metrics['eval_end'][:10]}"
    assert report.model_version == serving.models.customer.version


def test_without_the_report_the_reader_says_so(unevaluated_serving: Serving) -> None:
    with pytest.raises(ReportUnavailable, match="not in the serving snapshot"):
        load_report(unevaluated_serving)


def test_a_report_the_manifest_does_not_cover_is_refused(
    evaluated_dir: Path, tmp_path: Path
) -> None:
    """Rule 12: only the report the export wrote (and hashed) is served."""
    copy = tmp_path / "serving"
    shutil.copytree(evaluated_dir, copy)
    serving = Serving.load(copy)
    path = copy / REPORT_FILE
    document = json.loads(path.read_text("utf-8"))
    document["fair02"]["fpr"] = 0.5
    path.write_text(json.dumps(document), "utf-8")
    with pytest.raises(ReportUnavailable, match="changed since the export"):
        load_report(serving)


def test_a_report_of_another_format_is_refused(metrics: dict[str, Any]) -> None:
    document = evaluation_document(metrics, "risk-customer")
    with pytest.raises(ReportUnavailable, match="format"):
        parse_report({**document, "format": "something.else"})
    without_rows = {**document, "fair02": {**document["fair02"], "rows": []}}
    with pytest.raises(ReportUnavailable, match="no FAIR-02 segment"):
        fairness(parse_report(without_rows))


# ---- GET /api/fairness ---------------------------------------------------------------------------


def _fairness(api: Api) -> dict[str, Any]:
    response = api.client.get("/api/fairness")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    validate_body("fairness", body)
    return body


def test_fairness_serves_the_offline_report(evaluated_api: Api, metrics: dict[str, Any]) -> None:
    """US-605: false positives by segment for each dimension of the contract, as measured by the
    offline FAIR-02 (risk >= the customer model's threshold, held-out test customers)."""
    body = _fairness(evaluated_api)
    assert body["report_id"] == f"eval-{metrics['eval_end'][:10]}"
    assert body["scenario"] == "main"
    assert body["model_version"] == evaluated_api.serving.models.customer.version
    assert body["max_gap_threshold"]["value"] == 5.0
    assert body["max_gap_threshold"]["unit"] == "percentage_points"
    rows = {
        (contract_dimension(r["dimension"]), r["segment"]): r for r in metrics["fair02"]["rows"]
    }
    order = list(get_args(s.FairnessDimension))
    dims = [d["dimension"] for d in body["dimensions"]]
    assert dims == sorted(dims, key=order.index)
    for dim in body["dimensions"]:
        assert len(dim["segments"]) >= 2
        for seg in dim["segments"]:
            row = rows[(dim["dimension"], seg["segment"])]
            assert seg["false_positive_rate"]["value"] == pytest.approx(row["fpr"], abs=1e-6)
            assert seg["customers"]["value"] == row["non_abusers"]
            assert seg["false_positive_rate"]["provenance"]["nature"] == "synthetic"
        rates = [x["false_positive_rate"]["value"] for x in dim["segments"]]
        gap = (max(rates) - min(rates)) * 100
        assert dim["max_gap"]["value"] == pytest.approx(gap, abs=0.01)
        assert dim["warning"] == (dim["max_gap"]["value"] > 5.0)
    text = json.dumps(body)
    assert "C-" not in text  # aggregates only: no customer id (rule 12)


def _report(gaps: Mapping[str, tuple[float, float]]) -> dict[str, Any]:
    rows = []
    for dimension, (low, high) in gaps.items():
        for segment, rate in (("a", low), ("b", high)):
            rows.append(
                {
                    "dimension": dimension,
                    "segment": segment,
                    "non_abusers": 100,
                    "flagged": int(rate * 100),
                    "fpr": rate,
                }  # fmt: skip
            )
    return {
        "format": REPORT_FORMAT,
        "report_id": "eval-2026-10-01",
        "scenario": "main",
        "as_of": "2026-10-01T00:00:00+00:00",
        "model_version": "risk-customer",
        "provenance": {
            "origin": "test report",
            "period_start": "2025-10-01",
            "period_end": "2026-09-30",
            "nature": "synthetic",
        },
        "fair02": {
            "population": "held-out test customers",
            "non_abusers": 200,
            "fpr": 0.02,
            "threshold": 0.5,
            "rows": rows,
        },  # fmt: skip
    }


def test_fair02_warns_exactly_above_five_points() -> None:
    """FAIR-02: a warning exactly when the largest gap between segments exceeds 5 points."""
    report = parse_report(
        _report({
            "New customers": (0.01, 0.06),  # 5.00 points: no warning
            "Tenure": (0.01, 0.0601),  # 5.01 points: warning
            "Main category": (0.0, 0.2),
            "Linked accounts": (0.0, 0.5),  # no dimension of the contract: not served
        })
    )  # fmt: skip
    body = fairness(report)
    by_dim = {d.dimension: d for d in body.dimensions}
    assert set(by_dim) == {"new_customers", "tenure", "category"}
    assert not by_dim["new_customers"].warning
    assert by_dim["tenure"].warning and by_dim["category"].warning
    assert by_dim["category"].max_gap.value == 20.0
    s.FairnessResponse.model_validate(body.model_dump(mode="json"))


def test_a_dimension_with_one_segment_is_left_out() -> None:
    document = _report({"New customers": (0.01, 0.02), "Tenure": (0.01, 0.03)})
    rows = document["fair02"]["rows"]
    document["fair02"]["rows"] = [
        r for r in rows if not (r["dimension"] == "Tenure" and r["segment"] == "b")
    ]
    body = fairness(parse_report(document))
    assert [d.dimension for d in body.dimensions] == ["new_customers"]


def test_fairness_without_the_report_is_not_found(unevaluated_api: Api) -> None:
    response = unevaluated_api.client.get("/api/fairness")
    assert response.status_code == 404
    validate_body("error", response.json())
    assert "evaluation report" in response.json()["message"]


def test_fairness_is_open_to_every_demo_user(evaluated_api: Api) -> None:
    for user in ("sara", "luca", "ahmed"):
        response = evaluated_api.client.get("/api/fairness", headers={ACTOR_HEADER: user})
        assert response.status_code == 200
    response = evaluated_api.client.get("/api/fairness", headers={ACTOR_HEADER: "mallory"})
    assert response.status_code == 403 and response.json()["code"] == "forbidden_role"
