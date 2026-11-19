"""The sections of the wireframes on a series of sections (ADR 0016 «Замеры»; ТЗ 5.3 «шаг серии ≤ 300 мс», 8.1.5
«кеш 100 разрезов в фоне ≤ 60 с»): `GET /section-series/{id}/sections/{k}/wireframes` and the job `sections.cache`.

A project database with two wireframes (`tools/gen/wireframes.py`): a pit surface with 90 % of the triangles and
a closed ore body under it, and a vertical series of `sections` sections at 63° to the grid across the surface.
The answers are made as the endpoint makes them (`sections/wf_sections.py`), in a fresh process:
  batch_first_s   the first answer: the geometry of both wireframes read from the blobs, then cut;
  batch_warm_s    the slowest of 20 answers on sections spread over the series, the geometry kept
                  (`wireframes/exact.py`) — the step of a series when the cache of the series is not ready;
  cache_job_s     the job `sections.cache` over every section of the series (its handler in this process, from
                  the mapped blobs, the files under temporary names and moved in place at the end);
  batch_cached_s  the slowest of the same 20 answers from the files of the cache, the kept geometry dropped;
the answers from the files are those cut from the geometry byte for byte (`cache_equal`), the lines of the
ore body are closed. The folder of the cache is removed before the job: every repeat makes it again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

AZIMUTH = 63.0
SAMPLES = 20


def prepare(params: dict[str, Any], work: Path) -> None:
    from tools.gen.wireframes import body_of, ore_body, pit_surface

    from bf_core.db import dialect
    from bf_core.projects.project import create_folder
    from bf_core.sections import store as series_store
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes import store as wf_store

    n = int(params["triangles"])
    k = int(params["sections"])
    surface = pit_surface(round(0.9 * n))
    folder = create_folder(work, "Сечения серии", {})
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
        sx, sy = surface.side
        ox, oy, _oz = surface.origin
        # across the grid at 63°: P1 at the west corner, the planes over 90 % of the surface
        span = 0.9 * min(sx, sy)
        series = {"type": "vertical", "p1": [ox + 0.05 * sx, oy + 0.95 * sy, 0.0], "az": AZIMUTH, "s0": 0.0}
        series |= {"s1": span, "step": span / (k - 1), "towards": 1.0, "away": 1.0}
        with dialect.write_tx(engine) as conn:
            sid = series_store.create(conn, series, "Профили")["id"]
    finally:
        engine.dispose()
    (work / "folder.txt").write_text(f"{folder}\n{sid}\n{ids[0]}\n{ids[1]}", encoding="utf-8")
    load()  # compile into the cache outside the timing


def load() -> None:
    import numpy as np

    import bf_core.sections.cache_job  # noqa: F401
    import bf_core.wireframes.exact  # noqa: F401
    from bf_core.geo import section

    x, y = np.meshgrid(np.arange(5.0), np.arange(5.0))
    v = np.column_stack([x.ravel(), y.ravel(), np.sin(x.ravel()) + 0.3 * y.ravel()])
    i = (np.arange(4)[:, None] * 5 + np.arange(4)[None, :]).ravel()
    t = np.concatenate([np.column_stack([i, i + 1, i + 6]), np.column_stack([i, i + 6, i + 5])]).astype(np.int32)
    assert len(section.plane_section(v, t, [2.2, 2.2, 0.0], [1.0, 0.3, 0.0], clip=[[0.0, 1.0, 0.0, -0.5]])) == 1


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import shutil
    import threading
    import time
    from types import SimpleNamespace

    import numpy as np

    from bf_core.db import dialect
    from bf_core.jobs.context import JobContext
    from bf_core.sections import cache_files, cache_job
    from bf_core.sections import store as series_store
    from bf_core.sections import wf_sections as wf
    from bf_core.sections.geometry import series_geometry
    from bf_core.viz import binary
    from bf_core.wireframes import exact

    folder_text, sid_text, *id_text = (work / "folder.txt").read_text(encoding="utf-8").split("\n")
    folder, sid, ids = Path(folder_text), int(sid_text), [int(i) for i in id_text]
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    project = SimpleNamespace(engine=engine, uid="bench", folder=folder, read_only=False)
    shutil.rmtree(cache_files.root(folder), ignore_errors=True)
    try:
        with dialect.read_tx(engine) as conn:
            meta = series_store.row(conn, sid).meta
        geom = series_geometry(meta)
        key, total = wf.series_key(geom), wf.slot_count(geom)
        picked = sorted({round(j * (total - 1) / (SAMPLES - 1)) for j in range(SAMPLES)})
        exact.forget()

        def answer(k: int) -> tuple[float, bytes, int]:
            a = time.perf_counter()
            with dialect.read_tx(engine) as conn:
                found, empty, missing = wf.states(conn, ids)
            s = wf.slot_of(series_geometry(meta), k, None)
            body, hits = wf.answer(project, sid, key, s, found, empty, missing)
            return time.perf_counter() - a, body, hits

        first_s, _body, _hits = answer(picked[len(picked) // 2])
        warm = [answer(k) for k in picked]
        assert all(h == 0 for _s, _b, h in warm)
        exact.forget()

        ticks = [0]
        sent: list[tuple[Any, ...]] = []

        def send(msg: tuple[Any, ...]) -> None:
            ticks[0] += msg[0] == "progress"
            sent.append(msg)

        job = {"id": 1, "project_folder": str(folder), "params": {"series_id": sid, "wireframe_ids": ids,
               "key": key, "first": 0, "count": total}}  # fmt: skip
        ctx = JobContext(job, send, threading.Event())
        a = time.perf_counter()
        result = cache_job._run(ctx)
        job_s = time.perf_counter() - a
        ctx.close()
        assert result["files"] == len(ids) and result["count"] == total, result
        exact.forget()
        cached = [answer(k) for k in picked]
        assert all(h == len(ids) for _s, _b, h in cached)
        assert exact.cache_info()["loads"] == 0  # from the files only
    finally:
        exact.forget()
        engine.dispose()

    equal = all(w[1] == c[1] for w, c in zip(warm, cached, strict=True))
    lines = points = closed = 0
    for _s, body, _h in warm:
        head, arrays = binary.decode(body)
        lines += int(head["lines"])
        points += int(head["points"])
        wfs, shut = arrays["wf"], arrays["closed"].astype(bool)
        assert shut[wfs == ids[1]].all()  # the ore body is cut along closed lines
        closed += int(shut.sum())
    assert equal and lines and points
    return {
        "triangles": int(params["triangles"]),
        "sections": total,
        "batch_first_s": round(first_s, 4),
        "batch_warm_s": round(max(s for s, _b, _h in warm), 4),
        "batch_warm_median_s": round(float(np.median([s for s, _b, _h in warm])), 4),
        "cache_job_s": round(job_s, 3),
        "batch_cached_s": round(max(s for s, _b, _h in cached), 5),
        "cache_equal": int(equal),
        "cache_mb": round(cache_files.size(folder, key) / 1e6, 1),
        "lines": lines,
        "closed_lines": closed,
        "points": points,
        "ticks": ticks[0],
    }
