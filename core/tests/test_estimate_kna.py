"""KNA — the analysis of the kriging parameters (ТЗ 8.8.5; ADR 0024 §8, № 80; e18-api.md §8.2; Э18, 18.15): the rule of
the recommendation on a built example, the three auto blocks (the same ones for the same row, whatever the order of the
samples and of the blocks), the indicators = `kernel.estimate_block` bit for bit (a variant of the series = a row
compiled from scratch with that parameter), the cancel, the refusals in Russian, the form of `contract_answers.kna`,
the blocks of a real block model (the row, the parent, the point, the domain, the restrictor), the stored row and the
job in a worker, 3 blocks × 16 variants ≤ 5 s."""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.drillholes import store as dh
from bf_core.geostat.estimate import kernel as kn
from bf_core.geostat.estimate import kna
from bf_core.geostat.estimate.kna_blocks import BlockAt, Blocks, Targets
from bf_core.jobs.context import JobCancelled
from tests.estutil import W, body, make, world
from tests.test_estimate_kernel import ETALON_XYZ, ETALON_Z, raw_samples, samples_of, spec_of
from tests.test_vario_store import MODEL, noop

CONTRACT = json.loads((Path(__file__).parent / "data" / "estimate" / "contract_answers.json").read_text("utf-8"))
INDICATORS = ("KE", "SLOPE", "KV", "SNW", "w_far", "LAGR", "NS", "status")
PASSES = [{"factor": 1, "n_min": 3, "n_max": 24}, {"factor": 2, "n_min": 3, "n_max": 24},
          {"factor": 3, "n_min": 3, "n_max": 24}]  # fmt: skip


# --- a cloud of samples: dense, medium and sparse parts -----------------------------------------------------------


def cloud(seed: int = 5, shuffle: bool = False) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    parts = [(1500, 50.0), (1000, 200.0), (500, 400.0)]  # how many, the side of the square they fill
    xyz = np.concatenate([np.column_stack([rng.uniform(0, side, n), rng.uniform(0, side, n), rng.uniform(0, 20, n)])
                          for n, side in parts])  # fmt: skip
    z = 2.0 + 0.01 * xyz[:, 0] + 0.5 * np.sin(xyz[:, 1] / 15.0) + rng.normal(0, 0.3, len(xyz))
    if shuffle:
        order = np.random.default_rng(seed + 1).permutation(len(xyz))
        return xyz[order], z[order]
    return xyz, z


def grid_targets(step: float = 10.0, side: float = 400.0, shuffle: int | None = None) -> Targets:
    ax = np.arange(step / 2, side, step)
    centres = np.array([[x, y, z] for z in (5.0, 15.0) for y in ax for x in ax])
    n = len(centres)
    row = np.arange(n, dtype=np.int64)
    ijk = np.column_stack([(centres[:, 0] // step), (centres[:, 1] // step), (centres[:, 2] // 10.0)]).astype(np.int64)
    size = np.tile([step, step, 10.0], (n, 1))
    t = Targets(row, row.astype(np.uint64), ijk, centres, size, n)
    if shuffle is None:
        return t
    o = np.random.default_rng(shuffle).permutation(n)
    return Targets(t.row[o], t.p[o], t.ijk[o], t.centre[o], t.size[o], n)


def cloud_row(shuffle: bool = False, disc: tuple[int, int, int] = (2, 2, 2), **kw: Any) -> kn.RowKernel:
    xyz, z = cloud(shuffle=shuffle)
    spec = spec_of("ok", disc=disc, r1=40.0, passes=PASSES, **kw)
    return kn.compile_row(spec, samples_of(xyz, z))


def block(centre: Any, size: Any = (10.0, 10.0, 10.0), **kw: Any) -> BlockAt:
    return BlockAt(0, 0, (0, 0, 0), np.asarray(centre, dtype=np.float64), np.asarray(size, dtype=np.float64), **kw)


def same_bits(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a.keys() == b.keys() and all(a[k] == b[k] for k in a)


# --- the rule of the recommendation -------------------------------------------------------------------------------


def rows_of(slopes: list[float | None], status: int = 0) -> list[dict[str, Any]]:
    return [{"value": 4 * (i + 1), "SLOPE": s, "status": status} for i, s in enumerate(slopes)]


def test_recommend_is_the_smallest_n_max_within_95_percent_of_the_best() -> None:
    # SLOPE grows with n_max, peaks (0.97) at 28 and falls a little: 0.95 · 0.97 = 0.9215 → 16 (0.93), not 12 (0.90)
    slopes = [0.60, 0.80, 0.90, 0.93, 0.95, 0.96, 0.97, 0.96, 0.95, 0.94]
    assert kna.recommend(rows_of(slopes)) == 16
    assert kna.recommend(rows_of([0.60, 0.80, 0.90])) == 12 and kna.recommend(rows_of([1.0, 0.99])) == 4
    # exactly 95 % of the best counts; just under — not
    assert kna.recommend([{"value": 8, "SLOPE": 0.95, "status": 0}, {"value": 12, "SLOPE": 1.0, "status": 0}]) == 8
    assert kna.recommend([{"value": 8, "SLOPE": 0.9499, "status": 0}, {"value": 12, "SLOPE": 1.0, "status": 0}]) == 12
    # the order of the rows does not matter
    assert kna.recommend(list(reversed(rows_of(slopes)))) == 16


def test_recommend_skips_blocks_without_an_estimate_and_a_best_slope_not_above_zero() -> None:
    mixed = rows_of([0.2, None, 0.95, 0.96])
    mixed[0]["status"] = 2  # few samples: its SLOPE (if any) is not an estimate
    assert kna.recommend(mixed) == 12
    assert kna.recommend(rows_of([0.5, 0.9], status=2)) is None  # no estimated row
    assert kna.recommend(rows_of([None, None])) is None and kna.recommend([]) is None
    assert kna.recommend(rows_of([-0.2, -0.1])) is None and kna.recommend(rows_of([0.0, 0.0])) is None


def test_the_common_recommendation_is_the_largest_of_the_blocks() -> None:
    rk = cloud_row()
    blocks = [block([25, 25, 10], auto="много"), block([100, 100, 10], auto="средне"),
              block([300, 300, 10], auto="мало")]  # fmt: skip
    out = kna.analyse_blocks(rk, blocks, ["n_max"])
    recs = [b["recommend"]["n_max"] for b in out["blocks"]]
    assert all(r is not None and r in kna.N_MAX for r in recs)
    assert out["recommend"] == {"n_max": max(recs)}
    for b in out["blocks"]:  # self-consistent with the series: the first value that holds the 95 % line
        series = b["vary"]["n_max"]
        best = max(r["SLOPE"] for r in series if r["status"] == 0)
        first = next(r["value"] for r in series if r["status"] == 0 and r["SLOPE"] >= 0.95 * best)
        assert b["recommend"]["n_max"] == first
    # nothing recommended where n_max is not varied
    other = kna.analyse_blocks(rk, blocks[:1], ["disc"])
    assert other["recommend"] == {} and other["blocks"][0]["recommend"] == {}


def test_etalon_block_indicators_and_a_series_that_does_not_move() -> None:
    """Э-ОК-1 (4 samples): the indicators of the etalon; every n_max ≥ 4 takes the same four, so the recommendation is
    the smallest value of the series."""
    s = raw_samples(ETALON_XYZ, ETALON_Z)
    rk = kn.compile_row(spec_of("ok", r1=60.0), s)
    out = kna.analyse_blocks(rk, [block([0, 0, 0], (1.0, 1.0, 1.0))], ["n_max"])
    base = out["blocks"][0]["base"]
    assert base["KV"] == pytest.approx(0.544418, abs=1e-6) and base["SLOPE"] == pytest.approx(0.954774, abs=1e-6)
    assert base["KE"] == pytest.approx(0.455582, abs=1e-6) and base["LAGR"] == pytest.approx(-0.022653, abs=1e-6)
    assert base["SNW"] == pytest.approx(-0.025022, abs=1e-6) and base["NS"] == 4 and base["status"] == 0
    assert base["w_far"] == pytest.approx(-0.025022, abs=1e-6)  # the farthest sample (20, 20, 0) is the negative one
    series = out["blocks"][0]["vary"]["n_max"]
    assert [r["value"] for r in series] == list(kna.N_MAX) and all(r["SLOPE"] == base["SLOPE"] for r in series)
    assert out["recommend"] == {"n_max": 4}


# --- the three blocks ---------------------------------------------------------------------------------------------


def test_pick_quantiles_ranks_by_the_count_then_by_the_row() -> None:
    n = np.arange(11)
    assert kna.pick_quantiles(n, np.arange(11), np.ones(11, bool)) == [(9, "много"), (5, "средне"), (1, "мало")]
    # ties: the same count — the smaller row first; the order of the arrays does not matter
    n = np.array([5, 5, 5, 5, 1, 1, 9, 9])
    row = np.array([40, 30, 20, 10, 7, 6, 3, 2])
    got = kna.pick_quantiles(n, row, np.ones(8, bool))
    perm = np.random.default_rng(1).permutation(8)
    again = kna.pick_quantiles(n[perm], row[perm], np.ones(8, bool))
    assert [(int(row[i]), t) for i, t in got] == [(int(row[perm][i]), t) for i, t in again]
    # blocks out of reach are not ranked; two quantiles on one block give it once
    ok = np.array([False, True, False, True, True])
    got = kna.pick_quantiles(np.array([9, 3, 0, 2, 1]), np.arange(5), ok)
    assert got == [(1, "много"), (3, "средне"), (4, "мало")]  # the block 0 (9 samples) is out of reach
    assert kna.pick_quantiles(np.arange(2), np.arange(2), np.ones(2, bool)) == [(1, "много"), (0, "мало")]
    assert kna.pick_quantiles(np.arange(1), np.arange(1), np.ones(1, bool)) == [(0, "много")]
    assert kna.pick_quantiles(np.arange(3), np.arange(3), np.zeros(3, bool)) == []


def test_three_auto_blocks_are_dense_medium_and_sparse() -> None:
    rk = cloud_row()
    t = grid_targets()
    picks = kna.auto_blocks(rk, t)
    assert [b.auto for b in picks] == ["много", "средне", "мало"]
    counts = [b.n_r1 for b in picks]
    assert counts[0] > counts[1] >= counts[2] >= 0
    # n_r1 is what a plain count says: the samples of D inside the first radius (r1 · factor of the first pass)
    for b in picks:
        h = rk.ell.h(rk.local - (b.centre - rk.samples.origin))
        assert b.n_r1 == int((h <= 40.0).sum())
    # the ranks: the counts of the picks are those of the 0.9, 0.5, 0.1 quantiles of the blocks within reach of a pass
    every = np.array([int((rk.ell.h(rk.local - (c - rk.samples.origin)) <= 120.0).sum()) for c in t.centre])
    first = np.array([int((rk.ell.h(rk.local - (c - rk.samples.origin)) <= 40.0).sum()) for c in t.centre])
    ranked = np.sort(first[every >= 3])
    assert counts == [int(ranked[int(np.floor(q * (ranked.size - 1) + 0.5))]) for q in (0.9, 0.5, 0.1)]


def test_enough_in_is_count_in_at_least_k() -> None:
    rk = cloud_row()
    t = grid_targets()
    for radius in (40.0, 120.0, 400.0):
        n = kna.count_in(rk, t.centre, radius)
        for k in (1, 3, 25):
            assert (kna.enough_in(rk, t.centre, radius, k) == (n >= k)).all()
    # a sample exactly on the surface of the ball is inside (closed ball, as for `count_in`)
    centre = np.asarray(rk.samples.origin, dtype=np.float64)[None, :]  # the local origin: the sample is at `edge`
    edge = rk.local[rk.samples.dom == kna.sm.D][0]
    r = float(np.linalg.norm(rk.ell.S @ edge))
    assert kna.count_in(rk, centre, r)[0] >= 1 and kna.enough_in(rk, centre, r, 1)[0]


def test_auto_blocks_are_the_same_whatever_the_order_of_samples_and_blocks() -> None:
    ref = kna.auto_blocks(cloud_row(), grid_targets())
    key = [(b.row, b.auto, b.n_r1, tuple(b.centre)) for b in ref]
    assert key == [(b.row, b.auto, b.n_r1, tuple(b.centre)) for b in kna.auto_blocks(cloud_row(), grid_targets())]
    for seed in (1, 2, 3):  # the blocks in another order of the file
        got = kna.auto_blocks(cloud_row(), grid_targets(shuffle=seed))
        assert [(b.row, b.auto, b.n_r1, tuple(b.centre)) for b in got] == key
    shuffled = kna.auto_blocks(cloud_row(shuffle=True), grid_targets())  # the samples in another order
    assert [(b.row, b.auto, b.n_r1) for b in shuffled] == [(b.row, b.auto, b.n_r1) for b in ref]


def test_auto_blocks_leave_out_blocks_no_pass_can_reach_and_refuse_when_none_is_left() -> None:
    rk = cloud_row()
    far = Targets(np.arange(2), np.arange(2, dtype=np.uint64), np.zeros((2, 3), np.int64),
                  np.array([[5000.0, 5000.0, 5.0], [6000.0, 5000.0, 5.0]]), np.full((2, 3), 10.0), 2)  # fmt: skip
    with pytest.raises(ApiError) as e:
        kna.auto_blocks(rk, far)
    assert (e.value.status, e.value.code) == (422, "kna_no_blocks") and e.value.message and e.value.hint
    mixed = grid_targets()
    sub = Targets(*(a[:3] for a in (mixed.row, mixed.p, mixed.ijk, mixed.centre, mixed.size)), total=3)
    sub.centre[2] = [9000.0, 9000.0, 5.0]
    assert all(b.row != 2 for b in kna.auto_blocks(rk, sub))


# --- the indicators are `estimate_block` --------------------------------------------------------------------------


def fresh_ref(rk: kn.RowKernel, spec_change: dict[str, Any], b: BlockAt, scale: float = 1.0) -> dict[str, Any]:
    """The block by a row compiled from scratch with `spec_change` (a new tree, the block size in `block_size`)."""
    spec = dataclasses.replace(rk.spec, **spec_change)
    new = kn.compile_row(spec, rk.samples, block_size=tuple(b.size * scale))
    return kna.indicators(new, kn.estimate_block(new, b.centre, active=b.active))


def with_passes(rk: kn.RowKernel, f: Any) -> dict[str, Any]:
    s = rk.spec.search
    return {"search": dataclasses.replace(s, passes=tuple(f(p) for p in s.passes))}


def test_every_variant_is_estimate_block_of_a_row_compiled_with_that_parameter() -> None:
    rk = cloud_row(disc=(3, 3, 2))
    blocks = kna.auto_blocks(rk, grid_targets())
    out = kna.analyse_blocks(rk, blocks, kna.VARY)
    assert len(out["blocks"]) == 3
    for b, got in zip(blocks, out["blocks"], strict=True):
        assert same_bits(got["base"], fresh_ref(rk, {}, b))
        v = got["vary"]
        assert [r["value"] for r in v["n_max"]] == list(range(4, 65, 4))
        assert [r["value"] for r in v["radius"]] == [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
        assert [r["value"] for r in v["disc"]] == [[d, d, d] for d in range(1, 6)]
        assert [r["value"] for r in v["size"]] == [0.5, 1.0, 2.0]
        for r in v["n_max"]:
            ref = fresh_ref(rk, with_passes(rk, lambda p, n=r["value"]: dataclasses.replace(  # noqa: B023
                p, n_max=n, n_min=min(p.n_min, n))), b)  # fmt: skip
            assert same_bits({k: r[k] for k in INDICATORS}, ref), r["value"]
        for r in v["radius"]:
            ref = fresh_ref(rk, with_passes(rk, lambda p, f=r["value"]: dataclasses.replace(  # noqa: B023
                p, factor=p.factor * f)), b)  # fmt: skip
            assert same_bits({k: r[k] for k in INDICATORS}, ref), r["value"]
        for r in v["disc"]:
            assert same_bits({k: r[k] for k in INDICATORS}, fresh_ref(rk, {"disc": tuple(r["value"])}, b)), r["value"]
        for r in v["size"]:
            assert same_bits({k: r[k] for k in INDICATORS}, fresh_ref(rk, {}, b, r["value"])), r["value"]
        assert v["n_max"][-1]["value"] == 64 and v["size"][1]["KV"] == got["base"]["KV"]  # ×1 is the row itself


def test_radius_by_the_ellipsoid_and_by_the_passes_agree() -> None:
    """The variants scale the radius of every pass; a row whose ellipsoid is scaled gives the same indicators (the
    factors here are exact in binary, so no rounding apart)."""
    rk = cloud_row()
    b = block([60, 60, 10])
    out = kna.analyse_blocks(rk, [b], ["radius"])["blocks"][0]["vary"]["radius"]
    for r in out:
        spec = spec_of("ok", disc=(2, 2, 2), r1=40.0 * r["value"], passes=PASSES)
        new = kn.compile_row(spec, rk.samples, block_size=tuple(b.size))
        assert same_bits({k: r[k] for k in INDICATORS}, kna.indicators(new, kn.estimate_block(new, b.centre)))


def test_a_point_row_has_no_size_effect_and_says_so() -> None:
    rk = cloud_row(disc=(1, 1, 1))
    out = kna.analyse_blocks(rk, [block([60, 60, 10])], ["size", "disc"])["blocks"][0]["vary"]
    assert len({json.dumps({k: r[k] for k in INDICATORS}) for r in out["size"]}) == 1
    assert out["disc"][0]["KV"] == out["size"][0]["KV"]  # d = 1 is the point
    assert out["disc"][3]["KV"] != out["disc"][0]["KV"]  # a block has less variance than a point


def test_a_block_out_of_the_row_has_empty_indicators_never_zeros() -> None:
    rk = cloud_row()
    out = kna.analyse_blocks(rk, [block([60, 60, 10], active=False), block([9000, 9000, 10])], ["n_max"])
    for got in out["blocks"]:
        for r in [got["base"], *got["vary"]["n_max"]]:
            assert r["status"] in (1, 2)
            assert all(r[k] is None for k in ("KE", "SLOPE", "KV", "SNW", "w_far", "LAGR", "NS"))
        assert got["recommend"] == {"n_max": None}
    assert out["blocks"][0]["base"]["status"] == 1 and out["blocks"][1]["base"]["status"] == 2
    assert out["recommend"] == {"n_max": None}
    json.dumps(out, allow_nan=False)  # a gap is null, not NaN


# --- cancel, refusals, the form -----------------------------------------------------------------------------------


def test_cancel_stops_between_variants_and_leaves_nothing() -> None:
    rk = cloud_row()
    seen = [0]

    def tick(_p: float | None = None, _m: str | None = None) -> None:
        seen[0] += 1
        if seen[0] == 20:
            raise JobCancelled()

    with pytest.raises(JobCancelled):
        kna.analyse_blocks(rk, [block([60, 60, 10]), block([200, 200, 10])], kna.VARY, tick=tick)
    assert seen[0] == 20  # not one more calculation after the cancel
    seen[0] = 0
    assert kna.analyse_blocks(rk, [block([60, 60, 10])], ["n_max"], tick=lambda *_: None)["blocks"]


def test_progress_runs_forward_to_the_end_in_the_stage_variants() -> None:
    rk = cloud_row()
    marks: list[tuple[float | None, str | None]] = []
    kna.analyse_blocks(rk, [block([60, 60, 10]), block([200, 200, 10])], ["n_max", "size"],
                       tick=lambda p=None, m=None: marks.append((p, m)))  # fmt: skip
    ps = [p for p, _ in marks if p is not None]
    assert ps == sorted(ps) and 0.3 <= ps[0] and ps[-1] < 1.0 and {m for _, m in marks} == {"Варианты"}


def test_vary_and_methods_are_checked_in_russian() -> None:
    assert kna.parse_vary(None) == kna.VARY and kna.parse_vary(["size", "n_max", "size"]) == ("n_max", "size")
    for bad, param in ((["step"], "vary.0"), ([], "vary"), ("n_max", "vary"), (["n_max", 4], "vary.1")):
        with pytest.raises(ApiError) as e:
            kna.parse_vary(bad)
        assert (e.value.status, e.value.code, e.value.extra["param"]) == (422, "bad_param", param)
        assert e.value.message and e.value.hint
    for kind in ("nn", "idw"):
        spec = spec_of(kind, variogram=None)
        with pytest.raises(ApiError) as e:
            kna.analyse(None, spec)
        assert (e.value.status, e.value.code, e.value.extra["param"]) == (422, "kna_method", "method.kind")
        assert e.value.message and e.value.hint


def test_the_answer_has_the_form_of_the_contract() -> None:
    ref = CONTRACT["kna"]["body"]["blocks"][0]
    rk = cloud_row()
    out = kna.analyse_blocks(rk, [block([60, 60, 10], auto="много", n_r1=41)], kna.VARY)
    got = out["blocks"][0]
    assert set(ref) <= set(got) and set(ref["at"]) == set(got["at"]) and len(got["centre"]) == 3
    assert set(ref["base"]) <= set(got["base"]) == set(INDICATORS)
    for name in ref["vary"]:
        assert set(ref["vary"][name][0]) <= set(got["vary"][name][0]) and got["vary"][name][0]["status"] in range(4)
    assert set(got["recommend"]) == set(ref["recommend"])
    assert set(out["recommend"]) == set(CONTRACT["kna"]["body"]["recommend"])
    assert isinstance(got["vary"]["disc"][0]["value"], list) and isinstance(got["vary"]["n_max"][0]["NS"], int)
    json.dumps(out, allow_nan=False)


def test_the_time_of_3_blocks_by_16_variants() -> None:
    """The budget of the plan: 3 blocks × 16 variants (the series of n_max) ≤ 5 s on 3000 samples, 3 passes; the whole
    set of series — as much again."""
    rk = cloud_row(disc=(4, 4, 2))
    blocks = [block(c) for c in ([25, 25, 10], [100, 100, 10], [300, 300, 10])]
    kna.analyse_blocks(rk, blocks[:1], ["n_max"])  # warm: the compiled kernels
    t0 = time.perf_counter()
    out = kna.analyse_blocks(rk, blocks, ["n_max"])
    took = time.perf_counter() - t0
    assert [len(b["vary"]["n_max"]) for b in out["blocks"]] == [16, 16, 16]
    assert took <= 5.0, took
    t0 = time.perf_counter()
    kna.analyse_blocks(rk, blocks, kna.VARY)
    assert time.perf_counter() - t0 <= 10.0


# --- a real block model -------------------------------------------------------------------------------------------


def add_samples(w: W, seed: int = 11) -> tuple[int, int]:
    """A set of vertical holes through the block model of the world: dense in the corner, medium, then a few far
    apart; six samples a hole (every 2.5 m of the 15 m of the model). → (set_id, table_id)."""
    rng = np.random.default_rng(seed)
    with dialect.read_tx(w.project.engine) as conn:
        model = Model.open(conn, w.store, w.model)
    xy = [(x, y) for x in np.arange(2.5, 17.5, 5.0) for y in np.arange(2.5, 17.5, 5.0)]
    xy += [(x, y) for x in (22.0, 32.0, 42.0) for y in (3.0, 13.0, 23.0, 33.0)]
    xy += [(47.0, 37.0), (28.0, 38.0), (8.0, 36.0)]
    q = model.grid.to_project(np.array([[x, y, 15.0] for x, y in xy]))
    names = [f"K{i:02d}" for i in range(len(xy))]
    with dialect.write_tx(w.project.engine) as conn:
        set_id = dh.create_set(conn, "Для KNA")
        collars = [dh.CollarRow(n, x=float(p[0]), y=float(p[1]), z=float(p[2]), td=15.0, azimuth=0.0, dip=-90.0)
                   for n, p in zip(names, q, strict=True)]  # fmt: skip
        dh.replace_collars(conn, set_id, collars)
    holes = [n for n in names for _ in range(6)]
    frm = [2.5 * k for _ in names for k in range(6)]
    au = [1.0 + 0.04 * xy[i][0] + rng.normal(0, 0.25) for i in range(len(names)) for _ in range(6)]
    table = dh.write_table(w.project.engine, set_id, dh.TableData(
        name="Пробы KNA", kind="assay", holes=holes, from_m=frm, to_m=[f + 2.5 for f in frm],
        columns=[dh.ColumnSpec("AU", "number", "г/т", "component")], values=[au]), job_id=None, tick=noop)  # fmt: skip
    return set_id, int(table["table_id"])


KNA_SEARCH = {"from": "manual", "r1": 14.0, "r2": 14.0, "r3": 9.0,
              "passes": [{"factor": 1, "n_min": 3, "n_max": 24}, {"factor": 2, "n_min": 3, "n_max": 24}]}  # fmt: skip


@pytest.fixture()
def kw(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    w.kna_set, w.kna_table = add_samples(w)
    yield w
    w.project.engine.dispose()


def kbody(w: W, **over: Any) -> dict[str, Any]:
    b = body(w, samples={"set_id": w.kna_set, "table": str(w.kna_table), "field": "AU"}, target={},
             search=KNA_SEARCH, disc=[2, 2, 1], variogram={"model_id": w.vz, "snapshot": MODEL})  # fmt: skip
    b.update(over)
    return b


def kmake(w: W, **over: Any) -> int:
    raw = kbody(w, **over)
    return int(make(w, **{k: v for k, v in raw.items() if k != "model_id"})["id"])


def blocks_of(w: W, **over: Any) -> Blocks:
    from bf_core.geostat.estimate.spec import parse_estimation

    spec = parse_estimation(kbody(w, **over))
    with dialect.read_tx(w.project.engine) as conn:
        return Blocks.open(conn, w.store, spec)


def test_blocks_of_a_row_are_parents_or_rows_in_the_domain_and_the_restrictor(kw: W) -> None:
    bl = blocks_of(kw)
    t = bl.targets()
    assert t.n == t.total == len(np.unique(bl.p)) and np.array_equal(t.p, np.unique(bl.p))  # sub: parent
    own = blocks_of(kw, sub="own").targets()
    assert own.n == bl.model.rows == 350
    # the centre and the size are those of the parent / the row of the model
    c = geometry.centres(bl.model.grid, t.p)
    assert np.array_equal(t.centre, c) and np.array_equal(t.size, np.tile([5.0, 5.0, 2.5], (t.n, 1)))
    # the domain: DOM = руда
    dom = blocks_of(kw, sub="own", target={"domain_field": "DOM", "label": "руда"})
    code = bl.model.columns(["DOM"])["DOM"]
    assert dom.targets().n == int((code == bl.model.labels("DOM").index("руда")).sum()) > 0
    both = blocks_of(kw, target={"domain_field": "DOM", "label": "руда", "filter_id": kw.flt})
    assert 0 < both.targets().n < dom.targets().n  # the restrictor «AU > 0.1» takes some of them away
    # a value the field does not have: no block
    with pytest.raises(ApiError) as e:
        blocks_of(kw, target={"domain_field": "N", "label": "777"}).targets()
    assert e.value.code == "kna_no_blocks" and e.value.message and e.value.hint


def test_a_block_is_found_by_the_row_the_indices_or_the_point(kw: W) -> None:
    bl = blocks_of(kw)
    t = bl.targets()
    i = t.n // 2
    by_row = bl.place({"row": int(t.row[i])})
    by_ijk = bl.place({"ijk": [int(v) for v in t.ijk[i]]})
    by_xyz = bl.place({"xyz": [float(v) for v in t.centre[i]]})
    for b in (by_row, by_ijk, by_xyz):
        assert (b.p, b.ijk, b.active) == (int(t.p[i]), tuple(int(v) for v in t.ijk[i]), True)
        assert np.array_equal(b.centre, t.centre[i]) and np.array_equal(b.size, [5.0, 5.0, 2.5])
    assert by_row.at() == {"row": int(t.row[i]), "p": int(t.p[i]), "ijk": list(by_row.ijk)}
    for at, param in (({"row": 10**6}, "blocks.row"), ({"ijk": [99, 0, 0]}, "blocks.ijk.0"),
                      ({"xyz": [0.0, 0.0, 1e6]}, "blocks.xyz")):  # fmt: skip
        with pytest.raises(ApiError) as e:
            bl.place(at)
        assert (e.value.status, e.value.extra["param"]) == (422, param) and e.value.message and e.value.hint
    # a block outside the domain is placed, but not active
    dom = blocks_of(kw, sub="own", target={"domain_field": "DOM", "label": "руда"})
    outside = int(np.flatnonzero(~dom.mask)[0])
    assert dom.place({"row": outside}).active is False and dom.place({"row": int(np.flatnonzero(dom.mask)[0])}).active


def test_analyse_over_the_block_model_is_the_kernel_on_the_same_rows(kw: W) -> None:
    from bf_core.geostat.estimate.spec import parse_estimation

    spec = parse_estimation(kbody(kw))
    out = kna.analyse(kw.project, spec, None, ["n_max", "disc"])
    assert [b["auto"] for b in out["blocks"]] == ["много", "средне", "мало"] and out["candidates"] > 0
    assert out["method"] == "ok" and out["vary"] == ["n_max", "disc"] and out["threshold"] == 0.95
    json.dumps(out, allow_nan=False)
    # one of the blocks, by the kernel itself: the samples as the row loads them, the block of the model
    from bf_core.geostat.estimate import samples as sm

    s = sm.load(kw.project, spec)
    got = out["blocks"][0]
    rk = kn.compile_row(spec, s, block_size=tuple(got["size"]), rotation_deg=blocks_of(kw).rotation)
    d = kn.estimate_block(rk, np.array(got["centre"]))
    assert same_bits(got["base"], kna.indicators(rk, d)) and got["base"]["status"] == 0
    # the same blocks again, and the blocks asked for by their rows give the same numbers
    again = kna.analyse(kw.project, spec, [{"row": b["at"]["row"]} for b in out["blocks"]], ["n_max", "disc"])
    assert [b["base"] for b in again["blocks"]] == [b["base"] for b in out["blocks"]]
    assert [b["vary"] for b in again["blocks"]] == [b["vary"] for b in out["blocks"]]
    assert [b["auto"] for b in again["blocks"]] == [None] * 3 and all(b["n_r1"] is not None for b in again["blocks"])
    assert kna.analyse(kw.project, spec, None, ["n_max", "disc"]) == out  # deterministic


def test_analyse_refuses_a_bad_list_of_blocks(kw: W) -> None:
    from bf_core.geostat.estimate.spec import parse_estimation

    spec = parse_estimation(kbody(kw))
    for raw in ([], [{"row": 0}] * 11, "x"):
        with pytest.raises(ApiError) as e:
            kna.analyse(kw.project, spec, raw)
        assert (e.value.status, e.value.extra["param"]) == (422, "blocks") and e.value.message and e.value.hint
    with pytest.raises(ApiError) as e:
        kna.analyse(kw.project, spec, [{"row": 0}, {"row": 1, "ijk": [0, 0, 0]}])
    assert e.value.extra["param"] == "blocks.1"


def test_run_kna_of_a_stored_row_and_of_a_body(kw: W) -> None:
    eid = kmake(kw)
    saved = kna.run_kna(kw.project.engine, kw.project.folder, {"estimation_id": eid, "vary": ["n_max"]})
    assert saved["estimation_id"] == eid and saved["name"] and saved["recommend"].keys() == {"n_max"}
    assert len(saved["blocks"]) == 3 and saved["blocks"][0]["auto"] == "много"
    inline = kna.run_kna(kw.project.engine, kw.project.folder, {"estimation": kbody(kw), "vary": ["n_max"]})
    assert inline["estimation_id"] is None and inline["name"] is None
    # the stored row keeps its origin, the body finds it by the samples: the same origin, the same numbers
    assert [b["base"] for b in inline["blocks"]] == [b["base"] for b in saved["blocks"]]
    both = {"estimation_id": eid, "estimation": kbody(kw)}
    refused = (({}, "bad_param", 422), (both, "bad_param", 422), ({"estimation_id": True}, "bad_param", 422),
               ({"estimation_id": 99999}, "estimation_not_found", 404))  # fmt: skip
    for params, code, status in refused:
        with pytest.raises(ApiError) as e:
            kna.run_kna(kw.project.engine, kw.project.folder, params)
        assert (e.value.status, e.value.code) == (status, code) and e.value.message and e.value.hint


def test_cancel_in_the_job_body_writes_nothing(kw: W) -> None:
    eid = kmake(kw)
    with dialect.read_tx(kw.project.engine) as conn:
        from bf_core.geostat.estimate import store as st

        before = st.get(conn, eid)
    n = [0]

    def tick(_p: float | None = None, _m: str | None = None) -> None:
        n[0] += 1
        if n[0] > 40:
            raise JobCancelled()

    with pytest.raises(JobCancelled):
        kna.run_kna(kw.project.engine, kw.project.folder, {"estimation_id": eid}, tick)
    with dialect.read_tx(kw.project.engine) as conn:
        assert st.get(conn, eid) == before


# --- the job in a worker ------------------------------------------------------------------------------------------


def test_the_job_in_a_worker_gives_what_the_body_gives(pclient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`estimate.kna` registered here and in the worker (`BF_EXTRA_JOB_MODULES`) until the line of the orchestrator
    (`jobs/registry.py`); the route is 18.13's."""
    import importlib

    from bf_core.jobs.registry import HANDLER_MODULES
    from bf_core.jobs.service import jobs_of
    from bf_core.projects.service import projects_of
    from tests import estutil as eu
    from tests.test_import_job import API, wait_job
    from tests.test_vario_store import fill

    module = "bf_core.geostat.estimate.kna"
    if module not in HANDLER_MODULES:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", module)
    importlib.import_module(module)
    assert pclient.post(API + "/projects", json={"name": "KNA"}).status_code == 200
    project = projects_of(pclient.core).require_current()
    set_id, a, b = fill(project.engine)
    w = eu.fill_world(project, set_id, a, b)
    w.kna_set, w.kna_table = add_samples(w)
    eid = kmake(w)
    params = {"estimation_id": eid, "vary": ["n_max", "size"]}
    here = kna.run_kna(project.engine, project.folder, params)
    key, job_id = jobs_of(pclient.core).submit(kna.KIND, params, title="KNA")
    job = wait_job(pclient, key, job_id, timeout=240)
    assert job["status"] == "done", job
    assert job["result"] == json.loads(json.dumps(here)) and job["result"]["blocks"][0]["auto"] == "много"
    bad = jobs_of(pclient.core).submit(kna.KIND, {"estimation_id": 999_999}, title="KNA")
    failed = wait_job(pclient, *bad, timeout=120)
    assert failed["status"] == "failed" and failed["error"]["code"] == "estimation_not_found"
    assert failed["error"]["hint"]
