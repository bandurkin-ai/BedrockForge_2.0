"""Coding by wireframes: collars exactly on the edges and the corners of a body, holes in any direction (the review
of the milestone В5, group 3; the find of 19.18 — «устье ровно на ребре тела при направлении наружу даёт «внутри» на
весь ствол»).

An end of a segment on the line of an edge puts the line of the segment through the edge exactly, but the edge
function of `geo.tri_kernels.segment_triangle` is made of products with the direction of the hole: for a direction
that is not along the axes it came out as rounding noise of either sign instead of 0, so the rule of the moved hole
(up, east, north) was not asked. One hole in a few hundred then «entered» the body at depth 0 while going away from it
(inside to its end), was outside while going in, or left the body it had not entered — and the whole coding was
refused with `wireframe_orientation` for a wireframe that is in order. The reference is the box itself: the points of
the path off its faces."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from bf_core.composites import coding as cg
from bf_core.drillholes.desurvey import Collars, Surveys, desurvey
from tests.meshutil import UTM, cube
from tests.test_composites_coding import metres

pytestmark = pytest.mark.windows

SIDE = 80.0
AZIMUTHS = [0.0, 45.0, 90.0, 200.0, 33.3, 271.7, 135.0, 315.0, 180.0, 225.0]
DIPS = [-90.0, -60.0, -45.0, -30.0, -12.3, 0.0, 27.1, 45.0, 90.0]


def on_the_box(origin: np.ndarray, cells: int, seed: int, n: int = 3000):  # type: ignore[no-untyped-def]
    """A box of `cells` × `cells` cells a face and `n` holes whose collars are points of its faces, edges and corners
    (a lattice of 10 m: on the lines and the diagonals of the cells too), random directions and lengths."""
    rng = np.random.default_rng(seed)
    low = np.array([80.0, 0.0, -60.0]) + origin
    v, t = cube(cells, SIDE, tuple(low), seed=None)
    step = np.arange(0.0, SIDE + 1.0, 10.0)
    points = np.array([p for p in itertools.product(step, repeat=3) if any(c in (0.0, SIDE) for c in p)]) + low
    c = points[rng.integers(0, len(points), n)]
    az, dip, td = rng.choice(AZIMUTHS, n), rng.choice(DIPS, n), rng.uniform(20.0, 160.0, n)
    traj = desurvey(Collars(np.arange(n), c[:, 0], c[:, 1], c[:, 2], td),
                    Surveys(np.arange(n), np.zeros(n), az, dip), method="minimum_curvature", n_holes=n)  # fmt: skip
    return cg.Body.of("К", v, t), traj, td, low


@pytest.mark.parametrize(("origin", "cells", "seed"), [
    ((0.0, 0.0, 0.0), 2, 1), ((0.0, 0.0, 0.0), 2, 2), ((0.0, 0.0, 0.0), 8, 3), ((UTM[0], UTM[1], 0.0), 2, 1),
])  # fmt: skip
def test_collars_on_the_edges_of_a_body_and_holes_in_any_direction(origin, cells, seed) -> None:  # type: ignore[no-untyped-def]
    body, traj, td, low = on_the_box(np.array(origin), cells, seed)
    n = len(td)
    lines = metres([(h, float(td[h])) for h in range(n)], step=200.0)  # one sample a hole: the stretches matter
    st = cg.body_stretches(lines, traj, np.arange(n), [body])  # no `wireframe_orientation` for a good box
    ids = st.lines.hole_ids()
    order = np.argsort(ids, kind="stable")
    wrong = []
    for h in range(n):
        depths = np.arange(0.25, td[h], 0.5)
        p = traj.positions(np.full(depths.shape[0], h), depths)
        inside = ((p > low + 1e-6) & (p < low + SIDE - 1e-6)).all(axis=1)  # off the faces: no tie
        outside = ((p < low - 1e-6) | (p > low + SIDE + 1e-6)).any(axis=1)
        a, z = np.searchsorted(ids[order], [h, h + 1])
        got = np.zeros(depths.shape[0], dtype=bool)
        for k in order[a:z]:
            got |= (depths > st.lines.frm[k]) & (depths < st.lines.to[k])
        if ((got & outside) | (~got & inside)).any():
            wrong.append(h)
    assert wrong == []


def test_the_hole_of_the_find_goes_away_from_the_edge_and_is_outside() -> None:
    """The hole of 19.18: the collar on the top north edge of a box, north-east and down — away from the box."""
    v, t = cube(2, SIDE, (80.0, 0.0, -60.0), seed=None)
    td = 145.29199944545277
    traj = desurvey(Collars(np.arange(1), np.array([110.0]), np.array([80.0]), np.array([20.0]), np.array([td])),
                    Surveys(np.arange(1), np.zeros(1), np.array([45.0]), np.array([-30.0])),
                    method="minimum_curvature", n_holes=1)  # fmt: skip
    st = cg.body_stretches(metres([(0, td)], step=200.0), traj, np.arange(1), [cg.Body.of("К", v, t)])
    assert st.lines.n == 0 and st.report["bodies"][0]["holes"] == 0
