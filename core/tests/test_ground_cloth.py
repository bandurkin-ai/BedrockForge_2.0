"""Э7.1, 7.1.7: the cloth of the ground filter (`geo/ground_cloth.py`, ADR 0014 §4, test «Ткань») — the
cascade of grids against a direct sparse solver, the obstacle under a particle, the seeds."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from bf_core.geo import ground_cloth as gc

LOAD = 4.0 / 16.0**2  # RI = 2: p = 4 / W², W = 16 m; CR = 1 m


def _scene(n: int = 200, seed: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """An obstacle with a step of 15 m, a conical pit 30 m deep, a «building» and a gap in the data."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:n, 0:n].astype(np.float64)
    h = 0.02 * x + rng.normal(0.0, 0.05, (n, n))
    h[x >= 0.6 * n] -= 15.0  # a vertical step
    h -= np.clip(30.0 - 1.19 * np.hypot(x - 0.3 * n, y - 0.3 * n), 0.0, None)  # a pit with a wall of 50°
    h[int(0.65 * n) : int(0.8 * n), int(0.15 * n) : int(0.25 * n)] += 5.0  # a roof: the cloth passes under it
    mask = np.ones((n, n), np.uint8)
    mask[int(0.3 * n) : int(0.5 * n), int(0.75 * n) : int(0.9 * n)] = 0  # no data
    mask[:3, : n // 2] = 0
    return h, mask


def _direct(h: np.ndarray, mask: np.ndarray, load: float, u: np.ndarray) -> np.ndarray:
    """The membrane on the free set of `u` (contact particles held on the obstacle), by a direct sparse solver."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.linalg import spsolve

    inside = mask.astype(bool)
    free = inside & (u < h)
    number = np.full(h.shape, -1, np.int64)
    number[free] = np.arange(int(free.sum()))
    rows, cols, vals = [], [], []
    diag = np.zeros(h.shape)
    rhs = np.full(h.shape, load)
    for dj, di in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        nb_in = np.zeros(h.shape, bool)
        nb_free = np.zeros(h.shape, bool)
        nb_h = np.zeros(h.shape)
        nb_no = np.full(h.shape, -1, np.int64)
        src = (slice(max(dj, 0), h.shape[0] + min(dj, 0)), slice(max(di, 0), h.shape[1] + min(di, 0)))
        dst = (slice(max(-dj, 0), h.shape[0] + min(-dj, 0)), slice(max(-di, 0), h.shape[1] + min(-di, 0)))
        nb_in[dst], nb_free[dst], nb_h[dst], nb_no[dst] = inside[src], free[src], h[src], number[src]
        diag += nb_in
        rhs += np.where(nb_in & ~nb_free, nb_h, 0.0)
        link = free & nb_free
        rows.append(number[link])
        cols.append(nb_no[link])
        vals.append(np.full(int(link.sum()), -1.0))
    rows.append(number[free])
    cols.append(number[free])
    vals.append(diag[free])
    a = coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols)))).tocsr()
    out = h.copy()
    out[free] = spsolve(a, rhs[free])
    return out


def _update(h: np.ndarray, mask: np.ndarray, load: float, u: np.ndarray) -> np.ndarray:
    """(Σ neighbours + load) / number of neighbours — where the membrane alone would put every particle."""
    inside = mask.astype(bool)
    s = np.zeros(h.shape)
    n = np.zeros(h.shape)
    for dj, di in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        src = (slice(max(dj, 0), h.shape[0] + min(dj, 0)), slice(max(di, 0), h.shape[1] + min(di, 0)))
        dst = (slice(max(-dj, 0), h.shape[0] + min(-dj, 0)), slice(max(-di, 0), h.shape[1] + min(-di, 0)))
        s[dst] += np.where(inside[src], u[src], 0.0)
        n[dst] += inside[src]
    return (s + load) / np.maximum(n, 1)


def test_cascade_equals_the_direct_solver_on_its_free_set() -> None:
    h, mask = _scene()
    inside = mask.astype(bool)
    u, counts = gc.solve(h, mask, LOAD, tol=1e-10)
    assert len(counts) == 3 and all(0 < c < gc.MAX_SWEEPS for c in counts)  # 200 → 101 → 51
    assert (u[inside] <= h[inside]).all()
    free = inside & (u < h)
    # a step, a pit and a roof leave wide free spans; the rest lies on the obstacle
    assert 0.2 < free.sum() / inside.sum() < 0.9
    ref = _direct(h, mask, LOAD, u)
    assert float(np.abs(u - ref)[inside].max()) <= 1e-6
    # complementarity: a free particle is in balance, a particle on the obstacle is pressed to it
    want = _update(h, mask, LOAD, u)
    assert float(np.abs(want - u)[free].max()) <= 1e-4
    assert float((want - h)[inside & ~free].min()) >= -1e-4
    # the roof and the upper side of the step are far above the cloth, the pit bottom carries it
    assert (h - u)[int(0.7 * 200), int(0.2 * 200)] > 4.0
    assert (h - u)[100, 110] > 5.0
    assert (h - u)[60, 60] < 1e-9


def test_working_tolerance_is_close_to_the_exact_cloth() -> None:
    h, mask = _scene()
    inside = mask.astype(bool)
    exact, _ = gc.solve(h, mask, LOAD, tol=1e-11)
    u, counts = gc.solve(h, mask, LOAD)  # tol 1e-4 (ADR 0014)
    assert float(np.abs(u - exact)[inside].max()) <= 5e-3
    assert counts[-1] < 400


def test_sweeps_on_threads_are_bitwise_the_same() -> None:
    h, mask = _scene(420, seed=4)
    assert h.size >= gc.THREAD_NODES
    serial, counts = gc.solve(h, mask, LOAD)
    for threads in (2, 4, 8):
        with ThreadPoolExecutor(threads) as ex:

            def rows(fn, n, ex=ex, threads=threads):  # type: ignore[no-untyped-def]
                size = -(-n // threads)
                return list(ex.map(lambda r: fn(r, min(r + size, n)), range(0, n, size)))

            u, c = gc.solve(h, mask, LOAD, rows=rows)
        assert c == counts
        assert u.tobytes() == serial.tobytes()


def test_tick_is_called_and_may_cancel() -> None:
    h, mask = _scene(120)
    calls = []
    gc.solve(h, mask, LOAD, tick=lambda: calls.append(1))
    assert len(calls) >= 3

    def stop() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        gc.solve(h, mask, LOAD, tick=stop)


def test_depth_of_a_pit_does_not_add_sweeps() -> None:
    """ADR 0014, Т-З2: a pit ten times deeper (the same plan, the wall ten times steeper in height) takes the
    same sweeps — there are no time steps for the cloth to fall through."""
    n = 320
    y, x = np.mgrid[0:n, 0:n].astype(np.float64)
    r = np.hypot(x - n / 2, y - n / 2)
    mask = (r <= 150.0).astype(np.uint8)
    counts = []
    for depth in (30.0, 300.0):
        h = np.clip((r - 15.0) * (depth / 110.0), 0.0, depth) - depth
        u, c = gc.solve(h, mask, LOAD)
        assert float((h - u)[n // 2, n // 2]) < 1e-9  # the cloth lies on the bottom
        counts.append(c)
    assert sum(counts[1]) <= 1.25 * sum(counts[0]) + 16
    assert counts[1][-1] <= 1.25 * counts[0][-1] + 16


# --- obstacle and seeds ----------------------------------------------------------------------------------


def _cells(points: np.ndarray, ncx: int, ncy: int, c: float):  # type: ignore[no-untyped-def]
    """The lowest point of every cell of a small local cloud (the block starts at 0, 0)."""
    cmin = np.full(ncx * ncy, -1, np.int32)
    mx, my, mz = np.zeros(ncx * ncy, np.float32), np.zeros(ncx * ncy, np.float32), np.zeros(ncx * ncy)
    cell = (np.floor(points[:, 1] / c).astype(int) * ncx + np.floor(points[:, 0] / c).astype(int)).astype(np.int64)
    for k in np.lexsort((-points[:, 2], cell)):  # the lowest of a cell comes last
        j = cell[k]
        cmin[j] = k
        mx[j] = points[k, 0] - (j % ncx + 0.5) * c
        my[j] = points[k, 1] - (j // ncx + 0.5) * c
        mz[j] = points[k, 2]
    return cmin, mx, my, mz


def _obstacle(points: np.ndarray, size: int, cr: float = 1.0):  # type: ignore[no-untyped-def]
    c = cr / 2
    ncx = ncy = int(round(size / c))
    npx = ncx // 2 + 1
    cells = _cells(points, ncx, ncy, c)
    hob = np.zeros((npx, npx))
    mask = np.zeros((npx, npx), np.uint8)
    for r in range(0, npx, 5):
        gc.obstacle_rows(*cells, ncx, ncy, npx, r, min(r + 5, npx), c, cr, 0.1, hob, mask)
    return cells, hob, mask, ncx


def test_obstacle_is_the_plane_under_the_particle_not_the_nearest_return() -> None:
    rng = np.random.default_rng(8)
    tan = float(np.tan(np.radians(70.0)))
    pts = rng.random((6000, 2)) * 30.0  # 6.7 points/m²
    pts = np.column_stack([pts, 100.0 + tan * pts[:, 0] - 0.3 * pts[:, 1]])
    _cells_, hob, mask, _ = _obstacle(pts, 30)
    j, i = np.mgrid[0:31, 0:31]
    truth = 100.0 + tan * i - 0.3 * j
    inner = (slice(2, 29), slice(2, 29))
    assert mask.all()
    assert float(np.abs(hob - truth)[inner].max()) < 1e-9  # exact on a plane: the returns lie on it
    # the nearest return alone would miss by its distance times the slope
    assert tan * 0.2 > 0.5


def test_obstacle_keeps_to_the_ground_under_vegetation_and_marks_the_outside() -> None:
    rng = np.random.default_rng(9)
    ground = rng.random((1500, 2)) * 30.0
    ground = ground[ground[:, 0] < 22.0]  # nothing beyond X = 22 m
    veg = rng.random((3000, 2)) * [22.0, 30.0]
    pts = np.vstack(
        [
            np.column_stack([ground, 50.0 + 1.19 * ground[:, 0] + rng.normal(0, 0.02, len(ground))]),
            np.column_stack([veg, 50.0 + 1.19 * veg[:, 0] + 0.5 + 2.5 * rng.random(len(veg))]),
        ]
    )
    _cells_, hob, mask, _ = _obstacle(pts, 30)
    j, i = np.mgrid[0:31, 0:31]
    truth = 50.0 + 1.19 * i
    inner = (slice(3, 28), slice(3, 19))
    dev = (hob - truth)[inner]
    assert float(np.median(np.abs(dev))) < 0.08 and float(np.quantile(dev, 0.95)) < 0.6
    assert mask[:, :22].all() and not mask[:, 25:].any()  # 2·CR beyond the last return and no further


def test_seeds_are_the_returns_within_ct_of_the_cloth_along_its_normal() -> None:
    rng = np.random.default_rng(10)
    tan = 1.19
    pts = rng.random((7000, 2)) * 30.0
    z = 20.0 + tan * pts[:, 0] + rng.normal(0, 0.02, len(pts))
    high = rng.random(len(pts)) < 0.15
    z[high] += 0.4 + 2.0 * rng.random(int(high.sum()))  # 0.4 m along the vertical is 0.26 m along the normal
    pts = np.column_stack([pts, z])
    cells, hob, mask, ncx = _obstacle(pts, 30)
    cmin, mx, my, mz = cells
    u, _ = gc.solve(hob, mask, LOAD)
    state = np.zeros(len(cmin), np.uint8)
    gx, gy = np.zeros(len(cmin), np.float32), np.zeros(len(cmin), np.float32)
    seeds = 0
    for r in range(0, ncx, 9):
        seeds += gc.seed_rows(cmin, mx, my, mz, u, ncx, r, min(r + 9, ncx), 0.5, 1.0, 0.5, state, gx, gy)
    assert seeds == int(state.sum())
    has = cmin >= 0
    cell = np.flatnonzero(has)
    inner = (cell % ncx > 6) & (cell % ncx < ncx - 6) & (cell // ncx > 6) & (cell // ncx < ncx - 6)
    over = (mz[has] - (20.0 + tan * ((cell % ncx + 0.5) * 0.5 + mx[has]))) / np.hypot(1.0, tan)  # along the normal
    assert state[has][inner & (over < 0.35)].all()
    assert not state[has][inner & (over > 0.75)].any()
    assert abs(float(np.median(gx[has][inner])) - tan) < 0.05 and abs(float(np.median(gy[has][inner]))) < 0.05
    assert not state[~has].any()
