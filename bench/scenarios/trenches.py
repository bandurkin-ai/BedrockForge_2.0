"""Trenches along polylines (ТЗ 7.35 Д-16 «10 000 канав по 50 вершин ≤ 10 с»; ADR 0011 «Замеры»).

A Micromine STR with one string per trench (the name of the trench is its STRING) → the job `import.trenches`
into a new set of a project database: read the strings, name the trenches, depth of every vertex by 3D length,
collars and stations with coordinates in one transaction, the source file into the project storage. Then the
trajectories of the set are built from the database, and the largest distance between a vertex of the file and
the trajectory at its depth is reported (`max_error_m`: the trajectory goes through the vertices exactly).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any


def _vertices(trenches: int, vertices: int) -> Any:
    """Polylines on UTM coordinates: steps of 1–4 m that turn a little, down a slope."""
    import numpy as np

    rng = np.random.default_rng(16)
    start = np.column_stack(
        [
            400_000.0 + rng.uniform(0.0, 20_000.0, trenches),
            4_600_000.0 + rng.uniform(0.0, 20_000.0, trenches),
            rng.uniform(800.0, 1500.0, trenches),
        ]
    )
    turn = rng.normal(0.0, 0.15, (trenches, vertices - 1)).cumsum(axis=1)
    azimuth = rng.uniform(0.0, 2.0 * np.pi, (trenches, 1)) + turn
    step = rng.uniform(1.0, 4.0, (trenches, vertices - 1))
    delta = np.stack([step * np.sin(azimuth), step * np.cos(azimuth), rng.normal(-0.2, 0.3, step.shape)], axis=2)
    xyz = np.concatenate([start[:, None, :], start[:, None, :] + delta.cumsum(axis=1)], axis=1)
    return np.round(xyz, 3)  # millimetres, as a survey gives them


def prepare(params: dict[str, Any], work: Path) -> None:
    import numpy as np

    from bf_core.formats.lineset import LineSet
    from bf_core.formats.str_micromine import write_str
    from bf_core.projects.project import create_folder

    n, k = int(params["trenches"]), int(params["vertices"])
    xyz = _vertices(n, k)
    np.save(work / "xyz.npy", xyz)
    offsets = np.array([0, k], dtype=np.int64)
    closed = np.zeros(1, dtype=np.uint8)
    seq = np.arange(n * k, dtype=np.uint32).reshape(n, k)
    write_str(work / "trenches.str", [LineSet(f"TR-{i:05d}", xyz[i], offsets, closed, seq[i]) for i in range(n)])
    folder = create_folder(work, "Канавы", {})
    (work / "folder.txt").write_text(str(folder), encoding="utf-8")
    load()  # compile into the cache outside the timing


def load() -> None:
    import numpy as np
    import pyarrow  # noqa: F401

    import bf_core.drillholes.trajectory  # noqa: F401
    import bf_core.formats.str_micromine  # noqa: F401
    import bf_core.importing.jobs  # noqa: F401
    import bf_core.importing.router  # noqa: F401
    import bf_core.storage.blobs  # noqa: F401
    from bf_core.drillholes.desurvey import Collars, Surveys, desurvey
    from bf_core.importing.trench_lines import Polylines, build
    from bf_core.importing.trenches import import_trenches  # noqa: F401

    xyz = np.array([[0.0, 0.0, 0.0], [3.0, 4.0, 0.0], [3.0, 4.0, -12.0]])
    t = build(Polylines("micromine_str", ["T"], np.array([0, 3], dtype=np.int64), xyz))
    hole = np.zeros(3, dtype=np.int64)
    none = np.full(3, np.nan)
    one = np.zeros(1)
    traj = desurvey(Collars(hole[:1], one, one, one, t.depth[-1:]), Surveys(hole, t.depth, none, none, *xyz.T.copy()))
    traj.positions(hole, t.depth)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time
    from types import SimpleNamespace

    import numpy as np

    from bf_core.db import dialect
    from bf_core.drillholes.trajectory import trajectories
    from bf_core.importing.trenches import import_trenches
    from bf_core.jobs.context import JobContext

    folder = Path((work / "folder.txt").read_text(encoding="utf-8"))
    stamp = time.time_ns() // 1_000_000 % 1_000_000_000  # a repeated run is a new job into a new set
    job = {"id": stamp, "params": {"set_name": f"Канавы {stamp}", "lines": {"path": str(work / "trenches.str")}},
           "project_folder": str(folder)}  # fmt: skip
    ctx = JobContext(job, lambda _message: None, threading.Event())
    t0 = time.perf_counter()
    try:
        result = import_trenches(ctx)
    finally:
        ctx.close()
    t1 = time.perf_counter()

    xyz = np.load(work / "xyz.npy")
    n, k = xyz.shape[:2]
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        st = trajectories(SimpleNamespace(engine=engine, uid=f"bench-{stamp}"), result["set_id"])
    finally:
        engine.dispose()
    assert result["trenches"] == n and result["vertices"] == n * k and len(st.skipped) == 0
    assert st.names == [f"TR-{i:05d}" for i in range(n)]
    # every trench has 2k - 2 stations: a vertex between two segments is listed twice
    md = st.traj.md.reshape(n, 2 * k - 2)
    depth = np.concatenate([md[:, :1], md[:, 1::2]], axis=1)
    at = st.traj.positions(np.repeat(np.arange(n), k), depth.ravel())
    error = float(np.abs(at - xyz.reshape(-1, 3)).max())
    assert error <= 1e-9, error
    return {
        "trenches": result["trenches"],
        "vertices": result["vertices"],
        "length_m": round(result["length"], 3),
        "import_s": round(t1 - t0, 3),
        "paths_s": round(time.perf_counter() - t1, 3),
        "max_error_m": error,
    }
