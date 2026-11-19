"""Series of sections in the core (ADR 0016 «Замеры»; ТЗ 8.1.5: «Каркас по серии» 100 разрезов × 500 вершин ≤ 10 с;
ТЗ 5.3). The sections of wireframes on a series and the job `sections.cache` are `section_wireframes.py`; the step
`]` of the window to its data ready — the E2E `series.spec` (`bench/out/series_step.json`, `external`).

Two projects made in `prepare`, measured in a fresh process (the kernels loaded before, `load`):
  points_s         `SeriesGeometry.section_of` of `points` points, the median of 5: an inclined series (φ = 60°)
                   at UTM coordinates, 4 001 sections 0.3 m apart with windows 0.1 + 0.2 m (a partition); 5 % of
                   the points on the faces of the windows (of both series), 1 % with a NaN coordinate, some beyond
                   the ends.
                   points_gaps_s — the same points on windows 0.1 + 0.1 m (gaps: the other way of the kernel);
  table_s          the table of a series of 5 000 sections (`names.series_table`), the median of 5;
  links_s          `POST /section-series/{id}/links` of the contour layer (`contours` × `vertices`: four bodies,
                   one of them branching 1 → 2), the contours of the series cached — the median of 5;
                   links_first_s — the first request (the layer read, its sections); links_cross_s —
                   `links.auto_links` of `contours`/5 sections × 5 crossing ellipses of `vertices` vertices (no
                   vertex of one inside the other: every linked pair through the sweep of the edges);
  shadow_s         `GET /section-series/{id}/contours?k=k−1,k+1` from a layer of `shadow` contours of 100 vertices:
                   the slowest of the steps k = 1…K−2; shadow_first_s — the first one (the layer read);
  stitch_series_s  «Каркас по серии» of the contour layer as its job runs (`ops/warm.py::run_step`: the precheck,
                   `ops/job.py::run`, the wireframes written) on a copy of the project made at the start of `run`:
                   every repeat finds the project of `prepare`;
  snap_s           `GET /viz/sets/{id}/snap?plane=` of the 11 sections of an inclined series (φ = 60°) over
                   `holes` drifting holes × `intervals` samples: the slowest; snap_first_s — the first request
                   (the trajectories from the database, the snapshot of the table from its Parquet file).
What is checked (counters with a budget of 0):
  points_mismatches  the sections of the points against the windows lo_k ≤ τ < hi_k (τ by `geometry.dot`);
  links_wrong        links, bodies or conflicts of the table other than the layout makes, + crossing pairs ≠ 95;
  shadow_wrong       shadow answers with a contour off k ± 1 or a count other than the layer has there;
  bodies_wrong       wireframes other than the four bodies of 30, 30, 20, 20 contours, open or non-manifold
                     edges; volume_error — the largest relative difference of the volume of a body without a
                     branch from the sum of the trapezoids of its contours and its caps (Δ/2 each);
  snap_missed        targets of a window not as those of the whole set inside it, bit for bit.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

UTM = (512_345.678, 6_789_012.345, 0.0)
SERIES = {"type": "vertical", "p1": list(UTM), "az": 30.0, "s0": 0.0, "step": 20.0, "towards": 10.0, "away": 10.0}
POINTS = {"type": "inclined", "p1": [512_345.678, 7_012_345.123, 250.0], "az": 117.3, "phi": 60.0,
          "s0": -1234.5, "s1": -34.5, "step": 0.3, "towards": 0.1, "away": 0.2}  # fmt: skip
TABLE = {"type": "vertical", "p1": [512_345.678, 7_012_345.123, 0.0], "az": 90.0, "s0": 0.0, "s1": 2.0 * 4999,
         "step": 2.0}  # fmt: skip
SNAP = {"type": "inclined", "p1": [500_495.0, 6_000_245.0, 400.0], "az": 0.0, "phi": 60.0, "s0": -200.0,
        "s1": 200.0, "step": 40.0, "towards": 10.0, "away": 10.0}  # fmt: skip
METHOD = "minimum_curvature"
SHADOW_VERTICES = 100
REPEATS = 5


def _layout(contours: int) -> tuple[int, list[tuple[int, float, list[int]]]]:
    """K sections and the bodies [(body, centre u, sections)]: A and B on all K, C on the middle two thirds, D on
    the middle third as one contour (3) and then as two (4, 5: a branch 1 → 2) on the next sixth — 10K/3 contours."""
    k = 3 * contours // 10
    assert contours % 10 == 0 and k % 6 == 0, "contours: a multiple of 20"
    one, two = list(range(k // 3, 2 * k // 3)), list(range(2 * k // 3, 5 * k // 6))
    return k, [(0, 100.0, list(range(k))), (1, 260.0, list(range(k))), (2, 420.0, list(range(k // 6, 5 * k // 6))),
               (3, 600.0, one), (4, 585.0, two), (5, 615.0, two)]  # fmt: skip


def _rings(contours: int, vertices: int) -> tuple[list[Any], list[tuple[int, int]]]:
    """The contours of the layout as (u, w) on their sections (every other one drawn the other way, the start
    vertex moving, survey noise): [uw], [(body, k)]."""
    import numpy as np

    rng = np.random.default_rng(23)
    _k, bodies = _layout(contours)
    out, where = [], []
    for body, cu, ks in bodies:
        for k in ks:
            t = np.linspace(0.0, 2.0 * np.pi, vertices, endpoint=False) + 0.1 * k
            r = 1.0 + rng.normal(0.0, 0.004, vertices)
            if body >= 4:  # the branches of D: two small ellipses inside the last contour of its trunk
                a, b, u0, w0 = 11.0 + 0.5 * np.sin(k), 9.0, cu, 150.0
            else:
                a, b = (30.0 + 6.0 * np.sin(0.4 * k + body)) if body < 3 else 30.0, 12.0 + k % 5
                u0, w0 = cu + 4.0 * np.sin(0.3 * k), 150.0 + 3.0 * np.cos(0.2 * k)
            uw = np.column_stack([u0 + a * r * np.cos(t), w0 + b * r * np.sin(t)])
            out.append(uw if k % 2 else uw[::-1].copy())
            where.append((body, k))
    return out, where


def _xyz(geom: Any, k: int, uw: Any) -> Any:
    import numpy as np

    o = geom.origin(k)
    return np.column_stack([o[i] + uw[:, 0] * geom.s[i] + uw[:, 1] * geom.w[i] for i in range(3)])


def prepare(params: dict[str, Any], work: Path) -> None:
    import numpy as np

    from bf_core.db import dialect
    from bf_core.formats.lineset import from_lines
    from bf_core.lines import store as ls_store
    from bf_core.projects.project import create_folder
    from bf_core.sections import store as series_store
    from bf_core.sections.geometry import series_geometry
    from bf_core.storage.blobs import BlobStore

    contours, vertices = int(params["contours"]), int(params["vertices"])
    k, _bodies = _layout(contours)
    meta = {**SERIES, "s1": SERIES["step"] * (k - 1)}
    geom = series_geometry(meta)
    rings, where = _rings(contours, vertices)
    a = np.linspace(0.0, 2.0 * np.pi, SHADOW_VERTICES, endpoint=False)
    shadow = [_xyz(geom, n % k, np.column_stack([1000.0 + 5.0 * (n // k) + 2.0 * np.cos(a), 300.0 + 2.0 * np.sin(a)]))
              for n in range(int(params["shadow"]))]  # fmt: skip
    folder = create_folder(work, "Серия", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        with dialect.write_tx(engine) as conn:
            store = BlobStore(folder)
            file_id = ls_store.create_file(conn, store, name="контуры.str", meta={"format": "micromine_str"})
            body = [_xyz(geom, kk, uw) for uw, (_b, kk) in zip(rings, where, strict=True)]
            layer = ls_store.put_layer(conn, store, from_lines("Контуры", body, closed=[True] * len(body)),
                                       file_id=file_id)  # fmt: skip
            shade = ls_store.put_layer(conn, store, from_lines("Тень", shadow, closed=[True] * len(shadow)),
                                       file_id=file_id)  # fmt: skip
            sid = series_store.create(conn, {**meta, "contour_layer": layer}, "Серия")["id"]
    finally:
        engine.dispose()
    holes = _holes(work, int(params["holes"]), int(params["intervals"]))
    (work / "ids.txt").write_text(f"{folder}\n{sid}\n{layer}\n{shade}\n{holes}", encoding="utf-8")
    load()  # compile into the cache outside the timing
    project = _project(holes, "bench-holes")
    try:  # the snapshot of the table written to its Parquet file, as on a reopened project
        router_snap(project, _set_id(project), None)
    finally:
        project.engine.dispose()


def _holes(work: Path, holes: int, intervals: int) -> str:
    """A grid of 100 columns of holes 10 m apart, az 90° dip −70° with drift (20 stations), samples of 2 m."""
    import numpy as np

    from bf_core.db import dialect
    from bf_core.drillholes.store import (
        CollarRow,
        ColumnSpec,
        TableData,
        create_set,
        replace_collars,
        replace_surveys,
        write_table,
    )
    from bf_core.projects.project import create_folder

    rng = np.random.default_rng(29)
    folder = create_folder(work, "Скважины", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    length = 2.0 * intervals
    names = [f"H{i:05d}" for i in range(holes)]
    try:
        with dialect.write_tx(engine) as conn:
            set_id = create_set(conn, "Сетка")
            x, y = 500_000.0 + np.arange(holes) % 100 * 10.0, 6_000_000.0 + np.arange(holes) // 100 * 10.0
            rows = [CollarRow(n, float(x[i]), float(y[i]), 400.0, length, 90.0, -70.0, {}) for i, n in enumerate(names)]
            replace_collars(conn, set_id, rows)
        depth = np.linspace(0.0, length, 20)
        az = 90.0 + rng.normal(0.0, 1.0, (holes, 20)).cumsum(axis=1)
        dip = np.clip(-70.0 + rng.normal(0.0, 0.7, (holes, 20)).cumsum(axis=1), -89.0, -30.0)
        replace_surveys(engine, set_id, [
            (n, float(d), float(x), float(y))
            for n, xs, ys in zip(names, az.tolist(), dip.tolist(), strict=True)
            for d, x, y in zip(depth.tolist(), xs, ys, strict=True)
        ], lambda _f, _m: None)  # fmt: skip
        hole_of = np.repeat(np.arange(holes), intervals)
        f = np.tile(np.arange(intervals), holes).astype(float) * 2.0
        au = rng.lognormal(-1.0, 1.0, len(f))
        data = TableData("Пробы", "assay", [names[i] for i in hole_of.tolist()], f, f + 2.0,
                         [ColumnSpec("AU", "number", "g/t", "component")], [au])  # fmt: skip
        write_table(engine, set_id, data, job_id=None, tick=lambda _f, _m: None)
    finally:
        engine.dispose()
    return str(folder)


def load() -> None:
    import numpy as np

    import bf_core.sections.contours_router  # noqa: F401 - FastAPI, the models of the routers
    import bf_core.viz.router  # noqa: F401
    from bf_core.drillholes.desurvey import Collars, Surveys, desurvey
    from bf_core.ops import warm
    from bf_core.sections.geometry import series_geometry
    from bf_core.sections.names import series_table
    from bf_core.viz import arcs

    assert "stitch_series" in warm.warm(("stitch_series",))  # the windows, the links, the stitch, the checks
    for towards in (0.2, 0.1):
        series_geometry({**POINTS, "away": towards}).section_of(np.zeros((4, 3)))
    series_table(TABLE)
    hole = np.zeros(3, dtype=np.int64)
    traj = desurvey(Collars(hole[:1], np.array([0.5]), np.array([1.0]), np.array([100.0]), np.array([90.0])),
                    Surveys(hole, np.array([0.0, 40.0, 80.0]), np.array([10.0, 20.0, 30.0]),
                            np.array([-88.0, -85.0, -80.0])))  # fmt: skip
    arcs.hole_bounds(traj, hole[:1], 0.01)
    offsets, _xyz, md = arcs.densify(traj, hole[:1], 0.01, np.zeros(3))
    traj.positions(np.repeat(hole[:1], np.diff(offsets)), md)


class _Svc:
    def __init__(self, project: Any) -> None:
        self.project = project

    def require_current(self) -> Any:
        return self.project


def _project(folder: str, uid: str) -> Any:
    from types import SimpleNamespace

    from bf_core.db import dialect

    engine = dialect.create_engine(dialect.sqlite_url(str(Path(folder) / "project.sqlite")))
    return SimpleNamespace(engine=engine, uid=uid, folder=Path(folder), read_only=False)


def _ids(work: Path) -> tuple[str, int, int, int, str]:
    folder, sid, layer, shade, holes = (work / "ids.txt").read_text(encoding="utf-8").split("\n")
    return folder, int(sid), int(layer), int(shade), holes


def _plane(p: Any) -> str:
    return ",".join(repr(float(x)) for x in [*p.origin.tolist(), *p.v.tolist(), p.towards, p.away])


def _set_id(project: Any) -> int:
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import objects

    with dialect.read_tx(project.engine) as conn:
        return int(conn.execute(sa.select(objects.c.id).where(objects.c.kind == "drillhole_set")).scalar_one())


def router_snap(project: Any, set_id: int, plane: str | None) -> bytes:
    """`GET /viz/sets/{id}/snap?plane=` (no window: the whole set) in this process."""
    from bf_core.viz import router

    return bytes(router.snap_targets(_Svc(project), set_id, None, None, plane, METHOD).body)


def _points(params: dict[str, Any]) -> dict[str, Any]:
    import time

    import numpy as np

    from bf_core.sections.geometry import series_geometry

    n = int(params["points"])
    rng = np.random.default_rng(5)
    out: dict[str, Any] = {"points": n, "points_mismatches": 0}
    g, gaps = series_geometry(POINTS), series_geometry({**POINTS, "away": 0.1})
    tau = rng.uniform(float(g.lo[0]) - 2.0, float(g.hi[-1]) + 2.0, n)
    face = np.flatnonzero(rng.random(n) < 0.05)
    tau[face[::2]] = g.lo[rng.integers(0, g.count, len(face[::2]))]  # = hi of the section before: the next one
    tau[face[1::2]] = gaps.hi[rng.integers(0, g.count, len(face[1::2]))]  # the far face of a window with gaps
    u, w = rng.uniform(-1500.0, 1500.0, n), rng.uniform(-300.0, 300.0, n)
    xyz = np.empty((n, 3))
    for i in range(3):
        xyz[:, i] = g.p1[i] + u * g.s[i] + w * g.w[i] + tau * g.v[i]
    del u, w, tau
    bad = np.flatnonzero(rng.random(n) < 0.01)
    xyz[bad, rng.integers(0, 3, len(bad))] = np.nan
    for key, towards in (("points_s", 0.2), ("points_gaps_s", 0.1)):
        g = series_geometry({**POINTS, "away": towards})
        took = []
        for _ in range(REPEATS):
            a = time.perf_counter()
            got = g.section_of(xyz)
            took.append(time.perf_counter() - a)
        out[key] = round(float(np.median(took)), 4)
        t = g.depth(xyz)
        k = np.searchsorted(g.lo, t, side="right") - 1
        kk = np.clip(k, 0, g.count - 1)
        want = np.where((k >= 0) & (g.lo[kk] <= t) & (t < g.hi[kk]), kk, -1)
        out["points_mismatches"] += int(np.count_nonzero(got != want))
        out[f"{key[:-2]}_found"] = int(np.count_nonzero(got >= 0))
        del t, k, kk, want, got
    return out


def _table() -> dict[str, Any]:
    import time

    import numpy as np

    from bf_core.sections.names import series_table

    took = []
    for _ in range(REPEATS):
        a = time.perf_counter()
        t = series_table(TABLE)
        took.append(time.perf_counter() - a)
    assert t["count"] == 5000 and len({s["name"] for s in t["sections"]}) == 5000
    return {"table_s": round(float(np.median(took)), 4)}


def _crossing(contours: int, vertices: int) -> tuple[float, int]:
    """auto_links of crossing ellipses (as `tests/test_section_links.py`): the median of 5, the pairs."""
    import time

    import numpy as np

    from bf_core.sections.links import auto_links

    rng = np.random.default_rng(11)
    a = np.linspace(0.0, 2.0 * np.pi, vertices, endpoint=False)
    polys, ks = [], []
    for k in range(contours // 5):
        ru, rw, start = (30.0, 5.0, 0.0) if k % 2 == 0 else (5.0, 30.0, 0.5 * np.pi)
        for j in range(5):
            r = 1.0 + rng.normal(0.0, 0.003, vertices)
            u = 80.0 * j + 0.5 * (k % 3) + ru * r * np.cos(a + start)
            polys.append(np.column_stack([u, 0.5 * (k % 2) + rw * r * np.sin(a + start)]) + [512_000.0, 300.0])
            ks.append(k)
    uw, offsets = np.vstack(polys), np.concatenate([[0], np.cumsum([len(p) for p in polys])]).astype(np.int64)
    took, pairs, how = [], None, None
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        pairs, how = auto_links(uw, offsets, np.array(ks), 5.0)
        took.append(time.perf_counter() - t0)
    assert pairs is not None and how is not None
    return float(np.median(took)), int(len(pairs)) if (how == 1).all() else -1


def _expected_links(contours: int) -> tuple[int, list[int]]:
    _k, bodies = _layout(contours)
    by = {b: len(ks) for b, _cu, ks in bodies}
    chains = sum(n - 1 for n in by.values()) + 2  # + the branch 1 → 2
    return chains, [by[0], by[1], by[2], by[3] + by[4] + by[5]]


def _series_views(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time

    import numpy as np

    from bf_core.sections import contours_router as cr
    from bf_core.viz import binary

    folder, sid, _layer, shade, _holes = _ids(work)
    project = _project(folder, "bench-series")
    svc = _Svc(project)
    out: dict[str, Any] = {}
    try:
        a = time.perf_counter()
        table = cr.series_links(svc, sid, None)
        out["links_first_s"] = round(time.perf_counter() - a, 4)
        took = []
        for _ in range(REPEATS):
            a = time.perf_counter()
            again = cr.series_links(svc, sid, None)
            took.append(time.perf_counter() - a)
        out["links_s"] = round(float(np.median(took)), 4)
        links, sizes = _expected_links(int(params["contours"]))
        wrong = int(again != table) + int(len(table["links"]) != links) + int(len(table["conflicts"]) != 0)
        wrong += int(sorted(len(b) for b in table["bodies"]) != sorted(sizes))
        cross_s, cross = _crossing(int(params["contours"]), int(params["vertices"]))
        out["links_cross_s"] = round(cross_s, 4)
        out["links_wrong"] = wrong + int(cross != int(params["contours"]) - 5)  # 5 a section, k ↔ k + 1
        out["links"] = len(table["links"])

        k_count = int(table["count"])
        per = np.bincount(np.arange(int(params["shadow"])) % k_count, minlength=k_count)
        a = time.perf_counter()
        cr.series_contours(svc, sid, "0,2", str(shade), None)
        out["shadow_first_s"] = round(time.perf_counter() - a, 4)
        took, wrong = [], 0
        for k in range(1, k_count - 1):
            a = time.perf_counter()
            r = cr.series_contours(svc, sid, f"{k - 1},{k + 1}", str(shade), None)
            took.append(time.perf_counter() - a)
            head, arrays = binary.decode(r.body)
            sections = arrays["section"].astype(np.int64)
            wrong += int(head["lines"] != per[k - 1] + per[k + 1] or head["points"] != SHADOW_VERTICES * head["lines"]
                         or not np.isin(sections, [k - 1, k + 1]).all())  # fmt: skip
        out["shadow_s"] = round(max(took), 4)
        out["shadow_median_s"] = round(float(np.median(took)), 4)
        out["shadow_wrong"] = wrong
    finally:
        project.engine.dispose()
    return out


def _stitch(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import shutil
    import time

    import numpy as np

    from bf_core.db import dialect
    from bf_core.geo.meshes import edge_summary
    from bf_core.ops.warm import run_step
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes import store as ws

    folder, sid, _layer, _shade, _holes = _ids(work)
    copy = work / f"op-{os.getpid()}"
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(folder, copy)
    try:
        a = time.perf_counter()
        result = run_step(copy, "stitch_series", {"series_id": sid}, job_id=1)
        took = time.perf_counter() - a
        engine = dialect.create_engine(dialect.sqlite_url(str(copy / "project.sqlite")))
        try:
            with dialect.read_tx(engine) as conn:
                meshes = [ws.read_wireframe(conn, BlobStore(copy), i) for i in result["wireframe_ids"]]
        finally:
            engine.dispose()
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    _links, sizes = _expected_links(int(params["contours"]))
    wrong = int([b["contours"] for b in result["report"]["bodies"]] != sizes)
    wrong += sum(sum(edge_summary(t, len(v))) for v, t in meshes)
    # the bodies without a branch: the trapezoids between neighbours and the caps of Δ/2 at both ends, Δ·ΣA
    rings, where = _rings(int(params["contours"]), int(params["vertices"]))
    error = 0.0
    for body in range(3):
        areas = [abs(_area(uw)) for uw, (b, _k) in zip(rings, where, strict=True) if b == body]
        want = float(SERIES["step"]) * sum(areas)
        error = max(error, abs(float(result["volumes"][body]) - want) / want)
    return {
        "stitch_series_s": round(took, 3), "bodies": len(meshes), "bodies_wrong": wrong,
        "volume_error": float(f"{error:.2e}"), "triangles": int(np.sum([len(t) for _v, t in meshes])),
        "stitch_warnings": len(result.get("warnings") or []),
    }  # fmt: skip


def _area(uw: Any) -> float:
    x, y = uw[:, 0] - uw[0, 0], uw[:, 1] - uw[0, 1]
    return 0.5 * float(x @ (y[[*range(1, len(y)), 0]]) - y @ (x[[*range(1, len(x)), 0]]))


def _snap(work: Path) -> dict[str, Any]:
    import time

    import numpy as np

    from bf_core.sections.geometry import series_geometry
    from bf_core.viz import binary
    from bf_core.viz.snap import _inside, plane_window

    project = _project(_ids(work)[4], "bench-holes")
    g = series_geometry(SNAP)
    texts = [_plane(g.plane(k)) for k in range(g.count)]
    try:
        set_id = _set_id(project)
        a = time.perf_counter()
        router_snap(project, set_id, texts[g.count // 2])
        first = time.perf_counter() - a
        took, answers = [], []
        for text in texts:
            a = time.perf_counter()
            answers.append(router_snap(project, set_id, text))
            took.append(time.perf_counter() - a)
        full_head, full = binary.decode(router_snap(project, set_id, None))  # the reference: the whole set
    finally:
        project.engine.dispose()

    def targets(head: dict[str, Any], arr: dict[str, Any], keep: Any = None) -> set[tuple[str, float, bytes]]:
        names = np.array(head["names"])[arr["bound_hole"].astype(np.int64)]
        bounds, depths = arr["bounds"].reshape(-1, 3), arr["bound_depth"]
        if keep is not None:
            names, bounds, depths = names[keep], bounds[keep], depths[keep]
        return {(str(n), float(d), p.tobytes()) for n, d, p in zip(names, depths, bounds, strict=True)}

    missed, holes = 0, []
    for text, body in zip(texts, answers, strict=True):
        head, arr = binary.decode(body)
        keep = _inside(full["bounds"].reshape(-1, 3), plane_window(text).planes)
        missed += len(targets(full_head, full, keep) ^ targets(head, arr)) + int(head["truncated"])
        holes.append(int(head["holes"]))
    return {"snap_first_s": round(first, 4), "snap_s": round(max(took), 4),
            "snap_median_s": round(float(np.median(took)), 4), "snap_missed": missed, "snap_holes_min": min(holes),
            "snap_bounds": int(len(full["bound_depth"])), "snap_truncated": int(full_head["truncated"])}  # fmt: skip


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    out = _stitch(params, work)  # first: the copy of the project before any engine of this process opens it
    out |= _points(params)
    out |= _table()
    out |= _series_views(params, work)
    out |= _snap(work)
    return {"contours": int(params["contours"]), "vertices": int(params["vertices"]), **out}
