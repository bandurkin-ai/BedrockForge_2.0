"""Exact queries of the 3D window on a stored wireframe: the lines of a section and the triangle under a click
(ADR 0013 «Замеры»: «Сечение: первое / шаг — full ≤ 1 с / ≤ 300 мс», «Точный выбор ≤ 50 мс»; ТЗ 5.3, 7.14, 7.21).

Two wireframes in a project database (`tools/gen/wireframes.py`): a pit surface with 90 % of the triangles and a
closed ore body under it. The requests go through `wireframes/exact.py`, as the endpoints make them, in a fresh
process:
  section_first_s  the first section — a vertical plane at 63° to the grid through both wireframes: their
                   geometry is read from the blobs, then cut (`GET /viz/wireframes/{id}/section` for each);
  section_step_s   the slowest of 20 next sections, the plane moved by 20 m each time (a step of the section);
  pick_first_s     the first click on a wireframe after its geometry was dropped (`POST /wireframes/pick`):
                   the blobs again and the finer part table — the slower of the two wireframes (the surface
                   from above, the ore body from below);
  pick_s           the slowest of 250 next clicks: 100 rays straight down, 100 from a camera at an angle, 50
                   that graze the relief (they pass over many parts);
  pick_median_s    their median.
What is checked against the surface the wireframe was made from (`Surface.height`, float64):
  section_error_m  the largest distance of a point of a section from its plane and from the surface; the
                   surface gives one open line from border to border, the ore body — a closed one;
  pick_error_m     the largest distance of a picked point from the surface (for a ray straight down — from the
                   point under the ray), and no ray passes under the surface before its hit; the click from
                   below is answered by the ore body, on its ellipsoid.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

AZIMUTH = 63.0
STEPS, STEP_M = 20, 20.0  # (a smaller surface is stepped through by a third of its side)
DOWN, CAMERA, GRAZING = 100, 100, 50


def prepare(params: dict[str, Any], work: Path) -> None:
    from tools.gen.wireframes import body_of, ore_body, pit_surface

    from bf_core.db import dialect
    from bf_core.projects.project import create_folder
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes import store as wf_store

    n = int(params["triangles"])
    surface = pit_surface(round(0.9 * n))
    surface.save(work / "surface.npz")
    folder = create_folder(work, "Точные запросы", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    ids = []
    try:
        blobs = BlobStore(folder)
        for name, (v, t) in (
            ("Рельеф карьера", surface.mesh()),
            ("Рудное тело", ore_body(round(0.1 * n), **body_of(surface, 0))),
        ):
            staged = wf_store.stage_wireframe(blobs, v, t)
            try:
                with dialect.write_tx(engine) as conn:
                    ids.append(wf_store.commit_wireframe(conn, blobs, staged, name=name))
            finally:
                staged.discard()
            del v, t, staged
    finally:
        engine.dispose()
    (work / "folder.txt").write_text(f"{folder}\n{ids[0]}\n{ids[1]}", encoding="utf-8")
    load()  # compile into the cache outside the timing


def load() -> None:
    import numpy as np
    import pyarrow  # noqa: F401

    import bf_core.wireframes.exact  # noqa: F401
    from bf_core.geo import pick, section

    x, y = np.meshgrid(np.arange(5.0), np.arange(5.0))
    v = np.column_stack([x.ravel(), y.ravel(), np.sin(x.ravel()) + 0.3 * y.ravel()])
    i = (np.arange(4)[:, None] * 5 + np.arange(4)[None, :]).ravel()
    t = np.concatenate([np.column_stack([i, i + 1, i + 6]), np.column_stack([i, i + 6, i + 5])]).astype(np.int32)
    cut = section.plane_section(v, t, [2.2, 2.2, 0.0], [1.0, 0.3, 0.0], clip=[[0.0, 1.0, 0.0, -0.5]])
    assert len(cut) == 1
    fine = pick.fine_parts(v, t, None, size=8)
    assert pick.ray_pick(v, t, [2.2, 2.2, 9.0], [0.0, 0.0, -1.0], parts=fine, clip=[[0.0, 1.0, 0.0, -0.5]])


def _rays(surface: Any, seed: int = 5) -> tuple[Any, Any, Any]:
    """(origins, directions, kinds) of the clicks: straight down, from a camera, grazing the relief."""
    import numpy as np

    rng = np.random.default_rng(seed)
    sx, sy = surface.side
    ox, oy, oz = surface.origin
    n = DOWN + CAMERA + GRAZING
    x, y = ox + rng.uniform(0.02, 0.98, n) * sx, oy + rng.uniform(0.02, 0.98, n) * sy
    target = np.column_stack([x, y, surface.height(x, y)])
    origin = np.column_stack([x, y, np.full(n, oz + 1000.0)])
    camera = np.array([ox + 0.5 * sx, oy - sy, oz + 1.5 * max(sx, sy)])  # high: no ray comes under a border
    origin[DOWN : DOWN + CAMERA] = camera
    # grazing: from above a border of the surface, two degrees below the horizon, along and across the grid
    side = rng.integers(0, 4, GRAZING)
    start = np.column_stack([np.where(side == 0, ox, np.where(side == 1, ox + sx, x[-GRAZING:])),
                             np.where(side == 2, oy, np.where(side == 3, oy + sy, y[-GRAZING:])),
                             np.full(GRAZING, oz + 75.0)])  # fmt: skip
    across = target[-GRAZING:, :2] - start[:, :2]
    reach = np.linalg.norm(across, axis=1)
    target[-GRAZING:, 2] = start[:, 2] - reach * np.tan(np.radians(2.0))
    origin[-GRAZING:] = start
    kinds = np.repeat([0, 1, 2], [DOWN, CAMERA, GRAZING])
    return origin, target - origin, kinds


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import statistics
    import time
    from types import SimpleNamespace

    import numpy as np
    from tools.gen.wireframes import Surface, body_distance, body_of

    from bf_core.db import dialect
    from bf_core.viz import binary
    from bf_core.wireframes import exact

    folder_text, surface_id, body_id = (work / "folder.txt").read_text(encoding="utf-8").split("\n")
    folder, ids = Path(folder_text), [int(surface_id), int(body_id)]
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    project = SimpleNamespace(engine=engine, uid="bench", folder=folder)
    surface = Surface.load(work / "surface.npz")
    sx, sy = surface.side
    x0, y0 = surface.origin[0] + 0.5 * sx, surface.origin[1] + 0.5 * sy
    origins, directions, kinds = _rays(surface)
    try:
        exact.forget()

        def sections(offset: float) -> tuple[float, list[bytes]]:
            a = time.perf_counter()
            with dialect.read_tx(engine) as conn:
                states = [s for s, _stats in exact.set_targets(conn, ids)]
            plane = exact.parse_plane(x0=repr(x0), y0=repr(y0), az=repr(AZIMUTH), offset=repr(offset))
            bodies = [exact.section(project, s, plane) for s in states]
            return time.perf_counter() - a, bodies

        step = min(STEP_M, 0.3 * min(sx, sy) / STEPS)
        first_s, first = sections(0.0)
        steps = [sections(step * k) for k in range(1, STEPS + 1)]

        exact.forget()
        body = body_of(surface, 0)
        under = np.asarray(body["centre"]) - [0.0, 0.0, 1000.0]
        cold = []
        for o, d in ((origins[0], directions[0]), (under, np.array([0.0, 0.0, 1.0]))):
            a = time.perf_counter()
            first_hit = exact.pick(project, o, d, ids)["hit"]
            cold.append(time.perf_counter() - a)
        pick_first_s = max(cold)
        assert first_hit is not None and first_hit["wireframe_id"] == ids[1], first_hit
        shell = float(body_distance([first_hit["point"]], **body)[0])
        assert 0.98 < shell <= 1.0 + 1e-9, shell  # on a facet of the ore body: inside its ellipsoid, never outside
        picks, times = [], []
        for o, d in zip(origins, directions, strict=True):
            a = time.perf_counter()
            picks.append(exact.pick(project, o, d, ids)["hit"])
            times.append(time.perf_counter() - a)
        cache = exact.cache_info()
    finally:
        exact.forget()
        engine.dispose()

    # the sections against their plane and the surface
    section_error = 0.0
    lines = closed_lines = points = 0
    for _seconds, bodies in [(first_s, first), *steps]:
        for wf_id, body in zip(ids, bodies, strict=True):
            head, arrays = binary.decode(body)
            xyz, closed = arrays["xyz"].reshape(-1, 3), arrays["closed"].astype(bool)
            if not len(xyz):
                assert wf_id == ids[1], "the plane passes through the whole surface"
                continue
            off_plane = (xyz - np.asarray(head["point"])) @ np.asarray(head["normal"])
            section_error = max(section_error, float(np.abs(off_plane).max()))
            if wf_id == ids[0]:  # a surface over the plan: one open line from border to border, on the surface
                assert head["lines"] == 1 and not closed.any(), head
                section_error = max(section_error, float(np.abs(surface.height(xyz[:, 0], xyz[:, 1]) - xyz[:, 2]).max()))
            else:
                assert closed.all(), head  # a closed solid is cut along closed lines
            lines += int(head["lines"])
            closed_lines += int(closed.sum())
            points += len(xyz)

    # the picks against the surface
    pick_error = 0.0
    hits = 0
    for o, d, kind, hit in zip(origins, directions, kinds, picks, strict=True):
        unit = d / np.linalg.norm(d)
        if hit is None:
            assert kind == 2, "a ray aimed at the surface must hit it"
            reach = 2.0 * float(np.hypot(sx, sy))
        else:
            hits += 1
            p = np.asarray(hit["point"])
            assert hit["wireframe_id"] == ids[0], hit
            pick_error = max(pick_error, abs(float(surface.height(p[0], p[1])) - p[2]))
            if kind == 0:
                pick_error = max(pick_error, abs(hit["distance"] - (o[2] - float(surface.height(o[0], o[1])))))
            reach = float(hit["distance"])
        along = np.arange(0.0, reach - 0.25, 0.5)  # the ray is not under the surface before its hit
        way = o + along[:, None] * unit
        clear = way[:, 2] - surface.height(way[:, 0], way[:, 1])
        clear = clear[~np.isnan(clear)]  # (outside the surface there is nothing to be under)
        assert not len(clear) or float(clear.min()) >= -1e-6, (kind, float(clear.min()))

    assert hits >= DOWN + CAMERA and closed_lines >= 1
    assert section_error <= 1e-8 and pick_error <= 1e-8, (section_error, pick_error)
    return {
        "triangles": int(params["triangles"]),
        "section_first_s": round(first_s, 4),
        "section_step_s": round(max(s for s, _b in steps), 4),
        "section_step_median_s": round(statistics.median(s for s, _b in steps), 4),
        "section_lines": lines,
        "closed_lines": closed_lines,
        "section_points": points,
        "section_error_m": section_error,
        "pick_first_s": round(pick_first_s, 4),
        "pick_s": round(max(times), 5),
        "pick_median_s": round(statistics.median(times), 5),
        "picks": len(picks),
        "hits": hits,
        "pick_error_m": pick_error,
        "cache_mb": round(cache["bytes"] / 1e6, 1),
    }
