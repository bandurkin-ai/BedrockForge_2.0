"""Solids, part 1: the manifold3d bridge and the boolean operations (ТЗ 7.23, 5.3, 17 п. 5; ADR 0015 §7, 9.11).

Ported from BF `tests/test_solids.py` (the boolean half) with the assertions kept; differences: errors carry
{code, message, hint}, the hint names «Исправить каркас»; `tick(fraction, message)` instead of `progress`.
Synthetic bodies at UTM coordinates with known answers; the tolerance of every check is written next to it.
"""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from bf_core.geo import solids
from bf_core.geo.meshes import mesh_stats
from bf_core.geo.solids import SolidError, boolean, boolean_many, expand, is_closed_solid
from tests.solidutil import L_SHAPE, OFFSET, assert_good_solid, box, prism, sphere, volume

# --- boolean operations


def test_shifted_cubes_have_exact_volumes() -> None:
    a = box((0, 0, 0), (10, 10, 10))
    b = box((4, 3, 2), (14, 13, 12))
    common = 6 * 7 * 8
    for op, expected in (("union", 2000 - common), ("intersection", common), ("difference", 1000 - common)):
        v, t, report = boolean(a, b, op)
        assert_good_solid((v, t))
        # 9.11 criterion: cube − cube is closed and its volume is right to 1e-9 m³ (10 m cubes at UTM).
        assert volume((v, t)) == pytest.approx(expected, abs=1e-9)
        assert report["volume"] == pytest.approx(expected, abs=1e-9)
        assert report["volume_a"] == pytest.approx(1000, abs=1e-9)
        assert report["volume_b"] == pytest.approx(1000, abs=1e-9)
        assert report["op"] == op and report["closed"] and not report["empty"]
        assert report["parts"] == 1 and report["triangles"] == len(t)
        assert report["origin"] == np.round(OFFSET + [7, 6.5, 6]).tolist()
        assert 0 < report["tolerance"] < 1e-9
        # The result stays inside the extent of the inputs (to the last bit of float64 here).
        assert np.all(v.min(axis=0) >= OFFSET - 1e-9) and np.all(v.max(axis=0) <= OFFSET + [14, 13, 12] + 1e-9)


def test_input_vertices_reach_the_result_bit_for_bit() -> None:
    """Corners of A that survive the difference are the input numbers exactly (the origin is whole)."""
    a = box((0, 0, 0), (10, 10, 10))
    b = box((4, 3, 2), (14, 13, 12))
    v, _, _ = boolean(a, b, "difference")
    kept = {tuple(p) for p in v.tolist()}
    survivors = [p for p, q in zip(a[0].tolist(), (a[0] - OFFSET).tolist(), strict=True) if q != [10, 10, 10]]
    assert len(survivors) == 7 and all(tuple(p) in kept for p in survivors)


def test_difference_is_not_symmetric_and_disjoint_bodies() -> None:
    a = box((0, 0, 0), (10, 10, 10))
    b = box((4, 3, 2), (14, 13, 12))
    assert boolean(b, a, "difference")[2]["volume"] == pytest.approx(1000 - 336, abs=1e-9)
    far = box((100, 0, 0), (110, 10, 10))
    v, t, report = boolean(a, far, "intersection")
    assert report["empty"] and report["parts"] == 0 and report["volume"] == 0.0
    assert v.shape == (0, 3) and t.shape == (0, 3)
    v, t, report = boolean(a, far, "union")
    assert report["parts"] == 2 and report["volume"] == pytest.approx(2000, abs=1e-9)
    assert_good_solid((v, t))


def test_sphere_and_cube() -> None:
    r = 5.0
    ball = sphere(r, 64, 128, centre=(0, 0, 0))
    half = box((0, -6, -6), (6, 6, 6))  # covers the half x >= 0 of the sphere
    exact = 4 / 3 * math.pi * r**3
    ball_volume = volume(ball)
    # Discretisation of the sphere (64 × 128): its volume is below the exact one by < 0.2 %.
    assert ball_volume == pytest.approx(exact, rel=2e-3)
    v, t, report = boolean(ball, half, "intersection")
    assert_good_solid((v, t))
    # The mesh is symmetric about x = 0: exactly half of the faceted sphere (rel 1e-9).
    assert report["volume"] == pytest.approx(ball_volume / 2, rel=1e-9)
    assert report["volume"] == pytest.approx(exact / 2, rel=2e-3)
    v, t, report = boolean(half, ball, "difference")
    assert_good_solid((v, t))
    assert report["volume"] == pytest.approx(6 * 12 * 12 - ball_volume / 2, rel=1e-9)
    v, t, report = boolean(ball, half, "union")
    assert_good_solid((v, t))
    assert report["volume"] == pytest.approx(6 * 12 * 12 + ball_volume / 2, rel=1e-9)


@pytest.mark.parametrize("shift", [0.0, 1e-9, 1e-6, 1e-3])
def test_bodies_with_a_common_face(shift: float) -> None:
    """Hard data (ТЗ 17 п. 5): two prisms of one contour at UTM, stacked, the upper one lowered by `shift`."""
    lower = prism(L_SHAPE, 0, 10)
    upper = prism(L_SHAPE, 10 - shift, 20 - shift)
    v, t, report = boolean(lower, upper, "union")
    assert_good_solid((v, t))
    assert report["parts"] == 1
    # Tolerance 1e-6 m³ (bodies of 640 m³).
    assert report["volume"] == pytest.approx(64 * (20 - shift), abs=1e-6)
    v, t, report = boolean(lower, upper, "intersection")
    # Empty, or the sliver the two bodies really share.
    assert report["volume"] == pytest.approx(64 * shift, abs=1e-6)
    assert report["empty"] == (len(t) == 0)
    if not report["empty"]:
        assert mesh_stats(v, t)["closed"]
    v, t, report = boolean(lower, upper, "difference")
    assert_good_solid((v, t))
    assert report["volume"] == pytest.approx(64 * (10 - shift), abs=1e-6)


@pytest.mark.parametrize("gap", [1e-9, 1e-6, 1e-3])
def test_bodies_almost_touching(gap: float) -> None:
    """The same contour with a gap between the bodies: nothing in common, no exception."""
    lower = prism(L_SHAPE, 0, 10)
    upper = prism(L_SHAPE, 10 + gap, 20 + gap)
    v, t, report = boolean(lower, upper, "union")
    assert mesh_stats(v, t)["closed"]
    assert report["volume"] == pytest.approx(1280, abs=1e-6)
    assert boolean(lower, upper, "intersection")[2]["volume"] == pytest.approx(0, abs=1e-6)
    assert boolean(lower, upper, "difference")[2]["volume"] == pytest.approx(640, abs=1e-6)


@pytest.mark.parametrize("shift", [1e-9, 1e-6, 1e-3])
def test_shifted_cube_minus_cube_at_utm(shift: float) -> None:
    """Hard data: B's faces nearly on A's (shift along all three axes), the difference stays closed."""
    a = box((0, 0, 0), (10, 10, 10))
    b = box((5 + shift, -shift, -shift), (10 + shift, 10 + shift, 10 + shift))
    v, t, report = boolean(a, b, "difference")
    assert_good_solid((v, t))
    assert report["volume"] == pytest.approx(10 * 10 * (5 + shift), abs=1e-6)
    assert boolean(a, b, "intersection")[2]["volume"] == pytest.approx(10 * 10 * (5 - shift), abs=1e-6)


def test_side_by_side_bodies_sharing_a_wall() -> None:
    """Bodies sharing a vertical face with different triangulations of it."""
    a = prism(L_SHAPE, 0, 10)
    b = box((10, 0, 0), (15, 4, 10))
    v, t, report = boolean(a, b, "union")
    assert_good_solid((v, t))
    assert report["parts"] == 1
    assert report["volume"] == pytest.approx(640 + 200, abs=1e-6)
    # The bodies only touch: nothing, or the common wall as a skin of zero volume, marked flat.
    common = boolean(a, b, "intersection")[2]
    assert common["volume"] == pytest.approx(0.0, abs=1e-12) and (common["empty"] or common["flat"])
    assert not report["flat"]


VOLUME_CASES = {
    "cubes": lambda: (box((0, 0, 0), (10, 10, 10)), box((4, 3, 2), (14, 13, 12))),
    "non-convex and cube": lambda: (prism(L_SHAPE, 0, 10), box((2, 2, -3), (9, 9, 6))),
    "sphere and cube": lambda: (sphere(5, 48, 96), box((1, -2, -3), (8, 9, 4))),
    "two spheres": lambda: (sphere(5, 48, 96), sphere(4, 40, 80, centre=(3, 2, 1))),
    "sphere and non-convex": lambda: (sphere(6, 48, 96, centre=(4, 4, 5)), prism(L_SHAPE, 0, 10)),
    "common face": lambda: (prism(L_SHAPE, 0, 10), prism(L_SHAPE, 10, 20)),
}


@pytest.mark.parametrize("case", list(VOLUME_CASES))
def test_volume_identity(case: str) -> None:
    a, b = VOLUME_CASES[case]()
    reports = {op: boolean(a, b, op) for op in ("union", "intersection", "difference")}
    va, vb = volume(a), volume(b)
    union, common, minus = (reports[op][2]["volume"] for op in ("union", "intersection", "difference"))
    # V(A∪B) = V(A) + V(B) − V(A∩B) and V(A−B) = V(A) − V(A∩B): relative 1e-9 of V(A) + V(B).
    assert union == pytest.approx(va + vb - common, abs=1e-9 * (va + vb))
    assert minus == pytest.approx(va - common, abs=1e-9 * (va + vb))
    for op in ("union", "difference"):
        assert_good_solid(reports[op][:2])
    if not reports["intersection"][2]["empty"]:
        assert_good_solid(reports["intersection"][:2])


def test_inward_solid_is_fixed_and_reported() -> None:
    a = box((0, 0, 0), (10, 10, 10))
    b = box((4, 3, 2), (14, 13, 12))
    inside_out = (b[0], b[1][:, ::-1].copy())
    assert mesh_stats(*inside_out)["orientation"] == "inward"
    v, t, report = boolean(a, inside_out, "difference", names=("Руда", "Карьер"))
    assert report["flipped"] == ["Карьер"]
    assert report["volume"] == pytest.approx(1000 - 336, abs=1e-9)
    assert_good_solid((v, t))
    assert boolean(a, b, "union")[2]["flipped"] == []


def test_boolean_many() -> None:
    boxes = [box((0, 0, 0), (10, 10, 10)), box((5, 0, 0), (15, 10, 10)), box((0, 5, 0), (10, 15, 10))]
    v, t, report = boolean_many(boxes, "union")
    assert_good_solid((v, t))
    assert report["volume"] == pytest.approx(2000, abs=1e-9)  # 1000 + 500 + 500
    assert report["volumes"] == pytest.approx([1000, 1000, 1000], abs=1e-9)
    assert boolean_many(boxes, "intersection")[2]["volume"] == pytest.approx(250, abs=1e-9)
    # The first minus the rest.
    assert boolean_many(boxes, "difference")[2]["volume"] == pytest.approx(250, abs=1e-9)
    with pytest.raises(SolidError, match="не меньше двух"):
        boolean_many(boxes[:1], "union")


def test_inputs_are_not_changed() -> None:
    a, b = box((0, 0, 0), (10, 10, 10)), box((4, 3, 2), (14, 13, 12))
    inside_out = (b[0], b[1][:, ::-1].copy())
    copies = [x.copy() for x in (*a, *inside_out)]
    boolean(a, inside_out, "difference")
    for x, y in zip((*a, *inside_out), copies, strict=True):
        assert np.array_equal(x, y)


def test_tick_is_reported_and_may_cancel() -> None:
    a, b = box((0, 0, 0), (10, 10, 10)), box((4, 3, 2), (14, 13, 12))
    seen: list[tuple[float, str]] = []
    boolean(a, b, "union", tick=lambda fraction, message: seen.append((fraction, message)))
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions) and fractions[0] == 0.0 and fractions[-1] == 1.0
    assert all(isinstance(m, str) and m for _, m in seen)

    class Cancelled(Exception):
        pass

    def stop(fraction: float, message: str) -> None:
        if fraction > 0.2:
            raise Cancelled

    with pytest.raises(Cancelled):
        boolean(a, b, "union", tick=stop)


# --- refused inputs


def test_open_mesh_is_refused_with_the_number_of_open_edges() -> None:
    a = box((0, 0, 0), (10, 10, 10))
    holed = (a[0], a[1][:-2])
    ok, reason = is_closed_solid(*holed)
    assert not ok and "открытых рёбер — 4" in reason
    with pytest.raises(SolidError) as error:
        boolean(holed, a, "union", names=("Рудное тело 1", "Карьер"))
    e = error.value
    assert e.code == "wireframe_open"
    assert (
        e.message
        == "Каркас «Рудное тело 1» не замкнут: открытых рёбер — 4. «Объединение» выполняется только для замкнутых тел."
    )
    assert "«Исправить каркас»" in e.hint and e.as_dict() == {"code": e.code, "message": e.message, "hint": e.hint}
    with pytest.raises(SolidError, match="Тело B"):
        boolean(a, holed, "intersection")
    with pytest.raises(SolidError, match="Неизвестная операция") as error:
        boolean(a, a, "xor")
    assert error.value.code == "bad_operation"


def test_other_defects_are_refused() -> None:
    v, t = box((0, 0, 0), (10, 10, 10))
    ok, reason = is_closed_solid(v, np.zeros((0, 3), dtype=np.int32))
    assert not ok and "пуст" in reason
    with pytest.raises(SolidError, match="пуст") as error:
        boolean((v, np.zeros((0, 3), dtype=np.int32)), (v, t), "union")
    assert error.value.code == "empty_mesh"
    # One triangle turned over: closed, but the normals disagree.
    turned = t.copy()
    turned[0] = turned[0, ::-1]
    ok, reason = is_closed_solid(v, turned)
    assert not ok and "не согласованы" in reason
    with pytest.raises(SolidError) as error:
        boolean((v, turned), (v, t), "union")
    assert error.value.code == "wireframe_orientation" and "Исправить каркас" in error.value.hint
    # An extra triangle on an edge: three triangles share it.
    fin = (np.vstack([v, v[0] + [5, 5, 30]]), np.vstack([t, [[0, 1, 8]]]))
    ok, reason = is_closed_solid(*fin)
    assert not ok and "трёх и более" in reason
    with pytest.raises(SolidError) as error:
        boolean(fin, (v, t), "union")
    assert error.value.code in ("wireframe_open", "wireframe_nonmanifold")
    with pytest.raises(SolidError, match="несуществующие вершины"):
        boolean((v, t + 1), (v, t), "union")
    nan = v.copy()
    nan[0, 2] = np.nan
    with pytest.raises(SolidError, match="нечисловые"):
        boolean((nan, t), (v, t), "union")
    with pytest.raises(SolidError, match="не выполняется") as error:
        expand((v, t), 1.0)
    assert error.value.code == "not_supported"


def test_library_statuses_are_russian() -> None:
    """A mesh that passes our checks but not manifold3d's (face ids of a wrong length)."""
    v, t = box((0, 0, 0), (10, 10, 10))
    with pytest.raises(SolidError) as error:
        solids.to_manifold(v - OFFSET, t, "Тело", face_id=np.arange(5, dtype=np.uint64))
    assert error.value.code == "solid_rejected" and "«Тело»" in error.value.message
    assert error.value.status  # the library's own name of the status stays for the log
    assert not any("a" <= ch.lower() <= "z" for ch in error.value.message + error.value.hint)


@pytest.mark.slow
def test_speed_boolean_two_million_triangles() -> None:
    """ТЗ 5.3: a boolean of 2 × 1 million triangles ≤ 8 s (here a generous limit; the budget is in `bench`)."""
    ball = sphere(50, 500, 1000)
    other = sphere(50, 500, 1000, centre=(30, 20, 10))
    assert len(ball[1]) == len(other[1]) == 998_000
    started = time.perf_counter()
    v, t, report = boolean(ball, other, "difference")
    seconds = time.perf_counter() - started
    print(f"\nboolean: 2 × {len(ball[1])} triangles, difference: {seconds:.2f} s, result {len(t)} triangles")
    assert report["parts"] == 1 and mesh_stats(v, t)["closed"]
    half = boolean(ball, box((0, -60, -60), (60, 60, 60)), "intersection")[2]
    assert half["volume"] == pytest.approx(volume(ball) / 2, rel=1e-9)
    assert seconds < 30


def test_module_is_exported() -> None:
    for name in ("SolidError", "is_closed_solid", "boolean", "boolean_many", "expand", "to_manifold"):
        assert hasattr(solids, name)
