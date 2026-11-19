"""The sources of «Построить ЦТМ» (the job `model.dtm`, `wireframes/jobs.py`): layers of points and of lines,
collars of drillhole sets, the boundary, point clouds — read from the project into `Sources`.

Two steps. `gather` — inside the read transaction of the job: the layers, the collars and the boundary are
read; a cloud is only opened, its columns held open (`lines.cloud.HeldCloud`). `thin_clouds` — after the
transaction: every cloud is thinned (`cloud_source.thin`), which for 10–50 million points takes minutes. No
transaction is kept that long: on SQLite a reader that old keeps the WAL from being folded back while other
jobs write, on PostgreSQL it holds a snapshot.

What the thinning reads cannot be taken from under it. Blobs are immutable; a column a cloud no longer has
(«Фильтр земли» wrote new `class` and `h` meanwhile, the layer was deleted) loses its owner and the collector
removes such files (`storage/gc.py`, every 30 minutes and by command). The job holds the columns open instead
of owning them in the project: on Windows the collector cannot remove an open file (it is «busy», left for the
next pass), elsewhere the removed file is read to its end through the handle. The surface is built from the
cloud as it was when the job opened it, and nothing of the job stays in the project to be cleaned up after a
crash. A row of `blob_owners` for the job would guard the same files at the cost of two write transactions and
of that cleanup — and an archive of the project made meanwhile would carry it.

The points stand in `Sources` in one order — layers, clouds, sets — though the clouds are read last: the
surface is the one the job built when it thinned the clouds in their turn.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.db.schema import dh_holes, objects
from bf_core.jobs.context import JobError
from bf_core.lines.cloud import cloud_meta

from . import cloud_source as cs

SET = "drillhole_set"
LAYER = "line_layer"


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
        self.clouds: list[int] = []  # the clouds among the sources
        self.held: list[tuple[Any, dict[str, Any]]] = []  # clouds opened and not thinned yet, with their specs
        self.at = (0, 0)  # where their points and their records go: after those of the layers
        self.share = (0.0, 1.0)  # the progress when the thinning starts and what the thinning adds to it

    def release(self) -> None:
        """The columns of the clouds that wait to be thinned are closed (a failure, a cancel)."""
        for cloud, _spec in self.held:
            cloud.close()
        self.held = []

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
HELD = ("xyz", "class", "h")  # the columns the thinning reads (`cloud_source.thin`): a file handle each
READING, THINNING = "Чтение исходных данных", "Прореживание облака"


def _is_cloud(conn: sa.Connection, layer_id: int) -> bool:
    meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == layer_id, objects.c.kind == LAYER)).scalar()
    return cloud_meta(meta) is not None


def gather(conn: sa.Connection, store: Any, params: dict[str, Any], tick: Callable[[float, str], None]) -> Sources:
    """Read the sources of the parameters (as `request` returns them) from the project. `tick(share, stage)`.
    The clouds are only opened, their columns held open (`lines.cloud.HeldCloud`): `thin_clouds` reads them
    once the transaction is over — the thinning of 10–50 million points takes minutes, and no transaction is
    kept that long."""
    from bf_core.lines import cloud as lc
    from bf_core.lines import store as ls_store

    src = Sources()
    try:
        return _gather(conn, store, params, tick, src, lc, ls_store)
    except BaseException:
        src.release()
        raise


def _gather(conn, store, params, tick, src, lc, ls_store) -> Sources:  # type: ignore[no-untyped-def]
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
    for spec in clouds:
        src.held.append((lc.open_cloud(conn, store, int(spec["layer_id"])).hold(HELD), spec))
    src.at = (len(src.points), len(src.used))
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
        done += 1
        tick(done / total, READING)
    src.share = (done / total, heavy / total)
    return src


def thin_clouds(src: Sources, tick: Callable[[float, str], None]) -> None:
    """The clouds `gather` opened, each thinned (`cloud_source.thin`) — outside any transaction; the limit of
    vertices is shared by the clouds in proportion to their points of the classes (as `cloud_source.take`
    shares it). A cloud without such points — `no_ground`. Their columns are closed whatever happens."""
    held, at, (before, share) = src.held, src.at, src.share
    try:
        counts = [cs.count_of(cloud.classes, spec.get("classes")) for cloud, spec in held]
        points, used = src.points[at[0] :], src.used[at[1] :]  # what was read after the clouds were opened
        del src.points[at[0] :], src.used[at[1] :]
        for k, ((cloud, spec), of) in enumerate(zip(held, counts, strict=True)):
            classes = None if spec.get("classes") is None else [int(code) for code in spec["classes"]]
            if not of:
                raise cs.no_ground(cloud.name, classes)
            budget = cs.MAX_VERTICES if len(held) == 1 else cs.MAX_VERTICES * of // sum(counts)

            def part(f: float, k: int = k) -> None:
                tick(before + share * (k + f) / len(held), THINNING)

            got = cs.thin(cloud, classes, spec.get("cell"), budget=budget, tick=part)
            cloud.close()
            if not len(got.rows):  # `meta.cloud.classes` said there were points
                raise cs.no_ground(cloud.name, classes)
            one = cs.Taken(cloud.layer_id, cloud.name, classes, budget, got)
            src.add_points(got.xyz)
            src.cell = max(src.cell, got.cell)
            src.clouds.append(one.layer_id)
            src.used.append({"kind": LAYER, **one.used()})
            src.notes += one.notes()
        src.points += points
        src.used += used
    finally:
        src.release()
