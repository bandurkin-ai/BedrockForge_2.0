"""Solids from contours: stitching closed strings drawn on a series of sections (ТЗ 7.23; ADR 0015 §7).

    stitch_contours(contours, ties, ...)   contours on parallel-ish sections → one triangulated solid

How a series is stitched:

* The common normal is the mean of the contours' plane normals (Newell's vectors); contours are grouped into
  sections by their position along it (tolerance: a tenth of the median spacing, at least 1e-6 m) and every
  contour is turned counter-clockwise as seen from the tip of the normal. All geometry is computed in
  float64 relative to the centre of the data; vertices of the input contours are copied to the output
  untouched (bit-identical), first and in input order; new vertices (caps, branch cuts) follow.
* Pairs of contours on neighbouring sections: assignment by centres (`linear_sum_assignment`); 1 : 1 — a
  strip (`geo/stitch_strip.py`), 1 : N — a branching (`geo/stitch_branch.py`).
* Tie lines (2, 3): both ends on vertices of two contours that are stitched to each other (within
  `tie_tolerance`, by default 1e-3 of the typical contour size; KD-tree); each becomes an edge of the result.
* Ends: none, flat cap, pinch-out to a point or to a ridge line, or continuation by a share of the spacing.
* Checks: closedness and orientation always; self-intersections with `geo/checks.py` on the final
  coordinates, and what it reports is confirmed by an exact pairwise search (triangles lying in one plane
  within rounding can give the fast check a false alarm). A failure names the pair of sections.

Known limits (ТЗ 7.23): a contour must not be turned more than 60° away from the sections; the chord of a
branching is a single straight cut, so children that no straight line separates, or contours on
neighbouring sections that do not correspond to each other, may give tubes that cross — this is reported
with the pair of sections (tie lines, an extra contour or other contours help). Tie lines are honoured inside
one strip; in a branching a tie must stay within the part of the parent that goes to its child.
Self-intersections are not repaired.

Progress and cancellation: `progress(fraction, message)` is called between the strips, between the portions
of the search of a large pair and during the self-intersection check; it may raise to cancel (JobContext.tick).
The strips are built at once in threads (`threads`, `stitch_strip.strips`: the same bits for any number of
them); `progress` is called from the calling thread only.

Works used: H. Fuchs, Z. M. Kedem, S. P. Uselton, «Optimal surface reconstruction from planar contours», 1977.
Ported from BF `geo/stitch.py` (`stitch_contours`, `_sections`, `_links`, `_edge_faults`, `_pierced`,
`_first_crossing`) with the same results. Differences: errors with a code and a hint, a limit on the size of
a pair of contours, progress inside the large steps, the volume in the report.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bf_core.geo.checks import orientation_conflicts, self_intersections
from bf_core.geo.meshes import edge_summary
from bf_core.geo.polygon import StitchError, area_vector, centroid, clean_polygon, plane_axes, signed_area
from bf_core.geo.stitch_branch import Series
from bf_core.geo.stitch_strip import MAX_SPANS, strips
from bf_core.jit import njit

CAPS = ("none", "flat", "point", "line", "extend")
# Tie ends snap to contour vertices within this share of the typical contour size.
TIE_SNAP = 1e-3
TIE_HINT = "Поставьте концы тай-линии на вершины контуров соседних разрезов, которые сшиваются друг с другом."
SECTIONS_HINT = "Выберите замкнутые контуры, нарисованные на параллельных разрезах (хотя бы на двух)."

StitchProgress = Callable[[float, str], None] | None


@dataclass
class StitchResult:
    vertices: np.ndarray  # (n, 3) float64; the input contour vertices first, in input order, bit-identical
    triangles: np.ndarray  # (m, 3) int32, consistently oriented, normals outward when closed
    report: dict[str, Any] = field(default_factory=dict)


def _sections(pos: np.ndarray, tolerance: float | None) -> tuple[list[list[int]], float]:
    """Contours grouped by position along the normal; the tolerance used."""
    order = np.argsort(pos, kind="stable")
    gaps = np.diff(pos[order])
    if tolerance is None:
        widest = float(gaps.max()) if len(gaps) else 0.0
        real = gaps[gaps > max(1e-6, 0.05 * widest)]
        tolerance = max(1e-6, 0.1 * float(np.median(real))) if len(real) else 1e-6
    out = [[int(order[0])]]
    for i, gap in enumerate(gaps):
        if gap < tolerance:
            out[-1].append(int(order[i + 1]))
        else:
            out.append([int(order[i + 1])])
    return [sorted(s) for s in out], float(tolerance)


def _links(cent: np.ndarray, lower: list[int], upper: list[int]) -> list[tuple[list[int], list[int]]]:
    """Which contours of two neighbouring sections are stitched: [(lower contours, upper contours)].

    Equal counts: one to one, the pairing with the smallest total distance between centres.
    Otherwise every contour of the smaller group first gets one of the larger group (same rule),
    and each remaining contour joins the nearest one of the smaller group (a branching).
    """
    from scipy.optimize import linear_sum_assignment

    small, large = (lower, upper) if len(lower) <= len(upper) else (upper, lower)
    dist = np.linalg.norm(cent[small][:, None, :] - cent[large][None, :, :], axis=2)
    rows, cols = linear_sum_assignment(dist)
    children: dict[int, list[int]] = {s: [] for s in small}
    for r, c in zip(rows, cols, strict=True):
        children[small[r]].append(large[c])
    for c in set(range(len(large))) - set(cols.tolist()):
        children[small[int(np.argmin(dist[:, c]))]].append(large[c])
    out = []
    for s in small:
        kids = sorted(children[s])
        out.append(([s], kids) if small is lower else (kids, [s]))
    return out


def _edge_faults(tris: np.ndarray, vertex_count: int) -> np.ndarray:
    """Triangles with an edge that is open, shared by more than two, or walked twice the same way."""
    t = tris.astype(np.int64)
    m = len(t)
    a = np.concatenate([t[:, 0], t[:, 1], t[:, 2]])
    b = np.concatenate([t[:, 1], t[:, 2], t[:, 0]])
    owner = np.tile(np.arange(m), 3)
    plain = np.minimum(a, b) * vertex_count + np.maximum(a, b)
    _, inverse, counts = np.unique(plain, return_inverse=True, return_counts=True)
    bad = counts[inverse] != 2
    _, inverse, counts = np.unique(a * vertex_count + b, return_inverse=True, return_counts=True)
    bad |= counts[inverse] > 1
    return np.unique(owner[bad])


@njit(cache=True, nogil=True)
def _pierced(v, ta, tb, tol):  # type: ignore[no-untyped-def]
    """An edge of triangle ta goes through triangle tb: its ends are on opposite sides of tb's
    plane, farther than tol from it, and the crossing point is inside tb (or on its outline)."""
    a0, a1, a2 = v[tb[0]], v[tb[1]], v[tb[2]]
    e1 = a1 - a0
    e2 = a2 - a0
    n = np.array([e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]])
    size = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2])
    if size == 0.0:
        return False
    n = n / size
    d = np.empty(3)
    for k in range(3):
        p = v[ta[k]]
        d[k] = (p[0] - a0[0]) * n[0] + (p[1] - a0[1]) * n[1] + (p[2] - a0[2]) * n[2]
    for k in range(3):
        dp, dq = d[k], d[(k + 1) % 3]
        if not ((dp > tol and dq < -tol) or (dp < -tol and dq > tol)):
            continue
        p, q = v[ta[k]], v[ta[(k + 1) % 3]]
        x = p + (dp / (dp - dq)) * (q - p)
        inside = True
        for c in range(3):
            b0, b1 = v[tb[c]], v[tb[(c + 1) % 3]]
            f = b1 - b0
            g = x - b0
            w = (
                (f[1] * g[2] - f[2] * g[1]) * n[0]
                + (f[2] * g[0] - f[0] * g[2]) * n[1]
                + (f[0] * g[1] - f[1] * g[0]) * n[2]
            )
            if w < -1e-9 * size:
                inside = False
                break
        if inside:
            return True
    return False


@njit(cache=True, nogil=True)
def _first_crossing(v, t, lo, hi, order, axis, tol):  # type: ignore[no-untyped-def]
    """(i, j): the first pair of triangles without a common vertex that go through each other, or (−1, −1).

    Sweep along `axis` over triangles sorted by the lower bound of their boxes. Unlike the fast
    grid check this one is not misled by triangles that lie in one plane or touch a plane with an
    edge (walls continued by an extension, a vertex inserted on a straight edge): there the
    crossing point is a ratio of rounding errors.
    """
    m = len(t)
    o1, o2 = (axis + 1) % 3, (axis + 2) % 3
    for a in range(m):
        i = order[a]
        for b in range(a + 1, m):
            j = order[b]
            if lo[j, axis] > hi[i, axis]:
                break
            if lo[j, o1] > hi[i, o1] or lo[i, o1] > hi[j, o1] or lo[j, o2] > hi[i, o2] or lo[i, o2] > hi[j, o2]:
                continue
            shared = False
            for x in range(3):
                for y in range(3):
                    if t[i, x] == t[j, y]:
                        shared = True
            if shared:
                continue
            if _pierced(v, t[i], t[j], tol) or _pierced(v, t[j], t[i], tol):
                return i, j
    return -1, -1


def _crossing(v: np.ndarray, t: np.ndarray) -> tuple[int, int]:
    """Indices of two triangles that really cross (see _first_crossing), or (−1, −1)."""
    corners = v[t]
    lo, hi = corners.min(axis=1), corners.max(axis=1)
    extent = v.max(axis=0) - v.min(axis=0)
    axis = int(np.argmax(extent))
    order = np.argsort(lo[:, axis], kind="stable")
    i, j = _first_crossing(
        np.ascontiguousarray(v), np.ascontiguousarray(t, dtype=np.int64), lo, hi, order, axis,
        1e-8 * float(np.linalg.norm(extent)),
    )  # fmt: skip
    return int(i), int(j)


def _signed_volume(v: np.ndarray, t: np.ndarray) -> float:
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0


def _where(between: tuple[int, int]) -> str:
    if between[0] == between[1]:
        return f"На торце у разреза {between[0] + 1}"
    return f"Между контурами разрезов {between[0] + 1} и {between[1] + 1}"


def _contours_of(s: Series, groups: list[int]) -> tuple[int, ...]:
    return tuple(sorted({int(c) for g in groups for c in s.groups[g]["contours"]}))


def _named(contours: tuple[int, ...]) -> str:
    if not contours:
        return ""
    if len(contours) == 1:
        return f" (контур {contours[0] + 1})"
    return f" (контуры {', '.join(str(c + 1) for c in contours[:-1])} и {contours[-1] + 1})"


def _normal_and_sections(s: Series, clean: list[np.ndarray], section_tolerance: float | None) -> tuple:
    """Common normal (put on the series), sections, their levels and the section of every contour."""
    count = len(clean)
    normals = np.empty((count, 3))
    for i in range(count):
        area = area_vector(s.xyz(np.arange(s.starts[i], s.starts[i + 1])))
        length = float(np.linalg.norm(area))
        if length <= 1e-12 * max(float(np.ptp(clean[i], axis=0).max()) ** 2, 1e-300):
            raise StitchError(f"Контур {i + 1} вырожден: у него нулевая площадь.", code="degenerate_polygon")
        normals[i] = area / length
    reference = normals[0]
    normals[normals @ reference < 0] *= -1.0
    normal = normals.mean(axis=0)
    if float(np.linalg.norm(normal)) < 1e-9:
        raise StitchError(
            "Плоскости контуров слишком различаются: общее направление разрезов не определяется.",
            code="stitch_sections", hint=SECTIONS_HINT,
        )  # fmt: skip
    normal /= np.linalg.norm(normal)
    cent3 = np.array([s.xyz(np.arange(s.starts[i], s.starts[i + 1])).mean(axis=0) for i in range(count)])
    pos = cent3 @ normal
    if pos[0] > pos[-1] or (pos[0] == pos[-1] and normal[int(np.argmax(np.abs(normal)))] < 0):
        normal, pos = -normal, -pos
    for i in range(count):
        if abs(float(normals[i] @ normal)) < 0.5:
            raise StitchError(
                f"Контур {i + 1} лежит не в плоскости разрезов: он развёрнут к остальным больше чем на 60°.",
                code="stitch_sections", hint=SECTIONS_HINT,
            )  # fmt: skip
    s.normal = normal
    s.u, s.v = plane_axes(normal)
    sections, tolerance = _sections(pos, section_tolerance)
    if len(sections) < 2:
        raise StitchError(
            "Все контуры лежат на одном разрезе: для сшивки нужны контуры хотя бы на двух разрезах.",
            code="stitch_sections", hint=SECTIONS_HINT,
        )  # fmt: skip
    level = np.array([float(pos[sec].mean()) for sec in sections])
    section_of = np.empty(count, dtype=np.int64)
    for k, sec in enumerate(sections):
        section_of[sec] = k
    return normal, sections, tolerance, level, section_of


def _place_ties(
    s: Series, ties: Sequence[Any], jobs: list[dict[str, Any]], section_of: np.ndarray, tolerance: float
) -> int:
    """Every tie line → (index in the lower loop, index in the upper loop) of its strip; returns their number."""
    if not len(ties):
        return 0
    from scipy.spatial import cKDTree

    job_of = {(job["a"], job["b"]): job for job in jobs}
    tree = cKDTree(s.local)
    for number, tie in enumerate(ties, start=1):
        line = np.asarray(tie, dtype=np.float64)
        if line.shape != (2, 3) or not np.isfinite(line).all():
            raise StitchError(f"Тай-линия {number}: ожидаются две точки (2, 3).", code="stitch_ties", hint=TIE_HINT)
        dist, hit = tree.query(line - s.centre)
        for end in range(2):
            if dist[end] > tolerance:
                raise StitchError(
                    f"Тай-линия {number}: её конец не совпадает ни с одной вершиной контуров "
                    f"(до ближайшей {dist[end]:.3f} м, допуск {tolerance:.3f} м).",
                    code="stitch_ties", hint=TIE_HINT,
                )  # fmt: skip
        ca, cb = (int(np.searchsorted(s.starts, h, side="right")) - 1 for h in hit)
        ga, gb = int(hit[0]), int(hit[1])
        if section_of[ca] > section_of[cb]:
            ca, cb, ga, gb = cb, ca, gb, ga
        between = (int(section_of[ca]), int(section_of[cb]))
        if between[1] - between[0] != 1:
            raise StitchError(
                f"Тай-линия {number} должна соединять контуры соседних разрезов "
                f"(сейчас — разрезы {between[0] + 1} и {between[1] + 1}).",
                between, code="stitch_ties", hint=TIE_HINT,
            )  # fmt: skip
        job = job_of.get((ca, cb))
        if job is None:
            raise StitchError(
                f"Тай-линия {number} соединяет контуры {ca + 1} и {cb + 1}, которые не сшиваются друг с другом.",
                between, code="stitch_ties", hint=TIE_HINT,
            )  # fmt: skip
        la, lb = s.loops[job["lower"]], s.loops[job["upper"]]
        if ga not in la or gb not in lb:
            raise StitchError(
                f"Тай-линия {number}: при разветвлении её вершина попала в часть контура, которая сшивается "
                "с другой ветвью.",
                between, code="stitch_ties", hint=TIE_HINT,
            )  # fmt: skip
        job["ties"].append((la.index(ga), lb.index(gb), number))
    return len(ties)


def stitch_contours(
    contours: Sequence[Any],
    ties: Sequence[Any] = (),
    *,
    start_cap: str = "flat",
    end_cap: str = "flat",
    cap_distance: float = 0.5,
    section_tolerance: float | None = None,
    tie_tolerance: float | None = None,
    check: bool = True,
    progress: StitchProgress = None,
    threads: int | None = None,
) -> StitchResult:
    """A triangulated solid from closed contours drawn on a series of sections.

    contours            (k, 3) arrays, closed without a repeated last vertex (a repeated one is
                        dropped), any winding, any orientation of the sections.
    ties                (2, 3) arrays: a tie line between vertices of two contours that are
                        stitched to each other; it becomes an edge of the result.
    start_cap, end_cap  "none" | "flat" | "point" | "line" | "extend" for the first and the last
                        section. The first section is the end of the series nearer to the first
                        contour of the list.
    cap_distance        length of "point", "line" and "extend" caps as a share of the distance
                        to the neighbouring section.
    section_tolerance   contours closer than this along the common normal are on one section
                        (default: a tenth of the median spacing, at least 1e-6).
    tie_tolerance       how far a tie end may be from a contour vertex (default: 1e-3 of the
                        typical contour size).
    check               look for self-intersections; StitchError names the pair of sections.
    progress            callback (fraction, message); may raise to cancel. Called from this thread only.
    threads             strips built at once (None: up to 4, by the cores); the result is the same for any.

    The vertices of the contours come first in the result, in input order and bit-identical;
    new vertices (caps, branch cuts) follow. Report: sections, pairs, ties_used, tolerances, normal,
    warnings, closed and the volume of a closed solid (None otherwise).
    """
    now, doing = 0.0, ""

    def tell(fraction: float, message: str) -> None:
        nonlocal now, doing
        now, doing = float(fraction), message
        if progress is not None:
            progress(now, doing)

    def tick() -> None:  # inside a long step: the same fraction and message again (a chance to cancel)
        if progress is not None:
            progress(now, doing)

    def checking(fraction: float) -> None:
        tell(0.88 + 0.1 * fraction, doing)

    for name, value in (("start_cap", start_cap), ("end_cap", end_cap)):
        if value not in CAPS:
            raise StitchError(
                f"Неизвестный способ замыкания торца «{value}» ({name}): допустимы {', '.join(CAPS)}.",
                code="bad_parameter", hint="Выберите способ замыкания торца из списка.",
            )  # fmt: skip
    if not (cap_distance > 0 and math.isfinite(cap_distance)):
        raise StitchError(
            "Длина замыкания торца (доля расстояния между разрезами) должна быть больше нуля.",
            code="bad_parameter", hint="Задайте положительную долю, например 0,5.",
        )  # fmt: skip
    tell(0.0, "Подготовка контуров")
    clean = [clean_polygon(c, f"Контур {i + 1}") for i, c in enumerate(contours)]
    if len(clean) < 2:
        raise StitchError(
            "Для сшивки нужны контуры хотя бы на двух разрезах.", code="stitch_sections", hint=SECTIONS_HINT
        )
    s = Series(clean)
    s.tick = tick
    count = len(clean)
    normal, sections, section_tolerance, level, section_of = _normal_and_sections(s, clean, section_tolerance)

    # ---- loops: every contour counter-clockwise as seen from the tip of the normal
    cent2 = np.empty((count, 2))
    sizes = np.empty(count)
    for i in range(count):
        ids = list(range(int(s.starts[i]), int(s.starts[i + 1])))
        q = s.flat(ids)
        if signed_area(q) < 0:
            ids.reverse()
        s.loops.append(ids)
        s.family.append([i])
        cent2[i] = centroid(q)
        sizes[i] = float(np.linalg.norm(np.ptp(clean[i], axis=0)))
        if (np.linalg.norm(np.roll(q, -1, axis=0) - q, axis=1) == 0).any():
            s.warnings.append(f"Контур {i + 1}: есть совпадающие соседние вершины")

    # ---- which contours are stitched; branch cuts (all of them before any strip is built)
    jobs: list[dict[str, Any]] = []  # {"between", "lower": loop id, "upper": loop id, "a", "b", "pair", ...}
    pairs: list[dict[str, Any]] = []  # report entries
    for k in range(len(sections) - 1):
        gap = float(level[k + 1] - level[k])
        for lower, upper in _links(cent2, sections[k], sections[k + 1]):
            pairs.append(
                {"sections": [k, k + 1], "a": lower, "b": upper, "triangles": 0, "branch": len(lower) + len(upper) > 2}
            )
            if len(lower) == 1:
                parts = s.cut(lower[0], lower[0], upper, 1.0, gap, (k, k + 1))
                found = [(part, child, lower[0], child) for part, child in parts]
            else:
                parts = s.cut(upper[0], upper[0], lower, -1.0, gap, (k, k + 1))
                found = [(child, part, child, upper[0]) for part, child in parts]
            for lo_id, up_id, a, b in found:
                jobs.append(
                    {"between": (k, k + 1), "lower": lo_id, "upper": up_id, "a": a, "b": b,
                     "pair": len(pairs) - 1, "gap": gap, "ties": []}
                )  # fmt: skip
    for job in jobs:
        n, m = len(s.loops[job["lower"]]), len(s.loops[job["upper"]])
        if n * m > MAX_SPANS:
            raise StitchError(
                f"{_where(job['between'])} контуры слишком подробны для сшивки: {n} и {m} вершин "
                f"(допустимо произведение до {MAX_SPANS:,}).".replace(",", " "),
                job["between"], code="stitch_too_large",
                hint="Проредите контуры (операция «Проредить» редактора) и повторите.",
            )  # fmt: skip

    # ---- tie lines
    tolerance = float(tie_tolerance) if tie_tolerance is not None else TIE_SNAP * float(np.median(sizes))
    ties_used = _place_ties(s, ties, jobs, section_of, tolerance)

    # ---- strips: independent of each other, built at once in threads (stitch_strip.strips)
    def started(done: int) -> None:
        tell(0.05 + 0.75 * done / len(jobs), f"Сшивка контуров: {done + 1} из {len(jobs)}")

    items = []
    for job in jobs:
        la = np.asarray(s.loops[job["lower"]], dtype=np.int64)
        lb = np.asarray(s.loops[job["upper"]], dtype=np.int64)
        items.append((s.flat(la), s.flat(lb), la, lb, job["gap"], [(i, j) for i, j, _ in job["ties"]]))
    built = strips(items, threads=threads, tick=tick, done=started)
    for job, tris in zip(jobs, built, strict=True):
        if tris is None:
            numbers = ", ".join(str(n) for _, _, n in job["ties"])
            raise StitchError(
                f"{_where(job['between'])} тай-линии пересекаются (линии {numbers}): порядок вершин вдоль "
                "одного контура не совпадает с порядком вдоль другого.",
                job["between"], code="stitch_ties", hint="Удалите или переставьте пересекающиеся тай-линии.",
            )  # fmt: skip
        s.emit(job["between"], tris, (job["a"], job["b"]))
        pairs[job["pair"]]["triangles"] += len(tris)

    # ---- caps
    tell(0.82, "Замыкание торцов")
    last = len(sections) - 1
    for kind, section, outward, spacing in (
        (start_cap, 0, -1.0, level[1] - level[0]),
        (end_cap, last, 1.0, level[last] - level[last - 1]),
    ):
        for contour in sections[section]:
            s.cap(kind, s.loops[contour], outward, float(spacing), cap_distance, section, contour)

    # ---- assembly and checks
    tell(0.88, "Проверка тела")
    local = s.xyz(np.arange(len(s.local) + len(s.added)))
    tris = np.vstack([g["tris"] for g in s.groups])
    # New vertices left without triangles (a shortened ridge) are dropped; the given ones always stay.
    used = np.zeros(len(local), dtype=bool)
    used[: len(s.given)] = True
    used[tris] = True
    if not used.all():
        remap = np.cumsum(used) - 1
        local = local[used]
        tris = remap[tris]
        for g in s.groups:
            g["tris"] = remap[g["tris"]]
    owner = np.repeat(np.arange(len(s.groups)), [len(g["tris"]) for g in s.groups])
    open_edges, nonmanifold = edge_summary(tris, len(local))
    conflicts = orientation_conflicts(tris, len(local))
    closed = open_edges == 0 and nonmanifold == 0
    must_close = start_cap != "none" and end_cap != "none"
    if (must_close and not closed) or nonmanifold or conflicts:
        faulty = _edge_faults(tris, len(local)) if must_close else np.zeros(0, dtype=np.int64)
        between = None
        who: list[int] = []
        if len(faulty):
            # Strips are blamed before caps: a cap only repeats the outline it was given.
            blamed = sorted({s.groups[g]["between"] for g in owner[faulty]}, key=lambda b: (b[0] == b[1], b))
            between = blamed[0]
            who = [g for g in np.unique(owner[faulty]).tolist() if s.groups[g]["between"] == between]
        place = _where(between) if between else "В результате"
        contours = _contours_of(s, who)
        raise StitchError(
            f"{place} тело не замыкается (открытых рёбер: {open_edges}, рёбер с тремя и более треугольниками: "
            f"{nonmanifold}, конфликтов ориентации: {conflicts}): поправьте контуры или поставьте тай-линии"
            f"{_named(contours)}.",
            between, code="stitch_open", hint="Проверьте, что контуры замкнуты и не пересекают сами себя.",
            contours=contours,
        )  # fmt: skip
    volume = _signed_volume(local, tris) if closed else None
    if volume is not None and volume < 0:
        tris = tris[:, ::-1]
        volume = -volume
    tris = np.ascontiguousarray(tris, dtype=np.int32)
    vertices = np.empty((len(local), 3), dtype=np.float64)
    vertices[: len(s.given)] = s.given
    vertices[len(s.given) :] = local[len(s.given) :] + s.centre
    # The check of stage 10 on the result as it will be stored; what it finds is confirmed by the
    # slower exact search, because nearly coplanar triangles can give it a false alarm.
    crossings = 0
    if check:
        crossings = self_intersections(vertices, tris, limit=0, progress=checking).pairs
    if crossings:
        i, j = _crossing(local, tris)
        if i >= 0:
            (a1, b1), (a2, b2) = s.groups[owner[i]]["between"], s.groups[owner[j]]["between"]
            between = (min(a1, a2), max(b1, b2))
            culprits = [int(owner[i]), int(owner[j])]
            if owner[i] != owner[j]:
                # Two strips cross each other; a strip that crosses itself is the better culprit.
                for number, g in enumerate(s.groups):
                    if _crossing(local, g["tris"])[0] >= 0:
                        between = g["between"]
                        culprits = [number]
                        break
            contours = _contours_of(s, culprits)
            advice = (
                "выберите другой способ замыкания торца или поправьте контур"
                if between[0] == between[1]
                else "поставьте тай-линии или поправьте контуры"
            )
            raise StitchError(
                f"{_where(between)} получаются самопересечения: {advice}{_named(contours)}.",
                between, code="stitch_self_intersection",
                hint="Самопересечения не исправляются автоматически: тай-линии, промежуточный контур или другой торец.",
                contours=contours,
            )  # fmt: skip
    offset = float(s.centre @ normal)
    report = {
        "sections": [
            {"position": float(level[k] + offset), "contours": [int(c) for c in sec]} for k, sec in enumerate(sections)
        ],
        "pairs": [{**p, "a": [int(c) for c in p["a"]], "b": [int(c) for c in p["b"]]} for p in pairs],
        "ties_used": ties_used,
        "tolerance": tolerance,
        "section_tolerance": section_tolerance,
        "normal": [float(x) for x in normal],
        "warnings": s.warnings,
        "closed": bool(closed),
        "volume": volume,
    }
    tell(1.0, "Готово")
    return StitchResult(vertices, tris, report)
