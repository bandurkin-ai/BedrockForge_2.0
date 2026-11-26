"""The kernels and the pass of the resource report (подшаг 20.2; ТЗ 8.10; ADR 0027 decisions 3–7, 12).

Т8.10-1 (8 blocks, 12 500 t, 2,5 g/t, 31,25 kg = 1,004711 thousand ounces), Т8.10-2 (the numbers of BF
`grade_tonnage`, copied in `tests/ref/bf_report.py`: tonnes, volume, grades, missing tonnes, metal of g/t, % and ppb,
groups, «(пусто)», the cut-off `None` that takes the blocks without a value of the cut-off field), Т8.10-4 (a bench: a
block Z 0…10 cut at 5 — half and half; a random model against a brute-force reference), the same bits with a portion
of 1, 7 and 2²⁰ rows, a gap is not a zero, the Neumaier sums, 422 in Russian."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import restrict
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.projects.service import projects_of
from bf_core.resources import compute as cp
from bf_core.resources import kernels as rk
from bf_core.storage.blobs import BlobStore
from tests.ref import bf_report as bf
from tests.test_blocks_report import BF_GRID, bf_blocks, put

OZ = bf.TROY_OUNCE_G


@pytest.fixture()
def rc(pclient):  # type: ignore[no-untyped-def]
    assert pclient.post("/api/v1/projects", json={"name": "Ресурсы"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.store = BlobStore(pclient.project.folder)
    return pclient


def open_model(c: Any, mid: int, rules: dict[str, Any] | None = None) -> tuple[Model, restrict.Restrictor]:
    store = BlobStore(c.project.folder)
    with dialect.read_tx(c.project.engine) as conn:
        model = Model.open(conn, store, mid)
        return model, restrict.prepare(model, rules, conn=conn, store=store)


def edges_of(*groups: Any) -> np.ndarray:
    return np.unique(np.concatenate([np.asarray(g, dtype=np.float64) for g in groups]))


def total_row(s: cp.Sums, c: float | None) -> tuple[np.ndarray, np.ndarray]:
    """The row of the total for the cut-off c (None — «без борта»): sums [Q] and counts [K]."""
    e = len(s.core.edges)
    lo, hi = (0, e + 2) if c is None else (rk.cutoff_bin(s.core.edges, c), e + 1)
    q, n = rk.range_sums(*s.total_view(), lo, hi)
    return q[0], n[0]


def group_row(s: cp.Sums, code: int, c: float | None) -> tuple[np.ndarray, np.ndarray]:
    e = len(s.core.group_edges)
    lo, hi = (0, e + 2) if c is None else (rk.cutoff_bin(s.core.group_edges, c), e + 1)
    sg, cg, ng = s.group_view()
    q, n = rk.range_sums(sg[code : code + 1], cg[code : code + 1], ng[code : code + 1], lo, hi)
    return q[0], n[0]


def close(a: float, b: float, rel: float = 1e-12) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b))


# --- Т8.10-1 ----------------------------------------------------------------------------------------------------------


def test_t8_10_1_eight_blocks(rc) -> None:  # type: ignore[no-untyped-def]
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [2, 2, 2], "size": [10.0, 10.0, 10.0]}
    au = np.array([0.2, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0])
    mid = put(rc, grid, [{"name": "AU", "dtype": "f8", "unit": "g/t"}], np.arange(8, dtype=np.uint64), {"AU": au})
    model, r = open_model(rc, mid)
    core = cp.Core(["AU"], "AU", None, 2.5, np.array([1.0]), np.array([1.0]))
    s = cp.run(model, core, r)
    q, n = total_row(s, 1.0)
    assert n[0] == 5 and q[0] == 5000.0 and q[1] == 12_500.0
    grade = q[3] / q[2]
    assert grade == 2.5
    assert q[3] / 1000.0 == 31.25  # kg
    assert abs(q[3] / OZ / 1000.0 - 1.004711) < 1e-6  # thousand troy ounces
    assert q[4] == 0.0 and n[1] == 0
    q, n = total_row(s, None)
    assert n[0] == 8 and q[1] == 20_000.0 and s.excluded == {"filtered": 0, "no_volume": 0, "no_density": 0}


# --- Т8.10-2: BF `grade_tonnage` --------------------------------------------------------------------------------------

BF_FIELDS2 = [
    {"name": "AU_CUT", "dtype": "f8", "unit": "g/t", "role": "grade"},
    {"name": "CU_CUT", "dtype": "f8", "unit": "%", "role": "grade"},
    {"name": "PB", "dtype": "f8", "unit": "ppb"},
    {"name": "SG", "dtype": "f8", "role": "density"},
    {"name": "ALT", "dtype": "cat", "labels": ["G", "H", "Руда окисл. ёЁ"]},
    {"name": "OXIDE", "dtype": "cat", "labels": ["Ox", "Sulf"]},
]


def bf_model2(rc: Any, seed: int = 5) -> tuple[int, dict[str, Any]]:
    """A sub-blocked model of BF with gaps in the grades (a gap in the field of the cut-off too), a gap in the density
    and ppb: the values the BF function sees."""
    m = bf_blocks(seed)
    rng = np.random.default_rng(seed + 100)
    n = m["n"]
    for k, gap in (("AU_CUT", 0.15), ("CU_CUT", 0.25), ("PB", 0.4)):
        v = rng.lognormal(0.0, 0.8, n) * (1000.0 if k == "PB" else 1.0)
        v[rng.random(n) < gap] = np.nan
        m[k] = v
    m["SG"] = rng.uniform(2.2, 3.1, n)
    m["SG"][[1, 5]] = np.nan
    cols = {k: m[k] for k in ("AU_CUT", "CU_CUT", "PB", "SG", "ALT", "OXIDE")}
    return put(rc, BF_GRID, BF_FIELDS2, m["p"], cols, m["s"], m["w"]), m


def bf_ref(m: dict[str, Any], keep: np.ndarray, cutoff_field: str, cutoffs: list[float | None], *, key: str
           ) -> dict[str, Any]:  # fmt: skip
    units = {"AU_CUT": "g/t", "CU_CUT": "%", "PB": "ppb"}
    grades = [bf.Grade(k, k, m[k], u) for k, u in units.items()]
    codes = m[key]
    labels = ["G", "H", "Руда окисл. ёЁ"] if key == "ALT" else ["Ox", "Sulf"]
    return bf.grade_tonnage(m["vol"], m["SG"], grades, cutoff=m[cutoff_field], cutoffs=cutoffs, mask=keep,
                            groups=(codes, labels))  # fmt: skip


@pytest.mark.parametrize("rules", [None, {"cond": {"field": "CU_CUT", "op": ">", "value": 0.3}}])
@pytest.mark.parametrize("cutoff_field", ["AU_CUT", "PB"])
def test_t8_10_2_bf_grade_tonnage(rc, rules, cutoff_field) -> None:  # type: ignore[no-untyped-def]
    mid, m = bf_model2(rc)
    model, r = open_model(rc, mid, rules)
    keep = np.ones(m["n"], dtype=bool) if rules is None else np.nan_to_num(m["CU_CUT"], nan=-1.0) > 0.3
    user = [0.0, 0.5, 1.0, 2.5] if cutoff_field == "AU_CUT" else [0.0, 400.0, 1000.0]
    cuts: list[float | None] = [*user, None]
    for key in ("ALT", "OXIDE"):
        ref = bf_ref(m, keep, cutoff_field, cuts, key=key)
        core = cp.Core(["AU_CUT", "CU_CUT", "PB"], cutoff_field, "SG", None,
                       edges_of(user, np.array(user) * 1.5 + 0.1, [0.05]), edges_of(user),
                       [cp.Key(key, ["G", "H", "Руда окисл. ёЁ"] if key == "ALT" else ["Ox", "Sulf"])])  # fmt: skip
        s = cp.run(model, core, r, chunk=7)
        assert ref["excluded_no_density"] == s.excluded["no_density"] >= (rules is None) * 2
        labels = core.keys[0].labels
        seen = set()
        for row in ref["rows"]:
            if row["group"] is None:
                q, n = total_row(s, row["cutoff"])
            else:
                code = 0 if row["group"] == "(пусто)" else list(labels).index(row["group"]) + 1
                q, n = group_row(s, code, row["cutoff"])
                seen.add(code)
            assert n[0] == row["blocks"], (key, row["group"], row["cutoff"])
            assert close(q[0], row["volume"]) and close(q[1], row["tonnes"]), (key, row["group"], row["cutoff"])
            for k, name in enumerate(("AU_CUT", "CU_CUT", "PB")):
                g = row["grades"][name]
                wt, tot, miss = q[2 + 3 * k], q[3 + 3 * k], q[4 + 3 * k]
                if g["grade"] is None:
                    assert wt == 0.0
                else:
                    assert close(tot / wt, g["grade"]), (key, row["group"], row["cutoff"], name)
                assert close(miss, g["missing_tonnes"]) or abs(miss - g["missing_tonnes"]) <= 1e-9, name
                unit = {"AU_CUT": "g/t", "CU_CUT": "%", "PB": "ppb"}[name]
                want = bf.metal(tot, unit)
                if unit == "%":
                    assert close(tot * 0.01, want["value"])
                else:
                    k_g = 1.0 if unit == "g/t" else 1e-3  # grams of a g/t·t sum
                    assert close(tot * k_g / 1000.0, want["kg"]) and close(tot * k_g / OZ / 1000.0, want["value"])
        # a group BF skipped has no counted block
        for code in range(len(labels) + 1):
            if code not in seen:
                assert group_row(s, code, None)[1][0] == 0


def test_cutoff_none_takes_blocks_without_the_cutoff_value(rc) -> None:  # type: ignore[no-untyped-def]
    """BF `cutoff=None`: every block of the group, the ones with no g_б too; a cut-off 0 — those with a value."""
    mid, m = bf_model2(rc)
    model, r = open_model(rc, mid)
    core = cp.Core(["AU_CUT"], "AU_CUT", "SG", None, np.array([0.0]), np.array([0.0]))
    s = cp.run(model, core, r)
    has_t = np.isfinite(m["SG"])
    assert total_row(s, None)[1][0] == int(has_t.sum())
    assert total_row(s, 0.0)[1][0] == int((has_t & np.isfinite(m["AU_CUT"])).sum())
    assert total_row(s, None)[1][0] > total_row(s, 0.0)[1][0]
    # no cut-off field at all: «без борта» is the whole tonnage
    s2 = cp.run(model, cp.Core(["AU_CUT"], None, "SG", None, np.empty(0), np.empty(0)), r)
    assert total_row(s2, None)[0][1] == total_row(s, None)[0][1] and s2.s0.shape[0] == 2


# --- Т8.10-4: benches -------------------------------------------------------------------------------------------------

ONE = {"corner": [0.0, 0.0, 0.0], "counts": [1, 1, 1], "size": [10.0, 10.0, 10.0]}


def bench_sums(rc: Any, z_first: float, height: float, count: int, grid: dict[str, Any] = ONE) -> cp.Sums:
    n = int(np.prod(grid["counts"]))
    mid = put(rc, grid, [{"name": "AU", "dtype": "f8", "unit": "g/t"}], np.arange(n, dtype=np.uint64),
              {"AU": np.full(n, 2.0)})  # fmt: skip
    model, r = open_model(rc, mid)
    key = cp.Key(None, bench=cp.Bench(z_first, height, count))
    core = cp.Core(["AU"], "AU", None, 2.5, np.array([1.0]), np.array([1.0]), [key])
    return cp.run(model, core, r)


def test_t8_10_4_bench_half_and_half(rc) -> None:  # type: ignore[no-untyped-def]
    s = bench_sums(rc, 0.0, 5.0, 2)  # the block Z 0…10, the boundary at 5
    for code in (1, 2):
        q, n = group_row(s, code, 1.0)
        assert q[1] == 2500.0 / 2 and q[0] == 500.0 and q[3] / q[2] == 2.0 and n[0] == 1
    assert group_row(s, 0, None)[1][0] == 0  # nothing outside
    q, n = total_row(s, 1.0)
    assert q[1] == 2500.0 and n[0] == 1  # the total counts the block once


def test_bench_part_outside_the_benches_is_empty_group(rc) -> None:  # type: ignore[no-untyped-def]
    s = bench_sums(rc, -5.0, 10.0, 1)  # one bench −5…5: half of the block in it, half outside (№ 107)
    assert group_row(s, 1, None)[0][1] == 2500.0 / 2
    assert group_row(s, 0, None)[0][1] == 2500.0 / 2
    s = bench_sums(rc, 20.0, 5.0, 2)  # the benches over the block: all of it is «(пусто)»
    assert group_row(s, 0, None)[0][1] == 2500.0 and group_row(s, 1, None)[1][0] == 0
    s = bench_sums(rc, 2.5, 5.0, 3)  # 0…2.5 outside, 2.5…7.5, 7.5…10 and the rest of 12.5…
    assert [group_row(s, c, None)[0][1] for c in range(4)] == [2500.0 / 4, 2500.0 / 2, 2500.0 / 4, 0.0]
    s = bench_sums(rc, 0.0, 5.0, 2)  # a boundary that falls on the edge of the block adds no empty piece
    assert group_row(s, 0, None)[1][0] == 0 and s.sg.shape[0] == 3 * 3


def brute_bench(m: dict[str, np.ndarray], z0: float, dz: float, benches: tuple[float, float, int]) -> dict:
    """Per (key digits, bench digit): fsum of volume, tonnes, Σ t·g, wt, blocks — block by block in Python."""
    first, h, count = benches
    acc: dict[tuple[int, int, int], list[list[float]]] = {}
    for i in range(len(m["AU"])):
        a, b = z0 + m["k"][i] * dz, z0 + (m["k"][i] + 1) * dz
        pieces: dict[int, float] = {}
        inside = 0.0
        for j in range(count):
            ov = max(0.0, min(b, first + (j + 1) * h) - max(a, first + j * h))
            if ov > 0:
                pieces[j + 1] = ov / dz
                inside += ov
        if dz - inside > 1e-12:
            pieces[0] = (dz - inside) / dz
        t = m["vol"] * m["DENS"][i]
        for d, sh in pieces.items():
            key = (int(m["DOM"][i]) + 1, int(m["OX"][i]) + 1, d)
            cell = acc.setdefault(key, [[], [], [], [], []])
            cell[0].append(m["vol"] * sh)
            cell[1].append(t * sh)
            if math.isfinite(m["AU"][i]):
                cell[2].append(t * sh * m["AU"][i])
                cell[3].append(t * sh)
            cell[4].append(1.0)
    return {k: [math.fsum(x) for x in v] for k, v in acc.items()}


def random_model(rc: Any, seed: int = 7) -> tuple[int, dict[str, Any], dict[str, Any]]:
    grid = {"corner": [100.0, 200.0, -13.3], "counts": [4, 3, 6], "size": [10.0, 10.0, 5.0]}
    n = 4 * 3 * 6
    rng = np.random.default_rng(seed)
    m: dict[str, Any] = {"AU": rng.lognormal(0.0, 1.0, n), "DENS": rng.uniform(2.0, 3.0, n),
                         "DOM": rng.integers(-1, 3, n).astype(np.int32),
                         "OX": rng.integers(-1, 2, n).astype(np.int32)}  # fmt: skip
    m["AU"][rng.random(n) < 0.2] = np.nan
    m["k"] = np.arange(n) // 12
    m["vol"] = 10.0 * 10.0 * 5.0
    fields = [{"name": "AU", "dtype": "f8", "unit": "g/t"}, {"name": "DENS", "dtype": "f8"},
              {"name": "DOM", "dtype": "cat", "labels": ["A", "B", "C"]},
              {"name": "OX", "dtype": "cat", "labels": ["o", "s"]}]  # fmt: skip
    mid = put(rc, grid, fields, np.arange(n, dtype=np.uint64), {k: m[k] for k in ("AU", "DENS", "DOM", "OX")})
    return mid, grid, m


def random_core(benches: tuple[float, float, int], order: str = "dom,ox,bench", **kw: Any) -> cp.Core:
    keys = {"dom": cp.Key("DOM", ["A", "B", "C"]), "ox": cp.Key("OX", ["o", "s"]),
            "bench": cp.Key(None, bench=cp.Bench(*benches))}  # fmt: skip
    return cp.Core(["AU"], "AU", "DENS", None, np.array([0.0, 1.0]), np.array([0.0, 1.0]),
                   [keys[k] for k in order.split(",")], **kw)  # fmt: skip


def test_t8_10_4_random_model_three_keys_against_brute_force(rc) -> None:  # type: ignore[no-untyped-def]
    mid, grid, m = random_model(rc)
    model, r = open_model(rc, mid)
    benches = (-20.0, 7.0, 4)  # −20…8: the top of the model (up to 16.7) and a part of the lowest layer outside
    core = random_core(benches)
    s = cp.run(model, core, r, chunk=11)
    ref = brute_bench(m, grid["corner"][2], 5.0, benches)
    n_dom, n_ox, n_b = 4, 3, 5
    total_t = 0.0
    for d1 in range(n_dom):
        for d2 in range(n_ox):
            for d3 in range(n_b):
                code = (d1 * n_ox + d2) * n_b + d3
                q, n = group_row(s, code, None)
                want = ref.get((d1, d2, d3))
                if want is None:
                    assert n[0] == 0 and q[1] == 0.0
                    continue
                assert close(q[0], want[0]) and close(q[1], want[1]) and close(q[3], want[2], 1e-11) \
                    and close(q[2], want[3]) and n[0] == int(want[4]), (d1, d2, d3)  # fmt: skip
                total_t += q[1]
    tot_q, tot_n = total_row(s, None)
    assert close(total_t, tot_q[1], 1e-12) and tot_n[0] == 72
    # the cut-off rows: the groups add up to the total (the shares of a block add up to 1)
    for c in (0.0, 1.0):
        parts = sum(group_row(s, code, c)[0][1] for code in range(core.n_groups()))
        assert close(parts, total_row(s, c)[0][1])


def test_bench_key_in_any_position_gives_the_same_cells(rc) -> None:  # type: ignore[no-untyped-def]
    mid, _, _ = random_model(rc)
    model, r = open_model(rc, mid)
    benches = (-15.0, 5.0, 6)
    a = cp.run(model, random_core(benches, "dom,ox,bench"), r)
    b = cp.run(model, random_core(benches, "bench,dom,ox"), r)
    for dd in range(4):
        for do in range(3):
            for db in range(7):
                qa, na = group_row(a, cp.code_of(a.core, [dd, do, db]), None)
                qb, nb = group_row(b, cp.code_of(b.core, [db, dd, do]), None)
                assert np.array_equal(qa, qb) and np.array_equal(na, nb), (dd, do, db)
    assert cp.group_label(a.core, cp.code_of(a.core, [2, 0, 3])) == ["B", None, "2"]


def test_plain_and_general_paths_agree(rc, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Whole parents on a regular grid take the fast path (one volume, the layer from P); the general path goes
    through `geometry`: the same sums (the volumes bit for bit, the bench shares to rounding)."""
    mid, _, _ = random_model(rc)
    model, r = open_model(rc, mid)
    plain = cp.run(model, random_core((-20.0, 7.0, 4)), r)
    plain_nb = cp.run(model, random_core((-20.0, 7.0, 4), "dom,ox"), r)
    monkeypatch.setattr(cp, "_plain", lambda *_: False)
    general = cp.run(model, random_core((-20.0, 7.0, 4)), r)
    general_nb = cp.run(model, random_core((-20.0, 7.0, 4), "dom,ox"), r)
    for name in ("s0", "c0", "n0", "sg", "cg", "ng"):
        assert getattr(plain_nb, name).tobytes() == getattr(general_nb, name).tobytes(), name
    assert plain.n0.tobytes() == general.n0.tobytes() and plain.ng.tobytes() == general.ng.tobytes()
    assert np.allclose(plain.sg + plain.cg, general.sg + general.cg, rtol=1e-12, atol=1e-9)


# --- the same bits with any portion -----------------------------------------------------------------------------------


@pytest.mark.parametrize("rules", [None, {"cond": {"field": "PB", "op": ">", "value": 0.2}}])  # a gap does not pass
def test_same_bits_with_portions_of_1_7_and_2_20_rows(rc, rules) -> None:  # type: ignore[no-untyped-def]
    mid, m = bf_model2(rc)
    model, r = open_model(rc, mid, rules)
    keys = [cp.Key("ALT", ["G", "H", "Руда окисл. ёЁ"]), cp.Key(None, bench=cp.Bench(-1.0, 1.0, 9)),
            cp.Key("OXIDE", ["Ox", "Sulf"])]  # fmt: skip
    core = cp.Core(
        ["AU_CUT", "CU_CUT", "PB"],
        "AU_CUT",
        "SG",
        None,
        edges_of([0.0, 0.5, 1.0], [0.25, 0.75, 2.0]),
        np.array([0.0, 0.5, 1.0]),
        keys,
    )
    runs = [cp.run(model, core, r, chunk=c) for c in (1, 7, 1 << 20)]
    assert runs[0].s0[:, 1].sum() > 0
    assert (runs[0].excluded["filtered"] > 0) == (rules is not None)
    assert runs[0].no_density_volume > 0
    for other in runs[1:]:
        for name in ("s0", "c0", "n0", "sg", "cg", "ng", "no_density_acc"):
            assert getattr(runs[0], name).tobytes() == getattr(other, name).tobytes(), name
        assert runs[0].excluded == other.excluded
    assert np.count_nonzero(runs[0].cg) > 0  # the carries are real: the test is not about zeros


# --- gaps, units of work, errors --------------------------------------------------------------------------------------


def test_a_gap_is_not_a_zero(rc) -> None:  # type: ignore[no-untyped-def]
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [2, 2, 1], "size": [10.0, 10.0, 10.0]}
    au = np.array([1.0, np.nan, 3.0, np.nan])
    cu = np.full(4, np.nan)
    mid = put(rc, grid, [{"name": "AU", "dtype": "f8"}, {"name": "CU", "dtype": "f8"}, {"name": "D", "dtype": "f8"}],
              np.arange(4, dtype=np.uint64), {"AU": au, "CU": cu, "D": np.array([2.0, 2.0, 2.0, np.nan])})  # fmt: skip
    model, r = open_model(rc, mid)
    s = cp.run(model, cp.Core(["AU", "CU"], None, "D", None, np.empty(0), np.empty(0)), r)
    q, n = total_row(s, None)
    assert n[0] == 3 and q[1] == 6000.0  # the block without density is out (its volume is counted apart)
    assert (q[2], q[3], q[4], n[1]) == (4000.0, 1000.0 * 2.0 * 1.0 + 1000.0 * 2.0 * 3.0, 2000.0, 1)  # AU: gap in one
    assert (q[5], q[6], q[7], n[2]) == (0.0, 0.0, 6000.0, 3)  # CU: all gaps — no mean, no metal, never a zero grade
    assert s.excluded["no_density"] == 1 and s.no_density_volume == 1000.0


def test_neumaier_sums_do_not_drift() -> None:
    n = 200_000
    x = np.full(n, 0.1)
    z = np.zeros((1, 5))
    s0, c0, n0 = z.copy(), z.copy(), np.zeros((1, 2), dtype=np.int64)
    sg, cg, ng = z.copy(), z.copy(), np.zeros((1, 2), dtype=np.int64)
    for lo in range(0, n, 3333):  # portions: the accumulators live between them
        part = x[lo : lo + 3333]
        k = len(part)
        kc = (np.zeros(k, np.int32),) * 3
        rk.accumulate(np.zeros(k, np.int32), np.zeros(k, np.int32), kc, np.zeros(3, np.int64), np.ones(3, np.int64),
                      False, part, part, np.ones((1, k)), s0, c0, n0, sg, cg, ng, 1)  # fmt: skip
    want = math.fsum(x)
    assert abs((s0[0, 1] + c0[0, 1]) - want) <= 2e-16 * want
    assert abs(float(np.cumsum(x)[-1]) - want) > 1e-13 * want  # a plain running sum is off by more than that


def test_suffix_sums_equal_range_sums_bit_for_bit() -> None:
    rng = np.random.default_rng(3)
    s, c = rng.normal(size=(2, 6, 5)) * 1e6, rng.normal(size=(2, 6, 5)) * 1e-9
    n = rng.integers(0, 9, size=(2, 6, 3))
    suf, cnt = rk.suffix_sums(s, c, n)
    for j in range(7):
        q, k = rk.range_sums(s, c, n, j, 6)
        assert suf[:, j].tobytes() == q.tobytes() and (cnt[:, j] == k).all()


def test_errors_are_russian_with_a_hint() -> None:
    base = dict(grades=["AU"], cutoff_field="AU", density_field=None, density_value=2.5, edges=np.empty(0),
                group_edges=np.empty(0))  # fmt: skip
    dom = cp.Key("DOM", ["a"])
    cases = [
        ({"keys": [dom, dom, dom, dom]}, "bad_param"),
        ({"keys": [cp.Key(None, bench=cp.Bench(0.0, 5.0, 2))] * 2}, "bad_param"),
        ({"keys": [cp.Key(None, bench=cp.Bench(0.0, 0.0, 2))]}, "bad_param"),
        ({"keys": [cp.Key(None)]}, "bad_param"),
        ({"keys": [], "density_value": None}, "bad_param"),
        ({"keys": [cp.Key("D", [str(i) for i in range(30_000)])]}, "report_too_big"),
    ]
    for over, code in cases:
        with pytest.raises(ApiError) as e:
            cp.Core(**{**base, **over}).check()  # type: ignore[arg-type]
        assert e.value.status == 422 and e.value.code == code
        assert e.value.message and e.value.hint and any("а" <= ch.lower() <= "я" for ch in e.value.message)
