"""The estimation kernel (ТЗ 8.8.1–8.8.2; ADR 0024 §5–7; Д-21, Д-27, Д-29, Д-30; Э18, 18.7): the etalon Э-ОК-1 through
the whole kernel; Д-21 — `kb2d` of GeostatsPy 0.0.46 (point: 1e−9; block: the closed form in NumPy, 1e−9) and `declus`;
Т8.8-13 — the same bits on 1, 2, 4, 8 threads and for any cut into portions; Т8.8-12 — the cross-validation (a sample
or a hole left out) equals the estimate without it, bit for bit; ТД27-1, ТД27-2 (the status of a block); ТД29-1 at the
level of the estimate; the sizes of `own`; one block in detail (preview, inspector); the NaN branches; `run` and the
cancel; the warm-up."""

from __future__ import annotations

import json
import math
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.geostat.estimate import kernel as kn
from bf_core.geostat.estimate.cov import BlockCov, cov_model
from bf_core.geostat.estimate.samples import RowSamples, declus_mean, prepare
from bf_core.geostat.estimate.spec import parse_estimation
from bf_core.geostat.variogram.model import Model, Structure

REF = Path(__file__).resolve().parents[2] / "tests" / "data" / "ref" / "geostatspy" / "geostatspy_reference.json"
SPH = {"c0": 0.1, "structures": [{"type": "spherical", "c": 0.9, "a1": 30, "a2": 30, "a3": 30}]}
TWO = {"c0": 0.15, "common_axes": False,
       "structures": [{"type": "spherical", "c": 0.5, "a1": 120, "a2": 70, "a3": 25, "ang1": 20, "ang2": 5, "ang3": 0},
                      {"type": "exponential", "c": 0.35, "a1": 300, "a2": 200, "a3": 60, "ang1": 20, "ang2": 5,
                       "ang3": 0}]}  # fmt: skip


def samples_of(xyz, z, hole=None) -> RowSamples:
    xyz = np.asarray(xyz, dtype=np.float64)
    n = xyz.shape[0]
    hole = np.zeros(n, np.int32) if hole is None else np.asarray(hole, dtype=np.int32)
    names = [f"h{i}" for i in range(int(hole.max()) + 1)]
    return prepare(xyz, np.asarray(z, dtype=np.float64), None, None, hole, names, np.zeros(n, np.int16),
                   np.arange(n, dtype=np.int64), np.zeros((n, 2)), origin=np.zeros(3))  # fmt: skip


def raw_samples(xyz, z) -> RowSamples:
    """RowSamples exactly as given, in this order, with coincident points (`prepare` would average them)."""
    xyz = np.asarray(xyz, dtype=np.float64)
    n = xyz.shape[0]
    z = np.asarray(z, dtype=np.float64)
    return RowSamples(xyz=xyz, local=xyz.copy(), z=z, raw=z.copy(), w=None, hole=np.arange(n, dtype=np.int32),
                      holes=[f"h{i}" for i in range(n)], dom=np.zeros(n, np.int16), dom_labels=[],
                      row_id=np.arange(n, dtype=np.int64), span=np.zeros((n, 2)), merged=np.ones(n, np.int16),
                      origin=np.zeros(3), counts={}, inputs=[],
                      means={"naive": float(z.mean()), "declus": None})  # fmt: skip


def spec_of(kind="ok", *, variogram=SPH, disc=(1, 1, 1), passes=None, r1=100.0, radii=None, angles=None,
            power=2.0, mean=None, outputs=None):  # fmt: skip
    search = {"from": "manual", "r1": r1, "passes": passes or [{"factor": 1, "n_min": 1, "n_max": 24}]}
    if radii is not None:
        search.update(r1=radii[0], r2=radii[1], r3=radii[2])
    if angles is not None:
        search.update(ang1=angles[0], ang2=angles[1], ang3=angles[2])
    body = {"model_id": 1, "samples": {"set_id": 1, "table": "t", "field": "z"}, "disc": list(disc),
            "method": {"kind": kind, "power": power}, "search": search}  # fmt: skip
    if mean is not None:
        body["method"]["mean"] = {"mode": "value", "value": mean}
    if variogram is not None:
        body["variogram"] = {"model_id": 1, "snapshot": variogram}
    if outputs is not None:
        body["outputs"] = {"fields": outputs}
    return parse_estimation(body)


def same(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape == b.shape and bool(np.all((a == b) | (np.isnan(a) & np.isnan(b))))


def assert_same(x: kn.ChunkResult, y: kn.ChunkResult) -> None:
    assert x.values.keys() == y.values.keys()
    for key in x.values:
        assert same(x.values[key], y.values[key]), key
    assert (x.status == y.status).all()


# --- Э-ОК-1 through the kernel ---------------------------------------------------------------------------------------

ETALON_XYZ = np.array([[0.0, 10.0, 0.0], [10.0, 0.0, 0.0], [-6.0, -8.0, 0.0], [20.0, 20.0, 0.0]])
ETALON_Z = np.array([1.0, 2.0, 3.0, 4.0])


def test_etalon_ok_sk_and_block_through_the_whole_kernel():
    s = raw_samples(ETALON_XYZ, ETALON_Z)
    every = ["EST", "KV", "NS", "NH", "PASS", "SLOPE", "KE", "LAGR", "SNW", "NOCT", "AD", "DMIN"]
    ok = kn.estimate(kn.compile_row(spec_of("ok", r1=60.0, outputs=every), s), np.zeros((1, 3)))
    assert ok.status[0] == kn.ST_OK
    assert ok.values["EST"][0] == pytest.approx(1.992880, abs=1e-6)
    assert ok.values["KV"][0] == pytest.approx(0.544418, abs=1e-6)
    assert ok.values["SLOPE"][0] == pytest.approx(0.954774, abs=1e-6)
    assert ok.values["SNW"][0] == pytest.approx(-0.025022, abs=1e-6)
    assert ok.values["LAGR"][0] == pytest.approx(-0.022653, abs=1e-6)
    assert ok.values["KE"][0] == pytest.approx(0.455582, abs=1e-6)
    assert ok.values["NS"][0] == 4 and ok.values["PASS"][0] == 1 and ok.values["NH"][0] == 4
    s_oct = {int(o) for o in kn.compile_row(spec_of("ok", r1=60.0), s).ell.octant(ETALON_XYZ)}
    assert ok.values["NOCT"][0] == len(s_oct) and ok.values["DMIN"][0] == pytest.approx(10.0)
    assert ok.values["AD"][0] == pytest.approx(float(np.linalg.norm(ETALON_XYZ, axis=1).mean()))
    sk = kn.estimate(kn.compile_row(spec_of("sk", r1=60.0, mean=2.5), s), np.zeros((1, 3)))
    assert sk.values["EST"][0] == pytest.approx(1.980912, abs=1e-6)
    assert sk.values["KV"][0] == pytest.approx(0.542931, abs=1e-6)
    assert "LAGR" not in sk.values  # SK has no multiplier
    blk = kn.estimate(kn.compile_row(spec_of("ok", disc=(4, 4, 1), r1=60.0), s, block_size=(10.0, 10.0, 0.0)),
                      np.zeros((1, 3)))  # fmt: skip
    assert blk.values["EST"][0] == pytest.approx(2.024652, abs=1e-6)
    assert blk.values["KV"][0] == pytest.approx(0.246936, abs=1e-6)


# --- Д-21: kb2d 0.0.46, declus ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ref() -> dict:
    return json.loads(REF.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ref_samples(ref) -> RowSamples:
    d = ref["data"]
    xyz = np.column_stack([d["X"], d["Y"], np.zeros(len(d["X"]))])
    return samples_of(xyz, d["V"])


KB2D_VARIOGRAM = {"c0": 0.1, "structures": [{"type": "spherical", "c": 0.9, "a1": 300, "a2": 300, "a3": 300}]}


@pytest.mark.parametrize("case", ["ok_point_all", "sk_point_all", "ok_point_search12", "ok_block4_all",
                                  "sk_block4_all"])  # fmt: skip
def test_d21_kb2d_point_and_block(ref, ref_samples, case):
    """Д-21: the estimate and the kriging variance of the grid of `kb2d` (GeostatsPy 0.0.46; the block cases — the
    closed form in NumPy with the convention of GSLIB), the same bits of arithmetic to 1e−9."""
    c = ref["kb2d"]["cases"][case]
    p = c["params"]
    kind = "ok" if p["ktype"] == 1 else "sk"
    disc = (p["nxdis"], p["nydis"], 1)
    spec = spec_of(kind, variogram=KB2D_VARIOGRAM, disc=disc, r1=p["radius"],
                   mean=p["skmean"] if kind == "sk" else None,
                   passes=[{"factor": 1, "n_min": p["ndmin"], "n_max": p["ndmax"]}])  # fmt: skip
    rk = kn.compile_row(spec, ref_samples, block_size=(p["xsiz"], p["ysiz"], 1.0))
    grid = np.array([[p["xmn"] + ix * p["xsiz"], p["ymn"] + iy * p["ysiz"], 0.0] for iy in range(p["ny"])
                     for ix in range(p["nx"])])  # fmt: skip
    res = kn.estimate(rk, grid)
    assert (res.status == kn.ST_OK).all()
    est = np.array(c["est"]).reshape(-1)
    var = np.array(c["var"]).reshape(-1)
    assert np.abs(res.values["EST"] - est).max() <= 1e-9
    assert np.abs(res.values["KV"] - var).max() <= 1e-9


def test_d21_declus_mean_of_the_reference_weights(ref):
    """`declus`: Σ w·z / Σ w of the weights of GeostatsPy is its declustered mean at the chosen cell size."""
    v = np.array(ref["data"]["V"])
    w = np.array(ref["declus"]["weights"])
    got = declus_mean(v, w)
    assert got == pytest.approx(min(ref["declus"]["declustered_mean"]), abs=1e-9)


# --- a random 3D set: threads, portions, the exclusion ---------------------------------------------------------------


def holes_set(seed: int, n_holes: int = 120, per: int = 14):
    rng = np.random.default_rng(seed)
    cx = rng.uniform(0.0, 1000.0, n_holes)
    cy = rng.uniform(0.0, 1000.0, n_holes)
    xyz, hole = [], []
    for h in range(n_holes):
        zs = np.sort(rng.uniform(0.0, 200.0, per))
        xyz += [[cx[h] + 0.2 * zz, cy[h], zz] for zz in zs]
        hole += [h] * per
    xyz = np.array(xyz)
    z = rng.lognormal(0.3, 0.7, len(xyz))
    return samples_of(xyz, z, np.array(hole)), rng


BODY_PASSES = [{"factor": 1, "n_min": 4, "n_max": 16, "n_hole": 4, "n_oct": 6, "k_oct": 1},
               {"factor": 2, "n_min": 3, "n_max": 24, "n_hole": 5}, {"factor": 3, "n_min": 2, "n_max": 24}]  # fmt: skip


@pytest.fixture(scope="module")
def big():
    s, rng = holes_set(11)
    spec = spec_of("ok", variogram=TWO, disc=(3, 3, 2), radii=(140.0, 80.0, 40.0), angles=(20.0, 5.0, 0.0),
                   passes=BODY_PASSES, outputs=["EST", "KV", "NS", "NH", "NHY", "AD", "DMIN", "PASS", "SLOPE", "KE",
                                                "LAGR", "SNW", "NOCT"])  # fmt: skip
    rk = kn.compile_row(spec, s, block_size=(20.0, 20.0, 10.0), rotation_deg=12.0)
    centres = np.column_stack([rng.uniform(-50.0, 1050.0, 9000), rng.uniform(-50.0, 1050.0, 9000),
                               rng.uniform(-20.0, 220.0, 9000)])  # fmt: skip
    return rk, centres


def test_t8_8_13_the_same_bits_on_1_2_4_8_threads_and_for_any_portions(big):
    rk, centres = big
    whole = kn.estimate(rk, centres)
    assert (whole.status == kn.ST_OK).sum() > 3000  # a real mixture: estimated and not
    assert len({int(x) for x in whole.values["PASS"][~np.isnan(whole.values["PASS"])]}) == 3  # all passes work
    for size in (9000, 2048, 777):
        parts = [centres[a : a + size] for a in range(0, len(centres), size)]
        for threads in (1, 2, 4, 8):
            got = list(kn.run(rk, parts, threads=threads))
            assert len(got) == len(parts)
            for key in whole.values:
                glued = np.concatenate([g.values[key] for g in got])
                assert same(glued, whole.values[key]), (key, size, threads)
            assert (np.concatenate([g.status for g in got]) == whole.status).all()
            assert (sum(g.counts for g in got) == whole.counts).all()
            assert (sum(g.by_pass for g in got) == whole.by_pass).all()


def test_the_blocks_of_a_portion_are_independent_of_their_neighbours(big):
    rk, centres = big
    whole = kn.estimate(rk, centres[:600])
    pick = np.array([5, 599, 17, 300])
    one = kn.estimate(rk, centres[pick])
    for key in one.values:
        assert same(one.values[key], whole.values[key][pick]), key


def test_t8_8_12_a_sample_or_a_hole_left_out_equals_the_estimate_without_it():
    """The exclusion of the cross-validation is the same computation as the row without the sample, bit for bit."""
    s, rng = holes_set(5, n_holes=40, per=10)
    spec = spec_of("ok", variogram=TWO, radii=(160.0, 90.0, 45.0), angles=(20.0, 5.0, 0.0), passes=BODY_PASSES)
    rk = kn.compile_row(spec, s)
    picks = rng.choice(s.n, 25, replace=False)
    for i in picks:
        got = kn.estimate(rk, s.xyz[i : i + 1], exclude=("sample", np.array([i])))
        rest = np.delete(np.arange(s.n), i)
        want = kn.estimate(kn.compile_row(spec, s.take(rest)), s.xyz[i : i + 1])
        assert_same(got, want)
        assert got.values["NS"][0] > 0
    holes = rng.choice(len(s.holes), 6, replace=False)
    for h in holes:
        i = int(np.flatnonzero(s.hole == h)[3])
        got = kn.estimate(rk, s.xyz[i : i + 1], exclude=("hole", np.array([h])))
        want = kn.estimate(kn.compile_row(spec, s.take(np.flatnonzero(s.hole != h))), s.xyz[i : i + 1])
        assert_same(got, want)


def test_the_kernel_is_the_search_plus_the_kriging(big):
    """The composition: the neighbours of `Searcher.gather` into `KrigeWork` give the bits of the kernel."""
    from bf_core.geostat.estimate import krige as kg

    rk, centres = big
    res = kn.estimate(rk, centres[:200])
    c = centres[:200] - rk.samples.origin
    work = kg.KrigeWork()
    offs = rk.geometry(rk.size)[0]
    done = 0
    for k in range(rk.n_passes):
        nb = rk.searcher.gather(c, k)
        for b in np.flatnonzero(nb.ok & (res.values["PASS"] == k + 1)):
            n = int(nb.n[b])
            assert work.run(rk.cov, rk.samples.local[nb.idx[b, :n]], nb.z[b, :n], c[b], offs, rk.cvv, 0.0, 1) == 0
            assert work.out[kg.O_EST] == res.values["EST"][b] and max(work.out[kg.O_KV], 0.0) == res.values["KV"][b]
            assert work.out[kg.O_LAGR] == res.values["LAGR"][b]
            done += 1
    assert done == int((res.status == kn.ST_OK).sum()) > 20


# --- Д-27: the status of a block -------------------------------------------------------------------------------------

TD27_SPH = {"c0": 0.0, "structures": [{"type": "spherical", "c": 1.0, "a1": 50, "a2": 50, "a3": 50}]}
TD27_PASSES = [{"factor": 1, "n_min": 4, "n_max": 24}, {"factor": 2, "n_min": 2, "n_max": 24}]


def td27_row():
    """A: six samples round the origin (pass 1 holds); B at x = 60: two samples within 20, three more within 40
    (pass 2); C at x = 200: two samples in ONE place, no nugget — a degenerate system, nothing else within reach."""
    xyz = [[-4, 0, 0], [3, 2, 0], [0, -5, 1], [2, 4, -1], [-3, 3, 2], [5, -2, 0],
           [55, 0, 0], [65, 3, 0], [30, 0, 0], [90, 0, 1], [95, 2, 0],
           [205, 0, 0], [205, 0, 0]]  # fmt: skip
    z = [1.0, 2.0, 3.0, 2.0, 1.5, 2.5, 4.0, 5.0, 3.0, 6.0, 7.0, 8.0, 9.0]
    s = raw_samples(xyz, z)
    spec = spec_of("ok", variogram=TD27_SPH, passes=TD27_PASSES, r1=20.0, outputs=["EST", "KV", "NS", "NH", "PASS"])
    return kn.compile_row(spec, s), s


def test_td27_1_estimated_in_pass_1_few_then_pass_2_degenerate_in_every_pass():
    rk, _ = td27_row()
    centres = np.array([[0.5, 0.0, 0.0], [60.0, 0.0, 0.0], [200.0, 0.0, 0.0]])
    res = kn.estimate(rk, centres)
    assert list(res.status) == [kn.ST_OK, kn.ST_OK, kn.ST_SINGULAR]
    assert list(res.values["PASS"][:2]) == [1, 2] and math.isnan(res.values["PASS"][2])
    assert res.values["NS"][1] == 5  # pass 2 took the five samples within 40
    for key in ("EST", "KV", "NS", "NH", "PASS"):  # the status 3: NaN, not 0
        assert math.isnan(res.values[key][2]), key
    assert list(res.values["STATUS"]) == [0.0, 0.0, 3.0]
    assert list(res.counts) == [2, 0, 0, 1] and list(res.by_pass) == [1, 1] and list(res.singular) == [0, 1]
    # the value of A does not change when a second pass exists: the same bits as a row with the first pass only
    one = kn.compile_row(spec_of("ok", variogram=TD27_SPH, r1=20.0, passes=TD27_PASSES[:1]), td27_row()[1])
    a = kn.estimate(one, centres[:1])
    assert a.values["EST"][0] == res.values["EST"][0] and a.values["KV"][0] == res.values["KV"][0]


def test_td27_few_samples_in_every_pass_is_status_2_and_a_pass_that_failed_goes_on():
    rk, _ = td27_row()
    far = kn.estimate(rk, np.array([[500.0, 0.0, 0.0], [150.0, 0.0, 0.0]]))
    assert list(far.status) == [kn.ST_FEW, kn.ST_FEW]  # nothing within 40 of either
    for key in ("EST", "KV", "NS", "PASS"):
        assert np.isnan(far.values[key]).all(), key
    # a pass whose system is degenerate passes the block on: two samples in one place (one hole); pass 2 takes
    # one sample of a hole, so the pair cannot both be chosen
    xyz = [[0, 0, 0], [0, 0, 0], [35, 0, 0], [-30, 5, 0], [10, 30, 0]]
    s = replace(raw_samples(xyz, [1.0, 2.0, 3.0, 4.0, 5.0]), hole=np.array([0, 0, 1, 2, 3], np.int32),
                holes=["a", "b", "c", "d"])  # fmt: skip
    two_passes = [{"factor": 1, "n_min": 2, "n_max": 24}, {"factor": 8, "n_min": 3, "n_max": 24, "n_hole": 1}]
    spec = spec_of("ok", variogram=TD27_SPH, r1=5.0, passes=two_passes)
    res = kn.estimate(kn.compile_row(spec, s), np.array([[1.0, 0.0, 0.0]]))
    assert res.status[0] == kn.ST_OK and res.values["PASS"][0] == 2 and list(res.singular) == [1, 0]
    assert res.values["NS"][0] == 4


def test_td27_2_a_block_outside_the_domain_is_status_1_and_nan():
    rk, _ = td27_row()
    centres = np.array([[0.5, 0.0, 0.0], [0.5, 0.0, 0.0], [60.0, 0.0, 0.0], [math.nan, 0.0, 0.0]])
    res = kn.estimate(rk, centres, active=np.array([True, False, True, True]))
    assert list(res.status) == [kn.ST_OK, kn.ST_OUT, kn.ST_OK, kn.ST_OUT]  # the mask; a NaN centre is not a place
    for key in ("EST", "KV", "NS", "NH", "PASS"):
        assert np.isnan(res.values[key][[1, 3]]).all() and np.isfinite(res.values[key][[0, 2]]).all(), key
    assert list(res.counts) == [2, 2, 0, 0] and res.by_pass.sum() == 2
    assert res.values["STATUS"][1] == 1.0 and res.values["EST"][0] == kn.estimate(rk, centres[:1]).values["EST"][0]


# --- Д-29 at the level of the estimate -------------------------------------------------------------------------------


def td29_row(hy):
    xyz = [[20.0, 0, 0], [0, 20.0, 0], [-20.0, 0, 0], [0, 0, 80.0]]  # h_s = 0.2·r1 three times, 0.8·r1 once
    s = raw_samples(xyz, [1.0, 1.0, 1.0, 50.0])
    pass_ = {"factor": 1, "n_min": 1, "n_max": 24}
    if hy is not None:
        pass_["hy"] = hy
    return kn.compile_row(spec_of("idw", r1=100.0, power=1.0, passes=[pass_], outputs=["EST", "NS", "NHY", "PASS"]), s)


def test_td29_1_drop_and_cap_at_the_level_of_idw():
    z0 = np.zeros((1, 3))
    drop = kn.estimate(td29_row({"t": 5.0, "q": 0.5, "mode": "drop"}), z0)
    assert drop.values["EST"][0] == 1.0 and drop.values["NS"][0] == 3 and drop.values["NHY"][0] == 1
    cap = kn.estimate(td29_row({"t": 5.0, "q": 0.5, "mode": "cap"}), z0)
    w = np.array([1 / 20.0, 1 / 20.0, 1 / 20.0, 1 / 80.0])
    assert cap.values["EST"][0] == pytest.approx(float((w * [1, 1, 1, 5.0]).sum() / w.sum()), abs=1e-12)
    off = kn.estimate(td29_row(None), z0)
    assert off.values["EST"][0] == pytest.approx(float((w * [1, 1, 1, 50.0]).sum() / w.sum()), abs=1e-12)
    q1 = kn.estimate(td29_row({"t": 5.0, "q": 1.0, "mode": "drop"}), z0)  # ТД29-2: q = 1 — the search without the rule
    assert q1.values["EST"][0] == off.values["EST"][0] and q1.values["NHY"][0] == 0


# --- the size of a block per block, NN / IDW outputs -----------------------------------------------------------------


def test_own_sizes_split_the_portion_by_size_and_equal_the_separate_rows(big):
    rk, centres = big
    c = centres[:300]
    sizes = np.where((np.arange(300) % 3 == 0)[:, None], [20.0, 20.0, 10.0], [10.0, 10.0, 5.0])
    spec = rk.spec
    got = kn.estimate(kn.compile_row(spec, rk.samples, rotation_deg=12.0, blockcov=rk.blockcov), c, sizes=sizes)
    big_rows = np.flatnonzero(np.arange(300) % 3 == 0)
    small_rows = np.flatnonzero(np.arange(300) % 3 != 0)
    for rows, size in ((big_rows, (20.0, 20.0, 10.0)), (small_rows, (10.0, 10.0, 5.0))):
        one = kn.compile_row(spec, rk.samples, block_size=size, rotation_deg=12.0, blockcov=rk.blockcov)
        want = kn.estimate(one, c[rows])
        for key in want.values:
            assert same(got.values[key][rows], want.values[key]), key
    with pytest.raises(ApiError) as e:  # a block with no size anywhere
        kn.estimate(kn.compile_row(spec, rk.samples, rotation_deg=12.0), c[:3])
    assert e.value.code == "no_block_size" and e.value.status == 422


def test_nn_and_idw_outputs_and_the_keys_by_method():
    s, rng = holes_set(2, n_holes=30, per=8)
    c = rng.uniform(100.0, 900.0, (50, 3)) * [1, 1, 0.2]
    for kind, keys in (("nn", {"EST", "DMIN", "PASS", "STATUS"}), ("idw", {"EST", "NS", "AD", "PASS", "STATUS"})):
        res = kn.estimate(kn.compile_row(spec_of(kind, variogram=None, r1=250.0), s), c)
        assert set(res.values) == keys
        ok = res.status == 0
        assert ok.sum() > 10 and np.isnan(res.values["EST"][~ok]).all()
    sk = kn.estimate(kn.compile_row(spec_of("sk", variogram=TWO, r1=250.0), s), c)
    assert {"KV", "SLOPE"} <= set(sk.values) and "LAGR" not in sk.values and "EST" in sk.values
    assert np.nanmax(sk.values["SLOPE"]) <= 1.0 + 1e-9 or True  # SK: the slope is 1 by definition
    assert np.nanmin(sk.values["SLOPE"]) == 1.0 and np.nanmax(sk.values["SLOPE"]) == 1.0


def test_kv_is_clipped_at_zero_and_counted():
    """A sample at the very point of the block with no nugget: the variance of the system is 0 or a hair below."""
    s = raw_samples([[0.0, 0, 0], [30.0, 0, 0], [0, 25.0, 0], [-20.0, -20.0, 0]], [1.0, 2.0, 3.0, 4.0])
    rk = kn.compile_row(spec_of("ok", variogram=TD27_SPH, r1=100.0), s)
    res = kn.estimate(rk, np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]))
    assert res.values["KV"][0] >= 0.0 and res.values["KV"][1] > 0.0
    assert res.neg_kv in (0, 1, 2)
    assert (np.nan_to_num(res.values["KV"], nan=0.0) >= 0.0).all()


# --- one block in detail ---------------------------------------------------------------------------------------------


def test_estimate_block_is_the_same_code_as_the_portion(big):
    rk, centres = big
    res = kn.estimate(rk, centres[:80])
    seen = {1: 0, 2: 0, 3: 0}
    for i in range(80):
        d = kn.estimate_block(rk, centres[i])
        assert d.status == res.status[i]
        for key in ("EST", "KV", "NS", "PASS", "NOCT", "LAGR", "SLOPE"):
            assert same(np.array([d.values[key]]), res.values[key][i : i + 1]), (i, key)
        if d.status == 0:
            seen[d.pass_] += 1
            assert d.sel.size == int(d.values["NS"]) == d.lam.size and d.lam.sum() == pytest.approx(1.0, abs=1e-9)
            assert d.mu == d.values["LAGR"]
            # the candidates of the inspector: the selected ones are exactly the samples of the kernel
            chosen = d.candidates.idx[np.isin(d.candidates.reason, (0, 1))]
            assert sorted(chosen.tolist()) == sorted(d.sel.tolist())
            assert np.isfinite(d.cand_lam[np.isin(d.candidates.reason, (0, 1))]).all()
            assert d.passes[-1]["reason"] is None and d.passes[-1]["pass"] == d.pass_
            assert d.ellipsoid["pass"] == d.pass_ and d.ellipsoid["r1"] == rk.ell.radii[0] * [1, 2, 3][d.pass_ - 1]
        else:
            assert d.sel.size == 0 and math.isnan(d.values["EST"])
    assert seen[1] > 0 and seen[2] > 0  # the sample covers more than one pass


def test_estimate_block_the_reasons_of_a_block_not_estimated_and_outside():
    rk, _ = td27_row()
    far = kn.estimate_block(rk, np.array([500.0, 0.0, 0.0]))
    assert far.status == kn.ST_FEW and far.pass_ == 0
    assert [p["reason"] for p in far.passes] == ["few", "few"] and far.ellipsoid["pass"] == 2
    c = kn.estimate_block(rk, np.array([200.0, 0.0, 0.0]))
    assert c.status == kn.ST_SINGULAR and c.passes[-1]["reason"] == "singular" and c.sel.size == 0
    out = kn.estimate_block(rk, np.array([0.5, 0.0, 0.0]), active=False)
    assert out.status == kn.ST_OUT and out.candidates.idx.size == 0 and out.passes == []
    nan = kn.estimate_block(rk, np.array([math.nan, 0.0, 0.0]))
    assert nan.status == kn.ST_OUT


def test_estimate_block_idw_and_nn_weights():
    s = raw_samples([[10.0, 0, 0], [0, 20.0, 0], [-30.0, 0, 0], [0, 0, 90.0]], [1.0, 2.0, 3.0, 4.0])
    d = kn.estimate_block(kn.compile_row(spec_of("idw", variogram=None, r1=100.0, power=2.0), s), np.zeros(3))
    w = 1.0 / np.array([10.0, 20.0, 30.0, 90.0]) ** 2
    assert d.lam == pytest.approx(w / w.sum(), abs=1e-15)
    assert d.values["EST"] == pytest.approx(float(w @ [1, 2, 3, 4] / w.sum()))
    n = kn.estimate_block(kn.compile_row(spec_of("nn", variogram=None, r1=100.0), s), np.zeros(3))
    assert list(n.lam) == [1.0, 0.0, 0.0, 0.0] and n.values["EST"] == 1.0


def test_estimate_block_speed(big, capsys):
    rk, centres = big
    kn.estimate_block(rk, centres[0])
    t = time.perf_counter()
    for i in range(20):
        kn.estimate_block(rk, centres[i])
    ms = (time.perf_counter() - t) / 20 * 1e3
    with capsys.disabled():
        print(f"\nestimate_block: {ms:.1f} ms per block")
    assert ms < 100.0  # the preview of ТЗ 8.8.4: ≤ 100 ms


# --- the errors and the edges ----------------------------------------------------------------------------------------


def test_errors_are_russian_with_a_hint_and_nothing_is_a_zero():
    s = raw_samples(ETALON_XYZ, ETALON_Z)
    body = {"model_id": 1, "samples": {"set_id": 1, "table": "t", "field": "z"}, "method": {"kind": "ok"},
            "variogram": {"model_id": 1}, "search": {"from": "manual", "r1": 50.0}}  # fmt: skip
    with pytest.raises(ApiError) as e:
        kn.compile_row(parse_estimation(body), s)
    assert e.value.code == "no_variogram" and e.value.hint and "вариограмм" in e.value.message
    body["search"] = {"from": "model"}  # the radii of a model that was not copied yet
    with pytest.raises(ApiError) as e2:
        kn.compile_row(parse_estimation(body), s)
    assert e2.value.code == "search_unresolved" and e2.value.hint
    empty = kn.estimate(kn.compile_row(spec_of("ok"), s), np.empty((0, 3)))
    assert empty.status.size == 0 and list(empty.counts) == [0, 0, 0, 0] and empty.values["EST"].size == 0
    sk = kn.compile_row(spec_of("sk"), s)  # no weights: the mean of SK is the naive one
    assert sk.mean == pytest.approx(2.5)
    no_mean = replace(s, means={"naive": None, "declus": None})
    with pytest.raises(ApiError) as e3:
        kn.compile_row(spec_of("sk"), no_mean)
    assert e3.value.code == "no_mean" and e3.value.status == 422
    with pytest.raises(ApiError) as e4:
        kn.compile_row(spec_of("ok"), s, block_size=(1.0, 2.0))
    assert e4.value.code == "bad_param"


def test_sk_mean_declustered_by_default_and_given():
    s = raw_samples(ETALON_XYZ, ETALON_Z)
    w = np.array([1.0, 1.0, 1.0, 5.0])
    weighted = replace(s, w=w, means={"naive": 2.5, "declus": float(w @ ETALON_Z / w.sum())})
    assert kn.compile_row(spec_of("sk"), weighted).mean == pytest.approx(3.25)
    assert kn.compile_row(spec_of("sk", mean=1.25), weighted).mean == 1.25


def test_block_cov_cache_is_shared_between_rows_of_one_model():
    s = raw_samples(ETALON_XYZ, ETALON_Z)
    bc = BlockCov(cov_model(Model(0.1, (Structure("spherical", 0.9, 30.0, 30.0, 30.0),))))
    a = kn.compile_row(spec_of("ok", disc=(4, 4, 1)), s, block_size=(10.0, 10.0, 1.0), blockcov=bc)
    b = kn.compile_row(spec_of("sk", disc=(4, 4, 1)), s, block_size=(10.0, 10.0, 1.0), blockcov=bc)
    assert a.blockcov is b.blockcov is bc and bc.computed == 1 and a.cvv == b.cvv


# --- run: the order and the cancel -----------------------------------------------------------------------------------


class Stop(Exception):
    pass


def test_run_keeps_the_order_of_the_portions_and_stops_on_the_cancel(big):
    rk, centres = big
    parts = [centres[a : a + 500] for a in range(0, 4000, 500)]
    whole = kn.estimate(rk, centres[:4000])
    for threads in (1, 3):
        ticks = []
        got = list(kn.run(rk, iter(parts), threads=threads, tick=lambda f, m, t=ticks: t.append((f, m))))
        assert same(np.concatenate([g.values["EST"] for g in got]), whole.values["EST"])
        assert ticks and all(t == (None, None) for t in ticks)
        calls = {"n": 0}

        def cancel(f, m, calls=calls):
            calls["n"] += 1
            if calls["n"] >= 6:
                raise Stop

        out = []
        with pytest.raises(Stop):
            for r in kn.run(rk, parts, threads=threads, tick=cancel):
                out.append(r)
        assert len(out) < len(parts)
    pieces = [kn.Portion(centres[:100], active=np.arange(100) % 2 == 0), centres[100:200]]
    two = list(kn.run(rk, pieces, threads=2))
    assert (two[0].status[1::2] == kn.ST_OUT).all() and two[1].status.size == 100


def test_the_kernel_works_on_read_only_samples_and_warms_up():
    s, _ = holes_set(9, n_holes=10, per=6)
    for a in (s.local, s.z, s.xyz):
        a.setflags(write=False)
    rk = kn.compile_row(spec_of("ok", variogram=TWO, r1=400.0), s)
    res = kn.estimate(rk, np.array([[500.0, 500.0, 100.0]]))
    assert res.status[0] == 0
    kn.warm_kernel()
