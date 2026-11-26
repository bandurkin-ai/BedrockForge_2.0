"""The jobs `pit.check` and `pit.run` (подшаг 21.11; ТЗ 8.11; ADR 0028 решения 17, 20–23, 28, 30; docs/progress/
e21-api.md §4, §6): the whole run on a synthetic model of cubes with an ore body (the result, `meta`, the five files,
the fields PIT and ORE, the rows of the graph), the result against an independent calculation (the economics of each
block in plain Python, OR-Tools from scratch at every RF, the report by the blocks), «Перестроить» = a calculation from
nothing bit for bit, **Т8.11-9** through the job (blocks above the terrain change nothing and are not in the waste; PIT
the same bits on a repeat, with 1–8 threads and any portion of rows), the angles of the domains, a turned model and
sub-blocks. The cancel, the refusals, the check of the template and PostgreSQL — `test_pit_job_refusals.py`."""

# ruff: noqa: F811 - the fixture `pw` is imported, and the tests take it by its name
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core import lineage as lg
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, lineage
from bf_core.mine import flow_ref as flow
from bf_core.mine import graph as gr
from bf_core.mine import shells as sh
from bf_core.mine import store as st
from bf_core.mine import template as tp
from tests.pitjob import SMALL, columns, direct, field_of, info, rebuild, sha_of, terrain
from tests.pitworld import COUNTS, blocks, make_model, new, params, pw  # noqa: F401 - the fixture

K_GT = 1.0 / 31.1034768


# --- the whole run ----------------------------------------------------------------------------------------------


def test_a_run_writes_the_result_the_fields_and_the_graph(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw)
    sc = new(pw, mid, "Первый")
    sid = sc["id"]
    assert info(pw, sid)["has_result"] is False
    stages: list[str] = []
    out = direct(pw, sid, tick=lambda _p, m: m and stages.append(m.split(":")[0].split(" ")[0]), job_id=None)
    assert [x for x in dict.fromkeys(stages) if x] == ["Чтение", "Экономика", "Шаблон", "Граф", "RF_max", "Оболочки",
                                                       "Отчёт", "Запись"]  # fmt: skip
    assert out["scenario_id"] == sid and out["name"] == "Первый" and out["version"] == 2 and out["algo"] == "pit-1"
    assert out["solver"] == "hpf" and [s["k"] for s in out["shells"]] == [1, 2, 3, 4]
    assert [s["rf"] for s in out["shells"]] == [0.4, 0.7, 1.0, 1.3]
    assert out["events"] == [["pit.changed", {"kind": "pit_scenario", "id": sid}],
                             ["objects.changed", {"kind": "block_model", "id": mid}]]  # fmt: skip
    assert out["model"]["id"] == mid and out["model"]["cells_sha"] and out["model"]["version"] == 2
    assert out["template"]["k"] == 8 and out["template"]["domains"][0]["n_offsets"] == 17
    assert out["objective_error"] == pytest.approx(out["nodes"]["graph"] * out["q"] / 2.0)
    assert out["nodes"]["model"] == out["nodes"]["graph"] == math.prod(COUNTS) and out["nodes"]["air"] == 0
    assert 0 < out["nodes"]["cones"] <= out["nodes"]["graph"]
    assert out["outputs"] == {"fields": ["PIT", "ORE"], "wireframes": []}
    assert set(out["seconds"]) >= {"read", "econ", "template", "graph", "rf_max", "shells", "report", "write", "total"}
    sh_ = out["shells"]
    assert all(a["blocks"] <= b["blocks"] and a["ore_t"] <= b["ore_t"] for a, b in zip(sh_, sh_[1:], strict=False))
    assert sh_[-1]["blocks"] == out["totals"]["blocks"] > 0 and out["totals"]["ore_t"] == sh_[-1]["ore_t"]
    ctl = out["control"]
    assert ctl["closed"] is True and ctl["nested"] is True and ctl["air"]["blocks"] == 0

    got = info(pw, sid)
    meta = got["meta"]
    assert got["state"] == "ready" and got["has_result"] and got["version"] == 2 and got["job_id"] is None
    for key in (
        "v",
        "algo",
        "params",
        "params_hash",
        "model",
        "fields",
        "inputs",
        "template",
        "q",
        "objective_error",
        "shells",
        "totals",
        "outputs",
        "control",
        "solver",
        "seconds",
        "versions",
        "state",
        "job_id",
    ):
        assert key in meta, key
    assert meta["params"]["mining"]["z_ref"] == 90.0 and meta["params"]["density"] == "SG"  # the resolved form
    assert meta["model"]["id"] == mid and meta["model"]["cells_sha"] == out["model"]["cells_sha"]
    assert set(meta["fields"]) >= {"AU", "SG", "ROCK"}
    assert {i["kind"] for i in meta["inputs"]} >= {"object:block_model", "bm_field"}
    with dialect.read_tx(pw.project.engine) as conn:
        roles = set(
            conn.execute(
                sa.select(blob_owners.c.role).where(blob_owners.c.owner_id == sid, blob_owners.c.owner_kind == "object")
            ).scalars()
        )
        assert roles == {"data", "sidecar:benches", "sidecar:zgrid", "sidecar:template", "sidecar:control"}
        tab = st.read_data(conn, pw.store, sid)
        assert tab.num_rows == 4 and tab.column("k").to_pylist() == [1, 2, 3, 4]
        z = st.read_zgrid(conn, pw.store, sid)
        assert z.num_rows == COUNTS[0] * COUNTS[1] and z.column_names[:2] == ["i", "j"] and len(z.column_names) == 6
        assert st.read_template(conn, pw.store, sid)["domains"][0]["n_offsets"] == 17
        assert lg.status(conn, [lg.Node(st.NODE, sid)])[lg.Node(st.NODE, sid)].state == lg.CURRENT

    pit_f, ore_f = field_of(pw, mid, "PIT"), field_of(pw, mid, "ORE")
    assert pit_f and ore_f and pit_f["dtype"] == "i2" and ore_f["dtype"] == "i1"
    assert pit_f["source"]["op"] == "pit" and pit_f["source"]["scenario_id"] == sid == ore_f["source"]["scenario_id"]
    c = columns(pw, mid, "PIT", "ORE")
    pit = np.asarray(c["PIT"]).astype(int)
    assert pit.min() == 0 and pit.max() == 4 and not np.isnan(np.asarray(c["PIT"], dtype=float)).any()
    assert [int((pit == k).sum()) for k in range(1, 5)] == list(np.diff([0, *[s["blocks"] for s in sh_]]))
    ore = np.asarray(c["ORE"], dtype=float)
    assert set(np.unique(ore[~np.isnan(ore)])) <= {0.0, 1.0} and not np.isnan(ore).any()  # no «воздух» here
    with dialect.read_tx(pw.project.engine) as conn:  # the scenario is the input of both fields in the graph
        for name in ("pit", "ore"):
            node = lg.Node("bm_field", mid, name)
            assert lg.status(conn, [node])[node].state == lg.CURRENT
            n = conn.execute(sa.select(sa.func.count()).select_from(lineage).where(
                lineage.c.dst_kind == "bm_field", lineage.c.dst_sub == name, lineage.c.src_kind == st.NODE,
                lineage.c.src_id == sid)).scalar_one()  # fmt: skip
            assert n == 1, name


def test_a_model_with_gaps_in_the_grade_counts_them_and_never_makes_them_zero(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, gaps=0.08, seed=3)
    sid = new(pw, mid)["id"]
    out = direct(pw, sid)
    b = blocks(seed=3, gaps=0.08)  # a gap in a rock that is only waste is not counted: it has no revenue anyway
    want = int((np.isnan(b["AU"]) & (b["ROCK"] == 0)).sum())
    assert want > 0 and out["control"]["no_grade"]["AU"]["blocks"] == want
    assert any("нет содержания AU" in w["message"] for w in out["warnings"])
    assert out["totals"]["elements"]["AU"]["grade"] is not None and out["totals"]["strip"] is not None


# --- against an independent calculation -----------------------------------------------------------------------------


def independent(rfs: list[float], q: float, counts_: tuple[int, int, int], seed: int) -> dict[str, Any]:
    """The economics of every block in plain Python and the closure of OR-Tools from scratch at every RF (the same
    quantum): the number of the smallest shell of each block, and the increments at RF = 1."""
    nx, ny, nz = counts_
    b = blocks(counts_, seed)
    k = (b["p"] // np.uint64(nx * ny)).astype(float)
    t = 1000.0 * b["SG"]
    ore_rock = b["ROCK"] == 0
    z = 5.0 + 10.0 * k
    cm = t * (2.5 + 0.05 * np.maximum(0.0, (10.0 * nz - z) / 10.0))
    cp = np.where(ore_rock, t * 20.0, 0.0)

    def revenue(rf: float) -> np.ndarray:  # R = t·g·k_e·rec·(RF·P − S)·(1 − roy), only the ore rock gives it
        return np.where(ore_rock, t * b["AU"] * K_GT * 0.9 * (rf * 2000.0 - 5.0) * (1.0 - 0.02), 0.0)

    def value(rf: float) -> np.ndarray:
        return np.maximum(revenue(rf) - cp, 0.0) - cm

    tpl = tp.build_template(10.0, 10.0, 10.0, tp.angle_table([(0.0, 45.0)]))
    total = nx * ny * nz
    pit = np.zeros(total, dtype=int)
    for n, rf in enumerate(rfs, 1):
        g = gr.build_graph(counts_, sh.scale(value(rf), q), np.zeros(total, dtype=bool), None, [tpl.tables[0].offsets])
        mined = flow.solve(g)
        pit[(pit == 0) & mined] = n
    ore1 = ore_rock & (revenue(1.0) - cp > 0.0)
    inc = []
    for n in range(1, len(rfs) + 1):
        sel = pit == n
        o, w = float(t[sel & ore1].sum()), float(t[sel & ~ore1].sum())
        cf = float((np.where(ore1, revenue(1.0) - cp, 0.0) - cm)[sel].sum())
        inc.append({"blocks": int(sel.sum()), "ore_t": o, "waste_t": w, "cf": cf})
    return {"pit": pit, "inc": inc}


def test_the_result_is_what_an_independent_calculation_gives(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL, seed=5)
    sid = new(pw, mid)["id"]
    out = direct(pw, sid)
    rfs = [s["rf"] for s in out["shells"]]
    ref = independent(rfs, out["q"], SMALL, 5)
    got = np.asarray(columns(pw, mid, "PIT")["PIT"]).astype(int)
    assert np.array_equal(got, ref["pit"]), "PIT differs from OR-Tools from scratch at every RF"
    assert len(set(got.tolist())) >= 3  # the shells really differ
    with dialect.read_tx(pw.project.engine) as conn:
        shells = st.read_shells(conn, pw.store, sid)
    for s, r in zip(shells, ref["inc"], strict=True):
        assert s["blocks"] == r["blocks"]
        assert s["ore_t"] == pytest.approx(r["ore_t"], rel=1e-9, abs=1e-6)
        assert s["waste_t"] == pytest.approx(r["waste_t"], rel=1e-9, abs=1e-6)
        assert s["cf"] == pytest.approx(r["cf"], rel=1e-9, abs=1e-3)
        cf = s["revenue"] - s["cost_sales"] - s["royalty"] - s["cost_mine"] - s["cost_process"]
        assert cf == pytest.approx(s["cf"], rel=1e-9, abs=1e-3)  # the identity of the contract
    for key in ("ore_t", "waste_t", "cf"):  # Σ of the increments = the total
        assert math.fsum(s[key] for s in shells) == pytest.approx(out["totals"][key], rel=1e-9, abs=1e-3)
    assert out["totals"]["strip"] == pytest.approx(out["totals"]["waste_t"] / out["totals"]["ore_t"], rel=1e-12)


def test_the_ortools_solver_gives_the_same_pit_as_hpf(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL, seed=5)
    a, b = new(pw, mid, "HPF")["id"], new(pw, mid, "OR-Tools", prefix="B_", solver="ortools")["id"]
    ra, rb = direct(pw, a), direct(pw, b)
    assert (ra["solver"], rb["solver"]) == ("hpf", "ortools")
    pa, pb = columns(pw, mid, "PIT", "B_PIT")["PIT"], columns(pw, mid, "B_PIT")["B_PIT"]
    assert np.array_equal(pa, pb) and ra["q"] == rb["q"]
    assert [s["blocks"] for s in ra["shells"]] == [s["blocks"] for s in rb["shells"]]


# --- «Перестроить», the determinism ---------------------------------------------------------------------------------


def test_rebuild_is_a_calculation_from_nothing_bit_for_bit(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw)
    sid = new(pw, mid)["id"]
    first = direct(pw, sid)
    sha, pit = sha_of(pw, sid), np.asarray(columns(pw, mid, "PIT", "ORE")["PIT"]).tobytes()
    ore = np.asarray(columns(pw, mid, "ORE")["ORE"], dtype=float).tobytes()
    again = rebuild(pw, sid, chunk=5, threads=3)
    assert again["version"] == first["version"] + 1 and again["params_hash"] == first["params_hash"]
    assert sha_of(pw, sid) == sha  # the same `data`, byte for byte
    assert np.asarray(columns(pw, mid, "PIT")["PIT"]).tobytes() == pit
    assert np.asarray(columns(pw, mid, "ORE")["ORE"], dtype=float).tobytes() == ore
    assert again["shells"] == first["shells"] and again["totals"] == first["totals"] and again["q"] == first["q"]
    got = info(pw, sid)
    assert got["state"] == "ready" and got["version"] == 3 and not got["params_changed"]


def test_pit_is_the_same_bits_with_1_to_8_threads_and_any_portion_of_rows(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, (10, 9, 7), seed=2)
    sid = new(pw, mid)["id"]
    base = direct(pw, sid, threads=1)
    sha, pit = sha_of(pw, sid), np.asarray(columns(pw, mid, "PIT")["PIT"]).tobytes()
    for threads, chunk in ((2, 7), (4, 1 << 20), (8, 1), (3, 100)):
        out = rebuild(pw, sid, threads=threads, chunk=chunk)
        assert sha_of(pw, sid) == sha, (threads, chunk)
        assert np.asarray(columns(pw, mid, "PIT")["PIT"]).tobytes() == pit, (threads, chunk)
        assert out["shells"] == base["shells"] and out["q"] == base["q"], (threads, chunk)


def test_t8_11_9_blocks_above_the_terrain_change_nothing(pw) -> None:  # type: ignore[no-untyped-def]
    """The model A has rich blocks above the terrain; the model B has no blocks there at all (a sparse file): the pit,
    the tonnes and the report are the same, the blocks above are «воздух» (PIT 0, ORE empty), not a waste."""
    counts_ = (10, 9, 8)
    b = blocks(counts_, seed=4)
    nx, ny, _ = counts_
    level = (b["p"] // np.uint64(nx * ny)).astype(int)
    above = level >= 6  # the terrain is at z = 60: the levels 6 and 7 are above it
    rich = {**b, "AU": np.where(above, 40.0, b["AU"]), "ROCK": np.where(above, 0, b["ROCK"]).astype(np.int32)}
    from tests.pitworld import FIELDS, grid_of
    from tests.test_blocks_report import put

    a = put(pw, grid_of(counts_), FIELDS, rich["p"], {k: v for k, v in rich.items() if k != "p"}, name="A")
    keep = ~above
    sparse = {k: v[keep] for k, v in b.items()}
    bb = put(pw, grid_of(counts_), FIELDS, sparse["p"], {k: v for k, v in sparse.items() if k != "p"}, name="B")
    dtm = terrain(pw, 60.0)
    sa_ = new(pw, a, "A", terrain_id=dtm)["id"]
    sb = new(pw, bb, "B", terrain_id=dtm)["id"]
    ra, rb = direct(pw, sa_), direct(pw, sb)
    assert ra["nodes"]["air"] == int(above.sum()) and ra["nodes"]["graph"] == int(keep.sum()) == rb["nodes"]["graph"]
    assert rb["control"]["empty_cells"]["blocks"] == 0  # the cells above the terrain are «воздух», not an empty waste
    assert sha_of(pw, sa_) == sha_of(pw, sb)  # the report: the same bytes
    assert ra["shells"] == rb["shells"] and ra["totals"] == rb["totals"] and ra["q"] == rb["q"]
    pa, pb = columns(pw, a, "PIT", "ORE"), columns(pw, bb, "PIT", "ORE")
    pit_a = np.asarray(pa["PIT"]).astype(int)
    assert (pit_a[above] == 0).all() and np.array_equal(pit_a[keep], np.asarray(pb["PIT"]).astype(int))
    ore_a = np.asarray(pa["ORE"], dtype=float)
    assert np.isnan(ore_a[above]).all() and not np.isnan(ore_a[keep]).any()  # a gap, never 0
    assert np.array_equal(ore_a[keep], np.asarray(pb["ORE"], dtype=float))
    assert ra["totals"]["ore_t"] < 1e6  # the 40 g/t blocks above are not ore of the pit
    # without the terrain the rich blocks above DO change the pit: the terrain is what keeps them out
    sc = new(pw, a, "A без рельефа", prefix="N_")["id"]
    rc = direct(pw, sc)
    assert rc["totals"]["ore_t"] > ra["totals"]["ore_t"] * 1.2


def test_a_block_that_is_not_in_the_file_below_the_terrain_is_a_waste_of_the_type_empty(pw) -> None:  # type: ignore[no-untyped-def]
    counts_ = (8, 8, 6)
    b = blocks(counts_, seed=6)
    drop = np.zeros(len(b["p"]), dtype=bool)
    drop[[3, 4, 50, 70]] = True  # four cells of the lowest levels are absent from the file
    from tests.pitworld import FIELDS, grid_of
    from tests.test_blocks_report import put

    part = {k: v[~drop] for k, v in b.items()}
    mid = put(pw, grid_of(counts_), FIELDS, part["p"], {k: v for k, v in part.items() if k != "p"}, name="Разрежённая")
    dtm = terrain(pw, 60.0)
    base = params(mid, terrain_id=dtm, default_density=2.6)
    base["rocks"] = {**base["rocks"], "types": [*base["rocks"]["types"], {"name": "(пусто)", "waste_only": True}]}
    with dialect.write_tx(pw.project.engine) as conn:
        sid = st.create(conn, "Разрежённая", base)["id"]
    out = direct(pw, sid)
    assert out["control"]["empty_cells"]["blocks"] == 4 and out["nodes"]["graph"] == math.prod(counts_)
    assert any("Пустых ячеек ниже рельефа: 4" in w["message"] for w in out["warnings"])


def test_the_table_of_the_domain_of_a_node_gives_its_arcs(pw) -> None:  # type: ignore[no-untyped-def]
    """Every node is in the domain A: the table of A is the table of the whole pit; a domain that no node is in changes
    nothing (the general table is used)."""
    from tests.pitworld import FIELDS, grid_of
    from tests.test_blocks_report import put

    b = blocks(SMALL, seed=5)
    fields = [*FIELDS, {"name": "DOM", "dtype": "cat", "labels": ["A", "B"]}]
    vals = {**{k: v for k, v in b.items() if k != "p"}, "DOM": np.zeros(len(b["p"]), dtype=np.int32)}
    mid = put(pw, grid_of(SMALL), fields, b["p"], vals, name="Домены")

    def pit_of(prefix: str, **over: Any) -> tuple[bytes, dict[str, Any]]:
        sid = new(pw, mid, prefix, prefix=prefix, **over)["id"]
        out = direct(pw, sid)
        return np.asarray(columns(pw, mid, f"{prefix}PIT")[f"{prefix}PIT"]).tobytes(), out

    slope = {"table": [{"az": 0.0, "angle": 45.0}]}
    steep = {"table": [{"az": 0.0, "angle": 65.0}]}
    plain45, _ = pit_of("a_", slopes=slope)
    plain65, out65 = pit_of("b_", slopes=steep)
    assert plain45 != plain65 and out65["template"]["k"] >= 8  # the angle matters on this model
    by_domain, out = pit_of("c_", slopes={**slope, "domain_field": "DOM",
                                          "domains": [{"label": "A", "table": steep["table"]}]})  # fmt: skip
    assert by_domain == plain65 and [d["label"] for d in out["template"]["domains"]] == [None, "A"]
    unused, _ = pit_of("d_", slopes={**slope, "domain_field": "DOM",
                                     "domains": [{"label": "B", "table": steep["table"]}]})  # fmt: skip
    assert unused == plain45  # no node is in B: the general table is the table of every node


def test_a_turned_model_and_sub_blocks_are_run_like_any_other(pw) -> None:  # type: ignore[no-untyped-def]
    from tests.test_pit_econ import sub_model

    turned = make_model(pw, SMALL, seed=5, rotation=30.0, name="Повёрнутая")
    sid = new(pw, turned)["id"]
    out = direct(pw, sid)
    assert out["model"]["grid"]["rotation"] == 30.0 and out["shells"][-1]["blocks"] > 0
    z = info(pw, sid)["meta"]["totals"]
    assert z["ore_t"] > 0 and info(pw, sid)["has_result"]
    mid, parts = sub_model(pw)  # 2×2×1 parents of 4 m × 8 m, div 2: 9 rows in 4 parents
    body = {"model_id": mid, "elements": [{"field": "AU", "unit": "g/t", "price": 2000.0}], "density": "SG",
            "rocks": {"types": [{"name": "Все породы", "c_p": 20.0, "rec": {"AU": 0.9}}]},
            "mining": {"c_m0": 2.5}, "rf": [0.8, 1.2]}  # fmt: skip
    with dialect.write_tx(pw.project.engine) as conn:
        sub = st.create(conn, "Субблоки", body)["id"]
    res = direct(pw, sub)
    assert res["nodes"]["model"] == len(parts["a"]) == 9 and res["nodes"]["graph"] == 4  # a node is a parent
    c = columns(pw, mid, "PIT", "ORE")
    pit, ore = np.asarray(c["PIT"]).astype(int), np.asarray(c["ORE"], dtype=float)
    owner = parts["a"][:, 0]
    for parent in range(4):  # every row of a parent has the PIT of the parent; ORE is the row's own
        assert len(set(pit[owner == parent].tolist())) == 1
    assert pit.max() >= 1 and set(ore.tolist()) <= {0.0, 1.0}
    assert (ore[parts["vals"]["AU"] > 4.0] == 1.0).all()  # a rich row is ore at RF = 1
