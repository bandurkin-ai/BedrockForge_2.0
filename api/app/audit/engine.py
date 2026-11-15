"""Load a drillhole set's data, run the checks and store the result."""

from __future__ import annotations

from dataclasses import asdict, replace

import pandas as pd
from sqlalchemy import delete, insert, select, text
from sqlalchemy.orm import Session

from app.audit.checks import AuditConfig, AuditData, Finding, run_checks
from app.audit.rules import RULES
from app.models import AuditFinding, AuditRun, Collar, Survey
from app.samples import resolve_source, source_sql

_CHUNK = 5000
_ASSAY_KEYS = ["id", "hole_id", "sample_id", "from_m", "to_m"]


def latest_run(session: Session, set_id: int) -> AuditRun | None:
    return session.scalars(
        select(AuditRun).where(AuditRun.set_id == set_id).order_by(AuditRun.id.desc()).limit(1)
    ).first()


def _as_float(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    return df.astype({c: "float64" for c in columns})


def load_data(session: Session, set_id: int, table: str | None = None) -> AuditData:
    conn = session.connection()
    collar = pd.read_sql(
        select(Collar.hole_id, Collar.x, Collar.y, Collar.z, Collar.td, Collar.azimuth, Collar.dip, Collar.status)
        .where(Collar.set_id == set_id),
        conn,
    )
    survey = pd.read_sql(
        select(Survey.id, Survey.hole_id, Survey.depth, Survey.azimuth, Survey.dip)
        .where(Survey.set_id == set_id),
        conn,
    )
    src = resolve_source(session, set_id, table)
    from_sql, params = source_sql(src)
    raw = pd.read_sql(text(f"SELECT id, hole_id, sample_id, from_m, to_m, analytes FROM {from_sql}"), conn, params=params)
    if not src.builtin:  # a table of its own: only its numbers are components
        raw["analytes"] = [{k: v for k, v in (a or {}).items() if k in src.units} for a in raw["analytes"]]
    # An empty result comes back as object columns; make the numeric ones float explicitly.
    collar = _as_float(collar, ["x", "y", "z", "td", "azimuth", "dip"])
    survey = _as_float(survey, ["depth", "azimuth", "dip"])
    raw = _as_float(raw, ["from_m", "to_m"])
    assay = raw[_ASSAY_KEYS]
    components = pd.DataFrame.from_records(list(raw["analytes"]), index=raw.index)
    if components.shape[1] == 0:
        values = pd.DataFrame(columns=[*_ASSAY_KEYS, "component", "value"])
    else:
        values = assay.join(components).melt(id_vars=_ASSAY_KEYS, var_name="component", value_name="value")
        values["value"] = pd.to_numeric(values["value"], errors="coerce")
    return AuditData(collar=collar, survey=survey, assay=assay, values=values)


def summarize(findings: list[Finding]) -> dict:
    by_severity = {"error": 0, "warning": 0, "info": 0}
    by_code: dict[str, dict[str, int]] = {}
    for f in findings:
        by_severity[f.severity] += 1
        entry = by_code.setdefault(f.code, {"findings": 0, "rows": 0})
        entry["findings"] += 1
        entry["rows"] += f.count
    return {"by_severity": by_severity, "by_code": by_code}


def run_audit(session: Session, set_id: int, cfg: AuditConfig | None = None) -> AuditRun:
    """Run all checks and replace the set's stored audit with the new result."""
    cfg = cfg or AuditConfig()
    # The table checked is stored with the result, so the report says which one it was.
    cfg = replace(cfg, table=resolve_source(session, set_id, cfg.table).key)
    findings = run_checks(load_data(session, set_id, cfg.table), cfg)

    session.execute(delete(AuditRun).where(AuditRun.set_id == set_id))
    run = AuditRun(set_id=set_id, config=asdict(cfg), counts=summarize(findings))
    session.add(run)
    session.flush()

    rows = [
        {
            "run_id": run.id, "code": f.code, "severity": f.severity, "table_name": f.table,
            "hole_id": f.hole_id, "count": f.count, "message": f.message, "details": f.details,
        }
        for f in findings
    ]
    for start in range(0, len(rows), _CHUNK):
        session.execute(insert(AuditFinding), rows[start:start + _CHUNK])
    session.commit()
    return run


def rule_table(counts: dict) -> list[dict]:
    """Every rule with its result, including rules that found nothing."""
    by_code = counts.get("by_code", {})
    return [
        {
            "code": r.code, "severity": r.severity, "table": r.table, "title": r.title,
            "description": r.description,
            "findings": by_code.get(r.code, {}).get("findings", 0),
            "rows": by_code.get(r.code, {}).get("rows", 0),
        }
        for r in RULES.values()
    ]
