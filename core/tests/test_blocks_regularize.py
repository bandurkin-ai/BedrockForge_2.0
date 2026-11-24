"""Regularization of a block model (подшаг 17.25; ТЗ 8.7.5, Т8.7-7; ADR 0023 decision 14): exact overlaps against a
brute-force reference, volume, tonnage and metal kept (1e−9), the mode of a category and `P_<КОД>`, FRAC, gaps (never a
zero), sub-blocks into parents, the origin a multiple of the size, bit for bit at 1, 2, 4 and 8 threads and at any
size of a portion, 422/404, a cancel leaves nothing, the commit (meta, source, CRS)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import regularize as rg
from bf_core.blocks import schema as bs
from bf_core.blocks import store as bm
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.storage.blobs import BlobStore
from tests.test_blocks_fields_store import (  # noqa: F401  (fixtures)
    GRID,
    bc,
    blocks_router,
    project,
    tmp_clean,
)


@pytest.fixture()
def bp(bc):  # type: ignore[no-untyped-def]  # noqa: F811
    return bc


LABELS = ["руда", "порода", "дайка"]
FIELDS = [
    {"name": "AU", "dtype": "f8", "unit": "г/т", "role": "grade"},
    {"name": "CU", "dtype": "f4", "unit": "%"},
    {"name": "SG", "dtype": "f8", "role": "density"},
    {"name": "DOM", "dtype": "cat", "labels": LABELS, "role": "code"},
    {"name": "FRAC", "dtype": "f8", "role": "frac"},
]
PLAIN = {"corner": [100.0, 200.0, -50.0], "counts": [4, 4, 2], "size": [1.0, 1.0, 1.0], "rotation": 0}


def build(bc_: Any, grid: dict[str, Any], fields: list[dict[str, Any]], p: np.ndarray, values: dict[str, np.ndarray],
          s: np.ndarray | None = None, w: np.ndarray | None = None, row_group: int = 1 << 20) -> Model:  # fmt: skip
    """A committed model of the rows given (ascending by (P, sz, sy, sx)) → opened for reading."""
    pr = project(bc_)
    store = BlobStore(pr.folder)
    with bm.CellsWriterV2(store, grid, fields, row_group=row_group) as writer:
        writer.write(p, values, s, w)
        staged = writer.finish()
    try:
        with dialect.write_tx(pr.engine) as conn:
            mid = bm.commit_block_model(conn, store, staged, name="Исходная")
    finally:
        staged.discard()
    return open_model(bc_, mid)


def open_model(bc_: Any, mid: int) -> Model:
    pr = project(bc_)
    with dialect.read_tx(pr.engine) as conn:
        return Model.open(conn, BlobStore(pr.folder), mid)


def store_of(bc_: Any) -> BlobStore:
    return BlobStore(project(bc_).folder)


def done(bc_: Any, model: Model, res: rg.Result, name: str = "Результат") -> tuple[Model, dict[str, Any]]:
    pr = project(bc_)
    with dialect.write_tx(pr.engine) as conn:
        reply = rg.commit(conn, BlobStore(pr.folder), model, res, name=name)
    return open_model(bc_, reply["model_id"]), reply


def run(bc_: Any, model: Model, size: Any, **kw: Any) -> tuple[Model, dict[str, Any]]:
    res = rg.regularize(model, store_of(bc_), size, **kw)
    return done(bc_, model, res)


def all_fields(model: Model) -> dict[str, np.ndarray]:
    return model.columns([f["name"] for f in model.fields])


def same_bits(a: np.ndarray, b: np.ndarray) -> bool:
    if a.dtype.kind == "f":
        return bool(np.array_equal(a.view(np.uint64), b.view(np.uint64)))
    return bool(np.array_equal(a, b))


def rel(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


def sample(n: int, rng: np.random.Generator, total: int = 4 * 4 * 2) -> np.ndarray:
    return np.sort(rng.choice(total, size=n, replace=False)).astype(np.uint64)


def random_values(n: int, rng: np.random.Generator, gaps: bool = True) -> dict[str, np.ndarray]:
    au = rng.lognormal(0.0, 0.5, n)
    cu = rng.random(n).astype(np.float32).astype(np.float64)
    if gaps:
        au[rng.random(n) < 0.15] = np.nan
    return {"AU": au, "CU": cu, "SG": rng.normal(2.7, 0.1, n), "DOM": rng.integers(-1 if gaps else 0, 3, n).astype(
        np.int32), "FRAC": rng.uniform(0.3, 1.0, n)}  # fmt: skip


def boxes_of(model: Model) -> tuple[np.ndarray, np.ndarray]:
    """(lo, hi) of the rows in the local axes of the model: n×3."""
    b = model.boxes()
    return b[:, :3], b[:, 3:]


def brute(src: Model, dst: Model, size: tuple[float, float, float], field: str, weight: str = "volume",
          dens: str | None = None) -> np.ndarray:  # fmt: skip
    """The mean per new cell by exact pairwise overlaps in the local axes of `src` (a loop, no kernel): the new cells
    are found by their centres, which `dst` knows, turned into the axes of `src`."""
    lo, hi = boxes_of(src)
    cols = src.columns([field, *([dens] if dens else []), "FRAC"])
    centres = src.grid.to_local(dst.centres())
    half = np.asarray(size) / 2
    out = np.full(len(centres), np.nan)
    for c, ctr in enumerate(centres):
        ov = np.prod(np.clip(np.minimum(hi, ctr + half) - np.maximum(lo, ctr - half), 0, None), axis=1)
        wgt = ov * cols["FRAC"]
        if dens:
            wgt = wgt * cols[dens]
        ok = ~np.isnan(cols[field]) & (wgt > 0)
        if ok.any():
            out[c] = float(np.sum(wgt[ok] * cols[field][ok]) / np.sum(wgt[ok]))
    return out


# --- Т8.7-7: the sums kept; exact overlaps ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [(2.0, 2.0, 2.0), (1.5, 2.5, 1.25), (0.5, 3.0, 0.75)])
def test_t8_7_7_volume_tonnage_metal_kept(bp, size) -> None:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(11)
    p = sample(24, rng)
    src = build(bp, GRID_ROT, FIELDS, p, random_values(24, rng, gaps=False))
    for weight in ("volume", "tonnes"):
        grades = [{"field": "AU", "weight": weight}, {"field": "CU", "weight": "volume"}]
        dst, reply = run(bp, src, size, grades=grades, density="SG", categories=["DOM"])
        before, after = reply["totals"]["before"], reply["totals"]["after"]
        assert rel(before["volume"], after["volume"]) < 1e-9
        assert rel(before["tonnes"], after["tonnes"]) < 1e-9
        # the independent sums over the models themselves
        v_src = float(np.sum(src.volume()))
        assert rel(v_src, before["volume"]) < 1e-12
        cols = dst.columns(["FRAC", "SG", "AU"])
        vcell = size[0] * size[1] * size[2]
        assert rel(v_src, float(np.sum(cols["FRAC"]) * vcell)) < 1e-9
        t_src = float(np.sum(src.volume() * src.columns(["SG"])["SG"]))
        assert rel(t_src, float(np.sum(cols["FRAC"] * vcell * cols["SG"]))) < 1e-9
        if weight == "tonnes":  # metal = tonnes · grade is kept when the grade is weighted by tonnes
            m_src = float(np.sum(src.volume() * src.columns(["SG"])["SG"] * src.columns(["AU"])["AU"]))
            m_dst = float(np.sum(cols["FRAC"] * vcell * cols["SG"] * cols["AU"]))
            assert rel(m_src, m_dst) < 1e-9
            assert rel(before["metal"]["AU"], after["metal"]["AU"]) < 1e-9
        assert reply["totals"]["metal_basis"] == "tonnes" and set(before["metal"]) == {"AU", "CU"}
        assert rel(m_src_of(src, "CU"), before["metal"]["CU"]) < 1e-12


def m_src_of(src: Model, field: str) -> float:
    cols = src.columns([field, "SG"])
    return float(np.sum(src.volume() * cols["SG"] * cols[field]))


GRID_ROT = {**GRID, "counts": [4, 4, 2], "size": [1.0, 1.0, 1.0], "rotation": 30}


def test_overlaps_exact_against_brute_force(bp) -> None:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(5)
    p = sample(26, rng)
    src = build(bp, GRID_ROT, FIELDS, p, random_values(26, rng))
    size = (1.3, 0.9, 1.1)  # nothing fits: every row meets several cells
    dst, _ = run(bp, src, size, grades=[{"field": "AU", "weight": "volume"}], categories=[])
    assert dst.grid.rotation == 30 and dst.grid.sub.sub is False
    got = dst.columns(["AU"])["AU"]
    want = brute(src, dst, size, "AU")
    assert np.allclose(got, want, rtol=1e-12, atol=0, equal_nan=True)
    assert np.array_equal(np.isnan(got), np.isnan(want))


def test_tonnes_weight_against_brute_force(bp) -> None:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(6)
    p = sample(20, rng)
    src = build(bp, GRID_ROT, FIELDS, p, random_values(20, rng))
    size = (1.7, 1.7, 0.8)
    dst, _ = run(bp, src, size, grades=[{"field": "AU", "weight": "tonnes"}], density="SG", categories=[])
    want = brute(src, dst, size, "AU", dens="SG")
    assert np.allclose(dst.columns(["AU"])["AU"], want, rtol=1e-12, atol=0, equal_nan=True)


# --- the origin, the grid, the fields --------------------------------------------------------------------------------


def test_origin_is_a_multiple_of_the_size_and_phi_is_kept(bp) -> None:  # type: ignore[no-untyped-def]
    grid = {**PLAIN, "counts": [10, 10, 4], "rotation": 90}
    p = np.asarray([3 + 10 * (4 + 10 * 1), 4 + 10 * (5 + 10 * 2)], dtype=np.uint64)  # parents (3,4,1) and (4,5,2)
    src = build(bp, grid, [{"name": "AU", "dtype": "f8"}], p, {"AU": np.asarray([1.0, 3.0])})
    dst, reply = run(bp, src, (2.0, 2.0, 2.0), grades=["AU"], categories=[])
    # cover (3…5, 4…6, 1…3) m → origin (2, 4, 0) in the local axes: a multiple of the size, turned by 90° exactly
    q0 = src.grid.to_local(np.asarray([dst.grid.corner]))[0]
    assert np.allclose(q0, [2.0, 4.0, 0.0], atol=1e-12)
    assert dst.grid.counts == (2, 1, 2) and dst.grid.rotation == 90 and dst.grid.size == (2.0, 2.0, 2.0)
    assert dst.meta["source"]["op"] == "regularize" and dst.meta["algo"] == rg.ALGO
    assert dst.meta["inputs"]["model"] == [{"id": src.id, "sha": src.shas()["cells"]}]
    assert reply["rows"] == dst.rows == 2 and dst.parents == 2 and dst.sub is False
    frac = dst.columns(["FRAC"])["FRAC"]
    assert dst.role_field("frac")["name"] == "FRAC" and np.allclose(sorted(frac), [0.125, 0.125])  # 1 m³ of 8


def test_mode_and_p_code_ties_and_gaps(bp) -> None:  # type: ignore[no-untyped-def]
    codes = np.zeros((4, 4, 2), dtype=np.int32)
    codes[:2, :2, 1] = 1  # new cell A: 4 of «руда» (z 0) and 4 of «порода» (z 1): a tie → the lower code, P 0.5 / 0.5
    codes[2:, :2, :] = 2  # cell B: «дайка» but one parent of «руда»: mode 2, P 7/8 and 1/8
    codes[2, 0, 0] = 0
    codes[:2, 2:, :] = -1  # cell C: no category at all
    codes[2:, 2:, 0] = 1
    codes[2:, 2:, 1] = -1  # cell D: 4 of «порода», 4 empty → P 1.0, the empty are not in the share
    p = np.arange(32, dtype=np.uint64)
    i, j, k = p % 4, (p // 4) % 4, p // 16
    vals = {"DOM": codes[i, j, k], "AU": np.arange(32, dtype=np.float64)}
    src = build(bp, PLAIN, [FIELDS[3], {"name": "AU", "dtype": "f8"}], p, vals)
    dst, _ = run(bp, src, (2.0, 2.0, 2.0), grades=["AU"], categories=["DOM"])
    cols = dst.columns(["DOM", "P_руда", "P_порода", "P_дайка", "AU", "FRAC"])
    a, b, c, d = range(4)  # the file order: (0,0), (1,0), (0,1), (1,1) of the new grid
    assert list(cols["DOM"][[a, b, c, d]]) == [0, 2, -1, 1]
    assert np.allclose(cols["P_руда"][[a, b, d]], [0.5, 0.125, 0.0]) and np.isnan(cols["P_руда"][c])
    assert np.allclose(cols["P_порода"][[a, b, d]], [0.5, 0.0, 1.0])
    assert np.allclose(cols["P_дайка"][[a, b, d]], [0.0, 0.875, 0.0])
    assert np.allclose(cols["FRAC"], 1.0) and not np.isnan(cols["AU"]).any()
    assert dst.labels("DOM") == LABELS and dst.field("P_руда")["dtype"] == "f8"
    assert dst.field("DOM")["role"] == "code" and dst.field("FRAC")["role"] == "frac"
    # P_ of a category of a cell with codes: the shares sum to 1
    ok = [a, b, d]
    assert np.allclose(cols["P_руда"][ok] + cols["P_порода"][ok] + cols["P_дайка"][ok], 1.0)


def test_frac_of_an_edge_and_of_a_partial_source(bp) -> None:  # type: ignore[no-untyped-def]
    grid = {**PLAIN, "counts": [3, 1, 1]}
    p = np.asarray([0, 1, 2], dtype=np.uint64)
    fields = [{"name": "AU", "dtype": "f8"}, {"name": "FRAC", "dtype": "f8", "role": "frac"}]
    src = build(bp, grid, fields, p, {"AU": np.asarray([1.0, 3.0, 5.0]), "FRAC": np.asarray([1.0, 0.5, 0.25])})
    dst, _ = run(bp, src, (2.0, 1.0, 1.0), grades=["AU"], categories=[])
    cols = dst.columns(["AU", "FRAC"])
    # cell 0 = parents 0, 1 (frac 1, 0.5): AU = (1·1 + 3·0.5) / 1.5; FRAC = 1.5 / 2.  cell 1 = parent 2 and the edge
    assert np.allclose(cols["AU"], [(1.0 + 1.5) / 1.5, 5.0])
    assert np.allclose(cols["FRAC"], [0.75, 0.125])


def test_gaps_are_not_zero(bp) -> None:  # type: ignore[no-untyped-def]
    p = np.arange(32, dtype=np.uint64)
    au = np.arange(32, dtype=np.float64) + 1.0
    au[(p % 4 < 2) & ((p // 4) % 4 < 2) & (p // 16 == 0)] = np.nan  # cell (0,0,0): half of it empty
    cu = np.ones(32)
    cu[(p % 4 < 2) & ((p // 4) % 4 < 2)] = np.nan  # the whole column of cells (0,0,*) has no CU
    src = build(bp, PLAIN, [{"name": "AU", "dtype": "f8"}, {"name": "CU", "dtype": "f8"}], p, {"AU": au, "CU": cu})
    dst, _ = run(bp, src, (2.0, 2.0, 1.0), grades=["AU", "CU"], categories=[])
    cols = dst.columns(["AU", "CU", "FRAC"])
    first = (p % 4 < 2) & ((p // 4) % 4 < 2) & (p // 16 == 0)
    assert np.isnan(au[first]).all() and np.isnan(cols["AU"][0])  # all four parents empty → NaN, not 0
    assert np.isnan(cols["CU"][0]) and np.isnan(cols["CU"][4])  # the cells of the empty column stay empty
    nxt = (p % 4 >= 2) & ((p // 4) % 4 < 2) & (p // 16 == 0)
    assert np.allclose(cols["AU"][1], np.mean(au[nxt])) and np.allclose(cols["CU"][1], 1.0)
    assert np.allclose(cols["FRAC"], 1.0)


def test_subblocks_into_parents_and_unequal_subblocks(bp) -> None:  # type: ignore[no-untyped-def]
    grid = {**PLAIN, "counts": [2, 1, 1], "size": [4.0, 4.0, 4.0], "div": [4, 4, 4]}
    # parent 0: eight octants with the values 1…8; parent 1: a 1×4×4 slab (w = 1 of 4 in x) and a 3×4×4 slab
    s = [[0, 0, 0], [2, 0, 0], [0, 2, 0], [2, 2, 0], [0, 0, 2], [2, 0, 2], [0, 2, 2], [2, 2, 2], [0, 0, 0], [1, 0, 0]]
    w = [[2, 2, 2]] * 8 + [[1, 4, 4], [3, 4, 4]]
    p = np.asarray([0] * 8 + [1, 1], dtype=np.uint64)
    order = np.lexsort((np.asarray(s)[:, 0], np.asarray(s)[:, 1], np.asarray(s)[:, 2], p))
    vals = np.asarray([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 20.0])
    src = build(bp, grid, [{"name": "AU", "dtype": "f8"}], p[order], {"AU": vals[order]}, np.asarray(s)[order],
                np.asarray(w)[order])  # fmt: skip
    dst, _ = run(bp, src, (4.0, 4.0, 4.0), grades=["AU"], categories=[])
    assert dst.grid.counts == (2, 1, 1) and dst.sub is False
    assert np.allclose(dst.columns(["AU"])["AU"], [4.5, (10.0 * 1 + 20.0 * 3) / 4])
    assert np.allclose(dst.columns(["FRAC"])["FRAC"], 1.0)
    # to a bigger cell: the volume-weighted mean of the two parents, equal volumes
    big, _ = run(bp, src, (8.0, 4.0, 4.0), grades=["AU"], categories=[])
    assert np.allclose(big.columns(["AU"])["AU"], [(4.5 + 17.5) / 2])


def test_a_model_far_apart_in_z_streams_in_segments(bp) -> None:  # type: ignore[no-untyped-def]
    grid = {**PLAIN, "counts": [3, 3, 60]}
    k = np.asarray([0, 0, 1, 30, 31, 59])
    p = (np.asarray([0, 4, 8, 0, 4, 8]) + 9 * k).astype(np.uint64)
    order = np.argsort(p)
    au = np.asarray([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    src = build(bp, grid, [{"name": "AU", "dtype": "f8"}], p[order], {"AU": au[order]})
    whole, _ = run(bp, src, (2.0, 2.0, 2.0), grades=["AU"], categories=[])
    small = rg.regularize(src, store_of(bp), (2.0, 2.0, 2.0), grades=["AU"], categories=[], chunk=2)
    cut, _ = done(bp, src, small, "Порции")
    for f in ("AU", "FRAC"):
        assert same_bits(whole.columns([f])[f], cut.columns([f])[f])
    assert np.array_equal(whole.p(), cut.p()) and whole.rows == cut.rows
    assert whole.grid.counts[2] == 30  # the cover of layers 0…59 in 2 m cells, the empty middle is not written
    assert whole.rows < 30


# --- the threads, the portions ---------------------------------------------------------------------------------------


def test_bitwise_at_any_threads_and_portions(bp) -> None:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(21)
    grid = {**GRID, "counts": [12, 11, 9], "size": [5.0, 4.0, 2.5], "rotation": -40, "div": [2, 2, 2]}
    total = 12 * 11 * 9
    ps, ss, ws = [], [], []
    for par in np.sort(rng.choice(total, size=420, replace=False)):
        if rng.random() < 0.3:
            for c in range(8):  # eight octants
                ps.append(par)
                ss.append([c & 1, (c >> 1) & 1, c >> 2])
                ws.append([1, 1, 1])
        else:
            ps.append(par)
            ss.append([0, 0, 0])
            ws.append([2, 2, 2])
    n = len(ps)
    vals = random_values(n, rng)
    src = build(bp, grid, FIELDS, np.asarray(ps, np.uint64), vals, np.asarray(ss), np.asarray(ws), row_group=500)
    size = (7.5, 6.0, 4.0)
    grades = [{"field": "AU", "weight": "tonnes"}, {"field": "CU", "weight": "volume"}]
    base = rg.regularize(src, store_of(bp), size, grades=grades, density="SG", threads=1)
    ref = done(bp, src, base, "t1")
    for threads, chunk in ((2, 1 << 20), (4, 1 << 20), (8, 1 << 20), (1, 333), (8, 200), (3, 97)):
        res = rg.regularize(src, store_of(bp), size, grades=grades, density="SG", threads=threads, chunk=chunk)
        assert res.staged.file is not None and base.staged.file is None  # `base` was committed, `res` is staged
        assert res.totals == base.totals and res.rows == base.rows
        dst, _ = done(bp, src, res, f"t{threads}-{chunk}")
        for name, want in all_fields(ref[0]).items():
            assert same_bits(all_fields(dst)[name], want), (name, threads, chunk)
        assert np.array_equal(dst.p(), ref[0].p())
        assert dst.shas()["cells"] == ref[0].shas()["cells"]  # the same file, bit for bit


# --- errors, cancel, commit ------------------------------------------------------------------------------------------


def codes_of(exc: pytest.ExceptionInfo[ApiError]) -> tuple[int, str]:
    return exc.value.status, exc.value.code


def test_errors(bp) -> None:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(2)
    src = build(bp, PLAIN, FIELDS, sample(10, rng), random_values(10, rng))
    st = store_of(bp)
    with pytest.raises(ApiError) as e:
        rg.regularize(src, st, (2.0, 0.0, 2.0))
    assert codes_of(e) == (422, "bad_param") and e.value.extra["param"] == "size"
    with pytest.raises(ApiError) as e:
        rg.regularize(src, st, (2.0, 2.0, 2.0), grades=[{"field": "AU", "weight": "tonnes"}])
    assert codes_of(e) == (422, "bad_param") and e.value.extra["param"] == "density"
    with pytest.raises(ApiError) as e:
        rg.regularize(src, st, (2.0, 2.0, 2.0), grades=["DOM"])
    assert codes_of(e) == (422, "bad_param") and e.value.extra["param"] == "grades.0.field"
    with pytest.raises(ApiError) as e:
        rg.regularize(src, st, (2.0, 2.0, 2.0), grades=["NOPE"])
    assert codes_of(e) == (404, "block_field_not_found")
    with pytest.raises(ApiError) as e:
        rg.regularize(src, st, (2.0, 2.0, 2.0), categories=["AU"])
    assert e.value.extra["param"] == "categories.0"
    with pytest.raises(ApiError) as e:
        rg.regularize(src, st, (2.0, 2.0, 2.0), density="DOM")
    assert e.value.extra["param"] == "density"
    with pytest.raises(ApiError) as e:
        rg.regularize(src, st, (2.0, 2.0, 2.0), density=-1)
    assert e.value.extra["param"] == "density"
    assert tmp_clean(st)


def test_tensors_and_empty_models_are_refused(bp) -> None:  # type: ignore[no-untyped-def]
    grid = {"corner": [0.0, 0.0, 0.0], "tensors": {"x": [1.0, 2.0], "y": [1.0], "z": [1.0]}}
    one = [{"name": "AU", "dtype": "f8"}]
    src = build(bp, grid, one, np.asarray([0, 1], np.uint64), {"AU": np.asarray([1.0, 2.0])})
    with pytest.raises(ApiError) as e:
        rg.regularize(src, store_of(bp), (2.0, 2.0, 2.0))
    assert codes_of(e) == (422, "variable_grid")
    empty = build(bp, PLAIN, [], np.asarray([], np.uint64), {})
    with pytest.raises(ApiError) as e:
        rg.regularize(empty, store_of(bp), (2.0, 2.0, 2.0))
    assert codes_of(e) == (422, "block_model_empty")


def test_names_of_p_fields_do_not_collide(bp) -> None:  # type: ignore[no-untyped-def]
    fields = [
        {"name": "A", "dtype": "cat", "labels": ["x", "y"]},
        {"name": "B", "dtype": "cat", "labels": ["x", "z"]},
    ]
    p = np.arange(4, dtype=np.uint64)
    values = {"A": np.asarray([0, 1, 0, 1], np.int32), "B": np.asarray([1, 1, 0, 0], np.int32)}
    src = build(bp, PLAIN, fields, p, values)
    dst, _ = run(bp, src, (2.0, 2.0, 2.0))
    names = [f["name"] for f in dst.fields]
    assert names == ["A", "B", "P_x", "P_y", "P_B_x", "P_z", "FRAC"]  # the second «x» is named by its field


def test_cancel_leaves_nothing_and_ticks_grow(bp) -> None:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(3)
    src = build(bp, PLAIN, FIELDS, sample(30, rng), random_values(30, rng))
    st = store_of(bp)
    seen: list[float] = []

    def tick(x: float) -> None:
        seen.append(x)
        if len(seen) == 2:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        rg.regularize(src, st, (2.0, 2.0, 2.0), chunk=10, tick=tick)
    assert seen == sorted(seen) and 0.0 < seen[0] < seen[1] <= 0.97
    assert tmp_clean(st)
    res = rg.regularize(src, st, (2.0, 2.0, 2.0), chunk=7)
    res.staged.discard()
    assert tmp_clean(st)


def test_commit_name_crs_and_conflict(bp) -> None:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(4)
    src = build(bp, PLAIN, FIELDS, sample(12, rng), random_values(12, rng))
    pr = project(bp)
    with dialect.write_tx(pr.engine) as conn:
        conn.execute(sa.update(objects).where(objects.c.id == src.id).values(crs="EPSG:32636"))
    res = rg.regularize(src, store_of(bp), (2.0, 2.0, 2.0))
    dst, reply = done(bp, src, res, "Модель 2 м")
    res2 = rg.regularize(src, store_of(bp), (2.0, 2.0, 2.0))
    dst2, reply2 = done(bp, src, res2, "Модель 2 м")
    assert reply["name"] == "Модель 2 м" and reply2["name"] == "Модель 2 м (2)"
    assert reply["events"] == [["objects.changed", {"kind": bs.KIND, "id": dst.id}]]
    with dialect.read_tx(pr.engine) as conn:
        crs = conn.execute(sa.select(objects.c.crs).where(objects.c.id == dst.id)).scalar()
    assert crs == "EPSG:32636" and dst.name == "Модель 2 м" and dst2.name == "Модель 2 м (2)"
    assert dst.meta["source"]["params"]["size"] == [2.0, 2.0, 2.0]
    assert dst.shas()["cells"] == dst2.shas()["cells"]
