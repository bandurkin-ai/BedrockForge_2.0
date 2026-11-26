"""«Интрузия» in the stack of relations (подшаг 19.34; ТЗ Д-31; ADR 0026 решение 19): the points of every older group
INSIDE {f_I < 0} (by the value of the field of I at the point) are dropped, the body of I is subtracted from the older
bodies and is cut by the younger ones.

ТД31-1 two horizontal layers and a sphere R = 30 m on their contact: the volumes of the layers are the original ones
minus the parts of the sphere (±1 %), Σ the volumes = the boundary (1e-9); ТД31-2 the points of the older layers inside
the sphere do not change their fields outside it (same bits; with «Согласно» they do); ТД31-3 a younger layer above,
«Эрозия»: the top of the intrusion is cut (the plane, the volume of the spherical segment); ТД31-4 the same bytes with
1, 2, 4, 8 threads. Also: the intrusion through four older groups of real holes, the order of the solution, a younger
«Согласно» cover cuts the intrusion, the cycle → `bad_relations`, the journal of the dropped points, the cancel."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.model import data as md
from bf_core.model.bodies import BodiesResult
from bf_core.model.bodies_parts import box_solid
from bf_core.model.stack import build_stack, domains_of, order_of, precedence
from tests.test_model_bodies import (
    SCENE_BOX,
    Cancelled,
    assert_solid,
    bodies_of,
    domain,
    model,
    overlap,
    same,
    scene_points,
    scene_spec,
    sphere_points,
)
from tests.test_model_stack import column, grid, points

H = 3.0
R = 30.0
BOX = [-60.0, -60.0, -40.0, 60.0, 60.0, 40.0]
BOX_VOLUME = 120.0 * 120.0 * 80.0
SPHERE = 4.0 / 3.0 * math.pi * R**3
ORIGIN = np.zeros(3)


# --- the scene: two layers (z > 0 and z < 0) and a sphere of R = 30 m on their contact ---
# The layers are the half-spaces of the box (f = −z for the upper "Верхний", f = z for the lower "Нижний"): the linear
# field reproduces them exactly, so the only thing that can bend a layer is a data point that does not belong to it.

GX, GY = grid(-60.0, 13, 13, 10.0)
OUT = np.hypot(GX, GY) > R + 3.0  # the columns of data that do not touch the sphere (the observed data of the layers)


def layer_points(gid: int, name: str, sign: float, depths: tuple[float, ...] = (15.0, 30.0)) -> md.Points:
    """The layer on the side `sign` of the plane z = 0 (+1 — above): the contact on z = 0 and the points at ±15, ±30
    outside of the sphere (the columns that hit it are not observed: the sphere is there); `depths` — the distances
    from the plane of the points of the sides (under a younger cut they must stay below it)."""
    x, y = GX[OUT], GY[OUT]
    zero = np.zeros_like(x)
    parts = [("contact", column(x, y, zero), zero)]
    for d in depths:
        parts.append(("inside", column(x, y, np.full_like(x, sign * d)), np.full_like(x, -d)))
        parts.append(("outside", column(x, y, np.full_like(x, -sign * d)), np.full_like(x, d)))
    return points(gid, name, parts)


def spurious(gid: int, name: str, sign: float) -> md.Points:
    """Data of the layer that cannot be true inside the sphere: contacts, "inside" and "outside" points at random
    places within 14 m of the centre (f of the sphere is below −10 there)."""
    rng = np.random.default_rng(31 + gid)
    xyz = rng.uniform(-8.0, 8.0, size=(60, 3))
    xyz[:, 2] = np.abs(xyz[:, 2]) * sign
    wrong = np.zeros(60)
    wrong[20:40] = -6.0
    wrong[40:] = 9.0
    kinds = ["contact"] * 20 + ["inside"] * 20 + ["outside"] * 20
    parts = [(k, xyz[i : i + 1], wrong[i : i + 1]) for i, k in enumerate(kinds)]
    return points(gid, name, parts)


def sphere_data(gid: int = 1, name: str = "Интрузия") -> md.Points:
    return sphere_points(gid, ORIGIN, R, contacts=1500, shells=400, name=name)


def spec_of(groups: list[dict[str, Any]]) -> Any:
    return model(groups, BOX, h=H)


def intrusion_spec() -> Any:
    return spec_of([{**domain(1, "Интрузия"), "relation": "intrusion"}, domain(2, "Верхний"), domain(3, "Нижний")])


def scene(extra: bool = False) -> md.Points:
    up, low = layer_points(2, "Верхний", +1.0), layer_points(3, "Нижний", -1.0)
    if extra:
        up, low = md.assemble([up, spurious(2, "Верхний", +1.0)]), md.assemble([low, spurious(3, "Нижний", -1.0)])
    return md.assemble([sphere_data(), up, low])


def probe_out(n: int = 600) -> np.ndarray:
    """Points of the box outside the sphere (r > 35): the fields of the layers are compared there."""
    rng = np.random.default_rng(9)
    xyz = rng.uniform([-55.0, -55.0, -38.0], [55.0, 55.0, 38.0], size=(4 * n, 3))
    return xyz[np.linalg.norm(xyz, axis=1) > R + 5.0][:n]


@pytest.fixture(scope="module")
def intr() -> BodiesResult:
    return build_stack(intrusion_spec(), scene(), box_solid(BOX), threads=4)


# --- ТД31-1: the layers are the original ones minus the sphere ---


def test_td31_1_layers_are_the_originals_minus_the_sphere(intr: BodiesResult) -> None:
    res = intr
    assert [(o.kind, o.name) for o in res.outputs if o.group is not None] == [
        ("surface", "Интрузия: контакт"), ("body", "Интрузия"), ("surface", "Верхний: контакт"), ("body", "Верхний"),
        ("surface", "Нижний: контакт"), ("body", "Нижний")]  # fmt: skip
    body, upper, lower = (o for o in bodies_of(res) if o.group is not None)
    half = BOX_VOLUME / 2.0
    assert body.volume == pytest.approx(SPHERE, rel=0.01)  # the sphere is whole: nothing younger, nothing cut
    assert upper.volume == pytest.approx(half - SPHERE / 2.0, rel=0.01)  # ТД31-1: the original minus the half sphere
    assert lower.volume == pytest.approx(half - SPHERE / 2.0, rel=0.01)
    assert upper.vertices[:, 2].min() >= -1e-6 * H and lower.vertices[:, 2].max() <= 1e-6 * H  # the plane z = 0
    inner = np.linalg.norm(np.concatenate([upper.vertices, lower.vertices]), axis=1)
    assert inner.min() >= R - 0.25 * H  # nothing of the layers inside the sphere
    assert np.abs(np.linalg.norm(body.vertices, axis=1) - R).max() <= 0.25 * H  # the sphere to 0,25·h (Т8.9-1)
    bodies = bodies_of(res)
    assert sum(o.volume for o in bodies) == pytest.approx(BOX_VOLUME, rel=1e-9)  # Σ the volumes = the boundary
    assert res.stats["balance"] <= 1e-9
    rest = [o for o in bodies if o.group is None]
    assert not rest or rest[0].volume <= 1e-9 * BOX_VOLUME
    for x in (body, upper, lower):
        assert_solid(x.vertices, x.triangles)
    for x, y in ((body, upper), (body, lower), (upper, lower)):
        assert overlap(x, y) <= 1e-9 * BOX_VOLUME
    info = res.stats["stack"]
    assert info["order"] == [1, 2, 3] and info["relations"] == {1: "intrusion", 2: "conformable", 3: "conformable"}
    assert info["dropped_total"] == 0  # the observed data lie outside the sphere: nothing to drop
    assert res.journal[0].startswith("Стек отношений: порядок расчёта — «Интрузия», «Верхний», «Нижний»")


# --- ТД31-2: the points of the older layers inside the sphere change nothing outside it ---


@pytest.fixture(scope="module")
def dirty() -> BodiesResult:
    return build_stack(intrusion_spec(), scene(extra=True), box_solid(BOX), threads=4)


def test_td31_2_points_of_the_older_layers_inside_the_sphere_are_dropped(
    intr: BodiesResult, dirty: BodiesResult
) -> None:
    info = dirty.stats["stack"]
    extra = spurious(2, "Верхний", 1.0).n
    clean = layer_points(2, "Верхний", 1.0).n
    assert info["dropped_total"] == 2 * extra
    assert [(d["group"], d["by"], d["relation"], d["points"]) for d in info["dropped"]] == [
        (2, 1, "intrusion", extra), (3, 1, "intrusion", extra)]  # fmt: skip
    assert all(d["undefined"] > 0 for d in info["dropped"])  # far columns: no patch of the sphere reaches them, kept
    assert info["dropped"][0]["of"] == clean + extra
    assert dirty.fits[2].stats["points"] == clean and dirty.fits[3].stats["points"] == clean
    assert any("интрузия группы «Интрузия» — отброшено точек 60 из" in line and "внутри тела группы" in line
               for line in dirty.journal)  # fmt: skip
    probe = probe_out()
    assert len(probe) == 600
    for gid in (2, 3):  # the same bits outside the sphere (ТД31-2 asks 1e-9)
        assert dirty.fits[gid].field(probe).tobytes() == intr.fits[gid].field(probe).tobytes()
    same(dirty, intr)


def test_td31_2_control_without_the_intrusion_the_same_points_bend_the_layers(intr: BodiesResult) -> None:
    free = build_stack(spec_of([domain(1, "Интрузия"), domain(2, "Верхний"), domain(3, "Нижний")]),
                       scene(extra=True), box_solid(BOX), threads=2)  # fmt: skip
    assert free.stats["stack"]["dropped_total"] == 0
    probe = probe_out()
    for gid in (2, 3):
        assert np.abs(free.fits[gid].field(probe) - intr.fits[gid].field(probe)).max() > 0.1


def test_the_points_on_the_surface_of_the_intrusion_are_kept() -> None:
    """The contact of a layer ON the sphere is the contact of the layer with the intrusion: f_I ≈ 0, not inside."""
    ring = np.linspace(0.0, 2.0 * np.pi, 24, endpoint=False)
    on = column(R * np.cos(ring), R * np.sin(ring), np.zeros_like(ring))  # the circle where z = 0 meets the sphere
    layers = md.assemble([layer_points(2, "Верхний", 1.0),
                          points(2, "Верхний", [("contact", on, np.zeros(24))]),
                          layer_points(3, "Нижний", -1.0)])  # fmt: skip
    res = build_stack(intrusion_spec(), md.assemble([sphere_data(), layers]), box_solid(BOX), threads=2)
    assert res.stats["stack"]["dropped_total"] == 0
    assert res.fits[2].stats["points"] == layer_points(2, "Верхний", 1.0).n + 24


# --- ТД31-3: a younger layer above, «Эрозия»: the top of the intrusion is cut ---

CUT_Z = 15.0


def cap_points() -> md.Points:
    """The younger group "Кровля": the plane z = 15 (f = 15 − z, inside above), data at z = 15, 25, 35 and 5, −10."""
    x, y = grid(-60.0, 13, 13, 10.0)
    parts = [("contact", column(x, y, np.full_like(x, CUT_Z)), np.zeros_like(x))]
    for z in (25.0, 35.0):
        parts.append(("inside", column(x, y, np.full_like(x, z)), np.full_like(x, CUT_Z - z)))
    for z in (5.0, -10.0):
        parts.append(("outside", column(x, y, np.full_like(x, z)), np.full_like(x, CUT_Z - z)))
    return points(1, "Кровля", parts)


def cap_spec(relation: str) -> Any:
    return spec_of([{**domain(1, "Кровля"), "relation": relation}, {**domain(2, "Интрузия"), "relation": "intrusion"},
                    domain(3, "Верхний"), domain(4, "Нижний")])  # fmt: skip


def cap_scene() -> md.Points:
    return md.assemble(
        [
            cap_points(),
            sphere_data(2),
            layer_points(3, "Верхний", 1.0, (6.0, 12.0)),
            layer_points(4, "Нижний", -1.0, (6.0, 12.0)),
        ]
    )


SEGMENT = math.pi * (R - CUT_Z) ** 2 * (3.0 * R - (R - CUT_Z)) / 3.0  # the part of the sphere above z = 15


@pytest.fixture(scope="module")
def eroded() -> BodiesResult:
    return build_stack(cap_spec("erode"), cap_scene(), box_solid(BOX), threads=4)


def test_td31_3_the_younger_erosion_cuts_the_top_of_the_intrusion(eroded: BodiesResult) -> None:
    res = eroded
    cap, body, upper, lower = (o for o in bodies_of(res) if o.group is not None)
    assert [o.name for o in (cap, body, upper, lower)] == ["Кровля", "Интрузия", "Верхний", "Нижний"]
    assert res.stats["stack"]["order"] == [1, 2, 3, 4]
    assert cap.volume == pytest.approx(120.0 * 120.0 * (40.0 - CUT_Z), rel=1e-9)  # the younger wins the half-space
    assert body.vertices[:, 2].max() <= CUT_Z + 1e-6 * H  # the top of the intrusion is cut by the plane z = 15
    assert body.vertices[:, 2].max() >= CUT_Z - 1e-6 * H
    assert body.volume == pytest.approx(SPHERE - SEGMENT, rel=0.015)
    assert np.linalg.norm(body.vertices, axis=1).max() <= R + 0.5 * H
    # the host rock: the plane z = 15 above, the sphere cut out, the whole boundary is covered
    assert upper.volume == pytest.approx(120.0 * 120.0 * CUT_Z - (SPHERE / 2.0 - SEGMENT), rel=0.01)
    assert lower.volume == pytest.approx(BOX_VOLUME / 2.0 - SPHERE / 2.0, rel=0.01)
    assert sum(o.volume for o in bodies_of(res)) == pytest.approx(BOX_VOLUME, rel=1e-9)
    assert res.stats["balance"] <= 1e-9
    for x in (cap, body, upper, lower):
        assert_solid(x.vertices, x.triangles)
    for x, y in ((cap, body), (body, upper), (body, lower), (cap, upper)):
        assert overlap(x, y) <= 1e-9 * BOX_VOLUME
    by = {(d["group"], d["by"], d["relation"]) for d in res.stats["stack"]["dropped"]}
    assert (2, 1, "erode") in by  # the sphere data above the cut are dropped by the younger group
    assert not any(g == 3 and b == 1 for g, b, _ in by)  # the layers have no data above the cut
    assert all(d["by"] != 2 or d["relation"] == "intrusion" for d in res.stats["stack"]["dropped"])


def test_a_younger_conformable_cover_cuts_the_intrusion_with_no_data_dropped() -> None:
    """The younger body wins the overlap (the chronology): the sphere is whole in its field, its top is subtracted."""
    res = build_stack(cap_spec("conformable"), cap_scene(), box_solid(BOX), threads=2)
    cap, body, upper, lower = (o for o in bodies_of(res) if o.group is not None)
    assert body.vertices[:, 2].max() <= CUT_Z + 1e-6 * H
    assert body.volume == pytest.approx(SPHERE - SEGMENT, rel=0.01)
    assert res.stats["stack"]["dropped_total"] == 0  # the layers' data lie outside the sphere, nothing is eroded
    assert sum(o.volume for o in bodies_of(res)) == pytest.approx(BOX_VOLUME, rel=1e-9)
    assert res.fits[2].stats["points"] == sphere_data(2).n  # the whole sphere is in the solution


# --- ТД31-4: the same bytes with 1, 2, 4, 8 threads and any order of the holes ---


def test_td31_4_the_same_bytes_with_1_2_4_8_threads(
    intr: BodiesResult, eroded: BodiesResult, dirty: BodiesResult
) -> None:
    for threads in (1, 2, 8):
        same(intr, build_stack(intrusion_spec(), scene(), box_solid(BOX), threads=threads))
        same(eroded, build_stack(cap_spec("erode"), cap_scene(), box_solid(BOX), threads=threads))
    same(dirty, intr)  # the dropped points leave the bytes of the clean data (threads 4 both)
    for threads in (1, 8):
        again = build_stack(intrusion_spec(), scene(extra=True), box_solid(BOX), threads=threads)
        same(dirty, again)
        assert again.stats["stack"] == dirty.stats["stack"]


# --- the intrusion through four older groups of real holes ---


def test_the_intrusion_of_the_holes_replaces_four_older_groups_and_is_the_same_bytes() -> None:
    """Group 1 "INT" of the fixture scene is the intrusion: ALV, SS, SH, GR (older) drop the points inside it, the
    bodies add up to the boundary, the holes in any order and any threads give the same bytes."""
    pts = scene_points()
    base = build_stack(scene_spec(), pts, box_solid(SCENE_BOX), relations={1: "intrusion"}, threads=1)
    free = build_stack(scene_spec(), pts, box_solid(SCENE_BOX), threads=1)
    info = base.stats["stack"]
    assert info["order"] == [1, 2, 3, 4, 5] and info["relations"][1] == "intrusion"
    assert {d["by"] for d in info["dropped"]} == {1} and info["dropped"] and info["dropped_total"] > 0
    assert {d["group"] for d in info["dropped"]} <= {2, 3, 4, 5}
    assert free.stats["stack"]["dropped_total"] == 0
    boundary = 420.0 * 420.0 * 210.0
    bodies = bodies_of(base)
    assert sum(o.volume for o in bodies) == pytest.approx(boundary, rel=1e-9) and base.stats["balance"] <= 1e-9
    assert [o.group for o in bodies][:5] == [1, 2, 3, 4, 5]
    for i, x in enumerate(bodies):
        assert_solid(x.vertices, x.triangles)
        for y in bodies[i + 1 :]:
            assert overlap(x, y) <= 1e-9 * boundary
    shuffled = scene_points(order=np.random.default_rng(11).permutation(100))
    same(base, build_stack(scene_spec(), shuffled, box_solid(SCENE_BOX), relations={1: "intrusion"}, threads=8))


# --- the graph of the relations ---


def test_the_intrusion_is_in_front_of_the_older_groups_and_behind_the_winning_younger_ones() -> None:
    names = ["a", "b", "c", "d"]

    def groups(*rels: str) -> Any:
        spec = model([{**domain(i + 1, names[i]), "relation": r} for i, r in enumerate(rels)], BOX, h=H)
        return domains_of(spec)

    assert sorted(precedence(groups("intrusion", "conformable", "conformable"))) == [(0, 1), (0, 2), (1, 2)]
    for rels, want in ((("intrusion", "conformable", "conformable", "conformable"), [0, 1, 2, 3]),
                       (("conformable", "intrusion", "conformable", "conformable"), [0, 1, 2, 3]),  # cut by the younger
                       (("onlap", "intrusion", "conformable", "conformable"), [1, 2, 3, 0]),  # the onlap is on it
                       (("erode", "intrusion", "erode", "conformable"), [0, 1, 2, 3]),
                       (("intrusion", "onlap", "conformable", "conformable"), [0, 2, 3, 1]),  # the older onlap: after
                       (("intrusion",) * 3 + ("conformable",), [0, 1, 2, 3])):  # fmt: skip
        g = groups(*rels)
        assert order_of(len(g), precedence(g)) == want, rels


def test_a_cycle_through_the_intrusion_is_refused_in_russian() -> None:
    g = domains_of(model([{**domain(1, "Шток"), "relation": "intrusion"}, domain(2, "Сланец"), domain(3, "Гранит")],
                         BOX, h=H))  # fmt: skip
    with pytest.raises(ApiError) as err:
        order_of(len(g), [*precedence(g), (2, 0)], [x.name for x in g])  # an edge of another relation closes a cycle
    e = err.value
    assert e.status == 422 and e.code == "bad_relations" and e.hint and "цикл" in e.message
    assert "«Шток»" in e.message and "«Гранит»" in e.message


def test_the_intrusion_is_built_and_the_oldest_group_changes_nothing() -> None:
    spec = intrusion_spec()
    assert [g.relation for g in domains_of(spec)] == ["intrusion", "conformable", "conformable"]
    pts = md.assemble([layer_points(2, "Верхний", 1.0), layer_points(3, "Нижний", -1.0), sphere_data()])
    free = build_stack(spec, pts, box_solid(BOX), relations={1: "conformable"})
    last = build_stack(spec, pts, box_solid(BOX), relations={1: "conformable", 3: "intrusion"})
    same(free, last)  # nothing is older than the last group
    assert last.stats["stack"]["dropped_total"] == 0


# --- the group whose data all lie inside the intrusion, the cancel ---


def test_a_group_all_of_whose_data_are_inside_the_intrusion_has_no_contacts() -> None:
    inside = spurious(2, "Верхний", 1.0)
    with pytest.raises(ApiError) as err:
        build_stack(intrusion_spec(), md.assemble([sphere_data(), inside, layer_points(3, "Нижний", -1.0)]),
                    box_solid(BOX))  # fmt: skip
    e = err.value
    assert e.code == "no_contacts" and e.status == 422 and e.extra["group"] == 2 and e.extra["dropped"] == inside.n
    assert "после отбрасывания" in e.message and "Верхний" in e.message and e.hint


def test_cancel_leaves_nothing() -> None:
    spec, pts, box = intrusion_spec(), scene(extra=True), box_solid(BOX)
    seen: list[tuple[float, str]] = []
    res = build_stack(spec, pts, box, tick=lambda f, m: seen.append((f, m)))
    assert len([o for o in res.outputs if o.group is not None]) == 6 and len(seen) > 10
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    for stop in (1, len(seen) // 4, len(seen) // 2, len(seen) - 2):
        count = [0]

        def tick(_f: float, _m: str, stop: int = stop, count: list[int] = count) -> None:
            count[0] += 1
            if count[0] == stop:
                raise Cancelled

        with pytest.raises(Cancelled):
            build_stack(spec, pts, box, tick=tick)
