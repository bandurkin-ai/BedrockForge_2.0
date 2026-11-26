"""`/api/v1/planned_holes` — проектные скважины (ТЗ 7.35 Д-34 ч. 2; ADR 0026 решения 28, 29; e19-api.md §10). Included
into `router.py` of the module with the paths as they are (`/planned_holes…`, not `/drillholes/planned_holes…`).

  * `GET /planned_holes[?trajectory=true]` → {planned_holes: [{id, name, version, meta[, trajectory]}]}.
  * `POST /planned_holes` {name?, collar [x, y, z], azimuth, dip, length, change {azimuth, dip} | target [x, y, z],
    method?, set_id?} → {id, name, version, meta, trajectory {depth, x, y, z}, target_depth}.
  * `GET /planned_holes/{id}` → the same; `PUT /planned_holes/{id}` {expected_version, name?, …keys of the body} → the
    same, version + 1; `DELETE /planned_holes/{id}` → {deleted}.
  * `POST /planned_holes/{id}/evaluate` {model_id | mesh_ids, codes?, set_id?, point?} → {intervals [{from, to, code}],
    codes, nearest [{hole, hole_id, distance, depth}], target {point, depth}, …}: the forecast by the bodies (when
    the request names them) and the nearest holes (≤ 5) of the set (`set_id`, else the set of the hole) at `point`
    (else the target, else the end of the hole). A part that has no source in the request is null.
  * `POST /planned_holes/{id}/compare` {set_id?, hole | hole_id} → {rows [{depth, dx, dy, dz, d}], max, max_depth,
    length, hole, hole_id}: «План / факт» every 1 m.
  * `POST /planned_holes/{id}/export` {format: csv | dxf, path, overwrite?, delimiter?, decimal?, encoding?} →
    {format, files, points, size}.
Writes publish `implicit.changed {kind: "planned_hole", id, deleted?}`; refused in a project «только просмотр» (423).
`evaluate`, `compare`, `export` write nothing in the project: they work there. Refusals: 404 `planned_hole_not_found`,
`set_not_found`, `model_not_found`, `wireframe_not_found`, `hole_not_found`; 409 `no_project`, `name_taken`,
`version_conflict {version}`, `model_building`, `files_exist`; 422 `bad_param {param}`, `target_at_collar`,
`target_beyond_length {need}`, `no_bodies`, `wireframe_open`, `hole_no_trajectory`, `bad_format`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter(prefix="/planned_holes")
Svc = Annotated[ProjectService, Depends(projects)]
Payload = Annotated[Any, Body()]
EVENT = "implicit.changed"


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие в окне.")
    return raw


def _publish(request: Request, ident: int, deleted: bool = False) -> None:
    data: dict[str, Any] = {"kind": "planned_hole", "id": ident}
    if deleted:
        data["deleted"] = True
    request.app.state.core.events.publish(EVENT, data)


def _author() -> str | None:
    from .edit import author

    return author()


@router.get("")
def list_holes(svc: Svc, trajectory: Annotated[bool, Query()] = False) -> dict[str, Any]:
    from . import planned_store as ps

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"planned_holes": ps.list_rows(conn, with_path=trajectory)}


@router.post("")
def create(svc: Svc, request: Request, body: Payload) -> dict[str, Any]:
    from . import planned as pl
    from . import planned_store as ps

    raw = _body(body)
    project = svc.require_current(writable=True)
    p = pl.parse(raw)
    with dialect.write_tx(project.engine) as conn:
        out = ps.create(conn, raw.get("name"), p, _author())
    _publish(request, out["id"])
    return out


@router.get("/{ident}")
def get(svc: Svc, ident: int) -> dict[str, Any]:
    from . import planned_store as ps

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return ps.get(conn, ident)


@router.put("/{ident}")
def update(svc: Svc, request: Request, ident: int, body: Payload) -> dict[str, Any]:
    from . import planned_store as ps

    raw = _body(body)
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        out = ps.update(conn, ident, raw)
    _publish(request, ident)
    return out


@router.delete("/{ident}")
def delete(svc: Svc, request: Request, ident: int) -> dict[str, Any]:
    from . import planned_store as ps

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        out = ps.delete(conn, ident)
    _publish(request, ident, deleted=True)
    return out


def _plan(project: Any, ident: int) -> tuple[Any, dict[str, Any]]:
    from . import planned as pl
    from . import planned_store as ps

    with dialect.read_tx(project.engine) as conn:
        row = ps.stored(conn, ident)
    return pl.parse(row.meta or {}), {"id": int(row.id), "name": str(row.name)}


def _set_of(raw: dict[str, Any], p: Any) -> int | None:
    given = raw.get("set_id", p.set_id)
    if given is None:
        return None
    if isinstance(given, bool) or not isinstance(given, int):
        from . import planned as pl

        raise pl.bad("set_id", "Набор скважин: нужен номер набора.", "Выберите набор из списка.")
    return given


@router.post("/{ident}/evaluate")
def evaluate(svc: Svc, ident: int, body: Payload) -> dict[str, Any]:
    from . import planned as pl
    from . import planned_eval as pe
    from . import planned_store as ps
    from .trajectory import trajectories

    raw = _body(body)
    project = svc.require_current()
    p, who = _plan(project, ident)
    set_id = _set_of(raw, p)
    named = raw.get("model_id") is not None or raw.get("mesh_ids") is not None
    if not named and set_id is None:
        raise pl.bad("model_id", "Нечего проверять: не выбраны модель, каркасы или набор скважин.",
                     "Выберите модель или каркасы для прогноза либо набор для ближайших скважин.")  # fmt: skip
    traj = pl.trajectory(p)
    if raw.get("point") is not None:
        point, depth = list(pl.point_of(raw["point"], "Точка", "point")), None
    elif p.target is not None:
        point, depth = list(p.target), p.target_depth
    else:
        point, depth = pl.positions(traj, pl.depth_grid(p.length)[-1:])[0].tolist(), p.length
    out: dict[str, Any] = {"id": who["id"], "intervals": None, "codes": None, "nearest": None,
                           "target": {"point": [float(v) for v in point], "depth": depth}}  # fmt: skip
    if named:
        out.update(pe.forecast(project, traj, p.length, raw))
    if set_id is not None:
        with dialect.read_tx(project.engine) as conn:
            ps.check_set(conn, set_id)
        out["nearest"] = pe.nearest_rows(trajectories(project, set_id), point)
        out["set_id"] = set_id
    return out


@router.post("/{ident}/compare")
def compare(svc: Svc, ident: int, body: Payload) -> dict[str, Any]:
    from . import planned as pl
    from . import planned_eval as pe
    from . import planned_store as ps
    from .trajectory import trajectories

    raw = _body(body)
    project = svc.require_current()
    p, _who = _plan(project, ident)
    set_id = _set_of(raw, p)
    if set_id is None:
        raise ApiError(404, "set_not_found", "Не выбран набор скважин.", "Выберите набор с фактической скважиной.")
    with dialect.read_tx(project.engine) as conn:
        ps.check_set(conn, set_id)
    st = trajectories(project, set_id)
    hole = pe.find_hole(st, raw)
    return {"id": ident, "set_id": set_id, **pe.compare(pl.trajectory(p), p.length, st, hole)}


@router.post("/{ident}/export")
def export(svc: Svc, ident: int, body: Payload) -> dict[str, Any]:
    from . import planned_export as px

    raw = _body(body)
    project = svc.require_current()
    p, who = _plan(project, ident)
    fmt, path = raw.get("format"), raw.get("path")
    return px.export(
        who["name"],
        p,
        str(fmt),
        str(path or ""),
        overwrite=bool(raw.get("overwrite")),
        delimiter=raw.get("delimiter"),
        decimal=raw.get("decimal"),
        encoding=raw.get("encoding"),
    )
