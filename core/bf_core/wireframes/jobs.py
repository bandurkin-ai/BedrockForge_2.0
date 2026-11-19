"""Job `model.dtm` — «Построить ЦТМ» from points and lines of the project (ТЗ 7.20, 7.23; ADR 0011 §3).

Parameters {name?, layer_ids, set_ids, clouds, breaklines, boundary {layer_id, line?, use_z?}, max_edge, to_dtm,
color}:

* `layer_ids` — layers of points and of lines (`lines/store.py`). With `breaklines` (the default) every line is
  structural: its segments are edges of the surface; without it the vertices of the lines are plain points.
* `set_ids` — collars of drillhole sets (those that have X, Y and Z).
* `clouds` — point clouds [{layer_id, classes, cell?}] (ADR 0014 §5, `cloud_source.py`): the points of the
  classes (null — every class but the noise), one in every cell of the plan of `cell` metres (by default
  max(CR / 2, √(area / 2 million))), so that all the clouds together give at most 2 million vertices. A cloud is
  read by slices, never as a layer; a cloud among `layer_ids` is a cloud of every class but the noise.
  `request_from_points` — the command `terrain.from_points {cloud_id, classes [2], l_max, boundary_id?, cell?}`.
* `boundary` — a closed line of a layer (`line` — its number from 0; by default the only closed line of the
  layer): the surface is cut by it. Its heights are ignored unless `use_z` (a digitised outline often lies at
  Z = 0): the outline takes the heights of the surface.
* `max_edge` — long triangles are peeled from the outside (`geo/dtm.py`).
* `to_dtm` (the default) — the result goes straight to the section «ЦТМ» with the default settings.

A vertex without a height is left out and counted in the journal — it is never taken for 0; a line is cut at
such a vertex. The vertices of the result that come from the sources are their float64 coordinates bit for bit.

Steps: read the sources → `build_dtm` → `stage_wireframe` (spatial order, parts, measures, temp files) → the
levels of detail of every part into `cache\\mesh\\` → one write transaction that makes the wireframe. A cancel
or a failure before that transaction leaves nothing (temp files and the cache folder are removed; the folder
stays when a wireframe of the project has the same geometry — the levels are its own).

What the surface was built from is kept with it (`meta.built`: sources with their ids and names, parameters,
the report); a `.bfggis` import renumbers the ids (`remap_built`).
Ported from BF `ops/operations.py::op_dtm`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from contextlib import suppress
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_holes, line_layers, objects
from bf_core.jobs.context import JobCancelled, JobContext, JobError
from bf_core.jobs.registry import handler
from bf_core.lines.cloud import cloud_meta, not_a_cloud

from . import cloud_source as cs
from . import store as wf_store

MAX_SOURCES = 2000
SET = "drillhole_set"
LAYER = "line_layer"


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def _num(x: float) -> str:
    return f"{x:.10g}".replace(".", ",")


def _ids(values: Any, what: str) -> list[int]:
    ids = list(dict.fromkeys(int(i) for i in values or []))
    if len(ids) > MAX_SOURCES:
        raise ApiError(422, "too_many_sources", f"{what}: больше {MAX_SOURCES}.", "Выберите меньше источников.")
    return ids


def request(
    conn: sa.Connection,
    *,
    name: str | None = None,
    layer_ids: Any = None,
    set_ids: Any = None,
    clouds: Any = None,
    breaklines: bool = True,
    boundary: dict[str, Any] | None = None,
    max_edge: float | None = None,
    to_dtm: bool = True,
    color: str | None = None,
) -> dict[str, Any]:
    """Checked parameters of the job `model.dtm` (for the endpoint that starts it); ApiError otherwise."""
    layers = _ids(layer_ids, "Слоёв")
    sets = _ids(set_ids, "Наборов скважин")
    asked = cs.check_sources(clouds)
    _ids([c["layer_id"] for c in asked], "Облаков точек")
    if not layers and not sets and not asked:
        raise ApiError(
            422,
            "no_sources",
            "Не из чего строить поверхность.",
            "Выберите слои точек или линий, облака точек или наборы скважин (их устья).",
        )
    points = cloud_points = 0
    names: list[str] = []
    wanted = list(dict.fromkeys([*layers, *(c["layer_id"] for c in asked)]))
    if wanted:
        rows = conn.execute(
            sa.select(objects.c.id, objects.c.name, objects.c.meta, line_layers.c.point_count)
            .join(line_layers, line_layers.c.object_id == objects.c.id)
            .where(objects.c.id.in_(wanted), objects.c.kind == LAYER, objects.c.state == "ready")
        ).all()
        found = {int(r.id): r for r in rows}
        if len(found) != len(wanted):
            raise ApiError(404, "line_layer_not_found", "Слой линий или точек не найден.", "Обновите дерево проекта.")
        named = {c["layer_id"] for c in asked}
        for i in [i for i in layers if cloud_meta(found[i].meta) is not None]:
            layers.remove(i)  # a cloud among the layers: read by slices, every class but the noise
            if i not in named:
                asked.append({"layer_id": i, "classes": None, "cell": None})
        points += sum(int(found[i].point_count) for i in layers)
        names += [str(found[i].name) for i in layers]
        for c in asked:
            row = found[c["layer_id"]]
            meta = cloud_meta(row.meta)
            if meta is None:
                raise not_a_cloud()
            of = cs.count_of(meta.get("classes"), c["classes"])
            if not of:
                raise cs.no_ground(str(row.name), c["classes"])
            cloud_points += of
            names.append(str(row.name))
        points += min(cloud_points, cs.MAX_VERTICES)
    if sets:
        rows = conn.execute(
            sa.select(objects.c.id, objects.c.name).where(
                objects.c.id.in_(sets), objects.c.kind == SET, objects.c.state == "ready"
            )
        ).all()
        found = {int(r.id): r for r in rows}
        if len(found) != len(sets):
            raise ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")
        points += int(
            conn.execute(
                sa.select(sa.func.count()).select_from(dh_holes).where(dh_holes.c.set_id.in_(sets))
            ).scalar_one()
        )
        names += [f"устья «{found[i].name}»" for i in sets]
    out: dict[str, Any] = {
        "layer_ids": layers,
        "set_ids": sets,
        "breaklines": bool(breaklines),
        "to_dtm": bool(to_dtm),
        "points": points,
    }
    if asked:
        out["clouds"] = asked
        out["cloud_points"] = cloud_points  # before the thinning: what the thinning itself holds
    if boundary:
        layer_id = boundary.get("layer_id")
        row = conn.execute(
            sa.select(line_layers.c.points_only, line_layers.c.closed_count)
            .join(objects, line_layers.c.object_id == objects.c.id)
            .where(objects.c.id == layer_id, objects.c.kind == LAYER, objects.c.state == "ready")
        ).first()
        if row is None:
            raise ApiError(404, "line_layer_not_found", "Слой с линией границы не найден.", "Обновите дерево проекта.")
        if row.points_only or not row.closed_count:
            raise ApiError(
                422,
                "bad_boundary",
                "В слое границы нет замкнутой линии.",
                "Выберите слой с замкнутой линией — контуром поверхности.",
            )
        line = boundary.get("line")
        out["boundary"] = {
            "layer_id": int(layer_id),  # type: ignore[arg-type]
            "line": None if line is None else int(line),
            "use_z": bool(boundary.get("use_z")),
        }
    if max_edge is not None:
        if not (math.isfinite(float(max_edge)) and float(max_edge) > 0):
            raise ApiError(
                422,
                "bad_max_edge",
                "Наибольшая длина ребра должна быть положительной.",
                "Введите число больше нуля или оставьте поле пустым.",
            )
        out["max_edge"] = float(max_edge)
    if color is not None:
        out["color"] = wf_store.check_color(color)
    title = " ".join(str(name or "").split()) or ("ЦТМ: " + ", ".join(names))
    out["name"] = title[: wf_store.MAX_NAME]
    return out


def request_from_points(
    conn: sa.Connection,
    *,
    cloud_id: int,
    classes: Any = (cs.GROUND,),
    l_max: float | None = None,
    boundary_id: int | None = None,
    cell: float | None = None,
    name: str | None = None,
    to_dtm: bool = True,
    color: str | None = None,
) -> dict[str, Any]:
    """Parameters of `model.dtm` for the command `terrain.from_points` (ADR 0014 §5): the ground of one cloud.
    `l_max` — the longest edge; by default 5 CR of the last «Фильтр земли» of the cloud (5 m when it was never
    filtered), and not less than 4 steps of the thinning: a shorter one would peel the surface away."""
    row = conn.execute(
        sa.select(objects.c.meta).where(objects.c.id == cloud_id, objects.c.kind == LAYER, objects.c.state == "ready")
    ).first()
    if row is None:
        raise ApiError(404, "line_layer_not_found", "Облако точек не найдено.", "Обновите дерево проекта.")
    meta = cloud_meta(row.meta)
    if meta is None:
        raise not_a_cloud()
    if l_max is None:
        cr = cs.filter_cr(meta)
        step = cs.check_cell(cell) or cs.cell_of(meta["bbox"], cr)
        l_max = max(5.0 * (cr or cs.DEFAULT_CR), 4.0 * step)
    return request(
        conn,
        name=name,
        clouds=[{"layer_id": int(cloud_id), "classes": None if classes is None else list(classes), "cell": cell}],
        boundary=None if boundary_id is None else {"layer_id": int(boundary_id)},
        max_edge=l_max,
        to_dtm=to_dtm,
        color=color,
    )


def _memory(params: dict[str, Any]) -> int:
    """The sources, the triangulation (about 2 triangles a point), the mesh in spatial order and one part being
    simplified: about 0.5 KB a point over the libraries (measured: 1 million points — 660 MB at the peak).
    `points` counts a cloud by the vertices it gives (at most 2 million); the thinning before it holds
    `BYTES_A_POINT` a point of the classes and is over when the triangulation starts."""
    build = int(params.get("points") or 0) * 600
    thin = int(params.get("cloud_points") or 0) * cs.BYTES_A_POINT
    return max(512, int(max(build, thin) / 2**20) + 300)


# --- sources ---------------------------------------------------------------------------------------------


class Sources:
    """Points and structural lines gathered from the project, with what was left out."""

    def __init__(self) -> None:
        self.points: list[np.ndarray] = []
        self.lines: list[np.ndarray] = []
        self.boundary: np.ndarray | None = None
        self.used: list[dict[str, Any]] = []
        self.skipped = 0  # vertices without a height
        self.notes: list[tuple[str, str]] = []  # lines of the journal: (text, level)
        self.cell = 0.0  # the largest step a cloud was thinned with, m

    def add_points(self, xyz: np.ndarray) -> int:
        ok = np.isfinite(xyz).all(axis=1)
        self.skipped += int(len(xyz) - np.count_nonzero(ok))
        if ok.any():
            self.points.append(xyz if ok.all() else xyz[ok])
        return int(np.count_nonzero(ok))

    def add_lines(self, xyz: np.ndarray, offsets: np.ndarray) -> tuple[int, int]:
        """Every line as a breakline, cut at the vertices that have no height: runs of two or more vertices
        are structural, a lone vertex is a point. Returns (vertices taken, segments)."""
        ok = np.isfinite(xyz).all(axis=1)
        self.skipped += int(len(xyz) - np.count_nonzero(ok))
        # run starts: the first vertex of a line, or a vertex after one without a height
        start = np.zeros(len(xyz), dtype=bool)
        start[offsets[:-1]] = True
        start[1:] |= ~ok[:-1]
        start &= ok
        run = np.cumsum(start)[ok]  # the run of every vertex that has a height
        pts = xyz[ok]
        if not len(pts):
            return 0, 0
        cuts = np.flatnonzero(np.diff(run)) + 1
        bounds = np.concatenate([[0], cuts, [len(pts)]])
        sizes = np.diff(bounds)
        lone = bounds[:-1][sizes == 1]
        if len(lone):
            self.points.append(pts[lone])
        segments = 0
        for a, b in zip(bounds[:-1][sizes > 1].tolist(), bounds[1:][sizes > 1].tolist(), strict=True):
            self.lines.append(pts[a:b])
            segments += b - a - 1
        return len(pts), segments


def _boundary(ls: Any, ref: dict[str, Any]) -> np.ndarray:
    """The polygon of the boundary: a closed line of the layer without its repeated first vertex; (k, 3) with
    `use_z`, else (k, 2)."""
    closed = np.flatnonzero(np.asarray(ls.closed) != 0)
    line = ref.get("line")
    if line is None:
        if len(closed) != 1:
            raise JobError(
                "bad_boundary",
                f"В слое «{ls.name}» замкнутых линий: {len(closed)} — граница должна быть одна.",
                "Укажите линию границы или оставьте в слое одну замкнутую линию.",
            )
        line = int(closed[0])
    elif not 0 <= int(line) < ls.line_count or int(line) not in closed.tolist():
        raise JobError(
            "bad_boundary",
            f"Линия {int(line) + 1} слоя «{ls.name}» не замкнута или её нет.",
            "Выберите замкнутую линию — контур поверхности.",
        )
    xyz = np.asarray(ls.line(int(line)), dtype=np.float64)[:-1]  # a closed line repeats its first vertex
    if ref.get("use_z"):
        if not np.isfinite(xyz).all():
            raise JobError(
                "bad_boundary",
                f"У линии границы в слое «{ls.name}» есть вершины без высоты.",
                "Снимите «Учитывать высоты границы» или задайте высоты вершин.",
            )
        return xyz
    return np.ascontiguousarray(xyz[:, :2])


CLOUD_WEIGHT = 10  # a cloud in the progress of the reading: as many layers
READING, THINNING = "Чтение исходных данных", "Прореживание облака"


def _is_cloud(conn: sa.Connection, layer_id: int) -> bool:
    meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == layer_id, objects.c.kind == LAYER)).scalar()
    return cloud_meta(meta) is not None


def gather(conn: sa.Connection, store: Any, params: dict[str, Any], tick: Callable[[float, str], None]) -> Sources:
    """Read the sources of the parameters (as `request` returns them) from the project. `tick(share, stage)`."""
    from bf_core.lines import store as ls_store

    src = Sources()
    sets = [int(i) for i in params.get("set_ids") or []]
    clouds = cs.check_sources(params.get("clouds"))
    layers = []
    for layer_id in [int(i) for i in params.get("layer_ids") or []]:
        if not _is_cloud(conn, layer_id):
            layers.append(layer_id)
        elif all(c["layer_id"] != layer_id for c in clouds):  # parameters made before the clouds came
            clouds.append({"layer_id": layer_id, "classes": None, "cell": None})
    heavy = CLOUD_WEIGHT * len(clouds)
    total = max(1, len(layers) + len(sets) + heavy + (1 if params.get("boundary") else 0))
    done = 0
    for layer_id in layers:
        ls = ls_store.read_layer(conn, store, layer_id)
        xyz = np.asarray(ls.xyz, dtype=np.float64).reshape(-1, 3)
        if ls.points_only or not params.get("breaklines", True):
            taken = src.add_points(xyz)
            src.used.append({"kind": LAYER, "id": layer_id, "name": ls.name, "points": taken})
        else:
            taken, segments = src.add_lines(xyz, np.asarray(ls.offsets, dtype=np.int64))
            src.used.append({"kind": LAYER, "id": layer_id, "name": ls.name, "points": taken, "segments": segments})
        done += 1
        tick(done / total, READING)
    for one in cs.take(conn, store, clouds, lambda f: tick((done + heavy * f) / total, THINNING)):
        src.add_points(one.thinned.xyz)
        src.cell = max(src.cell, one.thinned.cell)
        src.used.append({"kind": LAYER, **one.used()})
        src.notes += one.notes()
    done += heavy
    for set_id in sets:
        name = conn.execute(sa.select(objects.c.name).where(objects.c.id == set_id, objects.c.kind == SET)).scalar()
        if name is None:
            raise JobError("set_not_found", "Набор скважин не найден.", "Обновите дерево проекта.")
        h = dh_holes.c
        rows = conn.execute(
            sa.select(h.x, h.y, h.z)
            .where(h.set_id == set_id, h.x.is_not(None), h.y.is_not(None), h.z.is_not(None))
            .order_by(h.id)
        ).all()
        missing = conn.execute(sa.select(sa.func.count()).select_from(dh_holes).where(h.set_id == set_id)).scalar_one()
        src.skipped += int(missing) - len(rows)
        taken = src.add_points(np.array(rows, dtype=np.float64).reshape(-1, 3)) if rows else 0
        src.used.append({"kind": SET, "id": set_id, "name": str(name), "collars": taken})
        done += 1
        tick(done / total, READING)
    ref = params.get("boundary")
    if ref:
        if _is_cloud(conn, int(ref["layer_id"])):
            raise JobError(
                "bad_boundary",
                "Граница поверхности — облако точек: в нём нет замкнутой линии.",
                "Выберите слой с замкнутой линией — контуром поверхности.",
            )
        ls = ls_store.read_layer(conn, store, int(ref["layer_id"]))
        src.boundary = _boundary(ls, ref)
        src.used.append({"kind": LAYER, "id": int(ref["layer_id"]), "name": ls.name, "boundary": len(src.boundary)})
        tick(1.0, READING)
    return src


def remap_built(built: Any, new_id: Callable[[int], int | None]) -> Any:
    """`meta.built` of a wireframe on a `.bfggis` import: the ids of its sources and of its boundary are those
    of the project it was built in. A source the archive does not have (deleted since) keeps its name and
    loses its id: the old number would point at another object of the new project."""

    def renumbered(value: Any) -> int | None:
        return new_id(value) if isinstance(value, int) and not isinstance(value, bool) else None

    if not isinstance(built, dict):
        return built
    out = dict(built)
    if isinstance(built.get("sources"), list):
        out["sources"] = [
            {**src, "id": renumbered(src.get("id"))} if isinstance(src, dict) else src for src in built["sources"]
        ]
    params = built.get("params")
    if isinstance(params, dict) and isinstance(params.get("boundary"), dict):
        boundary = params["boundary"]
        out["params"] = {**params, "boundary": {**boundary, "layer_id": renumbered(boundary.get("layer_id"))}}
    return out


def _geometry_in_use(engine: sa.Engine, shas: Mapping[str, str]) -> bool:
    """A wireframe of the project has the geometry of these blobs (`store.geometry_shared`). When the project
    cannot be asked — the job may be failing because of the database — the answer is «yes»: a folder of
    levels left behind costs disk space, a folder taken from a wireframe costs its first frames."""
    try:
        with dialect.read_tx(engine) as conn:
            return wf_store.geometry_shared(conn, shas)
    except Exception:  # noqa: BLE001 - never instead of the error the job is failing with
        return True


# --- the job ---------------------------------------------------------------------------------------------


@handler("model.dtm", "Построение ЦТМ", queue="heavy", estimate_memory=_memory)
def model_dtm(ctx: JobContext) -> dict[str, Any]:
    from bf_core.geo.dtm import DtmError, build_dtm
    from bf_core.storage.blobs import BlobStore
    from bf_core.viz import meshes as viz

    if ctx.project_folder is None:
        raise RuntimeError("model.dtm is a project job")
    p = ctx.params
    engine = ctx.engine()
    store = BlobStore(ctx.project_folder)

    def part(start: float, stop: float, message: str) -> Callable[[float], None]:
        return lambda f: ctx.tick(start + (stop - start) * max(0.0, min(1.0, f)), message)

    ctx.tick(0.0, READING)
    try:
        with dialect.read_tx(engine) as conn:
            src = gather(conn, store, p, lambda f, stage: ctx.tick(0.1 * max(0.0, min(1.0, f)), stage))
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    for text, level in src.notes:
        ctx.log(text, level)
    edge = p.get("max_edge")
    if edge is not None and edge < 2.0 * src.cell:
        ctx.log(
            f"Наибольшая длина ребра {_num(edge)} м меньше двух шагов прореживания облака "
            f"({_num(round(src.cell, 4))} м): поверхность может быть обрезана — увеличьте длину ребра.",
            "warn",
        )
    if src.skipped:
        ctx.log(f"Пропущено вершин без высоты: {_n(src.skipped)} (пустая отметка не считается нулём)", "warn")
    points = np.concatenate(src.points) if src.points else None
    n_points = 0 if points is None else len(points)
    n_lines = sum(len(line) for line in src.lines)
    ctx.log(f"Исходные данные: точек {_n(n_points)}, структурных линий {_n(len(src.lines))} ({_n(n_lines)} вершин)")
    try:
        v, t, report = build_dtm(
            points,
            src.lines,
            boundary=src.boundary,
            max_edge=p.get("max_edge"),
            progress=part(0.1, 0.55, "Триангуляция"),
        )
    except DtmError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    del points, src.points, src.lines
    notes = [f"вершин {_n(report['points'])}", f"треугольников {_n(report['triangles'])}"]
    if report["merged_duplicates"]:
        notes.append(f"совпадающих точек объединено {_n(report['merged_duplicates'])}")
    if report["crossings_added"]:
        notes.append(f"пересечений линий добавлено {_n(report['crossings_added'])}")
    if report["unused_points"]:
        notes.append(f"точек вне поверхности {_n(report['unused_points'])}")
    if report["dropped_long"]:
        notes.append(f"длинных треугольников убрано {_n(report['dropped_long'])}")
    ctx.log("Триангуляция: " + ", ".join(notes) + f"; площадь в плане {_num(round(report['area_plan'], 2))} м²")

    staged = None
    key = None
    try:
        try:
            staged = wf_store.stage_wireframe(
                store, v, t, progress=lambda f, m: ctx.tick(0.55 + 0.2 * max(0.0, min(1.0, f)), m)
            )
        except ApiError as exc:
            raise JobError(exc.code, exc.message, exc.hint) from exc
        del v, t
        key = viz.cache_key(tuple(sorted(staged.shas.items())), staged.render)
        lod = viz.build_cache(
            ctx.project_folder, key, staged.xyz, staged.tri, staged.parts, part(0.75, 0.97, "Уровни детализации")
        )
        ctx.tick(0.97, "Сохранение каркаса в проект")
        built = {
            "kind": "dtm",
            "method": report["method"],
            "sources": src.used,
            "params": {k: p.get(k) for k in ("breaklines", "boundary", "max_edge")},
            "report": {k: report[k] for k in report if k != "method"},
        }
        with dialect.write_tx(engine) as conn:
            name = wf_store.free_name(conn, str(p.get("name") or "ЦТМ"))
            wf_id = wf_store.commit_wireframe(
                conn, store, staged, name=name, color=p.get("color"), dtm=bool(p.get("to_dtm", True)),
                meta={"built": built},
            )  # fmt: skip
    except BaseException:
        if staged is not None:
            staged.discard()
            # the levels of detail are keyed by the geometry: a wireframe built from the same sources before
            # has the same key, and its levels are the files this job has just written over — they stay
            if key is not None and not _geometry_in_use(engine, staged.shas):
                viz.drop_cache(ctx.project_folder, key)
        raise
    ctx.log(
        f"Создан каркас «{name}»" + (" в разделе «ЦТМ»" if p.get("to_dtm", True) else "")
        + f": частей {_n(lod['parts'])}, треугольников по уровням " + " / ".join(_n(n) for n in lod["triangles"])
    )  # fmt: skip
    with suppress(JobCancelled):  # the wireframe is in the project: a cancel this late undoes nothing
        ctx.tick(1.0, "Готово")
    return {
        "wireframe_id": wf_id,
        "name": name,
        "dtm": bool(p.get("to_dtm", True)),
        "points": report["points"],
        "triangles": report["triangles"],
        "area": report["area"],
        "area_plan": report["area_plan"],
        "merged_duplicates": report["merged_duplicates"],
        "dropped_long": report["dropped_long"],
        "skipped_empty": src.skipped,
        "lod_triangles": lod["triangles"],
    }
