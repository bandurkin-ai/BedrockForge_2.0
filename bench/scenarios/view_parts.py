"""3D data of a drillhole set through the project database (ADR 0007 «Замеры»; ТЗ 5.3 «открытие ≤ 5 с до
первого кадра», 50 000 стволов).

Two stages of opening the 3D window in a fresh process (nothing cached in memory):
  paths  — the hole manifest and the paths of every part (trajectories from collars and surveys in the
           database, parts, arcs within 1 cm, BFA1): `paths_s`, answers `manifest_mb` and `paths_mb`;
  field  — one number field of the assay table for every part (the column snapshot from its Parquet cache, as
           on a reopened project; the index of rows by hole; BFA1): `field_s`.
`field_db_s` — the same field the first time after the import (the snapshot from the database, Parquet
written), measured once in `prepare`: reported, not budgeted (`drillholes/snapshot.py`).
Holes cover 20 km × 6 km at 50 m (parts of 2000 holes and ≤ 16 km), 20 survey stations with drift.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

METHOD = "minimum_curvature"
SURVEYS = 20
COLUMNS = 400  # holes per row of the grid: 20 km at 50 m


def prepare(params: dict[str, Any], work: Path) -> None:
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

    holes, rows = int(params["holes"]), int(params["rows"])
    rng = np.random.default_rng(17)
    folder = create_folder(work, "Вид", {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        names = [f"H{i:06d}" for i in range(holes)]
        per = rows // holes
        length = per * 2.0
        az0 = rng.uniform(0, 360, holes)
        with dialect.write_tx(engine) as conn:
            set_id = create_set(conn, "Набор")
            replace_collars(
                conn,
                set_id,
                [
                    CollarRow(
                        n,
                        400_000 + (i % COLUMNS) * 50.0 + rng.uniform(-5, 5),
                        7_000_000 + (i // COLUMNS) * 50.0 + rng.uniform(-5, 5),
                        300.0 + rng.uniform(-20, 20),
                        length,
                        float(az0[i]),
                        -60.0,
                        {"status": "Проектная"} if i % 50 == 0 else {},
                    )
                    for i, n in enumerate(names)
                ],
            )
        depth = np.linspace(0, length, SURVEYS)
        az = (az0[:, None] + rng.normal(0, 1.5, (holes, SURVEYS)).cumsum(axis=1)) % 360
        dip = np.clip(-60.0 + rng.normal(0, 1.0, (holes, SURVEYS)).cumsum(axis=1), -89.0, -5.0)
        surveys = [
            (n, float(d), float(a), float(p))
            for n, da, dp in zip(names, az.tolist(), dip.tolist(), strict=True)
            for d, a, p in zip(depth.tolist(), da, dp, strict=True)
        ]
        replace_surveys(engine, set_id, surveys, lambda _f, _m: None)
        hole_of = np.repeat(np.arange(holes), per)
        f = np.tile(np.arange(per), holes).astype(float) * 2.0
        au = rng.lognormal(-1, 1, len(f))
        au[rng.random(len(f)) < 0.01] = np.nan
        data = TableData(
            "Пробы",
            "assay",
            [names[i] for i in hole_of.tolist()],
            f,
            f + 2.0,
            [
                ColumnSpec("AU", "number", "g/t", "component"),
                ColumnSpec("CU", "number", "%", "component"),
                ColumnSpec("Номер пробы", "text", None, "sample"),
            ],
            [au, rng.lognormal(-2, 0.5, len(f)), [f"S{i:08d}" for i in range(len(f))]],
        )
        write_table(engine, set_id, data, job_id=None, tick=lambda _f, _m: None)
    finally:
        engine.dispose()
    (work / "folder.txt").write_text(str(folder), encoding="utf-8")
    load()  # compile the kernels into the cache outside the timing
    import json
    import time

    with _open(work) as (project, state, parts):
        t0 = time.perf_counter()
        _field(project, state, parts)  # builds the snapshot from the database and writes its Parquet file
        cold = round(time.perf_counter() - t0, 3)
    (work / "prepare.json").write_text(json.dumps({"field_db_s": cold}))


@contextmanager
def _open(work: Path) -> Iterator[tuple[Any, Any, list[dict[str, Any]]]]:
    """The project of the scenario, the state of its set and its parts (from the manifest, built here)."""
    from types import SimpleNamespace

    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import objects
    from bf_core.drillholes.snapshot import snapshots
    from bf_core.viz import holes

    folder = Path((work / "folder.txt").read_text(encoding="utf-8"))
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    project = SimpleNamespace(engine=engine, uid="bench", folder=folder)
    try:
        from bf_core.viz import binary
        from bf_core.viz.keys import set_state

        with dialect.read_tx(engine) as conn:
            state = set_state(conn, conn.execute(sa.select(objects.c.id)).scalar_one())
        head = holes.manifest(project, state, METHOD)
        project.manifest = head
        yield project, state, binary.decode(head)[0]["parts"]
    finally:
        snapshots.forget()
        holes.forget()
        engine.dispose()


def _field(project: Any, state: Any, parts: list[dict[str, Any]]) -> tuple[int, int]:
    from bf_core.db import dialect
    from bf_core.viz import binary
    from bf_core.viz.intervals import part_intervals
    from bf_core.viz.keys import fields_of, table_by_key

    with dialect.read_tx(project.engine) as conn:
        table = table_by_key(conn, state.set_id, "Пробы")
        refs = fields_of(conn, table, ["AU"])
    size = rows = 0
    for p in parts:
        body = part_intervals(project, state, METHOD, table, refs, p["k"])
        size += len(body)
        rows += binary.decode(body)[0]["rows"]
    return size, rows


def load() -> None:
    import numpy as np

    import bf_core.drillholes.router  # noqa: F401 - the API has it loaded (hole kinds, snapshots)
    import bf_core.viz.holes  # noqa: F401
    import bf_core.viz.intervals  # noqa: F401
    from bf_core.drillholes.desurvey import Collars, Surveys, desurvey
    from bf_core.viz.arcs import densify, hole_bounds

    one = np.zeros(1)
    hole = np.array([0, 0], dtype=np.int64)
    traj = desurvey(
        Collars(hole[:1], one, one, one, one + 50.0),
        Surveys(hole, np.array([0.0, 30.0]), np.zeros(2), -np.array([60.0, 50.0])),
    )
    hole_bounds(traj, hole[:1])
    densify(traj, hole[:1], 0.01, np.zeros(3))


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import json
    import time

    from bf_core.viz import binary
    from bf_core.viz.paths import part_paths

    out: dict[str, Any] = {}
    t0 = time.perf_counter()
    with _open(work) as (project, state, parts):
        paths = [part_paths(project, state, METHOD, 0.01, p["k"]) for p in parts]
        out["paths_s"] = round(time.perf_counter() - t0, 3)
        out["parts"] = len(parts)
        out["holes"] = sum(p["n"] for p in parts)
        out["points"] = sum(binary.decode(p)[0]["points"] for p in paths)
        out["manifest_mb"] = round(len(project.manifest) / 2**20, 2)
        out["paths_mb"] = round(sum(len(p) for p in paths) / 2**20, 2)
        del paths
        t0 = time.perf_counter()
        size, rows = _field(project, state, parts)
        out["field_s"] = round(time.perf_counter() - t0, 3)
        out["rows"] = rows
        out["field_mb"] = round(size / 2**20, 2)
    out.update(json.loads((work / "prepare.json").read_text()))
    return out
