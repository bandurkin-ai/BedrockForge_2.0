"""Persist a validated, normalized table into PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.importing.service import to_records
from app.models import Assay, Collar, Component, ImportJob, IntervalRow, IntervalTable, Survey

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


def interval_columns(df: pd.DataFrame) -> list[dict]:
    """Kept columns of an interval table: a number column when every filled value is a number."""
    out = []
    for c in [c for c in df.columns if c.startswith("attr:")]:
        s = df[c]
        text = s.astype(str).str.strip()
        filled = s.notna() & (text != "") & (text.str.lower() != "nan")
        numeric = pd.to_numeric(text.str.replace(",", ".", regex=False), errors="coerce")
        kind = "number" if filled.any() and numeric[filled].notna().all() else "text"
        out.append({"name": c[len("attr:"):], "kind": kind, "unit": ""})
    return out


def commit_interval_table(session: Session, set_id: int, name: str, df: pd.DataFrame, *, filename: str,
                          mapping: dict[str, str], kind: str = "interval") -> CommitResult:
    """Create the set's interval table `name`, or replace its rows if it exists (one transaction).
    kind "event": rows have one depth, kept as from = to."""
    columns = interval_columns(df)
    kinds = {c["name"]: c["kind"] for c in columns}
    table = session.scalar(select(IntervalTable).where(IntervalTable.set_id == set_id, IntervalTable.name == name))
    replaced = 0
    if table is None:
        table = IntervalTable(set_id=set_id, name=name)
        session.add(table)
        session.flush()
    else:
        replaced = session.execute(delete(IntervalRow).where(IntervalRow.table_id == table.id)).rowcount
    rows = []
    for rec in to_records(df):
        values = {}
        for col, value_kind in kinds.items():
            v = rec.get(f"attr:{col}")
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            if value_kind == "number":
                try:
                    values[col] = float(str(v).replace(",", "."))
                except ValueError:
                    continue
            else:
                values[col] = str(v).strip()
        top, bottom = (rec.get("depth"), rec.get("depth")) if kind == "event" else (rec.get("from"), rec.get("to"))
        rows.append({"table_id": table.id, "hole_id": str(rec["hole_id"]), "from_m": top, "to_m": bottom, "values": values})
    for start in range(0, len(rows), _CHUNK):
        session.execute(insert(IntervalRow), rows[start:start + _CHUNK])
    table.columns = columns
    table.row_count = len(rows)
    table.filename = filename[:255]
    table.kind = kind
    job = ImportJob(set_id=set_id, kind=kind, filename=filename, row_count=len(rows), mapping=mapping,
                    components=[{"table": name}])
    session.add(job)
    session.flush()
    session.commit()
    return CommitResult(job_id=job.id, kind=kind, inserted=len(rows), replaced=replaced)
