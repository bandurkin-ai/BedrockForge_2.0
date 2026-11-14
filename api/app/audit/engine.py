"""Load a project's data, run the checks and store the result."""

from __future__ import annotations

from dataclasses import asdict

import pandas as pd
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.audit.checks import AuditConfig, AuditData, Finding, run_checks
from app.audit.rules import RULES
from app.models import Assay, AuditFinding, AuditRun, Collar, Survey

_CHUNK = 5000
_ASSAY_KEYS = ["id", "hole_id", "sample_id", "from_m", "to_m"]


def _as_float(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    return df.astype({c: "float64" for c in columns})


def load_data(session: Session, project_id: int) -> AuditData:
    conn = session.connection()
    collar = pd.read_sql(
        select(Collar.hole_id, Collar.x, Collar.y, Collar.z, Collar.td, Collar.azimuth, Collar.dip, Collar.status)
        .where(Collar.project_id == project_id),
        conn,
    )
    survey = pd.read_sql(
        select(Survey.id, Survey.hole_id, Survey.depth, Survey.azimuth, Survey.dip)
        .where(Survey.project_id == project_id),
        conn,
    )
    raw = pd.read_sql(
        select(Assay.id, Assay.hole_id, Assay.sample_id, Assay.from_m, Assay.to_m, Assay.analytes)
        .where(Assay.project_id == project_id),
        conn,
    )
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


def run_audit(session: Session, project_id: int, cfg: AuditConfig | None = None) -> AuditRun:
    """Run all checks and replace the project's stored audit with the new result."""
    cfg = cfg or AuditConfig()
    findings = run_checks(load_data(session, project_id), cfg)

    session.execute(delete(AuditRun).where(AuditRun.project_id == project_id))
    run = AuditRun(project_id=project_id, config=asdict(cfg), counts=summarize(findings))
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
