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
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import ml.risk.__main__ as risk_cli
from contracts.io import (
    MAX_ORIGIN_CHARS,
    META_PROVENANCE,
    ContractError,
    Provenance,
    read_table,
    require_derived,
    write_table,
)
from contracts.parquet import EMBEDDING_COLUMNS, EMBEDDING_DIM, SCHEMAS, is_forbidden_feature
from ml.reference.model import TrainConfig
from ml.risk.__main__ import main as risk_main
from ml.risk.report import render
from ml.risk.run import GOLDEN_IDS, metrics_path
from ml.scoring.__main__ import main as scoring_main
from ml.scoring.batch import Models, open_return_ids, read_inputs, score, score_requests
from pipeline.anomalies.__main__ import main as anomalies_main
from pipeline.features.__main__ import store_month_ends
from pipeline.features.build import build, clusters_at
from pipeline.features.snapshot import load_snapshot
from pipeline.generator.calibration import load_calibration
from pipeline.generator.core import generate
from pipeline.graph.build import main as graph_main
from policy.engine import Thresholds, quadrant_for
from policy.types import CaseScores, EvidenceQuality

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
    assert (
        anomalies_main(["--store", str(out), "--out", str(out), "--as-of", "2026-10-01T00:00Z"])
        == 0
    )
    return out


@pytest.fixture(scope="module")
def job(data: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    out = tmp_path_factory.mktemp("risk-job")
    argv = ["--data", str(data), "--out", str(out), "--scores", str(out)]
    code = risk_main([*argv, "--results", str(out / "us-204.md")])
    doc = json.loads(metrics_path(out).read_text(encoding="utf-8"))
    # F21: the job exits 1 when a golden check fails (at this small scale some may).
    assert code == (0 if all(doc["golden"]["checks"].values()) else 1)
    return out, doc


def test_every_model_runs_on_the_same_split_and_no_group_crosses_it(
    job: tuple[Path, dict[str, Any]],
) -> None:
    """MOD-04 and MOD-07: A0..A3 on the same rows, A4 a hook; no cluster, ring or family on
    two sides; the OOD rows are separate (MOD-12)."""
    _, doc = job
    assert doc["split"]["mod07_shared_keys"] == {"cluster": 0, "ring": 0, "family": 0}
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


# ---- Phase 2 review: F2, F4, F21 ----------------------------------------------------------------


def test_f2_offline_outputs_hold_no_customer_with_its_ground_truth(
    data: Path, job: tuple[Path, dict[str, Any]]
) -> None:
    """F2 (rule 12): the metrics go to <out>/eval/, apart from the models a service loads; they
    hold counts and metrics, never a non-golden customer id (it would carry its label)."""
    out, _ = job
    assert metrics_path(out) == out / "eval" / "risk" / "metrics.json"
    assert not (out / "risk" / "metrics.json").exists()
    text = metrics_path(out).read_text(encoding="utf-8")
    labels, _ = read_table("truth/labels", data)
    golden_ids = set(GOLDEN_IDS)
    named = [c for c in labels.column("customer_id").to_pylist() if f'"{c}"' in text]
    assert set(named) <= golden_ids, sorted(set(named) - golden_ids)[:5]
    rings = {r for r in labels.column("ring_id").to_pylist() if r}
    families = {f for f in labels.column("family_id").to_pylist() if f}
    assert not [k for k in rings | families if f'"{k}"' in text]


def test_f4_every_row_is_split_by_abusive_behaviour(job: tuple[Path, dict[str, Any]]) -> None:
    """F4: the ablation and request rows, main and OOD, also report each abusive behaviour
    against the non-abusive rows, so the pooled OOD row cannot hide where the shift is."""
    _, doc = job
    run_rows = [r for r in doc["ablation"] if r["status"] == "run"]
    for row in [*run_rows, *doc["request_models"]]:
        for key in ("test", "ood"):
            parts = row[f"{key}_by_behaviour"]
            assert set(parts) <= {"wardrober", "promo_ring"} and parts
            pooled = row[key]
            negatives = pooled["n"] - pooled["positives"]
            assert sum(p["positives"] for p in parts.values()) == pooled["positives"]
            assert all(p["n"] - p["positives"] == negatives for p in parts.values())
    text = render(doc)
    assert "by abusive behaviour" in text and "wardrober" in text


def test_f21_golden_and_fair03_at_every_month_end(job: tuple[Path, dict[str, Any]]) -> None:
    """F21: the F-ROSSI check and FAIR-03 run at every month-end snapshot up to the evaluation
    time; instants before the train end are marked as in-sample backtests."""
    _, doc = job
    history = doc["over_time"]
    assert [h["as_of"] for h in history][-1] == doc["eval_end"]
    assert len(history) == 12  # 2025-11-01 .. 2026-10-01
    train_end = datetime.fromisoformat(doc["train_end"])
    for h in history:
        assert h["in_sample"] == (datetime.fromisoformat(h["as_of"]) < train_end)
        assert [f["population"] for f in h["fair03"]] == [
            "all customers",
            "held-out test customers",
        ]
    rossi = next(k for k in doc["golden"]["checks"] if k.startswith("F-ROSSI"))
    assert rossi.endswith("at every month end")
    assert doc["golden"]["checks"][rossi] == (
        not any(h["rossi_restrictive"] for h in history)
        and all(
            c["quadrant"] not in ("Q3", "Q4")
            for c in doc["golden"]["customers"]
            if c["customer_id"].startswith("C-ROSSI")
        )
    )
    assert "## Golden cases and FAIR-03 at every month end" in render(doc)


def test_f21_a_failed_golden_check_is_exit_code_1(
    job: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out, doc = job
    failing = json.loads(json.dumps(doc))
    failing["golden"]["checks"] = {"C-GIULIA in Q1": False, "R-MARCO accounts high risk": True}

    def fake_run(data: Path, out_root: Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
        path = metrics_path(out_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(failing), encoding="utf-8")
        return failing

    monkeypatch.setattr(risk_cli, "run", fake_run)
    assert risk_main(["--out", str(tmp_path), "--no-results"]) == 1
    failing["golden"]["checks"]["C-GIULIA in Q1"] = True
    assert risk_main(["--out", str(tmp_path), "--no-results"]) == 0


# ---- Phase 2 review: F1, F15, F17, F19, F20, F22 ------------------------------------------------


def test_f1_scoring_before_the_models_were_trained_is_refused(
    data: Path, job: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    """F1 (rule 7): a decision time before the training end would score with labels from after
    it; it is refused, or written as an explicit in-sample backtest."""
    out, doc = job
    models = out / "risk" / "models"
    early = datetime(2026, 4, 1, tzinfo=UTC)
    assert early < datetime.fromisoformat(doc["train_end"])
    with pytest.raises(ValueError, match="trained"):
        score(data, models, early)
    back = score(data, models, early, backtest=True)
    assert "in-sample backtest" in back.provenance.origin
    assert back.summary["backtest"] is True
    at_end = score(data, models)
    assert "backtest" not in at_end.provenance.origin and at_end.summary["backtest"] is False
    argv = ["--data", str(data), "--models", str(models), "--out", str(tmp_path)]
    with pytest.raises(SystemExit) as exc:
        scoring_main([*argv, "--as-of", "2026-04-01T00:00:00Z"])
    assert exc.value.code == 2
    assert scoring_main([*argv, "--as-of", "2026-04-01T00:00:00Z", "--backtest"]) == 0


def test_f15_every_quadrant_is_reproduced_from_its_own_row(
    job: tuple[Path, dict[str, Any]],
) -> None:
    """F15: the thresholds behind each stored quadrant are on its row; quadrant_for of the row
    gives the stored quadrant, for customers and for requests (with the value threshold of the
    request's own snapshot)."""
    out, doc = job
    th = doc["scoring"]["thresholds"]
    for name in ("scores/scores", "scores/request_scores"):
        frame = read_table(name, out)[0].to_pandas()
        sufficient = frame.loc[frame["evidence_quality"] == "sufficient"]
        assert len(sufficient)
        for r in sufficient.itertuples(index=False):
            limits = Thresholds(risk=float(r.risk_threshold), value_eur=r.value_threshold_eur)
            scores = CaseScores(float(r.risk), r.value_eur, EvidenceQuality.SUFFICIENT)
            assert quadrant_for(scores, limits).value == r.quadrant
    customers = read_table("scores/scores", out)[0].to_pandas()
    assert set(customers["risk_threshold"]) == {th["customer_risk"]}
    assert {str(v) for v in customers["value_threshold_eur"]} == {th["value_eur"]}
    requests = read_table("scores/request_scores", out)[0].to_pandas()
    scored = requests.loc[requests["path"] == "withdrawal"]
    assert set(scored["risk_threshold"]) == {th["request_risk"]}


def test_f17_f22_score_provenance_is_short_and_chained_to_its_inputs(
    data: Path, job: tuple[Path, dict[str, Any]]
) -> None:
    """F17: every origin written fits the API (300 characters); the lineage chains the scores to
    the features and the features to the store they were computed from (F22)."""
    out, _ = job
    _, store = read_table("store/orders", data)
    _, features = read_table("features/customer_features", data)
    _, clusters = read_table("features/clusters", data)
    _, anomalies = read_table("features/product_anomalies", data)
    require_derived(features, store, "features/customer_features", "store/orders")
    require_derived(clusters, store, "features/clusters", "store/orders")
    require_derived(anomalies, store, "features/product_anomalies", "store/orders")
    for name in ("scores/scores", "scores/request_scores"):
        _, prov = read_table(name, out)
        require_derived(prov, features, name, "features/customer_features")
    for path in [*data.rglob("*.parquet"), *out.rglob("*.parquet")]:
        meta = pq.read_schema(path).metadata
        origin = json.loads(meta[META_PROVENANCE])["origin"]
        assert len(origin) <= MAX_ORIGIN_CHARS, (path, len(origin))


def test_f19_f20_request_rows_without_a_snapshot_or_on_the_guarantee_path(
    data: Path, job: tuple[Path, dict[str, Any]]
) -> None:
    """F19: a scored request with no earlier snapshot of its account has insufficient evidence
    (standard terms with a missing-evidence reason), never a null one. F20: a guarantee-path row
    has no model version; a scored row names the request model."""
    out, doc = job
    models = Models.load(out / "risk" / "models")
    as_of = datetime.fromisoformat(doc["eval_end"])
    inputs = read_inputs(data, as_of)
    snap = load_snapshot(data, as_of)
    ids = open_return_ids(data, as_of)
    blind = replace(inputs, times=[])  # as if no snapshot had ever been computed
    rows = score_requests(models, blind, snap, ids, as_of)
    scored = rows.loc[rows["path"] == "withdrawal"]
    assert len(scored) and scored["context_as_of"].isna().all()
    assert (scored["evidence_quality"] == "insufficient").all()
    assert scored["quadrant"].isna().all() and scored["value_eur"].isna().all()
    assert (scored["model_version"] == models.request.version).all()
    guarantee = rows.loc[rows["path"] == "guarantee"]
    assert len(guarantee) and guarantee["model_version"].isna().all()
    assert guarantee["evidence_quality"].isna().all()
    table, _ = read_table("scores/request_scores", out)
    written = table.to_pandas()
    assert written.loc[written["path"] == "guarantee", "model_version"].isna().all()
    assert written.loc[written["path"] == "withdrawal", "evidence_quality"].notna().all()


def _restamp(root: Path, name: str, origin: str) -> None:
    """Rewrite a table of `root` as if it had been computed from another store."""
    table, prov = read_table(name, root)
    other = Provenance(origin, prov.period_start, prov.period_end, prov.nature)
    stale = other.derive(prov.origin, prov.period_start, prov.period_end)
    write_table(name, table, root, stale)


@pytest.mark.parametrize(
    "name", ["features/customer_features", "features/evidence_quality", "features/clusters"]
)
def test_f22_a_table_built_from_another_store_is_never_joined(
    data: Path, job: tuple[Path, dict[str, Any]], tmp_path: Path, name: str
) -> None:
    """F22: customer ids are the same at any seed, so a features/ table left over from another
    store would join silently; its lineage must end with the current store's origin."""
    _, doc = job
    copy = tmp_path / "data"
    shutil.copytree(data / "store", copy / "store")
    shutil.copytree(data / "features", copy / "features")
    _restamp(copy, name, "Navigli Fashion generator (seed 8, another store)")
    as_of = datetime.fromisoformat(doc["eval_end"])
    with pytest.raises(ContractError, match=name):
        read_inputs(copy, as_of)
    if name == "features/clusters":
        _, store = read_table("store/identifiers", copy)
        with pytest.raises(ContractError, match=name):
            clusters_at(copy, as_of, store)


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


def noise_embeddings(data: Path, root: Path, instants: list[datetime]) -> None:
    """features/customer_embeddings under root for every customer of `data` at each instant,
    derived from its store: seeded noise, no torch. The A4 hook is under test here; GraphSAGE
    itself is tested in tests/test_gnn_jobs.py."""
    features = read_table("features/customer_features", data)[0].to_pandas()
    _, store = read_table("store/identifiers", data)
    rng = np.random.default_rng(0)
    parts = []
    for t in instants:
        ids = features.loc[features["as_of"] == pd.Timestamp(t), "customer_id"].tolist()
        z = rng.normal(size=(len(ids), EMBEDDING_DIM))
        columns: dict[str, Any] = {"customer_id": ids}
        columns |= {name: z[:, j] for j, name in enumerate(EMBEDDING_COLUMNS)}
        columns |= {"encoder_version": ["noise-test"] * len(ids), "as_of": [t] * len(ids)}
        parts.append(pa.table(columns, schema=SCHEMAS["features/customer_embeddings"]))
    provenance = store.derive("test noise embeddings", store.period_start, store.period_end)
    write_table("features/customer_embeddings", pa.concat_tables(parts), root, provenance)


def test_a4_runs_on_the_same_rows_when_the_embeddings_exist(
    data: Path, job: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    """US-202 A4 hook: with features/customer_embeddings (main at the train end and the
    evaluation time, OOD at the evaluation time) the ablation runs A4 on the rows of A0..A3, which
    do not change; without the file A4 is reported as not run (the job fixture)."""
    _, doc = job
    train_end = datetime.fromisoformat(doc["train_end"])
    eval_end = datetime.fromisoformat(doc["eval_end"])
    emb = tmp_path / "emb"
    noise_embeddings(data, emb, [train_end, eval_end])
    noise_embeddings(data / "ood", emb / "ood", [eval_end])
    out = tmp_path / "out"
    argv = ["--data", str(data), "--out", str(out), "--no-scores", "--embeddings", str(emb)]
    risk_main([*argv, "--results", str(out / "us-204.md")])
    with_a4 = json.loads(metrics_path(out).read_text(encoding="utf-8"))
    before = {r["set"]: r for r in doc["ablation"]}
    after = {r["set"]: r for r in with_a4["ablation"]}
    a4 = after["A4"]
    assert a4["status"] == "run"
    assert a4["features"] == [*after["A3"]["features"], *EMBEDDING_COLUMNS]
    for population in ("test", "ood"):
        assert a4[population]["n"] == after["A3"][population]["n"]
    assert all(v["without_embedding"] == 0 for v in a4["embedding_coverage"].values())
    for name in ("A0", "A1", "A2", "A3"):
        assert after[name]["test"] == before[name]["test"], name
    assert with_a4["embeddings"]["main"]["encoder_versions"] == ["noise-test"]
    assert (out / "risk" / "models" / "A4.json").exists()
    text = (out / "us-204.md").read_text(encoding="utf-8")
    assert "A0 to A4 reported on the same rows" in text and "| A3 -> A4 |" in text
    # An embedding file without the train end is refused, never silently skipped.
    late = tmp_path / "late"
    noise_embeddings(data, late, [eval_end])
    with pytest.raises(ValueError, match="no embeddings at"):
        risk_main(
            [
                "--data",
                str(data),
                "--out",
                str(tmp_path / "x"),
                "--no-scores",
                "--no-results",
                "--embeddings",
                str(late),
            ]
        )


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
