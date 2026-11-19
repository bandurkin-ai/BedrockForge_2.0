"""Working state of one stitching (`geo/stitch.py`): vertices, contours as loops, branching by a chord, caps.

    Series(contours)                 the given vertices (untouched) and their copy relative to the centre
    Series.cut(contour, loop, ...)   the parent loop cut into one part per child (branching, ADR 0015 §7)
    Series.cap(kind, loop, ...)      the end of the series: none, flat, point, line, extend

Branching (one contour against several). The children are split into two groups by the straight line with
the widest clearance between them; the parent is cut along the corresponding line by one straight chord, and
each part is stitched to its child as a closed contour. The two tubes use the chord's edges in opposite
directions, so together they are a manifold "pants" surface bounded on the parent's section by the parent
contour. The chord's end points are inserted into the parent contour for all its uses (the strip on its
other side, its cap); the chord's inner points are moved a quarter of the way towards the children (a
saddle), which keeps them clear of the cap and of a second branching on the parent's other side. More than
two children: each part is cut again, the saddle of each next cut half as deep. Children that no line
separates are refused with the numbers of the sections.

Ends: flat cap (earcut in the plane of the sections), pinch-out to a point or to a ridge line, or
continuation by a share of the spacing with a flat cap.

Ported from BF `geo/stitch.py` (`_Series`) without changes to the geometry; errors with a code and a hint.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from bf_core.geo.polygon import StitchError, centroid, triangulate
from bf_core.geo.stitch_strip import Tick, strip

# Share of the gap between sections by which the inner points of a branching chord are lowered.
CHORD_SAG = 0.25
# Ridge of a "line" cap: share of the contour's extent along its main axis.
RIDGE_SHARE = 0.5
BRANCH_HINT = "Поставьте тай-линии или добавьте промежуточный контур между разрезами."


class Series:
    """Working state of one stitching: vertices, contours as loops of vertex numbers, triangles."""

    normal: np.ndarray
    u: np.ndarray
    v: np.ndarray

    def __init__(self, contours: list[np.ndarray]) -> None:
        self.counts = [len(c) for c in contours]
        self.starts = np.concatenate([[0], np.cumsum(self.counts)]).astype(np.int64)
        self.given = np.concatenate(contours, axis=0)  # the input vertices, untouched
        self.centre = (self.given.min(axis=0) + self.given.max(axis=0)) / 2.0
        self.local = self.given - self.centre  # everything is computed in these
        self.added: list[np.ndarray] = []  # new vertices, relative to the centre
        self._all: np.ndarray | None = self.local
        self.loops: list[list[int]] = []  # loop id → vertex numbers, counter-clockwise
        self.family: list[list[int]] = []  # contour → ids of its loop and of its parts
        self.groups: list[dict[str, Any]] = []  # {"between": (a, b), "tris": array}
        self.warnings: list[str] = []
        self.tick: Tick = None

    def add_vertex(self, point: np.ndarray) -> int:
        self.added.append(np.asarray(point, dtype=np.float64))
        self._all = None
        return len(self.local) + len(self.added) - 1

    def xyz(self, numbers: Any) -> np.ndarray:
        if self._all is None:
            self._all = np.vstack([self.local, np.asarray(self.added)])
        return self._all[np.asarray(numbers, dtype=np.int64)]

    def flat(self, numbers: Any) -> np.ndarray:
        """Projection of the vertices along the common normal."""
        p = self.xyz(numbers)
        return np.column_stack([p @ self.u, p @ self.v])

    def emit(self, between: tuple[int, int], tris: np.ndarray, contours: tuple[int, ...] = ()) -> None:
        """Triangles of one strip or cap: the sections and the contours they come from (for the messages)."""
        group = {"between": between, "tris": np.asarray(tris, dtype=np.int64).reshape(-1, 3), "contours": contours}
        self.groups.append(group)

    # --- branching

    def insert_on_edge(self, contour: int, g1: int, g2: int, w: int) -> None:
        """Puts vertex w between g1 and g2 in every loop of the contour that has this edge."""
        for loop_id in self.family[contour]:
            loop = self.loops[loop_id]
            if g1 not in loop:
                continue
            i = loop.index(g1)
            k = len(loop)
            if loop[(i + 1) % k] == g2:
                loop.insert(i + 1, w)
            elif loop[(i - 1) % k] == g2:
                loop.insert(i, w)

    def _refuse(self, between: tuple[int, int], what: str, contour: int, children: list[int]) -> StitchError:
        return StitchError(
            f"Разветвление между разрезами {between[0] + 1} и {between[1] + 1}: {what} "
            f"(контур {contour + 1}, ветви — контуры {', '.join(str(c + 1) for c in sorted(children))}).",
            between,
            code="stitch_branch",
            hint=BRANCH_HINT,
            contours=(contour, *sorted(children)),
        )

    def cut(
        self,
        contour: int,
        loop_id: int,
        children: list[int],
        toward: float,
        gap: float,
        between: tuple[int, int],
        depth: int = 0,
    ) -> list[tuple[int, int]]:
        """Cuts the loop into one part per child; returns [(loop id of the part, child contour)].

        The children are split into two groups by the straight line with the widest clearance
        between them; the parent is cut along the corresponding line. More than two children:
        each part is cut again (`depth` grows; the saddle of each next cut is half as deep).
        """
        if len(children) == 1:
            return [(loop_id, children[0])]
        shapes = [self.flat(self.loops[c]) for c in children]
        cents = np.array([centroid(shape) for shape in shapes])
        q = self.flat(self.loops[loop_id])
        own = centroid(q)

        # Candidate directions: between every two children and across them. The best one has the
        # widest empty band between the children's extents; the cut goes through its middle.
        best: tuple[float, np.ndarray, float] | None = None  # (width of the band, axis, level)
        farthest: tuple[float, np.ndarray | None] = (0.0, None)
        for i in range(len(children)):
            for j in range(i + 1, len(children)):
                d = cents[j] - cents[i]
                length = float(np.linalg.norm(d))
                if length > farthest[0]:
                    farthest = (length, d / max(length, 1e-300))
                if length == 0.0:
                    continue
                d = d / length
                for axis in (d, np.array([-d[1], d[0]])):
                    spans = sorted((float((shape @ axis).min()), float((shape @ axis).max())) for shape in shapes)
                    reach = spans[0][1]
                    for lo, hi in spans[1:]:
                        if lo - reach > 0 and (best is None or lo - reach > best[0]):
                            best = (lo - reach, axis, (lo + reach) / 2.0)
                        reach = max(reach, hi)
        if best is None:
            # The children overlap in every direction: split their centres at the widest gap.
            widest = farthest[1]
            if widest is None:
                raise self._refuse(between, "ветви совпадают по положению и не разделяются", contour, children)
            t = np.sort(cents @ widest)
            i = int(np.argmax(np.diff(t)))
            best = (0.0, widest, (t[i] + t[i + 1]) / 2.0)
        _, axis, level = best
        t = cents @ axis
        groups = (
            [c for c, x in zip(children, t, strict=True) if x < level],
            [c for c, x in zip(children, t, strict=True) if x >= level],
        )
        # The children's extent across the cut is mapped onto the parent's: a body that drifts or
        # tapers between the sections is cut in the corresponding place, not where the children
        # happen to project.
        c_lo = min(float((shape @ axis).min()) for shape in shapes)
        c_hi = max(float((shape @ axis).max()) for shape in shapes)
        p_lo, p_hi = float((q @ axis).min()), float((q @ axis).max())
        child_level = level
        level = p_lo + (level - c_lo) / (c_hi - c_lo) * (p_hi - p_lo)
        side = q @ axis - level
        if (side >= 0).all() or (side < 0).all():
            level = float(own @ axis)
            side = q @ axis - level
        along = np.array([-axis[1], axis[0]])
        loop = list(self.loops[loop_id])
        k = len(loop)
        size = float(np.ptp(q, axis=0).max())
        crossings = []  # (position along the cut, edge index, share of the edge)
        for i in range(k):
            j = (i + 1) % k
            if (side[i] >= 0) != (side[j] >= 0):
                share = side[i] / (side[i] - side[j])
                crossings.append((float((q[i] + share * (q[j] - q[i])) @ along), i, share))
        crossings.sort()
        if len(crossings) < 2:
            raise self._refuse(between, "не удалось разделить контур на части по числу ветвей", contour, children)
        # A line may cross a non-convex contour several times: take the inner piece nearest to the centre.
        ref = float(own @ along)
        pieces = [(crossings[i], crossings[i + 1]) for i in range(0, len(crossings) - 1, 2)]
        start, end = min(pieces, key=lambda p: (max(p[0][0] - ref, ref - p[1][0], 0.0), p[0][0] - p[1][0]))

        ends = []
        for _, i, share in (start, end):
            g1, g2 = loop[i], loop[(i + 1) % k]
            p1, p2 = self.xyz([g1, g2])
            edge = float(np.linalg.norm(q[(i + 1) % k] - q[i]))
            if share * edge <= 1e-7 * size:
                ends.append(g1)
            elif (1.0 - share) * edge <= 1e-7 * size:
                ends.append(g2)
            else:
                w = self.add_vertex(p1 + share * (p2 - p1))
                self.insert_on_edge(contour, g1, g2, w)
                ends.append(w)
        x, y = ends
        if x == y:
            raise self._refuse(between, "линия раздела вырождена, контур не удаётся разделить", contour, children)

        # Inner points of the chord, about one per typical edge. They are moved a share of the way
        # towards the children (a saddle): down along the normal and sideways to where the chord
        # would arrive between the children — the middle of the band between the two groups, as
        # long as the groups are. Each tube leans from its part to its child; a saddle left right
        # under the chord would be crossed by a tube leaning over it.
        loop = self.loops[loop_id]
        k = len(loop)
        px, py = self.xyz([x, y])
        ring = self.xyz(loop)
        typical = float(np.linalg.norm(np.roll(ring, -1, axis=0) - ring, axis=1).mean())
        inner_count = int(np.clip(round(float(np.linalg.norm(py - px)) / max(typical, 1e-12)) - 1, 1, 200))
        share = CHORD_SAG / 2.0**depth
        reach2 = [
            [f(float(f(shape @ along)) for c, shape in zip(children, shapes, strict=True) if c in group)
             for f in (min, max)]
            for group in groups
        ]  # fmt: skip
        target_lo = (reach2[0][0] + reach2[1][0]) / 2.0
        target_hi = (reach2[0][1] + reach2[1][1]) / 2.0
        qx, qy = self.flat([x, y])
        inner = []
        for i in range(1, inner_count + 1):
            f = i / (inner_count + 1.0)
            image = child_level * axis + (target_lo + f * (target_hi - target_lo)) * along
            move = share * (image - (qx + f * (qy - qx)))
            inner.append(
                self.add_vertex(
                    px + f * (py - px) + move[0] * self.u + move[1] * self.v + toward * share * gap * self.normal
                )
            )

        ix, iy = loop.index(x), loop.index(y)
        arc_xy = [loop[(ix + i) % k] for i in range((iy - ix) % k + 1)]
        arc_yx = [loop[(iy + i) % k] for i in range((ix - iy) % k + 1)]
        part_xy = arc_xy + inner[::-1]  # the chord is walked y → x here and x → y in the other part
        part_yx = arc_yx + inner
        between_xy = arc_xy[1:-1]
        if between_xy:
            positive = float((self.flat(between_xy) @ axis - level).mean()) >= 0.0
        else:
            positive = float((self.flat(arc_yx[1:-1]) @ axis - level).mean()) < 0.0
        parts = (part_yx, part_xy) if positive else (part_xy, part_yx)  # groups[0] is on the negative side

        out = []
        new_ids = []
        for part in parts:
            self.loops.append(part)
            self.family[contour].append(len(self.loops) - 1)
            new_ids.append(len(self.loops) - 1)
        for part_id, group in zip(new_ids, groups, strict=True):
            out += self.cut(contour, part_id, group, toward, gap, between, depth + 1)
        return out

    # --- caps

    def plane_point(self, loop: list[int], q: np.ndarray) -> np.ndarray:
        """The 2D point q of the projection put on the mean level of the loop."""
        level = float((self.xyz(loop) @ self.normal).mean())
        return np.asarray(q[0] * self.u + q[1] * self.v + level * self.normal)

    def cap(
        self, kind: str, loop: list[int], outward: float, spacing: float, distance: float, section: int, contour: int
    ) -> None:
        if kind == "none":
            return
        between = (section, section)
        who = (contour,)
        ids = np.asarray(loop, dtype=np.int64)
        q = self.flat(loop)
        reach = outward * distance * spacing * self.normal
        if kind == "flat":
            tris = ids[triangulate(q)]
            self.emit(between, tris if outward > 0 else tris[:, ::-1], who)
        elif kind == "point":
            apex = self.add_vertex(self.plane_point(loop, centroid(q)) + reach)
            nxt = np.roll(ids, -1)
            top = np.full(len(ids), apex, dtype=np.int64)
            self.emit(between, np.column_stack([ids, nxt, top] if outward > 0 else [nxt, ids, top]), who)
        elif kind == "extend":
            ring = self.xyz(loop) + reach
            copy = np.array([self.add_vertex(p) for p in ring], dtype=np.int64)
            k = len(ids)
            i = np.arange(k)
            j = (i + 1) % k
            lower, upper = (ids, copy) if outward > 0 else (copy, ids)
            walls = [np.column_stack([lower[i], lower[j], upper[i]]), np.column_stack([lower[j], upper[j], upper[i]])]
            self.emit(between, np.vstack(walls), who)
            tris = copy[triangulate(q)]
            self.emit(between, tris if outward > 0 else tris[:, ::-1], who)
        elif kind == "line":
            # Ridge along the main axis of the contour, through its centre, shortened.
            c = centroid(q)
            axis = np.linalg.svd(q - q.mean(axis=0), full_matrices=False)[2][0]
            t = (q - c) @ axis
            pieces = int(np.clip(len(ids) // 4, 1, 50))
            stops = np.linspace(t.min() * RIDGE_SHARE, t.max() * RIDGE_SHARE, pieces + 1)
            ridge = [self.add_vertex(self.plane_point(loop, c + s * axis) + reach) for s in stops]
            # The ridge as a contour of no width: there along one side and back along the other.
            both = np.array(ridge + ridge[-2:0:-1], dtype=np.int64)
            gap = distance * spacing
            if outward > 0:
                got = strip(q, self.flat(both), ids, both, gap, tick=self.tick)
            else:
                got = strip(self.flat(both), q, both, ids, gap, tick=self.tick)
            assert got is not None  # no ties: always a strip
            # An end of the ridge reached from a single contour vertex gives the same triangle on
            # both sides of the ridge: such a fold has no volume and is dropped (the ridge gets shorter).
            _, inverse, counts = np.unique(np.sort(got, axis=1), axis=0, return_inverse=True, return_counts=True)
            self.emit(between, got[counts[inverse.reshape(-1)] == 1], who)
