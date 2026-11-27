"""What the store checks and records of an implicit model against the project (подшаг 19.11; ADR 0026 решения 22,
25; docs/progress/e19-api.md §2.3, §3, §5): the wireframes and the DTM of the boundary, the expansions and the clip,
the trend, the table of the codes, the shells' sources, the layers of the manual data, the extent of the boundary,
the inputs of the graph (`meta.inputs`) and the current state of a stored model (`freshness`).

    prep = prepare(conn, spec)                       # Prepared(spec, extent, inputs): 404 / 422 as e19-api §3 names
    inputs_of(conn, spec) -> [{kind, id, fp, name}]  # the form of `meta.inputs`, the order of the table §2.3
    freshness(conn, meta) -> {stale, stale_reason, source_lost}

`fp` of an input (e19-api §2.3): a table — `<token>:<rev>`; a set — `traj:` and the key of its trajectories; the
composites of a shell — the SHA-256 of the result; a layer of the manual data, a trend — `version`; a wireframe and
a DTM (both are `wireframe` of the graph: a DTM is a wireframe with the settings) — the key of the geometry
(`viz.meshes.mesh_state`). Nothing heavy before BF-READY: the readers are imported in the functions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import dh_results, objects, wireframes
from bf_core.geostat.estimate import store_check as esc

from . import store_base as sb
from .spec import Group, ModelSpec, refuse
from .spec_parts import POINTS_MAX

LOST = "источник удалён"
SET, TABLE, RESULT = "dh_set", "dh_table", "dh_result"
LAYER, TREND, WIREFRAME = "object:line_layer", "object:trend", "wireframe"
LAYER_KIND = "line_layer"


def _api(status: int, code: str, message: str, hint: str, param: str | None = None, **extra: Any) -> ApiError:
    return ApiError(status, code, message, hint, {**({"param": param} if param else {}), **extra})


# --- the wireframes, the DTM, the trend, the layers -----------------------------------------------------------------


def _wireframe(conn: sa.Connection, wf_id: int, param: str, *, dtm: bool = False, closed: bool = False) -> Any:
    """The row of a ready wireframe (`name`, `extent`, `stats`, `dtm`); 404 `wireframe_not_found` /
    `dtm_not_found`, 422 `wireframe_open` for a boundary, expansion or clip that is not a closed solid."""
    r = conn.execute(sa.select(objects.c.name, objects.c.extent, wireframes.c.stats, wireframes.c.dtm).join(
        wireframes, wireframes.c.object_id == objects.c.id).where(
        objects.c.id == wf_id, objects.c.kind == "wireframe", objects.c.state == "ready")).first()  # fmt: skip
    if r is None or (dtm and r.dtm is None):
        if dtm:
            raise _api(404, "dtm_not_found", "ЦТМ не найдена.", "Выберите цифровую модель поверхности.", param)
        raise _api(404, "wireframe_not_found", "Каркас не найден.", "Выберите каркас из списка проекта.", param)
    if closed and not (r.stats or {}).get("closed"):
        raise refuse("wireframe_open", f"Каркас «{r.name}» не замкнут: внутренность не определена.",
                     param=param, wireframe_id=wf_id)  # fmt: skip
    return r


def _trend(conn: sa.Connection, trend_id: int) -> None:
    from bf_core.geostat import trend_store as ts

    try:
        ts.meta_of(conn, trend_id)  # a trend of any kind: a structural one is a trend of the model since 19.12б
    except ApiError as e:
        raise refuse("trend_not_found", param="trend_id") from e


def _layer(conn: sa.Connection, layer_id: int, param: str) -> None:
    if conn.execute(sa.select(objects.c.id).where(
            objects.c.id == layer_id, objects.c.kind == LAYER_KIND)).first() is None:  # fmt: skip
        raise _api(404, "layer_not_found", "Слой стрингов не найден.", "Выберите слой из списка проекта.", param)


def extent_of(conn: sa.Connection, spec: ModelSpec) -> list[float]:
    """The box of the boundary (xmin, ymin, zmin, xmax, ymax, zmax): its `box` or the extent of its wireframe; with a
    DTM — the part of it the DTM can cut (the plan intersection, the top no higher than the DTM). 422
    `boundary_empty` when nothing is left."""
    b = spec.boundary
    if b.box is not None:
        box = [float(v) for v in b.box]
    else:
        assert b.wireframe_id is not None
        got = _wireframe(conn, b.wireframe_id, "boundary.wireframe_id", closed=True).extent
        if not got or len(got) != 6:
            raise refuse("boundary_empty", param="boundary.wireframe_id")
        box = [float(v) for v in got]
    if b.dtm_id is not None:
        d = _wireframe(conn, b.dtm_id, "boundary.dtm_id", dtm=True).extent
        if not d or len(d) != 6:
            raise refuse("boundary_empty", param="boundary.dtm_id")
        lo = [max(box[0], d[0]), max(box[1], d[1]), box[2]]
        hi = [min(box[3], d[3]), min(box[4], d[4]), min(box[5], d[5])]
        if any(not lo[i] < hi[i] for i in range(3)):
            raise refuse("boundary_empty", "Граница модели пуста: ЦТМ не пересекает охват.",
                         "Выберите ЦТМ, которая лежит над границей модели.", param="boundary.dtm_id")
        box = [*lo, *hi]
    return box


# --- the data of the model ------------------------------------------------------------------------------------------


def _table_field(conn: sa.Connection, set_id: int, table: Any, field: Any, param: str) -> tuple[int, str]:
    """(id of the table, the field in its stored spelling) of a table of a set: 404 `set_not_found`,
    `table_not_found`, 422 `field_not_found`; `param` is the path of the key that names them."""
    from bf_core.composites import jobs as cj
    from bf_core.geostat.variogram import spec as vs

    if conn.execute(sa.select(objects.c.id).where(
            objects.c.id == set_id, objects.c.kind == "drillhole_set")).first() is None:  # fmt: skip
        raise _api(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.", f"{param}.set_id")
    t = vs._in_param(lambda: cj.resolve_table(conn, set_id, table), f"{param}.table")
    f = vs._in_param(lambda: vs._field(conn, int(t.id), field, "Поле", f"{param}.field", False), f"{param}.field")
    return int(t.id), str(f.name)


def _shell(conn: sa.Connection, spec: ModelSpec, g: Group, i: int) -> Group:
    """A shell group against the project: the composites exist (404 `result_not_found`) or the table of the set
    and the field of the content are there; the table as id text, the field as stored."""
    p, base = dict(g.params), f"groups.{i}.params"
    if p.get("source") == "composites":
        r = conn.execute(sa.select(dh_results.c.id, dh_results.c.table_id).where(
            dh_results.c.id == p["result_id"])).first()  # fmt: skip
        if r is None:
            raise _api(404, "result_not_found", "Композиты не найдены.", "Выберите результат из списка набора.",
                       f"{base}.result_id")  # fmt: skip
        return g
    set_id = p.get("set_id") or (spec.data.set_id if spec.data is not None else None)
    if set_id is None:
        raise sb.bad(f"{base}.set_id", "Не выбран набор скважин.", "Выберите набор скважин или таблицу кодов модели.")
    table, field = _table_field(conn, int(set_id), p["table"], p["field"], base)
    return replace(g, params={**p, "set_id": p.get("set_id"), "table": str(table), "field": field})


@dataclass(frozen=True)
class Prepared:
    spec: ModelSpec  # resolved (h), the table of the codes as id text, the shells' tables and fields as stored
    extent: list[float]
    inputs: list[dict[str, Any]]


def prepare(conn: sa.Connection, spec: ModelSpec, *, points: int | None = None) -> Prepared:
    """Checks `spec` against the project and completes it: 404 / 422 as e19-api §3 names them. `points` — the number
    of the data points when the caller knows it (the job counts them): above 2 million — 422 `too_many_points`."""
    if points is not None and points > POINTS_MAX:
        raise refuse("too_many_points", f"Точек данных больше {POINTS_MAX:,}: {points:,}.".replace(",", " "),
                     points=points)  # fmt: skip
    extent = extent_of(conn, spec)
    spec = spec.resolved(tuple(extent))
    if spec.trend_id is not None:
        _trend(conn, spec.trend_id)
    if spec.data is not None:
        table, field = _table_field(conn, spec.data.set_id, spec.data.table, spec.data.field, "data")
        spec = replace(spec, data=replace(spec.data, table=str(table), field=field))
    groups: list[Group] = []
    for i, g in enumerate(spec.groups):
        if g.type == "shell":
            g = _shell(conn, spec, g, i)
        elif g.type == "expand":
            _wireframe(conn, int(g.params["wireframe_id"]), f"groups.{i}.params.wireframe_id", closed=True)
        groups.append(g)
    spec = replace(spec, groups=tuple(groups))
    for i, m in enumerate(spec.manual):
        _layer(conn, m.layer_id, f"manual.{i}.layer_id")
    if spec.post.clip == "wireframe":
        assert spec.post.clip_id is not None
        _wireframe(conn, spec.post.clip_id, "post.clip_id", closed=True)
    elif spec.post.clip == "dtm":
        assert spec.post.clip_id is not None
        _wireframe(conn, spec.post.clip_id, "post.clip_id", dtm=True)
    return Prepared(spec, extent, inputs_of(conn, spec))


# --- the inputs of the graph and «устарела» -------------------------------------------------------------------------


def fp_of(conn: sa.Connection, kind: str, ident: Any) -> tuple[str, str] | None:
    """(name, `fp`) of an input as the project has it now; None — it is gone."""
    if isinstance(ident, bool) or not isinstance(ident, int):
        return None
    if kind == RESULT:
        r = conn.execute(sa.select(dh_results.c.name, dh_results.c.sha256).where(dh_results.c.id == ident)).first()
        return None if r is None else (str(r.name), str(r.sha256))
    if kind == LAYER:
        r2 = conn.execute(sa.select(objects.c.name, objects.c.version).where(
            objects.c.id == ident, objects.c.kind == LAYER_KIND)).first()  # fmt: skip
        return None if r2 is None else (str(r2.name), str(int(r2.version)))
    if kind == WIREFRAME:
        from bf_core.api.errors import ApiError as Api
        from bf_core.viz.meshes import mesh_state

        try:
            st = mesh_state(conn, ident)
        except Api:
            return None
        return st.name, st.cache_key()
    return esc.fp_of(conn, kind, ident)  # tables, sets, trends


def _is_dtm(conn: sa.Connection, wf_id: Any) -> bool:
    if isinstance(wf_id, bool) or not isinstance(wf_id, int):
        return False
    return conn.execute(sa.select(wireframes.c.dtm).where(wireframes.c.object_id == wf_id)).scalar() is not None


def _trend_sources(conn: sa.Connection, trend_id: int | None) -> list[tuple[str, Any]]:
    """What a structural trend reads (19.12б: its axes are those of the model, the model is stale when they change):
    the set and the table of the measurements, or the supporting wireframe. A global trend and a source that is gone
    read nothing here (the trend itself is an input: `prepare` and `freshness` name what is lost)."""
    if trend_id is None:
        return []
    from bf_core.geostat import trend_store as ts
    from bf_core.stats import source

    try:
        _name, meta = ts.meta_of(conn, int(trend_id))
        src = meta.get("source") if meta.get("kind") == "structural" else None
        if not isinstance(src, Mapping):
            return []
        if src.get("type") == "mesh":
            return [(WIREFRAME, src.get("wireframe_id"))]
        set_id = int(src["set_id"])
        return [(SET, set_id), (TABLE, source.find_table(conn, set_id, src.get("table")).id)]
    except (ApiError, KeyError, TypeError, ValueError):
        return []


def _result_set(conn: sa.Connection, result_id: Any) -> int | None:
    """The set the composites of a shell belong to (None — the result is gone)."""
    if isinstance(result_id, bool) or not isinstance(result_id, int):
        return None
    got = conn.execute(sa.select(dh_results.c.set_id).where(dh_results.c.id == result_id)).scalar()
    return None if got is None else int(got)


def inputs_of(conn: sa.Connection, spec: ModelSpec) -> list[dict[str, Any]]:
    """`meta.inputs` of a model (e19-api §2.3), in the order of its table: the set (trajectories), the table of the
    codes and the tables of the shells, the composites of the shells, the layers of the manual data, the trend, the
    wireframes (the boundary, the expansions, the clip), the DTM (the boundary, the clip). An input that is gone is
    not listed (the checks of `prepare` name it before)."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, Any]] = set()

    def add(kind: str, ident: Any) -> None:
        if ident is None or (kind, ident) in seen:
            return
        seen.add((kind, ident))
        got = fp_of(conn, kind, ident)
        if got is not None:
            out.append({"kind": kind, "id": ident, "fp": got[1], "name": got[0]})

    shells = [g for g in spec.groups if g.type == "shell"]
    if spec.data is not None:
        add(SET, spec.data.set_id)
    for g in shells:
        if g.params.get("source") == "table":
            sid = g.params.get("set_id") or (spec.data.set_id if spec.data is not None else None)
            add(SET, sid)
        elif g.params.get("source") == "composites":  # their points lie on the trajectories of the set they are of
            add(SET, _result_set(conn, g.params.get("result_id")))
    if spec.data is not None:
        add(TABLE, int(spec.data.table))
    for g in shells:
        if g.params.get("source") == "table":
            add(TABLE, int(g.params["table"]))
    for g in shells:
        if g.params.get("source") == "composites":
            add(RESULT, g.params.get("result_id"))
    for m in spec.manual:
        add(LAYER, m.layer_id)
    add(TREND, spec.trend_id)
    for kind, ident in _trend_sources(conn, spec.trend_id):
        add(kind, ident)
    add(WIREFRAME, spec.boundary.wireframe_id)
    for g in spec.groups:
        if g.type == "expand":
            add(WIREFRAME, g.params.get("wireframe_id"))
    if spec.post.clip == "wireframe":
        add(WIREFRAME, spec.post.clip_id)
    add(WIREFRAME, spec.boundary.dtm_id)
    if spec.post.clip == "dtm":
        add(WIREFRAME, spec.post.clip_id)
    return out


def _changed(conn: sa.Connection, kind: str, ident: Any, name: str) -> str:
    if kind == TABLE:
        return f"изменена таблица «{name}»"
    if kind == SET:
        return "изменены траектории"
    if kind == RESULT:
        return f"изменены композиты «{name}»"
    if kind == LAYER:
        return f"изменён слой «{name}»"
    if kind == TREND:
        return f"изменён тренд «{name}»"
    if kind == WIREFRAME:
        return f"изменена ЦТМ «{name}»" if _is_dtm(conn, ident) else f"изменён каркас «{name}»"
    return f"изменён вход «{name}»"


def freshness(conn: sa.Connection, meta: Mapping[str, Any]) -> dict[str, Any]:
    """`{stale, stale_reason, source_lost}` of a model now: the inputs it recorded against the project (an input that
    is gone — «источник удалён», `source_lost`), then the parameters against `last_run.params`. The first changed
    input names the reason (a set after the others: its key moves with every table). Reasons in Russian:
    «изменена таблица «…»», «изменены траектории», «изменён слой «…»», «изменён тренд «…»», «изменён каркас «…»»,
    «изменена ЦТМ «…»», «изменены параметры»."""
    reason: str | None = None
    moved = False  # the trajectories moved: the key of a set holds the revisions of all its tables
    for i in meta.get("inputs") or []:
        kind, ident = str(i.get("kind")), i.get("id")
        got = fp_of(conn, kind, ident)
        if got is None:
            return {"stale": True, "stale_reason": LOST, "source_lost": True}
        if got[1] != i.get("fp"):
            if kind == SET:
                moved = True
            elif reason is None:
                reason = _changed(conn, kind, ident, got[0])
    if reason is None and moved:
        reason = "изменены траектории"
    last = meta.get("last_run")
    if reason is None and isinstance(last, Mapping) and last.get("params") is not None:
        try:
            if sb.params_hash(sb.load_spec(meta)) != last["params"]:
                reason = "изменены параметры"
        except ApiError:
            reason = "параметры не читаются"
    return {"stale": reason is not None, "stale_reason": reason, "source_lost": False}
