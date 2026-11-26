"""The economics of a block for the pit optimiser (подшаг 21.2; ТЗ 8.11.2; ADR 0028 decisions 3–8, 16).

**Т8.11-7** (a block of 1000 m³, ρ = 2,7, Au 2 g/t, rec 0,9, P = 2000 $/oz, S = 5, c_p = 20, c_m0 = 2,5:
R = 311 723,99 $, v = 250 973,99 $ to 0,01 $, the cut-off grade 0,346460 g/t to 1e−6), a gap (NaN) is not a zero,
«воздух» and «только пустая», sub-blocks = the sum of the rows, the cells absent from a sparse model, the rock of a
row (field, rules), the cost of the depth, the same bits with a portion of 1, 7, 2²⁰ rows and 1–8 threads, the error
of the integer objective ≤ N·q/2, v does not fall with RF — all against a direct per-element formula in plain
Python."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry
from bf_core.blocks.model import Model
from bf_core.blocks.restrict import Restrictor
from bf_core.db import dialect
from bf_core.mine import econ, econ_table
from bf_core.mine import econ_kernels as ek
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_blocks_report import put

OZ = econ.TROY_OUNCE_G
K_GT = econ.unit_factor("g/t")


@pytest.fixture()
def rc(pclient):  # type: ignore[no-untyped-def]
    assert pclient.post("/api/v1/projects", json={"name": "Карьер"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.store = BlobStore(pclient.project.folder)
    return pclient


def opened(c: Any, mid: int) -> Model:
    with dialect.read_tx(c.project.engine) as conn:
        return Model.open(conn, BlobStore(c.project.folder), mid)


def inputs(**kw: Any) -> econ.Inputs:
    base: dict[str, Any] = {
        "elements": [econ.Element("AU", K_GT, 2000.0, 5.0)], "rocks": [econ.Rock("Руда", 20.0, rec=[0.9])],
        "c_m0": 2.5, "z_ref": 100.0, "density_field": "SG",
    }  # fmt: skip
    return econ.Inputs(**{**base, **kw})


NONE = Restrictor([], [], None)
GRADES = [{"name": "AU", "dtype": "f8", "unit": "g/t", "role": "grade"},
          {"name": "CU", "dtype": "f8", "unit": "%", "role": "grade"},
          {"name": "SG", "dtype": "f8", "role": "density"}]  # fmt: skip
ROCKS = {"name": "ROCK", "dtype": "cat", "labels": ["Руда", "Воздух", "Пустая"]}


def grid_of(n: tuple[int, int, int], div: int = 1) -> dict[str, Any]:
    g: dict[str, Any] = {"corner": [0.0, 0.0, 0.0], "counts": list(n), "size": [10.0, 10.0, 10.0]}
    if div > 1:
        g["div"] = [div] * 3
    return g


def random_model(c: Any, seed: int = 5, n: tuple[int, int, int] = (6, 5, 4), gaps: float = 0.12) -> tuple[int, dict]:
    """A model of 10 m blocks: lognormal Au, Cu in %, SG, the rock of each block; some grades are gaps."""
    rng = np.random.default_rng(seed)
    rows = n[0] * n[1] * n[2]
    au, cu = rng.lognormal(0.0, 0.9, rows), rng.lognormal(-1.0, 0.8, rows)
    au[rng.random(rows) < gaps] = np.nan
    cu[rng.random(rows) < gaps] = np.nan
    sg = rng.uniform(2.2, 3.0, rows)
    rock = rng.integers(0, 3, rows).astype(np.int32)
    vals = {"AU": au, "CU": cu, "SG": sg, "ROCK": rock}
    mid = put(c, grid_of(n), [*GRADES, ROCKS], np.arange(rows, dtype=np.uint64), vals)
    return mid, vals


def full_inputs(**kw: Any) -> econ.Inputs:
    """Au (g/t) and Cu (%), three rocks of the field ROCK: the ore, the air, a rock that is only waste."""
    base: dict[str, Any] = {
        "elements": [econ.Element("AU", K_GT, 2000.0, 5.0, 0.02), econ.Element("CU", econ.unit_factor("%"), 8000.0,
                                                                                300.0, 0.0)],
        "rocks": [econ.Rock("Руда", 20.0, 1.5, [0.9, 0.8]), econ.Rock("Воздух", 0.0, air=True, rec=[0.0, 0.0]),
                  econ.Rock("Пустая", 5.0, rec=[0.5, 0.5], waste_only=True)],
        "rock_field": "ROCK", "rock_map": [0, 0, 1, 2], "c_m0": 2.5, "c_gl": 0.4, "h_gl": 10.0, "z_ref": 40.0,
        "density_field": "SG",
    }  # fmt: skip
    return inputs(**{**base, **kw})


def ref_row(inp: econ.Inputs, rf: float, *, g: list[float], dens: float, vol: float, f: float, z: float,
            rock: int) -> dict[str, float]:  # fmt: skip
    """The formulas of 8.11.2 element by element in plain Python."""
    r = inp.rocks[rock]
    t = vol * f * dens
    rev = 0.0
    if not r.waste_only:
        for e, ge, rec in zip(inp.elements, g, r.rec, strict=True):
            if not math.isnan(ge):
                rev += t * ge * e.k * rec * (rf * e.price - e.sell) * (1.0 - e.royalty)
    cp = t * (r.c_p + r.c_mp)
    cm = t * (inp.c_m0 + inp.c_gl * max(0.0, (inp.z_ref - z) / inp.h_gl))
    return {"t": t, "R": rev, "cp": cp, "cm": cm, "v": max(rev - cp, 0.0) - cm, "ore": float(rev - cp > 0.0)}


def close(a: float, b: float, rel: float = 1e-9, ab: float = 1e-6) -> bool:
    return abs(a - b) <= max(ab, rel * max(abs(a), abs(b)))


# --- Т8.11-7 --------------------------------------------------------------------------------------


def test_t8_11_7_block_economics_and_cutoff_grade() -> None:
    inp = inputs(density_value=2.7, density_field=None)
    e = econ.block_econ(inp, f=np.array([1.0]), vol=np.array([1000.0]), rock=np.array([0]), z=np.array([0.0]),
                        g=np.array([[2.0]]))  # fmt: skip
    assert e.state[0] == ek.ST_OK and e.t[0] == pytest.approx(2700.0, abs=0)
    assert abs(e.revenue(1.0)[0] - 311_723.99) <= 0.01  # R
    assert abs(e.value(1.0)[0] - 250_973.99) <= 0.01  # v = R − C_p − C_m = 311723,99 − 54000 − 6750
    assert e.cp[0] == 54_000.0 and e.cm[0] == 6_750.0 and e.ore[0] == 1
    assert abs(econ.grade_cutoff(inp)[0][0] - 0.346460) <= 1e-6  # type = ore, element = Au (№ 123)
    # a hair below the cut-off grade the block is waste: R − C_p < 0 ⇒ v = −C_m
    low = econ.block_econ(inp, f=np.array([1.0]), vol=np.array([1000.0]), rock=np.array([0]), z=np.array([0.0]),
                          g=np.array([[0.3464]]))  # fmt: skip
    assert low.ore[0] == 0 and low.value(1.0)[0] == -6_750.0
    # the block at the cut-off grade breaks even without the cost of mining: R − C_p ≈ 0
    at = econ.block_econ(inp, f=np.array([1.0]), vol=np.array([1000.0]), rock=np.array([0]), z=np.array([0.0]),
                         g=np.array([[econ.grade_cutoff(inp)[0][0]]]))  # fmt: skip
    assert abs(at.revenue(1.0)[0] - at.cp[0]) < 1e-6


def test_cutoff_grade_per_rock_and_element() -> None:
    inp = full_inputs()
    cg = econ.grade_cutoff(inp)
    # Au of the ore: (20 + 1,5)/(k·0,9·(2000 − 5)·0,98); Cu: the cost over k·0,8·(8000 − 300)
    assert cg[0][0] == pytest.approx(21.5 / (K_GT * 0.9 * 1995.0 * 0.98), rel=1e-14)
    assert cg[0][1] == pytest.approx(21.5 / (0.01 * 0.8 * 7700.0), rel=1e-14)
    assert cg[1] == [None, None] and cg[2] == [None, None]  # the air and the waste rock have none
    flat = inputs(elements=[econ.Element("AU", K_GT, 5.0, 5.0)])  # P = S: «нет», not ∞
    assert econ.grade_cutoff(flat) == [[None]]
    assert econ.grade_cutoff(inputs(elements=[econ.Element("AU", K_GT, 4.0, 5.0)])) == [[None]]


def test_units() -> None:
    assert econ.unit_factor("g/t") == econ.unit_factor("ppm") == 1.0 / 31.1034768
    assert econ.unit_factor("ppb") == pytest.approx(1e-3 / 31.1034768, rel=1e-15) and econ.unit_factor("%") == 0.01
    with pytest.raises(ApiError) as bad:
        econ.unit_factor("унций")
    assert bad.value.code == "bad_param" and bad.value.extra["param"] == "elements.unit"


def test_inputs_refuse_what_the_kernels_cannot_take() -> None:
    cases = [
        (inputs(c_m0=-1.0), "c_m0"), (inputs(h_gl=0.0), "h_gl"), (inputs(default_density=-2.0), "default_density"),
        (inputs(density_field=None), "density"), (inputs(rocks=[econ.Rock("Р", 20.0, rec=[1.5])]), "rocks.0.rec"),
        (inputs(rocks=[econ.Rock("Р", -1.0, rec=[0.9])]), "rocks.0.c_p"),
        (inputs(rocks=[econ.Rock("Р", 20.0, rec=[0.9, 0.9])]), "rocks.0.rec"),
        (inputs(elements=[econ.Element("AU", K_GT, 2000.0, 5.0, 1.0)]), "elements.0.royalty"),
        (inputs(elements=[econ.Element("AU", K_GT, 2000.0, -5.0)]), "elements.0.price"),
        (inputs(rock_other=3), "rocks"), (inputs(rock_field="ROCK"), "rocks"),
    ]  # fmt: skip
    for inp, param in cases:
        with pytest.raises(ApiError) as e:
            inp.check()
        assert (e.value.status, e.value.code, e.value.extra["param"]) == (422, "bad_param", param), param
        assert e.value.message and e.value.hint  # Russian text and a hint


# --- the cost of the depth, the rows against the plain formula ------------------------------------


def test_depth_cost_and_the_formula_row_by_row() -> None:
    rng = np.random.default_rng(11)
    n = 400
    inp = full_inputs(density_field="SG")
    g = np.stack([rng.lognormal(0.0, 1.0, n), rng.lognormal(-1.0, 1.0, n)])
    g[rng.random((2, n)) < 0.1] = np.nan
    dens, vol, z = rng.uniform(2.0, 3.2, n), rng.uniform(100.0, 1000.0, n), rng.uniform(0.0, 80.0, n)
    f, rock = rng.choice([0.0, 0.25, 1.0], n), rng.integers(0, 3, n).astype(np.int32)
    e = econ.block_econ(inp, f=f, vol=vol, rock=rock, z=z, g=g, dens=dens)
    for rf in (0.3, 1.0, 1.5):
        for i in range(n):
            if f[i] == 0.0 or rock[i] == 1:  # f = 0 and the «air» rock: out of the graph
                assert e.state[i] == ek.ST_AIR and e.value(rf)[i] == 0.0 and e.t[i] == 0.0
                continue
            ref = ref_row(inp, rf, g=list(g[:, i]), dens=dens[i], vol=vol[i], f=f[i], z=z[i], rock=int(rock[i]))
            assert close(e.t[i], ref["t"]) and close(e.cm[i], ref["cm"]) and close(e.cp[i], ref["cp"])
            assert close(e.revenue(rf)[i], ref["R"], 1e-9, 1e-6), (rf, i)
            assert close(e.value(rf)[i], ref["v"], 1e-9, 1e-6), (rf, i)
            if rf == 1.0 and abs(ref["R"] - ref["cp"]) > 1e-6:
                assert e.ore[i] == ref["ore"]
    # a rock that is only waste: R = 0 whatever the grade, v = −C_m
    w = e.state > 0
    only_waste = w & (rock == 2)
    assert only_waste.any() and not e.ore[only_waste].any() and (e.a[only_waste] == 0.0).all()
    assert (e.value(1.0)[only_waste] == -e.cm[only_waste]).all()
    # the cost of the depth is the term of the formula: above Z_ref only c_m0
    assert close(e.cm[w & (z > 40.0)][0] / e.t[w & (z > 40.0)][0], 2.5)


# --- a gap is not a zero; the density -------------------------------------------------------------


def test_a_gap_in_the_grade_is_not_a_zero(rc) -> None:  # type: ignore[no-untyped-def]
    grid = grid_of((3, 1, 1))
    au = np.array([np.nan, 0.0, 2.0])
    mid = put(rc, grid, GRADES[:1] + GRADES[2:], np.arange(3, dtype=np.uint64),
              {"AU": au, "SG": np.full(3, 2.5)})  # fmt: skip
    tab = econ_table.build_table(opened(rc, mid), inputs(), NONE)
    d = tab.control.to_dict()
    assert d["grade_gaps"]["AU"]["blocks"] == 1  # the NaN block only: a zero is a value
    assert d["grade_gaps"]["AU"]["tonnes"] == 2500.0  # its tonnage is not lost
    assert d["tonnes"] == 7500.0 and d["rocks"][0]["blocks"] == 3
    # both are waste, but neither is NaN: v = −C_m = −2500·2,5, and the third is ore
    assert list(tab.values(1.0)[:2]) == [-6250.0, -6250.0] and tab.node_ore().tolist() == [False, False, True]
    assert any("нет содержания AU" in w for w in tab.control.warnings())
    # the revenue terms of the NaN block are 0 and carry no metal; the element is NOT counted as known
    rows = list(econ.iter_rows(opened(rc, mid), inputs(), NONE))
    assert len(rows) == 1
    terms = econ.row_terms(rows[0], inputs())
    assert terms.m[0].tolist() == [0.0, 0.0, 2500.0 * 2.0 * K_GT] and np.isnan(rows[0].g[0, 0])
    assert terms.gross[2] == pytest.approx(terms.mrec[0, 2] * 2000.0, rel=1e-15)
    assert terms.gross[2] - terms.selling[2] == pytest.approx(rows[0].econ.revenue(1.0)[2], rel=1e-12)


def test_a_gap_of_one_element_leaves_the_revenue_of_the_other(rc) -> None:  # type: ignore[no-untyped-def]
    mid, vals = random_model(rc, seed=2, n=(4, 4, 2), gaps=0.3)
    inp = full_inputs()
    inp = econ.Inputs(**{**inp.__dict__, "rock_field": None, "rock_map": (), "rock_other": 0})  # all ore
    tab = econ_table.build_table(opened(rc, mid), inp, NONE)
    v = tab.values(1.0)
    for i in range(len(v)):
        ref = ref_row(inp, 1.0, g=[vals["AU"][i], vals["CU"][i]], dens=vals["SG"][i], vol=1000.0, f=1.0,
                      z=5.0 + 10.0 * (i // 16), rock=0)  # fmt: skip
        assert close(v[i], ref["v"], 1e-9, 1e-5), i
    gaps = tab.control.to_dict()["grade_gaps"]
    assert gaps["AU"]["blocks"] == int(np.isnan(vals["AU"]).sum()) > 0
    assert gaps["CU"]["blocks"] == int(np.isnan(vals["CU"]).sum()) > 0


def test_density_missing_is_an_error_or_the_default(rc) -> None:  # type: ignore[no-untyped-def]
    grid = grid_of((4, 1, 1))
    sg = np.array([2.5, np.nan, 2.5, -1.0])
    mid = put(rc, grid, GRADES[:1] + GRADES[2:], np.arange(4, dtype=np.uint64),
              {"AU": np.full(4, 1.0), "SG": sg})  # fmt: skip
    m = opened(rc, mid)
    with pytest.raises(ApiError) as e:
        econ_table.build_table(m, inputs(), NONE)
    assert (e.value.status, e.value.code, e.value.extra["blocks"]) == (422, "pit_density_missing", 2)
    assert "плотност" in e.value.message and e.value.hint
    tab = econ_table.build_table(m, inputs(default_density=2.0), NONE)
    assert tab.n_nodes == 4 and tab.control.to_dict()["default_density"] == {"blocks": 2, "volume": 2000.0}
    t = [r.econ.t for r in econ.iter_rows(m, inputs(default_density=2.0), NONE)][0]
    assert t.tolist() == [2500.0, 2000.0, 2500.0, 2000.0]  # a gap is not a zero: the default, not 0
    # a constant density: no field to read
    const = inputs(density_field=None, density_value=2.7)
    assert [r.econ.t for r in econ.iter_rows(m, const, NONE)][0].tolist() == [2700.0] * 4


def test_no_volume_blocks_are_out_and_counted(rc) -> None:  # type: ignore[no-untyped-def]
    fields = [*GRADES[:1], *GRADES[2:], {"name": "FRAC", "dtype": "f8", "role": "frac"}]
    mid = put(rc, grid_of((3, 1, 1)), fields, np.arange(3, dtype=np.uint64),
              {"AU": np.full(3, 1.0), "SG": np.full(3, 2.5), "FRAC": np.array([0.5, np.nan, 1.0])})  # fmt: skip
    tab = econ_table.build_table(opened(rc, mid), inputs(frac="FRAC"), NONE)
    assert tab.n_nodes == 2 and tab.control.to_dict()["no_volume"] == 1
    assert tab.node_of_row.tolist() == [0, -1, 1]
    assert [r.econ.t for r in econ.iter_rows(opened(rc, mid), inputs(frac="FRAC"), NONE)][0].tolist() == [
        1250.0, 0.0, 2500.0]  # fmt: skip


# --- «воздух», «только пустая» --------------------------------------------------------------------


def test_air_is_out_of_the_graph_and_not_a_waste(rc) -> None:  # type: ignore[no-untyped-def]
    mid, vals = random_model(rc, seed=3)
    m, inp = opened(rc, mid), full_inputs()
    tab = econ_table.build_table(m, inp, NONE)
    air = vals["ROCK"] == 1
    assert (tab.node_of_row[air] == -1).all() and (tab.node_of_row[~air] >= 0).all()
    assert tab.n_nodes == int((~air).sum()) and (tab.ore_row[air] == 0).all()
    d = tab.control.to_dict()
    assert d["air"] == int(air.sum()) and sum(r["blocks"] for r in d["rocks"]) == tab.n_nodes
    assert d["rocks"][1]["blocks"] == 0 and d["rocks"][1]["waste_tonnes"] == 0.0  # the air is not a waste rock
    # the blocks above the relief do not affect the result (Т8.11-9): other grades and densities in the air rows
    vals2 = {k: v.copy() for k, v in vals.items()}
    rng = np.random.default_rng(1)
    for k in ("AU", "CU", "SG"):
        vals2[k][air] = rng.random(int(air.sum())) * 50
    mid2 = put(rc, grid_of((6, 5, 4)), [*GRADES, ROCKS], np.arange(120, dtype=np.uint64), vals2, name="Другая")
    tab2 = econ_table.build_table(opened(rc, mid2), inp, NONE)
    for name in ("p", "seg", "a", "d", "cm", "ore", "node_of_row", "ore_row"):
        assert getattr(tab, name).tobytes() == getattr(tab2, name).tobytes(), name
    assert tab.control.to_dict() == tab2.control.to_dict()


def test_a_zero_share_of_the_restrictor_is_air_too(rc) -> None:  # type: ignore[no-untyped-def]
    mid, _ = random_model(rc, seed=4, n=(4, 4, 3))
    m = opened(rc, mid)
    half = Restrictor(["AU"], [], lambda ch: np.where(np.arange(ch.start, ch.start + ch.n) % 2 == 0, 1.0, 0.0))
    inp = full_inputs(rock_field=None, rock_map=(), rock_other=0)
    tab = econ_table.build_table(m, inp, half)
    assert tab.n_nodes == 24 and (tab.node_of_row[1::2] == -1).all()
    assert tab.control.to_dict()["air"] == 24
    part = Restrictor([], [], lambda ch: np.full(ch.n, 0.5))  # a share: the tonnes are scaled, the block stays
    t = [r.econ.t for r in econ.iter_rows(m, inp, part)][0]
    full = [r.econ.t for r in econ.iter_rows(m, inp, NONE)][0]
    assert np.array_equal(t, full * 0.5)


# --- sub-blocks: a parent is the sum of its rows --------------------------------------------------


def sub_model(c: Any) -> tuple[int, dict[str, Any]]:
    """2×2×1 parents of 4 m (div 2): P0 whole, P1 in two (ore and poor), P2 in four, P3 two rows of the air."""
    parts = {
        0: [((0, 0, 0), (2, 2, 2))],
        1: [((0, 0, 0), (1, 2, 2)), ((1, 0, 0), (1, 2, 2))],
        2: [((x, y, 0), (1, 1, 2)) for y in range(2) for x in range(2)],
        3: [((0, 0, 0), (2, 2, 1)), ((0, 0, 1), (2, 2, 1))],
    }
    rows = [(p, *s, *w) for p, ps in parts.items() for s, w in ps]
    a = np.array(rows)
    order = np.lexsort((a[:, 1], a[:, 2], a[:, 3], a[:, 0]))
    a = a[order]
    n = len(a)
    rng = np.random.default_rng(8)
    vals = {"AU": np.where(rng.random(n) < 0.5, rng.uniform(4.0, 9.0, n), rng.uniform(0.0, 0.4, n)),
            "SG": rng.uniform(2.3, 2.9, n)}  # fmt: skip
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [2, 2, 1], "size": [4.0, 4.0, 8.0], "div": [2, 2, 2]}
    mid = put(c, grid, GRADES[:1] + GRADES[2:], a[:, 0].astype(np.uint64), vals, a[:, 1:4].astype(np.uint8),
              a[:, 4:7].astype(np.uint8))  # fmt: skip
    return mid, {"a": a, "vals": vals, "vol": a[:, 4] * a[:, 5] * a[:, 6] * (2.0 * 2.0 * 4.0)}


def test_sub_blocks_sum_to_the_parent(rc) -> None:  # type: ignore[no-untyped-def]
    mid, info = sub_model(rc)
    m, vals = opened(rc, mid), info["vals"]
    air3 = Restrictor([], [], lambda ch: np.where(ch.p == 3, 0.0, 1.0))
    inp = inputs(c_gl=0.2, z_ref=8.0, h_gl=2.0)
    tab = econ_table.build_table(m, inp, air3)
    assert tab.p.tolist() == [0, 1, 2]  # P3: every row is air, so the parent is air
    assert tab.node_of_row.tolist() == [0, 1, 1, 2, 2, 2, 2, -1, -1]
    assert tab.seg.tolist() == [0, 1, 3, 7]
    zc = m.centres()[:, 2]
    for rf in (0.4, 1.0, 1.4):
        rows = [ref_row(inp, rf, g=[vals["AU"][i]], dens=vals["SG"][i], vol=info["vol"][i], f=1.0, z=zc[i], rock=0)
                for i in range(7)]  # fmt: skip
        want = [rows[0]["v"], math.fsum(r["v"] for r in rows[1:3]), math.fsum(r["v"] for r in rows[3:7])]
        assert all(close(x, y, 1e-9, 1e-6) for x, y in zip(tab.values(rf), want, strict=True)), rf
    # the decision of ore / waste is made by the row: a parent with an ore row and a waste row
    ore_rows = [ref_row(inp, 1.0, g=[vals["AU"][i]], dens=vals["SG"][i], vol=info["vol"][i], f=1.0, z=zc[i],
                        rock=0)["ore"] for i in range(7)]  # fmt: skip
    assert tab.ore_row[:7].tolist() == [int(x) for x in ore_rows] and tab.ore_row[7:].tolist() == [0, 0]
    assert any(x == 1.0 for x in ore_rows) and any(x == 0.0 for x in ore_rows)
    mixed = []
    for p in range(1, 3):
        lo, hi = int(tab.seg[p]), int(tab.seg[p + 1])
        if 0 < sum(ore_rows[lo:hi]) < hi - lo:
            mixed.append(p)
    assert mixed  # decision 7: its value is NOT max(R − C_p; 0) − C_m of the sum of its rows
    for p in mixed:
        lo, hi = tab.seg[p], tab.seg[p + 1]
        lumped = max(float((tab.a[lo:hi] - tab.d[lo:hi]).sum()), 0.0) - float(tab.cm[lo:hi].sum())
        assert abs(tab.values(1.0)[p] - lumped) > 1.0
    # the metal and the tonnes of the parent are the sums of the rows (the pass of the report)
    t = np.concatenate([r.econ.t for r in econ.iter_rows(m, inp, air3)])
    assert np.allclose(t[:7], info["vol"][:7] * vals["SG"][:7], rtol=1e-14) and (t[7:] == 0.0).all()
    assert tab.control.to_dict()["air"] == 2


def test_the_sub_blocked_model_gives_the_same_bits_for_any_portion(rc) -> None:  # type: ignore[no-untyped-def]
    mid, _ = sub_model(rc)
    m, inp = opened(rc, mid), inputs(c_gl=0.2, z_ref=8.0, h_gl=2.0)
    air3 = Restrictor([], [], lambda ch: np.where(ch.p == 3, 0.0, 1.0))
    ref = econ_table.build_table(m, inp, air3)
    for chunk in (1, 2, 3, 7):  # a parent is cut between two portions
        got = econ_table.build_table(m, inp, air3, chunk=chunk)
        for name in ("p", "seg", "a", "d", "cm", "ore", "node_of_row", "ore_row"):
            assert getattr(ref, name).tobytes() == getattr(got, name).tobytes(), (chunk, name)
        assert got.control.to_dict() == ref.control.to_dict()


# --- the cells absent from a sparse model ---------------------------------------------------------


def test_cells_absent_from_the_model_below_the_relief_are_waste(rc) -> None:  # type: ignore[no-untyped-def]
    grid = grid_of((2, 1, 4))  # cells (i, k): P = i + 2k; z of the layers 0–10, 10–20, 20–30, 30–40
    have = np.array([0, 4], dtype=np.uint64)  # (0, k0), (0, k2)
    fields = [GRADES[0], GRADES[2], ROCKS]
    mid = put(rc, grid, fields, have, {"AU": np.array([3.0, 3.0]), "SG": np.array([2.5, 2.5]),
                                       "ROCK": np.array([0, 0], dtype=np.int32)})  # fmt: skip
    m = opened(rc, mid)

    def below(ch: Any) -> np.ndarray:  # the relief at z = 25: the share of the height of the block below it
        k = geometry.row_ijk(m.grid, ch.p)[2]
        return np.clip((25.0 - 10.0 * k) / 10.0, 0.0, 1.0)

    relief = Restrictor([], [], below)
    rocks = [econ.Rock("Руда", 20.0, rec=[0.9]), econ.Rock("(пусто)", 5.0, rec=[0.0])]
    kw = {"rocks": rocks, "rock_field": "ROCK", "rock_map": [1, 0, 0, 0], "default_density": 2.0, "c_gl": 0.0}
    tab = econ_table.build_table(m, inputs(fill_empty=True, **kw), relief)
    # present: (0, k0), (0, k2) at half; absent below the relief: (1, k0), (0, k1), (1, k1), (1, k2) — above: air
    assert tab.p.tolist() == [0, 1, 2, 3, 4, 5]
    assert tab.node_of_row.tolist() == [0, 4]
    t = tab.values(1.0)  # the nodes are the cells P = 0…5
    # the absent cell: waste of the rock «(пусто)» with the default density: v = −C_m = −t·c_m0
    assert t[1] == -1000.0 * 2.0 * 2.5 and t[2] == -1000.0 * 2.0 * 2.5 and t[3] == -1000.0 * 2.0 * 2.5
    assert t[5] == -500.0 * 2.0 * 2.5  # half of the cell is below the relief
    for node, f in ((0, 1.0), (4, 0.5)):  # the cells of the file: the ore of Au 3 g/t
        ref = ref_row(inputs(**kw), 1.0, g=[3.0], dens=2.5, vol=1000.0, f=f, z=5.0, rock=0)
        assert close(t[node if node == 0 else 4], ref["v"], 1e-12, 1e-6) and ref["ore"] == 1.0
    d = tab.control.to_dict()
    assert d["empty_cells"] == {"blocks": 4, "volume": 3500.0} and d["default_density"]["blocks"] == 4
    assert d["rocks"][1] == {"name": "(пусто)", "blocks": 4, "ore_blocks": 0, "ore_tonnes": 0.0,
                             "waste_tonnes": 7000.0}  # fmt: skip
    assert any("Пустых ячеек ниже рельефа: 4" in w for w in tab.control.warnings())
    # without a relief (fill_empty off) the absent cells are air: only the file rows are nodes
    off = econ_table.build_table(m, inputs(fill_empty=False, **kw), relief)
    assert off.p.tolist() == [0, 4] and off.control.to_dict()["empty_cells"]["blocks"] == 0
    # the same bits with a small portion
    small = econ_table.build_table(m, inputs(fill_empty=True, **kw), relief, chunk=3)
    for name in ("p", "seg", "a", "d", "cm", "ore", "node_of_row"):
        assert getattr(tab, name).tobytes() == getattr(small, name).tobytes(), name


def test_an_absent_cell_with_no_default_density_is_the_error(rc) -> None:  # type: ignore[no-untyped-def]
    mid = put(rc, grid_of((2, 1, 1)), GRADES[:1] + GRADES[2:], np.array([0], dtype=np.uint64),
              {"AU": np.array([1.0]), "SG": np.array([2.5])})  # fmt: skip
    with pytest.raises(ApiError) as e:
        econ_table.build_table(opened(rc, mid), inputs(fill_empty=True), NONE)
    assert e.value.code == "pit_density_missing" and e.value.extra["blocks"] == 1


# --- the rock of a block --------------------------------------------------------------------------


def test_the_rock_by_the_field_and_by_the_rules(rc) -> None:  # type: ignore[no-untyped-def]
    n = 6
    rock = np.array([0, 1, 2, -1, 1, 0], dtype=np.int32)
    au = np.array([5.0, 0.5, 2.0, 2.0, 9.0, 0.1])
    mid = put(rc, grid_of((n, 1, 1)), [GRADES[0], GRADES[2], ROCKS], np.arange(n, dtype=np.uint64),
              {"AU": au, "SG": np.full(n, 2.5), "ROCK": rock})  # fmt: skip
    m = opened(rc, mid)
    rocks = [econ.Rock("Руда", 20.0, rec=[0.9]), econ.Rock("Бедная", 10.0, rec=[0.5]),
             econ.Rock("Прочие", 5.0, rec=[0.3])]  # fmt: skip
    by_field = inputs(rocks=rocks, rock_field="ROCK", rock_map=[2, 0, 1, 1])  # «(пусто)» → the rock 2
    got = np.concatenate([r.rock for r in econ.iter_rows(m, by_field, NONE)])
    assert got.tolist() == [0, 1, 1, 2, 1, 0]  # a gap of the category is the digit 0, as `rock_map[0]`
    rich = Restrictor(["AU"], [], lambda ch: (ch.cols["AU"] >= 4.0).astype(np.float64))
    mid_ = Restrictor(["AU"], [], lambda ch: (ch.cols["AU"] >= 1.0).astype(np.float64))
    rules = inputs(rocks=rocks, rock_rules=[(0, rich), (1, mid_)], rock_other=2)  # the first rule that passes wins
    got = np.concatenate([r.rock for r in econ.iter_rows(m, rules, NONE)])
    assert got.tolist() == [0, 2, 1, 1, 0, 2]
    # one rock: every row has it
    one = np.concatenate([r.rock for r in econ.iter_rows(m, inputs(rocks=rocks, rock_other=1), NONE)])
    assert one.tolist() == [1] * 6
    with pytest.raises(ApiError) as e:  # the map does not match the labels of the field
        list(econ.iter_rows(m, inputs(rocks=rocks, rock_field="ROCK", rock_map=[0, 1]), NONE))
    assert e.value.code == "bad_param" and e.value.extra["param"] == "rocks.map"


# --- the same bits: portions and threads ----------------------------------------------------------


def same(a: econ_table.Table, b: econ_table.Table, what: Any) -> None:
    for name in ("p", "seg", "a", "d", "cm", "ore", "node_of_row", "ore_row"):
        assert getattr(a, name).tobytes() == getattr(b, name).tobytes(), (what, name)
    assert a.control.to_dict() == b.control.to_dict(), what


def test_the_same_bits_with_any_portion_and_any_thread_count(rc, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mid, _ = random_model(rc, seed=9, n=(5, 4, 3))
    m, inp = opened(rc, mid), full_inputs()
    monkeypatch.setattr(econ, "ROW_STRIP", 7)  # strips that threads can share
    monkeypatch.setattr(econ_table, "ROW_STRIP", 3)
    ref = econ_table.build_table(m, inp, NONE, chunk=1 << 20)
    for chunk in (1, 7, 1 << 20):
        for threads in (1, 2, 3, 8):
            got = econ_table.build_table(m, inp, NONE, chunk=chunk, threads=threads)
            same(ref, got, (chunk, threads))
    for rf in (0.3, 0.85, 1.0, 1.5):
        v1 = ref.values(rf, threads=1)
        for threads in (2, 4, 8):
            assert ref.values(rf, threads=threads).tobytes() == v1.tobytes(), (rf, threads)
        assert econ_table.to_int(v1, threads=1).v.tobytes() == econ_table.to_int(v1, threads=8).v.tobytes()


def test_the_values_are_those_of_the_formula_and_do_not_fall_with_rf(rc) -> None:  # type: ignore[no-untyped-def]
    mid, vals = random_model(rc, seed=13, n=(6, 5, 4))
    m, inp = opened(rc, mid), full_inputs()
    tab = econ_table.build_table(m, inp, NONE)
    zc = m.centres()[:, 2]
    live = np.flatnonzero(vals["ROCK"] != 1)
    rfs = [round(0.30 + 0.05 * i, 2) for i in range(25)]
    prev = None
    for rf in rfs:
        v = tab.values(rf)
        want = np.array([ref_row(inp, rf, g=[vals["AU"][i], vals["CU"][i]], dens=vals["SG"][i], vol=1000.0, f=1.0,
                                 z=zc[i], rock=int(vals["ROCK"][i]))["v"] for i in live])  # fmt: skip
        assert np.allclose(v, want, rtol=1e-9, atol=1e-6), rf
        if prev is not None:
            assert (v >= prev).all(), rf  # the nested pits rest on it (ТЗ 8.11.2)
        prev = v
    assert (tab.values(0.30) <= tab.values(1.5)).all() and (tab.values(1.5) > tab.values(0.30)).any()
    # the ore flag is at RF = 1: R − C_p > 0 of the formula
    ore = np.array([ref_row(inp, 1.0, g=[vals["AU"][i], vals["CU"][i]], dens=vals["SG"][i], vol=1000.0, f=1.0, z=zc[i],
                            rock=int(vals["ROCK"][i]))["ore"] for i in live])  # fmt: skip
    assert (tab.node_ore() == (ore == 1.0)).all()


# --- the integers of the solver -------------------------------------------------------------------


@pytest.mark.parametrize("scale", [1.0, 1e3, 1e14])
def test_the_error_of_the_integer_objective_is_at_most_n_q_over_2(scale: float) -> None:
    rng = np.random.default_rng(int(scale) % 1000 + 3)
    n = 2000
    v = (rng.lognormal(0.0, 2.0, n) * rng.choice([-1.0, 1.0], n) * scale).astype(np.float64)
    iv = econ_table.to_int(v)
    assert math.isclose(iv.q, max(0.01, math.fsum(np.abs(v)) / 1e16), rel_tol=1e-12)
    assert iv.v.dtype == np.int64 and iv.error == n * iv.q / 2.0
    assert int(np.abs(iv.v).sum()) <= 1e16 + n
    for _ in range(50):  # the objective of a closure — the sum over a set of the nodes
        sel = rng.random(n) < rng.random()
        exact = math.fsum(v[sel])
        got = int(iv.v[sel].sum()) * iv.q
        assert abs(exact - got) <= sel.sum() * iv.q / 2.0 + 1e-9 * max(abs(exact), 1.0)
    pos = iv.v[iv.v > 0].sum()
    assert iv.infinity == int(pos) + 1
    assert (np.abs(iv.v * iv.q - v) <= iv.q / 2.0 + 1e-9 * np.abs(v)).all()  # each node by half a quantum


def test_the_quantum_is_a_cent_for_small_values_and_grows_for_big_ones() -> None:
    small = econ_table.to_int(np.array([1.0, -2.5, 1e6]))
    assert small.q == 0.01 and small.v.tolist() == [100, -250, 100_000_000]
    huge = econ_table.to_int(np.array([5e17, -5e17, 3.0]))
    assert huge.q == pytest.approx(100.0, rel=1e-12) and int(np.abs(huge.v).sum()) <= 1e16 + 3
    assert econ_table.to_int(np.array([0.005, -0.005, 0.01])).v.tolist() == [0, 0, 1]  # 0,5 → 0 (half to even)
    assert econ_table.to_int(np.empty(0)).v.size == 0
    with pytest.raises(ApiError):
        econ_table.to_int(np.array([1.0, np.nan]))


# --- the pass: tick, the model --------------------------------------------------------------------


class Stop(Exception):
    pass


def test_the_pass_ticks_and_a_tick_can_cancel_it(rc) -> None:  # type: ignore[no-untyped-def]
    mid, _ = random_model(rc, seed=1, n=(4, 3, 2))
    m, shares = opened(rc, mid), []
    econ_table.build_table(m, full_inputs(), NONE, chunk=5, tick=lambda p, _t=None: shares.append(p))
    assert len(shares) >= 5 and shares == sorted(shares) and 0.0 < shares[0] and shares[-1] == 1.0

    def stop(p: float | None, _t: str | None = None) -> None:
        raise Stop

    with pytest.raises(Stop):
        econ_table.build_table(m, full_inputs(), NONE, chunk=5, tick=stop)


def test_the_model_checks(rc) -> None:  # type: ignore[no-untyped-def]
    mid, _ = random_model(rc, seed=1, n=(2, 2, 2))
    m = opened(rc, mid)
    with pytest.raises(ApiError) as e:
        econ_table.build_table(m, inputs(elements=[econ.Element("ZN", K_GT, 1.0)]), NONE)
    assert e.value.status == 404  # the field is not in the model
    with pytest.raises(ApiError) as e:
        econ_table.build_table(m, inputs(elements=[econ.Element("ROCK", K_GT, 1.0)]), NONE)
    assert e.value.code == "bad_param" and e.value.extra["param"] == "elements.0.field"
    assert econ.grid_top(m) == 20.0
    tensors = {"corner": [0.0, 0.0, 0.0], "counts": [2, 1, 1], "tensors": {"x": [10.0, 20.0], "y": [10.0], "z": [10.0]}}
    mid = put(rc, tensors, GRADES[:1] + GRADES[2:], np.arange(2, dtype=np.uint64),
              {"AU": np.ones(2), "SG": np.full(2, 2.5)}, name="Переменный шаг")  # fmt: skip
    with pytest.raises(ApiError) as e:  # a variable step: the pit graph needs a regular grid (decision 9)
        econ_table.build_table(opened(rc, mid), inputs(), NONE)
    assert (e.value.status, e.value.code) == (422, "variable_grid") and "Регуляризуйте" in e.value.hint


def test_one_numba_specialization_per_kernel(rc) -> None:  # type: ignore[no-untyped-def]
    """`warm()` compiles what the pass calls: the real calls (arrays through `_w`) add no signature."""
    ek.warm()
    mid, _ = random_model(rc, seed=1, n=(2, 2, 2))
    inp = full_inputs()
    tab = econ_table.build_table(opened(rc, mid), inp, NONE)
    econ_table.to_int(tab.values(1.0))
    for rows in econ.iter_rows(opened(rc, mid), inp, NONE):
        econ.row_terms(rows, inp)
    for fn in (ek.row_econ, ek.control, ek.terms, ek.node_values, ek.abs_sum, ek.scale_round):
        assert len(fn.signatures) == 1, fn.__name__
