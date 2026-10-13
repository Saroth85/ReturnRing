"""Write and read contract tables: validation, provenance metadata, deterministic files (ADR-13).

write_table validates a table against SCHEMAS[name] and contracts/checks.py, checks that its
provenance fits the table family (nature, and for event tables the period of every event_at),
embeds the contract name, the contract version and the provenance (origin, period, nature: rule
11, NFR-16) in the Parquet metadata, and writes it deterministically: the same rows give a
byte-identical file whatever their chunking (DATA-04), atomically (contracts.files). read_table
checks the contract of the file;
with as_of it keeps the rows of an event table available at that instant, and refuses an as-of
table computed after it (rule 7).

Paths are <root>/<name>.parquet, for example data/store/orders.parquet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, cast

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from contracts.checks import checks_for, finite_floats
from contracts.files import atomic_write
from contracts.parquet import AS_OF_TABLES, CONTRACT_VERSION, EVENT_TABLES, SCHEMAS, TS

Nature = Literal["real", "synthetic", "simulated"]
NATURES: tuple[Nature, ...] = ("real", "synthetic", "simulated")

# The nature each table family may carry (rule 11): ASOS is real, the generator's store and truth
# are synthetic, features and scores are computed from synthetic or simulated data. A caller may
# write another nature only by naming it in write_table(..., nature_override=...).
FAMILY_NATURES: dict[str, tuple[Nature, ...]] = {
    "raw": ("real",),
    "store": ("synthetic",),
    "truth": ("synthetic",),
    "features": ("synthetic", "simulated"),
    "scores": ("synthetic", "simulated"),
}

META_CONTRACT = b"returnring.contract"
META_VERSION = b"returnring.contract_version"
META_PROVENANCE = b"returnring.provenance"

# Fixed writer settings: no wall-clock value is written, so equal input gives equal bytes.
_WRITE_OPTIONS: dict[str, Any] = {
    "compression": "zstd",
    "compression_level": 3,
    "use_dictionary": True,
    "write_statistics": True,
    "version": "2.6",
    "data_page_version": "1.0",
    "row_group_size": 1_000_000,
    "store_schema": True,
}


class ContractError(ValueError):
    """A table, a file or a provenance that does not respect its contract."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        self.errors = errors or [message]
        super().__init__(message if errors is None else f"{message}: " + "; ".join(errors))


@dataclass(frozen=True)
class Provenance:
    """Where the numbers of a file come from (rule 11): origin, period and nature."""

    origin: str
    period_start: date
    period_end: date
    nature: Nature

    def __post_init__(self) -> None:
        if not isinstance(self.origin, str) or not self.origin.strip():
            raise ContractError("provenance origin is required")
        for field in ("period_start", "period_end"):
            value = getattr(self, field)
            if isinstance(value, datetime) or not isinstance(value, date):
                raise ContractError(f"provenance {field} must be a date, got {value!r}")
        if self.period_start > self.period_end:
            raise ContractError("provenance period_start is after period_end")
        if self.nature not in NATURES:
            raise ContractError(f"provenance nature must be one of {NATURES}, not {self.nature!r}")

    def to_json(self) -> str:
        return json.dumps(
            {
                "origin": self.origin,
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
                "nature": self.nature,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, text: str | bytes) -> Provenance:
        try:
            data = json.loads(text)
            return cls(
                origin=data["origin"],
                period_start=date.fromisoformat(data["period_start"]),
                period_end=date.fromisoformat(data["period_end"]),
                nature=cast(Nature, data["nature"]),
            )
        except ContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(f"invalid provenance metadata: {exc!r}") from None


def _schema(name: str) -> pa.Schema:
    try:
        return SCHEMAS[name]
    except KeyError:
        raise ContractError(f"unknown contract {name!r}") from None


def path_for(name: str, root: Path | str) -> Path:
    _schema(name)
    return Path(root) / f"{name}.parquet"


def _same_type(actual: pa.DataType, expected: pa.DataType) -> bool:
    """Equal types; for lists, equal element types whatever the element field is called."""
    if pa.types.is_list(expected):
        return pa.types.is_list(actual) and _same_type(actual.value_type, expected.value_type)
    return bool(actual.equals(expected))


def _structure_errors(schema: pa.Schema, table: pa.Table) -> list[str]:
    if len(set(table.column_names)) != len(table.column_names):
        return [f"duplicate column names in {table.column_names}"]
    errors: list[str] = []
    missing = [n for n in schema.names if n not in table.column_names]
    extra = [n for n in table.column_names if n not in schema.names]
    if missing:
        errors.append(f"missing columns {missing}")
    if extra:
        errors.append(f"unexpected columns {extra}")
    for field in schema:
        if field.name not in table.column_names:
            continue
        col = table.column(field.name)
        if not _same_type(col.type, field.type):
            errors.append(f"{field.name}: type {col.type}, contract type {field.type}")
            continue
        if not field.nullable and col.null_count:
            errors.append(f"{field.name}: {col.null_count} null values in a non-null column")
        if pa.types.is_list(field.type) and not field.type.value_field.nullable:
            nulls = pc.list_flatten(col).null_count
            if nulls:
                errors.append(f"{field.name}: {nulls} null list elements")
    return errors


def validate_table(name: str, table: pa.Table) -> list[str]:
    """Every violation of the contract `name` in `table`; an empty list means valid."""
    schema = _schema(name)
    errors = _structure_errors(schema, table)
    if errors:  # value checks assume the right columns and types
        return errors
    table = table.select(schema.names).combine_chunks()
    errors = finite_floats(table)
    for check in checks_for(name):
        errors.extend(check.violations(table))
    return errors


def provenance_errors(
    name: str, table: pa.Table, provenance: Provenance, nature_override: Nature | None = None
) -> list[str]:
    """Why `provenance` does not fit the table `name` (rule 11); an empty list means it fits.

    The nature must be one of FAMILY_NATURES for the family, unless the caller names it in
    nature_override. Every event_at of an event table lies in the provenance period (UTC days)."""
    _schema(name)
    errors: list[str] = []
    family = name.split("/", 1)[0]
    allowed = FAMILY_NATURES[family]
    if nature_override is not None:
        if nature_override != provenance.nature:
            errors.append(
                f"nature_override {nature_override!r} differs from the provenance nature "
                f"{provenance.nature!r}"
            )
    elif provenance.nature not in allowed:
        errors.append(
            f"provenance nature {provenance.nature!r}: {family}/ tables are {' or '.join(allowed)} "
            "(pass nature_override to write another nature on purpose)"
        )
    if name in EVENT_TABLES and table.num_rows:
        days = pc.cast(table.column("event_at"), pa.date32())
        first, last = pc.min(days).as_py(), pc.max(days).as_py()
        if first < provenance.period_start or last > provenance.period_end:
            errors.append(
                f"event_at from {first} to {last} is outside the provenance period "
                f"{provenance.period_start}..{provenance.period_end}"
            )
    return errors


def write_table(
    name: str,
    table: pa.Table,
    root: Path | str,
    provenance: Provenance,
    *,
    nature_override: Nature | None = None,
) -> Path:
    """Validate `table` against the contract `name` and write it to <root>/<name>.parquet.

    nature_override: write a nature that the family does not normally carry, for example a
    synthetic test fixture shaped like the real ASOS tables; it must equal provenance.nature."""
    schema = _schema(name)
    errors = validate_table(name, table)
    if errors:
        raise ContractError(f"{name} violates its contract", errors)
    errors = provenance_errors(name, table, provenance, nature_override)
    if errors:
        raise ContractError(f"{name}: provenance does not fit the table", errors)
    metadata = {
        META_CONTRACT: name.encode(),
        META_VERSION: CONTRACT_VERSION.encode(),
        META_PROVENANCE: provenance.to_json().encode(),
    }
    # One chunk per column: the row groups and pages written depend on the rows only, never on
    # how the caller's table happens to be chunked (DATA-04).
    conformed = table.select(schema.names).cast(schema).combine_chunks()
    conformed = conformed.replace_schema_metadata(metadata)
    path = path_for(name, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Through a new temporary file, never a planted symlink; nothing left on failure (P3).
    return atomic_write(path, lambda out: pq.write_table(conformed, out, **_WRITE_OPTIONS))


def read_table(
    name: str, root: Path | str, as_of: datetime | None = None
) -> tuple[pa.Table, Provenance]:
    """Read a contract file and its provenance. With as_of (rule 7):

    - event table: keep the rows whose available_at <= as_of, what was known at that instant;
    - as-of table: every row must have been computed at or before as_of, else ContractError;
    - static table (raw/, truth/): refused, they have no decision time."""
    schema = _schema(name)
    if as_of is not None:
        if name not in EVENT_TABLES and name not in AS_OF_TABLES:
            raise ContractError(f"{name} is a static table: it has no decision time for as_of")
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ContractError("as_of must be a timezone-aware datetime")
    table = pq.read_table(path_for(name, root))
    meta = table.schema.metadata or {}
    found = meta.get(META_CONTRACT)
    if found != name.encode():
        raise ContractError(f"file holds contract {found!r}, expected {name!r}")
    version = meta.get(META_VERSION)
    if version != CONTRACT_VERSION.encode():
        raise ContractError(f"contract version {version!r}, expected {CONTRACT_VERSION!r}")
    if not table.schema.remove_metadata().equals(schema):
        raise ContractError(f"{name}: file schema differs from the contract schema")
    found_provenance = meta.get(META_PROVENANCE)
    if found_provenance is None:
        raise ContractError(f"{name}: the file has no provenance metadata (rule 11)")
    provenance = Provenance.from_json(found_provenance)
    if as_of is not None:
        limit = pa.scalar(as_of.astimezone(UTC), type=TS)
        if name in EVENT_TABLES:
            table = table.filter(pc.less_equal(table.column("available_at"), limit))
        else:
            later = pc.sum(pc.greater(table.column("as_of"), limit)).as_py() or 0
            if later:
                raise ContractError(
                    f"{name}: {later} rows were computed after as_of {as_of.isoformat()}: "
                    "they hold facts not known at that instant"
                )
    return table, provenance
