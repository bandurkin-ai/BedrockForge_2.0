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
* A series of sections (ТЗ 8.1.4, ADR 0016 §8; `geo/stitch_links.py`): the normal, the section of every
  contour and the links may be given; then every connected group of contours is a body with caps at its ends.
  Without them the result is the one above, bit for bit.
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
Ported from BF `geo/stitch.py` (`stitch_contours`, `_edge_faults`, `_pierced`, `_first_crossing`; `_sections`
and `_links` are in `geo/stitch_links.py`) with the same results. Differences: errors with a code and a hint,
a limit on the size of a pair of contours, progress inside the large steps, the volume in the report, given
sections and links of a series.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bf_core.geo.checks import orientation_conflicts, self_intersections
from bf_core.geo.meshes import edge_summary
from bf_core.geo.polygon import StitchError, centroid, clean_polygon, signed_area
from bf_core.geo.stitch_branch import Series
from bf_core.geo.stitch_links import (
    SECTIONS_HINT,
    body_of_triangles,
    body_report,
    cap_plan,
    normal_and_sections,
    pair_links,
)
from bf_core.geo.stitch_strip import MAX_SPANS, strips
from bf_core.jit import njit

CAPS = ("none", "flat", "point", "line", "extend")
# Tie ends snap to contour vertices within this share of the typical contour size.
TIE_SNAP = 1e-3
TIE_HINT = "Поставьте концы тай-линии на вершины контуров соседних разрезов, которые сшиваются друг с другом."

StitchProgress = Callable[[float, str], None] | None


@dataclass
class StitchResult:
    vertices: np.ndarray  # (n, 3) float64; the input contour vertices first, in input order, bit-identical
    triangles: np.ndarray  # (m, 3) int32, consistently oriented, normals outward when closed
    report: dict[str, Any] = field(default_factory=dict)


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


def _within_bodies(
    s: Series, local: np.ndarray, t: np.ndarray, owner: np.ndarray, tri_body: np.ndarray, first: tuple[int, int]
) -> tuple[int, int]:
    """A crossing inside one body, or (−1, −1): bodies are separate solids, two of them that touch or cross
    each other (`first`) get a warning."""
    for b in np.unique(tri_body).tolist():
        sub = np.flatnonzero(tri_body == b)
        i, j = _crossing(local, t[sub])
        if i >= 0:
            return int(sub[i]), int(sub[j])
    i, j = first
    (a1, b1), (a2, b2) = s.groups[owner[i]]["between"], s.groups[owner[j]]["between"]
    s.warnings.append(
        f"Тела {tri_body[i] + 1} и {tri_body[j] + 1} касаются или пересекаются друг с другом (разрезы "
        f"{min(a1, a2) + 1}–{max(b1, b2) + 1}): каждое тело замкнуто, объёмы — по телам."
    )
    return -1, -1


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
    normal: Any = None,
    groups: Sequence[int] | None = None,
    links: Sequence[Sequence[int]] | None = None,
    spacing: float | None = None,
) -> StitchResult:
    """A triangulated solid from closed contours drawn on a series of sections.

    contours            (k, 3) arrays, closed without a repeated last vertex (a repeated one is
                        dropped), any winding, any orientation of the sections.
    ties                (2, 3) arrays: a tie line between vertices of two contours that are
                        stitched to each other; it becomes an edge of the result.
    start_cap, end_cap  "none" | "flat" | "point" | "line" | "extend" at the ends of every body: the
                        start cap on a contour with no link to the previous section, the end cap on one
                        with none to the next (by default: the first and the last section). The first
                        section is the end of the series nearer to the first contour of the list
                        (with `groups`: the lowest number).
    cap_distance        length of "point", "line" and "extend" caps as a share of `spacing` or of the
                        distance to the neighbouring section of the body.
    normal              direction across the sections (v̂ of a series); default: the mean of the
                        contours' plane normals. Turned if needed so that the sections follow it.
    groups              the section of every contour: integers from 0 as the series numbers its sections
                        (k and k + 1 are neighbours; a number without contours is an empty section, nothing
                        is stitched across it). Then report["sections"] and StitchError.between use these
                        numbers (an empty section: position None, no contours).
    links               (i, j) pairs of contour numbers (from 0) on neighbouring sections that are stitched;
                        on each pair of sections their groups are 1 : 1, 1 : N or N : 1 (N : M is refused,
                        code stitch_links). Default: by centres. A contour with no links is a body of its own.
    spacing             distance between the sections for the length of caps (Δ of a series).
    section_tolerance   contours closer than this along the common normal are on one section
                        (default: a tenth of the median spacing, at least 1e-6).
    tie_tolerance       how far a tie end may be from a contour vertex (default: 1e-3 of the
                        typical contour size).
    check               look for self-intersections; StitchError names the pair of sections.
    progress            callback (fraction, message); may raise to cancel. Called from this thread only.
    threads             strips built at once (None: up to 4, by the cores); the result is the same for any.

    The vertices of the contours come first in the result, in input order and bit-identical;
    new vertices (caps, branch cuts) follow. Report: sections, pairs, ties_used, tolerances, normal,
    warnings, closed and the volume of a closed solid (None otherwise); with `groups` or `links` also
    bodies: [{contours, sections: [first, last], triangles, volume}]. Without `normal`, `groups`, `links`
    and `spacing` the result is that of Э9 bit for bit.
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
    if spacing is not None and not (spacing > 0 and math.isfinite(spacing)):
        raise StitchError(
            "Расстояние между разрезами должно быть больше нуля.",
            code="bad_parameter", hint="Задайте шаг серии разрезов в метрах.",
        )  # fmt: skip
    tell(0.0, "Подготовка контуров")
    clean = [clean_polygon(c, f"Контур {i + 1}") for i, c in enumerate(contours)]
    if len(clean) < (1 if groups is not None else 2):
        raise StitchError(
            "Для сшивки нужны контуры хотя бы на двух разрезах.", code="stitch_sections", hint=SECTIONS_HINT
        )
    s = Series(clean)
    s.tick = tick
    count = len(clean)
    normal, sections, section_tolerance, level, section_of = normal_and_sections(
        s, clean, section_tolerance, normal, groups
    )

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
    linked = pair_links(links, sections, section_of, cent2)
    for k, entries in linked:
        gap = float(level[k + 1] - level[k])
        for lower, upper in entries:
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

    # ---- caps at the ends of every body
    tell(0.82, "Замыкание торцов")
    for kind, outward, contour, section, gap in cap_plan(
        start_cap, end_cap, linked, sections, section_of, level, spacing
    ):
        s.cap(kind, s.loops[contour], outward, gap, cap_distance, section, contour)

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
    grouped = groups is not None or links is not None
    solids, tri_body = body_of_triangles(s, owner, linked, count) if grouped else ([], None)
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
        if i >= 0 and tri_body is not None and tri_body[i] != tri_body[j]:
            i, j = _within_bodies(s, local, tris, owner, tri_body, (i, j))
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
            {"position": float(level[k] + offset) if sec else None, "contours": [int(c) for c in sec]}
            for k, sec in enumerate(sections)
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
    if tri_body is not None:
        report["bodies"] = body_report(solids, tri_body, local, tris, section_of, closed)
    tell(1.0, "Готово")
    return StitchResult(vertices, tris, report)
