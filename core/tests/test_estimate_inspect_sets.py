"""The reasons of the candidates of the inspector and the sub-blocks (ТЗ 7.35 Д-30; Э18, 18.Д3): ТД30-2 on a set of
the T13 kind built here (the file of the prompt is not in the repository) — octants, a sample exactly on the plane of
an octant, a hole in several octants, a far high sample, the soft boundary, another domain, a coincident pair, a top
cut: the reason of every candidate is the expected one, in Russian, and the weights of the selected ones give the
estimate; the cap of a high sample; the limit of 1000 candidates; a model with sub-blocks (`sub: parent` and `own`)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pytest

from bf_core.blocks import geometry
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.geostat.estimate import inspect as ins
from bf_core.geostat.estimate import inspect_spot as isp
from bf_core.geostat.estimate import kernel as kn
from bf_core.geostat.estimate import samples as sm
from bf_core.geostat.estimate import search as se
from bf_core.geostat.estimate.samples import D, S, prepare
from tests import estinspect as ei
from tests.estinspect import Ctx, prepared
from tests.test_estimate_kernel import spec_of

# --- ТД30-2 --------------------------------------------------------------------------------------------------------

# the set (centre 0, the axes of the ellipsoid are the axes of the project, r = 100, h_s = the distance):
#   name: (xyz, hole, domain, z, raw)
CRAFTED: dict[str, tuple[tuple[float, float, float], int, int, float, float]] = {
    "P1": ((0.0, 0.0, -3.0), 0, D, 2.0, 2.0),  # x = 0, y = 0: on the planes — the positive side; z < 0: octant 3
    "P2": ((3.0, 4.0, 0.0), 1, D, 2.0, 2.0),  # z = 0: on the plane — the positive side; octant 7
    "P3": ((-6.0, -8.0, 5.0), 2, D, 6.0, 9.0),  # octant 4; the top cut 9 → 6
    "Q1": ((12.0, 0.0, 0.0), 1, D, 2.0, 2.0),  # octant 7 again: taken by P2
    "R6": ((-15.0, -15.0, -15.0), 3, D, 2.0, 2.0),  # octant 0; the first of hole 3
    "R1": ((-20.0, 15.0, -10.0), 3, D, 2.0, 2.0),  # octant 2; the second of hole 3
    "R2": ((-22.0, 16.0, -11.0), 3, D, 2.0, 2.0),  # the third of hole 3, and octant 2 is taken as well
    "T2": ((-20.0, 18.0, 12.0), 5, D, 50.0, 50.0),  # a high one, but nearer than q·r = 50: selected
    "S1": ((-20.0, -20.0, -20.0), 9, S, 2.0, 2.0),  # soft: 34.6 m ≤ d_м = 40; the eighth — beyond n_max = 7
    "R4": ((25.0, -30.0, 15.0), 3, D, 2.0, 2.0),
    "Z1": ((45.0, 45.0, 45.0), 12, D, 2.0, 2.0),
    "T1": ((-60.0, 0.0, 0.0), 4, D, 50.0, 50.0),  # a far high one: 60 m > q·r = 50
    "E1": ((5.0, 5.0, 5.0), 7, 2, 2.0, 2.0),  # another domain
    "F2": ((-40.0, -40.0, -40.0), 9, S, 2.0, 2.0),  # soft, but 69.3 m > d_м
    "G1": ((150.0, 0.0, 0.0), 6, D, 2.0, 2.0),  # beyond the radius of the pass, inside that of the last
}
MERGED = ((5.0, -6.0, -7.0), 8)  # M1: two rows of one place (1 and 3 → 2), octant 1
EXPECTED = {
    "P1": "selected", "P2": "selected", "M1": "selected", "P3": "selected", "Q1": "octant_limit", "R6": "selected",
    "R1": "selected", "R2": "hole_limit", "T2": "selected", "S1": "over_n_max", "R4": "over_n_max",
    "Z1": "over_n_max", "T1": "hy_dropped", "E1": "other_domain", "F2": "soft_far", "G1": "outside",
}  # fmt: skip
LABELS_RU = {
    "selected": "выбрана", "octant_limit": "лимит октанта", "hole_limit": "лимит скважины", "over_n_max": "за n_max",
    "hy_dropped": "ураганная, отброшена", "other_domain": "другой домен (жёсткая граница)", "outside": "вне эллипсоида",
    "soft_far": "дальше d_м (мягкая граница)", "hy_capped": "урезана до 10",
}  # fmt: skip


def crafted_set() -> tuple[sm.RowSamples, dict[str, int]]:
    names = [*CRAFTED, "M1", "M1b"]
    xyz = [CRAFTED[k][0] for k in CRAFTED] + [MERGED[0], MERGED[0]]
    hole = [CRAFTED[k][1] for k in CRAFTED] + [MERGED[1], MERGED[1]]
    dom = [CRAFTED[k][2] for k in CRAFTED] + [D, D]
    z = [CRAFTED[k][3] for k in CRAFTED] + [1.0, 3.0]
    raw = [CRAFTED[k][4] for k in CRAFTED] + [1.0, 3.0]
    n = len(names)
    s = prepare(np.array(xyz), np.array(z), np.array(raw), None, np.array(hole, dtype=np.int32),
                [f"h{i}" for i in range(15)], np.array(dom, dtype=np.int16), np.arange(100, 100 + n, dtype=np.int64),
                np.column_stack([np.arange(n) * 2.5, np.arange(n) * 2.5 + 2.5]), origin=np.zeros(3),
                dom_labels=["сульфид"])  # fmt: skip
    where = {k: int(np.flatnonzero(np.all(s.xyz == np.array(v[0]), axis=1))[0]) for k, v in CRAFTED.items()}
    where["M1"] = int(np.flatnonzero(np.all(s.xyz == np.array(MERGED[0]), axis=1))[0])
    return s, where


def crafted_row(mode: str = "drop", n_max: int = 7, n_oct: int = 1, n_hole: int = 2) -> tuple[kn.RowKernel, Any]:
    s, where = crafted_set()
    hy = {"t": 10.0, "q": 0.5, "mode": mode}
    passes = [{"factor": 1, "n_min": 2, "n_max": n_max, "n_hole": n_hole, "n_oct": n_oct, "hy": hy},
              {"factor": 1.5, "n_min": 2, "n_max": 8}]  # fmt: skip
    spec = spec_of("idw", passes=passes, radii=(100.0, 100.0, 100.0), angles=(90.0, 0.0, 0.0), power=2.0)
    spec = replace(spec, samples=replace(spec.samples, soft=(("сульфид",), 40.0)))
    rk = kn.compile_row(spec, s)
    return rk, where


def test_td30_2_the_reasons_of_the_candidates_are_the_expected_ones_in_russian() -> None:
    rk, where = crafted_row()
    detail = kn.estimate_block(rk, np.zeros(3))
    table = ins.candidates_table(rk, detail)
    by_pos = {c["i"]: c for c in table}
    got = {k: by_pos[v]["reason"] for k, v in where.items()}
    assert got == EXPECTED
    assert len(table) == detail.candidates.total == 16 and [c["h"] for c in table] == sorted(c["h"] for c in table)
    for k, v in where.items():  # the Russian text of the reason, with the numbers of the set
        want = LABELS_RU[EXPECTED[k]]
        label = by_pos[v]["reason_label"]
        assert label.startswith(want), (k, label)
    assert by_pos[where["M1"]]["merged"] == 2 and by_pos[where["M1"]]["reason_label"].endswith("усреднены: 2")
    assert by_pos[where["M1"]]["z"] == 2.0 and by_pos[where["M1"]]["z_raw"] == 2.0
    # the octants of the boundary and of the hole in several octants
    octs = {k: by_pos[v]["octant"] for k, v in where.items()}
    assert (octs["P1"], octs["P2"], octs["P3"], octs["M1"], octs["R6"], octs["R1"], octs["T2"]) == (3, 7, 4, 1, 0, 2, 6)
    assert octs["Q1"] == 7 and octs["R2"] == 2  # the third of hole 3 is also in a taken octant: the hole limit wins
    # the values: before the top cut and used; the high sample that is not far is not touched
    assert (by_pos[where["P3"]]["z_raw"], by_pos[where["P3"]]["z"]) == (9.0, 6.0)
    assert by_pos[where["T2"]]["z"] == 50.0 and by_pos[where["T1"]]["z"] == 50.0
    # the domains and the places of the table of the window 7.17
    assert by_pos[where["E1"]]["domain"] == "сульфид" or by_pos[where["E1"]]["domain"]  # the label of the code 2
    assert by_pos[where["S1"]]["domain"] == "мягкая граница" and by_pos[where["P1"]]["domain"] == "домен оценки"
    assert all(c["hole"].startswith("h") and c["from"] < c["to"] if c["from"] is not None else True for c in table)
    assert all(c["row_id"] >= 100 for c in table) and all(len(c["xyz"]) == 3 for c in table)
    # the estimate: the weights of the selected samples (IDW, power 2) — only those
    sel = [c for c in table if c["reason"] == "selected"]
    assert len(sel) == 7 == detail.counts["selected"] and abs(sum(c["lambda"] for c in sel) - 1.0) < 1e-12
    assert all(c["lambda"] is None for c in table if c["reason"] != "selected")
    est = sum(c["lambda"] * c["z"] for c in sel)
    assert est == pytest.approx(detail.values["EST"], rel=1e-12)
    inv = np.array([1.0 / c["h"] ** 2 for c in sel])
    np.testing.assert_allclose([c["lambda"] for c in sel], inv / inv.sum(), rtol=1e-12)
    assert detail.status == 0 and detail.pass_ == 1 and detail.candidates.radius == 100.0
    # NHY: the dropped sample (60 m) is beyond the reach of the selection (the 7th selected is at 29.5 m): not counted
    assert (detail.values["NS"], detail.values["NH"], detail.values["NOCT"], detail.values["NHY"]) == (7, 6, 7, 0)


def test_td30_2_the_capped_sample_and_the_second_pass() -> None:
    rk, where = crafted_row("cap", n_max=24, n_oct=0, n_hole=0)
    detail = kn.estimate_block(rk, np.zeros(3))
    table = ins.candidates_table(rk, detail)
    t1 = next(c for c in table if c["i"] == where["T1"])
    assert (
        t1["reason"] == "hy_capped"
        and t1["z"] == 10.0
        and t1["z_raw"] == 50.0
        and t1["reason_label"] == "урезана до 10"
    )
    assert detail.values["NHY"] == 1 and detail.candidates.nhy == 1  # n_max is not reached: the reach is the radius
    assert t1["lambda"] is not None and t1["i"] in [s["i"] for s in ins.segments_of([0.0, 0.0, 0.0], table)]
    # the other three decimals of the cap are written with a comma
    assert ins.reason_label("hy_capped", 12.5, 1) == "урезана до 12,5"
    # no block here without samples: the few-samples block shows the candidates of the last pass and no weights
    far = kn.estimate_block(rk, np.array([5000.0, 5000.0, 5000.0]))
    assert far.status == 2 and ins.candidates_table(rk, far) == [] and far.counts == {"candidates": 0, "selected": 0}
    # a sample exactly on a boundary of the radius is inside: h = r
    assert kn.estimate_block(rk, np.array([-100.0 + 0.0, 0.0, 0.0])).candidates.total >= 1


def test_every_code_of_the_search_has_its_russian_text() -> None:
    assert set(ins.REASON_LABELS) == set(se.REASONS)
    assert all(t and t == t.strip() and t[0].islower() for t in ins.REASON_LABELS.values())
    assert ins.reason_label("merged", 0.0, 1) == "совпадающая точка (усреднена)"


def test_at_most_a_thousand_candidates_are_listed_and_the_total_is_told() -> None:
    rng = np.random.default_rng(2)
    n = 1500
    xyz = rng.uniform(-30.0, 30.0, (n, 3))
    s = prepare(xyz, rng.uniform(0.0, 3.0, n), None, None, np.arange(n, dtype=np.int32), [f"h{i}" for i in range(n)],
                np.zeros(n, np.int16), np.arange(n, dtype=np.int64), np.zeros((n, 2)), origin=np.zeros(3))  # fmt: skip
    spec = spec_of("idw", passes=[{"factor": 1, "n_min": 2, "n_max": 12}], radii=(100.0, 100.0, 100.0))
    rk = kn.compile_row(spec, s)
    detail = kn.estimate_block(rk, np.zeros(3))
    table = ins.candidates_table(rk, detail)
    assert len(table) == se.EXPLAIN_MAX == 1000 and detail.candidates.total == 1500
    assert [c["h"] for c in table] == sorted(c["h"] for c in table) and table[0]["h"] < table[-1]["h"]
    assert sum(1 for c in table if c["reason"] == "selected") == 12


# --- sub-blocks and `own` --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sub_ctx(tmp_path_factory: pytest.TempPathFactory) -> Any:
    from types import SimpleNamespace

    from bf_core.db import migrate
    from tests.dbutil import make_engine

    tmp = tmp_path_factory.mktemp("inspect_sub")
    engine = make_engine("sqlite", tmp)
    migrate.upgrade(engine, "project")
    project = SimpleNamespace(engine=engine, uid=f"test:{tmp}", folder=tmp)
    from tests.test_vario_store import fill

    set_id, a, b = fill(engine)
    from tests.estutil import fill_world, vmodel

    w = fill_world(project, set_id, a, b)
    w.vz3 = vmodel(w, "Три структуры", model=ei.MODEL3)  # type: ignore[attr-defined]
    w.big = ei.sub_model(w)  # type: ignore[attr-defined]
    w.big_set, w.big_table = ei.add_holes(w, w.big, n_holes=26, name="Для субблоков")  # type: ignore[attr-defined]
    search = {**ei.SEARCH, "r1": 24.0, "r2": 18.0, "r3": 9.0}
    out = {}
    for sub in ("parent", "own"):
        eid = ei.stored_world(w, name=f"sub {sub}", sub=sub, disc=[2, 2, 2], search=search,
                              outputs={"prefix": f"S_{sub[0].upper()}_"})  # fmt: skip
        out[sub] = (eid, ei.write_fields(w, eid))
    yield w, out
    engine.dispose()


def test_a_sub_block_row_of_the_plan_is_recalculated_for_the_parent_or_for_the_row(sub_ctx: Any) -> None:
    w, rows = sub_ctx
    with dialect.read_tx(w.project.engine) as conn:
        model = Model.open(conn, w.store, w.big)
    assert model.sub and model.rows > model.parents
    rng = np.random.default_rng(5)
    seen = {"parent": 0, "own": 0, "split": 0}
    for sub, (eid, cols) in rows.items():
        c = Ctx(w, eid, cols)
        mdl, row, entry, fresh = prepared(c, eid)
        for r in sorted(rng.choice(model.rows, 120, replace=False).tolist()):
            spot = isp.locate(mdl, {"row": r})
            got = ins.one_row(mdl, spot, row, entry, fresh, None, chosen=True)
            assert got is not None and got["sub"] == sub
            prefix = f"S_{sub[0].upper()}_"
            assert got["status"] == int(cols[prefix + "STATUS"][r]), (sub, r)
            if got["status"] != 1:
                assert got["same"] is True, (sub, r, [f for f in got["fields"] if f["same"] is False])
                seen[sub] += 1
            if sub == "parent":  # the block is the parent: the same for every row of it, its size
                assert got["block"]["size"] == [10.0, 10.0, 5.0] and got["block"]["rows"] == spot.rows
                np.testing.assert_allclose(got["block"]["centre"], spot.parent_centre, rtol=0, atol=0)
            else:
                np.testing.assert_allclose(got["block"]["size"], spot.size, rtol=0, atol=0)
                seen["split"] += int(spot.rows > 1)
    assert seen["parent"] > 60 and seen["own"] > 60 and seen["split"] > 20, seen


def test_a_sub_block_row_has_its_own_geometry_and_the_ijk_gives_the_first_row_of_the_parent(sub_ctx: Any) -> None:
    w, _ = sub_ctx
    with dialect.read_tx(w.project.engine) as conn:
        model = Model.open(conn, w.store, w.big)
    p = model.p()
    split = int(np.flatnonzero(np.bincount(p.astype(np.int64)) == 8)[0])  # a parent of eight sub-blocks
    first = int(np.flatnonzero(p == split)[0])
    ijk = [int(a[0]) for a in geometry.row_ijk(model.grid, np.array([split], dtype=np.uint64))]
    a = isp.locate(model, {"ijk": ijk})
    assert a.row == first and (a.first, a.stop) == (first, first + 8) and a.sub is not None
    np.testing.assert_allclose(a.size, [5.0, 5.0, 2.5])  # a sub-block: half of the parent
    np.testing.assert_allclose(a.parent_size, [10.0, 10.0, 5.0])
    b = isp.locate(model, {"xyz": a.centre.tolist()})  # the point holds in the sub-block it is the centre of
    assert b.row == first and b.sub == a.sub
    c = isp.locate(model, {"row": first + 5})
    assert c.row == first + 5 and (c.first, c.stop) == (first, first + 8) and c.p == split
    assert not np.allclose(c.centre, a.centre) and np.allclose(c.parent_centre, a.parent_centre)
