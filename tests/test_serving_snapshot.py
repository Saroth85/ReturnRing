"""The serving snapshot (ADR-16; rules 7, 11 and 12; DATA-03).

The API reads only SERVING_DIR. These tests prove the export carries no ground truth (no truth
path, no truth column, no truth ring or family id in any value), hashes every file, refuses a
changed or added file, keeps only rows known at its instant, and that the models it ships score
a request exactly as the batch did.
"""

from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from apps.api.serving import Serving, ServingError
from contracts.io import read_table
from contracts.parquet import SCHEMAS
from ml.reference.model import TrainConfig, fit, observed_frame
from ml.reference.persist import load_line_model, save_line_model
from pipeline.features.lines import line_frame
from pipeline.features.snapshot import load_snapshot
from pipeline.serving.export import (
    SERVED_TABLES,
    TRUTH_COLUMNS,
    TruthError,
    check_columns,
    check_source,
    export,
    scores_as_of,
)
from pipeline.serving.manifest import MANIFEST, Manifest, verify
from policy.engine import quadrant_for
from policy.types import CaseScores
from policy.types import Path as ReturnPath
from tests.api_harness import SEED


def _truth_ids(data: Path) -> set[str]:
    labels, _ = read_table("truth/labels", data)
    frame = labels.to_pandas()
    return {str(v) for c in ("ring_id", "family_id") for v in frame[c].dropna().unique()}


def test_truth_columns_are_the_columns_only_truth_tables_have() -> None:
    assert {"behaviour", "ring_id", "family_id", "defective", "base_return_rate"} <= TRUTH_COLUMNS
    assert "customer_id" not in TRUTH_COLUMNS and "variant_id" not in TRUTH_COLUMNS
    for name in SERVED_TABLES:
        assert not name.startswith("truth/")
        assert not set(SCHEMAS[name].names) & TRUTH_COLUMNS


def test_no_truth_path_column_or_value_reaches_the_snapshot(
    serving_data: Path, serving_dir: Path
) -> None:
    """DATA-03 for the API: the data root holds truth/, the snapshot holds none of it."""
    assert (serving_data / "truth" / "labels.parquet").exists()  # the source does have truth
    truth_ids = _truth_ids(serving_data)
    assert truth_ids  # R-MARCO, F-ROSSI...: none may appear in a served value
    for path in serving_dir.rglob("*"):
        assert "truth" not in path.relative_to(serving_dir).parts, path
        if path.suffix == ".parquet":
            table = pq.read_table(path)
            assert not set(table.column_names) & TRUTH_COLUMNS, path
            for column in table.column_names:
                if table.schema.field(column).type in ("string", "large_string"):
                    values = set(table.column(column).drop_null().to_pylist())
                    assert not values & truth_ids, (path, column)
        elif path.is_file():
            text = path.read_text(encoding="utf-8")
            assert not any(t in text for t in truth_ids), path
    assert not (serving_dir / "models" / "risk" / "metrics.json").exists()  # read truth


def test_the_export_refuses_truth_sources_and_columns(tmp_path: Path) -> None:
    with pytest.raises(TruthError):
        check_source(tmp_path / "data" / "truth")
    with pytest.raises(TruthError):
        check_columns("truth/labels", ["customer_id"])
    with pytest.raises(TruthError):
        check_columns("features/clusters", ["customer_id", "ring_id"])
    with pytest.raises(ServingError):
        Serving.load(tmp_path / "truth" / "serving")


def test_the_manifest_hashes_every_file_and_verify_refuses_changes(
    serving_dir: Path, tmp_path: Path
) -> None:
    assert verify(serving_dir) == []
    manifest = Manifest.read(serving_dir)
    files = {p.relative_to(serving_dir).as_posix() for p in serving_dir.rglob("*") if p.is_file()}
    hashed = files - {MANIFEST} - {f for f in files if f.startswith("llm_cache/")}
    assert set(manifest.files) == hashed
    assert {f"{name}.parquet" for name in SERVED_TABLES} <= hashed
    assert {"models/risk/request.json", "models/checkout/personalized.meta.json"} <= hashed
    assert manifest.seed == SEED
    copy = tmp_path / "copy"
    shutil.copytree(serving_dir, copy)
    (copy / "store" / "extra.parquet").write_bytes(b"x")
    meta = copy / "models" / "risk" / "request.meta.json"
    meta.write_text(meta.read_text(encoding="utf-8") + " ", encoding="utf-8")
    problems = verify(copy)
    assert "not in the manifest: store/extra.parquet" in problems
    assert "changed since the export: models/risk/request.meta.json" in problems
    with pytest.raises(ServingError):
        Serving.load(copy)
    (copy / "llm_cache").mkdir(exist_ok=True)
    (copy / "llm_cache" / "x.json").write_text("{}", encoding="utf-8")
    assert not any("llm_cache" in p for p in verify(copy))  # filled later, checked per entry


def test_tables_keep_their_provenance_and_rows_known_at_the_instant(
    serving_data: Path, serving_dir: Path
) -> None:
    """Rule 7 and rule 11: no row after the snapshot instant; the provenance of the source."""
    as_of = Manifest.read(serving_dir).as_of
    for name in SERVED_TABLES:
        table, prov = read_table(name, serving_dir)
        if name.startswith("store/"):
            late = table.to_pandas()["available_at"] > pd.Timestamp(as_of)
            assert not late.any(), name
            _, source_prov = read_table(name, serving_data)
            assert prov == source_prov, name
        else:
            assert (table.to_pandas()["as_of"] <= pd.Timestamp(as_of)).all(), name
    scores, _ = read_table("scores/scores", serving_dir)
    assert set(scores.to_pandas()["as_of"]) == {pd.Timestamp(as_of)}


def test_the_graph_is_served_at_every_snapshot_a_request_score_read(serving_dir: Path) -> None:
    req = read_table("scores/request_scores", serving_dir)[0].to_pandas()
    clusters = read_table("features/clusters", serving_dir)[0].to_pandas()
    links = read_table("features/links", serving_dir)[0].to_pandas()
    contexts = set(req["context_as_of"].dropna())
    assert contexts and contexts <= set(clusters["as_of"])
    assert contexts <= set(links["as_of"])


def test_the_export_is_deterministic(serving_data: Path, serving_dir: Path, tmp_path: Path) -> None:
    """NFR-10: the same data root gives the same files (the manifest hashes them all)."""
    again = tmp_path / "again"
    export(serving_data, serving_data / "artifacts", again, seed=SEED)
    first = json.loads((serving_dir / MANIFEST).read_text(encoding="utf-8"))
    second = json.loads((again / MANIFEST).read_text(encoding="utf-8"))
    assert first == second


def test_the_export_refuses_to_write_into_the_data_root(serving_data: Path) -> None:
    with pytest.raises(ValueError):
        export(serving_data, serving_data / "artifacts", serving_data)


def test_the_checkout_model_round_trips(serving_data: Path, tmp_path: Path) -> None:
    snap = load_snapshot(serving_data, scores_as_of(serving_data))
    frame = observed_frame(line_frame(snap))
    y = frame["returned"].to_numpy(dtype="float64")
    model = fit(frame, y, frame["customer_id"].to_numpy(), "personalized", snap.as_of,
                TrainConfig(folds=3, min_train_lines=500, min_validation_lines=100))  # fmt: skip
    save_line_model(model, tmp_path)
    loaded = load_line_model(tmp_path, "personalized")
    assert loaded.version == model.version and loaded.kind == model.kind
    np.testing.assert_allclose(loaded.predict(frame), model.predict(frame), rtol=0, atol=1e-12)


def test_live_scoring_equals_the_batch_and_the_engine_quadrant(serving: Serving) -> None:
    """The request model of the snapshot scores an open return exactly as the batch did, and
    the thresholds of the snapshot give the batch's quadrant (spec 8.6)."""
    for return_id in serving.open_return_ids():
        record = serving.store_return(return_id)
        assert record is not None
        batch = serving.batch_score(return_id)
        assert batch is not None and batch.source == "batch"
        if batch.path is ReturnPath.GUARANTEE:
            assert batch.risk is None and not batch.factors  # rule 4
            continue
        context = serving.customer_context(record.customer_id, record.known_at)
        assert context.as_of < record.known_at  # MOD-08
        if context.live or batch.quadrant is None:
            continue
        scores = CaseScores(batch.risk or 0.0, context.value_eur, context.evidence_quality)
        assert quadrant_for(scores, serving.thresholds(context)) == batch.quadrant
    some = [r for r in serving.open_return_ids()[:3]]
    for return_id in some:
        record = serving.store_return(return_id)
        assert record is not None
        live = serving._score_live(record)  # the path of a request the batch does not hold
        batch = serving.batch_score(return_id)
        assert batch is not None
        assert (live.risk, live.path, live.quadrant, live.factors) == (
            batch.risk, batch.path, batch.quadrant, batch.factors
        )  # fmt: skip


def test_an_adapter_request_is_scored_live_at_its_instant(serving: Serving) -> None:
    """A return of the shop adapter (not in the store) gets request features at its instant."""
    first = serving.store_return(serving.open_return_ids()[0])
    assert first is not None
    lines = serving.frame("store/order_lines")
    returns = serving.frame("store/returns")
    deliveries = serving.frame("store/deliveries").set_index("order_id")
    orders = serving.frame("store/orders").set_index("order_id")
    candidates = lines.loc[~lines["line_id"].isin(returns["line_id"])]
    candidates = candidates.loc[candidates["order_id"].isin(deliveries.index)]
    line = candidates.iloc[-1]
    delivered = deliveries.loc[line["order_id"], "available_at"]
    at = max(serving.as_of, delivered.to_pydatetime()) + timedelta(hours=1)
    record = serving.adapter_return("RET-ADAPTER-1", line["line_id"], first.reason, at)
    assert record.customer_id == orders.loc[line["order_id"], "customer_id"]
    score = serving.request_score(record)
    assert score.source == "live" and score.risk is not None and len(score.factors) == 3
    assert serving.request_score(record) is score  # memoized: one score per request
