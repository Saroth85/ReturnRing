"""The A4 EmbeddingSource: GraphSAGE customer embeddings read from features/customer_embeddings
(US-202, US-204; ADR-13 amendment 6). No torch here: the file is written by `python -m ml.gnn
embed` (ml/gnn, offline) and read by the risk job on any machine.

The file must have been computed from the store it is joined to (review F22): its lineage ends with
the chain of that store's store/identifiers. embeddings_at(T) returns the rows computed at exactly
T, never a later snapshot (rule 7), and refuses an instant the file does not hold.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Final

import pandas as pd

from contracts.io import Provenance, path_for, read_table, require_derived
from contracts.parquet import EMBEDDING_COLUMNS
from ml.risk.data import instants, utc

TABLE: Final = "features/customer_embeddings"


class FileEmbeddingSource:
    """features/customer_embeddings under `root`, checked against the store it is joined to."""

    def __init__(self, root: Path | str, store: Provenance) -> None:
        """store: the provenance of the store/identifiers the risk job reads (its lineage check)."""
        table, provenance = read_table(TABLE, root)
        require_derived(provenance, store, TABLE, "store/identifiers")
        self.root = Path(root)
        self.provenance = provenance
        self.frame = table.to_pandas()
        self.instants = instants(self.frame) if len(self.frame) else []

    def embeddings_at(self, as_of: datetime) -> pd.DataFrame:
        """customer_id and graphsage_0..15 of the rows computed at exactly as_of."""
        stamp = pd.Timestamp(utc(as_of))
        rows = self.frame.loc[self.frame["as_of"] == stamp]
        if rows.empty:
            known = ", ".join(t.isoformat() for t in self.instants) or "none"
            raise ValueError(
                f"{path_for(TABLE, self.root)} has no embeddings at {stamp.isoformat()} "
                f"(instants: {known}); run python -m ml.gnn embed with that --as-of"
            )
        return rows[["customer_id", *EMBEDDING_COLUMNS]].reset_index(drop=True)

    def describe(self) -> dict[str, Any]:
        """Provenance and versions for the metrics document (rule 11)."""
        return {
            "file": str(path_for(TABLE, self.root)),
            "origin": self.provenance.origin,
            "lineage": self.provenance.chain,
            "period": f"{self.provenance.period_start}..{self.provenance.period_end}",
            "nature": self.provenance.nature,
            "instants": [t.isoformat() for t in self.instants],
            "encoder_versions": sorted(str(v) for v in pd.unique(self.frame["encoder_version"])),
        }


def embedding_source(root: Path | str, store: Provenance) -> FileEmbeddingSource | None:
    """The source under `root`, or None when features/customer_embeddings does not exist there
    (A4 is then reported as not run)."""
    if not path_for(TABLE, root).exists():
        return None
    return FileEmbeddingSource(root, store)
