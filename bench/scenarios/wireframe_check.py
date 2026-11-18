"""What the job `wireframe.check` does with an imported wireframe, and what the 3D window then reads (ADR 0013
«Замеры»: «`wireframe.check`: LOD, самопересечения — по первому замеру»; «Первые данные кадра: манифест и все
части LOD 2 (кеш готов) ≤ 3 с; все части LOD 0 ≤ 15 с»; ТЗ 5.3, 7.21).

One wireframe in a project database (`tools/gen/wireframes.py`): a pit surface — a relief with noise, where
nothing is flat, cut by pits whose level berms and plane faces are a third of its cells (the flat regions of
`geo/flat.py`, 8.12) — and a fault sheet that cuts through it (6 % of the triangles): self-intersections all
along the fault. In a fresh process:
  lod_s          the geometry from its blobs and the levels 1/8 and 1/64 of every part into `cache\\mesh\\`
                 (`viz/meshes.py::build_cache`: the flat regions first, then pyfqmr);
  check_s        `geo/checks.py::run_checks`: the consistency of the normals and the self-intersections;
  first_frame_s  then, as the 3D window opens the wireframe: the manifest and every part at level 1/64;
  full_s         every part at level 0 (from the blobs).
What is checked:
  lod1_error_m, lod2_error_m  how far, by height, a vertex of the levels 1/8 and 1/64 is from the surface the
                 wireframe was made from (`Surface.height`): benches of 15 m cannot be kept by triangles of
                 16 m, but a vertex that flies off the surface (the flat regions before 8.12: 40 m) is seen;
  lod1_share, lod2_share  the triangles of the levels against all of them: 1/8 and 1/64 are asked for, the
                 borders of the parts stay bit for bit and take their share (8.12 got ≤ 1/6 and ≤ 1/40 on a
                 grid; a pit with relief in parts of 64 000 triangles — about 1/8 and 1/35);
  every triangle of the fault that has corners on both sides of the surface is found crossing it
  (`straddling` ≤ `crossing`), and the normals are consistent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

FAULT_SHARE = 0.06
FAULT_AZIMUTH = 63.0


def _along_across(xy: Any) -> tuple[Any, Any]:
    """Plan coordinates along the strike of the fault and across it."""
    import math

    a = math.radians(FAULT_AZIMUTH)
    return xy[:, 0] * math.sin(a) + xy[:, 1] * math.cos(a), xy[:, 0] * math.cos(a) - xy[:, 1] * math.sin(a)


def prepare(params: dict[str, Any], work: Path) -> None:
    import numpy as np
    from tools.gen.wireframes import fault, pit_surface

    from bf_core.db import dialect
    from bf_core.projects.project import create_folder
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes import store as wf_store

    n = int(params["triangles"])
    surface = pit_surface(round((1.0 - FAULT_SHARE) * n))
    v, t = surface.mesh()
    fv, ft = fault(surface, round(FAULT_SHARE * n), azimuth=FAULT_AZIMUTH)
    along, across = _along_across(fv)
    np.save(work / "fault.npy", np.array([along.min(), along.max(), across.min(), across.max()]))  # its plan
    above = fv[:, 2] - surface.height(fv[:, 0], fv[:, 1])
    side = above[ft] >= 0.0
    straddling = int(np.count_nonzero(side.any(axis=1) & ~side.all(axis=1)))
    surface.save(work / "surface.npz")
    xyz, tri = np.concatenate([v, fv]), np.concatenate([t, ft + len(v)])
    del v, t, fv, ft
    folder = create_folder(work, "Проверка каркаса", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        blobs = BlobStore(folder)
        staged = wf_store.stage_wireframe(blobs, xyz, tri)
        try:
            with dialect.write_tx(engine) as conn:
                wf_id = wf_store.commit_wireframe(conn, blobs, staged, name="Карьер с разломом", color="#8a99a8")
        finally:
            staged.discard()
    finally:
        engine.dispose()
    (work / "folder.txt").write_text(f"{folder}\n{wf_id}\n{surface.triangles}\n{straddling}", encoding="utf-8")
    load()  # compile into the cache outside the timing


def load() -> None:
    import numpy as np
    import pyarrow  # noqa: F401
    import pyfqmr  # noqa: F401

    import bf_core.viz.meshes  # noqa: F401
    import bf_core.wireframes.store  # noqa: F401
    from bf_core.geo import checks, meshes

    x, y = np.meshgrid(np.arange(13.0), np.arange(13.0))
    v = np.column_stack([x.ravel(), y.ravel(), np.where(x.ravel() < 6, 0.0, np.sin(x.ravel()) + 0.3 * y.ravel())])
    i = (np.arange(12)[:, None] * 13 + np.arange(12)[None, :]).ravel()
    t = np.concatenate([np.column_stack([i, i + 1, i + 14]), np.column_stack([i, i + 14, i + 13])]).astype(np.int32)
    assert len(t) >= meshes.LOD_MIN_TRIANGLES
    meshes.levels(v, t)  # the kernels of the flat regions and pyfqmr
    wall = v[:, [0, 2, 1]] * [1.0, 1.0, 0.5] + [0.0, 6.0, -2.0]  # a sheet through the surface: every kernel of the scan
    checks.run_checks(np.concatenate([v, wall]), np.concatenate([t, t + len(v)]))


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time
    from types import SimpleNamespace

    import numpy as np
    from tools.gen.wireframes import Surface

    from bf_core.db import dialect
    from bf_core.geo import checks
    from bf_core.storage.blobs import BlobStore
    from bf_core.viz import binary
    from bf_core.viz import meshes as viz
    from bf_core.wireframes import store as wf_store

    folder_text, wf_text, surface_text, straddling_text = (work / "folder.txt").read_text(encoding="utf-8").split("\n")
    folder, wf_id = Path(folder_text), int(wf_text)
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    project = SimpleNamespace(engine=engine, uid="bench", folder=folder)
    try:
        t0 = time.perf_counter()
        with dialect.read_tx(engine) as conn:
            state = viz.mesh_state(conn, wf_id)
            v, t = wf_store.read_wireframe(conn, BlobStore(folder), wf_id)
        lod = viz.build_cache(folder, state.cache_key(), v, t, viz.parts_of(project, state))
        t1 = time.perf_counter()
        found = checks.run_checks(v, t)
        t2 = time.perf_counter()
        del v, t

        viz.forget()
        with dialect.read_tx(engine) as conn:
            state = viz.mesh_state(conn, wf_id)
        head = binary.decode(viz.manifest(project, state))[0]
        coarse = [viz.part(project, state, p["k"], 2) for p in head["parts"]]
        t3 = time.perf_counter()
        full_mb = sum(len(viz.part(project, state, p["k"], 0)) for p in head["parts"]) / 1e6
        t4 = time.perf_counter()
        middle = [viz.part(project, state, p["k"], 1) for p in head["parts"]]
    finally:
        engine.dispose()

    # the simplified levels against the surface, as the window gets them (away from the fault in plan: its
    # vertices are not the surface)
    surface = Surface.load(work / "surface.npz")
    a0, a1, c0, c1 = np.load(work / "fault.npy").tolist()
    errors = []
    for bodies in (middle, coarse):
        worst = 0.0
        for body in bodies:
            part_head, arrays = binary.decode(body)
            xyz = np.asarray(part_head["origin"]) + arrays["xyz"].reshape(-1, 3).astype(np.float64)
            along, across = _along_across(xyz)
            off = (along < a0 - 1.0) | (along > a1 + 1.0) | (across < c0 - 1.0) | (across > c1 + 1.0)
            if off.any():
                worst = max(worst, float(np.nanmax(np.abs(surface.height(xyz[off, 0], xyz[off, 1]) - xyz[off, 2]))))
        errors.append(worst)

    stats = found.stats
    counts = lod["triangles"]
    assert stats["state"] == "done" and stats["normal_conflicts"] == 0, stats
    assert counts[0] == state.triangles and counts[2] < counts[1] < counts[0], counts
    assert stats["pairs"] > 0 and stats["triangles"] >= int(straddling_text) > 0, (stats["triangles"], straddling_text)
    return {
        "triangles": state.triangles,
        "surface_triangles": int(surface_text),
        "parts": len(head["parts"]),
        "lod_triangles": counts,
        "lod1_share": round(counts[1] / counts[0], 4),
        "lod2_share": round(counts[2] / counts[0], 4),
        "lod_mb": round(lod["bytes"] / 1e6, 2),
        "lod_s": round(t1 - t0, 3),
        "check_s": round(t2 - t1, 3),
        "pairs": stats["pairs"],
        "crossing": stats["triangles"],
        "listed": int(len(found.triangles)),
        "straddling": int(straddling_text),
        "normal_conflicts": stats["normal_conflicts"],
        "first_frame_s": round(t3 - t2, 3),
        "full_s": round(t4 - t3, 3),
        "coarse_mb": round(sum(len(b) for b in coarse) / 1e6, 2),
        "full_mb": round(full_mb, 2),
        "lod1_error_m": round(errors[0], 3),
        "lod2_error_m": round(errors[1], 3),
    }
