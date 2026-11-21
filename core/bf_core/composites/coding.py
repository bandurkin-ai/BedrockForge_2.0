"""Coding of samples by wireframes (ТЗ 8.2.2, Т8.2-7; ADR 0018 §6, решение 6): which closed body every part of
every hole is inside of, and the code of every sample.

    bodies = [Body.of("Руда", xyz, tri, stats, ordered=True), …]   # by priority; or Body(name, Geometry.target(stats))
    st = body_stretches(samples, traj, dense, bodies, threads=…, tick=…) -> Stretches
        st.lines — the stretches of the holes inside the bodies: disjoint, by (hole, FROM); st.code — int32 [n],
        the body of every stretch (its place in the list); st.names — the CODE of every body; st.known
    c = code_samples(samples, st, split=True, threads=…, tick=…) -> Coded
        c.lines, c.rows, c.code (−1 — in no body), c.inside_frac, c.text() — the column CODE
    c = code_by_wireframes(samples, traj, dense, bodies, split=…) -> Coded      # both; report has `bodies`
    composites by domains:  domains.domain_stretches(samples, st.lines, st.code);  DOMAIN — st.names

`samples` — `intervals.build_lines` of the table; `traj` — the trajectories of the set; `dense` — per hole of the
samples, its index in `traj` (`coords.hole_index`), −1 — none. `bodies` may be a generator: one wireframe is held
at a time (`n_bodies` — their number, for the progress). `check_stats(name, stats)` refuses an open wireframe by
its stored measures before anything is read.

Stretches. `geo/intersect.py` as it is: `hole_crossings` (a tree of boxes in float64, the depth of a crossing on
the course itself to 1e−8 m — finer than the chords ≤ 1 m of the ТЗ) and `inside_intervals` (entries and exits by
the side of the triangle), a body at a time, the holes in strips of 4096 on threads; only the holes whose box
(grown by half of their longest course: an arc stays within that of its chord) meets the box of the body.
  * The collar check. A hole that does not cross a body has no entry to tell it by: when its collar is within
    the box of the body, a vertical segment from above the body down to the collar goes through the same
    kernels — the entries less the exits is the number of shells the collar is inside of; more than none — the
    hole is inside from the collar to its end. (A body with a cavity: a hole in the cavity is outside.)
  * A hole said to be inside from its collar (it leaves the body without having entered it) is checked the same
    way; a collar that is outside, or a segment that leaves more shells than it enters, means the shells of the
    wireframe look different ways — `wireframe_orientation`, as do neighbouring triangles wound opposite ways.
    A wireframe that is not a closed solid — `wireframe_open`. Both with a hint; nothing is coded wrong silently.
  * A depth below the end of the path is its end (`Trajectories.positions`, the coordinates of samples): a hole
    that ends inside a body is inside it to the bottom of its last sample.
  * Bodies that overlap: the stretch goes to the first of them in the list. A stretch not longer than
    `DEPTH_EPS` (1e−6 m) is left out; two stretches nearer than that have one boundary (bodies that share a
    face leave no gap for the middle of a sample to fall into), and of one body — are one stretch.

Samples. With the division (ТЗ 8.2.2: by default) a sample is cut at every boundary of the stretches inside it,
the parts tile it — the first FROM and the last TO are its own, bit for bit — and each takes the code of its
middle; `rows` — the sample of every part (values are copied: `values[c.rows]`, or `c.lines.take(column of the
table)`). A boundary nearer than 1e−6 m to an end of the sample, or to the cut before it, does not cut.
Without the division `lines` is `samples`; CODE — the body at the middle of the sample; INSIDE_FRAC — the share
of the length of the sample inside the body of its code, and for a sample without a code — inside any of the
bodies (boundaries within 1e−6 m of the ends of the sample are at its ends: a sample inside is exactly 1).
A hole without a trajectory has no code and INSIDE_FRAC is NaN, never 0; the journal counts such holes.
The result is the same bit for bit with any number of threads and any order of the holes in `traj`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from bf_core.drillholes.desurvey import SOURCE_VERTICAL, Trajectories
from bf_core.geo import intersect as gi
from bf_core.geo.checks import orientation_conflicts
from bf_core.geo.meshes import mesh_stats
from bf_core.geo.tri_kernels import mesh_arrays
from bf_core.jit import njit

from . import intervals as iv
from . import strips
from .coords import hole_index
from .intervals import DEPTH_EPS, CompositeError
from .strips import Tick

FIX_HINT = "Кодируют только замкнутые тела: проверьте каркас («Проверить каркас») и запустите «Исправить каркас»."
ABOVE_M = 1.0  # the segment of the collar check starts this far above the box of the body
_NAMED = 10  # names of bodies listed in a line of the journal

# --- kernels -----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _resolve(hole, body, frm, to, n_bodies, eps, o_hole, o_code, o_frm, o_to):  # type: ignore[no-untyped-def]
    """Stretches inside bodies, grouped by hole (those of one body apart) → disjoint stretches, each of the
    least body that covers it; returns their number. Not longer than `eps` — left out; not further than `eps`
    from the one before — begins where that one ends, and of the same body — is joined to it."""
    n = hole.shape[0]
    active = np.zeros(n_bodies, dtype=np.int64)
    k = 0
    i = 0
    while i < n:
        j = i
        while j < n and hole[j] == hole[i]:
            j += 1
        m = j - i
        depth = np.empty(2 * m, dtype=np.float64)
        what = np.empty(2 * m, dtype=np.int64)  # body + 1 — it begins, −(body + 1) — it ends
        for q in range(m):
            depth[2 * q] = frm[i + q]
            what[2 * q] = body[i + q] + 1
            depth[2 * q + 1] = to[i + q]
            what[2 * q + 1] = -(body[i + q] + 1)
        order = np.argsort(depth, kind="mergesort")
        cur = -1
        since = 0.0
        first = k
        for e in order:
            x = depth[e]
            new = cur
            if what[e] > 0:
                b = what[e] - 1
                active[b] += 1
                if cur < 0 or b < cur:
                    new = b
            else:
                b = -what[e] - 1
                active[b] -= 1
                if b == cur and active[b] == 0:
                    new = -1
                    for c in range(b + 1, n_bodies):
                        if active[c] > 0:
                            new = c
                            break
            if new != cur:
                if cur >= 0 and x - since > eps:
                    touch = k > first and since - o_to[k - 1] <= eps
                    if touch and o_code[k - 1] == cur:
                        o_to[k - 1] = x
                    else:
                        o_hole[k] = hole[i]
                        o_code[k] = cur
                        o_frm[k] = o_to[k - 1] if touch else since
                        o_to[k] = x
                        k += 1
                cur = new
                since = x
        i = j
    return k


@njit(cache=True, nogil=True)
def _split_strip(  # type: ignore[no-untyped-def]
    h0, h1, starts, frm, to, src, t_starts, t_frm, t_to, t_code, eps, counts, o_starts, o_frm, o_to, o_row,
    o_code, fill,
):  # fmt: skip
    """The samples of holes [h0, h1) cut at the boundaries of the stretches (`src[h]` — the hole of the
    stretches, −1 — none): the count pass, then the fill pass — FROM, TO, the sample and the code of every
    part. The boundaries of a hole are met once, in order: 2·j — the start of stretch j, 2·j + 1 — its end."""
    for h in range(h0, h1):
        j0 = 0
        j1 = 0
        if src[h] >= 0:
            j0 = t_starts[src[h]]
            j1 = t_starts[src[h] + 1]
        bi = 2 * j0
        jm = j0  # the first stretch that ends below the middle of the part at hand
        n = 0
        q = 0
        if fill:
            q = o_starts[h]
        for i in range(starts[h], starts[h + 1]):
            pos = frm[i]
            end = to[i]
            more = True
            while more:
                cut = end
                more = False
                while bi < 2 * j1:
                    x = t_frm[bi >> 1] if bi & 1 == 0 else t_to[bi >> 1]
                    if x >= end - eps:
                        break
                    bi += 1
                    if x > pos + eps:
                        cut = x
                        more = True
                        break
                if fill:
                    mid = pos + 0.5 * (cut - pos)
                    while jm < j1 and t_to[jm] <= mid:
                        jm += 1
                    o_frm[q] = pos
                    o_to[q] = cut
                    o_row[q] = i
                    o_code[q] = t_code[jm] if jm < j1 and t_frm[jm] <= mid else -1
                    q += 1
                n += 1
                pos = cut
        if not fill:
            counts[h] = n


@njit(cache=True, nogil=True)
def _middle_strip(h0, h1, starts, frm, to, src, known, t_starts, t_frm, t_to, t_code, eps, o_code, o_frac):  # type: ignore[no-untyped-def]
    """The code at the middle of every sample of holes [h0, h1) and the share of its length inside: in the
    stretches of that code, or — without a code — in any. A hole that is not `known` — NaN."""
    for h in range(h0, h1):
        j0 = 0
        j1 = 0
        if src[h] >= 0:
            j0 = t_starts[src[h]]
            j1 = t_starts[src[h] + 1]
        jm = j0
        jo = j0
        for i in range(starts[h], starts[h + 1]):
            f = frm[i]
            t = to[i]
            mid = f + 0.5 * (t - f)
            while jm < j1 and t_to[jm] <= mid:
                jm += 1
            c = -1
            if jm < j1 and t_frm[jm] <= mid:
                c = t_code[jm]
            o_code[i] = c
            if not known[h]:
                o_frac[i] = np.nan
                continue
            e = eps  # a boundary this near to an end of the sample is at the end; a quarter of a tiny sample
            if t - f < 4.0 * eps:
                e = 0.25 * (t - f)
            while jo < j1 and t_to[jo] <= f + e:
                jo += 1
            own = 0.0
            total = 0.0
            j = jo
            while j < j1 and t_frm[j] < t - e:
                a = t_frm[j]
                if a <= f + e:
                    a = f
                b = t_to[j]
                if b >= t - e:
                    b = t
                if b > a:
                    total += b - a
                    if t_code[j] == c:
                        own += b - a
                j += 1
            share = (own if c >= 0 else total) / (t - f)
            o_frac[i] = 1.0 if share > 1.0 else share


# --- bodies ------------------------------------------------------------------------------------------------


def check_stats(name: str, stats: Mapping[str, Any] | None) -> None:
    """CompositeError `wireframe_open` unless the measures of a wireframe (`wireframes.stats`) say it is a
    closed solid — before its geometry is read."""
    s = stats or {}
    if s.get("closed"):
        return
    opened, nonmanifold = int(s.get("open_edges") or 0), int(s.get("nonmanifold_edges") or 0)
    if opened:
        detail = f"открытых рёбер — {opened}"
    elif nonmanifold:
        detail = f"рёбер с тремя и более треугольниками — {nonmanifold}"
    else:
        detail = "каркас не проверен" if "closed" not in s else "это поверхность или пустой каркас, а не тело"
    raise CompositeError("wireframe_open", f"Каркас «{name}» не замкнут: {detail}.", FIX_HINT, param="wireframe_ids")


@dataclass(frozen=True, eq=False)
class Body:
    """A closed wireframe that codes samples: `name` — the CODE of the samples inside it; `target` — its tree
    of boxes with `solid` set ("outward" / "inward"), as `wireframes.exact.Geometry.target(stats)` gives it."""

    name: str
    target: gi.MeshTarget

    @classmethod
    def of(
        cls, name: str, xyz: Any, tri: Any, stats: Mapping[str, Any] | None = None, *, ordered: bool = False
    ) -> Body:
        """From the arrays of a wireframe and its stored measures (None, or closed without an orientation —
        measured here); `ordered` — the triangles are in spatial order already (a stored wireframe).
        CompositeError `wireframe_open`."""
        if stats is None or (stats.get("closed") and stats.get("orientation") not in ("outward", "inward")):
            stats = mesh_stats(*mesh_arrays(xyz, tri))
        check_stats(name, stats)
        return cls(name, gi.MeshTarget.from_stats(xyz, tri, stats, ordered=ordered))


def checked_target(body: Body) -> gi.MeshTarget:
    """The tree of a body that can code: said to be a solid, its neighbouring triangles wound one way."""
    target = body.target
    if target.solid is None or target.triangles == 0:
        check_stats(body.name, {"closed": False})  # (a surface, or a tree made without the measures)
    conflicts = orientation_conflicts(target.tri, len(target.xyz))
    if conflicts:
        raise CompositeError(
            "wireframe_orientation",
            f"Каркас «{body.name}»: нормали соседних треугольников не согласованы (рёбер — {conflicts}).",
            FIX_HINT,
            param="wireframe_ids",
        )
    return target


def _part(tick: Tick | None, lo: float, hi: float) -> Tick | None:
    return None if tick is None else (lambda f: tick(lo + (hi - lo) * f))


def _hole_boxes(traj: Trajectories, which: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The boxes (low, high — n × 3) that hold the paths of the holes `which`: the stations, grown by half of
    the longest course of the hole (a course of length L between two stations stays within L/2 of its chord)."""
    off = np.asarray(traj.offsets, dtype=np.int64)
    first = off[which]
    if first.shape[0] == 0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    xyz, md = np.asarray(traj.xyz), np.asarray(traj.md)
    course = np.zeros(md.shape[0], dtype=np.float64)
    course[1:] = md[1:] - md[:-1]
    course[first] = 0.0
    built = np.flatnonzero(off[1:] > off[:-1])  # the holes with stations: their stations are consecutive
    at = np.searchsorted(built, which)
    lo = np.fmin.reduceat(xyz, off[built], axis=0)[at]  # (a station without coordinates is not a corner)
    hi = np.fmax.reduceat(xyz, off[built], axis=0)[at]
    grow = 0.5 * np.maximum.reduceat(course, off[built])[at][:, None] + 2.0 * gi.SAGITTA_M
    return lo - grow, hi + grow


def _inside(traj: Trajectories, target: gi.MeshTarget, which: np.ndarray, threads: int, tick: Tick | None) -> Any:
    """`hole_crossings` and `inside_intervals` of one body over strips of the holes `which` (ascending): the
    hole, FROM, TO, «from the collar», «to the end» of every stretch, and the holes that cross the body."""

    def strip(lo: int, hi: int) -> tuple[np.ndarray, ...]:
        hits = gi.hole_crossings(traj, [target], holes=which[lo:hi])
        s = gi.inside_intervals(traj, hits)
        return s.hole, s.from_m, s.to_m, s.from_collar, s.to_end, np.unique(hits.hole)

    parts = strips.run(strip, which.shape[0], threads=threads, tick=tick)
    if not parts:
        empty = np.zeros(0, dtype=np.int64)
        return empty, np.zeros(0), np.zeros(0), np.zeros(0, dtype=np.bool_), np.zeros(0, dtype=np.bool_), empty
    return tuple(np.concatenate(col) for col in zip(*parts, strict=True))


def _collar_levels(
    traj: Trajectories, target: gi.MeshTarget, holes: np.ndarray, top: float, threads: int, tick: Tick | None
) -> np.ndarray:
    """How many shells of the body the collar of every hole of `holes` is inside of (the collar check of the
    module docstring): the entries less the exits of the vertical segment from Z = `top` down to the collar —
    the same kernels and the same rule for a point on a surface as the hole itself. int64 [n]."""
    n = holes.shape[0]
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    collar = np.asarray(traj.xyz)[np.asarray(traj.offsets)[holes]]
    xyz = np.repeat(collar, 2, axis=0)
    xyz[0::2, 2] = top
    md = np.zeros(2 * n, dtype=np.float64)
    md[1::2] = np.maximum(top - collar[:, 2], 0.0)  # a collar above the body: no segment — outside
    dirs = np.zeros((2 * n, 3), dtype=np.float64)
    dirs[:, 2] = -1.0
    probe = Trajectories("tangent", np.arange(0, 2 * n + 1, 2, dtype=np.int64), md, xyz, dirs,
                         np.full(n, SOURCE_VERTICAL, dtype=np.uint8), np.zeros(0, dtype=np.int64), 0, 0)  # fmt: skip

    def strip(lo: int, hi: int) -> np.ndarray:
        hits = gi.hole_crossings(probe, [target], holes=np.arange(lo, hi, dtype=np.int64))
        sign = (hits.event == gi.ENTER).astype(np.int64) - (hits.event == gi.LEAVE)
        return np.bincount(hits.hole - lo, weights=sign, minlength=hi - lo).astype(np.int64)

    return np.concatenate(strips.run(strip, n, threads=threads, tick=tick))


# --- stretches ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Stretches:
    lines: iv.Lines  # the stretches inside the bodies: disjoint, ordered by (hole, FROM); holes — of the samples
    code: np.ndarray  # int32 [n]: the body of every stretch — its place in the list
    names: list[str]  # the CODE of every body
    known: np.ndarray  # bool [holes of the samples]: the hole has a trajectory (else its samples get no code)
    ends: np.ndarray  # float64 [holes of the samples]: the length of the path, NaN — none
    report: dict[str, Any]  # `bodies` [{name, triangles, holes, inside, length}], holes, no_trajectory, …
    journal: list[str]  # lines for the job log


def _listed(names: list[str]) -> str:
    more = f" и ещё {len(names) - _NAMED}" if len(names) > _NAMED else ""
    return ", ".join(f"«{n}»" for n in names[:_NAMED]) + more


def body_stretches(
    samples: iv.Lines,
    traj: Trajectories,
    dense: Any,
    bodies: Iterable[Body],
    *,
    n_bodies: int | None = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> Stretches:
    """The stretches of the holes of the samples inside the bodies (module docstring). CompositeError — a body
    that is not a closed solid with its shells looking one way. The result does not depend on `threads`."""
    idx = np.ascontiguousarray(dense, dtype=np.int64)
    if idx.shape[0] != samples.n_holes:
        raise ValueError("dense must be per hole of the samples")
    off = np.asarray(traj.offsets, dtype=np.int64)
    known = (idx >= 0) & (idx < traj.n_holes)
    if traj.n_holes:
        at = np.where(known, idx, 0)
        known &= off[at + 1] > off[at]
    place = np.flatnonzero(known)  # the holes of the samples that have a path, by their index in `traj`
    place = place[np.argsort(idx[place], kind="stable")]
    which = idx[place]
    if which.shape[0] > 1 and bool((which[1:] == which[:-1]).any()):
        raise ValueError("two holes of the samples share a trajectory")
    back = np.full(traj.n_holes, -1, dtype=np.int64)
    back[which] = place
    length = traj.lengths()
    ends = np.full(samples.n_holes, np.nan)
    ends[place] = length[which]
    top = np.minimum(samples.frm[samples.starts[:-1]], 0.0)
    bottom = samples.to[samples.starts[1:] - 1]
    lo, hi = _hole_boxes(traj, which)
    collar = np.asarray(traj.xyz)[off[which]]
    total = n_bodies if n_bodies is not None else (len(bodies) if hasattr(bodies, "__len__") else 0)  # type: ignore[arg-type]
    names: list[str] = []
    per: list[dict[str, Any]] = []
    found: list[tuple[np.ndarray, ...]] = []
    for k, body in enumerate(bodies):
        span = (k / max(total, k + 1), (k + 1) / max(total, k + 1))
        target = checked_target(body)
        names.append(body.name)
        low, high = target.xyz.min(axis=0), target.xyz.max(axis=0)
        near = np.flatnonzero((lo <= high + target.pad).all(axis=1) & (hi >= low - target.pad).all(axis=1))
        mid = span[0] + 0.8 * (span[1] - span[0])
        hole, frm, to, opened, closed, crossed = _inside(traj, target, which[near], threads, _part(tick, span[0], mid))
        # the collar check: the holes near the body that do not cross it, and those said to start inside it
        c = collar[near]
        quiet = ((c >= low) & (c <= high)).all(axis=1) & ~np.isin(which[near], crossed)
        claimed = np.unique(hole[opened])
        asked = np.union1d(which[near][quiet], claimed)
        level = _collar_levels(traj, target, asked, float(high[2]) + ABOVE_M, threads, _part(tick, mid, span[1]))
        wrong = (level < 0) | (np.isin(asked, claimed) & (level <= 0))
        if wrong.any():
            raise CompositeError(
                "wireframe_orientation",
                f"Каркас «{body.name}»: оболочки ориентированы по-разному или пересекаются — стволы выходят из "
                f"тела, не войдя в него (скважин — {int(np.count_nonzero(wrong))}).",
                FIX_HINT,
                param="wireframe_ids",
            )
        whole = asked[(level > 0) & ~np.isin(asked, claimed)]
        hole = np.concatenate([hole, whole])
        frm = np.concatenate([frm, np.zeros(whole.shape[0])])
        to = np.concatenate([to, length[whole]])
        yes = np.ones(whole.shape[0], dtype=np.bool_)
        opened, closed = np.concatenate([opened, yes]), np.concatenate([closed, yes])
        per.append({
            "name": body.name, "triangles": int(target.triangles), "holes": int(np.unique(hole).shape[0]),
            "inside": int(whole.shape[0]), "length": strips.total((to - frm).tolist()),
        })  # fmt: skip
        if hole.shape[0]:
            p = back[hole]
            found.append((p, np.full(p.shape[0], k, dtype=np.int64), np.where(opened, top[p], frm),
                          np.where(closed, np.maximum(to, bottom[p]), to)))  # fmt: skip
        del body, target
    if tick is not None:
        tick(1.0)
    o_hole, o_code = np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int32)
    o_frm = o_to = np.zeros(0, dtype=np.float64)
    if found:
        p, b, f, t = (np.concatenate(col) for col in zip(*found, strict=True))
        order = np.lexsort((b, f, p))
        room = 2 * p.shape[0]
        o_hole, o_code = np.empty(room, dtype=np.int64), np.empty(room, dtype=np.int32)
        o_frm, o_to = np.empty(room, dtype=np.float64), np.empty(room, dtype=np.float64)
        n = _resolve(p[order], b[order], np.ascontiguousarray(f[order]), np.ascontiguousarray(t[order]),
                     len(names), DEPTH_EPS, o_hole, o_code, o_frm, o_to)  # fmt: skip
        o_hole, o_code, o_frm, o_to = o_hole[:n], o_code[:n].copy(), o_frm[:n].copy(), o_to[:n].copy()
    counts = np.bincount(o_hole, minlength=samples.n_holes)
    keep = counts > 0
    lines = iv.Lines(samples.holes[keep], strips.offsets(counts[keep]), o_frm, o_to)
    journal = []
    unseen = [name for k, name in enumerate(names) if not bool((o_code == k).any())]
    if unseen:
        journal.append(
            f"Каркасов, внутри которых стволов нет (или они целиком закрыты телами выше по списку), — {len(unseen)}: "
            f"{_listed(unseen)}."
        )
    report = {"bodies": per, "holes": int(place.shape[0]), "no_trajectory": int(samples.n_holes - place.shape[0]),
              "stretches": int(lines.n), "length": float(lines.lengths().sum())}  # fmt: skip
    return Stretches(lines, o_code, names, known, ends, report, journal)


# --- samples -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Coded:
    lines: iv.Lines  # the rows of the result: the parts of the samples (split) or the samples themselves
    rows: np.ndarray  # int64 [n]: the sample (its row in the line order of `samples`) of every row
    code: np.ndarray  # int32 [n]: the body — its place in `names`; −1 — in no body (CODE is empty)
    inside_frac: np.ndarray | None  # float64 [n]: INSIDE_FRAC, NaN — no trajectory; None — with the division
    names: list[str]  # the CODE of every body
    report: dict[str, Any]  # numbers for `dh_results.report`
    journal: list[str]  # lines for the job log

    def text(self) -> np.ndarray:
        """The column CODE: the name of the body of every row; None — in no body."""
        return np.array([*self.names, None], dtype=object)[self.code]  # −1 → the last one: None


def code_samples(
    samples: iv.Lines, stretches: Stretches, *, split: bool = True, threads: int = 1, tick: Tick | None = None
) -> Coded:
    """The code of every sample by the stretches inside the bodies (module docstring): `split` — the samples
    are cut at the entries and exits; else the code by the middle and INSIDE_FRAC. The result does not depend
    on `threads`."""
    st = stretches.lines
    if stretches.known.shape[0] != samples.n_holes:
        raise ValueError("the stretches are not of these samples")
    src = hole_index(st.holes, samples.holes)  # the hole of the stretches of every hole of the samples
    known = np.ascontiguousarray(stretches.known, dtype=np.bool_)
    nh = samples.n_holes
    spans = (samples.starts, samples.frm, samples.to, src)
    cover = (st.starts, st.frm, st.to, np.ascontiguousarray(stretches.code, dtype=np.int32), DEPTH_EPS)
    frac = None
    if split:
        counts = np.zeros(nh, dtype=np.int64)
        none_f, none_i, none_c = np.zeros(0), np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int32)
        strips.run(
            lambda lo, hi: _split_strip(lo, hi, *spans, *cover, counts, none_i, none_f, none_f, none_i, none_c, False),
            nh, threads=threads, tick=tick, span=(0.0, 0.4),
        )  # fmt: skip
        starts = strips.offsets(counts)
        n = int(starts[-1])
        frm, to = np.empty(n, dtype=np.float64), np.empty(n, dtype=np.float64)
        rows, code = np.empty(n, dtype=np.int64), np.empty(n, dtype=np.int32)
        strips.run(
            lambda lo, hi: _split_strip(lo, hi, *spans, *cover, counts, starts, frm, to, rows, code, True),
            nh, threads=threads, tick=tick, span=(0.4, 1.0),
        )  # fmt: skip
        lines = iv.Lines(samples.holes, starts, frm, to, rows if samples.rows is None else samples.rows[rows])
    else:
        lines, rows = samples, np.arange(samples.n, dtype=np.int64)
        code, frac = np.empty(samples.n, dtype=np.int32), np.empty(samples.n, dtype=np.float64)
        strips.run(
            lambda lo, hi: _middle_strip(lo, hi, *spans, known, *cover, code, frac), nh, threads=threads, tick=tick
        )
    length = lines.lengths()
    coded = code >= 0
    n_names = len(stretches.names)
    n_rows = np.bincount(code[coded], minlength=n_names)
    by_len = np.bincount(code[coded], weights=length[coded], minlength=n_names)
    per_hole = np.diff(samples.starts)
    lost, lost_rows = int(np.count_nonzero(~known)), int(per_hole[~known].sum())
    below = int(np.count_nonzero(samples.to > np.repeat(stretches.ends, per_hole) + DEPTH_EPS))  # NaN: False
    cut = int(np.count_nonzero(np.bincount(rows, minlength=samples.n) > 1))
    report = {
        "split": bool(split),
        "rows_in": samples.n,
        "rows": lines.n,
        "cut": cut,
        "length": float(length.sum()),
        "coded": int(np.count_nonzero(coded)),
        "coded_len": float(length[coded].sum()),
        "codes": [
            {"name": name, "rows": int(n_rows[k]), "length": float(by_len[k])} for k, name in enumerate(stretches.names)
        ],
        "no_trajectory": {"holes": lost, "rows": lost_rows},
        "below_end": below,
    }
    journal = []
    if lost:
        blank = "код пуст" if split else "код и INSIDE_FRAC пусты"
        journal.append(f"Скважин без траектории — {lost} (проб — {lost_rows}): {blank}. Проверьте координаты устьев.")
    if below:
        journal.append(f"Проб ниже конца траектории — {below}: код взят по концу траектории.")
    if split:
        journal.append(f"Проб разделено на границах тел — {cut}; строк — {lines.n} (было {samples.n}).")
    empty = [name for k, name in enumerate(stretches.names) if n_rows[k] == 0]
    if empty:
        journal.append(f"Каркасов без проб внутри — {len(empty)}: {_listed(empty)}.")
    return Coded(lines, rows, code, frac, list(stretches.names), report, journal)


def code_by_wireframes(
    samples: iv.Lines,
    traj: Trajectories,
    dense: Any,
    bodies: Iterable[Body],
    *,
    split: bool = True,
    n_bodies: int | None = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> Coded:
    """`body_stretches`, then `code_samples`; the report gets the numbers of the stretches (`bodies`, `holes`,
    `stretches`), the journal — their lines first."""
    st = body_stretches(samples, traj, dense, bodies, n_bodies=n_bodies, threads=threads, tick=_part(tick, 0.0, 0.85))
    c = code_samples(samples, st, split=split, threads=threads, tick=_part(tick, 0.85, 1.0))
    report = {**c.report, **{key: st.report[key] for key in ("bodies", "holes", "stretches")}}
    return Coded(c.lines, c.rows, c.code, c.inside_frac, c.names, report, st.journal + c.journal)


def warm() -> None:
    """Every kernel of this module (and those of the crossings) once, with the dtypes of the job: compiled
    into NUMBA_CACHE_DIR or loaded from it (`composites/warm.py`)."""
    from bf_core.drillholes.desurvey import Collars, desurvey

    v = np.array([[0.0, 0.0, 0.0], [8.0, 0.0, 0.0], [0.0, 8.0, 0.0], [0.0, 0.0, 8.0]])  # a tetrahedron
    t = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [0, 3, 2]], dtype=np.int32)
    traj = desurvey(Collars(np.arange(2), [1.25, 2.5], [1.5, 2.0], [6.0, 3.0], [9.0, 0.5]), n_holes=2)
    samples = iv.build_lines([0, 0, 0, 1], [0.0, 3.0, 6.0, 0.0], [3.0, 6.0, 9.0, 0.5])
    for split in (True, False):
        code_by_wireframes(samples, traj, [0, 1], [Body.of("A", v, t), Body.of("B", v + 1.0, t)], split=split)
