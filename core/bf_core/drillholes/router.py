"""/api/v1/drillholes/sets/… — sets, their tables and the «Таблица» panel (ТЗ 7.10)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_holes, dh_surveys, dh_tables, objects
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

if TYPE_CHECKING:
    from .expr import ExprError
    from .snapshot import Snapshot

# numpy and the drillhole modules load on first use, not at the start of the core (tests/test_process.py:
# heavy libraries are not imported before BF-READY).

router = APIRouter(prefix="/drillholes")
Svc = Annotated[ProjectService, Depends(projects)]
TABLE_TITLES = {"collar": "Устья", "survey": "Инклинометрия"}


def _snapshot(project: Any, set_id: int, table: str) -> Snapshot:
    from . import actions
    from .snapshot import build_collars, build_rows, build_surveys, snapshots

    key = actions.current(project, set_id, table)

    def build() -> Snapshot:
        with dialect.read_tx(project.engine) as conn:
            if table == "collar":
                return build_collars(conn, set_id, key)
            if table == "survey":
                return build_surveys(conn, set_id, key)
            return build_rows(conn, set_id, int(table), key, project.folder / "cache" / "tables")

    return snapshots.get(key, build)


def _changed(request: Request, set_id: int, table: str, rev: int) -> None:
    """3D, charts and the audit learn that the data of a set changed (ТЗ 7.10 «После правки»)."""
    request.app.state.core.events.publish("drillholes.changed", {"set_id": set_id, "table": table, "rev": rev})


def _expr_error(exc: ExprError) -> ApiError:
    return ApiError(422, "bad_expression", str(exc), 'Пример: AU > 0,5 и [Литология] = "гранит".')


@router.get("/sets/{set_id}/tables")
def list_tables(svc: Svc, set_id: int) -> dict[str, Any]:
    """Tabs of a set: collars, surveys, then the interval tables (assay first)."""
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        s = conn.execute(
            sa.select(objects.c.id, objects.c.name, objects.c.version, objects.c.crs).where(
                objects.c.id == set_id, objects.c.kind == "drillhole_set", objects.c.state == "ready"
            )
        ).first()
        if s is None:
            raise ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")
        collars = conn.execute(
            sa.select(sa.func.count())
            .select_from(dh_holes)
            .where(dh_holes.c.set_id == set_id, dh_holes.c.has_collar.is_(True))
        ).scalar_one()
        surveys = conn.execute(
            sa.select(sa.func.count()).select_from(dh_surveys).where(dh_surveys.c.set_id == set_id)
        ).scalar_one()
        holes = conn.execute(
            sa.select(sa.func.count()).select_from(dh_holes).where(dh_holes.c.set_id == set_id)
        ).scalar_one()
        tabs: list[dict[str, Any]] = [
            {"table": "collar", "kind": "collar", "name": "Устья", "rows": collars, "rev": s.version},
            {"table": "survey", "kind": "survey", "name": "Инклинометрия", "rows": surveys, "rev": s.version},
        ]
        order = sa.case((dh_tables.c.kind == "assay", 0), (dh_tables.c.kind == "interval", 1), else_=2)
        for t in conn.execute(
            sa.select(dh_tables)
            .where(dh_tables.c.set_id == set_id, dh_tables.c.state == "ready")
            .order_by(order, dh_tables.c.id)
        ):
            tabs.append({"table": str(t.id), "kind": t.kind, "name": t.name, "rows": t.row_count, "rev": t.rev})
    return {"set": {"id": s.id, "name": s.name, "crs": s.crs, "holes": holes}, "tables": tabs}


@router.get("/sets/{set_id}/tables/{table}")
def table_page(
    svc: Svc,
    set_id: int,
    table: str,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    sort: str | None = None,
    desc: bool = False,
    hole: int | None = None,
    where: Annotated[str | None, Query(max_length=2000)] = None,
    coords: bool = False,
) -> dict[str, Any]:
    """A page of rows (ТЗ 7.10 «Просмотр», «Сортировка», «Выбор скважины», 7.34 п. 21 «Фильтр по выражению»)."""
    from .expr import ExprError
    from .query import page

    project = svc.require_current()
    snap = _snapshot(project, set_id, table)
    sort = sort or None
    if sort is not None and sort not in {c.key for c in snap.columns}:
        raise ApiError(422, "column_not_found", "Нет такой колонки для сортировки.", "Обновите таблицу.")
    try:
        out = page(snap, offset=offset, limit=limit, sort=sort, desc=desc, hole=hole, where=where)
    except ExprError as exc:
        raise _expr_error(exc) from exc
    out["columns"] = [c.as_dict() for c in snap.columns]
    out["rev"] = snap.key[-1]
    out["kind"] = snap.kind
    if coords and snap.kind in ("assay", "interval", "event") and out["rows"]:
        _add_coords(project, set_id, out["rows"], snap.kind)
    return out


def _add_coords(project: Any, set_id: int, rows: list[dict[str, Any]], kind: str) -> None:
    """3D coordinates of interval ends by the trajectory (ТЗ 7.11): x/y/z of «от» and «до» (events: one point)."""
    import numpy as np

    from .trajectory import interval_xyz, trajectories

    st = trajectories(project, set_id)
    holes = np.array([r["hole_id"] for r in rows], dtype=np.int64)
    f = np.array([np.nan if r.get("from") is None else r["from"] for r in rows], dtype=float)
    t = np.array([np.nan if r.get("to") is None else r["to"] for r in rows], dtype=float) if kind != "event" else f
    a, b = interval_xyz(st, holes, f, t)
    for r, p, q in zip(rows, a.tolist(), b.tolist(), strict=True):
        r["xyz_from"] = None if p[0] != p[0] else p
        r["xyz_to"] = None if q[0] != q[0] else q


@router.get("/sets/{set_id}/tables/{table}/stats")
def field_stats(
    svc: Svc,
    set_id: int,
    table: str,
    column: str,
    hole: int | None = None,
    where: Annotated[str | None, Query(max_length=2000)] = None,
) -> dict[str, Any]:
    """Σ: filled, empty, min, max, mean, median, standard deviation; for text — unique and frequent values."""
    from .expr import ExprError
    from .query import stats

    project = svc.require_current()
    snap = _snapshot(project, set_id, table)
    try:
        snap.column(column)
    except KeyError:
        raise ApiError(404, "column_not_found", "Колонка не найдена.", "Обновите таблицу.") from None
    try:
        return stats(snap, column, hole=hole, where=where)
    except ExprError as exc:
        raise _expr_error(exc) from exc


class CellBody(BaseModel):
    column: str = Field(max_length=300)
    value: Any = None
    rev: int | None = None


class RowBody(BaseModel):
    values: dict[str, Any]
    rev: int | None = None


@router.patch("/sets/{set_id}/tables/{table}/rows/{row_id}")
def edit_cell(svc: Svc, request: Request, set_id: int, table: str, row_id: int, body: CellBody) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    from . import actions

    out = actions.update_cell(project, set_id, table, row_id, body.column, body.value, body.rev)
    if out.get("changed"):
        _changed(request, set_id, table, out["rev"])
    return out


@router.post("/sets/{set_id}/tables/{table}/rows")
def add_row(svc: Svc, request: Request, set_id: int, table: str, body: RowBody) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    from . import actions

    out = actions.insert_row(project, set_id, table, body.values, body.rev)
    _changed(request, set_id, table, out["rev"])
    return out


@router.delete("/sets/{set_id}/tables/{table}/rows/{row_id}")
def remove_row(
    svc: Svc, request: Request, set_id: int, table: str, row_id: int, rev: int | None = None
) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    from . import actions

    out = actions.delete_row(project, set_id, table, row_id, rev)
    _changed(request, set_id, table, out["rev"])
    return out


@router.get("/sets/{set_id}/edits")
def edits(svc: Svc, set_id: int, table: str | None = None, limit: int = 200) -> dict[str, Any]:
    project = svc.require_current()
    from . import actions

    return {"edits": actions.history(project, set_id, table=table, limit=limit)}


@router.post("/edits/{edit_id}/undo")
def undo_edit(svc: Svc, request: Request, edit_id: int) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    from . import actions

    out = actions.undo(project, edit_id)
    _changed(request, out["set_id"], out["table"], out["rev"])
    return out


# --- sets (ТЗ 7.10: «+» у «Скважин», set.create) -------------------------------------------------------


class SetBody(BaseModel):
    name: str = Field(max_length=200)


@router.post("/sets")
def create_set(svc: Svc, request: Request, body: SetBody) -> dict[str, Any]:
    """An empty set: collars, surveys and tables are added by import or by hand."""
    from .store import NameTaken
    from .store import create_set as make

    project = svc.require_current(writable=True)
    name = " ".join(body.name.split())
    if not name:
        raise ApiError(422, "name_empty", "Название набора не может быть пустым.", "Введите название набора.")
    with dialect.write_tx(project.engine) as conn:
        try:
            set_id = make(conn, name)
        except NameTaken:
            raise ApiError(409, "name_taken", f"Набор «{name}» уже есть.", "Введите другое название.") from None
    request.app.state.core.events.publish("objects.changed", {"kind": "drillhole_set", "id": set_id})
    return {"id": set_id, "name": name}


@router.patch("/sets/{set_id}")
def rename_set(svc: Svc, request: Request, set_id: int, body: SetBody) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    name = " ".join(body.name.split())
    if not name:
        raise ApiError(422, "name_empty", "Название набора не может быть пустым.", "Введите название набора.")
    with dialect.write_tx(project.engine) as conn:
        taken = conn.execute(
            sa.select(objects.c.id).where(
                objects.c.kind == "drillhole_set",
                objects.c.name_key == dialect.name_key(name),
                objects.c.id != set_id,
                objects.c.state.in_(("ready", "building")),
            )
        ).first()
        if taken is not None:
            raise ApiError(409, "name_taken", f"Набор «{name}» уже есть.", "Введите другое название.")
        res = conn.execute(
            sa.update(objects)
            .where(objects.c.id == set_id, objects.c.kind == "drillhole_set", objects.c.state == "ready")
            .values(name=name, name_key=dialect.name_key(name), updated_at=dialect.utcnow())
        )
        if res.rowcount != 1:
            raise ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")
    request.app.state.core.events.publish("objects.changed", {"kind": "drillhole_set", "id": set_id})
    return {"id": set_id, "name": name}


@router.delete("/sets/{set_id}")
def delete_set(svc: Svc, request: Request, set_id: int) -> dict[str, Any]:
    """Deletes a set with its holes, tables and import history; its source files leave the storage
    (the storage cleanup removes files nobody owns)."""
    from .store import drop_import_owners

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        row = conn.execute(
            sa.select(objects.c.state).where(objects.c.id == set_id, objects.c.kind == "drillhole_set")
        ).first()
        if row is None:
            raise ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")
        if row.state != "ready":
            raise ApiError(409, "set_busy", "В набор сейчас идёт импорт.", "Дождитесь окончания или отмените задание.")
        if conn.execute(
            sa.select(dh_tables.c.id).where(dh_tables.c.set_id == set_id, dh_tables.c.state == "building")
        ).first():
            raise ApiError(409, "set_busy", "В набор сейчас идёт импорт.", "Дождитесь окончания или отмените задание.")
        table_ids = list(conn.execute(sa.select(dh_tables.c.id).where(dh_tables.c.set_id == set_id)).scalars())
        drop_import_owners(conn, [set_id])
        from bf_core.db.schema import blob_owners

        conn.execute(
            sa.delete(blob_owners).where(blob_owners.c.owner_kind == "object", blob_owners.c.owner_id == set_id)
        )
        conn.execute(sa.delete(objects).where(objects.c.id == set_id))
    from .snapshot import drop_cache_files, snapshots

    snapshots.forget(set_id)
    drop_cache_files(project.folder / "cache" / "tables", table_ids)
    request.app.state.core.events.publish("objects.changed", {"kind": "drillhole_set", "id": set_id, "deleted": True})
    return {"deleted": set_id}


# --- trajectories and the hole card (ТЗ 7.10 «Карточка скважины», 7.11) ----------------------------------


@router.get("/sets/{set_id}/trajectories")
def set_trajectories(
    svc: Svc,
    set_id: int,
    method: str = "minimum_curvature",
    holes: Annotated[list[int] | None, Query(max_length=1000)] = None,
) -> dict[str, Any]:
    """Stations (depth, X, Y, Z) of each hole; holes without collar coordinates are listed in `skipped`."""
    from .desurvey import METHOD_TITLES, METHODS, SOURCE_NAMES
    from .trajectory import trajectories

    if method not in METHODS:
        raise ApiError(
            422, "bad_method", "Неизвестный метод траекторий.", "Выберите: " + ", ".join(METHOD_TITLES.values()) + "."
        )
    project = svc.require_current()
    st = trajectories(project, set_id, method)
    wanted = None if not holes else {st.index[h] for h in holes if h in st.index}
    out = []
    skipped = set(st.traj.skipped.tolist()) if hasattr(st.traj.skipped, "tolist") else set(st.traj.skipped)
    for i, hid in enumerate(st.hole_ids.tolist()):
        if (wanted is not None and i not in wanted) or i in skipped:
            continue
        md, xyz, _dirs = st.traj.stations(i)
        out.append(
            {
                "id": hid,
                "name": st.names[i],
                "source": SOURCE_NAMES[int(st.traj.source[i])],
                "stations": [[float(d), *map(float, p)] for d, p in zip(md.tolist(), xyz.tolist(), strict=True)],
            }
        )
    return {"method": method, "method_title": METHOD_TITLES[method], "holes": out, "skipped": st.skipped}


HOLE_TYPES = {"hole": "Скважина", "trench": "Канава", "design": "Проектная скважина"}


def hole_type(attrs: dict[str, Any] | None) -> dict[str, str]:
    """Канавы и траншеи, проектные скважины (ТЗ 7.34 п. 102, 103) — by the collar attributes «Тип» / «Статус»
    as they come from the files (TYPE, HOLE_TYPE, STATUS…). A trench is described by its survey like a hole."""
    text = " ".join(
        str(v).casefold()
        for k, v in (attrs or {}).items()
        if str(k).casefold() in ("type", "hole_type", "тип", "status", "статус")
    )
    kind = (
        "trench"
        if any(w in text for w in ("канав", "тран", "trench"))
        else "design"
        if any(w in text for w in ("проект", "design", "plan"))
        else "hole"
    )
    return {"key": kind, "title": HOLE_TYPES[kind]}


@router.get("/sets/{set_id}/holes/{hole_id}")
def hole_card(svc: Svc, set_id: int, hole_id: int) -> dict[str, Any]:
    """«Карточка скважины» in «Свойства»: the collar, rows per table, the end of the trajectory (audit — Э5)."""
    from .trajectory import trajectories

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        h = conn.execute(sa.select(dh_holes).where(dh_holes.c.id == hole_id, dh_holes.c.set_id == set_id)).first()
        if h is None:
            raise ApiError(404, "hole_not_found", "Скважина не найдена.", "Обновите таблицу.")
        from bf_core.db.schema import dh_rows

        counts = {
            str(r.id): {"name": r.name, "kind": r.kind, "rows": r.n}
            for r in conn.execute(
                sa.select(dh_tables.c.id, dh_tables.c.name, dh_tables.c.kind, sa.func.count(dh_rows.c.id).label("n"))
                .join(dh_rows, dh_rows.c.table_id == dh_tables.c.id)
                .where(dh_tables.c.set_id == set_id, dh_tables.c.state == "ready", dh_rows.c.hole_id == hole_id)
                .group_by(dh_tables.c.id, dh_tables.c.name, dh_tables.c.kind)
            )
        }
        surveys = conn.execute(
            sa.select(sa.func.count()).select_from(dh_surveys).where(dh_surveys.c.hole_id == hole_id)
        ).scalar_one()
    st = trajectories(project, set_id)
    i = st.index.get(hole_id)
    end = None
    length = None
    if i is not None and i not in set(st.traj.skipped.tolist()):
        md, xyz, _d = st.traj.stations(i)
        if len(md):
            length, end = float(md[-1]), [float(v) for v in xyz[-1]]
    return {
        "id": h.id,
        "name": h.name,
        "type": hole_type(h.attrs),
        "collar": None
        if not h.has_collar
        else {"x": h.x, "y": h.y, "z": h.z, "td": h.td, "azimuth": h.azimuth, "dip": h.dip, **(h.attrs or {})},
        "surveys": surveys,
        "tables": counts,
        "trajectory": {"length": length, "end": end, "skipped": end is None},
        "audit": None,
    }


from .routes_tools import router as _tools  # noqa: E402 - the tools routes share the prefix

router.include_router(_tools)
