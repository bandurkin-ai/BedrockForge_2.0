"""The faults of an implicit model (подшаг 19.33; ТЗ Д-24 «Разлом»; ADR 0026 решение 20) on analytical scenes.

Т8.9-11 throw: a vertical fault x = 0, the layer z = 0 on the left and z = −10 on the right: the vertices of the
layer on the wings are z = 0 and z = −10 (≤ 0,25·h), the break at the fault is 10 m (the offset the module
measures, read only), the wireframes of the wings are closed and do not overlap, Σ the volumes = the boundary (1e-9).
Т8.9-12 finite fault: beyond R_F the fields of both wings are the undivided field f_0 BIT FOR BIT, the offset is 0, the
wings do not overlap; near the data of the fault the break is the whole 10 m.
Also: two faults (the wings by the combinations of the signs), a fault that cuts one group of two, the same bytes with
1, 2, 4, 8 threads and any order of the holes, the data on the fault go to both wings, the errors in Russian
{code, message, hint}, the cancel, a model without a cut group is `build_stack` bit by bit, the module is light."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.geo import checks, meshes, solids
from bf_core.model import data as md
from bf_core.model.bodies_parts import box_solid
from bf_core.model.faults import FaultsResult, build_with_faults
from bf_core.model.stack import build_stack
from tests.test_model_bodies import Cancelled, assert_solid, domain, model, overlap
from tests.test_model_stack import column, points

CORE = Path(__file__).resolve().parents[1]
H = 3.0
BOX = [-60.0, 0.0, -40.0, 60.0, 60.0, 40.0]
VOLUME = 120.0 * 60.0 * 80.0
YS = np.arange(0.0, 61.0, 10.0)
ZS = np.arange(-40.0, 41.0, 10.0)


def fault_group(gid: int, name: str, radius: float | None = None) -> dict[str, Any]:
    return {"id": gid, "name": name, "type": "fault", "codes": [], "params": {"radius": radius}}


def cut(gid: int, name: str, *faults: int, **more: Any) -> dict[str, Any]:
    return {**domain(gid, name), "faults": list(faults), **more}


def mesh_yz(ys: np.ndarray = YS, zs: np.ndarray = ZS) -> tuple[np.ndarray, np.ndarray]:
    gy, gz = np.meshgrid(ys, zs, indexing="ij")
    return gy.ravel(), gz.ravel()


def plane_x(gid: int, x0: float, *, contacts: bool = True, ys: np.ndarray = YS, zs: np.ndarray = ZS,
            name: str = "Разлом") -> md.Points:  # fmt: skip
    """The fault x = x0: f = x − x0 (f < 0 on the left): contacts on the plane, ±d points beside it all over it."""
    gy, gz = mesh_yz()
    parts = []
    if contacts:
        cy, cz = mesh_yz(ys, zs)
        parts.append(("contact", column(np.full_like(cy, x0), cy, cz), np.zeros_like(cy)))
    for d in (15.0, 30.0):
        parts.append(("inside", column(np.full_like(gy, x0 - d), gy, gz), np.full_like(gy, -d)))
        parts.append(("outside", column(np.full_like(gy, x0 + d), gy, gz), np.full_like(gy, d)))
    return points(gid, name, parts)


def layer(gid: int, xs: np.ndarray, z0: float, name: str = "Пласт", above: bool = False) -> md.Points:
    """A horizontal surface z = z0 over the columns `xs` (inside is below: f = z − z0; `above` — the inside is above,
    f = z0 − z) — the data of a wing."""
    sign = -1.0 if above else 1.0
    gx, gy = np.meshgrid(xs, YS, indexing="ij")
    gx, gy = gx.ravel(), gy.ravel()
    parts = [("contact", column(gx, gy, np.full_like(gx, z0)), np.zeros_like(gx))]
    for d in (10.0, 20.0):
        parts.append(("inside", column(gx, gy, np.full_like(gx, z0 - sign * d)), np.full_like(gx, -d)))
        parts.append(("outside", column(gx, gy, np.full_like(gx, z0 + sign * d)), np.full_like(gx, d)))
    return points(gid, name, parts)


LEFT = np.arange(-55.0, -4.0, 10.0)
RIGHT = np.arange(5.0, 56.0, 10.0)


def throw_points(radius_patch: bool = False) -> md.Points:
    """The fault (group 2) and the layer (group 1): z = 0 left of x = 0, z = −10 right of it."""
    fault = plane_x(2, 0.0, ys=YS[:3], zs=ZS[2:7]) if radius_patch else plane_x(2, 0.0)
    return md.assemble([layer(1, LEFT, 0.0), layer(1, RIGHT, -10.0), fault])


def throw_spec(radius: float | None = None) -> Any:
    return model([cut(1, "Пласт", 2), fault_group(2, "Сброс", radius)], BOX, h=H)


def faults_of(res: FaultsResult, gid: int) -> list[Any]:
    return [o for o in res.outputs if o.group == gid]


def bodies(res: FaultsResult) -> list[Any]:
    """The rock: the bodies of the domains and «Прочее» (not the regions of the wings of a fault)."""
    return [o for o in res.outputs if o.kind == "body" and not o.stats.get("region")]


def regions(res: FaultsResult) -> list[Any]:
    return [o for o in res.outputs if o.kind == "body" and o.stats.get("region")]


def same(a: FaultsResult, b: FaultsResult) -> None:
    keys = [(o.kind, o.group, o.wing, o.name) for o in a.outputs]
    assert keys == [(o.kind, o.group, o.wing, o.name) for o in b.outputs]
    for x, y in zip(a.outputs, b.outputs, strict=True):
        assert x.vertices.tobytes() == y.vertices.tobytes()
        assert x.triangles.tobytes() == y.triangles.tobytes()
        assert x.volume == y.volume
    assert a.stats["by_group"] == b.stats["by_group"]


@pytest.fixture(scope="module")
def throw() -> FaultsResult:
    return build_with_faults(throw_spec(), throw_points(), box_solid(BOX), threads=4)


# --- Т8.9-11 ---


def test_throw_t8911_the_layer_is_z0_left_and_z10_right_with_a_break_of_10(throw: FaultsResult) -> None:
    res = throw
    assert [(o.kind, o.group, o.wing) for o in res.outputs] == [
        ("surface", 2, None), ("body", 2, 0), ("body", 2, 1),
        ("surface", 1, 0), ("body", 1, 0), ("surface", 1, 1), ("body", 1, 1),
        ("body", None, 0), ("body", None, 1),
    ]  # fmt: skip
    left, right = (o for o in res.outputs if o.kind == "surface" and o.group == 1)
    assert left.name == "Пласт, крыло 0: контакт" and right.name == "Пласт, крыло 1: контакт"
    assert np.abs(left.vertices[:, 2] - 0.0).max() <= 0.25 * H  # the wings: z = 0 and z = −10
    assert np.abs(right.vertices[:, 2] + 10.0).max() <= 0.25 * H
    assert left.vertices[:, 0].max() <= 1e-6 and right.vertices[:, 0].min() >= -1e-6  # each stops at the fault
    assert left.vertices[:, 0].max() > -2.0 * H and right.vertices[:, 0].min() < 2.0 * H  # and reaches it
    sheet = faults_of(res, 2)[0]
    assert np.abs(sheet.vertices[:, 0]).max() <= 1e-6  # the surface of the fault: the plane x = 0
    # the break at the fault: the same y at the fault edge, 10 m apart
    assert left.vertices[:, 2].max() - right.vertices[:, 2].max() == pytest.approx(10.0, abs=0.25 * H)
    by_group = res.stats["by_group"]
    assert [g["group"] for g in by_group] == [1] and by_group[0]["wings"] == [0, 1] and by_group[0]["faults"] == [2]
    offset = by_group[0]["offset"]
    assert offset["mean"] == pytest.approx(10.0, abs=1e-6) and offset["max"] == pytest.approx(10.0, abs=1e-6)
    assert by_group[0]["offset_by_fault"][2]["max"] == pytest.approx(10.0, abs=1e-6)


def test_throw_the_wings_are_closed_and_do_not_overlap_and_the_volumes_add_up(throw: FaultsResult) -> None:
    res = throw
    wings = regions(res)
    assert [o.volume for o in wings] == [pytest.approx(VOLUME / 2, rel=1e-9)] * 2
    assert sum(o.volume for o in wings) == pytest.approx(VOLUME, rel=1e-9)  # Σ the wings of the fault = the boundary
    assert res.stats["faults"][2]["balance"] <= 1e-9
    rock = bodies(res)
    assert [o.name for o in rock] == ["Пласт, крыло 0", "Пласт, крыло 1", "Прочее, крыло 0", "Прочее, крыло 1"]
    want = [60.0 * 60.0 * 40.0, 60.0 * 60.0 * 30.0, 60.0 * 60.0 * 40.0, 60.0 * 60.0 * 50.0]  # below / above the layer
    for o, v in zip(rock, want, strict=True):
        assert o.volume == pytest.approx(v, rel=1e-6)
    assert sum(o.volume for o in rock) == pytest.approx(VOLUME, rel=1e-9) and res.stats["balance"] <= 1e-9
    for o in (*rock, *wings):
        assert_solid(o.vertices, o.triangles)
    for group in (rock, wings):
        for i, x in enumerate(group):
            for y in group[i + 1 :]:
                assert overlap(x, y) <= 1e-9 * VOLUME
    assert [o.wing for o in wings] == [0, 1] and all(o.stats["region"] for o in wings)


def test_throw_the_field_is_the_wing_field_and_breaks_at_the_fault(throw: FaultsResult) -> None:
    field = throw.fields[1]
    p = np.array([[-5.0, 20.0, -4.0], [5.0, 20.0, -4.0], [-40.0, 30.0, 10.0], [40.0, 30.0, -25.0]])
    f = field(p, threads=2)
    assert f[0] == pytest.approx(-4.0, abs=1e-6) and f[1] == pytest.approx(-4.0 + 10.0, abs=1e-6)
    assert f[2] == pytest.approx(10.0, abs=1e-6) and f[3] == pytest.approx(-25.0 + 10.0, abs=1e-6)
    fault = throw.faults[2].field(p)
    assert fault == pytest.approx(p[:, 0], abs=1e-6)  # f_F = x
    on = np.array([[0.0, 20.0, -4.0]])  # on the fault: either wing, as the position is forced
    assert field(on, force={0: 1})[0] == pytest.approx(6.0, abs=1e-6)
    assert field(on, force={0: 0})[0] == pytest.approx(-4.0, abs=1e-6)
    assert throw.fits[1].field is field and throw.fits[1].stats["wings"] == [0, 1]


def test_the_data_on_the_fault_go_to_both_wings() -> None:
    """A contact of the layer on the plane x = 0 belongs to both wings; the wing fields both reproduce it."""
    shared = layer(1, np.array([0.0]), -5.0)  # the column x = 0: f_F = 0 within the tolerance
    pts = md.assemble([throw_points(), shared])
    res = build_with_faults(throw_spec(), pts, box_solid(BOX), threads=2)
    wing_n = {k[2]: f.stats["points"] for k, f in res.wing_fits.items()}
    base = throw_points().of_group(1)
    assert wing_n[(0,)] == int((base.xyz[:, 0] < 0).sum()) + shared.n
    assert wing_n[(1,)] == int((base.xyz[:, 0] > 0).sum()) + shared.n
    assert res.fits[1].stats["points"] == base.n + shared.n  # the group's points, counted once


# --- the same bytes ---


def test_throw_is_the_same_bytes_for_threads_and_the_order_of_the_data(throw: FaultsResult) -> None:
    pts = throw_points()
    rng = np.random.default_rng(11)
    shuffled = md.assemble([pts.of_group(g).take(rng.permutation(pts.of_group(g).n)) for g in (2, 1)])
    assert shuffled.to_parquet() == pts.to_parquet()
    for threads, data in ((1, pts), (2, shuffled), (8, pts)):
        same(throw, build_with_faults(throw_spec(), data, box_solid(BOX), threads=threads))
    probe = np.random.default_rng(3).uniform([-55, 5, -35], [55, 55, 35], size=(300, 3))
    again = build_with_faults(throw_spec(), pts, box_solid(BOX), threads=1)
    assert again.fields[1](probe, threads=1).tobytes() == throw.fields[1](probe, threads=8).tobytes()


# --- Т8.9-12: a finite fault ---

RF = 12.0


@pytest.fixture(scope="module")
def finite() -> tuple[FaultsResult, Any]:
    """The data of the fault only where y ≤ 20 and −20 ≤ z ≤ 20 (its contacts), the surface x = 0 stays a plane (the ±d
    points are everywhere): R_F = 12 m reaches around those contacts only."""
    pts = throw_points(radius_patch=True)
    res = build_with_faults(throw_spec(RF), pts, box_solid(BOX), threads=4)
    contacts = pts.of_group(2)
    return res, contacts.xyz[contacts.f == 0.0]


def far_points(contacts: np.ndarray, n: int, seed: int) -> np.ndarray:
    from scipy.spatial import cKDTree

    rng = np.random.default_rng(seed)
    p = rng.uniform([-55, 1, -38], [55, 59, 38], size=(n * 4, 3))
    d = cKDTree(contacts).query(p)[0]
    return p[d > RF + 1e-6][:n]


def test_finite_fault_t8912_beyond_rf_both_wings_are_the_undivided_field_bit_for_bit(
    finite: tuple[FaultsResult, Any],
) -> None:
    res, contacts = finite
    far = far_points(contacts, 400, 5)
    assert len(far) == 400
    f0 = next(f for k, f in res.wing_fits.items() if k[1:] == (0, (-1,))).field
    want = f0(far).tobytes()
    wings = res.fields[1].wings
    assert set(wings) == {(0,), (1,)}
    assert wings[(0,)](far).tobytes() == want and wings[(1,)](far).tobytes() == want  # g_1 = g_2 = f_0
    from scipy.spatial import cKDTree

    on = np.column_stack([np.zeros(len(far)), far[:, 1], far[:, 2]])  # on the plane of the fault
    on = on[cKDTree(contacts).query(on)[0] > RF + 1e-6]
    assert len(on) > 50
    a = res.fields[1](on, force={0: 0})
    b = res.fields[1](on, force={0: 1})
    assert a.tobytes() == b.tobytes() == f0(on).tobytes()  # the offset beyond R_F is 0 (1e-9: here exactly)
    assert np.abs(a - b).max() <= 1e-9
    # the control: the data are not the same field — the undivided fit differs from the divided one near the fault
    layer_data = throw_points().of_group(1)
    assert np.abs(res.fields[1](layer_data.xyz) - layer_data.f).max() <= 1e-6  # g_k and f_0 both interpolate the data
    assert np.abs(f0(layer_data.xyz) - layer_data.f).max() <= 1e-6
    near = np.array([[-3.0, 5.0, -4.0], [3.0, 5.0, -4.0]])
    assert np.abs(res.fields[1](near) - f0(near)).max() > 0.1  # near the fault the wing fields are not f_0


def test_finite_fault_the_break_dies_out_and_the_wings_do_not_overlap(finite: tuple[FaultsResult, Any]) -> None:
    res, contacts = finite
    offset = res.stats["by_group"][0]["offset"]
    assert 8.0 <= offset["max"] <= 15.0  # next to the contacts of the fault the break (an estimate where 0 < w < 1)
    assert 0.0 < offset["mean"] < offset["max"]  # and none far from them
    rock = bodies(res)
    total = sum(o.volume for o in rock)
    assert total == pytest.approx(VOLUME, rel=1e-9) and res.stats["balance"] <= 1e-9
    for group in (rock, regions(res)):
        for o in group:
            assert_solid(o.vertices, o.triangles)
        for i, x in enumerate(group):
            for y in group[i + 1 :]:
                assert overlap(x, y) <= 1e-9 * VOLUME
    left, right = (o for o in res.outputs if o.kind == "surface" and o.group == 1)
    # far from the data of the fault the two sheets are one surface: the vertices at the fault edge, y > 40
    edge_l = left.vertices[(left.vertices[:, 0] > -1.0) & (left.vertices[:, 1] > 45.0)]
    edge_r = right.vertices[(right.vertices[:, 0] < 1.0) & (right.vertices[:, 1] > 45.0)]
    assert len(edge_l) and len(edge_r)
    f0 = next(f for k, f in res.wing_fits.items() if k[1:] == (0, (-1,))).field
    assert np.abs(f0(edge_l)).max() <= 0.25 * H and np.abs(f0(edge_r)).max() <= 0.25 * H


# --- several faults, several groups ---


def two_faults_points(x1: float, x2: float) -> md.Points:
    """Faults x = x1 (group 3) and x = x2 (group 4); the layer: z = 0 for x < x1, −10 between them, −25 for x > x2."""
    xs = (np.arange(-55.0, x1 - 4.0, 10.0), np.arange(x1 + 5.0, x2 - 4.0, 10.0), np.arange(x2 + 5.0, 56.0, 10.0))
    parts = [layer(1, x, z) for x, z in zip(xs, (0.0, -10.0, -25.0), strict=True)]
    return md.assemble([*parts, plane_x(3, x1, name="F1"), plane_x(4, x2, name="F2")])


def assert_closed(v: np.ndarray, t: np.ndarray) -> None:
    """Closed, manifold, outward — without the test of self-intersections, whose tolerance (1e-9 of the edge) reads
    the rounding along a crease of a plane that does not go through the nodes of the grid as a crossing."""
    assert meshes.edge_summary(t, len(v)) == (0, 0) and checks.orientation_conflicts(t, len(v)) == 0
    assert solids.signed_volume(v - solids.local_origin(v), t) > 0.0


@pytest.mark.parametrize("x1, x2", [(-21.0, 21.0), (-20.0, 20.0)])  # on the nodes of the grid (h = 3) and between them
def test_two_faults_the_wings_are_the_combinations_of_the_signs(x1: float, x2: float) -> None:
    spec = model([cut(1, "Пласт", 3, 4), fault_group(3, "F1"), fault_group(4, "F2")], BOX, h=H)
    res = build_with_faults(spec, two_faults_points(x1, x2), box_solid(BOX), threads=2)
    walls = {o.wing: o for o in res.outputs if o.group == 1 and o.kind == "surface"}
    assert sorted(walls) == [0, 1, 3]  # code 2 (f_1 < 0 and f_2 ≥ 0) is an empty region: skipped
    assert any("крыло 2" in line and "пусто" in line for line in res.journal)
    for code, z in ((0, 0.0), (1, -10.0), (3, -25.0)):
        assert np.abs(walls[code].vertices[:, 2] - z).max() <= 0.25 * H
    rock = bodies(res)
    assert sum(o.volume for o in rock) == pytest.approx(VOLUME, rel=1e-9) and res.stats["balance"] <= 1e-9
    assert [o.name for o in rock if o.group is None] == ["Прочее, крыло 0", "Прочее, крыло 1", "Прочее, крыло 3"]
    assert [g.get("wings") for g in res.stats["by_group"]] == [[0, 1, 3]]
    offset = res.stats["by_group"][0]["offset"]
    per = res.stats["by_group"][0]["offset_by_fault"]
    assert per[3]["mean"] == pytest.approx(10.0, abs=1e-5) and per[4]["mean"] == pytest.approx(15.0, abs=1e-5)
    assert offset["max"] == pytest.approx(15.0, abs=1e-5) and 10.0 < offset["mean"] < 15.0
    for i, x in enumerate(rock):
        for y in rock[i + 1 :]:
            assert overlap(x, y) <= 1e-9 * VOLUME
    for o in rock:
        (assert_solid if x1 == -21.0 else assert_closed)(o.vertices, o.triangles)
    assert sorted(res.stats["faults"]) == [3, 4]


def test_a_fault_cuts_one_group_of_two_and_the_other_stays_whole() -> None:
    """The younger «Верх» (z > 20) is not cut, the older layer is: the body of «Верх» meets both regions, Σ the volumes
    still = the boundary, no overlap."""
    top = layer(1, np.arange(-55.0, 56.0, 10.0), 20.0, "Верх", above=True)
    spec = model([domain(1, "Верх"), cut(2, "Пласт", 3), fault_group(3, "Сброс")], BOX, h=H)
    pts = md.assemble([top, layer(2, LEFT, 0.0), layer(2, RIGHT, -10.0), plane_x(3, 0.0, name="Сброс")])
    res = build_with_faults(spec, pts, box_solid(BOX), threads=2)
    rock = bodies(res)
    assert [o.name for o in rock] == ["Верх", "Пласт, крыло 0", "Пласт, крыло 1", "Прочее, крыло 0", "Прочее, крыло 1"]
    assert [o.wing for o in rock] == [None, 0, 1, 0, 1]
    assert [o.volume for o in rock] == [pytest.approx(v, rel=1e-6) for v in
                                       (120.0 * 60.0 * 20.0, 3600.0 * 40.0, 3600.0 * 30.0, 3600.0 * 20.0,
                                        3600.0 * 30.0)]  # fmt: skip
    assert sum(o.volume for o in rock) == pytest.approx(VOLUME, rel=1e-9) and res.stats["balance"] <= 1e-9
    for i, x in enumerate(rock):
        for y in rock[i + 1 :]:
            assert overlap(x, y) <= 1e-9 * VOLUME
    assert [g["offset"] is None for g in res.stats["by_group"]] == [True, False]


# --- the model without a cut group, the stack, the errors, the cancel ---


def test_a_model_without_a_cut_group_is_build_stack_bit_for_bit() -> None:
    pts = md.assemble([layer(1, np.arange(-55.0, 56.0, 10.0), -5.0), plane_x(2, 0.0)])
    spec = model([domain(1, "Пласт"), fault_group(2, "Сброс")], BOX, h=H)  # the fault cuts nobody
    a = build_with_faults(spec, pts, box_solid(BOX), threads=2)
    b = build_stack(spec, pts, box_solid(BOX), threads=2)
    assert [(o.kind, o.group, o.name) for o in a.outputs] == [(o.kind, o.group, o.name) for o in b.outputs]
    for x, y in zip(a.outputs, b.outputs, strict=True):
        assert x.vertices.tobytes() == y.vertices.tobytes() and x.volume == y.volume
    assert a.faults == {} and a.fields == {}


def test_erosion_and_a_cut_group_together() -> None:
    """The younger flat «Срез» (z > 10, an unfaulted domain) erodes the older layer that the fault cuts: the data above
    the cut are dropped by the value of the younger field before the older group is divided by the fault."""
    cutter = layer(1, np.arange(-55.0, 56.0, 10.0), 10.0, "Срез", above=True)
    old = md.assemble([layer(2, LEFT, -5.0, "Пласт"), layer(2, RIGHT, -15.0, "Пласт")])
    above = layer(2, np.array([-25.0, 25.0]), 30.0, "Пласт")  # contacts above the cut: they cannot be true
    spec = model([cut(1, "Срез", relation="erode"), cut(2, "Пласт", 3), fault_group(3, "Сброс")], BOX, h=H)
    pts = md.assemble([cutter, old, above, plane_x(3, 0.0)])
    res = build_with_faults(spec, pts, box_solid(BOX), threads=2)
    assert res.stats["stack"]["dropped_total"] > 0
    assert res.stats["stack"]["order"] == [1, 2]
    rock = bodies(res)
    assert sum(o.volume for o in rock) == pytest.approx(VOLUME, rel=1e-9)
    clean = md.assemble([cutter, old, plane_x(3, 0.0)])
    ref = build_with_faults(spec, clean, box_solid(BOX), threads=2)
    probe = np.array([[-20.0, 20.0, -8.0], [20.0, 20.0, -20.0]])
    assert res.fields[2](probe).tobytes() == ref.fields[2](probe).tobytes()


def test_a_wing_without_contacts_is_no_contacts_with_a_hint() -> None:
    """The fault runs beside the contacts: the right wing of the layer has only points above it (no change of sign)."""
    right_only_above = layer(1, RIGHT, -10.0)
    keep = right_only_above.f >= 0.0
    pts = md.assemble([layer(1, LEFT, 0.0), right_only_above.take(keep), plane_x(2, 0.0)])
    with pytest.raises(ApiError) as err:
        build_with_faults(throw_spec(), pts, box_solid(BOX))
    e = err.value
    assert e.status == 422 and e.code == "no_contacts" and e.hint
    assert "«Пласт»" in e.message and "«Сброс»" in e.message and "f ≥ 0" in e.message
    assert e.extra["group"] == 1 and e.extra["wing"] == 1 and e.extra["faults"] == [2]


def test_a_fault_without_contacts_a_fault_outside_the_boundary_and_a_missing_fault() -> None:
    layers = [layer(1, LEFT, 0.0), layer(1, RIGHT, -10.0)]
    sides = plane_x(2, 0.0, contacts=False)
    no_contacts = md.assemble([*layers, sides.take(np.flatnonzero(sides.f > 0.0))])  # only the outside of the fault
    with pytest.raises(ApiError) as err:
        build_with_faults(throw_spec(), no_contacts, box_solid(BOX))
    assert err.value.code == "no_contacts" and "разлома «Сброс»" in err.value.message and err.value.extra["fault"] == 2
    outside = md.assemble([*layers, plane_x(2, 100.0)])  # the surface x = 100: the whole box is wing 0
    with pytest.raises(ApiError) as err2:
        build_with_faults(throw_spec(), outside, box_solid(BOX))
    assert err2.value.code == "fault_outside" and err2.value.status == 422 and err2.value.hint
    assert "«Сброс»" in err2.value.message and err2.value.extra["fault"] == 2
    with pytest.raises(ApiError):  # a fault the model does not have: refused by the model itself
        model([cut(1, "Пласт", 9), fault_group(2, "Сброс")], BOX, h=H)


def test_cancel_leaves_nothing() -> None:
    seen: list[tuple[float, str]] = []
    res = build_with_faults(throw_spec(), throw_points(), box_solid(BOX), tick=lambda f, m: seen.append((f, m)))
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0 and len(seen) > 20
    assert len(res.outputs) == 9
    for stop in (1, len(seen) // 4, len(seen) // 2, 3 * len(seen) // 4, len(seen) - 1):
        count = [0]

        def tick(_f: float, _m: str, stop: int = stop, count: list[int] = count) -> None:
            count[0] += 1
            if count[0] == stop:
                raise Cancelled

        with pytest.raises(Cancelled):
            build_with_faults(throw_spec(), throw_points(), box_solid(BOX), tick=tick)


def test_module_does_not_load_numba_or_manifold() -> None:
    code = ("import bf_core.model.faults, bf_core.model.faults_parts, sys; "
            "bad = [m for m in ('numba', 'manifold3d', 'pyfqmr') if m in sys.modules]; "
            "raise SystemExit(3 if bad else 0)")  # fmt: skip
    run = subprocess.run([sys.executable, "-c", code], cwd=CORE, capture_output=True, text=True, timeout=120,
                         env={"PYTHONPATH": str(CORE), "BF_NO_WARMUP": "1", "PATH": "/usr/bin:/bin"})  # fmt: skip
    assert run.returncode == 0, run.stderr[-400:]
