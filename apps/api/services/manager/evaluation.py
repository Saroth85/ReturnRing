"""The offline evaluation report of the serving snapshot: false positives by segment (FAIR-02,
US-605, NFR-04) and by KPI cohort (US-603). Rule 12: a false positive needs a label, so these
numbers come from the offline evaluation of ml.risk on the synthetic labels of the held-out test
customers; the API reads the aggregates the export put in the snapshot, never data/truth/.

The file is SERVING_DIR/reports/evaluation.json (format returnring.evaluation.v1), covered by
the manifest like every served file: report_id, scenario, as_of (the evaluation instant),
model_version (the customer model evaluated), the provenance of the evaluation, and fair02, the
FairnessReport of ml.risk.fairness as written to artifacts/risk/metrics.json (population,
non_abusers, fpr, threshold, and one row per dimension and segment with non_abusers and fpr).
pipeline.serving does not write it yet (open issue of E6): without it, /api/fairness and
/api/kpi answer 404 rather than show a rate nobody measured.

What the API serves of it:

- the false-positive rate of a segment is the FAIR-02 rate "flagged": non-abusive customers with
  risk >= the customer model's threshold, over the non-abusive customers of the segment (the
  `customers` of the segment). The rate of the customers placed in Q3 or Q4 stays in the report;
- the dimensions of the contract (FairnessDimension) from the offline ones (OFFLINE_DIMENSIONS);
  the offline "Linked accounts" dimension has no place in the contract and is not served, and a
  dimension with fewer than two measured segments is left out;
- max_gap is the largest minus the smallest rate of a dimension, in percentage points, and the
  warning is raised exactly above GAP_THRESHOLD_PP (5 points, NFR-04), as the contract defines
  it. The offline report also compares each segment with the whole population; both are kept.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Literal, get_args

from apps.api import schemas as s
from apps.api.services.provenance import provenance
from apps.api.serving import Serving
from contracts.io import Nature
from pipeline.serving.manifest import sha256_of

REPORT_FILE: Final = "reports/evaluation.json"
REPORT_FORMAT: Final = "returnring.evaluation.v1"
GAP_THRESHOLD_PP: Final = 5.0  # NFR-04, FAIR-02: at most 5 points between segments
RATE_DECIMALS: Final = 6
# The contract dimension of each FAIR-02 dimension of ml.risk.fairness.segment_labels, by the
# name of the offline dimension without its note in parentheses ("Price band (mean line price)").
OFFLINE_DIMENSIONS: Final[dict[str, s.FairnessDimension]] = {
    "New customers": "new_customers",
    "Families": "households",
    "Low-data groups": "low_data",
    "Tenure": "tenure",
    "Price band": "price_band",
    "Main category": "category",
}
# The KPI cohorts (US-603) among the offline segments: (contract dimension, segment or prefix).
COHORT_SEGMENTS: Final[dict[s.CohortId, tuple[s.FairnessDimension, str]]] = {
    "new_customers": ("new_customers", "opened under 90 days ago"),
    "households": ("households", "family member"),
    "low_data": ("low_data", "fewer than "),
}
_ID: Final = re.compile(s.ID_PATTERN)


class ReportUnavailable(LookupError):
    """The snapshot holds no usable evaluation report: no rate is served without one."""


@dataclass(frozen=True)
class SegmentRate:
    dimension: str  # as named by the offline report
    segment: str
    customers: int  # non-abusive held-out customers of the segment: the denominator
    rate: float | None  # flagged false positives over customers; None without any customer


@dataclass(frozen=True)
class EvaluationReport:
    report_id: str
    scenario: Literal["main", "ood"]
    as_of: datetime
    model_version: str
    provenance: s.Provenance  # of the evaluation: its data, period and nature
    population: str
    non_abusers: int
    rate: float | None
    threshold: float | None
    segments: tuple[SegmentRate, ...]


def contract_dimension(offline: str) -> s.FairnessDimension | None:
    """The contract dimension of an offline FAIR-02 dimension, or None (not served)."""
    return OFFLINE_DIMENSIONS.get(offline.split(" (", 1)[0])


def _rate(value: Any) -> float | None:
    if value is None:
        return None
    rate = float(value)
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"a rate is in [0, 1], got {rate}")
    return rate


def _provenance(p: Mapping[str, Any]) -> s.Provenance:
    """The provenance of the evaluation (contracts.io fields; a long origin is shortened)."""
    nature = p["nature"]
    if nature not in get_args(Nature):
        raise ValueError(f"unknown nature {nature!r}")
    start = date.fromisoformat(str(p["period_start"]))
    end = date.fromisoformat(str(p["period_end"]))
    if start > end:
        raise ValueError("the period of the evaluation starts after it ends")
    return provenance(str(p["origin"]), start, end, nature)


def parse_report(document: Mapping[str, Any]) -> EvaluationReport:
    """The report of a returnring.evaluation.v1 document, or ReportUnavailable."""
    if document.get("format") != REPORT_FORMAT:
        raise ReportUnavailable(f"the evaluation report is not in the {REPORT_FORMAT} format")
    try:
        report_id = str(document["report_id"])
        if not _ID.fullmatch(report_id):
            raise ValueError(f"report_id {report_id!r} is not an id")
        scenario = document["scenario"]
        if scenario not in ("main", "ood"):
            raise ValueError(f"unknown scenario {scenario!r}")
        f02 = document["fair02"]
        segments = tuple(
            SegmentRate(
                dimension=str(row["dimension"]),
                segment=str(row["segment"]),
                customers=int(row["non_abusers"]),
                rate=_rate(row["fpr"]),
            )
            for row in f02["rows"]
        )
        threshold = f02.get("threshold")
        return EvaluationReport(
            report_id=report_id,
            scenario=scenario,
            as_of=datetime.fromisoformat(str(document["as_of"])),
            model_version=str(document["model_version"]),
            provenance=_provenance(document["provenance"]),
            population=str(f02["population"]),
            non_abusers=int(f02["non_abusers"]),
            rate=_rate(f02.get("fpr")),
            threshold=None if threshold is None else float(threshold),
            segments=segments,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReportUnavailable(f"the evaluation report is unreadable: {exc}") from None


def load_report(serving: Serving) -> EvaluationReport:
    """The evaluation report of the snapshot: the file the export wrote and the manifest hashed
    (rule 12: no other file is read as a report)."""
    entry = serving.manifest.files.get(REPORT_FILE)
    path = serving.root / REPORT_FILE
    if entry is None or not path.is_file():
        raise ReportUnavailable(
            "the offline evaluation report (FAIR-02) is not in the serving snapshot: false "
            "positives need labels, which only the offline evaluation reads (rule 12)"
        )
    if sha256_of(path) != entry.get("sha256"):
        raise ReportUnavailable(f"{REPORT_FILE} changed since the export")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ReportUnavailable(f"the evaluation report is unreadable: {exc}") from None
    if not isinstance(document, dict):
        raise ReportUnavailable("the evaluation report is not a JSON object")
    return parse_report(document)


def rates_provenance(report: EvaluationReport) -> s.Provenance:
    """Where the rates come from: the offline evaluation, its population, model and data."""
    p = report.provenance
    threshold = "no threshold" if report.threshold is None else f"risk >= {report.threshold:g}"
    origin = (
        f"offline evaluation {report.report_id} (FAIR-02): non-abusive {report.population} "
        f"flagged by {report.model_version} ({threshold}), synthetic labels; {p.origin}"
    )
    return provenance(origin, p.period_start, p.period_end, p.nature)


def _metric(value: float | int, unit: s.Unit, prov: s.Provenance) -> s.Metric:
    return s.Metric(value=value, unit=unit, provenance=prov)


def _rounded(rate: float) -> float:
    return round(rate, RATE_DECIMALS)


def fairness(report: EvaluationReport) -> s.FairnessResponse:
    """GET /api/fairness of the report (US-605)."""
    prov = rates_provenance(report)
    day = report.as_of.date()
    limit = provenance(
        "NFR-04 and FAIR-02 (spec sections 6 and 12): at most 5 percentage points between the "
        "false-positive rates of the segments of a dimension",
        day,
        day,
        "synthetic",
    )
    reports: list[s.FairnessDimensionReport] = []
    for dimension in get_args(s.FairnessDimension):
        rows = [
            x
            for x in report.segments
            if contract_dimension(x.dimension) == dimension and x.rate is not None
        ]
        if len(rows) < 2:
            continue
        rates = [_rounded(x.rate) for x in rows if x.rate is not None]
        gap = round((max(rates) - min(rates)) * 100, 2)
        reports.append(
            s.FairnessDimensionReport(
                dimension=dimension,
                segments=[
                    s.FairnessSegment(
                        segment=x.segment,
                        false_positive_rate=_metric(rate, "ratio", prov),
                        customers=_metric(x.customers, "count", prov),
                    )
                    for x, rate in zip(rows, rates, strict=True)
                ],
                max_gap=_metric(gap, "percentage_points", prov),
                warning=gap > GAP_THRESHOLD_PP,
            )
        )
    if not reports:
        raise ReportUnavailable(
            "the evaluation report has no FAIR-02 segment in the dimensions of the contract"
        )
    return s.FairnessResponse(
        report_id=report.report_id,
        scenario=report.scenario,
        model_version=report.model_version,
        max_gap_threshold=_metric(GAP_THRESHOLD_PP, "percentage_points", limit),
        dimensions=reports,
    )


def _segment(
    segments: Sequence[SegmentRate], dimension: s.FairnessDimension, prefix: str
) -> SegmentRate | None:
    found = [
        x
        for x in segments
        if contract_dimension(x.dimension) == dimension
        and x.segment.startswith(prefix)
        and x.rate is not None
    ]
    return found[0] if len(found) == 1 else None


def cohort_rates(report: EvaluationReport) -> list[s.CohortRate]:
    """The false positives of the KPI cohorts (US-603): all customers and the cohorts the offline
    FAIR-02 measures. A cohort it does not measure (high_value) is left out, never invented."""
    prov = rates_provenance(report)
    out: list[s.CohortRate] = []
    if report.rate is not None:
        out.append(
            s.CohortRate(
                cohort="all",
                false_positive_rate=_metric(_rounded(report.rate), "ratio", prov),
                customers=_metric(report.non_abusers, "count", prov),
            )
        )
    for cohort, (dimension, prefix) in COHORT_SEGMENTS.items():
        row = _segment(report.segments, dimension, prefix)
        if row is not None and row.rate is not None:
            out.append(
                s.CohortRate(
                    cohort=cohort,
                    false_positive_rate=_metric(_rounded(row.rate), "ratio", prov),
                    customers=_metric(row.customers, "count", prov),
                )
            )
    if not out:
        raise ReportUnavailable("the evaluation report measures no false positive")
    return out
