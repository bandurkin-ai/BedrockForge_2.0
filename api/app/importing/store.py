"""Persist a validated, normalized table into PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import delete, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.importing.service import to_records
from app.models import Assay, Collar, Component, ImportJob, Survey

MODELS = {"collar": Collar, "survey": Survey, "assay": Assay}
# Normalized key -> model column, where they differ.
_COLUMN_ALIASES = {"from": "from_m", "to": "to_m"}
_CHUNK = 5000


@dataclass(frozen=True)
class CommitResult:
    job_id: int
    kind: str
    inserted: int
    replaced: int


def _rows(kind: str, df: pd.DataFrame, set_id: int) -> list[dict]:
    base = [c for c in df.columns if not c.startswith(("attr:", "comp:"))]
    attrs = [c for c in df.columns if c.startswith("attr:")]
    comps = [c for c in df.columns if c.startswith("comp:")]

    rows = []
    for rec in to_records(df):
        row = {"set_id": set_id}
        for col in base:
            row[_COLUMN_ALIASES.get(col, col)] = rec[col]
        row["attributes"] = {c[len("attr:"):]: rec[c] for c in attrs}
        if kind == "assay":
            row["analytes"] = {c[len("comp:"):]: rec[c] for c in comps}
        rows.append(row)
    return rows


def commit_import(
    session: Session,
    set_id: int,
    kind: str,
    df: pd.DataFrame,
    *,
    filename: str,
    mapping: dict[str, str],
    components: list[dict[str, str]],
) -> CommitResult:
    """Replace the drillhole set's rows of this kind with the file's rows (one transaction)."""
    model = MODELS[kind]
    rows = _rows(kind, df, set_id)

    replaced = session.execute(delete(model).where(model.set_id == set_id)).rowcount
    for start in range(0, len(rows), _CHUNK):
        session.execute(insert(model), rows[start:start + _CHUNK])

    if components:
        stmt = pg_insert(Component).values(
            [{"set_id": set_id, "name": c["column"], "unit": c["unit"].strip()} for c in components]
        )
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["set_id", "name"], set_={"unit": stmt.excluded.unit}
            )
        )

    job = ImportJob(
        set_id=set_id,
        kind=kind,
        filename=filename,
        row_count=len(rows),
        mapping=mapping,
        components=components,
    )
    session.add(job)
    session.flush()
    session.commit()
    return CommitResult(job_id=job.id, kind=kind, inserted=len(rows), replaced=replaced)
