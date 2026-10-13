"""US-204 end to end on a small generated store: the offline job (python -m ml.risk), the batch
scoring (python -m ml.scoring), their contract files and the rules they must keep (MOD-04 to
MOD-08, MOD-12, FAIR-02, FAIR-03, DATA-01 for scores/*, rules 4, 6 and 12).

The store is the Navigli Fashion generator at scale 0.05, seed 7, from the illustrative example
calibration (contracts/examples/calibration.json), main and out-of-distribution scenarios: every
number here is synthetic and none is a result. Results come from `python -m ml.risk` on the full
store (docs/results/us-204-risk.md).
"""

from __future__ import annotations

import ast
import json
import shutil
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pytest

from contracts.io import read_table, write_table
from contracts.parquet import SCHEMAS, is_forbidden_feature
from ml.reference.model import TrainConfig
from ml.risk.__main__ import main as risk_main
from ml.risk.report import render
from ml.scoring.__main__ import main as scoring_main
from ml.scoring.batch import open_return_ids, score
from pipeline.features.__main__ import store_month_ends
from pipeline.features.build import build
from pipeline.generator.calibration import load_calibration
from pipeline.generator.core import generate
from pipeline.graph.build import main as graph_main

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "contracts" / "examples" / "calibration.json"
FAST = TrainConfig(folds=3, min_train_lines=500, min_validation_lines=100)


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("risk-store")
    calibration = load_calibration(EXAMPLE)
    generate(calibration, out, seed=7, scale=0.05, scenario="main")
    generate(calibration, out, seed=7, scale=0.05, scenario="ood")
    build(out, out, store_month_ends(out), config=FAST)
    build(out / "ood", out / "ood", store_month_ends(out / "ood"), source_root=out, config=FAST)
    assert graph_main(["--store", str(out)]) == 0
    assert graph_main(["--store", str(out / "ood")]) == 0
    return out


@pytest.fixture(scope="module")
def job(data: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    out = tmp_path_factory.mktemp("risk-job")
    argv = ["--data", str(data), "--out", str(out), "--scores", str(out)]
    assert risk_main([*argv, "--results", str(out / "us-204.md")]) == 0
    return out, json.loads((out / "risk" / "metrics.json").read_text(encoding="utf-8"))


def test_every_model_runs_on_the_same_split_and_no_group_crosses_it(
    job: tuple[Path, dict[str, Any]],
) -> None:
    """MOD-04 and MOD-07: A0..A3 on the same rows, A4 a hook; no cluster, ring or family on
    two sides; the OOD rows are separate (MOD-12)."""
    _, doc = job
    assert doc["split"]["mod07_shared_keys"] == {"cluster": [], "ring": [], "family": []}
    sets = {r["set"]: r for r in doc["ablation"]}
    assert list(sets) == ["A0", "A1", "A2", "A3", "A4"]
    assert sets["A4"]["status"].startswith("not run")
    n_test = doc["customer_rows"]["test"]["rows"]
    for name in ("A0", "A1", "A2", "A3"):
        assert sets[name]["test"]["n"] == n_test
        assert sets[name]["ood"]["n"] == doc["customer_rows"]["ood"]["rows"]
    assert len(doc["request_models"]) == 3
    assert {r["test"]["n"] for r in doc["request_models"]} == {doc["request_rows"]["test"]["rows"]}


def test_every_explained_case_has_three_allowed_factors(job: tuple[Path, dict[str, Any]]) -> None:
    """MOD-05 on the held-out customers and requests, main and OOD."""
    _, doc = job
    assert set(doc["mod05"]) == {"customers_test", "requests_test", "customers_ood", "requests_ood"}
    for check in doc["mod05"].values():
        assert check["rows"] > 0 and check["with_three"] == check["rows"] and not check["forbidden"]


def test_request_rows_never_use_a_later_snapshot(job: tuple[Path, dict[str, Any]]) -> None:
    """MOD-08 for the request unit, on every request of the main and OOD stores."""
    _, doc = job
    for key in ("context_main", "context_ood"):
        assert doc["request_rows"][key]["snapshot_strictly_before_request"]


def test_the_scores_respect_their_contracts(data: Path, job: tuple[Path, dict[str, Any]]) -> None:
    """DATA-01 for scores/*; every customer known at T is scored, with its evidence and value;
    no quadrant without sufficient evidence; three phrased factors per scored row."""
    out, doc = job
    scores, prov = read_table("scores/scores", out)
    assert prov.nature == "synthetic" and "risk-customer-xgboost" in prov.origin
    frame = scores.to_pandas()
    value, _ = read_table("features/customer_value", data)
    v = value.to_pandas()
    v = v.loc[v["as_of"] == v["as_of"].max()].set_index("customer_id")["value_eur"]
    assert sorted(frame["customer_id"]) == sorted(v.index)
    assert (frame.set_index("customer_id")["value_eur"] == v.reindex(frame["customer_id"])).all()
    insufficient = frame["evidence_quality"] == "insufficient"
    assert frame.loc[insufficient, "quadrant"].isna().all()
    assert frame.loc[~insufficient, "quadrant"].notna().all()
    for text in frame["shap_top3"]:
        factors = json.loads(text)
        assert len(factors) == 3
        for f in factors:
            assert not is_forbidden_feature(f["feature"])
            assert "not a cause" in f["phrase_en"] and "non una causa" in f["phrase_it"]
    assert doc["scoring"]["customers"] == len(frame)


def test_open_returns_are_scored_as_known_when_they_arrived(
    data: Path, job: tuple[Path, dict[str, Any]]
) -> None:
    """Every open return at T is listed; guarantee-path ones are never scored (rule 4);
    the customer snapshot of a scored one precedes it (MOD-08)."""
    out, doc = job
    table, _ = read_table("scores/request_scores", out)
    req = table.to_pandas()
    as_of = pd.Timestamp(doc["eval_end"])
    assert sorted(req["return_id"]) == open_return_ids(data, as_of.to_pydatetime())
    guarantee = req["path"] == "guarantee"
    assert guarantee.any() and (~guarantee).any()
    assert req.loc[guarantee, ["risk", "quadrant", "shap_top3"]].isna().all().all()
    scored = req.loc[~guarantee]
    assert scored["risk"].notna().all()
    known = scored.loc[scored["context_as_of"].notna()]
    assert (known["context_as_of"] < known["decided_at"]).all()
    elena = req.loc[req["customer_id"] == "C-ELENA"]
    assert len(elena) == 1 and elena["path"].iloc[0] == "guarantee"
    assert doc["golden"]["checks"]["C-ELENA's open return on the guarantee path, not scored"]


def test_the_scoring_cli_reproduces_the_job(
    data: Path, job: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    """python -m ml.scoring with the saved models writes the same files byte for byte; with
    --truth (offline only) it measures the same FAIR-02 and FAIR-03."""
    out, doc = job
    fairness = tmp_path / "fairness.json"
    argv = ["--data", str(data), "--models", str(out / "risk" / "models"), "--out", str(tmp_path)]
    assert scoring_main([*argv, "--truth", str(data), "--fairness", str(fairness)]) == 0
    for name in ("scores", "request_scores"):
        mine = (tmp_path / "scores" / f"{name}.parquet").read_bytes()
        assert mine == (out / "scores" / f"{name}.parquet").read_bytes(), name
    measured = json.loads(fairness.read_text(encoding="utf-8"))
    assert measured["fair02"] == json.loads(json.dumps(doc["fair02"]))
    assert [c["because_of_cluster"] for c in measured["fair03"]] == [
        c["because_of_cluster"] for c in doc["fair03"]
    ]


def test_customer_value_never_changes_the_risk(
    data: Path, job: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    """Rule 6 (US-205): tripling every customer value moves quadrants, never a risk score."""
    out, _ = job
    copy = tmp_path / "data"
    shutil.copytree(data / "store", copy / "store")
    shutil.copytree(data / "features", copy / "features")
    table, prov = read_table("features/customer_value", copy)
    frame = table.to_pandas()
    frame["value_eur"] = [(Decimal(3) * x).quantize(Decimal("0.01")) for x in frame["value_eur"]]
    schema = SCHEMAS["features/customer_value"]
    rebuilt = pa.Table.from_pandas(frame[schema.names], schema=schema, preserve_index=False)
    write_table("features/customer_value", rebuilt, copy, prov)
    before = score(data, out / "risk" / "models").scores.set_index("customer_id")
    after = score(copy, out / "risk" / "models").scores.set_index("customer_id")
    assert (before["risk"] == after["risk"]).all()
    assert (before["shap_top3"] == after["shap_top3"]).all()
    assert (after["value_eur"] != before["value_eur"]).any()


def test_a_late_refund_does_not_close_a_return_at_the_decision_time(
    data: Path, job: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    """Rule 7: a state recorded after T is not known at T."""
    _, doc = job
    as_of = pd.Timestamp(doc["eval_end"]).to_pydatetime()
    copy = tmp_path / "data"
    shutil.copytree(data / "store", copy / "store")
    open_ids = open_return_ids(copy, as_of)
    events, prov = read_table("store/return_events", copy)
    frame = events.to_pandas()
    late = {
        "return_id": open_ids[0],
        "state": "refunded",
        "event_at": pd.Timestamp(as_of - timedelta(hours=1)),
        "available_at": pd.Timestamp(as_of + timedelta(hours=1)),
    }
    frame = pd.concat([frame, pd.DataFrame([late])], ignore_index=True)
    schema = SCHEMAS["store/return_events"]
    write_table(
        "store/return_events",
        pa.Table.from_pandas(frame[schema.names], schema=schema, preserve_index=False),
        copy,
        prov,
    )
    assert open_return_ids(copy, as_of) == open_ids
    assert open_ids[0] not in open_return_ids(copy, as_of + timedelta(hours=2))


def test_the_results_file_is_the_rendered_metrics(job: tuple[Path, dict[str, Any]]) -> None:
    out, doc = job
    text = (out / "us-204.md").read_text(encoding="utf-8")
    assert text == render(doc)
    for heading in (
        "## Tests of spec 12",
        "## Ablation A0 to A4, customer unit (MOD-04)",
        "### Out-of-distribution scenario, a separate row (MOD-12)",
        "## Request unit",
        "## FAIR-02: false positives by segment",
        "## FAIR-03: shared-benign accounts and the cluster",
        "## Golden cases (demo cases, never a measure of accuracy)",
    ):
        assert heading in text
    assert "Nature: synthetic" in text and "/tmp" not in text.split("## Tests")[0].split("-->")[0]


def test_a_naive_decision_time_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        risk_main(["--eval-end", "2026-10-01T00:00:00"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        scoring_main(["--as-of", "2026-10-01T00:00:00"])
    assert exc.value.code == 2


def _strings(path: Path) -> list[str]:
    """String literals of a module, docstrings excluded."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }


def test_rule12_the_scoring_path_never_names_the_ground_truth() -> None:
    """The batch scoring and every module it imports name no truth/ path; only the offline
    modules do (labels, fairness, run, report), and the scoring CLI imports them only on the
    --truth evaluation path."""
    scoring_path = [
        ROOT / "ml" / "scoring" / "__init__.py",
        ROOT / "ml" / "scoring" / "batch.py",
        *(ROOT / "ml" / "risk" / f"{m}.py" for m in ("data", "features", "model", "explain")),
        *(ROOT / "ml" / "risk" / f"{m}.py" for m in ("metrics", "rules", "split")),
    ]
    for path in scoring_path:
        assert not [s for s in _strings(path) if "truth" in s.lower()], path
    assert any("truth/labels" in s for s in _strings(ROOT / "ml" / "risk" / "labels.py"))
    imports = _module_imports(ROOT / "ml" / "scoring" / "__main__.py")
    assert "ml.risk.labels" not in imports and "ml.risk.fairness" not in imports
    offline = {f"ml.risk.{m}" for m in ("labels", "fairness", "run", "report")}
    assert not _module_imports(ROOT / "ml" / "scoring" / "batch.py") & offline
