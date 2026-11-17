"""Audit of a drillhole set (ТЗ 7.12): data of the set as arrays, the run, the stored result.

The last result of a set is kept (a new run replaces it) with the thresholds used, the sample table checked
and the versions of the data: the result is «устарел» once the set version (collars, surveys, imports) or the
revision of the checked table moved. Thresholds and the chosen table are kept per set in `settings`
(scope «audit»).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import audit_findings, audit_runs, dh_holes, dh_surveys, dh_tables, objects, settings

SCOPE = "audit"
PORTION = 5_000
STATUS_KEYS = ("status", "статус")


def sample_tables(conn: sa.Connection, set_id: int) -> list[dict[str, Any]]:
    """Tables that can be checked as samples (ТЗ 7.12 «Проверяемая таблица проб»): assay tables first."""
    rows = conn.execute(
        sa.select(dh_tables.c.id, dh_tables.c.name, dh_tables.c.kind, dh_tables.c.row_count)
        .where(dh_tables.c.set_id == set_id, dh_tables.c.state == "ready", dh_tables.c.kind.in_(("assay", "interval")))
        .order_by(sa.case((dh_tables.c.kind == "assay", 0), else_=1), dh_tables.c.id)
    ).all()
    return [{"table": str(r.id), "name": r.name, "kind": r.kind, "rows": r.row_count} for r in rows]


def default_table(conn: sa.Connection, set_id: int) -> str | None:
    """«Пробы», else the first assay table, else the first table with numbers."""
    tabs = sample_tables(conn, set_id)
    named = next((t for t in tabs if dialect.name_key(t["name"]) == dialect.name_key("Пробы")), None)
    return (named or (tabs[0] if tabs else None) or {}).get("table")


def get_settings(conn: sa.Connection, set_id: int) -> dict[str, Any]:
    from .checks import AuditConfig

    raw = conn.execute(
        sa.select(settings.c.value).where(settings.c.scope == SCOPE, settings.c.key == str(set_id))
    ).scalar()
    raw = raw or {}
    cfg = AuditConfig.from_dict(raw.get("config") or {})
    table = raw.get("table") or default_table(conn, set_id)
    return {"config": cfg.as_dict(), "table": table}


def put_settings(conn: sa.Connection, set_id: int, config: dict[str, Any] | None, table: str | None) -> dict[str, Any]:
    from .checks import AuditConfig

    try:
        cfg = AuditConfig.from_dict(config or {})
    except ValueError as exc:
        raise ApiError(422, "bad_threshold", str(exc), "Исправьте порог: нужно положительное число.") from exc
    if table is not None and table not in {t["table"] for t in sample_tables(conn, set_id)}:
        raise ApiError(404, "table_not_found", "Таблица проб не найдена.", "Выберите таблицу из списка.")
    value = {"config": cfg.as_dict(), "table": table}
    key = str(set_id)
    if conn.execute(sa.select(settings.c.key).where(settings.c.scope == SCOPE, settings.c.key == key)).first() is None:
        conn.execute(sa.insert(settings).values(scope=SCOPE, key=key, value=value, version=1))
    else:
        conn.execute(
            sa.update(settings)
            .where(settings.c.scope == SCOPE, settings.c.key == key)
            .values(value=value, version=settings.c.version + 1)
        )
    return get_settings(conn, set_id)


def versions(conn: sa.Connection, set_id: int, table: str | None) -> dict[str, Any]:
    v = conn.execute(sa.select(objects.c.version).where(objects.c.id == set_id)).scalar()
    out: dict[str, Any] = {"set": v}
    if table and table.isdigit():
        r = conn.execute(sa.select(dh_tables.c.rev, dh_tables.c.created_at).where(dh_tables.c.id == int(table))).first()
        out["table"] = None if r is None else [int(r.rev), r.created_at.isoformat()]
    return out


# --- data ------------------------------------------------------------------------------------------


def load(conn: sa.Connection, set_id: int, table: str | None) -> Any:
    """The set as `AuditData`: holes (dense ids by dh_holes.id), collars, surveys in file order, the samples."""
    from bf_core.drillholes.snapshot import build_rows

    from .checks import AuditData

    holes = conn.execute(sa.select(dh_holes).where(dh_holes.c.set_id == set_id).order_by(dh_holes.c.id)).all()
    index = {int(h.id): i for i, h in enumerate(holes)}

    def num(name: str) -> np.ndarray:
        return np.array(
            [np.nan if getattr(h, name) is None or not h.has_collar else float(getattr(h, name)) for h in holes],
            dtype=float,
        )

    def status(h: Any) -> str | None:
        for k, v in (h.attrs or {}).items():
            if str(k).casefold() in STATUS_KEYS and v not in (None, ""):
                return str(v)
        return None

    sv = conn.execute(
        sa.select(dh_surveys.c.id, dh_surveys.c.hole_id, dh_surveys.c.depth, dh_surveys.c.azimuth, dh_surveys.c.dip)
        .where(dh_surveys.c.set_id == set_id)
        .order_by(dh_surveys.c.id)
    ).all()
    kwargs: dict[str, Any] = {
        "holes": np.array([h.name for h in holes], dtype=object),
        "has_collar": np.array([bool(h.has_collar) for h in holes], dtype=bool),
        "x": num("x"),
        "y": num("y"),
        "z": num("z"),
        "td": num("td"),
        "azimuth": num("azimuth"),
        "dip": num("dip"),
        "status": np.array([status(h) if h.has_collar else None for h in holes], dtype=object),
        "survey_row_id": np.array([r.id for r in sv], dtype=np.int64),
        "survey_hole": np.array([index[int(r.hole_id)] for r in sv], dtype=np.int64),
        "survey_depth": np.array([np.nan if r.depth is None else r.depth for r in sv], dtype=float),
        "survey_azimuth": np.array([np.nan if r.azimuth is None else r.azimuth for r in sv], dtype=float),
        "survey_dip": np.array([np.nan if r.dip is None else r.dip for r in sv], dtype=float),
    }
    if table is not None:
        snap = build_rows(conn, set_id, int(table), (), None)
        order = np.argsort(snap.ids, kind="stable")
        comps = [c for c in snap.columns if c.kind == "number" and c.role == "component"]
        if not comps:  # a table without marked components: every number column but the fixed ones
            comps = [c for c in snap.columns if c.kind == "number" and c.role != "fixed"]
        sample = next((c for c in snap.columns if c.role == "sample"), None)
        kwargs.update(
            assay_row_id=snap.ids[order],
            assay_hole=np.array([index.get(int(h), -1) for h in snap.hole_ids[order].tolist()], dtype=np.int64),
            assay_from=snap.data["from"][order],
            assay_to=snap.data["to"][order],
            assay_sample=None if sample is None else snap.data[sample.key][order],
            components={c.name: snap.data[c.key][order] for c in comps},
        )
    else:
        kwargs.update(
            assay_row_id=np.zeros(0, dtype=np.int64),
            assay_hole=np.zeros(0, dtype=np.int64),
            assay_from=np.zeros(0),
            assay_to=np.zeros(0),
            assay_sample=None,
            components={},
        )
    return AuditData(**kwargs)


# --- run and store -------------------------------------------------------------------------------


def run(
    engine: sa.Engine, set_id: int, *, table: str | None, config: dict[str, Any], job_id: int | None, tick: Any
) -> dict[str, Any]:
    """Runs the audit and replaces the stored result of the set; returns the totals."""
    from .checks import AuditConfig, run_checks

    with dialect.read_tx(engine) as conn:
        name = conn.execute(sa.select(objects.c.name).where(objects.c.id == set_id)).scalar()
        if name is None:
            raise ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")
        tname = "—"
        if table is not None:
            tname = conn.execute(sa.select(dh_tables.c.name).where(dh_tables.c.id == int(table))).scalar() or "—"
        ver = versions(conn, set_id, table)
        data = load(conn, set_id, table)
    result = run_checks(data, AuditConfig.from_dict(config), tick=tick)
    summary = [dict(r) for r in result.summary]
    totals = {"error": 0, "warning": 0, "info": 0}
    for r in summary:
        totals[r["severity"]] += int(r["rows"])
    with dialect.write_tx(engine) as conn:
        conn.execute(sa.delete(audit_runs).where(audit_runs.c.set_id == set_id))
        res = conn.execute(
            sa.insert(audit_runs).values(
                set_id=set_id,
                table_ref=table or "",
                table_name=tname,
                config=config,
                versions=ver,
                summary=summary,
                totals=totals,
                job_id=job_id,
                created_at=dialect.utcnow(),
            )
        )
        key = res.inserted_primary_key
        assert key is not None
        run_id = int(key[0])
        rows = [
            {
                "run_id": run_id,
                "code": f["code"],
                "severity": f["severity"],
                "source": f["table"],
                "hole": f["hole"],
                "count": int(f["count"]),
                "examples": f["examples"] or None,
            }
            for f in result.findings
        ]
        for start in range(0, len(rows), PORTION):
            conn.execute(sa.insert(audit_findings), rows[start : start + PORTION])
    return {"run_id": run_id, "totals": totals, "findings": len(rows)}


def latest(conn: sa.Connection, set_id: int) -> dict[str, Any] | None:
    r = (
        conn.execute(
            sa.select(audit_runs).where(audit_runs.c.set_id == set_id).order_by(audit_runs.c.id.desc()).limit(1)
        )
        .mappings()
        .first()
    )
    if r is None:
        return None
    stale = versions(conn, set_id, r["table_ref"] or None) != r["versions"]
    return {
        "run_id": r["id"],
        "table": r["table_ref"] or None,
        "table_name": r["table_name"],
        "config": r["config"],
        "summary": r["summary"],
        "totals": r["totals"],
        "stale": stale,
        "created_at": r["created_at"].isoformat(),
    }


def findings(
    conn: sa.Connection,
    run_id: int,
    *,
    severity: str | None = None,
    source: str | None = None,
    code: str | None = None,
    hole: str | None = None,
    offset: int = 0,
    limit: int = 200,
) -> dict[str, Any]:
    q = sa.select(audit_findings).where(audit_findings.c.run_id == run_id)
    if severity:
        q = q.where(audit_findings.c.severity == severity)
    if source:
        q = q.where(audit_findings.c.source == source)
    if code:
        q = q.where(audit_findings.c.code == code)
    if hole:
        q = q.where(audit_findings.c.hole == hole)
    total = conn.execute(sa.select(sa.func.count()).select_from(q.subquery())).scalar_one()
    sev = sa.case((audit_findings.c.severity == "error", 0), (audit_findings.c.severity == "warning", 1), else_=2)
    rows = (
        conn.execute(
            q.order_by(sev, audit_findings.c.code, audit_findings.c.hole, audit_findings.c.id)
            .offset(offset)
            .limit(limit)
        )
        .mappings()
        .all()
    )
    return {
        "total": int(total),
        "findings": [
            {
                "code": r["code"],
                "severity": r["severity"],
                "table": r["source"],
                "hole": r["hole"],
                "count": r["count"],
                "examples": r["examples"] or [],
            }
            for r in rows
        ],
    }
