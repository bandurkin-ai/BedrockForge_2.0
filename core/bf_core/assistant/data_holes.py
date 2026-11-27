"""`data_holes` and `data_hole`: the list and the card of drillholes (подшаг 23.7; e23-api.md §5.2; ТЗ 7.10).

Reads `dh_holes`, `dh_surveys`, `dh_rows` of the open project and the last audit of the set (`audit.service`).
A hole the audit did not look at has `findings: null`, never 0 (the audit was not run is not «no findings»).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import audit_findings, dh_holes, dh_rows, dh_surveys, dh_tables

from .data_tools import (
    DataCall,
    DataError,
    clean,
    limit_of,
    next_offset,
    offset_of,
    register,
    require_set,
)

SEVERITY = {"error": "E", "warning": "W", "info": "I"}
FINDINGS_MAX = 20
ATTRS_MAX = 12
TYPE_KEYS = ("type", "hole_type", "тип", "тип скважины")
SUGGEST = 5
_KIND_ORDER = sa.case((dh_tables.c.kind == "assay", 0), (dh_tables.c.kind == "interval", 1), else_=2)

_SET_ID = {"type": "integer", "description": "Номер набора скважин; по умолчанию — активный набор."}
_OFFSET = {"type": "integer", "description": "Смещение для следующей страницы (next_offset предыдущего ответа)."}
_LIMIT = {"type": "integer", "description": "Размер страницы, не больше 200."}

HOLES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "set_id": _SET_ID,
        "search": {"type": "string", "description": "Часть имени скважины."},
        "with_findings": {"type": "boolean", "description": "Только скважины с находками аудита."},
        "offset": _OFFSET,
        "limit": _LIMIT,
    },
    "required": [],
    "additionalProperties": False,
}
HOLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "hole_id": {"type": "string", "description": "Имя скважины."},
        "set_id": _SET_ID,
    },
    "required": ["hole_id"],
    "additionalProperties": False,
}


STALE = "Результат аудита устарел: данные набора изменились после запуска. Запустите аудит заново (audit.run)."


def audit_run(conn: sa.Connection, set_id: int) -> tuple[int, bool] | None:
    """(the id of the last audit run of a set, whether it is stale); None - the audit was never run."""
    from bf_core.audit.service import latest

    last = latest(conn, set_id)
    return None if last is None else (int(last["run_id"]), bool(last["stale"]))


def _flagged(call: DataCall, conn: sa.Connection, run_id: int) -> dict[str, int]:
    """Findings per hole name in an audit run: the sum of the counts of its groups."""
    q = (
        sa.select(audit_findings.c.hole, sa.func.sum(audit_findings.c.count))
        .where(audit_findings.c.run_id == run_id, audit_findings.c.hole.is_not(None))
        .group_by(audit_findings.c.hole)
    )
    out: dict[str, int] = {}
    for h, n in conn.execute(q):
        call.check()
        out[str(h)] = int(n)
    return out


def _hole_ids(call: DataCall, conn: sa.Connection, set_id: int, only: set[str] | None, search: str | None) -> list[int]:
    """Ids of the holes of a set (in the order of import) among the names `only` that hold the part of a name."""
    needle = dialect.name_key(search) if search else ""
    rows = conn.execute(
        sa.select(dh_holes.c.id, dh_holes.c.name).where(dh_holes.c.set_id == set_id).order_by(dh_holes.c.id)
    ).all()
    ids: list[int] = []
    for r in rows:
        call.check()  # a project of 50 000 holes: this loop is the long part
        if (only is None or r.name in only) and (not needle or needle in dialect.name_key(r.name)):
            ids.append(int(r.id))
    return ids


def _row(h: Any, flagged: dict[str, int] | None) -> dict[str, Any]:
    return {
        "hole_id": h.name,
        "x": h.x,
        "y": h.y,
        "z": h.z,
        "depth": h.td,
        "azimuth": h.azimuth,
        "dip": h.dip,
        "findings": None if flagged is None else flagged.get(h.name, 0),
    }


def holes(call: DataCall, args: dict[str, Any]) -> dict[str, Any]:
    set_id = call.set_of(args)
    offset, limit = offset_of(args), limit_of(args)
    search = (args.get("search") or "").strip() or None
    only_flagged = bool(args.get("with_findings"))
    cols = [dh_holes.c.id, dh_holes.c.name, dh_holes.c.x, dh_holes.c.y, dh_holes.c.z, dh_holes.c.td]
    cols += [dh_holes.c.azimuth, dh_holes.c.dip]
    with dialect.read_tx(call.project.engine) as conn:
        require_set(conn, set_id)
        run = audit_run(conn, set_id)
        flagged = None if run is None else _flagged(call, conn, run[0])
        if only_flagged and flagged is None:
            raise DataError("Аудит набора не запускался: находок ещё нет. Запустите аудит в окне «Аудит данных».")
        if search is None and not only_flagged:  # the plain page: the database cuts it
            count = sa.select(sa.func.count()).select_from(dh_holes).where(dh_holes.c.set_id == set_id)
            total = int(conn.execute(count).scalar_one())
            q = sa.select(*cols).where(dh_holes.c.set_id == set_id).order_by(dh_holes.c.id)
            page = conn.execute(q.offset(offset).limit(limit)).all()
            call.check()
        else:
            ids = _hole_ids(call, conn, set_id, set(flagged or ()) if only_flagged else None, search)
            total = len(ids)
            chosen = ids[offset : offset + limit]
            page = (
                conn.execute(sa.select(*cols).where(dh_holes.c.id.in_(chosen)).order_by(dh_holes.c.id)).all()
                if chosen
                else []
            )
    call.summary = f"Скважин: {total}" + (f", показано {len(page)}" if len(page) < total else "")
    out: dict[str, Any] = {
        "set_id": set_id,
        "total": total,
        "rows": [_row(h, flagged) for h in page],
        "next_offset": next_offset(offset, len(page), total),
    }
    if run is not None and run[1]:  # the counts of findings belong to data that changed since (as in data_audit)
        out["stale"], out["note"] = True, STALE
    return out


# --- the card ----------------------------------


def _find_hole(conn: sa.Connection, set_id: int, name: str) -> Any:
    cols = [dh_holes.c.id, dh_holes.c.name, dh_holes.c.x, dh_holes.c.y, dh_holes.c.z, dh_holes.c.td, dh_holes.c.attrs]
    row = conn.execute(sa.select(*cols).where(dh_holes.c.set_id == set_id, dh_holes.c.name == name)).first()
    if row is not None:
        return row
    # the name as the model saw it (cleaned) or typed in another case
    want = {dialect.name_key(name), dialect.name_key(clean(name))}
    q = sa.select(dh_holes.c.id, dh_holes.c.name).where(dh_holes.c.set_id == set_id).order_by(dh_holes.c.id)
    rows = conn.execute(q).all()
    for r in rows:
        if dialect.name_key(r.name) in want or dialect.name_key(clean(r.name)) in want:
            return conn.execute(sa.select(*cols).where(dh_holes.c.id == r.id)).one()
    stem = dialect.name_key(clean(name))[:3]
    near = [clean(r.name, 60) for r in rows if stem and stem in dialect.name_key(r.name)][:SUGGEST]
    hint = f" Похожие: {', '.join(near)}." if near else " Список скважин — data_holes."
    raise DataError(f"Скважина «{clean(name, 60)}» не найдена в наборе.{hint}")


def _type_of(attrs: Any) -> str | None:
    if not isinstance(attrs, dict):
        return None
    lowered = {str(k).casefold(): v for k, v in attrs.items()}
    for key in TYPE_KEYS:
        if lowered.get(key) not in (None, ""):
            return str(lowered[key])
    return None


def _survey(conn: sa.Connection, hole_pk: int) -> dict[str, Any]:
    s = dh_surveys
    n = int(conn.execute(sa.select(sa.func.count()).select_from(s).where(s.c.hole_id == hole_pk)).scalar_one())
    out: dict[str, Any] = {"n": n, "first": None, "last": None}
    if n:
        q = sa.select(s.c.depth, s.c.azimuth, s.c.dip).where(s.c.hole_id == hole_pk)
        first = conn.execute(q.order_by(s.c.depth).limit(1)).one()
        last = conn.execute(q.order_by(s.c.depth.desc()).limit(1)).one()
        out["first"] = {"depth": first.depth, "azimuth": first.azimuth, "dip": first.dip}
        out["last"] = {"depth": last.depth, "azimuth": last.azimuth, "dip": last.dip}
    return out


def _tables(conn: sa.Connection, set_id: int, hole_pk: int) -> list[dict[str, Any]]:
    tabs = conn.execute(
        sa.select(dh_tables.c.id, dh_tables.c.name, dh_tables.c.kind)
        .where(dh_tables.c.set_id == set_id, dh_tables.c.state == "ready")
        .order_by(_KIND_ORDER, dh_tables.c.id)
    ).all()
    r = dh_rows
    agg = {
        int(a.table_id): a
        for a in conn.execute(
            sa.select(
                r.c.table_id,
                sa.func.count().label("n"),
                sa.func.min(r.c.from_m).label("lo"),
                sa.func.max(r.c.to_m).label("hi"),
            )
            .where(r.c.hole_id == hole_pk)
            .group_by(r.c.table_id)
        )  # fmt: skip
    }
    out = []
    for t in tabs:
        a = agg.get(int(t.id))
        out.append({"table": t.name, "kind": t.kind, "intervals": 0 if a is None else int(a.n),
                    "from": None if a is None else a.lo, "to": None if a is None else a.hi})  # fmt: skip
    return out


def _findings(conn: sa.Connection, run_id: int | None, name: str) -> list[dict[str, Any]] | None:
    if run_id is None:
        return None
    from bf_core.audit.rules import RULES

    rows = conn.execute(
        sa.select(audit_findings.c.code, audit_findings.c.severity, audit_findings.c.count.label("n"))
        .where(audit_findings.c.run_id == run_id, audit_findings.c.hole == name)
        .order_by(
            sa.case((audit_findings.c.severity == "error", 0), (audit_findings.c.severity == "warning", 1), else_=2),
            audit_findings.c.code,
        )
        .limit(FINDINGS_MAX)
    ).all()
    out = []
    for f in rows:
        rule = RULES.get(f.code)
        title = rule.title if rule else f.code
        out.append({"rule": f.code, "severity": SEVERITY.get(f.severity, f.severity),
                    "message": title if f.n <= 1 else f"{title} (×{f.n})"})  # fmt: skip
    return out


def hole(call: DataCall, args: dict[str, Any]) -> dict[str, Any]:
    set_id = call.set_of(args)
    with dialect.read_tx(call.project.engine) as conn:
        require_set(conn, set_id)
        h = _find_hole(conn, set_id, str(args["hole_id"]))
        run = audit_run(conn, set_id)
        run_id = None if run is None else run[0]
        attrs = h.attrs if isinstance(h.attrs, dict) else {}
        out = {
            "hole_id": h.name,
            "set_id": set_id,
            "collar": {"x": h.x, "y": h.y, "z": h.z},
            "depth": h.td,
            "type": _type_of(h.attrs),
            "attrs": dict(list(attrs.items())[:ATTRS_MAX]),
            "survey": _survey(conn, int(h.id)),
            "tables": _tables(conn, set_id, int(h.id)),
            "findings": _findings(conn, run_id, h.name),
        }
        if run is not None and run[1]:
            out["stale"], out["note"] = True, STALE
    call.summary = f"Скважина {clean(h.name, 60)}"
    return out


register(
    "data_holes",
    HOLES_SCHEMA,
    holes,
    title="Список скважин",
    description=(
        "Список скважин набора постранично: имя, координаты устья, глубина, азимут, угол и число находок аудита "
        "(null — аудит не запускался). Можно искать по части имени и брать только скважины с находками. "
        "Большой список читайте страницами: next_offset из ответа подставьте в offset."
    ),
)
register(
    "data_hole",
    HOLE_SCHEMA,
    hole,
    title="Карточка скважины",
    description=(
        "Карточка одной скважины: устье, глубина, тип, инклинометрия (число замеров, первый и последний), "
        "таблицы набора с числом интервалов и диапазоном глубин, находки аудита (не более 20)."
    ),
)
