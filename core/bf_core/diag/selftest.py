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
    v = np.array(
        [[x, y, z] for z in (0, size) for y in (0, size) for x in (0, size)], dtype=np.float64
    ) + np.array([ox, oy, oz])
    t = np.array(
        [[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4],
         [2, 6, 3], [3, 6, 7], [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]],
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
        row = con.execute(
            "SELECT count(*), avg(au) FROM read_parquet(?) WHERE au >= 0.5", [str(path)]
        ).fetchone()
        assert row is not None
        cnt, mean = row
        con.close()
    assert cnt == n // 2, cnt
    return {"duckdb": duckdb.__version__, "pyarrow": pa.__version__, "rows": int(cnt), "mean": float(mean)}


CHECKS: dict[str, Callable[[], dict[str, Any]]] = {
    "numpy_scipy": check_numpy_scipy,
    "numba": check_numba,
    "manifold3d": check_manifold,
    "pythoncdt": check_cdt,
    "earcut": check_earcut,
    "pyfqmr_skimage": check_pyfqmr,
    "duckdb_pyarrow": check_duckdb_arrow,
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
