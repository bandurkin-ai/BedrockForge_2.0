"""Bodies by priority, the class of a point, FRAC and the code of a box (ТЗ 8.7.3, steps 3, 4, 6; ADR 0023 §6).

    bodies = Bodies.of(grid, meshes, priorities=…)              # `columns.Mesh` list, one set of rays for all
    class_points(bodies, q_local, threads=…)                    # int16 [n]: input index of the body, n_bodies — outside
    box_codes(bodies, boxes, m=4, mode="inside", threads=…)     # (code, frac) of arbitrary boxes by their m³ points

Priority. A point belongs to the body of the highest priority that contains it (the parity of the crossings of its
own vertical ray with the triangles of the body, `columns.ray_crossings`, below it); a point in no body is
"outside". Bodies are ranked by priority, larger first; equal ones — in the order of the list. The rays of all points
(the centre of a box and the m³ points of FRAC) are shifted by (√2·1e−6; √3·1e−6)·the sub-cell (`columns.RAY_OFFSET`,
step 6); the triangles of all bodies are in one index of the XY plane with a finer cell than a parent (≤ 8 per
axis of sub-cells), so a ray reads a few triangles.

Box. The centre q_a = (i_a + (s_a + w_a/2)/div_a)·d_a (`geometry.row_local_centres`) tells the class `cc` (step 3).
FRAC (step 4) is the share of the m·m·m centres of the equal parts of the box that belong to the class of the code
(m = 1 — the centre). Code and FRAC (`decide`) by the mode — "inside": the bodies, "outside": outside, "both":
all:
  · `cc` is wanted — its code, FRAC = share of its points (may be 0);
  · `cc` is not, but points of a wanted class are in the box — the class with the most points (a tie — the higher
    priority, outside the last), FRAC = its share: a box whose centre is outside still holds a part of the body,
    and the volume Σ FRAC·V of the boundary does not lose it;
  · no point is wanted — no code (−1).
A box that no triangle goes through is of one class: FRAC = 1 (`decide_uniform`).

Threads. A chunk of points / boxes is computed alone, the counters are integers: the same bits with any number
of threads. Kernels are `nogil` (ADR 0014).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from bf_core.api.errors import ApiError
from bf_core.blocks.fill import columns as fc
from bf_core.blocks.fill.columns import RAY_OFFSET, Mesh, RayIndex, ray_crossings
from bf_core.blocks.schema import GridV2, SubblockSpec
from bf_core.composites import strips
from bf_core.jit import njit

MODES = ("inside", "outside", "both")
MAX_M = 16  # points per axis of a box (ТЗ 8.7.3: m = 1–16)
FINE_FACTOR = 8  # the XY index cell is the sub-cell, not coarser than this many parts of a parent per axis
FINE_CELLS = 1 << 24  # cells of the XY index over the body at most (more — the cell grows)
POINT_CHUNK = 1 << 12  # boxes / points in a chunk of `box_codes` / `class_points`

Tick = fc.Tick


# --- errors ------------------------------------------------------------------------------------------------


def check_mode(mode: str) -> tuple[bool, bool]:
    """(bodies wanted, outside wanted) of the mode; 422 `fill_mode {param: "mode"}` otherwise."""
    if mode not in MODES:
        raise ApiError(
            422,
            "fill_mode",
            f"Режим заполнения «{mode}» неизвестен.",
            "Выберите «внутри», «снаружи» или «оба».",
            extra={"param": "mode"},
        )
    return mode in ("inside", "both"), mode in ("outside", "both")


def check_m(m: object) -> int:
    """m — an integer 1…16 (points per axis for FRAC); 422 `fill_m {param: "m"}` otherwise."""
    if isinstance(m, bool) or not isinstance(m, (int, float)) or float(m) != int(m) or not 1 <= int(m) <= MAX_M:
        raise ApiError(
            422,
            "fill_m",
            f"Точек подсетки m = {m!r}: нужно целое число от 1 до {MAX_M}.",
            "Доля объёма считается по m × m × m точкам блока; по умолчанию m = 4.",
            extra={"param": "m"},
        )
    return int(m)


# --- kernels -----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def sample_ray(v, t, lab, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, nb, px, py, zq, nq, zs, ss, ts, par, cls):  # type: ignore[no-untyped-def]
    """Classes of the points (px, py, zq[c]), c < nq, zq ascending: cls[c] = the least rank of a body with an odd
    number of crossings below the point (a crossing at its very z is not below), nb — outside."""
    n = ray_crossings(v, t, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, px, py, zs, ss, ts)
    for q in range(nb):
        par[q] = 0
    e = 0
    for c in range(nq):
        z = zq[c]
        while e < n and zs[e] < z:
            par[lab[ts[e]]] ^= 1
            e += 1
        k = nb
        for q in range(nb):
            if par[q] != 0:
                k = q
                break
        cls[c] = k


@njit(cache=True, nogil=True)
def class_at(v, t, lab, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, nb, x, y, z, zs, ss, ts, par, cls):  # type: ignore[no-untyped-def]
    """The class of the point (x, y, z): the ray is already shifted by the caller."""
    zq = np.empty(1, np.float64)
    zq[0] = z
    sample_ray(v, t, lab, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, nb, x, y, zq, 1, zs, ss, ts, par, cls)
    return cls[0]


@njit(cache=True, nogil=True)
def leaf_counts(  # type: ignore[no-untyped-def]
    v, t, lab, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, nb,
    x0, y0, z0, x1, y1, z1, cx, cy, cz, m, ex, ey, zs, ss, ts, par, zq, cls, cnt,
):  # fmt: skip
    """cnt[class] — the points of the box (x0…z1) among its m·m·m centres of equal parts, by class (rank, nb —
    outside); m = 1 — the centre (cx, cy, cz). Rays are shifted by (ex, ey)."""
    for q in range(nb + 1):
        cnt[q] = 0
    if m == 1:
        zq[0] = cz
        sample_ray(v, t, lab, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, nb, cx + ex, cy + ey, zq, 1, zs, ss, ts, par, cls)
        cnt[cls[0]] += 1
        return
    hx, hy, hz = (x1 - x0) / m, (y1 - y0) / m, (z1 - z0) / m
    for c in range(m):
        zq[c] = z0 + (c + 0.5) * hz
    for a in range(m):
        px = x0 + (a + 0.5) * hx + ex
        for b in range(m):
            py = y0 + (b + 0.5) * hy + ey
            sample_ray(v, t, lab, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, nb, px, py, zq, m, zs, ss, ts, par, cls)
            for c in range(m):
                cnt[cls[c]] += 1


@njit(cache=True, nogil=True)
def decide(cnt, nb, cc, allow_in, allow_out, m3):  # type: ignore[no-untyped-def]
    """(class, FRAC) of a box by its counts `cnt` and the class of its centre `cc` (see the module doc); class −1,
    FRAC NaN — nothing wanted."""
    if (cc < nb and allow_in) or (cc == nb and allow_out):
        return cc, cnt[cc] / m3
    best, most = -1, 0
    for q in range(nb + 1):
        if (q < nb and allow_in) or (q == nb and allow_out):
            if cnt[q] > most:
                best, most = q, cnt[q]
    if best < 0:
        return -1, np.nan
    return best, most / m3


@njit(cache=True, nogil=True)
def decide_uniform(nb, cc, allow_in, allow_out):  # type: ignore[no-untyped-def]
    """The same for a box of one class."""
    if (cc < nb and allow_in) or (cc == nb and allow_out):
        return cc, 1.0
    return -1, np.nan


@njit(cache=True, nogil=True)
def _points(  # type: ignore[no-untyped-def]
    v, t, lab, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, cap, nb, q, ex, ey, lo, hi, out,
):  # fmt: skip
    zs = np.empty(cap, np.float64)
    ss = np.empty(cap, np.int8)
    ts = np.empty(cap, np.int32)
    par = np.zeros(max(nb, 1), np.int8)
    cls = np.empty(1, np.int64)
    for m in range(lo, hi):
        out[m] = class_at(v, t, lab, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, nb,
                          q[m, 0] + ex, q[m, 1] + ey, q[m, 2], zs, ss, ts, par, cls)  # fmt: skip


@njit(cache=True, nogil=True)
def _boxes(  # type: ignore[no-untyped-def]
    v, t, lab, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, cap, nb,
    bx, cen, m, ex, ey, allow_in, allow_out, lo, hi, code, frac,
):  # fmt: skip
    zs = np.empty(cap, np.float64)
    ss = np.empty(cap, np.int8)
    ts = np.empty(cap, np.int32)
    par = np.zeros(max(nb, 1), np.int8)
    zq = np.empty(MAX_M, np.float64)
    cls = np.empty(MAX_M, np.int64)
    cnt = np.empty(nb + 1, np.int64)
    for r in range(lo, hi):
        leaf_counts(v, t, lab, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, nb,
                    bx[r, 0], bx[r, 1], bx[r, 2], bx[r, 3], bx[r, 4], bx[r, 5], cen[r, 0], cen[r, 1], cen[r, 2],
                    m, ex, ey, zs, ss, ts, par, zq, cls, cnt)  # fmt: skip
        cc = class_at(v, t, lab, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, nb, cen[r, 0] + ex, cen[r, 1] + ey, cen[r, 2],
                      zs, ss, ts, par, cls)  # fmt: skip
        c, f = decide(cnt, nb, cc, allow_in, allow_out, m * m * m)
        code[r] = c
        frac[r] = f


# --- bodies ------------------------------------------------------------------------------------------------


def _fine_index(grid: GridV2, v: np.ndarray, t: np.ndarray, max_entries: int) -> RayIndex:
    """The XY index of the triangles with the cell of a sub-cell (≤ FINE_FACTOR parts of a parent per axis)."""
    nx, ny, _ = grid.counts
    dx, dy, dz = grid.regular("Заполнение")
    fx, fy = min(grid.sub.div[0], FINE_FACTOR), min(grid.sub.div[1], FINE_FACTOR)
    lo, hi = v.min(axis=0), v.max(axis=0)
    while True:
        cells = ((hi[0] - lo[0]) * fx / dx + 2.0) * ((hi[1] - lo[1]) * fy / dy + 2.0)
        if cells <= FINE_CELLS or (fx == 1 and fy == 1):
            break
        if fx >= fy:
            fx //= 2
        else:
            fy //= 2
    gx, gy = dx / fx, dy / fy
    fine = dataclasses.replace(grid, counts=(nx * fx, ny * fy, 1), size=(gx, gy, dz), sub=SubblockSpec())
    return fc._build_index(fine, (gx, gy, dz), v, t, (float(lo[0]), float(lo[1]), float(lo[2])),  # noqa: SLF001
                           (float(hi[0]), float(hi[1]), float(hi[2])), max_entries)  # fmt: skip


@dataclass(frozen=True, eq=False)
class Bodies:
    """The closed bodies of a fill in the local axes of one grid, ranked by priority (rank 0 — the main one).
    Triangles of all bodies are joined: `v`, `t` (into `v`), `lab[triangle]` — the rank of its body, `toff[b]` —
    the first triangle of the body b (input order), `input_of[rank]` — the input number of a rank."""

    n: int
    grid: GridV2
    v: np.ndarray
    t: np.ndarray
    lab: np.ndarray
    toff: np.ndarray
    input_of: np.ndarray
    rank_of: np.ndarray
    index: RayIndex
    unit: tuple[float, float]

    @classmethod
    def of(
        cls,
        grid: GridV2,
        meshes: Sequence[Mesh],
        priorities: Sequence[int | None] | None = None,
        *,
        unit: tuple[float, float] | None = None,
        max_entries: int = fc.MAX_ENTRIES,
    ) -> Bodies:
        """`meshes` — checked `columns.Mesh` (local axes of `grid`); `priorities` — larger is the main (None — 0;
        equal — the earlier in the list is). `unit` — the sub-cell of the rays (the grid's by default)."""
        grid.regular("Заполнение")
        n = len(meshes)
        if n == 0:
            raise ApiError(
                422,
                "fill_wireframes",
                "Не выбрано ни одного каркаса.",
                "Выберите хотя бы один замкнутый каркас.",
                extra={"param": "wireframes"},
            )
        prio = [0 if (priorities is None or p is None) else int(p) for p in (priorities or [None] * n)]
        if len(prio) != n:
            raise ValueError("priorities and meshes differ in length")
        input_of = np.array(sorted(range(n), key=lambda b: (-prio[b], b)), dtype=np.int16)
        rank_of = np.empty(n, np.int16)
        rank_of[input_of] = np.arange(n, dtype=np.int16)
        voff = np.cumsum([0] + [len(m.v) for m in meshes])
        toff = np.cumsum([0] + [len(m.t) for m in meshes]).astype(np.int64)
        v = np.concatenate([m.v for m in meshes]) if n > 1 else meshes[0].v
        t = np.concatenate([m.t + np.int32(voff[b]) for b, m in enumerate(meshes)]) if n > 1 else meshes[0].t
        lab = np.concatenate([np.full(len(m.t), rank_of[b], np.int16) for b, m in enumerate(meshes)])
        sub = grid.subcell()
        u = (float(unit[0]), float(unit[1])) if unit is not None else (sub[0], sub[1])
        index = _fine_index(grid, v, t, max_entries) if len(t) else meshes[0].index
        return cls(n, grid, v, np.ascontiguousarray(t, dtype=np.int32), lab, toff, input_of, rank_of, index, u)

    def offsets(self) -> tuple[float, float]:
        """The shift of every ray (step 6)."""
        return RAY_OFFSET[0] * self.unit[0], RAY_OFFSET[1] * self.unit[1]

    def args(self) -> tuple[object, ...]:
        """(v, t, lab, ptr, idx, ncx, ncy, ci0, cj0, gx, gy) — the first arguments of the kernels."""
        ix = self.index
        return self.v, self.t, self.lab, ix.ptr, ix.idx, ix.ncx, ix.ncy, ix.ci0, ix.cj0, ix.gx, ix.gy

    def to_input(self, rank_code: np.ndarray) -> np.ndarray:
        """Codes by rank (n — outside, −1 — none) → by the input number (n — outside, −1 — none)."""
        table = np.concatenate([self.input_of, np.array([self.n, -1], np.int16)])
        return table[np.where(rank_code < 0, self.n + 1, rank_code)]


# --- points, boxes -----------------------------------------------------------------------------------------


def class_points(
    bodies: Bodies, q: object, *, threads: int = 1, tick: Tick | None = None, chunk: int = POINT_CHUNK * 16
) -> np.ndarray:
    """int16 [n]: the body of each point q (n×3, local axes) by priority — its input number, `bodies.n` — outside."""
    pts = np.ascontiguousarray(q, dtype=np.float64).reshape(-1, 3)
    out = np.empty(len(pts), np.int64)
    if len(pts) == 0:
        return out.astype(np.int16)
    ex, ey = bodies.offsets()
    args = bodies.args()

    def one(lo: int, hi: int) -> None:
        _points(*args, bodies.index.cap, bodies.n, pts, ex, ey, lo, hi, out)  # type: ignore[call-arg]

    strips.run(one, len(pts), threads=threads, tick=tick, size=int(chunk))
    return bodies.to_input(out.astype(np.int16))


def box_codes(
    bodies: Bodies,
    boxes: object,
    *,
    m: int = 4,
    mode: str = "inside",
    threads: int = 1,
    tick: Tick | None = None,
    chunk: int = POINT_CHUNK,
) -> tuple[np.ndarray, np.ndarray]:
    """(code int16, frac float64) of boxes n×6 (x0 y0 z0 x1 y1 z1, local axes) by their m³ points — every box is
    evaluated by points (no pruning by triangles: `octree` does that). code: the input number of the body,
    `bodies.n` — outside, −1 and FRAC NaN — nothing wanted by the mode."""
    allow_in, allow_out = check_mode(mode)
    m = check_m(m)
    bx = np.ascontiguousarray(boxes, dtype=np.float64).reshape(-1, 6)
    cen = np.ascontiguousarray((bx[:, :3] + bx[:, 3:]) * 0.5)
    code = np.empty(len(bx), np.int64)
    frac = np.empty(len(bx), np.float64)
    if len(bx):
        ex, ey = bodies.offsets()
        args = bodies.args()

        def one(lo: int, hi: int) -> None:
            _boxes(*args, bodies.index.cap, bodies.n, bx, cen, m, ex, ey, allow_in, allow_out, lo, hi, code, frac)  # type: ignore[call-arg]

        strips.run(one, len(bx), threads=threads, tick=tick, size=int(chunk))
    return bodies.to_input(code.astype(np.int16)), frac
