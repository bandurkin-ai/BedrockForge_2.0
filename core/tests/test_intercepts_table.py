"""The table «Рудные пересечения» and the warm-up of the stage (ТЗ 8.5.3, Т8.5-11, Т8.5-12; ADR 0021 §8, решения
14, 20; `intercepts/table.py`, `intercepts/warm.py`): the columns of the contract and their order, the labels of the
two acceptance examples, LEN_G and its balance, TT_G, the means of the other fields (by length, by density; a
missing value is never a zero), acceptance by m_min with the true thickness of the table, a hole without a
trajectory, the summary, one hash with 1, 2, 4 and 8 threads, `tick`, and a warm-up after which nothing compiles.

Tolerances: lengths, grades and метропроцент — 1e−12 relative (ТЗ 8.5.5); TT on a straight hole — 1e−12·l against
the expanded formula; coordinates of the middles — 1e−6 m at UTM-sized coordinates; labels and counts — exact."""

from __future__ import annotations

import hashlib
import math
import os
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from bf_core.composites import source
from bf_core.composites.intervals import Lines, build_lines
from bf_core.drillholes.desurvey import Collars, desurvey
from bf_core.intercepts import formulas as fm
from bf_core.intercepts import orient, rule, table
from bf_core.intercepts import spec as sp
from bf_core.intercepts.rule import RuleParams

NAN = math.nan
REL = 1e-12
T11 = [0.1, 1.5, 2.0, 0.2, 0.3, 1.8, 0.1, 0.1, 0.1, 3.0]  # Т8.5-11: samples of 1 m from the collar
X0, Y0, Z0 = 512_345.678, 6_234_567.123, 412.345  # a UTM position, as the data of a deposit
HEAD = ["HOLE", "FROM", "TO", "LENGTH", "TT", "TT_FLAG", "TT_SRC", "ALPHA", "DIPDIR", "HOLE_THETA", "HOLE_AZ"]
TAIL = ["LEN_G", "TT_G", "WASTE_LEN", "N_INT", "X", "Y", "Z", "INCL", "LABEL", "ID", "KIND", "MISS_LEN"]
GLOBAL = sp.Source("global", alpha=30.0, dipdir=120.0)
LEVEL = sp.Source("global", alpha=0.0, dipdir=0.0)  # a level body: TT = l·|cos θ|


def samples(*holes: Any) -> tuple[Lines, np.ndarray]:
    """Samples of 1 m from the collar: the grades of the hole 0, of the hole 1, …"""
    ids = np.concatenate([np.full(len(g), k, dtype=np.int64) for k, g in enumerate(holes)])
    frm = np.concatenate([np.arange(len(g), dtype=np.float64) for g in holes])
    lines = build_lines(ids, frm, frm + 1.0)
    return lines, lines.take(np.concatenate([np.asarray(g, dtype=np.float64) for g in holes]))


def scene() -> Any:
    """The trajectories of four holes on UTM-sized coordinates: 0 — vertical; 1 — θ = 60°, Aс = 90°; 2 — no
    collar coordinates: no trajectory; 3 — vertical, 300 m to the east."""
    x = np.array([0.0, 100.0, NAN, 300.0]) + X0
    return desurvey(
        Collars(np.arange(4), x, np.full(4, Y0), np.full(4, Z0), np.full(4, 200.0),
                azimuth=[NAN, 90.0, 0.0, NAN], dip=[NAN, -30.0, -90.0, NAN]),
        n_holes=4,
    )  # fmt: skip


def orienting(*chain: Any, traj: Any = None, eps: float = 0.1, step: float = 1.0) -> table.Orienting:
    """The holes 0…3 of `scene` (their keys 101…104) with a chain of sources."""
    traj = scene() if traj is None else traj
    n = traj.n_holes
    params = SimpleNamespace(orientation=list(chain), eps=eps, step=step)
    return table.Orienting(params, orient.Inputs(traj), np.arange(n) + 101, np.arange(n))


def col(t: table.InterceptTable, name: str) -> list[Any]:
    return t.column(name).tolist()  # type: ignore[no-any-return]


def digest(t: table.InterceptTable) -> str:
    h = hashlib.sha256()
    for a in (t.hole, t.frm, t.to):
        h.update(np.ascontiguousarray(a).tobytes())
    for c in t.columns:
        h.update(c.name.encode())
        if c.kind == "text":
            h.update("\n".join(c.values.tolist()).encode())
        else:
            h.update(np.ascontiguousarray(c.values, dtype=np.float64).tobytes())
    return h.hexdigest()


# --- Т8.5-11, Т8.5-12 ----------------------------------------------------------------------------------------


def test_t8_5_11_columns_and_label() -> None:
    """Т8.5-11: one intercept 1–6 m, 5 m @ 1,16, метропроцент 5,8; 9–10 is dropped. The columns are those of the
    contract, in its order; without an orientation TT is empty (never 0), TT_SRC «нет», TT_FLAG 2."""
    lines, g = samples(T11)
    t = table.run(lines, g, RuleParams(1.0, l_min=2.0, waste_max=2.0), name="AU")
    assert t.names() == [*HEAD, "AU", *TAIL] == table.column_names("AU")
    assert (t.n, t.hole.tolist(), t.frm.tolist(), t.to.tolist()) == (1, [0], [1.0], [6.0])
    assert [col(t, name)[0] for name in ("LENGTH", "AU", "LEN_G")] == pytest.approx([5.0, 1.16, 5.8], rel=REL)
    assert (col(t, "WASTE_LEN"), col(t, "MISS_LEN"), col(t, "N_INT")) == ([2.0], [0.0], [5.0])
    assert (col(t, "ID"), col(t, "INCL"), col(t, "KIND")) == ([1.0], [0.0], ["основное"])
    assert col(t, "LABEL") == ["5,0 м @ 1,16"]
    assert (col(t, "TT_FLAG"), col(t, "TT_SRC")) == ([2.0], ["нет"])
    for name in ("TT", "TT_G", "ALPHA", "DIPDIR", "HOLE_THETA", "HOLE_AZ", "X", "Y", "Z"):
        assert np.isnan(t.column(name)).all(), name
    assert t.outcome is not None and t.outcome.main.n == 2  # 9–10 is a candidate that was dropped
    assert t.outcome.main_reason.tolist() == [rule.REASON_OK, rule.REASON_SHORT]
    s = t.summary
    assert (s["rows"], s["intercepts"], s["included"], s["excluded"]) == (1, 1, 0, 0)
    assert (s["length"], s["waste_len"], s["miss_len"], s["tt"]) == (pytest.approx(5.0, rel=REL), 2.0, 0.0, None)
    assert s["len_g"] == pytest.approx(5.8, rel=REL) and s["mean"] == pytest.approx(1.16, rel=REL)
    assert s["src"] == {"нет": 1} and s["flags"] == {"1": 0, "2": 1, "4": 0, "8": 0}


def test_units_and_kinds_of_the_columns() -> None:
    lines, g = samples(T11)
    t = table.run(lines, g, RuleParams(1.0), name="AU", unit=" г/т ", fields={"CU": lines.frm}, units={"CU": "%"},
                  roles={"CU": "component"})  # fmt: skip
    assert col(t, "LABEL") == ["5,0 м @ 1,16 г/т"]
    by_name = {c.name: c for c in t.columns}
    assert [c.name for c in t.columns] == [*HEAD[3:], "AU", "CU", *TAIL]
    assert {by_name[n].unit for n in ("LENGTH", "TT", "WASTE_LEN", "MISS_LEN", "X", "Y", "Z")} == {"м"}
    assert {by_name[n].unit for n in ("ALPHA", "DIPDIR", "HOLE_THETA", "HOLE_AZ")} == {"°"}
    assert (by_name["AU"].unit, by_name["AU"].role, by_name["CU"].unit, by_name["CU"].role) == (
        "г/т", "component", "%", "component",
    )  # fmt: skip
    assert by_name["LEN_G"].unit == by_name["TT_G"].unit == "м·г/т"
    assert {c.name for c in t.columns if c.kind == "text"} == {"TT_SRC", "LABEL", "KIND"}
    for c in t.columns:
        assert c.values.shape == (t.n,)
        assert c.values.dtype == (object if c.kind == "text" else np.float64), c.name
    assert col(t, "CU") == pytest.approx([3.0], rel=REL)  # the mean FROM of the samples 1…5


def test_t8_5_12_included() -> None:
    """Т8.5-12: c2 = 1,5 — «в том числе 1–3 м: 2 м @ 1,75»; 5–6 is dropped. The row follows its main intercept,
    INCL is the ID of it; the sums of the summary are of the main rows only."""
    lines, g = samples(T11)
    t = table.run(lines, g, RuleParams(1.0, l_min=2.0, waste_max=2.0, include_cutoff=1.5), name="AU")
    assert (t.frm.tolist(), t.to.tolist()) == ([1.0, 1.0], [6.0, 3.0])
    assert col(t, "KIND") == ["основное", "в том числе"]
    assert (col(t, "ID"), col(t, "INCL")) == ([1.0, 2.0], [0.0, 1.0])
    assert col(t, "LABEL") == ["5,0 м @ 1,16", "в т. ч. 2,0 м @ 1,75"]
    assert col(t, "AU") == pytest.approx([1.16, 1.75], rel=REL)
    assert col(t, "LEN_G") == pytest.approx([5.8, 3.5], rel=REL)
    assert (col(t, "N_INT"), col(t, "WASTE_LEN")) == ([5.0, 2.0], [2.0, 0.0])
    assert t.outcome is not None and t.outcome.sub is not None
    assert t.outcome.sub_reason.tolist() == [rule.REASON_OK, rule.REASON_SHORT]  # 5–6: 1 m of 1,8
    s = t.summary
    assert (s["rows"], s["intercepts"], s["included"]) == (2, 1, 1)
    assert s["length"] == pytest.approx(5.0, rel=REL) and s["len_g"] == pytest.approx(5.8, rel=REL)


# --- labels --------------------------------------------------------------------------------------------------


def test_label_as_the_spec_writes_it() -> None:
    """«12,0 м @ 2,10 г/т (ИМ 9,8 м)»; no TT — no brackets; bit 1 — «вдоль тела»; «в т. ч. » for an included
    row; the rounding of the labels of Э12 — half up on the shortest notation of the number."""
    assert table.label(12.0, 2.1, 9.8, unit="г/т") == "12,0 м @ 2,10 г/т (ИМ 9,8 м)"
    assert table.label(12.0, 2.1, 0.3, flag=1, unit="г/т") == "12,0 м @ 2,10 г/т (ИМ 0,3 м, вдоль тела)"
    assert table.label(12.0, 2.1, 0.0, flag=1) == "12,0 м @ 2,10 (ИМ 0,0 м, вдоль тела)"  # Т8.5-3: m = 0
    assert table.label(12.0, 2.1, NAN, flag=2, unit="г/т") == "12,0 м @ 2,10 г/т"
    assert table.label(12.0, 2.1, NAN, flag=8, kind=rule.KIND_INCL, unit="%") == "в т. ч. 12,0 м @ 2,10 %"
    assert table.label(3.0, 1.5, 2.6, flag=4 | 1, kind=rule.KIND_INCL) == "в т. ч. 3,0 м @ 1,50 (ИМ 2,6 м, вдоль тела)"
    assert table.label(0.05, 2.675, 0.25) == "0,1 м @ 2,68 (ИМ 0,3 м)"  # the doubles are below the halves
    assert table.label(2.45, 1.005) == "2,5 м @ 1,01"
    assert table.label(1e9, 12_345_678.125, 1e12) == "1000000000,0 м @ 12345678,13 (ИМ 1000000000000,0 м)"
    assert table.label(5.0, 1.16, unit=None) == "5,0 м @ 1,16"


def test_labels_of_many_rows_are_the_labels_of_each() -> None:
    rng = np.random.default_rng(15)
    n = 20_000
    length = np.round(rng.uniform(0.0, 300.0, n), 2)  # halves of the first decimal among them
    grade = rng.lognormal(0.0, 2.0, n)
    grade[:2000] = np.round(grade[:2000], 3)  # decimals whose third digit is a 5 among them
    tt = np.where(rng.random(n) < 0.3, NAN, length * rng.random(n))
    flag = rng.choice([0, 1, 2, 4, 5, 8, 9], n).astype(np.uint8)
    kind = rng.integers(0, 2, n).astype(np.int8)
    hard = [(0.05, 2.675, 0.25), (2.45, 1.005, 0.95), (1e9, 3e9, 5.0), (7.0, -0.004, NAN), (1e-30, 1e30, 1e-30),
            (0.0, 0.0, 0.0), (999999999.95, 0.995, 0.05), (1.0, 1e9, -0.0)]  # fmt: skip
    for k, (a, b, c) in enumerate(hard):
        length[k], grade[k], tt[k] = a, b, c
    got = table.labels(length, grade, tt, flag, kind, "г/т")
    want = [table.label(length[r], grade[r], tt[r], int(flag[r]), int(kind[r]), "г/т") for r in range(n)]
    assert got == want
    assert table.labels(length[:50], grade[:50], tt[:50], flag[:50], kind[:50], "") == [
        table.label(length[r], grade[r], tt[r], int(flag[r]), int(kind[r])) for r in range(50)
    ]
    assert table.labels([], [], [], [], []) == []


# --- the true thickness and the middles ----------------------------------------------------------------------


def test_orientation_fills_tt_angles_and_middles() -> None:
    """A vertical hole, an inclined one and one without a trajectory under a global plane α = 20°, Aп = 270°:
    TT against the expanded formula of ТЗ 8.5.1, TT_G = TT·g, the middle on the path, the label with «ИМ»;
    no trajectory — TT, the angles of the hole and X, Y, Z are empty (bit 8), never 0."""
    lines, g = samples(T11, T11, T11)
    west = sp.Source("global", alpha=20.0, dipdir=270.0)
    t = table.run(lines, g, RuleParams(1.0), name="AU", unit="г/т", orienting=orienting(west))
    assert t.hole.tolist() == [0, 1, 2] and t.frm.tolist() == [1.0] * 3 and t.to.tolist() == [6.0] * 3
    upright = 5.0 * math.cos(math.radians(20.0))
    inclined = float(fm.tt_expanded(5.0, 20.0, 270.0, 60.0, 90.0))  # against the dip: l·sin(α + β) = 5·sin 50°
    tt = t.column("TT")
    assert inclined == pytest.approx(5.0 * math.sin(math.radians(50.0)), rel=REL)
    assert tt[:2] == pytest.approx([upright, inclined], abs=5.0 * REL) and math.isnan(tt[2])
    assert col(t, "TT_FLAG") == [0.0, 0.0, 8.0]
    assert col(t, "TT_SRC") == ["глобальная"] * 3
    assert (col(t, "ALPHA"), col(t, "DIPDIR")) == ([20.0] * 3, [270.0] * 3)  # as given, bit for bit
    theta, az = t.column("HOLE_THETA"), t.column("HOLE_AZ")
    assert theta[:2] == pytest.approx([0.0, 60.0], abs=1e-9) and az[1] == pytest.approx(90.0, abs=1e-9)
    assert math.isnan(theta[2]) and math.isnan(az[2])
    grade = t.column("AU")
    assert t.column("TT_G")[:2] == pytest.approx((tt * grade)[:2], rel=REL) and math.isnan(t.column("TT_G")[2])
    s60, c60 = math.sin(math.radians(60.0)), math.cos(math.radians(60.0))
    xyz = np.column_stack([t.column(n) for n in "XYZ"])
    assert xyz[0] == pytest.approx([X0, Y0, Z0 - 3.5], abs=1e-6)
    assert xyz[1] == pytest.approx([X0 + 100.0 + 3.5 * s60, Y0, Z0 - 3.5 * c60], abs=1e-6)
    assert np.isnan(xyz[2]).all()
    assert col(t, "LABEL") == ["5,0 м @ 1,16 г/т (ИМ 4,7 м)", "5,0 м @ 1,16 г/т (ИМ 3,8 м)", "5,0 м @ 1,16 г/т"]
    s = t.summary
    assert s["tt"] == pytest.approx(upright + inclined, rel=REL)
    assert s["src"] == {"глобальная": 3} and s["flags"] == {"1": 0, "2": 0, "4": 0, "8": 1}
    assert any("без траектории" in line for line in t.journal)


def test_hole_almost_along_the_body_is_marked_not_dropped() -> None:
    """Т8.5-3 on the table: α = 30°, θ = 60°, Aс = Aп — TT = 0 with bit 1; the row stays, its label says so."""
    lines, g = samples([0.1], T11)
    along = sp.Source("global", alpha=30.0, dipdir=90.0)
    t = table.run(lines, g, RuleParams(1.0), name="AU", orienting=orienting(along))
    assert t.hole.tolist() == [1]
    assert col(t, "TT") == pytest.approx([0.0], abs=5.0 * REL) and col(t, "TT_FLAG") == [1.0]
    assert col(t, "LABEL") == ["5,0 м @ 1,16 (ИМ 0,0 м, вдоль тела)"]
    assert col(t, "TT_G") == pytest.approx([0.0], abs=1e-11)  # a zero that was computed, not a missing value


def test_acceptance_by_true_thickness() -> None:
    """`min_by = tt` under a level body (TT = l·cos θ), m_min = 2 м: 3 m in a vertical hole pass (TT 3), 3 m at
    θ = 60° do not (TT 1,5), 4 m at θ = 60° do — TT = m_min is enough; a hole without a trajectory has no TT:
    its 3 m are checked by the length with the same number. TT of the table is the TT the acceptance was given."""
    ore = [0.1, 0.1, 2.0, 2.0, 2.0, 0.1]
    lines, g = samples(ore, [*ore, 2.0, 2.0, 2.0, 2.0], ore)
    o = orienting(LEVEL)
    by_tt = RuleParams(1.0, l_min=2.0, waste_max=0.5, min_by="tt")
    t = table.run(lines, g, by_tt, name="AU", orienting=o)
    assert list(zip(t.hole.tolist(), t.frm.tolist(), t.to.tolist(), strict=True)) == [
        (0, 2.0, 5.0), (1, 6.0, 10.0), (2, 2.0, 5.0),
    ]  # fmt: skip
    tt = t.column("TT")
    assert tt[:2] == pytest.approx([3.0, 2.0], abs=4.0 * REL) and math.isnan(tt[2])
    assert np.array_equal(tt, t.rows.tt, equal_nan=True)
    assert col(t, "TT_FLAG") == [0.0, 0.0, 8.0]
    assert t.outcome is not None
    assert t.outcome.main_reason.tolist() == [rule.REASON_OK, rule.REASON_THIN, rule.REASON_OK, rule.REASON_OK]
    assert any("приняты по длине" in line and "— 1" in line for line in t.journal)
    # by the length all four pass, and the table still carries their TT
    by_len = table.run(lines, g, RuleParams(1.0, l_min=2.0, waste_max=0.5), name="AU", orienting=o)
    assert by_len.n == 4 and by_len.column("TT")[:3] == pytest.approx([3.0, 1.5, 2.0], abs=4.0 * REL)
    assert not any("приняты по длине" in line for line in by_len.journal)
    # m_min above every TT: only the hole without a trajectory is left (3 m ≥ 2,5 m by its length)
    thin = table.run(lines, g, RuleParams(1.0, l_min=3.5, waste_max=0.5, min_by="tt"), name="AU", orienting=o)
    assert thin.n == 0 and thin.summary["mean"] is None
    # no orientation at all: every candidate is checked by its length; the journal says so
    blind = table.run(lines, g, by_tt, name="AU")
    assert blind.n == 4 and col(blind, "TT_FLAG") == [2.0] * 4
    assert any("приняты по длине" in line for line in blind.journal)


def test_orienting_of_a_table_snapshot() -> None:
    """`Orienting.of(spec, src, st)` — as the job has them: the holes of the lines are the places in `src.holes`
    (by name), their keys `dh_holes.id` in any order, the trajectories by ascending id. A domain field of the
    samples gives an intercept the plane of the code with the greatest length inside it."""
    traj = scene()
    st = SimpleNamespace(hole_ids=np.array([11, 22, 33, 44], dtype=np.int64), traj=traj)
    grades = [0.1, 2.0, 2.0, 2.0, 2.0, 0.1]
    codes, names = source.text_codes(np.array(["A", "A", "B", "B", "B", "B"] * 2 + ["A"] * 6, dtype=object))
    src = source.Lines(
        set_id=1, table_id=10, name="Пробы", name_key="пробы", token="t", rev=1, kind="assay", fields={},
        row_ids=np.arange(1, 19, dtype=np.int64), holes=["А-1", "Б-2", "В-3"],
        hole_db_ids=np.array([44, 11, 22], dtype=np.int64), starts=np.array([0, 6, 12, 18], dtype=np.int64),
        from_m=np.tile(np.arange(6.0), 3), to_m=np.tile(np.arange(6.0) + 1.0, 3),
        numbers={"AU": np.array(grades * 3)}, codes={"DOM": codes}, labels={"DOM": names},
    )  # fmt: skip
    lines = build_lines(src.hole_of().astype(np.int64), src.from_m, src.to_m)
    d = sp.cj.DomainSpec(10, "Пробы", "DOM", "text")
    by_code = sp.Source("domain", domain=d, attitudes=(sp.Attitude("A", 0.0, 0.0), sp.Attitude("B", 90.0, 90.0)))
    spec = SimpleNamespace(orientation=[by_code], eps=0.1, step=1.0)
    o = table.Orienting.of(spec, src, st)
    assert o.keys.tolist() == [44, 11, 22] and o.dense.tolist() == [3, 0, 1]
    t = table.run(lines, lines.take(src.numbers["AU"]), RuleParams(1.0), name="AU", orienting=o)
    assert t.hole.tolist() == [0, 1, 2] and t.frm.tolist() == [1.0] * 3 and t.to.tolist() == [5.0] * 3
    assert col(t, "TT_SRC") == ["домен"] * 3
    # «А-1», «Б-2» — the vertical holes 3 and 0: 3 of their 4 m are «B», a vertical plane; «В-3» — the hole at
    # θ = 60° to the east, all «A», a level plane: TT = 4·cos 60°
    assert col(t, "ALPHA") == [90.0, 90.0, 0.0]
    assert t.column("TT") == pytest.approx([0.0, 0.0, 2.0], abs=4.0 * REL)
    assert col(t, "TT_FLAG") == [1.0, 1.0, 0.0]
    assert t.column("X") == pytest.approx([X0 + 300.0, X0, X0 + 100.0 + 3.0 * math.sin(math.radians(60.0))], abs=1e-6)
    thin = table.run(lines, lines.take(src.numbers["AU"]), RuleParams(1.0, l_min=1.5, min_by="tt"), orienting=o)
    assert thin.hole.tolist() == [2] and thin.rows.tt.tolist() == col(thin, "TT")  # 0 m along the body: too thin
    with pytest.raises(ValueError, match="hole"):
        o.rows(build_lines(np.array([5]), np.array([0.0]), np.array([1.0])))


# --- the other fields, the density, the balance --------------------------------------------------------------


def test_other_fields_are_means_and_a_missing_value_is_never_zero() -> None:
    """A missing grade inside an intercept is waste with g = 0 for the grade of the calculation (ТЗ 8.5.3) —
    and no value for the other fields: they are means over the samples that have one; none — empty."""
    au = [3.0, 1.5, NAN, 2.0, 0.1, 0.1]
    lines, g = samples(au)
    cu = np.array([1.0, NAN, 5.0, 3.0, 9.0, 9.0])
    ag = np.array([NAN, NAN, NAN, NAN, 7.0, 7.0])
    rho = np.array([2.0, 4.0, NAN, 2.0, 3.0, 3.0])
    t = table.run(lines, g, RuleParams(1.0), name="AU", fields={"CU": cu, "AG": ag, "AU": cu})
    assert t.names() == [*HEAD, "AU", "CU", "AG", *TAIL]  # the grade of the calculation is not given twice
    assert (t.frm.tolist(), t.to.tolist()) == ([0.0], [4.0])
    assert (col(t, "MISS_LEN"), col(t, "WASTE_LEN"), col(t, "N_INT")) == ([1.0], [1.0], [4.0])
    assert col(t, "AU") == pytest.approx([6.5 / 4.0], rel=REL)
    assert col(t, "LEN_G") == pytest.approx([6.5], rel=REL)
    assert col(t, "CU") == pytest.approx([3.0], rel=REL)
    assert math.isnan(t.column("AG")[0])
    # by density: Σ l·ρ·g / Σ l·ρ; a sample without one takes the mean density of the row, 8/3
    w = table.run(lines, g, RuleParams(1.0), name="AU", fields={"CU": cu, "AG": ag}, density=rho)
    assert col(w, "AU") == pytest.approx([16.0 / (8.0 + 8.0 / 3.0)], rel=REL)
    assert col(w, "CU") == pytest.approx([(1.0 * 2.0 + 3.0 * 2.0 + 5.0 * 8.0 / 3.0) / (4.0 + 8.0 / 3.0)], rel=REL)
    assert math.isnan(w.column("AG")[0])
    assert w.column("LEN_G")[0] == w.column("LENGTH")[0] * w.column("AU")[0]  # LENGTH·g, g — by density
    assert w.summary["mean"] == pytest.approx(1.5, rel=REL) and w.summary["len_g"] == pytest.approx(6.0, rel=REL)


def test_a_field_named_as_a_column_of_the_table_gets_its_own_name() -> None:
    lines, g = samples(T11)
    t = table.run(lines, g, RuleParams(1.0), name="TT", fields={"length": lines.frm, "LABEL_SRC": lines.to})
    assert t.names() == [*HEAD, "TT_SRC_2", "length_SRC", "LABEL_SRC", *TAIL]
    assert len({n.casefold() for n in t.names()}) == len(t.names())
    assert col(t, "TT_SRC_2") == pytest.approx([1.16], rel=REL)
    assert table.column_names("TT", ["length", "LABEL_SRC"]) == t.names()


def hard_set(n_holes: int, seed: int) -> SimpleNamespace:
    """Holes of 5–40 samples of 0,5–2 m with stretches without samples, missing grades, densities and values, a
    quarter of the holes barren, a few without a trajectory; UTM-sized collars, any azimuth, dips −90…−25°."""
    rng = np.random.default_rng(seed)
    counts = rng.integers(5, 41, n_holes)
    n = int(counts.sum())
    hole = np.repeat(np.arange(n_holes, dtype=np.int64), counts)
    first = np.concatenate(([0], np.cumsum(counts)[:-1]))
    length = rng.choice([0.5, 1.0, 1.0, 1.5, 2.0], n)
    gap = np.where(rng.random(n) < 0.03, rng.choice([0.25, 1.0, 3.0], n), 0.0)
    gap[first] = rng.choice([0.0, 2.0, 37.5], n_holes)
    end = np.cumsum(length + gap)
    to = end - np.repeat(end[first] - (length + gap)[first], counts)
    frm = to - length
    au = rng.lognormal(-0.3, 1.2, n) * np.repeat(rng.random(n_holes) > 0.25, counts)
    au[rng.random(n) < 0.03] = NAN
    rho = np.where(rng.random(n) < 0.1, NAN, rng.normal(2.7, 0.2, n))
    cu = np.where(rng.random(n) < 0.2, NAN, rng.random(n))
    ag = np.where(rng.random(n) < 0.6, NAN, rng.lognormal(1.0, 1.0, n))
    order = rng.permutation(n)  # the rows of the table come in any order
    lines = build_lines(hole[order], frm[order], to[order])
    x = X0 + rng.uniform(0.0, 5000.0, n_holes)
    x[rng.random(n_holes) < 0.01] = NAN
    traj = desurvey(
        Collars(np.arange(n_holes), x, Y0 + rng.uniform(0.0, 5000.0, n_holes), Z0 + rng.uniform(-50.0, 50.0, n_holes),
                np.full(n_holes, 200.0), azimuth=rng.uniform(0.0, 360.0, n_holes),
                dip=rng.uniform(-90.0, -25.0, n_holes)),
        n_holes=n_holes,
    )  # fmt: skip
    cols = {k: lines.take(v[order]) for k, v in {"au": au, "rho": rho, "cu": cu, "ag": ag}.items()}
    return SimpleNamespace(lines=lines, traj=traj, n_holes=n_holes, **cols)


def test_balance_of_len_g() -> None:
    """Σ LEN_G of the main rows is the метропроцент of the samples inside them, Σ l·g (a missing grade adds
    nothing); LEN_G = LENGTH·g of the row; the «в том числе» rows lie inside their main row and add nothing to
    the sums of the summary. With a density g is Σ l·ρ·g / Σ l·ρ and LEN_G is still LENGTH·g."""
    d = hard_set(600, 8)
    lines, au = d.lines, d.au
    p = RuleParams(0.8, l_min=2.0, waste_max=2.0, include_cutoff=2.0)
    t = table.run(lines, au, p, name="AU")
    main = t.column("KIND") == "основное"
    assert 200 < int(main.sum()) < t.n
    lg = lines.lengths() * np.where(np.isfinite(au), au, 0.0)
    inside = [math.fsum(lg[a:b].tolist()) for a, b in zip(t.rows.first.tolist(), t.rows.last.tolist(), strict=True)]
    assert t.column("LEN_G") == pytest.approx(inside, rel=REL)
    assert t.column("LEN_G") == pytest.approx(t.column("LENGTH") * t.column("AU"), rel=REL)
    assert t.summary["len_g"] == math.fsum(t.column("LEN_G")[main].tolist())
    assert t.summary["len_g"] == pytest.approx(math.fsum(np.asarray(inside)[main].tolist()), rel=REL)
    assert t.summary["length"] == math.fsum(t.column("LENGTH")[main].tolist())
    assert t.summary["mean"] == t.summary["len_g"] / t.summary["length"]
    assert t.summary["intercepts"] == int(main.sum()) and t.summary["included"] == t.n - int(main.sum())
    # an included row never holds more metal than the intercept it lies in
    owner = np.flatnonzero(main)[np.cumsum(main) - 1]
    assert (t.column("LEN_G") <= t.column("LEN_G")[owner] * (1.0 + REL)).all()
    assert (t.column("INCL")[~main] == t.column("ID")[owner][~main]).all() and (t.column("INCL")[main] == 0).all()
    assert (t.column("MISS_LEN") <= t.column("WASTE_LEN")).all() and t.column("MISS_LEN").max() > 0.0
    # by density
    w = table.run(lines, au, p, name="AU", density=d.rho)
    assert np.array_equal(w.column("LEN_G"), w.column("LENGTH") * w.column("AU"))
    for r in np.random.default_rng(1).choice(w.n, 60, replace=False).tolist():
        a, b = int(w.rows.first[r]), int(w.rows.last[r])
        ln, g, rho = lines.lengths()[a:b], np.where(np.isfinite(au[a:b]), au[a:b], 0.0), d.rho[a:b]
        has = np.isfinite(rho) & (rho > 0.0)
        if not has.any():
            want = float((ln * g).sum() / ln.sum())
        else:
            mean_rho = float((ln * rho)[has].sum() / ln[has].sum())
            gaps = float(w.column("LENGTH")[r] - ln.sum())  # stretches without samples: g = 0, the mean density
            want = float(((ln * rho * g)[has].sum() + mean_rho * (ln * g)[~has].sum())
                         / ((ln * rho)[has].sum() + mean_rho * (ln[~has].sum() + gaps)))  # fmt: skip
        assert w.column("AU")[r] == pytest.approx(want, rel=1e-11)


def test_empty_table() -> None:
    lines, g = samples([0.1, 0.2, NAN])
    t = table.run(lines, g, RuleParams(1.0), name="AU", fields={"CU": g}, orienting=orienting(GLOBAL))
    assert t.n == 0 and t.names() == [*HEAD, "AU", "CU", *TAIL]
    for c in t.columns:
        assert c.values.shape == (0,) and c.values.dtype == (object if c.kind == "text" else np.float64)
    s = t.summary
    assert (s["rows"], s["intercepts"], s["included"], s["length"], s["len_g"]) == (0, 0, 0, 0.0, 0.0)
    assert s["mean"] is None and s["tt"] is None and s["src"] == {}


def test_hard_rows_and_values() -> None:
    """Rows that take no part are counted. A grade of a wrong base (1e12), a negative one and ±inf (no grade:
    waste with g = 0) stay what they are; a number the quick rounding does not take is still written."""
    lines = build_lines([0, 0, 0, 0], [0.0, 1.0, 1.5, 3.0], [1.0, 2.0, 2.5, 3.0])  # an overlap; TO = FROM
    t = table.run(lines, lines.take(np.array([2.0, 2.0, 9.0, 9.0])), RuleParams(1.0), name="AU")
    assert t.summary["excluded"] == 2 and col(t, "LENGTH") == [2.0]
    lines, g = samples([2.0, 1e12, -5.0, math.inf, 3.0, 0.1])
    t = table.run(lines, g, RuleParams(1.0, waste_max=5.0), name="AU", unit="г/т")
    assert (t.frm.tolist(), t.to.tolist(), col(t, "MISS_LEN"), col(t, "WASTE_LEN")) == ([0.0], [5.0], [1.0], [2.0])
    assert col(t, "AU") == [2e11] and col(t, "LEN_G") == [1e12]
    assert col(t, "LABEL") == ["5,0 м @ 200000000000,00 г/т"]


def test_wrong_inputs_are_refused() -> None:
    lines, g = samples(T11)
    rows = rule.run(lines, g, RuleParams(1.0)).intercepts()
    with pytest.raises(ValueError, match="CU"):
        table.build(lines, rows, name="AU", fields={"CU": g[:3]})
    with pytest.raises(ValueError, match="xyz"):
        table.build(lines, rows, name="AU", xyz=np.zeros((2, 3)))
    with pytest.raises(ValueError, match="density"):
        table.build(lines, rows, name="AU", density=g[:3])
    with pytest.raises(rule.CompositeError) as e:
        table.run(lines, g, RuleParams(1.0, l_min=-1.0), name="AU")
    assert e.value.code == "bad_param" and e.value.param == "l_min"


# --- threads, progress ---------------------------------------------------------------------------------------


def test_same_bytes_with_any_threads() -> None:
    """More holes than two strips of 4096: the columns, the labels and the summary are the same bit for bit with
    1, 2, 4 and 8 threads (ТЗ 8.0.2) — acceptance by TT, «в том числе», a density, other fields."""
    d = hard_set(9000, 3)
    o = orienting(sp.Source("global", alpha=55.0, dipdir=250.0), traj=d.traj)
    p = RuleParams(0.8, l_min=1.5, waste_max=2.0, min_by="tt", include_cutoff=2.0)
    made = [
        table.run(d.lines, d.au, p, name="AU", unit="г/т", fields={"CU": d.cu, "AG": d.ag}, density=d.rho,
                  orienting=o, threads=k)
        for k in (1, 2, 4, 8)
    ]  # fmt: skip
    t = made[0]
    assert t.n > 3000 and t.summary["included"] > 100
    assert len({digest(m) for m in made}) == 1
    assert all(m.summary == t.summary and m.journal == t.journal for m in made)
    tt = t.column("TT")
    assert np.array_equal(tt, t.rows.tt, equal_nan=True)
    assert 0 < int(np.isnan(tt).sum()) < 400  # the holes without a trajectory: empty, never 0
    assert np.array_equal(np.isnan(tt), np.isnan(t.column("X"))) and np.isnan(t.column("TT_G")[np.isnan(tt)]).all()
    assert (tt[~np.isnan(tt)] >= 1.5 * (1.0 - 1e-9)).all()  # every row with a TT was accepted by it
    assert (t.column("LENGTH")[np.isnan(tt)] >= 1.5 * (1.0 - 1e-9)).all()  # the rest — by the length
    assert (t.column("TT") <= t.column("LENGTH") * (1.0 + 1e-9))[~np.isnan(tt)].all()
    flags = t.column("TT_FLAG").astype(np.int64)
    assert ((flags & 8) > 0).tolist() == np.isnan(tt).tolist()
    assert t.summary["flags"]["8"] == int(np.isnan(tt).sum()) and t.summary["flags"]["2"] == 0
    assert t.summary["src"] == {"глобальная": t.n}
    labels = col(t, "LABEL")
    assert all(("ИМ" in s) == bool(np.isfinite(v)) for s, v in zip(labels, tt.tolist(), strict=True))
    assert all(s.startswith("в т. ч. ") == (k == "в том числе") for s, k in zip(labels, col(t, "KIND"), strict=True))
    assert all(("вдоль тела" in s) == bool(f & 1) for s, f in zip(labels, flags.tolist(), strict=True))
    assert np.isnan(t.column("AG")).any() and not np.isnan(t.column("AU")).any()


def test_tick_reports_and_cancels() -> None:
    d = hard_set(9000, 3)
    o = orienting(GLOBAL, traj=d.traj)
    p = RuleParams(0.8, min_by="tt", include_cutoff=2.0)
    seen: list[float] = []
    table.run(d.lines, d.au, p, name="AU", fields={"CU": d.cu}, orienting=o, tick=seen.append)
    assert len(seen) > 10 and seen[-1] == 1.0
    assert all(0.0 <= a <= b <= 1.0 for a, b in zip(seen, seen[1:], strict=False))

    class Cancelled(Exception):
        pass

    for stop_at in (2, len(seen) // 2, len(seen) - 2):
        calls = 0

        def tick(_f: float, stop_at: int = stop_at) -> None:
            nonlocal calls
            calls += 1
            if calls == stop_at:
                raise Cancelled

        with pytest.raises(Cancelled):
            table.run(d.lines, d.au, p, name="AU", fields={"CU": d.cu}, orienting=o, tick=tick)


# --- the warm-up ---------------------------------------------------------------------------------------------

_AFTER_WARM = r"""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from bf_core.intercepts import warm
took = warm.warm()
assert list(took) == ["tt", "mesh", "table"], took

from bf_core.composites import intervals_kernels
from bf_core.drillholes import desurvey as ds
from bf_core.geo import intersect, nearest, tri_kernels
from bf_core.intercepts import core_orient, directions, orient, orient_mesh, rule, table, tt
from bf_core.intercepts.rule import RuleParams

def kernels():
    mods = (intervals_kernels, ds, tri_kernels, intersect, nearest, core_orient, directions, orient, orient_mesh,
            rule, tt)
    return {f"{m.__name__}.{k}": len(f.signatures) for m in mods for k, f in vars(m).items()
            if hasattr(f, "signatures") and hasattr(f, "py_func")}

before = kernels()
assert before["bf_core.intercepts.rule.scan_strip"] == 3 and before["bf_core.geo.nearest._nearest_band"] >= 2, before
frozen = warm.frozen

# a calculation as `intercept.run` runs it: other data, ordinary and read-only columns, by TT and by length
rng = np.random.default_rng(5)
nh = 50
frm = np.tile(np.arange(20.0), nh)
lines = table.iv.build_lines(np.repeat(np.arange(nh), 20), frm, frm + 1.0)
au = rng.lognormal(0.0, 1.0, lines.n); au[::17] = np.nan
au[:100] = np.tile([0.1] * 6 + [5.0] * 6 + [0.1] * 3 + [5.0] * 3 + [0.1] * 2, 5)  # five holes over the wireframe
rho = rng.normal(2.7, 0.1, lines.n); rho[::13] = np.nan
x, y = rng.uniform(5e5, 5.1e5, nh), rng.uniform(6e6, 6.1e6, nh)
x[:5], y[:5] = 5e5 + 10.0 + 5.0 * np.arange(5), 6e6 + 20.0
dip = rng.uniform(-90, -30, nh); dip[:5] = -90.0
traj = ds.desurvey(ds.Collars(np.arange(nh), x, y, np.full(nh, 300.0), np.full(nh, 50.0),
                              azimuth=rng.uniform(0, 360, nh), dip=dip), n_holes=nh)
plane = SimpleNamespace(kind="global", alpha=40.0, dipdir=10.0)
keys = np.arange(nh) + 1000
o = table.Orienting(SimpleNamespace(orientation=[plane], eps=0.1, step=1.0), orient.Inputs(traj), keys, np.arange(nh))
for g, w in ((au, rho), (frozen(au), frozen(rho)), (frozen(au), None), (au, frozen(rho)), (frozen(au), rho)):
    for min_by in ("tt", "length"):
        p = RuleParams(1.0, min_by=min_by, include_cutoff=2.0)
        assert table.run(lines, g, p, name="AU", fields={"CU": g}, density=w, orienting=o, threads=2).n > 0
    if g is not au and w is not rho:  # the rule itself takes read-only columns as they are
        assert rule.run(lines, g, p, density=w).intercepts().lines.n > 0

# the source «каркас» of the chain: a wireframe in memory and the mapped blobs of a stored one
xs = np.linspace(0.0, 40.0, 9)
gx, gy = np.meshgrid(xs, xs, indexing="ij")
v = np.column_stack([gx.ravel(), gy.ravel(), 0.1 * gx.ravel()]) + (5e5, 6e6, 290.0)
i = (np.arange(8)[:, None] * 9 + np.arange(8)[None, :]).ravel()
tri = np.concatenate([np.stack([i, i + 9, i + 10], axis=1), np.stack([i, i + 10, i + 1], axis=1)]).astype(np.int32)
chain = [SimpleNamespace(kind="mesh", d_max=50.0, r_n=5.0), plane]
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    v.tofile(Path(tmp) / "xyz")
    tri.tofile(Path(tmp) / "tri")
    mapped = (np.memmap(Path(tmp) / "xyz", dtype=np.float64, mode="r").reshape(-1, 3),
              np.memmap(Path(tmp) / "tri", dtype=np.int32, mode="r").reshape(-1, 3))
    for xyz, t3, ordered in ((v, tri[rng.permutation(len(tri))], False), (*mapped, True)):
        target = intersect.MeshTarget(xyz, t3, ordered=ordered)
        inputs = orient.Inputs(traj, mesh=orient_mesh.mesh_source(lambda source, target=target: [target]))
        m = table.Orienting(SimpleNamespace(orientation=chain, eps=0.1, step=1.0), inputs, keys, np.arange(nh))
        t = table.run(lines, au, RuleParams(1.0, min_by="tt"), name="AU", orienting=m, threads=2)
        assert {"каркас: пересечение", "каркас: ближайшая", "глобальная"} == set(t.summary["src"]), t.summary["src"]
    del target, inputs, m, mapped

grown = {k: (before.get(k, 0), n) for k, n in kernels().items() if n != before.get(k, 0)}
assert not grown, f"compiled after the warm-up: {grown}"
print("ok", " ".join(f"{k}={v:.2f}" for k, v in took.items()))
"""


def test_warm_leaves_nothing_to_compile() -> None:
    """A fresh process, as a worker that has just started: after `warm()` a calculation on other data — ordinary
    and read-only columns, a chain with a wireframe in memory and with the mapped blobs of a stored one — adds no
    specialization to any kernel of the stage (compiled, or loaded from the cache)."""
    env = {**os.environ, "PYTHONUTF8": "1"}
    done = subprocess.run([sys.executable, "-c", _AFTER_WARM], capture_output=True, text=True, env=env, timeout=600,
                          encoding="utf-8", check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr[-3000:]
    assert done.stdout.strip().startswith("ok")
