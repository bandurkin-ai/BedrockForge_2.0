"""The stack of relations of the domains (подшаг 19.32; ТЗ Д-24; ADR 0026 решения 15, 19): «Согласно», «Эрозия»,
«Налегание» on analytical scenes of layers.

Т8.9-9 erosion: a layer dipping 15° cut by a horizontal surface; the data of the older layer above the cut (and on it,
and outside the boundary) are dropped by the VALUE of the younger field, so they do not change the older field (the
same bits), the cut is the plane to 1e-6·h and the older body is the older minus the younger.
Т8.9-10 onlap: a younger layer over an uneven older surface; its data below that surface are dropped, its lower face
IS the older surface (≤ 0,25·h), the older body wins the overlap.
Also: Σ the volumes = the boundary (1e-9); the same bytes with 1, 2, 4, 8 threads and any order of the holes; cancel;
the errors in Russian {code, message, hint}; «Согласно» = `build_bodies` bit by bit; the order of the solution; the
module is light."""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.model import data as md
from bf_core.model.bodies import BodiesResult, build_bodies
from bf_core.model.bodies_parts import box_solid
from bf_core.model.stack import build_stack, domains_of, order_of, precedence
from tests.test_model_bodies import (
    KIND,
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
)

CORE = Path(__file__).resolve().parents[1]
H = 3.0
TAN15 = math.tan(math.radians(15.0))


# --- points of analytical layers ---


def points(gid: int, name: str, parts: list[tuple[str, np.ndarray, np.ndarray]]) -> md.Points:
    """[(kind, xyz [n, 3], f [n])] → the points of group `gid` in the order they are given."""
    xyz = np.concatenate([p[1] for p in parts]).astype(np.float64)
    f = np.concatenate([p[2] for p in parts]).astype(np.float64)
    kind = np.concatenate([np.full(len(p[2]), KIND[p[0]]) for p in parts]).astype(np.int8)
    n = len(f)
    return md.Points(xyz, f, kind, np.full(n, gid, np.int32), np.full(n, -1, np.int8), np.zeros(n, np.int32),
                     np.full((n, 2), np.nan), np.full(n, -1, np.int64), [name], {"points": n}, [])  # fmt: skip


def column(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    return np.column_stack([x, y, z])


def grid(dx: float, nx: int, ny: int, step: float) -> tuple[np.ndarray, np.ndarray]:
    gx, gy = np.meshgrid(dx + step * np.arange(nx), step * np.arange(ny), indexing="ij")
    return gx.ravel(), gy.ravel()


# --- the scene of the erosion (Т8.9-9) ---
# Box x 0…120, y 0…60, z −60…60. The cut is the plane z = 0 (the younger "Срез": everything above). The older layer
# "Пласт" dips 15° (rises to +x): its top z_t = tan 15°·x − 16, 24 m thick, so the cut takes its top where z_t > 0.

BOX_E = [0.0, 0.0, -60.0, 120.0, 60.0, 60.0]
GX, GY = grid(0.0, 13, 7, 10.0)


def zt_e(x: np.ndarray) -> np.ndarray:
    return TAN15 * x - 16.0


def cut_points() -> md.Points:
    """The younger group: the plane z = 0, f = −z (the inside is above); the linear field reproduces it."""
    parts = [("contact", column(GX, GY, np.zeros_like(GX)), np.zeros_like(GX))]
    for z in (20.0, 40.0):
        parts.append(("inside", column(GX, GY, np.full_like(GX, z)), np.full_like(GX, -z)))
        parts.append(("outside", column(GX, GY, np.full_like(GX, -z)), np.full_like(GX, z)))
    return points(1, "Срез", parts)


def layer_points_e() -> md.Points:
    """The older layer where it exists: below the cut. Its top contacts only where z_t < 0."""
    top = zt_e(GX)
    low = top < -2.0
    mid = top - 12.0
    keep_mid = mid < -2.0
    out_up = top + 12.0
    keep_up = out_up < -2.0
    bottom = top - 24.0
    parts = [
        ("contact", column(GX[low], GY[low], top[low]), np.zeros(int(low.sum()))),
        ("contact", column(GX, GY, bottom), np.zeros_like(GX)),
        ("inside", column(GX[keep_mid], GY[keep_mid], mid[keep_mid]), np.full(int(keep_mid.sum()), -12.0)),
        ("outside", column(GX[keep_up], GY[keep_up], out_up[keep_up]), np.full(int(keep_up.sum()), 12.0)),
        ("outside", column(GX, GY, bottom - 12.0), np.full_like(GX, 12.0)),
    ]
    return points(2, "Пласт", parts)


def spurious_e(outside_box: bool) -> md.Points:
    """Data of the older layer that cannot be true above the cut: the contacts of the cut itself (z = 0, f = 0), points
    over the top of the layer with the value of its inside, and, if asked, points outside the boundary where f < 0 of
    the younger field is still negative: a mask by the wireframe would keep them, the mask by the value drops them."""
    sel = GX >= 70.0
    over = zt_e(GX[sel]) + 6.0
    parts = [
        ("contact", column(GX[sel], GY[sel], np.zeros(int(sel.sum()))), np.zeros(int(sel.sum()))),
        ("inside", column(GX[sel], GY[sel], over), np.full(int(sel.sum()), -6.0)),
    ]
    if outside_box:
        far = np.array([[150.0, 30.0, 20.0], [160.0, 10.0, 35.0], [-30.0, 40.0, 25.0]])
        parts.append(("inside", far, np.full(3, -5.0)))
    return points(2, "Пласт", parts)


def with_extra(base: md.Points, extra: md.Points) -> md.Points:
    return md.assemble([base, extra])


def erosion_spec(relation: str = "erode", order: tuple[int, ...] = (1, 2)) -> Any:
    names = {1: "Срез", 2: "Пласт"}
    groups = [{**domain(g, names[g]), **({"relation": relation} if g == 1 else {})} for g in order]
    return model(groups, BOX_E, h=H)


def probe_below(n: int = 400) -> np.ndarray:
    rng = np.random.default_rng(5)
    xyz = rng.uniform([5.0, 5.0, -45.0], [115.0, 55.0, -3.0], size=(n, 3))
    return xyz


@pytest.fixture(scope="module")
def erosion() -> BodiesResult:
    pts = md.assemble([cut_points(), layer_points_e()])
    return build_stack(erosion_spec(), pts, box_solid(BOX_E), threads=4)


def analytic_layer_volume() -> float:
    xs = np.linspace(0.0, 120.0, 120_001)
    top = np.minimum(zt_e(xs), 0.0)
    thick = np.maximum(top - (zt_e(xs) - 24.0), 0.0)
    return float(np.trapezoid(thick, xs)) * 60.0


def test_erosion_t899_the_cut_is_a_plane_and_the_layer_is_cut_by_it(erosion: BodiesResult) -> None:
    res = erosion
    assert [(o.kind, o.name) for o in res.outputs] == [("surface", "Срез: контакт"), ("body", "Срез"),
                                                       ("surface", "Пласт: контакт"), ("body", "Пласт"),
                                                       ("body", "Прочее")]  # fmt: skip
    cut_sheet = res.outputs[0]
    assert np.abs(cut_sheet.vertices[:, 2]).max() <= 1e-6 * H  # the contact of the cut: the plane z = 0
    cut, layer, rest = bodies_of(res)
    assert cut.volume == pytest.approx(120.0 * 60.0 * 60.0, rel=1e-9)  # the younger wins the whole half-space
    assert layer.vertices[:, 2].max() <= 1e-6 * H  # nothing of the layer above the cut
    assert layer.volume == pytest.approx(analytic_layer_volume(), rel=0.03)
    for o in (cut, layer, rest):
        assert_solid(o.vertices, o.triangles)
    total = 120.0 * 60.0 * 120.0
    assert sum(o.volume for o in bodies_of(res)) == pytest.approx(total, rel=1e-9)  # Σ the volumes = the boundary
    assert res.stats["balance"] <= 1e-9
    for x, y in ((cut, layer), (cut, rest), (layer, rest)):
        assert overlap(x, y) <= 1e-9 * total
    # the face of the layer on the cut lies in the plane z = 0 (the vertices at the top of the layer)
    top = layer.vertices[layer.vertices[:, 2] > -1e-3]
    assert len(top) > 0 and np.abs(top[:, 2]).max() <= 1e-6 * H
    assert res.stats["stack"]["order"] == [1, 2] and res.stats["stack"]["relations"] == {1: "erode", 2: "conformable"}


def test_erosion_drops_the_data_above_the_cut_by_the_value_not_by_the_frame(erosion: BodiesResult) -> None:
    clean = layer_points_e()
    extra = spurious_e(outside_box=True)
    outside = int((extra.xyz[:, 0] > 120.0).sum() + (extra.xyz[:, 0] < 0.0).sum())
    assert outside == 3  # in the field of the younger group (f < 0), outside the boundary: no body there
    dirty = md.assemble([cut_points(), with_extra(clean, extra)])
    res = build_stack(erosion_spec(), dirty, box_solid(BOX_E), threads=4)
    info = res.stats["stack"]
    assert info["dropped_total"] == extra.n and len(info["dropped"]) == 1
    assert info["dropped"][0] == {"group": 2, "by": 1, "relation": "erode", "points": extra.n, "of": clean.n + extra.n,
                                  "undefined": 0}  # fmt: skip
    assert res.fits[2].stats["points"] == clean.n
    assert any(f"отброшено точек {extra.n} из {clean.n + extra.n}" in line for line in res.journal)
    assert res.journal[0].startswith("Стек отношений: порядок расчёта — «Срез», «Пласт»")
    # the field of the older layer is the same bits as without the dropped points: they change nothing below the cut
    probe = probe_below()
    assert res.fits[2].field(probe).tobytes() == erosion.fits[2].field(probe).tobytes()
    same(res, erosion)


def test_without_the_relation_the_same_data_do_distort_the_older_layer(erosion: BodiesResult) -> None:
    """The control of the test above: with «Согласно» the dropped points stay and move the field by more than 0,1."""
    extra = spurious_e(outside_box=False)
    dirty = md.assemble([cut_points(), with_extra(layer_points_e(), extra)])
    res = build_stack(erosion_spec("conformable"), dirty, box_solid(BOX_E), threads=4)
    assert res.stats["stack"]["dropped_total"] == 0 and res.fits[2].stats["points"] == dirty.n - 91 * 5
    probe = probe_below()
    shift = np.abs(res.fits[2].field(probe) - erosion.fits[2].field(probe)).max()
    assert shift > 0.1


def test_erosion_shared_contacts_on_the_cut_are_dropped_and_the_contacts_below_are_not(erosion: BodiesResult) -> None:
    """A contact the layer shares with the cut (f_cut ≈ 0) belongs to the cut, not to the layer: dropped."""
    shared = points(2, "Пласт", [("contact", column(GX[GX >= 70.0], GY[GX >= 70.0], np.zeros(int((GX >= 70.0).sum()))),
                                  np.zeros(int((GX >= 70.0).sum())))])  # fmt: skip
    pts = md.assemble([cut_points(), with_extra(layer_points_e(), shared)])
    res = build_stack(erosion_spec(), pts, box_solid(BOX_E), threads=2)
    assert res.stats["stack"]["dropped_total"] == shared.n
    assert res.fits[2].field(probe_below()).tobytes() == erosion.fits[2].field(probe_below()).tobytes()


# --- the scene of the onlap (Т8.9-10) ---
# Box x 0…120, y 0…60, z −50…50. The older "Основание" is under the uneven surface s(x, y) = 5·sin(2πx/60)·cos(2πy/60);
# the younger "Налегание" lies on it up to the plane z = 25. Its stray data below s must not bend its lower face.

BOX_L = [0.0, 0.0, -50.0, 120.0, 60.0, 50.0]
ZTOP = 25.0
LX, LY = grid(0.0, 21, 11, 6.0)


def s_of(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return 5.0 * np.sin(2.0 * np.pi * x / 60.0) * np.cos(2.0 * np.pi * y / 60.0)


def base_points() -> md.Points:
    s = s_of(LX, LY)
    parts = [("contact", column(LX, LY, s), np.zeros_like(LX))]
    for d in (15.0, 30.0):
        parts.append(("inside", column(LX, LY, s - d), np.full_like(LX, -d)))
        parts.append(("outside", column(LX, LY, s + d), np.full_like(LX, d)))
    return points(2, "Основание", parts)


def lap_points() -> md.Points:
    s = s_of(LX, LY)
    parts = [
        ("contact", column(LX, LY, np.full_like(LX, ZTOP)), np.zeros_like(LX)),
        ("contact", column(LX, LY, s), np.zeros_like(LX)),
        ("inside", column(LX, LY, (s + ZTOP) / 2.0), -(ZTOP - s) / 2.0),
        ("outside", column(LX, LY, np.full_like(LX, ZTOP + 15.0)), np.full_like(LX, 15.0)),
        ("outside", column(LX, LY, s - 15.0), np.full_like(LX, 15.0)),
    ]
    return points(1, "Налегание", parts)


def stray_l(outside_box: bool) -> md.Points:
    """Points of the younger layer below the older surface: "inside" of the layer where the older rock is."""
    sel = np.arange(len(LX)) % 3 == 0
    parts = [("inside", column(LX[sel], LY[sel], s_of(LX[sel], LY[sel]) - 10.0), np.full(int(sel.sum()), -10.0))]
    if outside_box:
        parts.append(("inside", np.array([[150.0, 30.0, -20.0], [-30.0, 10.0, -35.0]]), np.full(2, -5.0)))
    return points(1, "Налегание", parts)


def lap_spec(relation: str = "onlap", order: tuple[int, ...] = (1, 2)) -> Any:
    names = {1: "Налегание", 2: "Основание"}
    groups = [{**domain(g, names[g]), **({"relation": relation} if g == 1 else {})} for g in order]
    return model(groups, BOX_L, h=H)


@pytest.fixture(scope="module")
def onlap() -> BodiesResult:
    pts = md.assemble([lap_points(), base_points()])
    return build_stack(lap_spec(), pts, box_solid(BOX_L), threads=4)


def probe_lap(n: int = 400) -> np.ndarray:
    rng = np.random.default_rng(6)
    return rng.uniform([5.0, 5.0, -40.0], [115.0, 55.0, 40.0], size=(n, 3))


def test_onlap_t8910_the_lower_face_of_the_younger_layer_is_the_older_surface(onlap: BodiesResult) -> None:
    res = onlap
    lap, base, rest = bodies_of(res)
    assert [o.name for o in bodies_of(res)] == ["Налегание", "Основание", "Прочее"]  # the order of the groups
    assert res.stats["stack"]["order"] == [2, 1]  # the older is solved first and wins the overlap
    v = lap.vertices
    on_top = v[:, 2] > ZTOP - 5.0  # the top face (the surface of the younger group itself)
    on_wall = (v[:, 0] <= 1e-9) | (v[:, 0] >= 120.0 - 1e-9) | (v[:, 1] <= 1e-9) | (v[:, 1] >= 60.0 - 1e-9)
    lower = v[~on_top & ~on_wall]
    assert len(lower) > 100
    assert np.abs(lower[:, 2] - s_of(lower[:, 0], lower[:, 1])).max() <= 0.25 * H  # the older surface (Т8.9-10)
    assert np.abs(res.fits[2].field(lower)).max() <= 0.25 * H  # and on f = 0 of the older field itself
    assert base.vertices[:, 2].max() <= s_of(*base.vertices[:, :2].T).max() + 0.25 * H
    total = 120.0 * 60.0 * 100.0
    assert sum(o.volume for o in bodies_of(res)) == pytest.approx(total, rel=1e-9)
    assert res.stats["balance"] <= 1e-9
    assert lap.volume == pytest.approx(120.0 * 60.0 * ZTOP, rel=0.01)  # ∫(25 − s) over whole periods
    assert base.volume == pytest.approx(120.0 * 60.0 * 50.0, rel=0.01)
    for x, y in ((lap, base), (lap, rest), (base, rest)):
        assert overlap(x, y) <= 1e-9 * total
    for o in (lap, base, rest):
        assert_solid(o.vertices, o.triangles)


def test_onlap_drops_the_points_of_the_younger_group_below_the_older_surface(onlap: BodiesResult) -> None:
    extra = stray_l(outside_box=True)
    clean = lap_points()
    dirty = md.assemble([with_extra(clean, extra), base_points()])
    res = build_stack(lap_spec(), dirty, box_solid(BOX_L), threads=2)
    info = res.stats["stack"]
    below = len(
        LX
    )  # the "outside" points of the younger layer under the older surface go too: they lie in the older rock
    assert info["dropped_total"] == extra.n + below and info["dropped"][0]["by"] == 2
    assert info["dropped"][0]["group"] == 1
    assert info["dropped"][0]["relation"] == "onlap"
    assert res.fits[1].stats["points"] == clean.n - below
    probe = probe_lap()
    assert res.fits[1].field(probe).tobytes() == onlap.fits[1].field(probe).tobytes()
    same(res, onlap)
    assert any("налегание группы «Основание»" in line and "отброшено точек" in line for line in res.journal)
    # the control: «Согласно» keeps them and the younger field moves
    inside = stray_l(outside_box=False)
    kept = md.assemble([with_extra(clean, inside), base_points()])
    free = build_stack(lap_spec("conformable"), kept, box_solid(BOX_L), threads=2)
    assert free.stats["stack"]["dropped_total"] == 0 and free.fits[1].stats["points"] == clean.n + inside.n
    assert np.abs(free.fits[1].field(probe) - onlap.fits[1].field(probe)).max() > 0.5


def test_a_contact_shared_with_the_older_surface_is_kept_by_the_onlap(onlap: BodiesResult) -> None:
    """The younger contacts on s lie ON the older surface (f ≈ 0): within the tolerance, kept, not dropped."""
    assert onlap.stats["stack"]["dropped_total"] == len(LX)  # only the points under the surface: s − 15
    assert onlap.fits[1].stats["points"] == lap_points().n - len(LX)  # the 231 contacts on s are in


# --- «Согласно» is `build_bodies` ---


def test_conformable_is_the_bits_of_build_bodies() -> None:
    pts = md.assemble([lap_points(), base_points()])
    spec = lap_spec("conformable")
    a = build_stack(spec, pts, box_solid(BOX_L), threads=2)
    b = build_bodies(spec, pts, box_solid(BOX_L), threads=2)
    same(a, b)
    assert a.journal == b.journal
    assert a.stats["stack"]["order"] == [1, 2] and a.stats["stack"]["dropped_total"] == 0
    assert list(a.fits) == list(b.fits)
    assert [o.kind for o in a.outputs] == [o.kind for o in b.outputs]
    # the relation of the model and the argument `relations` are the same thing
    spec_model = lap_spec("onlap")
    c = build_stack(spec_model, pts, box_solid(BOX_L), relations={1: "conformable"}, threads=2)
    same(a, c)


# --- the same bytes for threads and the order of the holes ---


def test_stack_scene_is_the_same_bytes_for_threads(erosion: BodiesResult, onlap: BodiesResult) -> None:
    pe = md.assemble([cut_points(), layer_points_e()])
    pl = md.assemble([lap_points(), base_points()])
    for threads in (1, 2, 8):
        same(erosion, build_stack(erosion_spec(), pe, box_solid(BOX_E), threads=threads))
        same(onlap, build_stack(lap_spec(), pl, box_solid(BOX_L), threads=threads))


def test_mixed_stack_of_real_holes_tiles_the_boundary_and_is_the_same_bytes() -> None:
    """The five domains of the fixture holes with «Эрозия» of ALV and «Налегание» of SH: Σ the volumes = the boundary,
    the order of the solution puts GR before SH; threads 1 / 8 and the shuffled holes give the same bytes."""
    rel = {2: "erode", 4: "onlap"}
    pts = scene_points()
    base = build_stack(scene_spec(), pts, box_solid(SCENE_BOX), relations=rel, threads=1)
    assert base.stats["stack"]["order"] == [1, 2, 3, 5, 4]
    boundary = 420.0 * 420.0 * 210.0
    bodies = bodies_of(base)
    assert sum(o.volume for o in bodies) == pytest.approx(boundary, rel=1e-9)
    assert base.stats["balance"] <= 1e-9
    assert [o.group for o in base.outputs if o.kind == "body"][:5] == [1, 2, 3, 4, 5]  # by the age of the groups
    for o in bodies:
        assert_solid(o.vertices, o.triangles)
    for i, x in enumerate(bodies):
        for y in bodies[i + 1 :]:
            assert overlap(x, y) <= 1e-9 * boundary
    dropped = base.stats["stack"]["dropped"]
    assert dropped and all(d["points"] > 0 for d in dropped)
    shuffled = scene_points(order=np.random.default_rng(7).permutation(100))
    assert shuffled.to_parquet() == pts.to_parquet()
    same(base, build_stack(scene_spec(), shuffled, box_solid(SCENE_BOX), relations=rel, threads=8))


# --- the graph of the relations, the errors ---


def test_the_order_of_the_solution_follows_the_winners() -> None:
    names = ["a", "b", "c", "d"]

    def groups(*rels: str) -> Any:
        spec = parse_stack(rels)
        return domains_of(spec)

    def parse_stack(rels: tuple[str, ...]) -> Any:
        gs = [{**domain(i + 1, names[i]), "relation": r} for i, r in enumerate(rels)]
        return model(gs, BOX_L, h=H)

    for rels, want in ((("conformable",) * 4, [0, 1, 2, 3]),
                       (("erode", "conformable", "conformable", "conformable"), [0, 1, 2, 3]),
                       (("onlap", "conformable", "conformable", "conformable"), [1, 2, 3, 0]),
                       (("conformable", "onlap", "conformable", "conformable"), [0, 2, 3, 1]),
                       (("erode", "onlap", "erode", "conformable"), [0, 2, 3, 1]),
                       (("onlap", "onlap", "onlap", "conformable"), [3, 2, 1, 0])):  # fmt: skip
        g = groups(*rels)
        assert order_of(len(g), precedence(g)) == want, rels


def test_a_cycle_of_the_relations_is_refused_in_russian() -> None:
    with pytest.raises(ApiError) as err:
        order_of(3, [(0, 1), (1, 2), (2, 0)], ["Верх", "Середина", "Низ"])
    e = err.value
    assert e.status == 422 and e.code == "bad_relations" and e.hint
    assert "цикл" in e.message and "«Верх»" in e.message and "«Низ»" in e.message


def test_bad_relations_argument_is_refused_in_russian() -> None:
    spec = lap_spec()
    pts = md.assemble([lap_points(), base_points()])
    box = box_solid(BOX_L)
    for rel, code, text in (({9: "erode"}, "bad_param", "несуществующей"),
                            ({1: "над"}, "bad_param", "неизвестное")):  # fmt: skip
        with pytest.raises(ApiError) as err:
            build_stack(spec, pts, box, relations=rel)
        e = err.value
        assert e.status == 422 and e.code == code and text in e.message and e.hint
    with pytest.raises(ApiError) as bad_order:
        build_bodies(spec, pts, box, order=[1])
    assert bad_order.value.code == "bad_param" and bad_order.value.extra["param"] == "order"


def test_a_relation_given_to_a_group_outside_the_stack_is_refused_unless_it_is_the_one_it_has() -> None:
    from dataclasses import replace

    base = model([domain(1, "a"), domain(2, "b"), domain(3, "c")], BOX_L, h=H)
    spec = replace(base, groups=(*base.groups[:2], replace(base.groups[2], type="vein_system")))  # fmt: skip
    with pytest.raises(ApiError) as err:
        domains_of(spec, {3: "erode"})
    e = err.value
    assert e.status == 422 and e.code == "bad_param" and e.extra["param"] == "relations.3" and e.hint
    assert [g.id for g in domains_of(spec, {3: "conformable", 1: "erode"})] == [1, 2]  # the stored one: nothing to say


def test_a_group_whose_data_are_all_dropped_has_no_contacts() -> None:
    """The older layer has data only above the cut: nothing is left, the message says why."""
    over = points(2, "Пласт", [("contact", column(GX, GY, np.full_like(GX, 20.0)), np.zeros_like(GX)),
                               ("inside", column(GX, GY, np.full_like(GX, 30.0)), np.full_like(GX, -3.0)),
                               ("outside", column(GX, GY, np.full_like(GX, 40.0)), np.full_like(GX, 3.0))])  # fmt: skip
    with pytest.raises(ApiError) as err:
        build_stack(erosion_spec(), md.assemble([cut_points(), over]), box_solid(BOX_E))
    e = err.value
    assert e.code == "no_contacts" and e.status == 422 and e.extra["group"] == 2 and e.extra["dropped"] == over.n
    assert "после отбрасывания" in e.message and "Пласт" in e.message and e.hint


def test_the_relation_of_the_oldest_group_does_nothing() -> None:
    """Nothing is older than the last group: its relation changes no data; the result is that of «Согласно»."""
    pts = md.assemble([lap_points(), base_points()])
    spec = lap_spec("conformable")
    free = build_stack(spec, pts, box_solid(BOX_L))
    last = build_stack(spec, pts, box_solid(BOX_L), relations={2: "onlap"})
    assert last.stats["stack"]["dropped_total"] == 0
    same(free, last)


# --- the cancel ---


def test_cancel_leaves_nothing() -> None:
    pts = md.assemble([lap_points(), base_points()])
    spec = lap_spec()
    box = box_solid(BOX_L)
    seen: list[tuple[float, str]] = []
    res = build_stack(spec, pts, box, tick=lambda f, m: seen.append((f, m)))
    assert len(res.outputs) == 5 and len(seen) > 10
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0 and 0.0 <= fractions[0]
    for stop in (1, len(seen) // 4, len(seen) // 2, 3 * len(seen) // 4, len(seen) - 2):
        count = [0]

        def tick(_f: float, _m: str, stop: int = stop, count: list[int] = count) -> None:
            count[0] += 1
            if count[0] == stop:
                raise Cancelled

        with pytest.raises(Cancelled):
            build_stack(spec, pts, box, tick=tick)


# --- the module is light ---


def test_module_does_not_load_numba_or_manifold() -> None:
    code = ("import bf_core.model.stack, sys; "
            "bad = [m for m in ('numba', 'manifold3d', 'pyfqmr') if m in sys.modules]; "
            "raise SystemExit(3 if bad else 0)")  # fmt: skip
    run = subprocess.run([sys.executable, "-c", code], cwd=CORE, capture_output=True, text=True, timeout=120,
                         env={"PYTHONPATH": str(CORE), "BF_NO_WARMUP": "1", "PATH": "/usr/bin:/bin"})  # fmt: skip
    assert run.returncode == 0, run.stderr[-400:]
