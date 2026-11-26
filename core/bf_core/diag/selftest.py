"""Self-test of native dependencies (spike Э0-а/г): each check imports and computes."""

from __future__ import annotations

import os
import platform
import sys
import tempfile
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import bf_core


def _cube(size: float = 1.0, origin: tuple[float, float, float] = (0.0, 0.0, 0.0)):  # type: ignore[no-untyped-def]
    import numpy as np

    ox, oy, oz = origin
    v = np.array([[x, y, z] for z in (0, size) for y in (0, size) for x in (0, size)], dtype=np.float64) + np.array(
        [ox, oy, oz]
    )
    t = np.array(
        [
            [0, 2, 1],
            [1, 2, 3],
            [4, 5, 6],
            [5, 7, 6],
            [0, 1, 4],
            [1, 5, 4],
            [2, 6, 3],
            [3, 6, 7],
            [0, 4, 2],
            [2, 4, 6],
            [1, 3, 5],
            [3, 7, 5],
        ],
        dtype=np.uint32,
    )
    return v, t


def check_numpy_scipy() -> dict[str, Any]:
    import numpy as np
    import scipy
    from scipy.spatial import cKDTree

    pts = np.random.default_rng(1).random((10_000, 3))
    d, _ = cKDTree(pts).query(pts[:10], k=2)
    assert float(d[:, 0].max()) == 0.0
    return {"numpy": np.__version__, "scipy": scipy.__version__}


def check_numba() -> dict[str, Any]:
    import numba
    import numpy as np

    from bf_core.diag.kernels import signed_volume

    v, t = _cube(10.0, (500_000.0, 7_000_000.0, 300.0))
    vol = signed_volume(v, t.astype(np.int64))
    assert abs(vol - 1000.0) <= 1e-9 * 1000.0, vol
    cache = Path(os.environ.get("NUMBA_CACHE_DIR", ""))
    files = sorted(p.name for p in cache.rglob("*") if p.suffix in (".nbi", ".nbc")) if cache.is_dir() else []
    return {"numba": numba.__version__, "volume": vol, "cache_dir": str(cache), "cache_files": len(files)}


def check_manifold() -> dict[str, Any]:
    import manifold3d as m3d

    a = m3d.Manifold.cube((10.0, 10.0, 10.0))
    b = m3d.Manifold.cube((10.0, 10.0, 10.0)).translate((5.0, 0.0, 0.0))
    va, vb = a.volume(), b.volume()
    vu, vi = (a + b).volume(), (a ^ b).volume()
    assert abs(vu - (va + vb - vi)) <= 1e-9 * (va + vb), (vu, va, vb, vi)
    return {"union": vu, "intersection": vi}


def check_cdt() -> dict[str, Any]:
    import pythoncdt as cdt

    t = cdt.Triangulation(cdt.VertexInsertionOrder.AS_PROVIDED, cdt.IntersectingConstraintEdges.NOT_ALLOWED, 0.0)
    t.insert_vertices([cdt.V2d(0, 0), cdt.V2d(1, 0), cdt.V2d(1, 1), cdt.V2d(0, 1), cdt.V2d(0.5, 0.5)])
    t.erase_super_triangle()
    return {"triangles": len(t.triangles)}


def check_earcut() -> dict[str, Any]:
    import mapbox_earcut as earcut
    import numpy as np

    ring = np.array([[0, 0], [4, 0], [4, 4], [0, 4]], dtype=np.float64)
    tris = earcut.triangulate_float64(ring, np.array([4], dtype=np.uint32))
    return {"indices": int(len(tris))}


def check_pyfqmr() -> dict[str, Any]:
    import numpy as np
    import pyfqmr
    from skimage.measure import marching_cubes

    g = np.linspace(-1, 1, 40)
    x, y, z = np.meshgrid(g, g, g, indexing="ij")
    verts, faces, _, _ = marching_cubes(x * x + y * y + z * z, 0.5)
    simp = pyfqmr.Simplify()
    simp.setMesh(np.array(verts, dtype=np.float64), np.array(faces, dtype=np.int32))
    simp.simplify_mesh(target_count=len(faces) // 8, aggressiveness=7, preserve_border=True, verbose=False)
    _, f2, _ = simp.getMesh()
    return {"faces": int(len(faces)), "simplified": int(len(f2))}


def check_duckdb_arrow() -> dict[str, Any]:
    import duckdb
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    with tempfile.TemporaryDirectory(prefix="bf-selftest-") as tmp:
        path = Path(tmp) / "blocks.parquet"
        n = 100_000
        table = pa.table({"i": np.arange(n, dtype=np.int64), "au": np.linspace(0.0, 1.0, n)})
        pq.write_table(table, path, compression="zstd", row_group_size=25_000)
        con = duckdb.connect()
        row = con.execute("SELECT count(*), avg(au) FROM read_parquet(?) WHERE au >= 0.5", [str(path)]).fetchone()
        assert row is not None
        cnt, mean = row
        con.close()
    assert cnt == n // 2, cnt
    return {"duckdb": duckdb.__version__, "pyarrow": pa.__version__, "rows": int(cnt), "mean": float(mean)}


def check_routers() -> dict[str, Any]:
    """Every HTTP module imports in this build (in the frozen exe a missing one is a 404)."""
    from bf_core.api.app import discover_routers
    from bf_core.api.modules import ROUTER_MODULES

    routers = discover_routers()
    paths = sorted({getattr(r, "path", "") for router in routers for r in router.routes})
    return {"modules": list(ROUTER_MODULES), "routes": paths}


def check_migrations() -> dict[str, Any]:
    """Alembic revisions ship as data files in the frozen exe: both chains upgrade an empty database."""
    from bf_core.db import dialect, migrate

    heads = {}
    with tempfile.TemporaryDirectory(prefix="bf-selftest-") as tmp:
        for chain in ("project", "instance"):
            engine = dialect.create_engine(dialect.sqlite_url(str(Path(tmp) / f"{chain}.sqlite")))
            try:
                migrate.upgrade(engine, chain)
                assert migrate.state(engine, chain) == "current"
                heads[chain] = migrate.head(chain)
            finally:
                engine.dispose()
    return {"heads": heads}


def check_worker() -> dict[str, Any]:
    """A `spawn` worker starts from this build (in the frozen exe — bf-core.exe itself) and runs a job."""
    import multiprocessing as mp

    from bf_core.jobs.worker import main

    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    proc = ctx.Process(target=main, args=(child, "quick"), daemon=True)
    t0 = time.perf_counter()
    proc.start()
    child.close()
    try:
        msgs = []
        while parent.poll(60):
            msg = parent.recv()
            msgs.append(msg[0])
            if msg[0] == "hello":
                continue
            if msg[0] == "ready":
                parent.send(("run", {"id": 1, "kind": "diag.quick", "params": {}}))
            elif msg[0] in ("done", "error"):
                break
        assert msgs and msgs[-1] == "done", msgs
        return {"start_and_run_s": round(time.perf_counter() - t0, 3)}
    finally:
        try:
            parent.send(("stop",))
        except OSError:
            pass
        proc.join(5)
        if proc.is_alive():
            proc.kill()


def check_pyproj() -> dict[str, Any]:
    """PROJ data (proj.db) ships with the build: a .prj is described and EPSG codes resolve."""
    from bf_core.crs import describe

    info = describe("EPSG:28407")  # Pulkovo 1942 / Gauss-Kruger zone 7
    assert info.projected and info.epsg == 28407, info
    return {"name": info.name}


def check_ortools() -> dict[str, Any]:
    """OR-Tools: SimpleMaxFlow on three nodes (the reference and fallback solver of the pit optimiser, ADR 0028)."""
    import numpy as np
    import ortools
    from ortools.graph.python import max_flow

    mf = max_flow.SimpleMaxFlow()
    mf.add_arcs_with_capacity(
        np.array([0, 1], dtype=np.int32), np.array([1, 2], dtype=np.int32), np.array([5, 3], dtype=np.int64)
    )
    assert mf.solve(0, 2) == max_flow.SimpleMaxFlow.Status.OPTIMAL and mf.optimal_flow() == 3
    return {"ortools": ortools.__version__, "flow": int(mf.optimal_flow())}


CHECKS: dict[str, Callable[[], dict[str, Any]]] = {
    "pyproj": check_pyproj,
    "worker": check_worker,
    "routers": check_routers,
    "migrations": check_migrations,
    "numpy_scipy": check_numpy_scipy,
    "numba": check_numba,
    "manifold3d": check_manifold,
    "pythoncdt": check_cdt,
    "earcut": check_earcut,
    "pyfqmr_skimage": check_pyfqmr,
    "duckdb_pyarrow": check_duckdb_arrow,
    "ortools": check_ortools,
}


def run_selftest(only: list[str] | None = None) -> dict[str, Any]:
    results: dict[str, Any] = {}
    ok = True
    for name, fn in CHECKS.items():
        if only and name not in only:
            continue
        t0 = time.perf_counter()
        try:
            results[name] = {"ok": True, **fn(), "seconds": round(time.perf_counter() - t0, 3)}
        except Exception as exc:  # noqa: BLE001 - report every failure
            ok = False
            results[name] = {"ok": False, "error": repr(exc), "trace": traceback.format_exc(limit=5)}
    return {
        "ok": ok,
        "version": bf_core.__version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "checks": results,
    }
