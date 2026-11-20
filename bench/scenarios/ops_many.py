"""«На части» into many small wireframes (10.10; ADR 0015 §8): the result of an operation in packs, not in blobs of
its own (`ops/job.py::_write` → `stage_wireframe(write=False)` + `wireframes/files.py::stage_files`).

One wireframe of `parts` closed boxes (12 to 108 triangles each, UTM coordinates float64 as they are, scattered over
5 km) in a project; the job `geometry.operation` `split` with «Не больше частей» = `parts` runs in this process as
a worker runs it: the reading of the source, the connected parts, the spatial order, parts and measures of every
part, its files (the small ones share a pack, `pack:<n>`), the objects with `meta.built` in one transaction.
  split_s        the job, from its start to its result;
  read_back_s    the geometry of every part read back from the project (`store.read_wireframe`);
What is checked: every part is there, its triangles those of its box bit for bit (as sets of corners: the store keeps
them in spatial order); `blobs` — distinct blobs of the parts of this run (was 2 a part: `xyz` and `tri`),
`packed` — parts whose geometry is an entry of a pack. A repeated run on the same project makes «… (2)» parts.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

X0, Y0, Z0 = 512_345.678_901, 6_234_567.123_456, 412.345_678


def boxes(count: int, seed: int = 23) -> list[tuple[Any, Any]]:
    import numpy as np

    from bench.scenarios.wireframes_many import _box

    rng = np.random.default_rng(seed)
    out = []
    for _k in range(count):
        at = np.array([X0, Y0, Z0]) + rng.uniform([0.0, 0.0, -300.0], [5_000.0, 5_000.0, 300.0])
        out.append(_box(int(rng.integers(1, 4)), rng.uniform(2.0, 40.0, 3), at))
    return out


def _project(work: Path, name: str, count: int, seed: int) -> dict[str, Any]:
    import numpy as np

    from bf_core.db import dialect
    from bf_core.projects.project import create_folder
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes import store as ws

    given = boxes(count, seed)
    shift = np.cumsum([0] + [len(v) for v, _t in given[:-1]])
    v = np.concatenate([b[0] for b in given])
    t = np.concatenate([b[1] + s for b, s in zip(given, shift, strict=True)]).astype(np.int32)
    folder = create_folder(work, name, {})
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        with dialect.write_tx(engine) as conn:
            wf_id = ws.put_wireframe(conn, BlobStore(folder), v, t, name="Выработки")
    finally:
        engine.dispose()
    return {"folder": str(folder), "wireframe_id": wf_id}


def prepare(params: dict[str, Any], work: Path) -> None:
    info = _project(work, "Части", int(params["parts"]), 23)
    (work / "project.json").write_text(json.dumps(info), encoding="utf-8")
    load()
    small = work / "малый"
    small.mkdir()  # the kernels of the split and of the store compiled into the cache: a few parts
    warm = _project(small, "Прогрев", 30, 3)
    _job(1, Path(warm["folder"]), warm["wireframe_id"], 30)


def load() -> None:
    import bf_core.geo.reshape  # noqa: F401
    import bf_core.ops.job  # noqa: F401
    import bf_core.ops.run_meshes  # noqa: F401
    import bf_core.wireframes.store  # noqa: F401


def _job(job_id: int, folder: Path, wf_id: int, parts: int) -> dict[str, Any]:
    from bf_core.db import dialect
    from bf_core.jobs.context import JobContext
    from bf_core.ops import job as ops_job
    from bf_core.ops import registry
    from bf_core.storage.blobs import BlobStore

    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        with dialect.read_tx(engine) as conn:
            checked = registry.precheck(conn, BlobStore(folder), "split", {"wireframe_id": wf_id, "max_parts": parts})
    finally:
        engine.dispose()
    job = {"id": job_id, "params": ops_job.job_params(checked), "project_folder": str(folder)}
    ctx = JobContext(job, lambda _message: None, threading.Event())
    try:
        return ops_job.run(ctx)
    finally:
        ctx.close()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time

    import numpy as np
    import sqlalchemy as sa

    from bf_core.db import dialect
    from bf_core.db.schema import blob_owners
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes import store as ws

    count = int(params["parts"])
    info = json.loads((work / "project.json").read_text(encoding="utf-8"))
    folder = Path(info["folder"])
    stamp = time.time_ns() // 1_000_000 % 1_000_000_000  # a repeated run is a new job
    t0 = time.perf_counter()
    result = _job(stamp, folder, int(info["wireframe_id"]), count)
    split_s = time.perf_counter() - t0
    ids = [int(i) for i in result["wireframe_ids"]]
    assert len(ids) == count, len(ids)

    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    store = BlobStore(folder)
    try:
        with dialect.read_tx(engine) as conn:
            t1 = time.perf_counter()
            got = [ws.read_wireframe(conn, store, i) for i in ids]
            read_back_s = time.perf_counter() - t1
            mine = blob_owners.c.owner_id.in_(ids) & (blob_owners.c.owner_kind == "object")
            n_blobs = int(conn.execute(sa.select(sa.func.count(sa.distinct(blob_owners.c.sha256))).where(mine))
                          .scalar_one())  # fmt: skip
            packed = int(
                conn.execute(
                    sa.select(sa.func.count(sa.distinct(blob_owners.c.owner_id))).where(
                        mine, blob_owners.c.role.like("pack:%")
                    )
                ).scalar_one()
            )
    finally:
        engine.dispose()

    def corners(v: Any, t: Any) -> frozenset[bytes]:
        """Every triangle by its corners, bit for bit, from its smallest one (the winding kept)."""
        out = set()
        for tri in np.ascontiguousarray(np.asarray(v, dtype=np.float64)[t]):
            rows = [tri[i].tobytes() for i in range(3)]
            k = rows.index(min(rows))
            out.add(b"".join(rows[k:] + rows[:k]))
        return frozenset(out)

    want = {corners(v, t) for v, t in boxes(count, 23)}
    worst = sum(corners(v, t) not in want for v, t in got) + abs(len({corners(v, t) for v, t in got}) - len(want))
    assert worst == 0, worst
    return {
        "parts": count,
        "split_s": round(split_s, 3),
        "ms_a_part": round(1000 * split_s / count, 2),
        "read_back_s": round(read_back_s, 3),
        "blobs": n_blobs,
        "packed": packed,
        "mismatches": worst,
    }
