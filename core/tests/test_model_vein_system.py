"""The system of veins (подшаг 19.35; ТЗ 7.35 Д-39, ТД39-1…3; ADR 0026 решение 21).

Slabs of known size: a plate of the thickness 2 m in a rectangle of the plane (x, y), (y, z) or (x, z). Analytic: the
vein A (z ∈ [−1, 1], x, y ∈ [−50, 50]) and the vein B (x ∈ [−1, 1], y ∈ [−30, 30], z ∈ [−50, 50]) cross in the box
[−1, 1] × [−30, 30] × [−1, 1] (volume 240 m³); their surfaces cross in two closed loops (the top and the bottom of A,
each of the perimeter 2 × (60 + 2) = 124 m): the length of the lines is 248 m.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.geo import meshes, solids
from bf_core.model import vein_system as vs
from bf_core.model import veins

ORG = np.array([512_345.0, 6_234_567.0, 412.0])  # UTM-sized coordinates
HULL = {"bound": "hull", "offset": 0.0, "t_min": 0.0}
H = 2.0
AXES = {"z": (0, 1, 2), "x": (1, 2, 0), "y": (2, 0, 1)}  # the normal → the xyz columns of (p, q, normal)


def slab(code: str, normal: str, p: float, q: float, thick: float = 2.0, centre: Any = (0.0, 0.0, 0.0), n: int = 11,
         params: dict[str, Any] | None = None, name: str | None = None) -> vs.VeinInput:  # fmt: skip
    """A flat vein: the plane normal to `normal`, the rectangle ±`p`, ±`q` around `centre`, `thick` metres thick."""
    ip, iq, i_n = AXES[normal]
    gp, gq = np.meshgrid(np.linspace(-p, p, n), np.linspace(-q, q, n), indexing="ij")
    pts = np.zeros((gp.size, 3))
    pts[:, ip], pts[:, iq] = gp.ravel(), gq.ravel()
    hang, foot = pts.copy(), pts.copy()
    hang[:, i_n], foot[:, i_n] = thick / 2.0, -thick / 2.0
    c = ORG + np.asarray(centre, dtype=np.float64)
    return vs.VeinInput(code, name or f"Жила {code}", hang + c, foot + c, dict(params or HULL))


def A() -> vs.VeinInput:
    return slab("A", "z", 50.0, 50.0)


def B() -> vs.VeinInput:
    return slab("B", "x", 30.0, 50.0)  # (y, z) plane: ±30 along y, ±50 along z


def C() -> vs.VeinInput:
    return slab("C", "y", 30.0, 50.0)  # (z, x) plane: ±30 along z, ±50 along x → x ∈ ±50, z ∈ ±30


def run(veins: list[vs.VeinInput], **kw: Any) -> vs.VeinSystemResult:
    return vs.build_vein_system(veins, H, **kw)


def assert_closed(v: np.ndarray, t: np.ndarray, volume: float) -> None:
    assert v.dtype == np.float64 and t.dtype == np.int32
    assert meshes.edge_summary(t, len(v)) == (0, 0)
    closed, why = solids.is_closed_solid(v, t)
    assert closed, why
    signed = solids.signed_volume(v - v.mean(axis=0), t.astype(np.int64))
    assert signed > 0.0 and signed == pytest.approx(volume, rel=1e-9)  # the normals look outward


def inter_volume(a: Any, b: Any) -> float:
    _, _, report = solids.boolean_many([a, b], "intersection")
    return 0.0 if report["empty"] or report["flat"] else float(report["volume"])


def part(res: vs.VeinSystemResult, code: str) -> vs.VeinPart:
    return next(p for p in res.parts if p.code == code)


def pair(res: vs.VeinSystemResult, a: str, b: str) -> dict[str, Any]:
    return next(p for p in res.pairs if (p["a"], p["b"]) == (a, b))


# --- ТД39-1: two flat veins at 90° ---------------------------------------------------------------------------------


def test_td39_1_two_flat_veins_crossing_at_a_right_angle() -> None:
    res = run([B(), A()])  # B is the younger: it cuts A
    a, b = part(res, "A"), part(res, "B")
    va, vb = 100.0 * 100.0 * 2.0, 60.0 * 100.0 * 2.0
    common = 240.0
    assert b.raw_volume == pytest.approx(vb, rel=0.01) and a.raw_volume == pytest.approx(va, rel=0.01)
    assert pair(res, "B", "A")["volume_common"] == pytest.approx(common, rel=0.01)  # the box before the cut
    assert a.volume == pytest.approx(a.raw_volume - pair(res, "B", "A")["volume_common"], rel=1e-9)
    assert a.volume == pytest.approx(va - common, rel=0.01)  # the older vein: area × 2 − the common zone
    assert b.volume == pytest.approx(b.raw_volume, rel=1e-12) and b.cut_by == () and a.cut_by == ("B",)
    for p in (a, b):
        assert_closed(p.vertices, p.triangles, p.volume)
    assert inter_volume((a.vertices, a.triangles), (b.vertices, b.triangles)) == 0.0  # the bodies do not overlap
    assert res.common is not None
    cv, ct, cvol = res.common
    assert_closed(cv, ct, cvol)
    assert cvol == pytest.approx(a.volume + b.volume, rel=1e-9)  # the union is the sum of the volumes
    assert cvol == pytest.approx(a.raw_volume + b.raw_volume - pair(res, "B", "A")["volume_common"], rel=1e-9)


def test_the_lines_of_two_slabs_and_the_table_of_pairs() -> None:
    res = run([A(), B()])
    row = pair(res, "A", "B")
    assert row["volume_common"] == pytest.approx(240.0, rel=0.01)
    assert row["line_length"] == pytest.approx(248.0, rel=1e-6)
    assert res.stats["pairs"] == 1 and res.stats["line_length"] == row["line_length"]
    assert len(res.lines) == 1 and (res.lines[0].a, res.lines[0].b) == ("A", "B")
    lines = res.lines[0].lines
    assert len(lines) == 2 and all(line["closed"] and line["points"].dtype == np.float64 for line in lines)
    assert math.fsum(vs.line_length([line]) for line in lines) == pytest.approx(248.0, rel=1e-6)
    pts = np.concatenate([line["points"] for line in lines]) - ORG  # in project coordinates, on both surfaces
    assert np.all(np.abs(pts[:, 2]) <= 1.0 + 1e-4) and np.all(np.abs(pts[:, 0]) <= 1.0 + 1e-4)
    assert np.all(np.abs(pts[:, 1]) <= 30.0 + 1e-4)


def test_the_balance_of_the_volumes() -> None:
    for order in ([A(), B()], [B(), A()]):
        res = run(order)
        row = res.pairs[0]
        cut = sum(p.volume for p in res.parts)
        assert res.common is not None
        raw = sum(p.raw_volume for p in res.parts)
        assert cut + row["volume_common"] == pytest.approx(raw, rel=1e-9)  # Σ cut + the common zone = Σ raw
        assert cut == pytest.approx(res.common[2], rel=1e-9)  # Σ cut = the union
        assert res.stats["volume_cut"] == pytest.approx(cut, rel=1e-12)


# --- ТД39-2: the age decides who is cut ----------------------------------------------------------------------------


def test_td39_2_the_swap_of_the_age_moves_the_cut() -> None:
    first = run([A(), B()])  # A is the younger
    second = run([B(), A()])  # B is the younger
    common = first.pairs[0]["volume_common"]
    assert first.pairs[0]["volume_common"] == second.pairs[0]["volume_common"]  # before the cut: the same
    assert part(first, "A").volume == pytest.approx(part(first, "A").raw_volume, rel=1e-12)
    assert part(first, "B").volume == pytest.approx(part(first, "B").raw_volume - common, rel=1e-9)
    assert part(second, "B").volume == pytest.approx(part(second, "B").raw_volume, rel=1e-12)
    assert part(second, "A").volume == pytest.approx(part(second, "A").raw_volume - common, rel=1e-9)
    assert part(first, "B").cut_by == ("A",) and part(second, "A").cut_by == ("B",)
    assert (first.pairs[0]["a"], first.pairs[0]["b"]) == ("A", "B")
    assert (second.pairs[0]["a"], second.pairs[0]["b"]) == ("B", "A")
    assert first.common is not None and second.common is not None
    assert first.common[2] == pytest.approx(second.common[2], rel=1e-9)  # the union does not depend on the age


# --- ТД39-3: the same bits for 1, 2, 4, 8 threads and any order of the contacts ------------------------------------


def digest(res: vs.VeinSystemResult) -> tuple[Any, ...]:
    out: list[Any] = [res.pairs, res.stats, res.notes]
    for p in res.parts:
        out += [p.code, p.vertices.tobytes(), p.triangles.tobytes(), p.volume, p.raw_volume, p.cut_by, p.status]
    if res.common is not None:
        out += [res.common[0].tobytes(), res.common[1].tobytes(), res.common[2]]
    for pl in res.lines:
        out += [pl.a, pl.b] + [(x["closed"], x["points"].tobytes()) for x in pl.lines]
    return tuple(out)


def shuffled(v: vs.VeinInput, seed: int) -> vs.VeinInput:
    rng = np.random.default_rng(seed)
    return vs.VeinInput(v.code, v.name, rng.permutation(v.hanging), rng.permutation(v.footwall), v.params)


def test_td39_3_the_threads_and_the_order_of_the_contacts_do_not_change_a_bit() -> None:
    veins = [C(), A(), B()]
    base = digest(run(veins, threads=1))
    for threads in (2, 4, 8):
        assert digest(run(veins, threads=threads)) == base
    assert digest(run([shuffled(v, 7 + i) for i, v in enumerate(veins)], threads=4)) == base


# --- more than two veins -------------------------------------------------------------------------------------------


def test_three_veins_by_age() -> None:
    res = run([A(), B(), C()])  # A the youngest, C the oldest: C − A − B, B − A
    a, b, c = part(res, "A"), part(res, "B"), part(res, "C")
    assert a.cut_by == () and b.cut_by == ("A",) and c.cut_by == ("A", "B")
    assert [(p["a"], p["b"]) for p in res.pairs] == [("A", "B"), ("A", "C"), ("B", "C")]
    assert res.common is not None
    assert a.volume + b.volume + c.volume == pytest.approx(res.common[2], rel=1e-9)  # the cut bodies tile the union
    bodies = [(p.vertices, p.triangles) for p in (a, b, c)]
    for i in range(3):
        assert_closed(*bodies[i], (a, b, c)[i].volume)
        for j in range(i + 1, 3):
            assert inter_volume(bodies[i], bodies[j]) == 0.0
    # the oldest by hand: raw C minus the raw A and B
    raw = [veins.build_vein(x.hanging, x.footwall, x.params, H) for x in (A(), B(), C())]
    assert all(r is not None for r in raw)
    ra, rb, rc = ((r.vertices, r.triangles) for r in raw if r is not None)
    _, _, rep = solids.boolean_many([rc, ra, rb], "difference")
    assert c.volume == pytest.approx(rep["volume"], rel=1e-9)
    for row in res.pairs:
        assert row["volume_common"] > 0.0 and row["line_length"] > 0.0


def test_a_vein_cut_out_entirely_by_a_younger_one_has_no_body() -> None:
    inner = slab("B", "x", 20.0, 3.0, thick=1.0)  # x ∈ ±0.5, y ∈ ±20, z ∈ ±3: inside the thick vein
    thick = slab("A", "z", 50.0, 50.0, thick=10.0)  # z ∈ ±5
    res = run([thick, inner])
    b = part(res, "B")
    assert b.empty and b.status == "cut_out" and b.volume == 0.0
    assert b.raw_volume == pytest.approx(40.0 * 6.0, rel=0.01)
    assert b.cut_by == ("A",)
    assert any("целиком" in n and "B" in n for n in res.notes)
    row = pair(res, "A", "B")
    assert row["volume_common"] == pytest.approx(b.raw_volume, rel=1e-9) and row["line_length"] == 0.0
    assert res.common is not None and res.common[2] == pytest.approx(part(res, "A").volume, rel=1e-9)
    back = run([inner, thick])  # the other way round: the thin one stays whole, the thick one has a slot
    assert part(back, "B").volume == pytest.approx(part(back, "B").raw_volume, rel=1e-12)
    assert part(back, "A").volume == pytest.approx(part(back, "A").raw_volume - b.raw_volume, rel=1e-9)


def test_veins_that_do_not_meet_stay_whole_and_have_no_pair() -> None:
    far = slab("F", "z", 20.0, 20.0, centre=(300.0, 0.0, 0.0))
    res = run([A(), far])
    assert res.pairs == [] and res.lines == [] and all(p.cut_by == () for p in res.parts)
    for p in res.parts:
        assert p.volume == p.raw_volume
        v, t = solids.canonical(p.vertices, p.triangles)  # a whole body is already canonical
        assert v.tobytes() == p.vertices.tobytes() and t.tobytes() == p.triangles.tobytes()
    assert res.common is not None and res.common[2] == pytest.approx(sum(p.volume for p in res.parts), rel=1e-9)


def test_a_vein_pinched_out_everywhere_is_left_out_not_an_error() -> None:
    thin = slab("T", "x", 30.0, 50.0, thick=1.0, params={"bound": "hull", "offset": 0.0, "t_min": 5.0})
    res = run([A(), B(), thin])
    t = part(res, "T")
    assert t.empty and t.status == "pinched" and t.raw_volume == 0.0
    assert any("выклинилась целиком" in n and "T" in n for n in res.notes)
    assert all(row["a"] != "T" and row["b"] != "T" for row in res.pairs)
    assert len(res.pairs) == 1 and res.common is not None
    allgone = run([thin, slab("U", "z", 20.0, 20.0, thick=1.0, params=thin.params)])
    assert allgone.common is None and allgone.pairs == [] and all(p.empty for p in allgone.parts)
    assert any("Общего тела нет" in n for n in allgone.notes)


def test_a_missing_coordinate_is_dropped_not_zero() -> None:
    clean = run([A(), B()])
    a = A()
    hang = a.hanging.copy()
    hang[5] = (np.nan, hang[5, 1], hang[5, 2])
    res = run([vs.VeinInput(a.code, a.name, hang, a.footwall, a.params), B()])
    assert any("пропуск" in n for n in res.notes)
    assert part(res, "A").raw_volume == pytest.approx(part(clean, "A").raw_volume, rel=0.01)


# --- refusals ------------------------------------------------------------------------------------------------------


def test_the_count_and_the_codes_are_checked() -> None:
    with pytest.raises(ApiError) as e1:
        run([A()])
    assert e1.value.code == "bad_param" and "жил" in e1.value.message and e1.value.hint
    with pytest.raises(ApiError) as e2:
        run([A(), slab("A", "x", 10.0, 10.0)])
    assert e2.value.code == "bad_param" and "повторяется" in e2.value.message
    with pytest.raises(ApiError) as e3:
        run([slab(str(i), "z", 10.0, 10.0) for i in range(21)])
    assert e3.value.code == "bad_param"


def test_a_vein_without_contacts_is_named_in_the_error() -> None:
    bad = vs.VeinInput("X", "Жила икс", A().hanging[:2], A().footwall, HULL)
    with pytest.raises(ApiError) as err:
        run([A(), bad])
    e = err.value
    assert e.code == "no_contacts" and "Жила икс" in e.message and e.hint and e.extra["code"] == "X"


def test_the_tick_may_cancel() -> None:
    class Stop(Exception):
        pass

    def tick(share: float | None, text: str | None) -> None:
        if share is not None and share > 0.1:
            raise Stop

    with pytest.raises(Stop):
        run([A(), B()], tick=tick)
