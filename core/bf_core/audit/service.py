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
from bf_core.db.schema import audit_findings, audit_runs, dh_holes, dh_tables, objects

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


def _resolve(conn: sa.Connection, set_id: int, name: str | None) -> str | None:
    """The chosen table by its name (stable across «Заменить» and `.bfggis`), else the default one."""
    tabs = sample_tables(conn, set_id)
    if name:
        hit = next((t for t in tabs if dialect.name_key(t["name"]) == dialect.name_key(name)), None)
        if hit is not None:
            return str(hit["table"])
    return default_table(conn, set_id)


def get_settings(conn: sa.Connection, set_id: int) -> dict[str, Any]:
    """Thresholds and the sample table of a set, kept in the set's own `meta` (renumbered with the set in
    `.bfggis`; the table by name, so a replaced or renumbered table is found again)."""
    from .checks import AuditConfig

    meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == set_id)).scalar() or {}
    raw = meta.get(SCOPE) or {}
    cfg = AuditConfig.from_dict(raw.get("config") or {})
    return {"config": cfg.as_dict(), "table": _resolve(conn, set_id, raw.get("table_name"))}


def put_settings(conn: sa.Connection, set_id: int, config: dict[str, Any] | None, table: str | None) -> dict[str, Any]:
    from .checks import AuditConfig

    try:
        cfg = AuditConfig.from_dict(config or {})
    except ValueError as exc:
        raise ApiError(422, "bad_threshold", str(exc), "Исправьте порог: нужно положительное число.") from exc
    tabs = {t["table"]: t["name"] for t in sample_tables(conn, set_id)}
    if table is not None and table not in tabs:
        raise ApiError(404, "table_not_found", "Таблица проб не найдена.", "Выберите таблицу из списка.")
    meta = dict(conn.execute(sa.select(objects.c.meta).where(objects.c.id == set_id)).scalar() or {})
    meta[SCOPE] = {"config": cfg.as_dict(), "table_name": tabs.get(table) if table else None}
    conn.execute(sa.update(objects).where(objects.c.id == set_id).values(meta=meta))  # not the version: data unchanged
    return get_settings(conn, set_id)


def versions(conn: sa.Connection, set_id: int, table: str | None) -> dict[str, Any]:
    v = conn.execute(sa.select(objects.c.version).where(objects.c.id == set_id)).scalar()
    out: dict[str, Any] = {"set": v}
    if table and table.isdigit():
        r = conn.execute(sa.select(dh_tables.c.rev, dh_tables.c.created_at).where(dh_tables.c.id == int(table))).first()
        out["table"] = None if r is None else [int(r.rev), r.created_at.isoformat()]
    return out


# --- data ------------------------------------------------------------------------------------------


def load(conn: sa.Connection, set_id: int, table: str | None, tick: Any = None) -> Any:
    """The set as `AuditData`: holes (dense ids by dh_holes.id), collars, surveys and samples in id order.
    Rows come through the driver in portions, samples with only the columns the rules need (ТЗ 7.12:
    50 000 holes / 5 million samples)."""
    from .checks import AuditData

    tick = tick or (lambda _f, _m=None: None)
    holes = conn.execute(sa.select(dh_holes).where(dh_holes.c.set_id == set_id).order_by(dh_holes.c.id)).all()
    ids = np.array([h.id for h in holes], dtype=np.int64)

    def dense(hole_ids: np.ndarray) -> np.ndarray:
        """dh_holes.id → index 0…n-1 (ids are ascending); -1 for a hole of another set (never expected)."""
        pos = np.searchsorted(ids, hole_ids)
        pos = np.clip(pos, 0, max(len(ids) - 1, 0))
        ok = (len(ids) > 0) & (ids[pos] == hole_ids) if len(ids) else np.zeros(len(hole_ids), dtype=bool)
        return np.where(ok, pos, -1).astype(np.int64)

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

    tick(0.02, "Устья и инклинометрия")
    sv: list[list[Any]] = [[], [], [], [], []]
    for chunk in dialect.iter_driver_rows(
        conn, "SELECT id, hole_id, depth, azimuth, dip FROM dh_surveys WHERE set_id = ? ORDER BY id", (set_id,)
    ):
        for j, col in enumerate(zip(*chunk, strict=True)):
            sv[j].extend(col)
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
        "survey_row_id": np.array(sv[0], dtype=np.int64),
        "survey_hole": dense(np.array(sv[1], dtype=np.int64)),
        "survey_depth": np.array(sv[2], dtype=float),  # None → NaN
        "survey_azimuth": np.array(sv[3], dtype=float),
        "survey_dip": np.array(sv[4], dtype=float),
    }
    del sv
    kwargs.update(_samples(conn, table, dense, tick) if table is not None else _no_samples())
    return AuditData(**kwargs)


def _no_samples() -> dict[str, Any]:
    return {
        "assay_row_id": np.zeros(0, dtype=np.int64),
        "assay_hole": np.zeros(0, dtype=np.int64),
        "assay_from": np.zeros(0),
        "assay_to": np.zeros(0),
        "assay_sample": None,
        "components": {},
    }


def _samples(conn: sa.Connection, table: str, dense: Any, tick: Any) -> dict[str, Any]:
    """Hole, from, to, sample number and the components of the checked table — nothing else is read."""
    from bf_core.drillholes.snapshot import _decode_vectors, table_columns

    kind = conn.execute(sa.select(dh_tables.c.kind, dh_tables.c.row_count).where(dh_tables.c.id == int(table))).first()
    if kind is None:
        raise ApiError(404, "table_not_found", "Проверяемая таблица проб не найдена.", "Выберите таблицу проб заново.")
    cols = table_columns(conn, int(table), kind.kind)
    comps = [c for c in cols if c.kind == "number" and c.role == "component"]
    if not comps:  # a table without marked components: every number column but the fixed ones
        comps = [c for c in cols if c.kind == "number" and c.role != "fixed"]
    sample = next((c for c in cols if c.role == "sample" and c.slot is not None), None)
    width = max([c.slot for c in comps if c.slot is not None], default=-1) + 1
    fields = ["id", "hole_id", "from_m", "to_m", "num"]
    if sample is not None:
        fields.append(dialect.json_field_sql(conn, "txt", str(sample.slot)))
    sql = f"SELECT {', '.join(fields)} FROM dh_rows WHERE table_id = ? ORDER BY id"
    total = max(int(kind.row_count or 0), 1)
    parts: dict[str, list[np.ndarray]] = {"id": [], "hole": [], "from": [], "to": [], "sample": []}
    values: dict[str, list[np.ndarray]] = {c.name: [] for c in comps}
    done = 0
    for chunk in dialect.iter_driver_rows(conn, sql, (int(table),)):
        got = list(zip(*chunk, strict=True))
        parts["id"].append(np.array(got[0], dtype=np.int64))
        parts["hole"].append(dense(np.array(got[1], dtype=np.int64)))
        parts["from"].append(np.array(got[2], dtype=float))
        parts["to"].append(np.array(got[3], dtype=float))
        if width:
            matrix = _decode_vectors(list(got[4]), width)
            for c in comps:
                if c.slot is not None:
                    values[c.name].append(np.ascontiguousarray(matrix[:, c.slot]))
        if sample is not None:
            parts["sample"].append(np.array(got[5], dtype=object))
        done += len(chunk)
        tick(0.05 + 0.25 * min(done / total, 1.0), f"Чтение проб: {done:,}".replace(",", " "))
        del chunk, got

    def cat(arrs: list[np.ndarray], dtype: Any) -> np.ndarray:
        return np.concatenate(arrs) if arrs else np.zeros(0, dtype=dtype)

    n = sum(len(a) for a in parts["id"])
    return {
        "assay_row_id": cat(parts["id"], np.int64),
        "assay_hole": cat(parts["hole"], np.int64),
        "assay_from": cat(parts["from"], float),
        "assay_to": cat(parts["to"], float),
        "assay_sample": cat(parts["sample"], object) if sample is not None else None,
        "components": {k: (cat(v, float) if v else np.full(n, np.nan)) for k, v in values.items()},
    }


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
        if (
            table is not None
            and conn.execute(sa.select(dh_tables.c.id).where(dh_tables.c.id == int(table))).first() is None
        ):
            raise ApiError(
                404, "table_not_found", "Проверяемая таблица проб не найдена.", "Выберите таблицу проб заново."
            )
        ver = versions(conn, set_id, table)
        data = load(conn, set_id, table, tick)

    def checks_tick(f: float | None, m: str | None = None) -> None:
        tick(None if f is None else 0.3 + 0.6 * f, m)

    result = run_checks(data, AuditConfig.from_dict(config), tick=checks_tick)
    del data
    summary = [dict(r) for r in result.summary]
    totals = {"error": 0, "warning": 0, "info": 0}
    for r in summary:
        totals[r["severity"]] += int(r["rows"])
    with dialect.write_tx(engine) as conn:
        conn.execute(sa.delete(audit_runs).where(audit_runs.c.set_id == set_id))
        res = conn.execute(
            sa.insert(audit_runs).values(
                set_id=set_id,
                table_id=int(table) if table else None,
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
            tick(0.9 + 0.1 * start / max(len(rows), 1), f"Запись находок: {start:,}".replace(",", " "))
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
    table = None if r["table_id"] is None else str(r["table_id"])
    stale = versions(conn, set_id, table) != r["versions"]
    return {
        "run_id": r["id"],
        "table": table,
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
    hole_prefix: str | None = None,
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
    if hole_prefix:
        esc = hole_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        q = q.where(audit_findings.c.hole.like(f"{esc}%", escape="\\"))
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
