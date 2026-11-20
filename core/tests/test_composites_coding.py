"""Coding of samples by wireframes (ТЗ 8.2.2, 8.2.4; ADR 0018 §6, решение 6): Т8.2-7 — a vertical hole through a
cube Z 0…100, the depths of entry and exit to 1e−6 m; curved holes of every desurvey method against a bisection
on `positions()`; a hole wholly inside a body (the collar check), a body with a cavity; overlapping bodies — the
order of the list; an open surface and a body turned inside out — {code, message, hint}; the division of the
samples at the entries and exits, and without it — the code by the middle and INSIDE_FRAC; holes without a
trajectory (no code, never a zero); 1, 2, 4, 8 threads and any order of the holes — bit for bit."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.composites import coding as cg
from bf_core.composites import domains as dm
from bf_core.composites import intervals as iv
from bf_core.composites import length as cl
from bf_core.drillholes.desurvey import METHODS, Collars, Surveys, Trajectories, desurvey
from tests.meshutil import UTM, cube, grid, icosphere

DEPTH = 1e-6  # metres along the hole (Т8.2-7)
NAN = math.nan
O = np.array([UTM[0], UTM[1], 0.0])  # the low corner of the cube Z 0…100


def holes(collars, surveys=(), method="minimum_curvature") -> Trajectories:  # type: ignore[no-untyped-def]
    """collars [(x, y, z, td)] in metres from `O`, surveys [(hole, depth, azimuth, dip)]."""
    c = np.array(collars, dtype=float).reshape(-1, 4)
    s = np.array(surveys, dtype=float).reshape(-1, 4)
    return desurvey(
        Collars(np.arange(len(c)), c[:, 0] + O[0], c[:, 1] + O[1], c[:, 2] + O[2], c[:, 3]),
        Surveys(s[:, 0].astype(np.int64), s[:, 1], s[:, 2], s[:, 3]),
        method=method,
        n_holes=len(c),
    )


def box(low=(0.0, 0.0, 0.0), size=(100.0, 100.0, 100.0), flip=False):  # type: ignore[no-untyped-def]
    """A closed box of 12 triangles, normals outward (`flip` — inward), metres from `O`."""
    v, t = cube(1, 1.0, offset=[0.0, 0.0, 0.0], seed=None)
    return v * np.asarray(size, dtype=float) + np.asarray(low, dtype=float) + O, t[:, ::-1] if flip else t


def shells(*parts):  # type: ignore[no-untyped-def]
    """Several meshes as one wireframe."""
    v = np.concatenate([p[0] for p in parts])
    shift = np.cumsum([0] + [len(p[0]) for p in parts[:-1]])
    return v, np.concatenate([p[1] + s for p, s in zip(parts, shift, strict=True)]).astype(np.int32)


def body(name, mesh) -> cg.Body:  # type: ignore[no-untyped-def]
    return cg.Body.of(name, *mesh)


def metres(ids_lengths, step=1.0) -> iv.Lines:  # type: ignore[no-untyped-def]
    """Samples of `step` metres from the collar to the given depth of every hole [(id, depth)]."""
    hole, frm = [], []
    for h, depth in ids_lengths:
        f = np.arange(0.0, depth, step)
        hole += [h] * len(f)
        frm.append(f)
    f = np.concatenate(frm)
    return iv.build_lines(hole, f, f + step)


def spans(lines: iv.Lines, hole: int | None = None) -> list[tuple[float, float]]:
    rows = slice(None) if hole is None else lines.hole_ids() == hole
    return list(zip(lines.frm[rows].tolist(), lines.to[rows].tolist(), strict=True))


def close(got, want, tol: float = DEPTH) -> bool:  # type: ignore[no-untyped-def]
    """Two lists of (from, to) agree within `tol`."""
    g, w = np.array(got, dtype=float).reshape(-1, 2), np.array(want, dtype=float).reshape(-1, 2)
    return g.shape == w.shape and bool((np.abs(g - w) <= tol).all())


CUBE = cube(10, 100.0, offset=O)  # Z 0…100, 10 m cells


# --- Т8.2-7 -----------------------------------------------------------------------------------------------


def test_t8_2_7_vertical_hole_through_a_cube() -> None:
    traj = holes([(37.3, 61.7, 150.25, 300.0)])
    lines = metres([(7, 300.0)])
    st = cg.body_stretches(lines, traj, [0], [body("Руда", CUBE)])
    assert st.lines.holes.tolist() == [7] and st.code.tolist() == [0] and st.names == ["Руда"]
    assert st.lines.frm[0] == pytest.approx(50.25, abs=DEPTH) and st.lines.to[0] == pytest.approx(150.25, abs=DEPTH)
    assert st.lines.frm[0] == pytest.approx(50.25, abs=1e-9)  # (what the kernels really give on a straight hole)
    # with the division: the samples 50–51 and 150–151 are cut at the entry and at the exit, the values are copied
    c = cg.code_samples(lines, st)
    assert c.lines.n == 302 and c.inside_frac is None and c.report["cut"] == 2
    assert close(spans(c.lines)[49:53], [(49.0, 50.0), (50.0, 50.25), (50.25, 51.0), (51.0, 52.0)])
    assert c.code[49:53].tolist() == [-1, -1, 0, 0] and c.rows[49:53].tolist() == [49, 50, 50, 51]
    assert close(spans(c.lines)[150:153], [(149.0, 150.0), (150.0, 150.25), (150.25, 151.0)])
    assert c.code[150:153].tolist() == [0, 0, -1] and c.rows[150:153].tolist() == [149, 150, 150]
    assert c.text()[50:52].tolist() == [None, "Руда"]
    length = c.lines.lengths()
    assert length.sum() == pytest.approx(300.0, rel=1e-12) and length[c.code == 0].sum() == pytest.approx(100.0, abs=2e-6)
    grade = np.arange(300.0)  # a column of the table: every part takes the value of its sample
    assert c.lines.take(grade).tolist() == c.rows.astype(float).tolist()
    # without it: the code by the middle of the sample and the share of its length inside
    m = cg.code_samples(lines, st, split=False)
    assert m.lines is lines and m.rows.tolist() == list(range(300))
    assert m.code[[49, 50, 51, 149, 150, 151]].tolist() == [-1, 0, 0, 0, -1, -1]
    assert m.inside_frac[[50, 150]] == pytest.approx([0.75, 0.25], abs=DEPTH)
    assert m.inside_frac[[49, 51, 149, 151]].tolist() == [0.0, 1.0, 1.0, 0.0]  # exact
    assert m.text()[[49, 50]].tolist() == [None, "Руда"]
    assert m.report["coded"] == 100 and m.report["codes"] == [{"name": "Руда", "rows": 100, "length": 100.0}]


def test_holes_through_vertices_edges_and_faces_of_the_cube() -> None:
    """Where the numbers leave it open the hole is the one moved by an infinitesimal up, east, north
    (`geo/intersect.py`): the stretch is whole or absent, never torn, and the collar check agrees with it."""
    through = [(50.25, 150.25)]
    cases = [
        ((30.0, 60.0), through),  # a vertex of the lattice of the top and bottom faces
        ((35.0, 35.0), through),  # a diagonal of a cell
        ((30.0, 44.4), through),  # a line of the lattice
        ((0.0, 37.3), through),  # down the west wall: east of it — inside
        ((100.0, 37.3), []),  # down the east wall: east of it — outside
        ((0.0, 0.0), through),  # down the south-west vertical edge
        ((100.0, 100.0), []),  # down the north-east one
    ]
    traj = holes([(x, y, 150.25, 300.0) for (x, y), _ in cases])
    lines = metres([(h, 300.0) for h in range(len(cases))], step=3.0)
    st = cg.body_stretches(lines, traj, np.arange(len(cases)), [body("К", CUBE)])
    for h, (_xy, want) in enumerate(cases):
        assert close(spans(st.lines, h), want), (h, spans(st.lines, h))
    # collars on the faces: on the top one the hole is above it and enters at depth 0; on the bottom one it is
    # above it too — inside the cube — and leaves at once
    traj = holes([(37.3, 61.7, 100.0, 50.0), (37.3, 61.7, 0.0, 50.0), (37.3, 61.7, 100.0, 30.0)],
                 [(2, 0.0, 0.0, 90.0)])  # fmt: skip
    st = cg.body_stretches(metres([(0, 50.0), (1, 50.0), (2, 30.0)]), traj, [0, 1, 2], [body("К", CUBE)])
    assert spans(st.lines) == [(0.0, 50.0)] and st.lines.holes.tolist() == [0]


# --- curved holes against a bisection ----------------------------------------------------------------------

HARD = [  # surveys of holes that bend: (depth, azimuth, dip)
    [],
    [(0.0, 45.0, -60.0)],
    [(0.0, 10.0, -80.0), (50.0, 40.0, -60.0), (120.0, 90.0, -35.0), (200.0, 150.0, -10.0), (260.0, 200.0, 15.0)],
    [(0.0, 0.0, -89.0), (30.0, 180.0, -20.0), (60.0, 350.0, -70.0), (140.0, 300.0, -50.0)],
    [(0.0, 300.0, -45.0), (80.0, 120.0, 45.0)],  # the direction turns back: no arc fits
    [(0.0, 77.0, -55.0), (12.0, 78.0, -54.5), (24.0, 79.5, -54.0), (150.0, 95.0, -30.0), (151.0, 140.0, -80.0)],
]


def hard_holes(method: str) -> Trajectories:
    collars, surveys = [], []
    rng = np.random.default_rng(42)
    for h, stations in enumerate(HARD * 3):
        x, y = rng.uniform(25.0, 75.0, 2)
        collars.append((x, y, rng.uniform(70.0, 110.0), 270.0))
        surveys += [(h, d, (az + 37.0 * (h // len(HARD))) % 360.0, dip) for d, az, dip in stations]
    return holes(collars, surveys, method=method)


def reference(traj: Trajectories, h: int, inside, step: float = 0.01) -> list[tuple[float, float]]:  # type: ignore[no-untyped-def]
    """The stretches of hole `h` where `inside(points)` holds: a scan of the path and a bisection."""
    length = float(traj.lengths()[h])
    d = np.unique(np.append(np.arange(0.0, length, step), length))
    s = inside(traj.positions(np.full(len(d), h), d))
    marks = [0.0] if s[0] else []
    for i in np.flatnonzero(s[1:] != s[:-1]):
        a, b = d[i], d[i + 1]
        for _ in range(60):
            m = 0.5 * (a + b)
            if inside(traj.positions(np.array([h]), np.array([m])))[0] == s[i]:
                a = m
            else:
                b = m
        marks.append(0.5 * (a + b))
    if len(marks) % 2:
        marks.append(length)
    return list(zip(marks[::2], marks[1::2], strict=True))


@pytest.mark.parametrize("method", METHODS)
def test_curved_holes_against_bisection(method: str) -> None:
    traj = hard_holes(method)
    lines = metres([(h, 270.0) for h in range(traj.n_holes)], step=2.0)
    ball = icosphere(2, 45.0, centre=O + [60.0, 55.0, 20.0])
    a, b, c = (ball[0][ball[1][:, k]] for k in range(3))
    normal = np.cross(b - a, c - a)
    offset = np.einsum("ij,ij->i", normal, a)

    def in_cube(p: np.ndarray) -> np.ndarray:
        return ((p >= O) & (p <= O + 100.0)).all(axis=1)

    def in_ball(p: np.ndarray) -> np.ndarray:  # a convex polyhedron: under the plane of every face
        return (p @ normal.T < offset).all(axis=1)

    st = cg.body_stretches(lines, traj, np.arange(traj.n_holes), [body("Шар", ball), body("Куб", CUBE)])
    total = 0
    for h in range(traj.n_holes):
        want = [(x, y, 0) for x, y in reference(traj, h, in_ball)]
        want += [(x, y, 1) for x, y in reference(traj, h, lambda p: in_cube(p) & ~in_ball(p))]
        want.sort()
        mine = st.lines.hole_ids() == h
        assert st.code[mine].tolist() == [k for _, _, k in want], (method, h)
        assert close(spans(st.lines, h), [(x, y) for x, y, _ in want]), (method, h, spans(st.lines, h), want)
        total += len(want)
    assert total >= 2 * len(HARD)


# --- wholly inside, a cavity, priorities --------------------------------------------------------------------


def test_hole_wholly_inside_a_body_and_a_body_with_a_cavity() -> None:
    """A hole that never crosses the body is inside it when its collar is (ADR 0018 §6): the vertical segment
    from above the body down to the collar enters more shells than it leaves."""
    hollow = shells(box(), box((30.0, 30.0, 30.0), (40.0, 40.0, 40.0), flip=True))  # the cavity 30…70
    traj = holes(
        [
            (50.0, 50.0, 150.0, 300.0),  # through the body and its cavity
            (50.0, 50.0, 60.0, 20.0),  # wholly in the cavity: outside the body
            (10.0, 12.0, 90.0, 50.0),  # wholly in the body
            (50.0, 50.0, 50.0, 100.0),  # from the cavity through the body and out
            (50.0, 50.0, 90.0, 30.0),  # from the body into the cavity
            (150.0, 50.0, 90.0, 50.0),  # beside
            (50.0, 50.0, 250.0, 50.0),  # above
        ]
    )
    lines = metres([(h, float(traj.lengths()[h])) for h in range(7)])
    st = cg.body_stretches(lines, traj, np.arange(7), [body("Тело", hollow)])
    assert spans(st.lines) == [(50.0, 80.0), (120.0, 150.0), (0.0, 50.0), (20.0, 50.0), (0.0, 20.0)]
    assert st.lines.holes.tolist() == [0, 2, 3, 4] and st.report["bodies"][0]["inside"] == 1
    # the same body alone, without a cavity: the hole in the middle is inside now
    st = cg.body_stretches(lines, traj, np.arange(7), [body("Тело", box())])
    assert spans(st.lines, 1) == [(0.0, 20.0)] and spans(st.lines, 2) == [(0.0, 50.0)]
    assert spans(st.lines, 5) == [] and spans(st.lines, 6) == []


def test_overlapping_bodies_take_the_code_by_the_order_of_the_list() -> None:
    big, small, upper = box(), box((40.0, 40.0, 40.0), (20.0, 20.0, 20.0)), box((0.0, 0.0, 50.0))
    traj = holes([(50.0, 50.0, 200.0, 300.0)])
    lines = metres([(0, 300.0)], step=4.0)

    def coded(*order):  # type: ignore[no-untyped-def]
        st = cg.body_stretches(lines, traj, [0], [body(n, m) for n, m in order])
        return [(a, b, st.names[k]) for (a, b), k in zip(spans(st.lines), st.code.tolist(), strict=True)]

    b, s, u = ("Б", big), ("М", small), ("В", upper)
    # a body inside another: first in the list — it is cut out of the outer one; second — it is not seen
    assert coded(s, b) == [(100.0, 140.0, "Б"), (140.0, 160.0, "М"), (160.0, 200.0, "Б")]
    assert coded(b, s) == [(100.0, 200.0, "Б")]
    # two bodies that overlap in Z 50…100: the overlap goes to the first
    assert coded(b, u) == [(50.0, 100.0, "В"), (100.0, 200.0, "Б")]
    assert coded(u, b) == [(50.0, 150.0, "В"), (150.0, 200.0, "Б")]
    assert coded(u, s, b) == [(50.0, 150.0, "В"), (150.0, 160.0, "М"), (160.0, 200.0, "Б")]
    st = cg.body_stretches(lines, traj, [0], [body("Б", big), body("М", small)])
    assert any("«М»" in line for line in st.journal)  # a body that codes nothing is said in the journal


def test_random_stacks_of_boxes_against_brute_force() -> None:
    """Vertical holes through bodies made of stacked boxes: the stretches after the priorities, the parts of
    the samples and INSIDE_FRAC against plain Python over the known Z ranges."""
    rng = np.random.default_rng(7)
    tiny = 0
    for trial in range(12):
        top = 400.0
        ranges = []  # per body: [(z0, z1)] apart from each other, on a grid of 0.25 m
        for _k in range(int(rng.integers(1, 6))):
            cuts = np.sort(rng.choice(np.arange(1, 1200), size=2 * int(rng.integers(1, 5)), replace=False)) * 0.25
            ranges.append([(float(a), float(b)) for a, b in zip(cuts[::2], cuts[1::2], strict=True) if b - a >= 0.5])
        ranges = [r for r in ranges if r]
        bodies = [
            body(f"Т{k}", shells(*(box((-9.0 - k, -8.0, z0), (20.0 + 2 * k, 17.0, z1 - z0)) for z0, z1 in r)))
            for k, r in enumerate(ranges)
        ]
        traj = holes([(0.3, 0.7, top, 350.0), (2.3, -1.7, top - 250.0, 350.0)])  # the second starts among them
        edges = np.sort(rng.choice(np.arange(1, 3500), size=120, replace=False)) * 0.1
        near = [top - z + d for r in ranges for z, _ in r[:1] for d in (-4e-7, 3e-7)]  # within the tolerance
        f = np.unique(np.concatenate([edges, [x for x in near if 0.0 < x < 350.0]]))
        lines = iv.build_lines([0] * (len(f) - 1) + [1] * (len(f) - 1), np.tile(f[:-1], 2), np.tile(f[1:], 2))
        st = cg.body_stretches(lines, traj, [0, 1], bodies)
        split, whole = cg.code_samples(lines, st), cg.code_samples(lines, st, split=False)
        for h, collar in ((0, top), (1, top - 250.0)):
            length = 350.0
            marks = sorted({min(max(collar - z, 0.0), length) for r in ranges for pair in r for z in pair})
            want: list[list[float]] = []
            for a, b in zip(marks[:-1], marks[1:], strict=True):
                z = collar - 0.5 * (a + b)
                k = next((k for k, r in enumerate(ranges) if any(z0 < z < z1 for z0, z1 in r)), -1)
                if k >= 0 and want and want[-1][2] == k and want[-1][1] == a:
                    want[-1][1] = b
                elif k >= 0:
                    want.append([a, b, k])
            mine = st.lines.hole_ids() == h
            assert st.code[mine].tolist() == [k for _, _, k in want], (trial, h)
            assert close(spans(st.lines, h), [(a, b) for a, b, _ in want], 1e-9), (trial, h)
            got = [(float(a), float(b), int(k)) for (a, b), k in zip(spans(st.lines, h), st.code[mine], strict=True)]

            def code_at(x: float, got=got) -> int:  # type: ignore[no-untyped-def]
                return next((k for a, b, k in got if a <= x < b), -1)

            rows = np.flatnonzero(lines.hole_ids() == h)
            parts, fracs, codes = [], [], []
            for i in rows.tolist():
                lo, hi = float(lines.frm[i]), float(lines.to[i])
                pos = lo
                for x in sorted({x for a, b, _ in got for x in (a, b)}):
                    if x >= hi - DEPTH:
                        break
                    if x > pos + DEPTH:
                        parts.append((pos, x, i, code_at(0.5 * (pos + x))))
                        pos = x
                parts.append((pos, hi, i, code_at(0.5 * (pos + hi))))
                k = code_at(0.5 * (lo + hi))
                inside, e = 0.0, min(DEPTH, 0.25 * (hi - lo))
                for a, b, kb in got:
                    if a < hi - e and b > lo + e and (kb == k or k < 0):
                        inside += (hi if b >= hi - e else b) - (lo if a <= lo + e else a)
                codes.append(k)
                fracs.append(inside / (hi - lo))
            mine = split.lines.hole_ids() == h
            assert spans(split.lines, h) == [(a, b) for a, b, _, _ in parts]  # exact: the cuts are the stretches'
            assert split.rows[mine].tolist() == [i for _, _, i, _ in parts]
            assert split.code[mine].tolist() == [k for _, _, _, k in parts]
            assert whole.code[rows].tolist() == codes
            assert whole.inside_frac[rows] == pytest.approx(fracs, abs=1e-12)
        # the parts tile the samples: the first FROM and the last TO of a sample are its own, bit for bit
        first = np.flatnonzero(np.diff(split.rows, prepend=-1) != 0)
        last = np.append(first[1:] - 1, split.lines.n - 1)
        assert np.array_equal(split.lines.frm[first], lines.frm) and np.array_equal(split.lines.to[last], lines.to)
        assert np.array_equal(split.lines.frm[1:][split.rows[1:] == split.rows[:-1]],
                              split.lines.to[:-1][split.rows[1:] == split.rows[:-1]])  # fmt: skip
        short = split.lines.lengths() <= DEPTH  # no sliver: a cut nearer than 1e−6 m to an end is not made —
        assert (np.bincount(split.rows)[split.rows[short]] == 1).all()  # only whole samples that are that short
        tiny += int(short.sum())
    assert tiny > 0


# --- refusals ----------------------------------------------------------------------------------------------


def test_open_surface_is_refused_with_code_message_and_hint() -> None:
    surface = grid(8, 8, lambda x, y: 0.0 * x + 50.0, step=10.0, offset=O)
    with pytest.raises(cg.CompositeError) as err:
        body("Кровля", surface)
    e = err.value.as_dict()
    assert e["code"] == "wireframe_open" and "«Кровля»" in e["message"] and "не замкнут" in e["message"]
    assert "открытых рёбер — 32" in e["message"] and "Исправить каркас" in e["hint"]
    # the same by the measures of a stored wireframe, before anything is read; a wireframe never measured
    with pytest.raises(cg.CompositeError) as err:
        cg.check_stats("Кровля", {"closed": False, "open_edges": 32, "nonmanifold_edges": 0})
    assert err.value.code == "wireframe_open"
    with pytest.raises(cg.CompositeError) as err:
        cg.check_stats("Тело", None)
    assert err.value.code == "wireframe_open" and "не проверен" in err.value.message
    cg.check_stats("Тело", {"closed": True, "orientation": "inward"})
    # a ready tree that is not said to be a solid is refused when the stretches are asked for
    traj, lines = holes([(50.0, 50.0, 150.0, 200.0)]), metres([(0, 200.0)])
    from bf_core.geo.intersect import MeshTarget

    with pytest.raises(cg.CompositeError) as err:
        cg.body_stretches(lines, traj, [0], [cg.Body("Кровля", MeshTarget(*surface))])
    assert err.value.code == "wireframe_open"


def test_a_body_turned_inside_out_is_refused_not_coded_wrong() -> None:
    traj, lines = holes([(50.0, 50.0, 150.0, 300.0), (350.0, 50.0, 150.0, 300.0)]), metres([(0, 300.0), (1, 300.0)])
    v, t = cube(4, 100.0, offset=O, seed=None)
    t = t.copy()
    t[:5] = t[:5, ::-1]  # some triangles wound the other way: the normals of neighbours disagree
    with pytest.raises(cg.CompositeError) as err:
        cg.body_stretches(lines, traj, [0, 1], [body("Тело", (v, t))])
    assert err.value.code == "wireframe_orientation" and "«Тело»" in err.value.message and err.value.hint
    # two shells of one wireframe, the smaller one inside out: closed, «outward» by its volume, yet a hole
    # comes out of the small shell without having entered it
    two = shells(box(), box((300.0, 0.0, 0.0), (100.0, 100.0, 60.0), flip=True))
    with pytest.raises(cg.CompositeError) as err:
        cg.body_stretches(lines, traj, [0, 1], [body("Линзы", two)])
    assert err.value.code == "wireframe_orientation" and "«Линзы»" in err.value.message
    # inward normals as such are fine: the whole body is turned
    st = cg.body_stretches(lines, traj, [0, 1], [body("Тело", box(flip=True))])
    assert spans(st.lines) == [(50.0, 150.0)]


# --- holes without a trajectory, samples below the end of the path -------------------------------------------


def test_holes_without_a_trajectory_get_no_code_and_never_a_zero() -> None:
    traj = desurvey(Collars(np.arange(2), [O[0] + 50.0, NAN], [O[1] + 50.0, NAN], [150.0, NAN], [300.0, 100.0]),
                    n_holes=2)  # fmt: skip
    lines = metres([(3, 200.0), (5, 100.0), (9, 100.0)], step=10.0)  # hole 5 has no collar, 9 is not in the set
    st = cg.body_stretches(lines, traj, [0, 1, -1], [body("Руда", box())])
    assert st.known.tolist() == [True, False, False] and st.lines.holes.tolist() == [3]
    for split in (True, False):
        c = cg.code_samples(lines, st, split=split)
        rest = c.lines.hole_ids() != 3
        assert (c.code[rest] == -1).all() and spans(c.lines, 5) == spans(lines, 5)
        assert c.report["no_trajectory"] == {"holes": 2, "rows": 20}
        assert any("без траектории — 2" in line for line in c.journal)
    assert np.isnan(c.inside_frac[rest]).all() and not np.isnan(c.inside_frac[~rest]).any()
    assert c.inside_frac[~rest].tolist() == [0.0] * 5 + [1.0] * 10 + [0.0] * 5


def test_samples_below_the_end_of_the_path_take_the_code_of_its_end() -> None:
    """`positions` puts a depth below the end of the path at its end (as the coordinates of samples do): a hole
    that ends inside a body is inside it to the bottom of its last sample."""
    traj = holes([(50.0, 50.0, 150.0, 100.0), (50.0, 50.0, 250.0, 60.0)])  # ends at Z 50 — inside; at Z 190
    lines = metres([(0, 120.0), (1, 120.0)], step=10.0)
    st = cg.body_stretches(lines, traj, [0, 1], [body("Руда", box())])
    assert spans(st.lines) == [(50.0, 120.0)]
    c = cg.code_samples(lines, st, split=False)
    assert c.code.tolist() == [-1] * 5 + [0] * 7 + [-1] * 12
    assert c.report["below_end"] == 2 + 6 and any("ниже конца траектории — 8" in line for line in c.journal)


# --- threads, the order of the holes -------------------------------------------------------------------------


def field(n: int, seed: int = 3) -> tuple[Trajectories, iv.Lines, list[cg.Body]]:
    rng = np.random.default_rng(seed)
    x, y = rng.uniform(-150.0, 250.0, n), rng.uniform(-150.0, 250.0, n)
    z = rng.uniform(40.0, 160.0, n)  # some collars are inside the bodies
    td = rng.uniform(60.0, 220.0, n)
    bent = np.flatnonzero(rng.random(n) < 0.6)
    s_hole = np.concatenate([bent, bent])
    s_depth = np.concatenate([np.zeros(len(bent)), rng.uniform(30.0, 60.0, len(bent))])
    az = rng.uniform(0.0, 360.0, len(bent))
    s_az = np.concatenate([az, az + rng.uniform(-20.0, 20.0, len(bent))]) % 360.0
    dip = rng.uniform(-90.0, -45.0, len(bent))
    s_dip = np.concatenate([dip, np.clip(dip + rng.uniform(-15.0, 25.0, len(bent)), -90.0, 10.0)])
    traj = desurvey(Collars(np.arange(n), x + O[0], y + O[1], z, td), Surveys(s_hole, s_depth, s_az, s_dip), n_holes=n)
    per = 20
    cuts = np.sort(rng.uniform(0.0, 1.0, (n, per + 1)), axis=1) * td[:, None]
    keep = rng.random((n, per)) < 0.9  # gaps
    hole = np.repeat(np.arange(n), per).reshape(n, per)
    lines = iv.build_lines(hole[keep], cuts[:, :-1][keep], cuts[:, 1:][keep])
    bodies = [
        body("Ядро", cube(3, 30.0, offset=O + [40.0, 40.0, 30.0])),
        body("Шар", icosphere(3, 70.0, centre=O + [60.0, 50.0, 40.0])),
        body("Куб", CUBE),
        body("Пласт", box((-200.0, -200.0, -40.0), (500.0, 500.0, 25.0))),
    ]
    return traj, lines, bodies


def same(a: cg.Coded, b: cg.Coded) -> bool:
    cols = [(c.lines.holes, c.lines.starts, c.lines.frm, c.lines.to, c.rows, c.code) for c in (a, b)]
    frac = a.inside_frac is None or np.array_equal(a.inside_frac, b.inside_frac, equal_nan=True)  # type: ignore[arg-type]
    return all(np.array_equal(p, q) for p, q in zip(*cols, strict=True)) and frac and a.report == b.report


def test_threads_and_the_order_of_the_holes_do_not_change_the_result() -> None:
    n = 9000  # three strips of holes
    traj, lines, bodies = field(n)
    dense = np.arange(n)[lines.holes]
    one = {split: cg.code_by_wireframes(lines, traj, dense, bodies, split=split) for split in (True, False)}
    assert one[True].report["cut"] > 1000 and (np.bincount(one[False].code + 1, minlength=5) > 100).all()
    inside = [b["inside"] for b in one[True].report["bodies"]]  # holes wholly inside the ball and the cube
    assert inside[1] > 20 and inside[2] > 10
    for threads in (2, 4, 8):
        for split in (True, False):
            again = cg.code_by_wireframes(lines, traj, dense, bodies, split=split, threads=threads)
            assert same(one[split], again), (threads, split)
    # the trajectories in another order: the same holes behind other indices
    perm = np.random.default_rng(1).permutation(n)
    back = np.empty(n, dtype=np.int64)
    back[perm] = np.arange(n)
    off = traj.offsets
    rows = np.concatenate([np.arange(off[h], off[h + 1]) for h in perm])
    moved = Trajectories(traj.method, np.concatenate([[0], np.cumsum((off[1:] - off[:-1])[perm])]), traj.md[rows],
                         traj.xyz[rows], traj.dirs[rows], traj.source[perm], traj.skipped, 0, 0)  # fmt: skip
    for split in (True, False):
        assert same(one[split], cg.code_by_wireframes(lines, moved, back[lines.holes], bodies, split=split, threads=3))
    # bodies one at a time from a generator, and progress that can cancel
    seen: list[float] = []
    lazy = cg.code_by_wireframes(lines, traj, dense, (b for b in bodies), n_bodies=4, split=False, tick=seen.append)
    assert same(one[False], lazy) and seen == sorted(seen) and seen[-1] == 1.0 and len(seen) > 8

    def stop(_f: float) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        cg.code_by_wireframes(lines, traj, dense, bodies, tick=stop)


# --- composites by domains over the stretches -----------------------------------------------------------------


def test_stretches_are_domains_of_composites() -> None:
    """`domains.domain_stretches` takes the stretches as they are: no composite crosses an entry or an exit."""
    traj = holes([(37.3, 61.7, 150.25, 300.0)])
    lines = metres([(7, 300.0)])
    values = iv.stack_fields(lines, [np.arange(300.0)])
    st = cg.body_stretches(lines, traj, [0], [body("Руда", CUBE)])
    d = dm.domain_stretches(lines, st.lines, st.code)
    assert d.code.tolist() == [-1, 0, -1]
    assert close(spans(d.lines), [(0.0, 50.25), (50.25, 150.25), (150.25, 300.0)])
    c = dm.composite_by_domains(lines, values, cl.LengthParams.of({"length": 5.0}), d)
    inside = dm.domain_text(c.domain, st.names) == "Руда"
    assert inside.sum() == 20 and c.lines.frm[inside].min() == pytest.approx(50.25, abs=DEPTH)
    assert c.lines.to[inside].max() == pytest.approx(150.25, abs=DEPTH)
    assert c.lines.lengths()[inside] == pytest.approx(5.0, abs=DEPTH)
