"""Splitting of boundary parents into sub-blocks and the whole fill (ТЗ 8.7.3, steps 3–5; ADR 0023 §6).

    j = join_boundaries(bodies, [boundary_parents(grid, mesh) …])      # triangles of all bodies per parent
    r = split_rows(grid, bodies, j, p, s, w, m=4, mode="inside")      # rows (P, s, w) → sub-blocks, code, FRAC
    f = fill_grid(grid, meshes, priorities=…, mode=…, m=…, threads=…)  # all stages: columns … merge

Splitting. A box (P, s, w) that no triangle of any body goes through (`boundary.tri_box_overlap`, the half-sizes
reduced by `(1 − boundary.SHRINK)`: a triangle that only touches the box does not cross it) is of one class —
that of its centre (`frac.decide_uniform`): one row, FRAC = 1. A box that triangles go through is halved along
every axis whose size is still over one sub-cell (the levels of an axis that is exhausted stay as they are): the
children get the triangles of the box that reach them. A sub-cell (w = 1·1·1) that is still crossed gets its code
and FRAC by the m³ points (`frac.leaf_counts`, `frac.decide`). With `split=False` the box itself is that last
level. Merging (step 5): children that tile their parent with one code and the same FRAC ∈ {0; 1} (or all
"nothing") become one row, recursively — up to the root row. The rows of a root are in the order (sz, sy, sx), the
roots in the order given: a fill goes by P ascending, so the file order (P, sz, sy, sx) holds.

Rows. code — the input number of the body (`frac.Bodies.input_of`), `bodies.n` — outside; a box with nothing wanted
by the mode is left out (`keep=False`, a fill) or kept with code −1 and FRAC NaN (`keep=True`, coding a model: the
rows are not ours to drop). A body mesh outside the grid, an unclosed or inconsistent one — 422 from
`columns.Mesh.of` / `columns.inside_columns` (the caller builds the meshes).

The fill. Parents no triangle goes through: the class of the centre ray (`columns.inside_columns`, the body of
the highest priority that contains it) — a whole parent, FRAC 1; the others — `split_rows`. The mode takes the
wanted ones: "inside" — parents in a body, "outside" — in none, "both" — all.

Threads. Roots go in fixed chunks (`CHUNK_ROOTS`), each to its own arrays, joined in the order of the roots: the
same bits with 1, 2, 4, 8 threads (ТЗ 8.0.2). Kernels are `nogil`, `cache=True` (ADR 0014).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from bf_core.blocks.fill import columns as fc
from bf_core.blocks.fill.boundary import SHRINK, Boundary, boundary_parents, tri_box_overlap
from bf_core.blocks.fill.columns import Mesh
from bf_core.blocks.fill.frac import (
    MAX_M,
    Bodies,
    check_m,
    check_mode,
    class_at,
    decide,
    decide_uniform,
    leaf_counts,
)
from bf_core.blocks.schema import GridV2
from bf_core.composites import strips
from bf_core.jit import njit

CHUNK_ROOTS = 64  # roots in a chunk of the kernel
STACK = 512  # frames of the walk (the depth is at most 21 levels of 8 children)

Tick = fc.Tick


# --- kernel ------------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _walk(  # type: ignore[no-untyped-def]
    v, t, lab, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, cap, nb, jptr, jtri,
    nx, ny, dx, dy, dz, dv0, dv1, dv2, rp, rs, rw, rslot, r0, r1,
    m, ex, ey, allow_in, allow_out, split, drop, shrink,
):  # fmt: skip
    """The roots r0 ≤ r < r1: (src root, s, w, code by rank, FRAC) of their rows, in the order of roots, then
    (sz, sy, sx). A frame of the stack: [kind (0 visit, 1 merge), s0, s1, s2, w0, w1, w2, list start, list length,
    rows before]."""
    zs = np.empty(cap, np.float64)
    ss = np.empty(cap, np.int8)
    ts = np.empty(cap, np.int32)
    par = np.zeros(max(nb, 1), np.int8)
    zq = np.empty(MAX_M, np.float64)
    cls = np.empty(MAX_M, np.int64)
    cnt = np.empty(nb + 1, np.int64)
    arena = np.empty(1024, np.int32)  # the lists of triangles of the nodes on the path, one after another
    stack = np.empty((STACK, 10), np.int64)
    ocap = 1024 + 64 * (r1 - r0)
    o_src = np.empty(ocap, np.int64)
    o_s = np.empty((ocap, 3), np.uint8)
    o_w = np.empty((ocap, 3), np.uint8)
    o_code = np.empty(ocap, np.int16)
    o_frac = np.empty(ocap, np.float64)
    n = 0
    m3 = m * m * m
    for r in range(r0, r1):
        pp = np.int64(rp[r])
        i = pp % nx
        j = (pp // nx) % ny
        k = pp // (nx * ny)
        slot = rslot[r]
        nl = 0
        if slot >= 0:
            nl = jptr[slot + 1] - jptr[slot]
            if nl > len(arena):
                arena = np.empty(2 * nl, np.int32)
            for e in range(nl):
                arena[e] = jtri[jptr[slot] + e]
        base = n
        sp = 0
        stack[0, 0] = 0
        for a in range(3):
            stack[0, 1 + a] = rs[r, a]
            stack[0, 4 + a] = rw[r, a]
        stack[0, 7] = 0
        stack[0, 8] = nl
        stack[0, 9] = 0
        sp = 1
        while sp > 0:
            sp -= 1
            kind = stack[sp, 0]
            s0, s1, s2 = stack[sp, 1], stack[sp, 2], stack[sp, 3]
            w0, w1, w2 = stack[sp, 4], stack[sp, 5], stack[sp, 6]
            if kind == 1:  # merge: do the rows since the visit tile this box with one code and FRAC?
                rb = stack[sp, 9]
                if n - rb >= 2:
                    cm = o_code[rb]
                    fm = o_frac[rb]
                    same = cm == -1 or fm == 1.0 or fm == 0.0
                    vol = 0
                    if same:
                        for q in range(rb, n):
                            if o_code[q] != cm or (cm != -1 and o_frac[q] != fm):
                                same = False
                                break
                            vol += np.int64(o_w[q, 0]) * np.int64(o_w[q, 1]) * np.int64(o_w[q, 2])
                    if same and vol == w0 * w1 * w2:
                        o_s[rb, 0], o_s[rb, 1], o_s[rb, 2] = s0, s1, s2
                        o_w[rb, 0], o_w[rb, 1], o_w[rb, 2] = w0, w1, w2
                        n = rb + 1
                continue
            lstart, llen = stack[sp, 7], stack[sp, 8]
            top = lstart + llen
            x0 = (i + s0 / dv0) * dx
            x1 = (i + (s0 + w0) / dv0) * dx
            y0 = (j + s1 / dv1) * dy
            y1 = (j + (s1 + w1) / dv1) * dy
            z0 = (k + s2 / dv2) * dz
            z1 = (k + (s2 + w2) / dv2) * dz
            cx = (i + (s0 + w0 * 0.5) / dv0) * dx
            cy = (j + (s1 + w1 * 0.5) / dv1) * dy
            cz = (k + (s2 + w2 * 0.5) / dv2) * dz
            hx = 0.5 * (x1 - x0) * (1.0 - shrink)
            hy = 0.5 * (y1 - y0) * (1.0 - shrink)
            hz = 0.5 * (z1 - z0) * (1.0 - shrink)
            if top + llen > len(arena):
                bigger = np.empty(max(2 * len(arena), top + llen), np.int32)
                bigger[:top] = arena[:top]
                arena = bigger
            nc = 0
            for e in range(llen):
                tri = arena[lstart + e]
                va, vb, vc = t[tri, 0], t[tri, 1], t[tri, 2]
                if tri_box_overlap(cx, cy, cz, hx, hy, hz, v[va, 0], v[va, 1], v[va, 2], v[vb, 0], v[vb, 1], v[vb, 2],
                                   v[vc, 0], v[vc, 1], v[vc, 2]):  # fmt: skip
                    arena[top + nc] = tri
                    nc += 1
            leaf = (not split) or (w0 == 1 and w1 == 1 and w2 == 1)
            if nc > 0 and not leaf:
                stack[sp, 0] = 1  # this frame becomes the merge of its children (its box stays)
                stack[sp, 9] = n
                sp += 1
                h0, h1, h2 = w0 // 2, w1 // 2, w2 // 2
                n0, n1, n2 = (2 if w0 >= 2 else 1), (2 if w1 >= 2 else 1), (2 if w2 >= 2 else 1)
                for c2 in range(n2 - 1, -1, -1):
                    for c1 in range(n1 - 1, -1, -1):
                        for c0 in range(n0 - 1, -1, -1):
                            stack[sp, 0] = 0
                            stack[sp, 1] = s0 + (h0 if c0 == 1 else 0)
                            stack[sp, 2] = s1 + (h1 if c1 == 1 else 0)
                            stack[sp, 3] = s2 + (h2 if c2 == 1 else 0)
                            stack[sp, 4] = w0 - h0 if c0 == 1 else (h0 if n0 == 2 else w0)
                            stack[sp, 5] = w1 - h1 if c1 == 1 else (h1 if n1 == 2 else w1)
                            stack[sp, 6] = w2 - h2 if c2 == 1 else (h2 if n2 == 2 else w2)
                            stack[sp, 7] = top
                            stack[sp, 8] = nc
                            stack[sp, 9] = 0
                            sp += 1
                continue
            cc = class_at(v, t, lab, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, nb, cx + ex, cy + ey, cz,
                          zs, ss, ts, par, cls)  # fmt: skip
            if nc == 0:
                code, frac = decide_uniform(nb, cc, allow_in, allow_out)
            else:
                leaf_counts(v, t, lab, ptr, idx, ncx, ncy, ci0, cj0, gx, gy, nb, x0, y0, z0, x1, y1, z1, cx, cy, cz,
                            m, ex, ey, zs, ss, ts, par, zq, cls, cnt)  # fmt: skip
                code, frac = decide(cnt, nb, cc, allow_in, allow_out, m3)
            if code >= 0 or not drop:
                if n == ocap:
                    ocap *= 2
                    b_src = np.empty(ocap, np.int64)
                    b_s = np.empty((ocap, 3), np.uint8)
                    b_w = np.empty((ocap, 3), np.uint8)
                    b_code = np.empty(ocap, np.int16)
                    b_frac = np.empty(ocap, np.float64)
                    b_src[:n] = o_src[:n]
                    b_s[:n] = o_s[:n]
                    b_w[:n] = o_w[:n]
                    b_code[:n] = o_code[:n]
                    b_frac[:n] = o_frac[:n]
                    o_src, o_s, o_w, o_code, o_frac = b_src, b_s, b_w, b_code, b_frac
                o_src[n] = r
                o_s[n, 0], o_s[n, 1], o_s[n, 2] = s0, s1, s2
                o_w[n, 0], o_w[n, 1], o_w[n, 2] = w0, w1, w2
                o_code[n] = code
                o_frac[n] = frac
                n += 1
        cnt_r = n - base
        if cnt_r > 1:  # the order of the file: (sz, sy, sx)
            keys = np.empty(cnt_r, np.int64)
            for q in range(cnt_r):
                keys[q] = (np.int64(o_s[base + q, 2]) << 16) + (np.int64(o_s[base + q, 1]) << 8) + o_s[base + q, 0]
            order = np.argsort(keys)
            t_s = o_s[base:n].copy()
            t_w = o_w[base:n].copy()
            t_code = o_code[base:n].copy()
            t_frac = o_frac[base:n].copy()
            for q in range(cnt_r):
                o = order[q]
                for a in range(3):
                    o_s[base + q, a] = t_s[o, a]
                    o_w[base + q, a] = t_w[o, a]
                o_code[base + q] = t_code[o]
                o_frac[base + q] = t_frac[o]
    return o_src[:n].copy(), o_s[:n].copy(), o_w[:n].copy(), o_code[:n].copy(), o_frac[:n].copy()


# --- joint triangles ---------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Joint:
    """The boundary parents of all bodies: `p` (uint64, ascending) and the triangles that go through each (CSR
    `ptr`, `tri` — numbers in `Bodies.t`, ascending within a parent)."""

    p: np.ndarray
    ptr: np.ndarray
    tri: np.ndarray


def join_boundaries(bodies: Bodies, boundaries: Sequence[Boundary]) -> Joint:
    """The boundaries of the bodies (in the order of `Bodies.of`'s meshes) joined by parent."""
    if len(boundaries) != bodies.n:
        raise ValueError("one boundary per body")
    if bodies.n == 1:
        return Joint(boundaries[0].p, boundaries[0].ptr, boundaries[0].tri)
    pp = np.concatenate([np.repeat(b.p, np.diff(b.ptr)) for b in boundaries])
    tt = np.concatenate([b.tri + np.int32(bodies.toff[k]) for k, b in enumerate(boundaries)])
    if len(pp) == 0:
        return Joint(np.zeros(0, np.uint64), np.zeros(1, np.int64), np.zeros(0, np.int32))
    order = np.argsort(pp, kind="stable")
    pp, tt = pp[order], tt[order]
    first = np.ones(len(pp), dtype=bool)
    np.not_equal(pp[1:], pp[:-1], out=first[1:])
    starts = np.flatnonzero(first)
    ptr = np.empty(len(starts) + 1, np.int64)
    ptr[:-1], ptr[-1] = starts, len(pp)
    return Joint(pp[starts].astype(np.uint64), ptr, np.ascontiguousarray(tt))


# --- rows --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Rows:
    """Sub-blocks: P (uint64), s, w (uint8 n×3), `code` (int16: the input number of the body, `outside` — outside,
    −1 — nothing wanted, `keep=True` only), `frac` (float64, NaN with code −1), `src` — the root row of each."""

    p: np.ndarray
    s: np.ndarray
    w: np.ndarray
    code: np.ndarray
    frac: np.ndarray
    src: np.ndarray
    outside: int


def _span(tick: Tick | None, a: float, b: float) -> Tick | None:
    if tick is None:
        return None
    return lambda f: tick(a + (b - a) * f)


def split_rows(
    grid: GridV2,
    bodies: Bodies,
    joint: Joint,
    p: object,
    s: object = None,
    w: object = None,
    *,
    m: int = 4,
    mode: str = "inside",
    split: bool = True,
    keep: bool = False,
    threads: int = 1,
    tick: Tick | None = None,
    span: tuple[float, float] = (0.0, 1.0),
    chunk: int = CHUNK_ROOTS,
) -> Rows:
    """Rows (P, s, w) — `s`, `w` None: whole parents — into sub-blocks of their code and FRAC (see the module
    doc). `joint` holds the parents with triangles; a row of another parent is of one class (its centre)."""
    allow_in, allow_out = check_mode(mode)
    m = check_m(m)
    nx, ny, _ = grid.counts
    dx, dy, dz = grid.regular("Заполнение")
    dv = grid.sub.div
    pa = np.ascontiguousarray(p, dtype=np.uint64).reshape(-1)
    n = len(pa)
    sa = np.zeros((n, 3), np.uint8) if s is None else np.ascontiguousarray(s, dtype=np.uint8).reshape(-1, 3)
    wa = np.empty((n, 3), np.uint8) if w is None else np.ascontiguousarray(w, dtype=np.uint8).reshape(-1, 3)
    if w is None:
        wa[:] = np.asarray(dv, dtype=np.uint8)
    slot = np.full(n, -1, np.int64)
    if len(joint.p) and n:
        pos = np.minimum(np.searchsorted(joint.p, pa), len(joint.p) - 1)
        slot = np.where(joint.p[pos] == pa, pos, -1).astype(np.int64)
    ex, ey = bodies.offsets()
    args = bodies.args()

    def one(lo: int, hi: int) -> tuple[np.ndarray, ...]:
        return _walk(  # type: ignore[no-any-return, call-arg]
            *args, bodies.index.cap, bodies.n, joint.ptr, joint.tri, nx, ny, dx, dy, dz, dv[0], dv[1], dv[2],
            pa, sa, wa, slot, lo, hi, m, ex, ey, allow_in, allow_out, split, not keep, SHRINK,
        )  # fmt: skip

    parts = strips.run(one, n, threads=threads, tick=tick, size=int(chunk), span=span)
    if not parts:
        return Rows(np.zeros(0, np.uint64), np.zeros((0, 3), np.uint8), np.zeros((0, 3), np.uint8),
                    np.zeros(0, np.int16), np.zeros(0), np.zeros(0, np.int64), bodies.n)  # fmt: skip
    src = np.concatenate([x[0] for x in parts])
    return Rows(
        pa[src],
        np.concatenate([x[1] for x in parts]),
        np.concatenate([x[2] for x in parts]),
        bodies.to_input(np.concatenate([x[3] for x in parts])),
        np.concatenate([x[4] for x in parts]),
        src,
        bodies.n,
    )


# --- the whole fill ----------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Fill:
    """The rows of a fill in the order of the files (P, sz, sy, sx): `p`, `s`, `w`, `code` (the input number of
    the wireframe, `outside` — outside), `frac`. `stages` — seconds of «columns», «boundary», «split», «merge»;
    `boundary`, `pairs` — boundary parents and their (parent, triangle) pairs; `whole` — parents taken by the
    centre ray."""

    p: np.ndarray
    s: np.ndarray
    w: np.ndarray
    code: np.ndarray
    frac: np.ndarray
    outside: int
    stages: dict[str, float] = field(default_factory=dict)
    boundary: int = 0
    pairs: int = 0
    whole: int = 0

    @property
    def parents(self) -> int:
        return int(len(np.unique(self.p))) if len(self.p) else 0


def fill_grid(
    grid: GridV2,
    meshes: Sequence[Mesh],
    *,
    priorities: Sequence[int | None] | None = None,
    mode: str = "inside",
    m: int = 4,
    threads: int = 1,
    tick: Tick | None = None,
    unit: tuple[float, float] | None = None,
) -> Fill:
    """Fills the grid with the closed bodies `meshes` (`columns.Mesh`, the local axes of `grid`; `grid.sub.div` —
    the levels L). Stages as the module doc; `tick(fraction)` after every strip."""
    allow_in, allow_out = check_mode(mode)
    m = check_m(m)
    bodies = Bodies.of(grid, meshes, priorities, unit=unit)
    nb = bodies.n
    cells = grid.cells
    stages: dict[str, float] = {}
    t0 = time.perf_counter()
    # the class of every parent by the centre ray: the body of the highest priority (written last) that contains it
    cls = np.full(cells, nb, np.int16)
    for step, rank in enumerate(range(nb - 1, -1, -1)):
        b = int(bodies.input_of[rank])
        cols = fc.inside_columns(grid, meshes[b], unit=bodies.unit, threads=threads,
                                 tick=_span(tick, 0.25 * step / nb, 0.25 * (step + 1) / nb), index=b)  # fmt: skip
        np.copyto(cls, np.int16(b), where=cols.inside.view(np.bool_))
        del cols
    t1 = time.perf_counter()
    stages["columns"] = t1 - t0
    boundaries = [
        boundary_parents(
            grid, mesh, threads=threads, tick=_span(tick, 0.25 + 0.05 * b / nb, 0.25 + 0.05 * (b + 1) / nb)
        )
        for b, mesh in enumerate(meshes)
    ]
    joint = join_boundaries(bodies, boundaries)
    del boundaries
    t2 = time.perf_counter()
    stages["boundary"] = t2 - t1
    rows = split_rows(grid, bodies, joint, joint.p, m=m, mode=mode, threads=threads, tick=tick, span=(0.3, 0.95))
    t3 = time.perf_counter()
    stages["split"] = t3 - t2
    # the parents the tests did not touch, whole: the wanted classes; then the two runs go into one by P
    wanted = np.zeros(cells, bool)
    if allow_in:
        np.less(cls, nb, out=wanted)
    if allow_out:
        wanted |= cls == nb
    wanted[joint.p.astype(np.intp)] = False
    whole = np.flatnonzero(wanted).astype(np.uint64)
    del wanted
    nbr, nf = len(rows.p), len(whole)
    dest_b = np.arange(nbr) + np.searchsorted(whole, rows.p, side="left")
    dest_f = np.arange(nf) + np.searchsorted(rows.p, whole, side="left")
    total = nbr + nf
    p_all = np.empty(total, np.uint64)
    s_all = np.zeros((total, 3), np.uint8)
    w_all = np.empty((total, 3), np.uint8)
    code_all = np.empty(total, np.int16)
    frac_all = np.ones(total, np.float64)
    p_all[dest_b], p_all[dest_f] = rows.p, whole
    s_all[dest_b] = rows.s
    w_all[dest_b], w_all[dest_f] = rows.w, np.asarray(grid.sub.div, dtype=np.uint8)
    code_all[dest_b], code_all[dest_f] = rows.code, cls[whole.astype(np.intp)]
    frac_all[dest_b] = rows.frac
    if tick is not None:
        tick(1.0)
    stages["merge"] = time.perf_counter() - t3
    return Fill(p_all, s_all, w_all, code_all, frac_all, nb, stages, len(joint.p), len(joint.tri), nf)
