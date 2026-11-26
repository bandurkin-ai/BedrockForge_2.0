"""The pit slope template (подшаг 21.3; ТЗ 8.11.2, Т8.11-6; ADR 0028 decisions 9–12).

Т8.11-6 and the numbers of the prototype of the spec (45°, cubes, K = 8: 17 offsets, 1,44° (mean 0,58°); sectors 40°
(north) … 50° (south): 1,63°; 55°, K = 12: 2,05°), an independent brute-force reference (sets of offsets and the
explicit breadth-first closure R(M)), a turn by 30° = a shift of the azimuths, the automatic K, the domains, the
repeat bit for bit, the reduction of transitive arcs, the warning «увеличьте K», 422 in Russian."""

from __future__ import annotations

import json
import math
import time

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks.schema import GridV2
from bf_core.mine import template as tp

CUBE = (1.0, 1.0, 1.0)


def table(*rows: tuple[float, float]) -> tp.AngleTable:
    return tp.angle_table(list(rows))


def build(table_: tp.AngleTable, steps: tuple[float, float, float] = CUBE, **kw: object) -> tp.PitTemplate:
    return tp.build_template(*steps, table_, **kw)  # type: ignore[arg-type]


# ---- an independent reference: plain Python sets, no kernels ----


def ref_phi(az: float, rows: list[tuple[float, float]]) -> float:
    rows = sorted((a % 360.0, p) for a, p in rows)
    if len(rows) == 1:
        return rows[0][1]
    az %= 360.0
    for i, (a0, p0) in enumerate(rows):
        a1, p1 = rows[(i + 1) % len(rows)]
        a1 += 360.0 if i == len(rows) - 1 else 0.0
        x = az if az >= a0 else az + 360.0
        if a0 <= x < a1:
            return p0 + (p1 - p0) * (x - a0) / (a1 - a0)
    raise AssertionError(az)


def ref_azimuth(di: int, dj: int, dx: float, dy: float, rot: float) -> float:
    s, c = math.sin(math.radians(rot)), math.cos(math.radians(rot))
    a, b = di * dx, dj * dy
    return math.degrees(math.atan2(c * a - s * b, s * a + c * b)) % 360.0


def ref_template(dx, dy, dz, rows, k, rot=0.0):  # type: ignore[no-untyped-def]
    """{m: set of (Δi, Δj)} by the definition, and {m: all compositions that add up to m levels}."""
    reach_h = dz / math.tan(math.radians(min(p for _, p in rows)))
    wi, wj = int(k * reach_h / dx) + 3, int(k * reach_h / dy) + 3
    t: dict[int, set[tuple[int, int]]] = {}
    comp: dict[int, set[tuple[int, int]]] = {0: {(0, 0)}}
    for m in range(1, k + 1):
        without = {(a[0] + b[0], a[1] + b[1]) for m1 in range(1, m) for a in t[m1] for b in comp[m - m1] if m - m1 >= 1}
        cur = set()
        for i in range(-wi, wi + 1):
            for j in range(-wj, wj + 1):
                d = math.hypot(i * dx, j * dy)
                lim = m * dz / math.tan(math.radians(ref_phi(ref_azimuth(i, j, dx, dy, rot), rows))) if d else 1.0
                if d <= lim * (1 + 1e-12) and (i, j) not in without:
                    cur.add((i, j))
        t[m] = cur
        comp[m] = cur | without
    return t, comp


def ref_rho(t, k, dx, dy, rot, az):  # type: ignore[no-untyped-def]
    """ρ(az) over the explicit closure of the template on exactly 2K levels (breadth-first by levels)."""
    m_total = 2 * k
    level = {0: {(0, 0)}}
    for lv in range(1, m_total + 1):
        level[lv] = {(r[0] + o[0], r[1] + o[1]) for m in range(1, min(k, lv) + 1) for o in t[m] for r in level[lv - m]}
    s, c = math.sin(math.radians(rot)), math.cos(math.radians(rot))
    ux, uy = math.sin(math.radians(az)), math.cos(math.radians(az))
    return max((c * i * dx - s * j * dy) * ux + (s * i * dx + c * j * dy) * uy for i, j in level[m_total])


def level_sets(tpl: tp.PitTemplate, idx: int = 0) -> dict[int, set[tuple[int, int]]]:
    d = tpl.tables[idx]
    return {m: {(int(a), int(b)) for a, b, _ in d.level(m)} for m in range(1, tpl.k + 1)}


# ---- Т8.11-6 and the prototype of the spec ----


def test_t8_11_6_prototype_45_cubes_k8() -> None:
    d = build(table((0, 45)), k=8).tables[0]
    assert d.offsets.shape == (
        17,
        3,
    )  # 5 of the level 1, 4 of the level 3 (±2, ±2), 8 of the level 5 (±4, ±3), (±3, ±4)
    assert d.err_max <= 1.5  # Т8.11-6
    assert d.err_max == pytest.approx(1.44, abs=0.005)
    assert d.err_mean == pytest.approx(0.58, abs=0.005)
    assert sorted(set(d.offsets[:, 2].tolist())) == [1, 3, 5]
    assert [len(d.level(m)) for m in range(1, 9)] == [5, 0, 4, 0, 8, 0, 0, 0]


def test_t8_11_6_sectors_40_north_50_south() -> None:
    d = build(table((0, 40), (180, 50)), k=8).tables[0]
    assert d.err_max <= 2.0  # Т8.11-6
    assert d.err_max == pytest.approx(1.63, abs=0.005)


def test_prototype_55_deg_k12() -> None:
    tpl = build(table((0, 55)), k=12)
    assert tpl.tables[0].err_max == pytest.approx(2.05, abs=0.005)
    assert tpl.warnings == ()  # 2,05° ≤ 2,5°


def test_phi_eff_is_the_closure_not_the_table() -> None:
    d = build(table((0, 45)), k=8).tables[0]
    assert np.all(d.phi == 45.0)
    assert np.all(d.phi_eff >= 45.0 - 1e-9)  # the closure is steeper than the cone: φ_эфф ≥ φ on cubes
    assert d.phi_eff[0] == pytest.approx(45.0, abs=1e-9)  # along an axis the offsets are exact
    assert d.phi_eff[45] > 45.5  # off the axes the closure is steeper


# ---- against the independent reference ----


@pytest.mark.parametrize(
    ("steps", "rows", "k", "rot"),
    [
        (CUBE, [(0.0, 45.0)], 8, 0.0),
        (CUBE, [(0.0, 40.0), (180.0, 50.0)], 6, 0.0),
        ((10.0, 6.0, 7.0), [(30.0, 50.0), (200.0, 40.0), (300.0, 60.0)], 6, 30.0),
        ((5.0, 5.0, 10.0), [(0.0, 60.0), (90.0, 35.0), (180.0, 55.0), (270.0, 45.0)], 5, 17.5),
        ((12.5, 25.0, 6.0), [(45.0, 30.0)], 7, 90.0),
    ],
)
def test_matches_the_brute_force_reference(steps, rows, k, rot) -> None:  # type: ignore[no-untyped-def]
    tpl = tp.build_template(*steps, tp.angle_table(rows), k=k, rotation=rot)
    t, _ = ref_template(*steps[:3], rows, k, rot)
    assert level_sets(tpl) == {m: t[m] for m in range(1, k + 1)}
    # φ_эфф of the kernel (a DP over the levels) = the max over the explicit breadth-first closure R(2K)
    d = tpl.tables[0]
    for az in range(0, 360, 15):
        rho = ref_rho(t, k, steps[0], steps[1], rot, az)
        assert d.phi_eff[az] == pytest.approx(math.degrees(math.atan(2 * k * steps[2] / rho)), abs=1e-9)


def test_reduction_of_transitive_arcs_and_completeness() -> None:
    tpl = build(table((0, 40), (180, 50)), (10.0, 10.0, 10.0), k=10)
    d = tpl.tables[0]
    offs = {(int(a), int(b), int(m)) for a, b, m in d.offsets}
    cone = {
        (i, j, m)
        for m in range(1, 11)
        for i in range(-40, 41)
        for j in range(-40, 41)
        if math.hypot(i, j) * 10.0 <= m * 10.0 / math.tan(math.radians(ref_phi(ref_azimuth(i, j, 10, 10, 0), [(0, 40), (180, 50)]))) * (1 + 1e-12)  # noqa: E501
    }  # fmt: skip
    assert offs <= cone
    # no offset is the sum of offsets of the template of smaller levels
    for a, b, m in offs:
        for a1, b1, m1 in offs:
            if m1 < m and (a - a1, b - b1, m - m1) in offs:
                raise AssertionError(((a, b, m), (a1, b1, m1)))
    # every cone offset is in the template or is a composition of its offsets: the closure covers the cone
    closure = {(0, 0, 0)}
    for _ in range(10):
        closure |= {(a + o[0], b + o[1], m + o[2]) for a, b, m in closure for o in offs if m + o[2] <= 10}
    assert cone <= closure


def test_the_first_level_has_the_block_above_and_the_order_is_canonical() -> None:
    d = build(table((0, 50), (120, 38), (240, 60)), (4.0, 5.0, 3.0)).tables[0]
    assert d.offsets.dtype == np.int32 and d.offsets.ndim == 2 and d.offsets.shape[1] == 3
    assert d.level_ptr.dtype == np.int32 and d.level_ptr[0] == 0 and d.level_ptr[-1] == len(d.offsets)
    assert (0, 0, 1) in {tuple(int(x) for x in r) for r in d.level(1)}
    assert not np.any((d.offsets[:, 0] == 0) & (d.offsets[:, 1] == 0) & (d.offsets[:, 2] > 1))
    keys = [(int(m), int(j), int(i)) for i, j, m in d.offsets]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)  # the order (m, Δj, Δi)
    for m in range(1, 9):
        assert np.all(d.level(m)[:, 2] == m)


# ---- directions: azimuth, z, a turn of the model ----


def test_azimuth_is_from_north_clockwise_and_z_grows_with_k() -> None:
    assert tp.azimuth_of(0, 1, 1.0, 1.0) == 0.0  # +j is north at φ = 0
    assert tp.azimuth_of(1, 0, 1.0, 1.0) == 90.0
    assert tp.azimuth_of(0, -1, 1.0, 1.0) == 180.0
    assert tp.azimuth_of(1, 0, 1.0, 1.0, 30.0) == pytest.approx(60.0)  # the i axis of a model turned by 30° (CCW)
    g = GridV2.of({"corner": [10.0, 20.0, 100.0], "counts": [2, 2, 3], "size": [1.0, 1.0, 2.0], "rotation": 30.0})
    p = g.to_project(np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 2.0], [1.0, 0.0, 0.0]]))
    assert p[1, 2] > p[0, 2]  # k + m is UP: the offsets (Δi, Δj, m > 0) go to the higher blocks
    ax = np.degrees(np.arctan2(p[2, 0] - p[0, 0], p[2, 1] - p[0, 1])) % 360.0
    assert ax == pytest.approx(tp.azimuth_of(1, 0, 1.0, 1.0, 30.0))  # the same azimuth as `GridV2.to_project`


def test_gentler_slope_reaches_further_in_its_direction() -> None:
    d = build(table((0, 40), (180, 50)), k=8).tables[0]  # 40° in the north: the long way to +j
    assert d.offsets[:, 1].max() > -d.offsets[:, 1].min()
    d = build(table((270, 30), (90, 60)), k=8, rotation=90.0).tables[0]  # local +j of a model turned by 90° is west
    assert d.offsets[:, 1].max() > -d.offsets[:, 1].min()


@pytest.mark.parametrize("rows", [[(0.0, 40.0), (180.0, 50.0)], [(10.0, 35.0), (100.0, 60.0), (250.0, 45.0)]])
def test_a_turn_by_30_degrees_is_a_shift_of_the_azimuths(rows) -> None:  # type: ignore[no-untyped-def]
    shifted = [((a + 30.0) % 360.0, p) for a, p in rows]
    turned = build(tp.angle_table(rows), k=8, rotation=30.0).tables[0]
    plain = build(tp.angle_table(shifted), k=8).tables[0]
    assert np.array_equal(turned.offsets, plain.offsets)
    assert np.array_equal(turned.level_ptr, plain.level_ptr)
    # φ_эфф(az) of the turned model at the project azimuth az is φ_эфф of the shifted table at az + 30°
    assert np.allclose(turned.phi_eff, np.roll(plain.phi_eff, -30), atol=1e-9)
    assert np.allclose(turned.phi, np.roll(plain.phi, -30), atol=1e-9)
    assert turned.err_max == pytest.approx(plain.err_max, abs=1e-9)
    assert turned.err_mean == pytest.approx(plain.err_mean, abs=1e-9)


def test_a_turn_by_90_swaps_the_axes() -> None:
    a = build(table((0, 40), (180, 50)), (3.0, 5.0, 4.0), k=8).tables[0]
    b = build(table((0, 40), (180, 50)), (5.0, 3.0, 4.0), k=8, rotation=90.0).tables[0]
    # local (i, j) of the turned model = (j, −i) of the plain one: i·dx of the turned is −j·dy of the plain
    assert {(int(i), int(j), int(m)) for i, j, m in b.offsets} == {(int(j), -int(i), int(m)) for i, j, m in a.offsets}


# ---- K ----


@pytest.mark.parametrize(
    ("steps", "angle", "k"),
    [
        (CUBE, 45.0, 8),  # 8·1·tan45 / 1 = 8 (tan 45° is 0,9999999999999999: no 9)
        (CUBE, 60.0, 14),  # 8·tan60 = 13,86
        (CUBE, 75.0, 20),  # 8·tan75 = 29,9 → the cap
        ((1.0, 1.0, 2.0), 60.0, 8),  # 8·1,73/2 = 6,9 → the floor of 8
        ((10.0, 5.0, 2.0), 50.0, 20),  # 8·5·1,19/2 = 23,8 → the cap
        ((10.0, 10.0, 10.0), 50.0, 10),  # 9,53 → 10
        ((5.0, 7.0, 3.0), 40.0, 12),  # 8·5·0,839/3 = 11,2 → 12
    ],
)
def test_auto_k(steps, angle, k) -> None:  # type: ignore[no-untyped-def]
    assert tp.auto_k(*steps, angle) == k


def test_auto_k_takes_the_steepest_angle_of_all_tables_and_is_shared() -> None:
    gen = table((0, 45))
    steep = tp.angle_table([(0.0, 40.0), (180.0, 70.0)])
    tpl = tp.build_template(1.0, 1.0, 1.0, gen, [("A", steep)])
    assert tpl.k_auto and tpl.k == tp.auto_k(1.0, 1.0, 1.0, 70.0) == 20
    assert build(gen, k=5).k == 5 and not build(gen, k=5).k_auto


# ---- the domains ----


def test_domains_have_their_own_tables_and_the_general_one_is_the_default() -> None:
    gen, flat, steep = table((0, 45)), table((0, 30)), table((0, 60))
    tpl = tp.build_template(10.0, 10.0, 10.0, gen, [("Окисленные", flat), ("Сульфиды", steep), ("Те же", gen)], k=10)
    assert len(tpl.tables) == 4
    assert tpl.table_index(None) == 0 and tpl.table_index("нет такого") == 0
    assert [tpl.table_index(x) for x in ("Окисленные", "Сульфиды", "Те же")] == [1, 2, 3]
    assert tpl.tables[1].key == "Окисленные" and tpl.tables[0].key is None
    reach = [int(np.abs(t.offsets[:, :2]).max()) for t in tpl.tables]
    assert reach[1] > reach[0] > reach[2]  # the flatter the slope, the farther the offsets
    assert np.array_equal(tpl.tables[3].offsets, tpl.tables[0].offsets) and tpl.tables[3].key == "Те же"
    assert tpl.tables[3].offsets is tpl.tables[0].offsets  # an equal table is computed once
    # a domain = the template of a model with that table only
    alone = tp.build_template(10.0, 10.0, 10.0, flat, k=10).tables[0]
    assert np.array_equal(alone.offsets, tpl.tables[1].offsets)


def test_sixteen_domains_at_most_and_unique_labels() -> None:
    gen = table((0, 45))
    ok = [(f"D{i}", table((0, 30.0 + i))) for i in range(16)]
    assert len(tp.build_template(1.0, 1.0, 1.0, gen, ok, k=3).tables) == 17
    with pytest.raises(ApiError) as e:
        tp.build_template(1.0, 1.0, 1.0, gen, [*ok, ("D16", gen)], k=3)
    assert e.value.status == 422 and e.value.code == "bad_param" and e.value.extra == {"param": "domains"}
    with pytest.raises(ApiError) as e:
        tp.build_template(1.0, 1.0, 1.0, gen, [("A", gen), ("A", gen)], k=3)
    assert e.value.code == "bad_param"
    assert len(tp.build_template(1.0, 1.0, 1.0, gen, {"A": gen, "B": table((0, 50))}, k=3).tables) == 3  # a mapping


# ---- the warning, determinism, JSON ----


def test_the_angle_error_above_the_tolerance_is_a_warning_not_a_refusal() -> None:
    gen = table((0, 45))
    tpl = tp.build_template(1.0, 1.0, 1.0, gen, [("Крутой", table((0, 70)))], k=5)
    assert tpl.k == 5 and len(tpl.warnings) >= 1
    w = tpl.warnings[0]
    assert w["code"] == "angle_error" and w["error"] > tpl.tolerance == 2.5
    assert w["message"].startswith("Ошибка угла ") and w["message"].endswith("увеличьте K")
    assert "," in w["message"] and "°" in w["message"] and "Увеличьте K" in w["hint"]
    assert {x["domain"] for x in tpl.warnings} <= {None, "Крутой"}
    assert any(x["domain"] == "Крутой" for x in tpl.warnings)
    loose = tp.build_template(1.0, 1.0, 1.0, gen, [("Крутой", table((0, 70)))], k=5, tolerance=10.0)
    assert loose.warnings == ()
    cap = tp.build_template(10.0, 10.0, 1.0, table((0, 80)), k=20)  # K is at its cap: another hint
    assert cap.warnings and "наибольший" in cap.warnings[0]["hint"]


def test_repeat_is_bit_for_bit() -> None:
    rows = [(i * 45.0, 35.0 + (i * 7) % 25) for i in range(8)]
    a = tp.build_template(10.0, 12.5, 7.0, tp.angle_table(rows), [("X", table((10, 55)))], k=12, rotation=37.0)
    b = tp.build_template(10.0, 12.5, 7.0, tp.angle_table(rows), [("X", table((10, 55)))], k=12, rotation=37.0)
    assert a.signature() == b.signature()
    for ta, tb in zip(a.tables, b.tables, strict=True):
        assert ta.offsets.tobytes() == tb.offsets.tobytes()
        assert ta.phi_eff.tobytes() == tb.phi_eff.tobytes() and ta.phi.tobytes() == tb.phi.tobytes()
        assert ta.err_max == tb.err_max and ta.err_mean == tb.err_mean
    c = tp.build_template(10.0, 12.5, 7.0, tp.angle_table(rows), [("X", table((10, 55)))], k=13, rotation=37.0)
    assert c.signature() != a.signature()


def test_json_has_what_the_check_window_needs() -> None:
    tpl = tp.build_template(10.0, 10.0, 5.0, table((0, 40), (180, 50)), [("A", table((0, 35)))])
    j = json.loads(json.dumps(tpl.to_json()))
    assert j["k"] == tpl.k and j["k_auto"] is True and j["tolerance"] == 2.5 and j["steps"] == [10.0, 10.0, 5.0]
    assert [t["domain"] for t in j["tables"]] == [None, "A"]
    t0 = j["tables"][0]
    assert len(t0["phi_eff"]) == 360 == len(t0["phi"]) and len(t0["offsets"]) == t0["count"]
    assert t0["slopes"] == [{"azimuth": 0.0, "angle": 40.0}, {"azimuth": 180.0, "angle": 50.0}]
    assert "offsets" not in tpl.to_json(with_offsets=False)["tables"][0]


def test_tick_is_called_and_may_cancel() -> None:
    seen: list[tuple[float | None, str | None]] = []
    tp.build_template(
        1.0, 1.0, 1.0, table((0, 45)), [("A", table((0, 50)))], k=4, tick=lambda p, m: seen.append((p, m))
    )
    assert seen[0] == (0.0, "Шаблон") and seen[-1] == (1.0, "Шаблон")

    class Cancel(Exception):
        pass

    def boom(_p: float | None, _m: str | None) -> None:
        raise Cancel

    with pytest.raises(Cancel):
        tp.build_template(1.0, 1.0, 1.0, table((0, 45)), k=4, tick=boom)


# ---- the input: 422 in Russian ----


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [(0.0, 9.9)],
        [(0.0, 80.1)],
        [(0.0, 45.0)] * 2,
        [(0.0, 45.0), (360.0, 50.0)],
        [(float(i * 20), 45.0) for i in range(17)],
        [(float("nan"), 45.0)],
        [(0.0, float("inf"))],
        [("a", 45.0)],
        [(0.0,)],
        "45",
    ],
)
def test_bad_tables(rows) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ApiError) as e:
        tp.angle_table(rows)
    assert e.value.status == 422 and e.value.code == "bad_param" and e.value.extra == {"param": "slopes"}
    assert e.value.message and e.value.hint and any("а" <= ch <= "я" for ch in e.value.message.lower())


def test_table_forms() -> None:
    a = tp.angle_table([{"azimuth": 350.0, "angle": 40.0}, {"az": -90.0, "angle": 50.0}, (10.0, 45.0)])
    assert a.azimuths == (10.0, 270.0, 350.0) and a.angles == (45.0, 50.0, 40.0)  # reduced to [0; 360) and sorted
    assert tp.angle_table([(0.0, 10.0)]).phi(123.0) == 10.0 and tp.angle_table([(0.0, 80.0)]).phi(0.0) == 80.0
    t = tp.angle_table([(0.0, 40.0), (180.0, 50.0)])
    assert t.phi(0.0) == 40.0 and t.phi(180.0) == 50.0 and t.phi(90.0) == pytest.approx(45.0)
    assert t.phi(270.0) == pytest.approx(45.0) and t.phi(45.0) == pytest.approx(42.5) and t.phi(360.0) == 40.0
    assert t.phi(-45.0) == pytest.approx(42.5)  # round the circle through north


@pytest.mark.parametrize(
    ("kw", "param"),
    [
        ({"k": 0}, "k"),
        ({"k": 21}, "k"),
        ({"k": True}, "k"),
        ({"k": 2.5}, "k"),
        ({"tolerance": 0.4}, "tolerance"),
        ({"tolerance": 10.5}, "tolerance"),
        ({"rotation": float("nan")}, "rotation"),
    ],
)
def test_bad_parameters(kw, param) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ApiError) as e:
        build(table((0, 45)), **kw)
    assert e.value.status == 422 and e.value.extra == {"param": param}


@pytest.mark.parametrize(
    "steps", [(0.0, 1.0, 1.0), (1.0, -1.0, 1.0), (1.0, 1.0, float("nan")), (1.0, 1.0, float("inf"))]
)
def test_bad_steps(steps) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ApiError) as e:
        build(table((0, 45)), steps)
    assert e.value.code == "bad_param" and e.value.extra["param"] in ("dx", "dy", "dz")


# ---- timing ----


def test_k20_16_sectors_8_domains_within_a_second() -> None:
    rows = [(i * 22.5, 35.0 + (i * 7) % 25) for i in range(16)]
    doms = [(f"D{d}", tp.angle_table([(a + 3.0 * d, p) for a, p in rows])) for d in range(8)]
    gen = tp.angle_table(rows)
    tp.build_template(10.0, 10.0, 10.0, gen, doms[:1], k=4)  # compiled / loaded from the cache
    t0 = time.perf_counter()
    tpl = tp.build_template(10.0, 10.0, 10.0, gen, doms, k=20)
    assert time.perf_counter() - t0 <= 1.0
    assert len(tpl.tables) == 9 and tpl.k == 20 and all(len(t.offsets) > 0 for t in tpl.tables)
