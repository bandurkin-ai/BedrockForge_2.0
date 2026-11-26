"""The field of patches and its isosurface (ADR 0026 «Замеры»; ТЗ 8.9.4: поверхность ≈ 2 млн треугольников ≤ 90 с
на 4 ядрах; подшаги 19.4, 19.5).

The data are a sphere of radius `radius` at the coordinates of a mine (500 000 / 7 000 000 / 100): `rays` directions
(a Fibonacci spiral), on each the contact at R (f = 0) and `±STEP·j`, j = 1 … 6, inside (−) and outside (+); the points
go through the octree, `solve.solve_patches` (η = 0) and `evaluate.PatchField` in the box of the sphere plus a margin;
`geo.iso.isosurface(field, lo, hi, h, levels=2)` on `params["threads"]` threads.
  prepare_s   the octree and the solution of the patches (not the time of ТЗ);  iso_s — the isosurface: the time of ТЗ
  triangles   the number of them;  nodes — the points the field was asked for (the coarse grid and the bricks)
  us_node     microseconds of one field point (iso_s · threads / nodes: the cost of the field with the surface's own)
  dev_max     the largest | |v − c| − R | of the vertices, metres;  volume_err — of the volume against 4/3πR³
What is checked (a failure stops the run): the mesh is closed (every edge of two triangles), of the Euler
characteristic V − E + F = 2 (a sphere), oriented outward (a positive volume), its volume within `VOLUME` of the
sphere's and its vertices within `DEVIATION`·R of the sphere (the interpolation between the rays, Т8.9-1 is the
unit test); the same bits on 1 and on `threads` threads (Т8.9-8) on the octant of the sphere (the box of the octant:
a run of one thread over the whole mesh is several times the run of the target)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from . import implicit_common as ic

CENTRE = np.array([500_000.0, 7_000_000.0, 100.0])
STEP = 5.0  # m: the distance of the inside and the outside points from the contact
SIDE = 6  # points on each side
VOLUME = 0.01
DEVIATION = 0.002
FILE = "sphere.npz"
N_PATCH, N_MIN, DELTA = 1000, 100, 0.25


def sphere_points(radius: float, rays: int) -> tuple[np.ndarray, np.ndarray]:
    """The data of a sphere (contacts and ±D along the rays) in the canonical order (x, y, z, f)."""
    k = np.arange(rays) + 0.5
    phi = np.arccos(1.0 - 2.0 * k / rays)
    theta = np.pi * (1.0 + 5.0**0.5) * k
    d = np.column_stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)])
    t = np.arange(-SIDE, SIDE + 1) * STEP  # −SIDE … SIDE: the position along a ray from the contact (+ — outside)
    xyz = CENTRE + d[:, None, :] * (radius + t)[None, :, None]
    f = np.broadcast_to(t[None, :], (rays, t.size))
    xyz, f = xyz.reshape(-1, 3), f.reshape(-1)
    order = np.lexsort((f, xyz[:, 2], xyz[:, 1], xyz[:, 0]))
    return np.ascontiguousarray(xyz[order]), np.ascontiguousarray(f[order])


def prepare(params: dict[str, Any], work: Path) -> None:
    xyz, f = sphere_points(float(params["radius"]), int(params["rays"]))
    np.savez(work / FILE, xyz=xyz, f=f)


def load() -> None:
    from bf_core.model.warm import warm

    warm()


def audit(v: np.ndarray, t: np.ndarray, radius: float) -> dict[str, Any]:
    """Closed, χ, orientation, volume and deviation of a mesh of the sphere."""
    nv, nt = int(v.shape[0]), int(t.shape[0])
    e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]).astype(np.int64)
    key = np.minimum(e[:, 0], e[:, 1]) * nv + np.maximum(e[:, 0], e[:, 1])
    _, count = np.unique(key, return_counts=True)
    edges = int(count.size)
    local = v - CENTRE  # the volume in the coordinates of the centre (the numbers are of the size of the sphere)
    a, b, c = local[t[:, 0]], local[t[:, 1]], local[t[:, 2]]
    vol = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)
    exact = 4.0 / 3.0 * np.pi * radius**3
    used = int(np.unique(t).size)
    dev = float(np.abs(np.linalg.norm(local, axis=1) - radius).max())
    return {"closed": bool((count == 2).all()), "chi": used - edges + nt, "volume_err": abs(vol - exact) / exact,
            "oriented": vol > 0.0, "dev_max": dev, "edges": edges}  # fmt: skip


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.geo.iso import isosurface
    from bf_core.model import octree
    from bf_core.model.evaluate import PatchField
    from bf_core.model.solve import solve_patches

    threads = ic.limit_threads(params)
    radius, h = float(params["radius"]), float(params["h"])
    with np.load(work / FILE) as z:
        xyz, f = z["xyz"], z["f"]
    t0 = time.perf_counter()
    ps = octree.build_patches(xyz, n_patch=N_PATCH, n_min=N_MIN, delta=DELTA)
    sols = solve_patches(xyz, f, ps, kind="linear", eta=0.0, threads=threads)
    field = PatchField(ps, sols, threads=threads)
    prepare_s = time.perf_counter() - t0
    pad = 4.0 * h * 4
    lo, hi = CENTRE - radius - pad, CENTRE + radius + pad
    t1 = time.perf_counter()
    mesh = isosurface(field, lo, hi, h, levels=2, threads=threads)
    iso_s = time.perf_counter() - t1
    rss = ic.peak_mb()
    v, t = mesh.vertices, mesh.triangles
    a = audit(v, t, radius)
    st = mesh.stats
    assert a["closed"], "the mesh of the sphere is not closed: an edge is not of two triangles"
    assert a["chi"] == 2, f"the Euler characteristic of the sphere is {a['chi']}, not 2"
    assert a["oriented"], "the mesh of the sphere is turned inside out (negative volume)"
    assert a["volume_err"] <= VOLUME, f"the volume of the mesh is off by {a['volume_err']:.2%}"
    assert a["dev_max"] <= DEVIATION * radius, f"the vertices are {a['dev_max']:.3f} m off the sphere"
    bits_s = 0.0
    if params.get("bits", True):  # the octant: 1 thread against several, the same bytes
        olo, ohi = CENTRE - pad, CENTRE + radius + pad
        t2 = time.perf_counter()
        one = isosurface(field, olo, ohi, h, levels=2, threads=1)
        bits_s = time.perf_counter() - t2
        many = isosurface(field, olo, ohi, h, levels=2, threads=max(threads, 2))
        assert one.vertices.tobytes() == many.vertices.tobytes(), "the vertices differ between 1 and several threads"
        assert one.triangles.tobytes() == many.triangles.tobytes(), "the triangles differ between the threads"
        assert one.triangles.shape[0] > 0
    nodes = int(st.nodes + st.coarse_nodes)
    return {"threads": threads, "points": int(xyz.shape[0]), "patches": int(ps.n_patches),
            "prepare_s": round(prepare_s, 3), "iso_s": round(iso_s, 3), "triangles": int(t.shape[0]),
            "vertices": int(v.shape[0]), "nodes": nodes,
            "us_node": round(1e6 * iso_s * threads / max(nodes, 1), 3), "band_bricks": int(st.band_bricks),
            "meshed_bricks": int(st.meshed_bricks), "dev_max": round(a["dev_max"], 4),
            "volume_err": a["volume_err"], "chi": a["chi"], "bits_s": round(bits_s, 3), "process_mb": rss,
            "tri_per_s": round(t.shape[0] / iso_s, 1)}  # fmt: skip
