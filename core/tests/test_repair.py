"""«Исправить каркас» (ADR 0015 §7, 9.8–9.9): every defect alone and all together, every step switched on
separately, the report «было → стало». Ported from BF `tests/test_repair.py` (the assertions kept; the meshes
are `tests/meshutil` ones: UTM-sized coordinates, triangles shuffled). References and tolerances:

  * the cube of side 60 has the volume 216 000 and the area 21 600 exactly; a fix that only re-indexes or flips
    keeps them to rel 1e-12, a planar patch to rel 1e-9 (summation order);
  * surviving vertices: bit for bit and in the order of the input — `subsequence` finds every output row
    among the input rows (by bytes) and requires the positions to grow;
  * closed parts outward: the signed volume of every closed connected part (computed here) is positive.
Hard data: UTM coordinates, a triangle soup, shifts of 0.4 of the tolerance across the rounding grid, a tolerance
below the spacing of float64 at UTM, read-only input arrays, cancellation from `progress`.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from bf_core.geo import repair as R
from bf_core.geo.meshes import MeshError
from bf_core.geo.repair import repair, repair_checks
from tests.meshutil import UTM, cube, grid, icosphere

SIDE = 60.0
VOLUME = SIDE**3


def box(n: int = 6) -> tuple[np.ndarray, np.ndarray]:
    return cube(n, SIDE, UTM)  # type: ignore[no-any-return]


def assert_sound(checks: dict[str, Any], volume: float | None = None, rel: float = 1e-9) -> None:
    """Every check of a wireframe passes; the volume matches within `rel`."""
    assert checks["closed"] and checks["open_edges"] == 0 and checks["nonmanifold_edges"] == 0
    assert checks["degenerate"] == 0 and checks["duplicate"] == 0 and checks["orientation_conflicts"] == 0
    assert checks["orientation"] == "outward" and checks["self_intersections"] == 0
    if volume is not None:
        assert checks["volume"] == pytest.approx(volume, rel=rel)


def rows(a: np.ndarray) -> set[bytes]:
    return {r.tobytes() for r in np.ascontiguousarray(a)}


def subsequence(out_v: np.ndarray, in_v: np.ndarray) -> bool:
    """Every output vertex is an input one bit for bit (its first occurrence), in the order of the input."""
    first: dict[bytes, int] = {}
    for i, r in enumerate(np.ascontiguousarray(in_v)):
        first.setdefault(r.tobytes(), i)
    pos = [first.get(r.tobytes(), -1) for r in np.ascontiguousarray(out_v)]
    return min(pos, default=0) >= 0 and bool(np.all(np.diff(pos) > 0))


def same_windings(a: np.ndarray, b: np.ndarray) -> bool:
    """The same triangles in the same order with the same normals (any rotation of the corners)."""

    def canonical(t: np.ndarray) -> np.ndarray:
        first = np.argmin(t, axis=1)[:, None]
        return np.take_along_axis(t, (first + np.arange(3)) % 3, axis=1)

    return a.shape == b.shape and np.array_equal(canonical(a), canonical(b))


def step(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in report["steps"] if s["step"] == name)


def only(**on: Any) -> dict[str, Any]:
    """Arguments of `repair` with every fix off except the named ones."""
    base: dict[str, Any] = {"weld": None, "drop_degenerate": False, "drop_duplicate": False, "orient": False}
    base.update(on)
    return base


def signed_volumes(v: np.ndarray, t: np.ndarray) -> list[float]:
    """The signed volume of every connected part (shared vertices), from its own centre."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n = len(v)
    a = np.concatenate([t[:, 0], t[:, 1]])
    b = np.concatenate([t[:, 1], t[:, 2]])
    _, label = connected_components(coo_matrix((np.ones(len(a)), (a, b)), shape=(n, n)), directed=False)
    out = []
    for part in np.unique(label[t[:, 0]]):
        pt = t[label[t[:, 0]] == part]
        c = v[np.unique(pt)].mean(axis=0)
        p, q, r = (v[pt[:, k]] - c for k in range(3))
        out.append(float(np.einsum("ij,ij->i", p, np.cross(q, r)).sum()) / 6.0)
    return out


# ---- defects, one at a time


def soup(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return v[t.reshape(-1)], np.arange(3 * len(t), dtype=np.int32).reshape(-1, 3)


def with_degenerate(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A repeated index, a collinear triple of mesh vertices, and a zero-area triangle built into the surface
    (a vertex inserted on an edge of one of its two triangles only)."""
    line = np.flatnonzero((np.abs(v[:, 1] - UTM[1]) < 1e-9) & (np.abs(v[:, 2] - UTM[2]) < 1e-9))
    line = line[np.argsort(v[line, 0])][:3]
    extra = [[t[5, 0], t[5, 0], t[5, 1]], line.tolist()]
    i = 40
    a, b, p = t[i]
    c = len(v)
    v = np.concatenate([v, [(v[a] + v[b]) / 2]])
    t = np.concatenate([np.delete(t, i, axis=0), [[a, c, p], [c, b, p], [a, b, c]], extra]).astype(np.int32)
    return v, t


def with_duplicates(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return v, np.concatenate([t, t[[3, 9]], t[[20, 21]][:, [1, 2, 0]], t[[30]][:, ::-1]]).astype(np.int32)


def with_flips(v: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t = t.copy()
    idx = np.random.default_rng(3).choice(len(t), 25, replace=False)
    t[idx] = t[idx][:, ::-1]
    return v, t


def test_checks_of_a_sound_solid_and_of_every_defect() -> None:
    v, t = box()
    c = repair_checks(v, t)
    assert_sound(c, VOLUME, rel=1e-12)
    assert c["triangles"] == len(t) and c["vertices"] == len(v) and c["components"] == 1
    assert c["area"] == pytest.approx(6 * SIDE**2, rel=1e-12)
    assert repair_checks(v, t, intersections=False)["self_intersections"] is None

    assert repair_checks(*soup(v, t))["open_edges"] == 3 * len(t)
    assert repair_checks(*soup(v, t))["components"] == len(t)
    assert repair_checks(*with_degenerate(v, t))["degenerate"] == 3
    assert repair_checks(*with_duplicates(v, t))["duplicate"] == 5
    assert repair_checks(*with_flips(v, t))["orientation_conflicts"] > 0
    inward = repair_checks(v, t[:, ::-1])
    assert inward["orientation"] == "inward" and inward["volume"] == pytest.approx(VOLUME, rel=1e-12)
    empty = repair_checks(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32))
    assert empty["triangles"] == 0 and empty["duplicate"] == 0 and empty["components"] == 0


def test_weld_restores_a_triangle_soup_exactly() -> None:
    v, t = box()
    sv, st = soup(v, t)
    out_v, out_t, report = repair(sv, st)
    assert_sound(report["after"], VOLUME, rel=1e-12)
    assert report["before"]["open_edges"] == 3 * len(t) and report["tolerance"] == 1e-6
    assert step(report, "weld")["changed"] == len(sv) - len(v) and step(report, "weld")["title"]
    # no averaging: the vertices are exactly the source ones, each at its first occurrence, in that order
    assert rows(out_v) == rows(v) and len(out_t) == len(t) and subsequence(out_v, sv)


def test_weld_merges_within_the_tolerance_and_keeps_the_first_vertex() -> None:
    v, t = box()
    # copies shifted by 0.4 of the tolerance in every direction (also across the rounding grid)
    rng = np.random.default_rng(5)
    shifted = v + rng.choice([-4e-7, 4e-7], size=v.shape)
    half = len(t) // 2
    both = np.concatenate([v, shifted])
    tt = t.copy()
    tt[half:] += len(v)
    out_v, _out_t, report = repair(both, tt)
    assert_sound(report["after"], VOLUME, rel=1e-12)
    assert out_v.tobytes() == v.tobytes()  # the first of every pair, in the order given
    # a tolerance below the shift leaves the two halves apart
    assert not repair(both, tt, weld=1e-8)[2]["after"]["closed"]
    # weld=0 merges only exactly equal vertices; so does a tolerance below the spacing of float64 at UTM
    assert repair(*soup(v, t), weld=0.0)[2]["after"]["closed"]
    assert repair(*soup(v, t), weld=1e-300)[2]["after"]["closed"]
    assert not repair(both, tt, weld=1e-300)[2]["after"]["closed"]
    # weld_on=False is the same as weld=None
    assert repair(*soup(v, t), weld_on=False)[2]["after"]["open_edges"] == 3 * len(t)
    with pytest.raises(MeshError) as bad:
        repair(v, t, weld=-1.0)
    assert bad.value.code == "bad_tolerance" and bad.value.hint


def test_degenerate_triangles_are_dropped_or_flipped_away() -> None:
    v, t = box()
    dv, dt = with_degenerate(v, t)
    out_v, out_t, report = repair(dv, dt)
    assert_sound(report["after"], VOLUME, rel=1e-12)
    s = step(report, "degenerate")
    # the two loose ones are dropped; the built-in one is removed by flipping its longest edge
    assert (s["dropped"], s["flipped"], s["changed"]) == (2, 1, 3)
    assert len(out_t) == len(t) + 2 and len(out_v) == len(v) + 1 and out_v.tobytes() == dv.tobytes()


def test_duplicate_triangles_are_dropped() -> None:
    v, t = box()
    _out_v, out_t, report = repair(*with_duplicates(v, t))
    assert_sound(report["after"], VOLUME, rel=1e-12)
    assert step(report, "duplicate")["changed"] == 5 and np.array_equal(out_t, t)


def test_flipped_triangles_and_an_inward_mesh_are_oriented() -> None:
    v, t = box()
    _, out_t, report = repair(*with_flips(v, t))
    assert_sound(report["after"], VOLUME, rel=1e-12)
    assert step(report, "orient")["changed"] == 25 and same_windings(out_t, t)

    _out_v, out_t, report = repair(v, t[:, ::-1])
    assert_sound(report["after"], VOLUME, rel=1e-12)
    assert step(report, "orient")["changed"] == len(t) and step(report, "orient")["turned_outward"] == 1


def test_closed_parts_are_turned_outward_open_ones_keep_their_majority() -> None:
    a_v, a_t = box(4)  # inward
    b_v, b_t = icosphere(3, 25.0, UTM + [200.0, 0.0, 0.0])  # outward, 30 triangles flipped
    c_v, c_t = grid(12, 9, lambda x, y: 0.1 * x * y, step=5.0, offset=UTM + [0.0, 300.0, 0.0])  # open, up
    b_t = with_flips(b_v, b_t)[1]
    c_t = c_t.copy()
    c_t[:20] = c_t[:20, ::-1]  # a minority of the open surface looks down
    v = np.concatenate([a_v, b_v, c_v])
    t = np.concatenate([a_t[:, ::-1], b_t + len(a_v), c_t + len(a_v) + len(b_v)]).astype(np.int32)
    out_v, out_t, report = repair(v, t, **only(orient=True))
    s = step(report, "orient")
    assert (s["parts"], s["closed_parts"], s["turned_outward"]) == (3, 2, 1)
    assert s["changed"] == len(a_t) + 25 + 20 and out_v.tobytes() == v.tobytes()
    closed = signed_volumes(out_v, out_t[: len(a_t) + len(b_t)])
    assert len(closed) == 2 and min(closed) > 0
    # the open surface: every triangle looks up again (its majority)
    sheet = out_t[len(a_t) + len(b_t) :]
    n = np.cross(out_v[sheet[:, 1]] - out_v[sheet[:, 0]], out_v[sheet[:, 2]] - out_v[sheet[:, 0]])
    assert np.all(n[:, 2] > 0)


@pytest.mark.parametrize(
    ("defect", "switch", "check"),
    [
        (soup, {"weld": 1e-6}, "open_edges"),
        (with_degenerate, {"drop_degenerate": True}, "degenerate"),
        (with_duplicates, {"drop_duplicate": True}, "duplicate"),
        (with_flips, {"orient": True}, "orientation_conflicts"),
    ],
)
def test_every_fix_is_switched_on_separately(defect: Any, switch: dict[str, Any], check: str) -> None:
    dv, dt = defect(*box())
    assert repair_checks(dv, dt)[check] > 0
    # all fixes off: nothing changes; only this one on: its check passes
    same_v, same_t, report = repair(dv, dt, **only())
    assert report["steps"] == [] and report["after"][check] == report["before"][check] and len(same_t) == len(dt)
    assert np.array_equal(same_t, dt) and same_v.tobytes() == dv.tobytes()
    _, _, report = repair(dv, dt, **only(**switch))
    assert len(report["steps"]) == 1 and report["steps"][0]["changed"] > 0
    assert report["after"][check] == 0


# ---- the contract of the function


def test_read_only_inputs_cancellation_and_progress() -> None:
    v, t = with_flips(*soup(*box()))
    v.flags.writeable = False
    t.flags.writeable = False
    keep_v, keep_t = v.copy(), t.copy()
    calls: list[float] = []
    out_v, out_t, report = repair(v, t, progress=calls.append)
    assert_sound(report["after"], VOLUME, rel=1e-12)
    assert v.tobytes() == keep_v.tobytes() and t.tobytes() == keep_t.tobytes()  # inputs untouched
    assert calls == sorted(calls) and calls[-1] == 1.0 and len(calls) >= 6
    assert out_v.dtype == np.float64 and out_t.dtype == np.int32 and out_v.flags.c_contiguous

    class Stop(Exception):
        pass

    def cancel(fraction: float) -> None:
        if fraction > 0.3:
            raise Stop

    with pytest.raises(Stop):
        repair(v, t, progress=cancel)

    # an empty wireframe comes back as it is; a vertex without coordinates is refused, not zeroed
    e_v, e_t, e_report = repair(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32))
    assert len(e_t) == 0 and e_report["steps"] == [] and e_report["after"]["triangles"] == 0
    bad = np.array(keep_v)
    bad[7, 2] = np.nan
    with pytest.raises(MeshError) as err:
        repair(bad, keep_t)
    assert err.value.code == "bad_coordinates"


def test_unused_vertices_are_dropped_and_the_rest_keep_their_order() -> None:
    v, t = box()
    extra = UTM + np.array([[-5.0, -5.0, -5.0], [1.0, 2.0, 3.0]])
    v2 = np.concatenate([extra[:1], v, extra[1:]])
    out_v, out_t, report = repair(v2, t + 1)
    assert out_v.tobytes() == v.tobytes() and np.array_equal(out_t, t)
    assert report["before"]["vertices"] == len(v) + 2 and report["after"]["vertices"] == len(v)
    assert R.TITLES.keys() >= {"weld", "duplicate", "degenerate", "orient"}
