"""The warm-up of the numba kernels of the implicit model (Э19, 19.21; the step `implicit` of `ops/warm.py`; ADR 0026,
the decision «numba: cache=True»; lesson 17.39б).

`warm()` does not keep a list of kernels: it calls the PUBLIC functions of the stage on a tiny scene — a sphere of a few
dozens of points, a hole with three intervals, a vein of two sides — exactly as the jobs call them, so whatever kernel
the stage uses (`kernels`, `field_kernel`, `data_kernels`, `veins_kernels`) is compiled, or loaded from NUMBA_CACHE_DIR,
for the types the real call has. The types are the point: a READ-ONLY array (a column of Parquet, an `np.load` memory
map, the frozen arrays of a `PatchSet`, the `Lines` of a table) is another signature for numba than a writable one, and
a signature missing from the cache is a compilation of seconds in the first request of the user (17.39б). So every part
runs its input both ways, and a part that fails does not stop the others (the job compiles what is missing itself).

numba is not imported here: every import is inside a function, so a module that imports `warm` does not pay for it.
The stage's `numba` kernels are in `model/*_kernels.py`; the steps of the pipeline that use no numba (`smoothing`,
`geo/iso*`, `octree`) are called too: they put the BLAS / scipy / skimage code on the path of the first request.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable
from typing import Any

import numpy as np

KINDS = ("linear", "cubic", "wendland")
_R = 10.0  # the radius of the warm-up sphere, metres


def _frozen(a: np.ndarray) -> np.ndarray:
    """A read-only copy: what a column read from a file or a cache is."""
    out = np.array(a)
    out.setflags(write=False)
    return out


def _fibonacci(n: int, r: float) -> np.ndarray:
    k = np.arange(n) + 0.5
    phi = np.arccos(1.0 - 2.0 * k / n)
    th = np.pi * (1.0 + 5.0**0.5) * k
    return r * np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], axis=1)


def _scene() -> tuple[np.ndarray, np.ndarray]:
    """The contacts on the sphere (f = 0) and the points inside (−) and outside (+): the data of a domain."""
    pts = np.concatenate([_fibonacci(48, _R), _fibonacci(24, 0.5 * _R), _fibonacci(24, 1.5 * _R)])
    f = np.concatenate([np.zeros(48), np.full(24, -0.5 * _R), np.full(24, 0.5 * _R)])
    order = np.lexsort((f, pts[:, 2], pts[:, 1], pts[:, 0]))
    return np.ascontiguousarray(pts[order]), np.ascontiguousarray(f[order])


def _spec(mode: str) -> Any:
    from .spec import parse_model

    return parse_model({"boundary": {"box": [-100, -100, -100, 100, 100, 100]}, "smoothing": {"mode": mode}})


def _writable(patches: Any) -> Any:
    """The same patches with writable arrays (the arrays of a `PatchSet` are frozen: another type for numba)."""
    fields = {f.name: np.array(getattr(patches, f.name)) for f in dataclasses.fields(patches)
              if isinstance(getattr(patches, f.name), np.ndarray)}  # fmt: skip
    return dataclasses.replace(patches, **fields)


def _solve() -> None:
    """`solve_patches` — the blocks Φ (`kernels.gram`, `cross`, `phi`) for every kernel, a global matrix, the matrices
    of the patches, the smoothing η of both modes; the points read-only and writable."""
    from .kernels import cross, gram, phi
    from .octree import build_patches
    from .smoothing import eta_for_patches
    from .solve import solve_patches

    pts, f = _scene()
    a = np.diag([1.0, 1.0, 2.0])
    gram(pts[:6], "linear")  # the wrappers copy the input: one signature for every caller
    cross(pts[:6], pts[:4], "cubic")
    phi(np.linspace(0.0, 2.0, 5), "wendland", 5.0)
    for frozen in (True, False):
        p = _frozen(pts) if frozen else pts
        v = _frozen(f) if frozen else f
        ps = build_patches(p, n_patch=40, n_min=24)
        for kind in KINDS:
            radius = 3.0 * _R if kind == "wendland" else None
            solve_patches(p, v, ps, kind=kind, radius=radius, eta=0.0)
            solve_patches(p, v, ps, kind=kind, radius=radius, eta=1e-3, threads=2)
        for mode in ("manual", "auto"):
            eta = eta_for_patches(p, None, ps, _spec(mode), radius=3.0 * _R)
            solve_patches(p, v, ps, kind="cubic", eta=list(eta))
        mats = [a] * ps.n_patches
        solve_patches(p, v, ps, kind="cubic", matrices=mats)
        solve_patches(p, v, build_patches(p, n_patch=40, n_min=24, matrix=a), kind="cubic")


def _field() -> None:
    """`PatchField` — the field of a solved model (`field_kernel.field_band`, `count_terms`): the arrays of the patches
    frozen (as built, loaded) and writable, the points of the query read-only and writable, with matrices or without."""
    from .evaluate import PatchField
    from .octree import build_patches
    from .solve import solve_patches

    pts, f = _scene()
    ps = build_patches(pts, n_patch=40, n_min=24)
    mats = [np.diag([1.0, 1.0, 2.0])] * ps.n_patches
    q = np.ascontiguousarray(np.random.default_rng(3).uniform(-1.2 * _R, 1.2 * _R, (40, 3)))
    for patches in (ps, _writable(ps)):
        for kind in KINDS:
            radius = 3.0 * _R if kind == "wendland" else None
            sols = solve_patches(pts, f, patches, kind=kind, radius=radius)
            for m in (None, mats):
                fld = PatchField(patches, sols, matrices=m)
                for qq in (q, _frozen(q)):
                    fld(qq)
                    fld.terms(qq)
                fld(q[:0])
                fld.inside(q)


def _iso() -> None:
    """`isosurface` of a `PatchField` on a tiny grid: the coarse grid, the band, the bricks, marching cubes."""
    from bf_core.geo.iso import isosurface

    from .evaluate import PatchField
    from .octree import build_patches
    from .solve import solve_patches

    pts, f = _scene()
    ps = build_patches(pts, n_patch=40, n_min=24)
    fld = PatchField(ps, solve_patches(pts, f, ps, kind="cubic"))
    lo, hi = np.full(3, -1.4 * _R), np.full(3, 1.4 * _R)
    isosurface(lambda q: fld(q), lo, hi, 2.5, threads=1)


def _data() -> None:
    """`domain_points` (`data_kernels.domain_strip`) — the intervals of a hole read-only (a table of the project is
    read from a cache) and writable; the counting pass and the filling pass."""
    from bf_core.composites import intervals as iv
    from bf_core.drillholes.desurvey import Collars, desurvey

    from .data_holes import domain_points

    traj = desurvey(Collars(np.arange(2), [100.0, 130.0], [200.0, 200.0], [500.0, 500.0], [40.0, 40.0]), n_holes=2)
    lines = iv.build_lines([0, 0, 0, 1, 1], [0, 10, 20, 0, 12], [10, 20, 30, 12, 30])
    state = np.array([1, 0, 1, 1, 0], dtype=np.int8)
    cols: dict[str, Any] = {k: _frozen(getattr(lines, k)) for k in ("holes", "starts", "frm", "to")}
    ro = dataclasses.replace(lines, **cols)
    for ln in (lines, ro):
        domain_points(ln, state, traj, np.array([0, 1]), d_max=6.0, step=2.0, group=1, names=["H1", "H2"])
        domain_points(ln, _frozen(state), traj, np.array([0, 1]), d_max=6.0, step=2.0, group=1, threads=2)


def _veins() -> None:
    """`veins_kernels.warm()` (its own two kernels on 2D points) and `build_vein` on a mini vein — the plane, the
    2D thin-plate spline of both sides, the solid of the grid (u, v)."""
    from . import veins_kernels
    from .veins import build_vein

    veins_kernels.warm()
    u, v = np.meshgrid(np.linspace(0.0, 20.0, 5), np.linspace(0.0, 12.0, 4), indexing="ij")
    uv = np.column_stack([u.ravel(), v.ravel()])
    base = np.column_stack([uv, 0.1 * uv[:, 0]])
    hang, foot = base + [0.0, 0.0, 1.0], base - [0.0, 0.0, 1.0]
    for h, f in ((hang, foot), (_frozen(hang), _frozen(foot))):
        build_vein(h, f, {"bound": "hull", "offset": 0.0, "t_min": 0.0}, 2.0)


#: part → its warm-up (a function without arguments); the order matters little: a kernel compiled by one part is a
#: call of the compiled code in the next
PARTS: dict[str, Callable[[], Any]] = {
    "solve": _solve,
    "field": _field,
    "iso": _iso,
    "data": _data,
    "veins": _veins,
}


def warm() -> dict[str, float]:
    """The parts one by one → the seconds of each by name; a part that fails is left out and does not stop the rest."""
    took: dict[str, float] = {}
    for name, fn in PARTS.items():
        t0 = time.perf_counter()
        try:
            fn()
        except Exception:  # noqa: BLE001 - a warm-up never stops a worker; the job compiles the kernel itself then
            continue
        took[name] = time.perf_counter() - t0
    return took
