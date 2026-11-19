"""Import of many small wireframes (ADR 0015 §8 and «Замеры»: «Импорт многих мелких каркасов — 3 000 каркасов в
DXF — ≤ 5 с (было 19 с)»; the debt of Э8: about 6 ms a wireframe, its blobs written one by one).

One ASCII DXF (`formats/dxf_write_mesh.py`) of `wireframes` closed boxes — 12 to 108 triangles, in UTM coordinates
float64 as they are, each a polyface mesh on a layer of its own — loaded by the job `import.objects` into a
project database: the reading, the spatial order, parts and measures of every wireframe, its blobs (the small
ones of a portion share one — a pack, `pack:<n>`, `wireframes/files.py`), the objects and the source file.
  import_s       the job, from the start to its result;
  read_back_s    the geometry of every wireframe read back from the project (`store.read_wireframe`);
What is checked: every wireframe is there, its vertices bit for bit the ones written and its triangles the same
(as sets of corners: the store keeps them in spatial order); `blobs` — rows of the storage after the import,
`packed` — wireframes whose geometry is an entry of a pack.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

X0, Y0, Z0 = 512_345.678_901, 6_234_567.123_456, 412.345_678


def _box(n: int, side: Any, at: Any) -> tuple[Any, Any]:
    """A closed box of `side` (3 lengths) with n × n cells a face, its corner at `at`; welded vertices."""
    import numpy as np

    s = np.linspace(0.0, 1.0, n + 1)
    a, b = (g.ravel() for g in np.meshgrid(s, s))
    i = (np.arange(n)[:, None] * (n + 1) + np.arange(n)[None, :]).ravel()
    quad = np.concatenate([np.column_stack([i, i + 1, i + n + 2]), np.column_stack([i, i + n + 2, i + n + 1])])
    zero, one = np.zeros_like(a), np.ones_like(a)
    faces_v, faces_t = [], []
    for axis, value, flip in ((2, zero, True), (2, one, False), (0, zero, True), (0, one, False),
                              (1, zero, False), (1, one, True)):  # fmt: skip
        cols = {0: (value, a, b), 1: (a, value, b), 2: (a, b, value)}[axis]
        faces_t.append((quad[:, ::-1] if flip else quad) + sum(len(p) for p in faces_v))
        faces_v.append(np.column_stack(cols))
    v, inverse = np.unique(np.round(np.concatenate(faces_v), 12), axis=0, return_inverse=True)
    t = inverse.reshape(-1)[np.concatenate(faces_t)]
    return v * side + at, t.astype(np.int32)


def meshes(count: int, seed: int = 17) -> list[Any]:
    import numpy as np

    from bf_core.formats.meshset import MeshSet

    rng = np.random.default_rng(seed)
    out = []
    for k in range(count):
        at = np.array([X0, Y0, Z0]) + rng.uniform([0.0, 0.0, -300.0], [5_000.0, 5_000.0, 300.0])
        v, t = _box(int(rng.integers(1, 4)), rng.uniform(2.0, 40.0, 3), at)
        out.append(MeshSet(f"Выработка {k + 1}", v, t))
    return out


def prepare(params: dict[str, Any], work: Path) -> None:
    from bf_core.formats.dxf_write_mesh import write_dxf_meshes
    from bf_core.projects.project import create_folder

    write_dxf_meshes(work / "выработки.dxf", meshes(int(params["wireframes"])))
    folder = create_folder(work, "Мелкие каркасы", {})
    (work / "folder.txt").write_text(str(folder), encoding="utf-8")
    load()
    small = work / "малый"
    small.mkdir()  # the kernels of the reader and of the store compiled into the cache: a few wireframes
    write_dxf_meshes(small / "выработки.dxf", meshes(30, seed=3))
    warm = create_folder(small, "Прогрев", {})
    _job(1, warm, {"files": [str(small / "выработки.dxf")]})


def load() -> None:
    import ezdxf  # noqa: F401
    import pyarrow  # noqa: F401

    import bf_core.formats.dxf_meshes  # noqa: F401
    import bf_core.importing.objects  # noqa: F401
    import bf_core.wireframes.store  # noqa: F401


def _job(job_id: int, folder: Path, params: dict[str, Any]) -> dict[str, Any]:
    from bf_core.jobs.context import JobContext
    from bf_core.jobs.registry import load_handlers

    job = {"id": job_id, "params": params, "project_folder": str(folder)}
    ctx = JobContext(job, lambda _message: None, threading.Event())
    try:
        out = load_handlers()["import.objects"].run(ctx)
    finally:
        ctx.close()
    assert out is not None
    return dict(out)


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time

    import numpy as np
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import blob_owners, blobs, objects
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes import store as ws

    count = int(params["wireframes"])
    folder = Path((work / "folder.txt").read_text(encoding="utf-8"))
    stamp = time.time_ns() // 1_000_000 % 1_000_000_000  # a repeated run is a new job
    t0 = time.perf_counter()
    result = _job(stamp, folder, {"files": [str(work / "выработки.dxf")]})
    import_s = time.perf_counter() - t0
    ids = [int(i) for i in result["wireframe_ids"]]
    assert len(ids) == count and not result["failed"], {k: result[k] for k in ("failed", "rest")}

    given = meshes(count)
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    store = BlobStore(folder)
    worst = 0
    try:
        with dialect.read_tx(engine) as conn:
            names = dict(conn.execute(sa.select(objects.c.id, objects.c.name).where(objects.c.id.in_(ids))).all())
            t1 = time.perf_counter()
            got = [ws.read_wireframe(conn, store, i) for i in ids]
            read_back_s = time.perf_counter() - t1
            n_blobs = int(conn.execute(sa.select(sa.func.count()).select_from(blobs)).scalar_one())
            packed = int(
                conn.execute(
                    sa.select(sa.func.count(sa.distinct(blob_owners.c.owner_id))).where(
                        blob_owners.c.owner_id.in_(ids), blob_owners.c.role.like("pack:%")
                    )
                ).scalar_one()
            )
    finally:
        engine.dispose()
    by_name = {m.name: m for m in given}

    def corners(v: Any, t: Any) -> set[bytes]:
        """Every triangle by its corners, bit for bit, from its smallest one (the winding kept)."""
        out = set()
        for tri in np.ascontiguousarray(v[t]):
            rows = [tri[i].tobytes() for i in range(3)]
            k = rows.index(min(rows))
            out.add(b"".join(rows[k:] + rows[:k]))
        return out

    for wf_id, (v, t) in zip(ids, got, strict=True):
        m = by_name[str(names[wf_id]).split(" (")[0]]  # a second run gets «name (2)»
        worst += corners(v, t) != corners(m.xyz, m.tri)
    assert worst == 0, worst
    return {
        "wireframes": count,
        "triangles": int(sum(len(m.tri) for m in given)),
        "import_s": round(import_s, 3),
        "ms_a_wireframe": round(1000 * import_s / count, 2),
        "read_back_s": round(read_back_s, 3),
        "blobs": n_blobs,
        "packed": packed,
        "mismatches": worst,
    }
