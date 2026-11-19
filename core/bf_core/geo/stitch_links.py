"""Sections and links of a stitching (`geo/stitch.py`; ТЗ 7.23, 8.1.4; ADR 0016 §8).

    normal_and_sections(s, clean, ...)   common normal, sections, their levels, the section of every contour
    pair_links(links, sections, ...)     [(k, [(lower contours, upper contours), ...])] for sections k, k + 1
    cap_plan(start_cap, end_cap, ...)    a cap at every end of every body: (kind, outward, contour, k, spacing)
    bodies(count, links)                 connected groups of contours: one solid each
    body_of_triangles(...)               the bodies and the body of every triangle
    body_report(...)                     contours, sections, triangles and volume of every body

By default (no `normal`, `groups`, `links`, `spacing`) everything is as in Э9, bit for bit: the normal is the
mean of the contours' plane normals, sections by position along it, links by centres (BF `_sections`,
`_links`), caps on the first and the last section.

Given sections (`groups`: the number of the section of every contour, from 0, as a series numbers them):
sections k and k + 1 are neighbours; a number with no contours is an empty section, nothing is stitched
across it (ADR 0016, question 6). The normal (given or the mean) is turned so that the sections follow it
in the order of their numbers; sections out of this order are refused.

Given links (pairs of contour numbers): only contours of neighbouring sections. On each pair of sections the
links fall into connected groups, each 1 : 1, 1 : N (a branching) or N : 1; N : M is refused with the
numbers of the sections. Without links the contours of neighbouring sections are linked by centres (Э9).

Ends of bodies: a contour with no link to the previous section gets the start cap, with none to the next
one the end cap. The length of a "point", "line" or "extend" cap is a share of `spacing` (by default the
distance to the neighbouring section of the body; for a contour linked to nothing — the median step
between the sections).

Bodies are separate solids: with given sections or links only a body that crosses itself fails the check;
bodies that touch or cross each other (unlinked contours of neighbouring sections whose continued ends meet)
get a warning.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from bf_core.geo.polygon import StitchError, area_vector, plane_axes
from bf_core.geo.stitch_branch import Series

SECTIONS_HINT = "Выберите замкнутые контуры, нарисованные на параллельных разрезах (хотя бы на двух)."
LINKS_HINT = (
    "Разделите связи: между соседними разрезами каждая группа — один к одному или разветвление "
    "одного контура на несколько."
)
LONE_HINT = "Свяжите контур с контуром соседнего разреза или выберите продление торца или выклинивание."
# Section numbers above this are refused (a series has up to a few thousand sections).
MAX_SECTION = 100_000

Pairs = list[tuple[int, list[tuple[list[int], list[int]]]]]


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


def _bad(message: str, hint: str) -> StitchError:
    return StitchError(message, code="bad_parameter", hint=hint)


def _unit(value: Any, what: str) -> np.ndarray:
    v = np.asarray(value, dtype=np.float64).reshape(-1)
    length = float(np.linalg.norm(v)) if v.shape == (3,) and np.isfinite(v).all() else 0.0
    if length == 0.0:
        raise _bad(f"{what}: ожидается ненулевой вектор из трёх чисел.", "Задайте направление нормали разрезов.")
    return v / length


def _numbers(groups: Any, count: int) -> np.ndarray:
    raw = np.asarray(groups)
    if raw.shape != (count,) or raw.dtype.kind not in "iuf" or not np.isfinite(raw).all():
        raise _bad(
            f"Номера разрезов контуров: ожидается по одному целому числу на контур ({count}).",
            "Передайте номер разреза серии для каждого контура (с нуля).",
        )
    number = raw.astype(np.int64)
    if (number != raw).any() or (number < 0).any() or (number > MAX_SECTION).any():
        raise _bad(
            f"Номера разрезов контуров должны быть целыми от 0 до {MAX_SECTION:,}.".replace(",", " "),
            "Передайте номер разреза серии для каждого контура (с нуля).",
        )
    return number


def normal_and_sections(
    s: Series, clean: list[np.ndarray], section_tolerance: float | None, normal: Any = None, groups: Any = None
) -> tuple[np.ndarray, list[list[int]], float | None, np.ndarray, np.ndarray]:
    """Common normal (put on the series), sections, the tolerance used, their levels, the section of every
    contour. With `groups` the sections are those numbers (empty ones have the level NaN)."""
    count = len(clean)
    normals = np.empty((count, 3))
    for i in range(count):
        area = area_vector(s.xyz(np.arange(s.starts[i], s.starts[i + 1])))
        length = float(np.linalg.norm(area))
        if length <= 1e-12 * max(float(np.ptp(clean[i], axis=0).max()) ** 2, 1e-300):
            raise StitchError(f"Контур {i + 1} вырожден: у него нулевая площадь.", code="degenerate_polygon")
        normals[i] = area / length
    if normal is None:
        reference = normals[0]
        normals[normals @ reference < 0] *= -1.0
        normal = normals.mean(axis=0)
        if float(np.linalg.norm(normal)) < 1e-9:
            raise StitchError(
                "Плоскости контуров слишком различаются: общее направление разрезов не определяется.",
                code="stitch_sections", hint=SECTIONS_HINT,
            )  # fmt: skip
        normal /= np.linalg.norm(normal)
    else:
        normal = _unit(normal, "Нормаль разрезов")
    cent3 = np.array([s.xyz(np.arange(s.starts[i], s.starts[i + 1])).mean(axis=0) for i in range(count)])
    pos = cent3 @ normal
    number = None if groups is None else _numbers(groups, count)
    if number is None:
        if pos[0] > pos[-1] or (pos[0] == pos[-1] and normal[int(np.argmax(np.abs(normal)))] < 0):
            normal, pos = -normal, -pos
    else:
        first, last = int(number.min()), int(number.max())
        if float(pos[number == first].mean()) > float(pos[number == last].mean()):
            normal, pos = -normal, -pos
    for i in range(count):
        if abs(float(normals[i] @ normal)) < 0.5:
            raise StitchError(
                f"Контур {i + 1} лежит не в плоскости разрезов: он развёрнут к остальным больше чем на 60°.",
                code="stitch_sections", hint=SECTIONS_HINT,
            )  # fmt: skip
    s.normal = normal
    s.u, s.v = plane_axes(normal)
    tolerance: float | None = None
    if number is None:
        sections, tolerance = _sections(pos, section_tolerance)
        if len(sections) < 2:
            raise StitchError(
                "Все контуры лежат на одном разрезе: для сшивки нужны контуры хотя бы на двух разрезах.",
                code="stitch_sections", hint=SECTIONS_HINT,
            )  # fmt: skip
    else:
        sections = [[] for _ in range(int(number.max()) + 1)]
        for i in range(count):
            sections[int(number[i])].append(i)
    level = np.array([float(pos[sec].mean()) if sec else np.nan for sec in sections])
    present = [k for k, sec in enumerate(sections) if sec]
    for a, b in zip(present, present[1:], strict=False):
        if not level[b] > level[a]:
            raise StitchError(
                f"Разрезы {a + 1} и {b + 1} идут не по порядку вдоль нормали разрезов: контуры разреза {b + 1} "
                f"лежат не дальше контуров разреза {a + 1}.",
                (a, b), code="stitch_sections", hint="Проверьте номера разрезов контуров и направление нормали.",
            )  # fmt: skip
    section_of = np.empty(count, dtype=np.int64)
    for k, sec in enumerate(sections):
        section_of[sec] = k
    return normal, sections, tolerance, level, section_of


def _pair(link: Any, number: int, count: int) -> tuple[int, int]:
    raw = np.asarray(link)
    if raw.shape != (2,) or raw.dtype.kind not in "iu":
        raise _bad(f"Связь {number}: ожидается пара номеров контуров.", LINKS_HINT)
    a, b = int(raw[0]), int(raw[1])
    if not (0 <= a < count and 0 <= b < count):
        raise _bad(f"Связь {number}: нет таких контуров ({a + 1} и {b + 1}; всего {count}).", LINKS_HINT)
    if a == b:
        raise _bad(f"Связь {number}: контур {a + 1} связан сам с собой.", LINKS_HINT)
    return a, b


def _find(parent: dict[int, int], x: int) -> int:
    while parent.setdefault(x, x) != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _components(edges: Any) -> list[list[int]]:
    """Connected groups of the graph given by its edges (pairs), each sorted, ordered by the first."""
    parent: dict[int, int] = {}
    for a, b in edges:
        ra, rb = _find(parent, int(a)), _find(parent, int(b))
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups: dict[int, list[int]] = {}
    for x in sorted(parent):
        groups.setdefault(_find(parent, x), []).append(x)
    return sorted(groups.values())


def pair_links(links: Any, sections: list[list[int]], section_of: np.ndarray, cent: np.ndarray) -> Pairs:
    """Links of every pair of neighbouring sections with contours: [(k, [(lower, upper), ...])].

    Default: by centres (`_links`). Given links: their connected groups on each pair of sections, in the
    order `_links` would give them (by the contour of the side with fewer contours), so links equal to the
    default ones give the same solid bit for bit.
    """
    if links is None:
        return [(k, _links(cent, sections[k], sections[k + 1])) for k in range(len(sections) - 1)
                if sections[k] and sections[k + 1]]  # fmt: skip
    count = len(section_of)
    edges: dict[int, set[tuple[int, int]]] = {}
    for number, link in enumerate(links, start=1):
        a, b = _pair(link, number, count)
        if section_of[a] > section_of[b]:
            a, b = b, a
        ka, kb = int(section_of[a]), int(section_of[b])
        if kb - ka != 1:
            where = f"оба на разрезе {ka + 1}" if ka == kb else f"на разрезах {ka + 1} и {kb + 1}"
            raise StitchError(
                f"Связь {number}: контуры {a + 1} и {b + 1} лежат {where}, а связываются только контуры "
                "соседних разрезов.",
                (ka, kb), code="stitch_links", hint=LINKS_HINT, contours=(a, b),
            )  # fmt: skip
        edges.setdefault(ka, set()).add((a, b))
    out: Pairs = []
    for k in sorted(edges):
        lower_first = len(sections[k]) <= len(sections[k + 1])
        entries = []
        for group in _components(sorted(edges[k])):
            lower = [c for c in group if section_of[c] == k]
            upper = [c for c in group if section_of[c] != k]
            if len(lower) > 1 and len(upper) > 1:
                raise StitchError(
                    f"Между разрезами {k + 1} и {k + 2} связи образуют группу {len(lower)} : {len(upper)} "
                    f"(контуры {', '.join(str(c + 1) for c in lower)} и {', '.join(str(c + 1) for c in upper)}): "
                    "разделите связи.",
                    (k, k + 1), code="stitch_links", hint=LINKS_HINT, contours=tuple(group),
                )  # fmt: skip
            entries.append((lower, upper))
        entries.sort(key=lambda e: min(e[0] if lower_first else e[1]))
        out.append((k, entries))
    return out


def _step(sections: list[list[int]], level: np.ndarray) -> float | None:
    """Median distance between neighbouring sections per one step of their numbers."""
    present = [k for k, sec in enumerate(sections) if sec]
    steps = [(level[b] - level[a]) / (b - a) for a, b in zip(present, present[1:], strict=False)]
    return float(np.median(steps)) if steps else None


def cap_plan(
    start_cap: str,
    end_cap: str,
    pairs: Pairs,
    sections: list[list[int]],
    section_of: np.ndarray,
    level: np.ndarray,
    spacing: float | None,
) -> list[tuple[str, float, int, int, float]]:
    """(kind, outward, contour, section, spacing) of every cap: start caps first, by section and contour."""
    count = len(section_of)
    down = np.zeros(count, dtype=bool)  # linked to the previous section
    up = np.zeros(count, dtype=bool)  # linked to the next one
    for _, entries in pairs:
        for lower, upper in entries:
            up[lower] = True
            down[upper] = True
    order = sorted(range(count), key=lambda c: (int(section_of[c]), c))
    thin = ("flat", "none")
    step: float | None = spacing
    for c in order:
        if down[c] or up[c]:
            continue
        k = int(section_of[c])
        if start_cap in thin and end_cap in thin:
            raise StitchError(
                f"Контур {c + 1} на разрезе {k + 1} не связан с контурами соседних разрезов: с такими торцами "
                "тело из одного разреза не имеет объёма.",
                (k, k), code="stitch_links", hint=LONE_HINT, contours=(c,),
            )  # fmt: skip
        if step is None:
            step = _step(sections, level)
            if step is None:
                raise StitchError(
                    "Все контуры лежат на одном разрезе: для торцов нужно расстояние между разрезами серии.",
                    (k, k), code="stitch_sections", hint="Задайте расстояние между разрезами.",
                )  # fmt: skip
    plan = []
    for kind, outward, linked, inner in ((start_cap, -1.0, down, up), (end_cap, 1.0, up, down)):
        for c in order:
            if linked[c]:
                continue
            k = int(section_of[c])
            if spacing is not None:
                gap = spacing
            elif inner[c]:
                gap = level[k + 1] - level[k] if outward < 0 else level[k] - level[k - 1]
            else:
                assert step is not None
                gap = step
            plan.append((kind, outward, c, k, float(gap)))
    return plan


def bodies(count: int, links: Sequence[Sequence[int]]) -> list[list[int]]:
    """Connected groups of contours 0 … count − 1 by the links (pairs of contour numbers): one solid each.

    Every contour is in one group (a contour without links is a group of its own); groups are sorted and
    ordered by their first contour.
    """
    edges = [(int(a), int(b)) for a, b in links] + [(c, c) for c in range(count)]
    return _components(edges)


def body_of_triangles(s: Series, owner: np.ndarray, pairs: Pairs, count: int) -> tuple[list[list[int]], np.ndarray]:
    """The bodies (`bodies` of the links used) and the body of every triangle (`owner`: its group in `s`)."""
    found = bodies(count, [(lower[0], c) for _, entries in pairs for lower, upper in entries for c in lower + upper])
    body_of = np.empty(count, dtype=np.int64)
    for b, group in enumerate(found):
        body_of[group] = b
    of_group = np.array([body_of[g["contours"][0]] for g in s.groups], dtype=np.int64)
    return found, of_group[owner]


def body_report(
    found: list[list[int]],
    tri_body: np.ndarray,
    local: np.ndarray,
    tris: np.ndarray,
    section_of: np.ndarray,
    closed: bool,
) -> list[dict[str, Any]]:
    """Per body: contours, first and last section, triangles, volume (None unless the solid is closed)."""
    a, b, c = local[tris[:, 0]], local[tris[:, 1]], local[tris[:, 2]]
    per = np.einsum("ij,ij->i", a, np.cross(b, c))
    volumes = np.bincount(tri_body, weights=per, minlength=len(found)) / 6.0
    triangles = np.bincount(tri_body, minlength=len(found))
    return [
        {
            "contours": group,
            "sections": [int(section_of[group].min()), int(section_of[group].max())],
            "triangles": int(triangles[b]),
            "volume": float(volumes[b]) if closed else None,
        }
        for b, group in enumerate(found)
    ]
