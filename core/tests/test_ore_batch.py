"""Д-26 for a set of holes (ТЗ 7.35, Т8.2-19; ADR 0018 §5, §8, решение 21): the table of all classes, ORE_ID and
ORE_CLS of the samples, the means of the other fields, the summary by class and domain; the invariants — MC of
an interval = Σ l·g of its samples, Σ M of the classes = the length of the sampling lines, a missing value is
not zero, the result does not depend on the order of the holes or on the number of threads."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bf_core.composites import ore_units as ou
from bf_core.composites import warm as composites_warm
from bf_core.composites.ore_intervals import READY_MODES, OreLines, OreParams, ore_intervals
from bf_core.composites.ore_set import CLASS_NAMES, OreSet, label, run_set, warm
from tests.oreutil import EXAMPLES, close, example_lines, example_params, make_lines
from tests.test_ore_simple import _random_holes


def params(**over: object) -> OreParams:
    return OreParams(**{"cutoff": 1.0, "m_min": 3.0, "m_waste_max": 3.0, "mode": "strict_excl", **over})  # type: ignore[arg-type]


def _set(seed: int, holes: int) -> tuple[list[list[tuple[float, float, float]]], OreLines, dict[str, np.ndarray]]:
    """Hard data of a set: `_random_holes` with a thickness field (0,8 of the length; some missing), domains
    (runs of codes 0–2, some samples outside) and two other fields with gaps."""
    data = _random_holes(seed, holes)
    plain = make_lines(data)
    n = len(plain.frm)
    rng = np.random.default_rng(seed + 1)
    thick = (plain.to - plain.frm) * 0.8
    thick[rng.random(n) < 0.01] = np.nan
    domain = np.repeat(rng.integers(-1, 3, n // 6 + 1), 6)[:n].astype(np.int32)
    ag = np.where(rng.random(n) < 0.2, np.nan, rng.random(n) * 50.0)
    lines = OreLines.of(plain.starts, plain.frm, plain.to, plain.grade, thick, domain)
    return data, lines, {"AG": ag, "CU": plain.grade * 0.5 + 0.1}


def _permuted(lines: OreLines, fields: dict[str, np.ndarray], order: np.ndarray) -> tuple[OreLines, dict, np.ndarray]:
    """The same holes in another order; also the rows of the samples in the new order."""
    rows = np.concatenate([np.arange(lines.starts[h], lines.starts[h + 1]) for h in order])
    starts = np.concatenate([[0], np.cumsum(np.diff(lines.starts)[order])])
    moved = OreLines.of(
        starts, lines.frm[rows], lines.to[rows], lines.grade[rows],
        None if lines.thick is None else lines.thick[rows], None if lines.domain is None else lines.domain[rows],
    )  # fmt: skip
    return moved, {name: col[rows] for name, col in fields.items()}, rows


def test_t8_2_19_invariants_in_every_mode() -> None:
    """MC of every row = Σ l·g of its samples; Σ M of the rows with NESTED = 0 = the thickness of the sampling
    lines, by class too; an ore interval begins and ends with ore samples and meets К; ORE_ID is 1…k in a hole."""
    _, lines, fields = _set(2024, 1500)
    length = lines.to - lines.frm
    for mode in READY_MODES:
        p = params(mode=mode, m_waste_max=2.0).checked()
        res = run_set(lines, p, fields=fields)
        iv = res.intervals
        w = np.where(np.isfinite(lines.thick), lines.thick, length)  # type: ignore[arg-type]
        in_line = iv.sample_state == ou.ROW_PIECE
        total = float(w[in_line].sum())
        main = iv.nested == 0
        assert close(float(iv.m[main].sum()), total, 1e-11) and close(res.summary["length"], total, 1e-11)
        assert close(float(iv.mc[main].sum()), float((w * lines.grade)[in_line].sum()), 1e-11)
        by_class = {e["cls"]: e for e in res.summary["classes"]}
        assert close(math.fsum(e["m"] for e in by_class.values() if not e["nested"]), total, 1e-11)
        assert {e["cls"] for e in by_class.values() if e["nested"]} == {"waste_in", "frame"} - (
            {"frame"} if mode == "contacts" else set()
        )
        assert np.all((res.ore_cls >= 0) == in_line) and res.ore_cls.dtype == np.int8 and res.ore_id.dtype == np.int32
        for r in iv.rows()[:4000]:
            rows = slice(r["row0"], r["row1"] + 1)
            mine = in_line[rows]
            assert close(r["mc"], float((w[rows] * lines.grade[rows])[mine].sum()))
            assert close(r["m"], float(w[rows][mine].sum())) and r["n_samples"] == int(mine.sum())
            if r["cls"] == "ore":
                assert r["basis"] in ("thickness", "mc") and np.all(res.ore_id[rows][mine] == r["ore_id"])
                if mode != "contacts":
                    assert lines.grade[r["row0"]] >= 1.0 and lines.grade[r["row1"]] >= 1.0
                    assert r["c"] >= 1.0 - 1e-9 and (r["m"] >= 3.0 - 1e-9 or r["mc"] >= 3.0 - 1e-9)
            elif not r["nested"]:
                assert np.all(res.ore_id[rows][mine] == 0)
                assert {CLASS_NAMES[c] for c in res.ore_cls[rows][mine]} == {r["cls"]}
        ore = (iv.cls == ou.CLS_ORE).nonzero()[0]
        ids = iv.ore_id[ore]
        new_hole = np.concatenate([[True], iv.hole[ore][1:] != iv.hole[ore][:-1]])
        assert np.all(ids[new_hole] == 1) and np.all(np.diff(ids)[~new_hole[1:]] == 1)
        assert by_class["ore"]["count"] == len(ore) > 300
        assert np.all(res.domain[main] == lines.domain[iv.row0[main]])  # type: ignore[index]


def test_grade_at_the_cutoff_is_ore_and_missing_is_not_zero() -> None:
    """g = Сб — ore; a sample without a grade ends the sampling line and stays without a class — were it zero,
    the two ore samples around it would be one interval with a waste sample inside."""
    hole = [(0.0, 1.0, 1.0), (1.0, 2.0, 1.0), (2.0, 3.0, 1.0), (3.0, 4.0, math.nan), (4.0, 5.0, 1.0), (5.0, 6.0, 1.0)]
    res = run_set(make_lines([hole]), params(m_min=2.0), fields={"AG": [1.0, math.nan, 3.0, 9.0, math.nan, math.nan]})
    table = res.table()
    assert [CLASS_NAMES[c] for c in table["CLASS"]] == ["ore", "ore"] and table["ORE_ID"].tolist() == [1, 2]
    assert table["FROM"].tolist() == [0.0, 4.0] and table["TO"].tolist() == [3.0, 6.0]
    assert table["C"].tolist() == [1.0, 1.0] and table["MC"].tolist() == [3.0, 2.0] and table["M"].tolist() == [3.0, 2.0]
    assert table["BASIS"].tolist() == [ou.BASIS_THICKNESS, ou.BASIS_THICKNESS] and table["FLAG"].tolist() == [0, 0]
    assert res.ore_cls.tolist() == [0, 0, 0, -1, 0, 0] and res.ore_id.tolist() == [1, 1, 1, 0, 2, 2]
    assert res.summary["missing"] == 1 and res.summary["lines"] == 2 and res.summary["length"] == 5.0
    # other fields: the sample without AG is skipped (2,0, not 4 / 3); a row without values is NaN, not 0
    assert table["AG"][0] == 2.0 and math.isnan(table["AG"][1])
    # «пустая порода до L_н» is a choice: one interval, flagged; AG of the bridged sample is its own value
    rich = [(f, t, g * 2.0) for f, t, g in hole]
    res = run_set(
        make_lines([rich]), params(m_min=2.0, missing="waste", missing_max=1.0),
        fields={"AG": [1.0, math.nan, 3.0, 9.0, math.nan, math.nan]},
    )  # fmt: skip
    table = res.table()
    assert table["TO"].tolist()[0] == 6.0 and table["FLAG"].tolist()[0] == 1 and close(table["C"][0], 10.0 / 6.0)
    assert [CLASS_NAMES[c] for c in table["CLASS"]] == ["ore", "waste_in"] and table["NESTED"].tolist() == [0, 1]
    assert close(table["AG"][0], 13.0 / 3.0) and table["AG"][1] == 9.0 and res.ore_cls.tolist() == [0, 0, 0, 2, 0, 0]


def test_table_fields_and_summary() -> None:
    """Т8.2-12 twice and Т8.2-15 as a set of three holes with domains: the columns of the table, the means of
    another field weighted by the thickness, the summary by class and by domain, the labels."""
    a, b = example_lines("T8.2-12"), example_lines("T8.2-15")
    n = 34 + 34 + 8
    grade = np.concatenate([a.grade, a.grade, b.grade])
    frm = np.concatenate([a.frm, a.frm, b.frm])
    domain = np.concatenate([np.zeros(34, np.int32), np.ones(34, np.int32), np.full(8, 2, np.int32)])
    lines = OreLines.of([0, 34, 68, 76], frm, frm + 1.0, grade, None, domain)
    ag = np.arange(n, dtype=np.float64)
    res = run_set(lines, example_params("T8.2-12", "strict_excl"), fields={"AG": ag})
    table = res.table()
    assert list(table) == [
        "HOLE", "FROM", "TO", "LENGTH", "M", "C", "MC", "CLASS", "BASIS", "N_SAMPLES", "WASTE_LEN", "NESTED",
        "ORE_ID", "FLAG", "DOMAIN", "AG",
    ]  # fmt: skip
    assert all(len(col) == len(res) for col in table.values())
    ore = table["CLASS"] == ou.CLS_ORE
    assert table["HOLE"][ore].tolist() == [0, 0, 0, 1, 1, 1, 2] and table["ORE_ID"][ore].tolist() == [1, 2, 3, 1, 2, 3, 1]
    assert table["DOMAIN"][ore].tolist() == [0, 0, 0, 1, 1, 1, 2] and table["LENGTH"][ore].tolist()[:3] == [8.0, 5.0, 5.0]
    assert close(table["C"][ore][0], 2.8375) and close(table["MC"][ore][0], 22.7)
    assert table["AG"][ore].tolist()[:3] == [6.5, 19.0, 30.0]  # the mean of the rows 3…10, 17…21, 28…32
    assert res.labels("г/т")[int(np.flatnonzero(ore)[0])] == "8,0 м @ 2,84 г/т (МС 22,7)"
    assert [text for text, is_ore in zip(res.labels("г/т"), ore, strict=True) if not is_ore] == [""] * int((~ore).sum())
    assert label(5.0, 1.16, 5.8) == "5,0 м @ 1,16 (МС 5,8)" and label(10.6, 2.452830188679245, 26.0, "%") == (
        "10,6 м @ 2,45 % (МС 26,0)"
    )
    assert label(0.25, 1.125, 0.28125, "г/т") == "0,3 м @ 1,13 г/т (МС 0,3)"  # half up, as written by hand

    by_class = {e["cls"]: e for e in res.summary["classes"]}
    one = {"count": 3, "m": 18.0, "mc": 43.0}  # Т8.2-12: 8 + 5 + 5 м; 22,7 + 14,5 + 5,8
    assert by_class["ore"]["count"] == 2 * one["count"] + 1
    assert close(by_class["ore"]["m"], 2 * one["m"] + 8.0) and close(by_class["ore"]["mc"], 2 * one["mc"] + 9.8)
    assert close(by_class["ore"]["c"], 95.8 / 44.0) and close(by_class["ore"]["m_mean"], 44.0 / 7)
    assert by_class["interlayer"]["count"] == 2 and close(by_class["interlayer"]["c"], 0.6)
    assert not by_class["ore"]["nested"] and by_class["frame"]["nested"] and by_class["waste_in"]["nested"]
    assert close(math.fsum(e["m"] for e in by_class.values() if not e["nested"]), 76.0) and res.summary["length"] == 76.0
    by_domain = {(e["domain"], e["cls"]): e for e in res.summary["domains"]}
    assert [e["domain"] for e in res.summary["domains"]] == sorted(e["domain"] for e in res.summary["domains"])
    for code in (0, 1):
        assert by_domain[code, "ore"]["count"] == 3 and close(by_domain[code, "ore"]["m"], 18.0)
        assert close(by_domain[code, "ore"]["c"], 43.0 / 18.0) and close(by_domain[code, "ore"]["m_mean"], 6.0)
        assert close(math.fsum(e["m"] for (d, _), e in by_domain.items() if d == code and not e["nested"]), 34.0)
    assert by_domain[2, "ore"]["count"] == 1 and (2, "interlayer") not in by_domain
    assert (res.summary["holes"], res.summary["samples"], res.summary["lines"]) == (3, n, 3)
    # the thickness from a field weighs the other fields as well
    half = OreLines.of([0, 34], a.frm, a.to, a.grade, np.where(np.arange(34) % 2 == 0, 0.5, 1.5))
    res = run_set(half, example_params("T8.2-12", "soft"), fields={"AG": np.arange(34.0)}, nested=False)
    row = int(np.flatnonzero(res.table()["CLASS"] == ou.CLS_ORE)[0])
    r0, r1 = int(res.intervals.row0[row]), int(res.intervals.row1[row])
    w = half.thick[r0 : r1 + 1]  # type: ignore[index]
    assert close(res.table()["AG"][row], float((w * np.arange(r0, r1 + 1)).sum() / w.sum()))
    assert not np.any(res.table()["NESTED"]) and "frame" not in {e["cls"] for e in res.summary["classes"]}
    assert res.table()["DOMAIN"].tolist() == [-1] * len(res) and res.summary["domains"] == []


def test_the_order_of_the_holes_does_not_matter() -> None:
    """Т8.2-19: the holes in another order give the same rows for every hole, the same fields of the samples
    and the same summary, to the bit."""
    _, lines, fields = _set(555, 6000)
    order = np.random.default_rng(9).permutation(len(lines.starts) - 1)
    moved, moved_fields, rows = _permuted(lines, fields, order)
    for mode in ("strict_excl", "soft", "contacts"):
        p = params(mode=mode, m_waste_max=2.0)
        one = run_set(lines, p, fields=fields)
        two = run_set(moved, p, fields=moved_fields, threads=4)
        assert one.summary == two.summary and one.summary["classes"][0]["count"] > 500
        assert two.ore_cls.tobytes() == one.ore_cls[rows].tobytes()
        assert two.ore_id.tobytes() == one.ore_id[rows].tobytes()
        # the rows of the table: by the hole of the first set, in their order inside the hole
        a, b = one.table(), two.table()
        hole = order[b["HOLE"]]  # the hole of the first set
        there = np.argsort(hole, kind="stable")
        for name in a:
            if name != "HOLE":
                assert a[name].tobytes() == b[name][there].tobytes(), name
        assert np.array_equal(a["HOLE"], hole[there])


@pytest.mark.windows
def test_threads_give_the_same_bytes() -> None:
    """Т8.2-19: 1, 2, 4 and 8 threads — the rows, the fields and the summary are the same bytes; strips of 4096
    holes, the last one is short."""
    _, lines, fields = _set(31337, 3 * 4096 + 17)
    for mode in READY_MODES:
        p = params(mode=mode, m_waste_max=2.0, missing="waste", missing_max=1.5)
        one = run_set(lines, p, fields=fields)
        assert len(one) > 50_000 and one.summary["bridged"] > 100
        for threads in (2, 4, 8):
            res = run_set(lines, p, fields=fields, threads=threads)
            assert res.intervals.iv_i.tobytes() == one.intervals.iv_i.tobytes()
            assert res.intervals.iv_f.tobytes() == one.intervals.iv_f.tobytes()
            assert res.ore_cls.tobytes() == one.ore_cls.tobytes() and res.ore_id.tobytes() == one.ore_id.tobytes()
            assert all(res.fields[name].tobytes() == one.fields[name].tobytes() for name in fields)
            assert res.domain.tobytes() == one.domain.tobytes() and res.summary == one.summary


def test_tick_cancel_and_an_empty_set() -> None:
    _, lines, fields = _set(77, 9000)
    seen: list[float] = []
    res = run_set(lines, params(), fields=fields, threads=2, tick=seen.append)
    assert isinstance(res, OreSet) and seen == sorted(seen) and seen[-1] == 1.0 and len(seen) >= 4
    assert max(f for f in seen if f <= 0.85) == 0.85  # the kernel, then the means of the fields

    class Cancelled(Exception):
        pass

    def stop(_: float) -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        run_set(lines, params(), fields=fields, threads=2, tick=stop)
    empty = run_set(OreLines.of([0], [], [], []), params(), fields={"AG": []})
    assert len(empty) == 0 and empty.summary["classes"] == [] and empty.summary["length"] == 0.0
    assert empty.table()["AG"].shape == (0,) and empty.labels() == []
    no_samples = run_set(OreLines.of([0, 0, 0], [], [], []), params())
    assert len(no_samples) == 0 and no_samples.summary["holes"] == 2


def test_warm_runs_the_kernels() -> None:
    """The warm-up of Д-26 runs every mode; it is a part of the step `composites` of the warm-up of the workers."""
    warm()
    composites_warm.warm()
    res = ore_intervals(example_lines("T8.2-16"), example_params("T8.2-16", "strict_excl"))
    assert len(res) == 3 and EXAMPLES["T8.2-16"]["expect"]["strict_excl"]["intervals"][1]["cls"] == "interlayer"


def test_sums_of_the_summary_are_exact() -> None:
    """The sums of the summary are those of `math.fsum`: exactly rounded, whatever the order of the rows."""
    from bf_core.composites.ore_set import _add_exact, _exact_total

    rng = np.random.default_rng(5)
    cases = [
        [1e100, 1.0, -1e100, 1e-100, 3.0],
        [0.1] * 10,
        [1.0, 1e-16, 1e-16, 1e-16],
        [2.0**53, 1.0, 1.0, -(2.0**53)],
        (rng.lognormal(0.0, 12.0, 5000) * rng.choice([-1.0, 1.0], 5000)).tolist(),
        [],
    ]
    for values in cases:
        for order in (values, values[::-1], sorted(values)):
            parts = np.empty(64)
            n = 0
            for x in order:
                n = _add_exact(parts, n, x)
            assert _exact_total(parts, n) == math.fsum(values)
