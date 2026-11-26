"""The pit-by-pit report of the nested shells (подшаг 21.8; ТЗ 8.11.4; ADR 0028 decisions 16, 18, 19).

The increments ΔC_k = C_k \\ C_(k−1) at the base price RF = 1 (ore, waste, W/O, the grades, the metal, the revenue and
the costs, CF) against a per-block formula in plain Python; Σ increments = the total of C_N; O = 0 → the stripping
ratio is empty (not 0); a gap in a grade is not a zero; the worst case on the benches from the top of the model down;
the same bits with a portion of 1, 7 or 2²⁰ rows and 1–8 threads; the sub-blocked and the sparse model; the best case
at least the worst at r > 0 on a model whose CF falls from shell to shell (the property is shown on that model, not
claimed in general). The NPV itself — `test_pit_npv.py` (Т8.11-8)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry
from bf_core.blocks.restrict import Restrictor
from bf_core.mine import econ, econ_table, npv, report
from bf_core.mine import report_kernels as rk
from bf_core.mine.spec_parts import Calendar
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_blocks_report import put
from tests.test_pit_econ import (
    GRADES,
    K_GT,
    NONE,
    ROCKS,
    full_inputs,
    grid_of,
    inputs,
    opened,
    random_model,
    ref_row,
    sub_model,
)


@pytest.fixture()
def rc(pclient):  # type: ignore[no-untyped-def]
    assert pclient.post("/api/v1/projects", json={"name": "Карьер"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.store = BlobStore(pclient.project.folder)
    return pclient


def close(a: float, b: float, rel: float = 1e-9, ab: float = 1e-6) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return abs(a - b) <= max(ab, rel * max(abs(a), abs(b)))


def same_report(a: report.Report, b: report.Report, what: Any) -> None:
    assert list(a.data) == list(b.data)
    for c in a.data:
        assert a.data[c].tobytes() == b.data[c].tobytes(), (what, c)
    for c in a.benches:
        assert a.benches[c].tobytes() == b.benches[c].tobytes(), (what, c)


def run(m: Any, inp: econ.Inputs, r: Any, pit: Any, rf: Any, **kw: Any) -> tuple[econ_table.Table, report.Report]:
    tab = econ_table.build_table(m, inp, r)
    return tab, report.build_report(m, inp, r, tab, pit, rf, **kw)


# --- the reference: a block at a time, in plain Python --------------------------------------------


def ref_report(inp: econ.Inputs, vals: dict[str, np.ndarray], zc: np.ndarray, pit_of_row: dict[int, int], n_sh: int,
               nxy: int, rf1: float = 1.0) -> dict[str, Any]:  # fmt: skip
    """The increments, the cumulative sums and the benches of the model of `random_model` (10 m cubes, the rock of the
    field ROCK: 0 ore, 1 air, 2 only waste) from the pit of its rows (row → shell; air is not in it)."""
    out: dict[int, dict[str, list[float]]] = {k: {} for k in range(1, n_sh + 1)}
    per: dict[int, list[dict[str, Any]]] = {k: [] for k in range(1, n_sh + 1)}
    for i, k in pit_of_row.items():
        g = [float(vals["AU"][i]), float(vals["CU"][i])]
        rock = int(vals["ROCK"][i])
        b = ref_row(inp, rf1, g=g, dens=float(vals["SG"][i]), vol=1000.0, f=1.0, z=float(zc[i]), rock=rock)
        ore = b["ore"] == 1.0
        el = []
        for e, ge, rec in zip(inp.elements, g, inp.rocks[rock].rec, strict=True):
            fin = not math.isnan(ge)
            m = b["t"] * ge * e.k if fin else 0.0
            mr = m * rec
            el.append({"fin": fin, "t": b["t"], "tg": b["t"] * ge if fin else 0.0, "m": m, "mr": mr,
                       "gross": mr * (rf1 * e.price), "sell": mr * e.sell,
                       "roy": mr * (rf1 * e.price - e.sell) * e.royalty})  # fmt: skip
        gross = sum(x["gross"] for x in el) if ore else 0.0
        sell = sum(x["sell"] for x in el) if ore else 0.0
        roy = sum(x["roy"] for x in el) if ore else 0.0
        cf = gross - sell - roy - b["cm"] - (b["cp"] if ore else 0.0)
        per[k].append({"ore": ore, "t": b["t"], "cm": b["cm"], "cp": b["cp"] if ore else 0.0, "gross": gross,
                       "sell": sell, "roy": roy, "cf": cf, "el": el, "level": i // nxy})  # fmt: skip
    return {"per": per, "out": out}


def sums(rows: list[dict[str, Any]], n_el: int) -> dict[str, Any]:
    ore = [r for r in rows if r["ore"]]
    waste = [r for r in rows if not r["ore"]]
    s: dict[str, Any] = {"blocks": len(rows), "ore_t": math.fsum(r["t"] for r in ore),
                         "waste_t": math.fsum(r["t"] for r in waste), "revenue": math.fsum(r["gross"] for r in rows),
                         "cost_mine": math.fsum(r["cm"] for r in rows),
                         "cost_process": math.fsum(r["cp"] for r in rows),
                         "cost_sales": math.fsum(r["sell"] for r in rows), "royalty": math.fsum(r["roy"] for r in rows),
                         "cf": math.fsum(r["cf"] for r in rows), "el": []}  # fmt: skip
    for e in range(n_el):
        fin = [r["el"][e] for r in ore if r["el"][e]["fin"]]
        gt = math.fsum(x["t"] for x in fin)
        s["el"].append({"grade": math.fsum(x["tg"] for x in fin) / gt if gt > 0 else math.nan,
                        "metal": math.fsum(x["m"] for x in fin) if (fin or not ore) else math.nan,
                        "rec": math.fsum(x["mr"] for x in fin) if (fin or not ore) else math.nan})  # fmt: skip
    return s


def check_against(rep: report.Report, inp: econ.Inputs, ref: dict[str, Any], n_sh: int) -> None:
    names = [e.field for e in inp.elements]
    for k in range(1, n_sh + 1):
        inc = sums(ref["per"][k], len(names))
        cum = sums([r for j in range(1, k + 1) for r in ref["per"][j]], len(names))
        for pre, want in (("", inc), ("cum_", cum)):
            d = rep.data
            assert int(d[pre + "blocks"][k - 1]) == want["blocks"], (k, pre)
            for c in ("ore_t", "waste_t", "revenue", "cost_mine", "cost_process", "cost_sales", "royalty", "cf"):
                assert close(float(d[pre + c][k - 1]), want[c], 1e-9, 1e-6), (k, pre, c, d[pre + c][k - 1], want[c])
            for i, name in enumerate(names):
                g, m, r = report.elem_names(name)
                assert close(float(d[pre + g][k - 1]), want["el"][i]["grade"], 1e-9, 1e-9), (k, pre, g)
                assert close(float(d[pre + m][k - 1]), want["el"][i]["metal"], 1e-9, 1e-9), (k, pre, m)
                assert close(float(d[pre + r][k - 1]), want["el"][i]["rec"], 1e-9, 1e-9), (k, pre, r)
    # the benches of every n: the levels of C_n from the top down
    b = rep.benches
    for n in range(1, n_sh + 1):
        rows = [r for j in range(1, n + 1) for r in ref["per"][j]]
        levels = sorted({r["level"] for r in rows}, reverse=True)
        got = rep.bench_of(n)
        assert got["level"].tolist() == levels, n
        for lv, o, w, cf in zip(got["level"], got["ore_t"], got["waste_t"], got["cf"], strict=True):
            at = [r for r in rows if r["level"] == lv]
            s = sums(at, len(names))
            assert close(float(o), s["ore_t"], 1e-9, 1e-6) and close(float(w), s["waste_t"], 1e-9, 1e-6), (n, lv)
            assert close(float(cf), s["cf"], 1e-9, 1e-6), (n, lv)
    assert (np.diff(b["n"]) >= 0).all()


N_SH = 5


def random_case(rc: Any, seed: int = 21, n: tuple[int, int, int] = (6, 5, 4)
                ) -> tuple[Any, econ.Inputs, dict, np.ndarray]:  # fmt: skip
    mid, vals = random_model(rc, seed=seed, n=n)
    m, inp = opened(rc, mid), full_inputs()
    return m, inp, vals, m.centres()[:, 2]


def assign(vals: dict[str, np.ndarray], seed: int = 4, n_sh: int = N_SH) -> tuple[np.ndarray, dict[int, int]]:
    """A pit of the live (not air) rows: a random shell 0…N of every node; the nodes are the live rows in P order."""
    live = np.flatnonzero(vals["ROCK"] != 1)
    pit = np.random.default_rng(seed).integers(0, n_sh + 1, live.size).astype(np.int16)
    return pit, {int(i): int(k) for i, k in zip(live, pit, strict=True) if k > 0}


# --- the increments -------------------------------------------------------------------------------


def test_the_increments_the_cumulative_sums_and_the_benches_are_those_of_the_formula(rc) -> None:  # type: ignore[no-untyped-def]
    m, inp, vals, zc = random_case(rc)
    pit, by_row = assign(vals)
    rf = [0.3, 0.5, 0.8, 1.0, 1.4]
    tab, rep = run(m, inp, NONE, pit, rf)
    ref = ref_report(inp, vals, zc, by_row, N_SH, nxy=6 * 5)
    check_against(rep, inp, ref, N_SH)
    assert rep.data["k"].tolist() == [1, 2, 3, 4, 5] and rep.data["rf"].tolist() == rf  # the shells and their RF
    assert list(rep.data) == report.column_names(["AU", "CU"]) and rep.elements == ["AU", "CU"]
    # a block is ore or waste by the base price, not by the RF of its shell: both kinds are in every shell
    assert (rep.data["ore_t"] > 0).all() and (rep.data["waste_t"] > 0).all()
    # a node of the graph is one block: the blocks of the increments add up to the nodes of the pit
    assert int(rep.data["blocks"].sum()) == int((pit > 0).sum()) == rep.data["cum_blocks"][-1]


def test_the_sum_of_the_increments_is_the_total_of_c_n(rc) -> None:  # type: ignore[no-untyped-def]
    m, inp, vals, zc = random_case(rc, seed=33)
    pit, by_row = assign(vals, seed=8)
    tab, rep = run(m, inp, NONE, pit, [0.4, 0.6, 0.9, 1.0, 1.2])
    d = rep.data
    for c in ("ore_t", "waste_t", "revenue", "cost_mine", "cost_process", "cost_sales", "royalty", "cf"):
        assert close(math.fsum(d[c]), float(d["cum_" + c][-1]), 1e-12, 1e-6), c  # Σ ΔC_k = the last cumulative row
        assert d["cum_" + c][0] == d[c][0]
    for name in ("AU", "CU"):
        assert close(math.fsum(d[f"{name}:metal"]), float(d[f"cum_{name}:metal"][-1]), 1e-12, 1e-9), name
    # the total of C_N computed in one go over all the blocks of the pit
    whole = sums([r for k in range(1, N_SH + 1) for r in ref_report(inp, vals, zc, by_row, N_SH, 30)["per"][k]], 2)
    tot = rep.totals()
    assert tot["blocks"] == whole["blocks"] and close(tot["ore_t"], whole["ore_t"], 1e-9, 1e-6)
    assert close(tot["waste_t"], whole["waste_t"], 1e-9, 1e-6) and close(tot["cf"], whole["cf"], 1e-9, 1e-6)
    assert close(tot["strip"], whole["waste_t"] / whole["ore_t"], 1e-9, 1e-12)
    assert close(tot["elements"]["AU"]["grade"], whole["el"][0]["grade"], 1e-9, 1e-12)
    # CF is revenue − the costs (the same number up to the rounding) and is the sum of the values of the nodes at RF = 1
    for pre in ("", "cum_"):
        rhs = (d[pre + "revenue"] - d[pre + "cost_sales"] - d[pre + "royalty"] - d[pre + "cost_mine"]
               - d[pre + "cost_process"])  # fmt: skip
        assert np.allclose(d[pre + "cf"], rhs, rtol=1e-12, atol=1e-6)
    v1 = tab.values(1.0)
    for k in range(1, N_SH + 1):
        assert close(float(d["cf"][k - 1]), math.fsum(v1[pit == k]), 1e-9, 1e-6), k
    # the benches of n = N add up to the total of C_N, level by level cumulative over n
    last = rep.bench_of(N_SH)
    assert close(math.fsum(last["ore_t"]), tot["ore_t"], 1e-12, 1e-6)
    assert close(math.fsum(last["waste_t"]), tot["waste_t"], 1e-12, 1e-6)
    assert close(math.fsum(last["cf"]), tot["cf"], 1e-9, 1e-6)
    assert all(close(rep.bench_of(n)["cf"].sum(), float(d["cum_cf"][n - 1]), 1e-9, 1e-6) for n in range(1, N_SH + 1))


def test_no_ore_means_no_stripping_ratio_not_zero(rc) -> None:  # type: ignore[no-untyped-def]
    m, inp, vals, zc = random_case(rc, seed=17)
    live = np.flatnonzero(vals["ROCK"] != 1)
    tab_ore = econ_table.build_table(m, inp, NONE).node_ore()
    assert tab_ore.any() and (~tab_ore).any()
    # the shell 1 holds ore and waste, the shell 2 holds only waste nodes, the shell 3 is empty, the shell 4 is ore only
    pit = np.zeros(live.size, dtype=np.int16)
    pit[np.flatnonzero(~tab_ore)[:6]] = 2
    pit[np.flatnonzero(tab_ore)[:3]] = 1
    pit[np.flatnonzero(tab_ore)[3:6]] = 4
    pit[np.flatnonzero(~tab_ore)[6:9]] = 1
    _, rep = run(m, inp, NONE, pit, [0.5, 0.7, 0.9, 1.0])
    d, strip = rep.data, rep.strip()
    assert d["ore_t"][1] == 0.0 and d["waste_t"][1] > 0.0  # O = 0: the waste is there
    assert math.isnan(strip[1])  # empty: not 0 and not ∞
    assert not math.isnan(strip[0]) and strip[0] > 0.0 and strip[3] == 0.0  # O > 0, W = 0 is 0: a real zero
    assert d["blocks"].tolist() == [6, 6, 0, 3]  # the empty shell
    assert d["ore_t"][2] == d["waste_t"][2] == d["cf"][2] == d["cost_mine"][2] == 0.0
    assert math.isnan(strip[2])  # no ore, no waste: empty
    assert math.isnan(d["AU:grade"][1]) and d["AU:metal"][1] == 0.0  # no ore: no grade; the metal of no ore is 0
    assert d["cum_blocks"].tolist() == [6, 12, 12, 15]
    cum = rep.strip(cum=True)
    assert not math.isnan(cum[1]) and rep.totals()["strip"] == cum[-1]  # the first shell has ore: C_2 has a ratio
    assert d["cost_process"][1] == 0.0 and d["revenue"][1] == 0.0
    assert d["cf"][1] == -d["cost_mine"][1]  # waste: the mining only
    # a model with no ore at all in the pit: W/O is empty in the total
    only_waste = np.zeros(live.size, dtype=np.int16)
    only_waste[np.flatnonzero(~tab_ore)[:4]] = 1
    _, rep0 = run(m, inp, NONE, only_waste, [1.0])
    assert rep0.totals()["strip"] is None and rep0.totals()["elements"]["AU"]["grade"] is None


def two_element_model(rc: Any) -> tuple[Any, econ.Inputs]:
    """Four blocks of 1000 m³, ρ 2,7, one rock: AU in g/t and CU in %; b0 AU 2 / CU gap, b1 AU gap / CU 1,5, b2 AU 2 /
    CU 0,5, b3 both gaps (waste)."""
    nan = float("nan")
    vals = {"AU": np.array([2.0, nan, 2.0, nan]), "CU": np.array([nan, 1.5, 0.5, nan]), "SG": np.full(4, 2.7)}
    mid = put(rc, grid_of((4, 1, 1)), GRADES, np.arange(4, dtype=np.uint64), vals)
    cu = econ.Element("CU", econ.unit_factor("%"), 8000.0, 300.0)
    inp = inputs(elements=[econ.Element("AU", K_GT, 2000.0, 5.0), cu], rocks=[econ.Rock("Руда", 20.0, rec=[0.9, 0.8])],
                 rock_other=0, c_gl=0.0)  # fmt: skip
    return opened(rc, mid), inp


def test_a_gap_in_a_grade_is_skipped_not_taken_as_zero(rc) -> None:  # type: ignore[no-untyped-def]
    m, inp = two_element_model(rc)
    tab = econ_table.build_table(m, inp, NONE)
    assert tab.node_ore().tolist() == [True, True, True, False]  # the blocks with a gap in one element are ore
    _, rep = run(m, inp, NONE, np.array([1, 2, 1, 0]), [0.8, 1.0])
    d, t = rep.data, 2700.0
    # the shell 1 = b0 (AU 2, CU —) + b2 (AU 2, CU 0,5): the AU grade is 2 (not 4/3 …), the CU grade is 0,5 (not 0,25)
    assert d["AU:grade"][0] == 2.0 and d["CU:grade"][0] == 0.5
    assert d["AU:metal"][0] == pytest.approx(2 * t * 2.0 * K_GT, rel=1e-12)  # the metal is of the blocks with a value
    assert d["CU:metal"][0] == pytest.approx(t * 0.5 * 0.01, rel=1e-12)
    assert d["AU:rec_metal"][0] == pytest.approx(0.9 * 2 * t * 2.0 * K_GT, rel=1e-12)
    assert d["CU:rec_metal"][0] == pytest.approx(0.8 * t * 0.5 * 0.01, rel=1e-12)
    # the shell 2 = b1 (AU —, CU 1,5): there is ore and no block with an AU value: the grade and the metal are unknown
    assert d["ore_t"][1] == t and math.isnan(d["AU:grade"][1]) and math.isnan(d["AU:metal"][1])
    assert math.isnan(d["AU:rec_metal"][1]) and d["CU:grade"][1] == 1.5
    # the cumulative: AU over b0, b2 — 2 g/t; CU over b1, b2 — (0,5 + 1,5)/2 = 1 (the tonnes of b0 are not in it)
    assert d["cum_AU:grade"][1] == 2.0 and d["cum_CU:grade"][1] == pytest.approx(1.0, rel=1e-12)
    assert d["cum_AU:metal"][1] == d["AU:metal"][0]  # the shell 2 adds no AU metal
    assert tab.control.to_dict()["grade_gaps"]["AU"]["blocks"] == 2  # …and «Контроль» counts the gaps


# --- the worst case: the benches ------------------------------------------------------------------


def test_the_benches_go_from_the_top_level_down_and_hold_the_levels_of_c_n(rc) -> None:  # type: ignore[no-untyped-def]
    m, inp, vals, zc = random_case(rc, seed=29, n=(4, 4, 6))
    pit, by_row = assign(vals, seed=2)
    _, rep = run(m, inp, NONE, pit, [0.3, 0.6, 0.8, 1.0, 1.3])
    b = rep.benches
    assert list(b) == list(report.BENCH_COLUMNS) and b["n"].dtype == np.int32 and b["level"].dtype == np.int32
    for n in range(1, N_SH + 1):
        lv = rep.bench_of(n)["level"]
        assert (np.diff(lv) < 0).all() and lv.max() <= 5  # strictly from the top level (the highest index) down
        have = {i // 16 for i, k in by_row.items() if k <= n}
        assert set(lv.tolist()) == have  # only the levels that hold a block of C_n
    # C_n ⊆ C_(n+1): the ore of a level does not shrink with n
    for lvl in range(6):
        ore = [float(rep.bench_of(n)["ore_t"][rep.bench_of(n)["level"] == lvl].sum()) for n in range(1, N_SH + 1)]
        assert all(b2 >= a - 1e-9 for a, b2 in zip(ore, ore[1:], strict=False)), lvl
    ref = ref_report(inp, vals, zc, by_row, N_SH, nxy=16)
    check_against(rep, inp, ref, N_SH)


def test_the_report_of_the_stored_tables_is_the_same_report(rc) -> None:  # type: ignore[no-untyped-def]
    m, inp, vals, _ = random_case(rc, seed=6)
    pit, _ = assign(vals, seed=3)
    _, rep = run(m, inp, NONE, pit, [0.4, 0.6, 0.8, 1.0, 1.1])
    back = report.Report.from_columns(rep.elements, {c: v.tolist() for c, v in rep.data.items()},
                                      {c: v.tolist() for c, v in rep.benches.items()})  # fmt: skip
    same_report(rep, back, "round trip")
    cal = Calendar(5e5, None, 0.1)
    a, b = npv.npv(rep, cal), npv.npv(back, cal)
    assert a.best.tobytes() == b.best.tobytes() and a.worst.tobytes() == b.worst.tobytes()
    with pytest.raises(ApiError) as e:
        report.Report.from_columns(rep.elements, {c: v for c, v in rep.data.items() if c != "cf"}, rep.benches)
    assert e.value.code == "pit_internal" and "cf" in e.value.message and e.value.hint


# --- the same bits --------------------------------------------------------------------------------


def test_the_same_bits_with_any_portion_and_any_thread_count(rc, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    m, inp, vals, _ = random_case(rc, seed=9, n=(5, 4, 3))
    pit, _ = assign(vals, seed=5)
    monkeypatch.setattr(econ, "ROW_STRIP", 7)  # strips that threads can share
    tab = econ_table.build_table(m, inp, NONE)
    ref = report.build_report(m, inp, NONE, tab, pit, [0.5, 0.7, 0.9, 1.0, 1.2], chunk=1 << 20)
    for chunk in (1, 7, 1 << 20):
        for threads in (1, 2, 3, 8):
            got = report.build_report(m, inp, NONE, tab, pit, [0.5, 0.7, 0.9, 1.0, 1.2], chunk=chunk, threads=threads)
            same_report(ref, got, (chunk, threads))
    # the table of the nodes may be built with other portions and threads: the report does not change
    tab2 = econ_table.build_table(m, inp, NONE, chunk=5, threads=4)
    same_report(ref, report.build_report(m, inp, NONE, tab2, pit, [0.5, 0.7, 0.9, 1.0, 1.2]), "table")


def test_a_sub_blocked_parent_is_one_block_and_its_tonnes_are_those_of_its_rows(rc) -> None:  # type: ignore[no-untyped-def]
    mid, info = sub_model(rc)
    m, inp = opened(rc, mid), inputs(c_gl=0.2, z_ref=8.0, h_gl=2.0)
    air3 = Restrictor([], [], lambda ch: np.where(ch.p == 3, 0.0, 1.0))
    tab = econ_table.build_table(m, inp, air3)
    pit = np.array([1, 1, 2], dtype=np.int16)  # nodes: P0, P1 (two rows), P2 (four rows)
    ref = None
    for chunk in (1, 2, 3, 7, 1 << 20):  # a parent is cut between two portions
        rep = report.build_report(m, inp, air3, tab, pit, [0.8, 1.0], chunk=chunk)
        if ref is None:
            ref = rep
        same_report(ref, rep, chunk)
    assert ref is not None
    d = ref.data
    assert d["blocks"].tolist() == [2, 1]  # parents, not rows
    # the tonnes are those of the rows; the rows of a parent may be ore and waste each by itself (decision 7)
    assert close(float(d["cum_ore_t"][-1] + d["cum_waste_t"][-1]), float(tab.control.to_dict()["tonnes"]), 1e-12, 1e-6)
    rows3 = float((info["vol"][:3] * info["vals"]["SG"][:3]).sum())
    assert close(float(d["ore_t"][0] + d["waste_t"][0]), rows3, 1e-12, 1e-6)
    assert d["ore_t"][0] > 0 and d["waste_t"][0] > 0
    assert close(float(d["cf"][0]), float(tab.values(1.0)[:2].sum()), 1e-9, 1e-6)


def test_the_cells_absent_from_a_sparse_model_are_in_the_report(rc) -> None:  # type: ignore[no-untyped-def]
    grid = grid_of((2, 1, 4))
    vals = {"AU": np.array([3.0, 3.0]), "SG": np.array([2.5, 2.5]), "ROCK": np.array([0, 0], dtype=np.int32)}
    mid = put(rc, grid, [GRADES[0], GRADES[2], ROCKS], np.array([0, 4], dtype=np.uint64), vals)
    m = opened(rc, mid)

    def below(ch: Any) -> np.ndarray:
        k = geometry.row_ijk(m.grid, ch.p)[2]
        return np.clip((25.0 - 10.0 * k) / 10.0, 0.0, 1.0)

    relief = Restrictor([], [], below)
    rocks = [econ.Rock("Руда", 20.0, rec=[0.9]), econ.Rock("(пусто)", 5.0, rec=[0.0])]
    inp = inputs(rocks=rocks, rock_field="ROCK", rock_map=[1, 0, 0, 0], default_density=2.0, c_gl=0.0, fill_empty=True)
    tab = econ_table.build_table(m, inp, relief)
    assert tab.p.tolist() == [0, 1, 2, 3, 4, 5]
    pit = np.array([1, 1, 2, 2, 1, 2], dtype=np.int16)
    rep = report.build_report(m, inp, relief, tab, pit, [0.9, 1.0])
    d = rep.data
    # the file rows (0, k0) whole and (0, k2) at a half are ore of Au 3 g/t; the four absent cells are waste «(пусто)»
    assert d["ore_t"].tolist() == [3750.0, 0.0]  # P0 whole (2500 t) and P4 at a half (1250 t)
    assert close(float(d["cum_ore_t"][-1]), 3750.0) and close(float(d["cum_waste_t"][-1]), 7000.0)
    assert d["blocks"].tolist() == [3, 3] and d["waste_t"].tolist() == [2000.0, 5000.0]  # P1 (2000 t), P2, P3, P5
    assert close(float(d["cum_ore_t"][-1] + d["cum_waste_t"][-1]), float(tab.control.to_dict()["tonnes"]), 1e-12, 1e-6)
    assert math.isnan(d["AU:grade"][1]) and d["AU:grade"][0] == 3.0  # the shell 2 has no ore
    small = report.build_report(m, inp, relief, tab, pit, [0.9, 1.0], chunk=3)
    same_report(rep, small, "chunk 3")


# --- the inputs -----------------------------------------------------------------------------------


def test_a_pit_that_does_not_fit_the_graph_is_an_internal_error(rc) -> None:  # type: ignore[no-untyped-def]
    m, inp, vals, _ = random_case(rc, seed=2)
    pit, _ = assign(vals, seed=1)
    tab = econ_table.build_table(m, inp, NONE)
    rf = [0.5, 1.0, 1.2, 1.3, 1.4]
    for bad in (pit[:-1], pit.astype(np.float64), np.where(pit == 3, 9, pit).astype(np.int16), pit - 1):
        with pytest.raises(ApiError) as e:
            report.build_report(m, inp, NONE, tab, bad, rf)
        assert e.value.status == 500 and e.value.code == "pit_internal" and e.value.hint
    with pytest.raises(ApiError) as e2:
        report.build_report(m, inp, NONE, tab, pit, [])
    assert e2.value.code == "bad_param" and e2.value.extra == {"param": "rf"}


def test_a_cancel_in_the_tick_stops_the_pass(rc) -> None:  # type: ignore[no-untyped-def]
    class Stop(Exception):
        pass

    m, inp, vals, _ = random_case(rc, seed=3)
    pit, _ = assign(vals)
    tab = econ_table.build_table(m, inp, NONE)

    def stop(_p: float | None, _t: str | None = None) -> None:
        raise Stop

    with pytest.raises(Stop):
        report.build_report(m, inp, NONE, tab, pit, [0.5, 1.0, 1.1, 1.2, 1.3], tick=stop)
    seen: list[float] = []
    report.build_report(m, inp, NONE, tab, pit, [0.5, 1.0, 1.1, 1.2, 1.3], chunk=40,
                        tick=lambda p, _t=None: seen.append(0.0 if p is None else p))  # fmt: skip
    assert seen and seen == sorted(seen) and seen[-1] <= 1.0


def test_one_numba_specialization_per_kernel(rc) -> None:  # type: ignore[no-untyped-def]
    m, inp, vals, _ = random_case(rc, seed=12)
    pit, _ = assign(vals)
    rk.warm()
    before = {k: len(getattr(rk, k).signatures) for k in ("accumulate", "cumulate", "cumulate_benches")}
    run(m, inp, NONE, pit, [0.5, 0.8, 1.0, 1.2, 1.3], chunk=50)
    run(m, inp, NONE, pit.astype(np.int64), [0.5, 0.8, 1.0, 1.2, 1.3], chunk=1 << 20)  # a wider PIT, the same kernel
    after = {k: len(getattr(rk, k).signatures) for k in ("accumulate", "cumulate", "cumulate_benches")}
    assert before == after and set(after.values()) == {1}


# --- the best case against the worst case ----------------------------------------------------------


def falling_cf_model(rc: Any) -> tuple[Any, econ.Inputs, np.ndarray]:
    """7×7×6 blocks of 10 m, one rock, Au: the rich core at the bottom centre, the grade falls to the rim and up. The
    shells are the five rings of the distance from the core (shell 1 — the core): the CF per tonne of ore falls from
    shell to shell, the top levels hold mostly waste."""
    nx, ny, nz = 7, 7, 6
    i, j, k = (a.reshape(-1).astype(np.float64) for a in np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz),
                                                                      indexing="ij"))  # fmt: skip
    order = np.lexsort((i, j, k))  # P = i + nx (j + ny k): i is the fastest
    i, j, k = i[order], j[order], k[order]
    d = np.sqrt(((i - 3.0) / 3.5) ** 2 + ((j - 3.0) / 3.5) ** 2 + (k / 6.0) ** 2)
    au = 4.0 * np.exp(-2.5 * d**2)
    vals = {"AU": au, "SG": np.full(au.size, 2.7)}
    mid = put(rc, grid_of((nx, ny, nz)), [GRADES[0], GRADES[2]], np.arange(au.size, dtype=np.uint64), vals)
    pit = np.where(d <= 1.0, np.ceil(5.0 * np.maximum(d, 1e-9)), 0).astype(np.int16)
    return opened(rc, mid), inputs(rock_other=0, c_gl=0.1, z_ref=60.0), pit


def piecewise(o: np.ndarray, cf: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The cumulative CF against the cumulative ore: the vertices of the piecewise linear function of the NPV formula
    (a part spreads its CF evenly over its ore; a part with no ore is a jump at its start)."""
    return np.concatenate(([0.0], np.cumsum(o))), np.concatenate(([0.0], np.cumsum(cf)))


def test_the_best_case_is_not_below_the_worst_case_when_the_cf_falls_by_shell(rc) -> None:  # type: ignore[no-untyped-def]
    m, inp, pit = falling_cf_model(rc)
    tab, rep = run(m, inp, NONE, pit, [0.5, 0.7, 0.85, 1.0, 1.2])
    d = rep.data
    assert (d["blocks"] > 0).all() and (d["ore_t"] > 0).all() and (d["waste_t"] > 0).any()
    cf_per_t = d["cf"] / d["ore_t"]
    assert (np.diff(cf_per_t) < 0).all()  # the CF per tonne of ore falls from shell to shell
    for n in range(1, 6):
        o, _, cf = (a[:n] for a in rep.increments())
        b = rep.bench_of(n)
        xb, fb = piecewise(o, cf)
        xw, fw = piecewise(b["ore_t"], b["cf"])
        assert close(float(xb[-1]), float(xw[-1]), 1e-12, 1e-6) and close(float(fb[-1]), float(fw[-1]), 1e-9, 1e-3)
        # the theorem behind the property: if the best case has earned at least as much as the worst one by every
        # tonne of ore processed, its NPV is not lower at any r ≥ 0 (the discount factor falls with time)
        xs = np.union1d(xb, xw)
        assert (np.interp(xs, xb, fb) >= np.interp(xs, xw, fw) - 1e-6).all(), n
    for r in (0.02, 0.08, 0.25):
        res = npv.npv(rep, Calendar(2e5, None, r))
        assert (res.best >= res.worst - 1e-6).all(), (r, res.best, res.worst)
        assert (res.best[1:] > res.worst[1:]).all()  # strictly, for n ≥ 2 (the stripping of the top benches)
    zero = npv.npv(rep, Calendar(2e5, None, 0.0))  # r = 0: the same blocks, the same sum
    assert np.allclose(zero.best, zero.worst, rtol=1e-9, atol=1e-3)
    assert np.allclose(zero.best, np.cumsum(d["cf"]), rtol=1e-9, atol=1e-3)
