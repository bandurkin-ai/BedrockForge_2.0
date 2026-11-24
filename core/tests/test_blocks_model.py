"""Reading a block model (подшаг 17.4; ADR 0023 §2, §15; docs/progress/e17-api.md §2–3): `blocks.model.Model` of v1
and v2 alike, the portions of `iter`, the slices, the fields of the files `fld:`, the geometry of the rows, the cache of
the columns of `stats.blocks_cache` (memory, disk, a replaced field), swath of a turn and sub-blocks (17.10)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry
from bf_core.blocks import store as bm
from bf_core.blocks.model import Chunk, Model, Reader
from bf_core.db import dialect
from bf_core.stats import blocks_cache as bc
from bf_core.storage.blobs import BlobStore
from tests.test_blocks_fields_store import (  # noqa: F401  (fixtures)
    FIELDS,
    GRID,
    GRID_SUB,
    RG,
    add_fields,
    f4,
    make_model,
    project,
    same,
    sample,
)

API = "/api/v1"


@pytest.fixture()
def blocks_router(monkeypatch: pytest.MonkeyPatch) -> None:
    import bf_core.api.app as app_module

    if "blocks" not in app_module.ROUTER_MODULES:
        monkeypatch.setattr(app_module, "ROUTER_MODULES", (*app_module.ROUTER_MODULES, "blocks"))


@pytest.fixture()
def bproj(blocks_router, pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects", json={"name": "Блоки"}).status_code == 200
    return pclient


PLAIN = {**GRID, "rotation": 0}
V1_GRID = {"corner": [1000.5, 2000.25, -300.0], "counts": [10, 8, 6], "size": [5.0, 5.0, 2.5]}
V1_FIELDS = [
    {"name": "Au", "kind": "number", "unit": "g/t"},
    {"name": "Порода", "kind": "category", "labels": ["руда", "порода", "дайка"]},
]


def make_v1(p: Any, store: BlobStore, n: int = 350) -> tuple[int, np.ndarray, dict[str, np.ndarray]]:
    idx, values = sample(n)
    vals = {"Au": values["AU"], "Порода": (values["DOM"] % 3).astype(np.int32)}
    with bm.CellsWriter(store, V1_GRID, V1_FIELDS, row_group=RG) as w:
        w.write(idx[:130], {k: v[:130] for k, v in vals.items()})
        w.write(idx[130:], {k: v[130:] for k, v in vals.items()})
        staged = w.finish()
    try:
        with dialect.write_tx(p.engine) as conn:
            return bm.commit_block_model(conn, store, staged, name="V1"), idx, vals
    finally:
        staged.discard()


def make_sub(p: Any, store: BlobStore, name: str = "Субблоки") -> tuple[int, Any, Any, Any, Any]:
    """A model with sub-blocks (div 2·2·2): 1–4 rows a parent, 250+ rows, row groups of RG."""
    rng = np.random.default_rng(3)
    ps, ss, ws = [], [], []
    for par in np.sort(rng.choice(10 * 8 * 6, size=150, replace=False)):
        picks = np.unique(rng.integers(0, 8, int(rng.integers(1, 5))))
        for c in picks:  # (sz, sy, sx) ascending: the code c = sx + 2·sy + 4·sz is that order
            ps.append(par)
            ss.append((c & 1, (c >> 1) & 1, c >> 2))
            ws.append((1, 1, 1))
    p_arr, s_arr, w_arr = np.asarray(ps, np.uint64), np.asarray(ss, np.int64), np.asarray(ws, np.int64)
    au = rng.lognormal(0.0, 1.0, len(p_arr))
    au[rng.random(len(p_arr)) < 0.1] = np.nan
    with bm.CellsWriterV2(store, GRID_SUB, [{"name": "AU", "dtype": "f8"}], row_group=RG) as w:
        half = len(p_arr) // 2
        w.write(p_arr[:half], {"AU": au[:half]}, s_arr[:half], w_arr[:half])
        w.write(p_arr[half:], {"AU": au[half:]}, s_arr[half:], w_arr[half:])
        staged = w.finish()
    try:
        with dialect.write_tx(p.engine) as conn:
            return bm.commit_block_model(conn, store, staged, name=name), p_arr, s_arr, w_arr, au
    finally:
        staged.discard()


def open_model(p: Any, store: BlobStore, model_id: int) -> Model:
    with dialect.read_tx(p.engine) as conn:
        return Model.open(conn, store, model_id)


def cached(p: Any, model_id: int) -> bc.Model:
    with dialect.read_tx(p.engine) as conn:
        return bc.model_of(conn, model_id)


@pytest.fixture(autouse=True)
def clean_cache() -> Any:
    bc.MODELS.clear()
    yield
    bc.MODELS.clear()


# --- v1 and v2 ------------------------------------------------------------------------------------------------------


def test_v1_and_v2_read_alike(bproj) -> None:  # type: ignore[no-untyped-def]
    p = project(bproj)
    store = BlobStore(p.folder)
    v1, idx1, vals1 = make_v1(p, store)
    v2 = make_model(p, store)
    idx2, vals2 = sample()
    m1, m2 = open_model(p, store, v1), open_model(p, store, v2)
    assert (m1.rows, m1.parents, m1.sub, m1.full, m1.version) == (350, 350, False, False, 1)
    assert (m2.rows, m2.parents, m2.sub, m2.version, m2.name) == (350, 350, False, 1, "Модель")
    assert m1.meta["v"] == 2 and m1.grid.rotation == 0.0 and m2.grid.rotation == 30.0  # v1 is read as v2
    assert m1.subs() is None and m2.subs() is None
    assert np.array_equal(m1.p(), idx1) and m1.p().dtype == np.uint64
    assert np.array_equal(m2.p(), idx2)
    c1, c2 = m1.columns(["Au", "Порода"]), m2.columns(["AU", "CU", "N", "DOM"])
    assert same(c1["Au"], vals1["Au"]) and c1["Au"].dtype == np.float64
    assert np.array_equal(c1["Порода"], vals1["Порода"]) and c1["Порода"].dtype == np.int32
    assert same(c2["AU"], vals2["AU"]) and same(c2["CU"], f4(vals2["CU"])) and same(c2["N"], vals2["N"])
    assert np.isnan(c2["AU"]).any() and np.isnan(c2["N"]).any()  # empty stays empty, never 0
    assert np.array_equal(c2["DOM"], vals2["DOM"]) and c2["DOM"].dtype == np.int32 and (c2["DOM"] == -1).any()
    assert m2.labels("dom") == ["руда", "порода", "дайка"] and m1.labels("Au") == []
    assert set(m2.shas()) == {"cells"} and m2.field("au")["dtype"] == "f8"
    assert list(m2.columns(["au", "AU", "cu"])) == ["AU", "CU"]  # the stored names, once


def test_open_and_field_errors(bproj) -> None:  # type: ignore[no-untyped-def]
    p = project(bproj)
    store = BlobStore(p.folder)
    model_id = make_model(p, store)
    with dialect.read_tx(p.engine) as conn, pytest.raises(ApiError) as exc:
        Model.open(conn, store, 9999)
    assert exc.value.status == 404 and exc.value.code == "block_model_not_found"
    m = open_model(p, store, model_id)
    for call in (lambda: m.field("нет"), lambda: m.columns(["AU", "нет"]), lambda: m.labels("нет")):
        with pytest.raises(ApiError) as exc:
            call()
        assert exc.value.status == 404 and exc.value.code == "block_field_not_found"
    with pytest.raises(ValueError):
        next(m.iter([], 0))
    store.path_of(m.shas()["cells"]).unlink()  # the file of cells is gone
    with pytest.raises(ApiError) as exc:
        m.p()
    assert exc.value.status == 409 and exc.value.code == "block_model_broken"


# --- portions and slices --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("chunk", [1, 77, RG, 130, 349, 350, 1 << 20])
def test_iter_portions(bproj, chunk: int) -> None:  # type: ignore[no-untyped-def]
    p = project(bproj)
    store = BlobStore(p.folder)
    m = open_model(p, store, make_model(p, store))
    idx, vals = sample()
    got = list(m.iter(["AU", "DOM"], chunk=chunk))
    assert all(isinstance(c, Chunk) and c.n <= chunk and c.s is None and c.w is None for c in got)
    assert [c.start for c in got] == list(range(0, 350, chunk))  # the first row of every portion
    assert all(c.n == chunk for c in got[:-1]) and sum(c.n for c in got) == 350
    assert np.array_equal(np.concatenate([c.p for c in got]), idx)
    assert same(np.concatenate([c.cols["AU"] for c in got]), vals["AU"])
    assert np.array_equal(np.concatenate([c.cols["DOM"] for c in got]), vals["DOM"])
    assert list(m.iter(chunk=chunk))[0].cols == {}  # no fields asked: the rows only


def test_iter_portions_of_subblocks(bproj) -> None:  # type: ignore[no-untyped-def]
    p = project(bproj)
    store = BlobStore(p.folder)
    model_id, par, s, w, au = make_sub(p, store)
    m = open_model(p, store, model_id)
    assert m.sub and m.rows == len(par) > 2 * RG and m.parents == 150 and m.grid.sub.div == (2, 2, 2)
    sw = m.subs()
    assert sw is not None and sw[0].dtype == np.uint8 and sw[0].shape == (m.rows, 3)
    assert np.array_equal(sw[0], s) and np.array_equal(sw[1], w) and np.array_equal(m.p(), par)
    got = list(m.iter(["AU"], chunk=61))
    assert all(c.s is not None and c.w is not None for c in got)
    assert np.array_equal(np.concatenate([c.s for c in got]), s)  # type: ignore[misc]
    assert np.array_equal(np.concatenate([c.w for c in got]), w)  # type: ignore[misc]
    assert same(np.concatenate([c.cols["AU"] for c in got]), au)
    assert [c.n for c in got][:-1] == [61] * (len(got) - 1)


def test_slices_read_only_their_row_groups(bproj, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    import pyarrow.parquet as pq

    p = project(bproj)
    store = BlobStore(p.folder)
    m = open_model(p, store, make_model(p, store))
    idx, vals = sample()
    reads: list[int] = []
    real = pq.ParquetFile.read_row_group

    def spy(self: Any, i: int, *a: Any, **k: Any) -> Any:
        reads.append(i)
        return real(self, i, *a, **k)

    monkeypatch.setattr(pq.ParquetFile, "read_row_group", spy)
    for lo, hi in [(0, 350), (120, 130), (95, 205), (299, 350), (340, 999), (10, 10), (400, 500), (0, 1)]:
        reads.clear()
        got = m.columns(["AU", "DOM"], lo, hi)
        want = slice(lo, hi)
        assert same(got["AU"], vals["AU"][want]) and np.array_equal(got["DOM"], vals["DOM"][want]), (lo, hi)
        assert np.array_equal(m.p(lo, hi), idx[want]), (lo, hi)
        if (lo, hi) == (120, 130):
            assert set(reads) == {1} or len(reads) == 2  # P and the fields: the row group 1 only
    reads.clear()
    m.columns(["AU"], 120, 130)
    assert reads == [1]


def test_row_groups_of_another_size_are_broken(bproj) -> None:  # type: ignore[no-untyped-def]
    p = project(bproj)
    store = BlobStore(p.folder)
    model_id = make_model(p, store)
    idx, vals = sample()
    add_fields(p, store, model_id, [{"name": "X"}], {"X": vals["AU"] * 2.0})
    m = open_model(p, store, model_id)
    assert set(m.shas()) == {"cells", m.field("X")["file"]}
    rows = m.reader.group_sizes()
    assert rows == [100, 100, 100, 50]
    other = Reader(store, {**m.shas(), "cells": m.shas()[m.field("X")["file"]]}, m.fields, m.rows)
    assert other.group_sizes() == rows  # the same groups in the file of the field (add_fields wrote them so)


# --- the files of fields --------------------------------------------------------------------------------------------


def test_fields_of_other_files_read_in_step(bproj) -> None:  # type: ignore[no-untyped-def]
    p = project(bproj)
    store = BlobStore(p.folder)
    model_id = make_model(p, store)
    idx, vals = sample()
    cells_sha = open_model(p, store, model_id).shas()["cells"]
    add_fields(p, store, model_id, [{"name": "AU2", "unit": "г/т"}, {"name": "CU2", "dtype": "f4"}],
               {"AU2": vals["AU"] * 2.0, "CU2": vals["CU"]})  # fmt: skip
    add_fields(p, store, model_id, [{"name": "CLS", "dtype": "cat", "labels": ["a", "b"]}],
               {"CLS": (vals["DOM"] % 2).astype(np.int32)})  # fmt: skip
    m = open_model(p, store, model_id)
    assert m.version == 3 and m.shas()["cells"] == cells_sha and len(m.shas()) == 3
    got = m.columns(["CLS", "AU", "CU2", "AU2"])
    assert same(got["AU2"], vals["AU"] * 2.0) and same(got["CU2"], f4(vals["CU"])) and same(got["AU"], vals["AU"])
    assert np.array_equal(got["CLS"], (vals["DOM"] % 2).astype(np.int32)) and got["CLS"].dtype == np.int32
    part = m.columns(["AU2", "CLS"], 130, 275)  # across the groups, from a file that is not `cells`
    assert same(part["AU2"], (vals["AU"] * 2.0)[130:275]) and np.array_equal(part["CLS"], got["CLS"][130:275])
    chunks = list(m.iter(["AU", "AU2", "CLS"], chunk=64))
    assert same(np.concatenate([c.cols["AU2"] for c in chunks]), vals["AU"] * 2.0)
    assert np.array_equal(np.concatenate([c.cols["CLS"] for c in chunks]), got["CLS"])
    assert np.array_equal(np.concatenate([c.p for c in chunks]), idx)
    add_fields(p, store, model_id, [{"name": "AU2", "replace": True}], {"AU2": vals["AU"] + 1.0})  # a new file
    again = open_model(p, store, model_id)
    assert same(again.columns(["AU2"])["AU2"], vals["AU"] + 1.0)
    assert again.field("AU2")["file"] != m.field("AU2")["file"]
    store.path_of(again.shas()[again.field("AU2")["file"]]).unlink()
    with pytest.raises(ApiError) as exc:
        again.columns(["AU2"])
    assert exc.value.code == "block_model_broken"
    assert same(again.columns(["AU"])["AU"], vals["AU"])  # the other files are read as before


# --- the geometry of the rows ---------------------------------------------------------------------------------------


def test_centres_boxes_and_volumes(bproj) -> None:  # type: ignore[no-untyped-def]
    p = project(bproj)
    store = BlobStore(p.folder)
    plain = open_model(p, store, make_model(p, store))
    idx, _ = sample()
    assert np.array_equal(plain.centres(), geometry.centres(plain.grid, idx))
    some = np.array([5, 0, 349, 100], np.intp)
    assert np.array_equal(plain.centres(some), plain.centres()[some])
    assert np.array_equal(plain.local(some), geometry.row_local_centres(plain.grid, idx[some]))
    assert np.array_equal(plain.boxes(), geometry.row_boxes(plain.grid, idx))
    assert np.array_equal(plain.volume(), np.full(350, 5.0 * 5.0 * 2.5))  # no FRAC: the whole block
    assert np.array_equal(plain.volume(some), np.full(4, 62.5))
    model_id, par, s, w, _au = make_sub(p, store)
    sub = open_model(p, store, model_id)
    assert np.array_equal(sub.centres(), geometry.centres(sub.grid, par, s, w))
    assert np.array_equal(sub.volume(), np.full(sub.rows, 62.5 / 8))
    assert np.array_equal(sub.boxes(np.array([2], np.intp)), geometry.row_boxes(sub.grid, par[2:3], s[2:3], w[2:3]))


def test_volume_with_frac(bproj) -> None:  # type: ignore[no-untyped-def]
    p = project(bproj)
    store = BlobStore(p.folder)
    model_id = make_model(p, store)
    frac = np.random.default_rng(5).random(350)
    frac[::7] = np.nan
    add_fields(p, store, model_id, [{"name": "FRAC", "role": "frac"}], {"FRAC": frac})
    m = open_model(p, store, model_id)
    assert m.role_field("frac") is not None and m.role_field("code")["name"] == "DOM"  # type: ignore[index]
    vol = m.volume()
    assert same(vol, 62.5 * frac)  # an empty FRAC is an empty volume, not 0
    assert same(m.volume(np.array([7, 8], np.intp)), (62.5 * frac)[[7, 8]])
    assert geometry.volume_sum(vol) == (float(np.sum(62.5 * frac[~np.isnan(frac)])), int(np.isnan(frac).sum()))


def test_iter_keeps_the_memory_of_a_portion(bproj) -> None:  # type: ignore[no-untyped-def]
    import tracemalloc

    p = project(bproj)
    store = BlobStore(p.folder)
    rows = 60_000
    rng = np.random.default_rng(1)
    big = {**GRID, "counts": [100, 100, 10]}
    with bm.CellsWriterV2(store, big, [{"name": "AU", "dtype": "f8"}, {"name": "B", "dtype": "f8"}],
                          row_group=8_192) as w:  # fmt: skip
        w.write(np.arange(rows, dtype=np.uint64), {"AU": rng.random(rows), "B": rng.random(rows)})
        staged = w.finish()
    with dialect.write_tx(p.engine) as conn:
        model_id = bm.commit_block_model(conn, store, staged, name="Большая")
    m = open_model(p, store, model_id)
    full = rows * (8 + 8 + 8)
    tracemalloc.start()
    seen = 0
    for c in m.iter(["AU", "B"], chunk=4_096):
        seen += c.n
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert seen == rows and peak < full / 3  # a portion, not the model


# --- the cache of the columns ---------------------------------------------------------------------------------------


def test_cache_reads_a_file_once_v1_and_v2(bproj, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    p = project(bproj)
    store = BlobStore(p.folder)
    v1, idx1, vals1 = make_v1(p, store)
    v2 = make_model(p, store, grid=PLAIN)
    idx2, vals2 = sample()
    add_fields(p, store, v2, [{"name": "AU2"}], {"AU2": vals2["AU"] * 3.0})
    m1, m2 = cached(p, v1), cached(p, v2)
    c1 = bc.columns(p, m1, ["Au", "Порода"])
    assert c1.idx.dtype == np.uint32 and np.array_equal(c1.idx, idx1.astype(np.uint32))
    assert same(c1.number("Au"), vals1["Au"]) and np.array_equal(c1.codes("Порода"), vals1["Порода"])
    assert not c1.idx.flags.writeable and not c1.number("Au").flags.writeable
    c2 = bc.columns(p, m2, ["AU", "DOM", "AU2"])  # `cells` and a file `fld:` in one request
    assert same(c2.number("AU"), vals2["AU"]) and same(c2.number("AU2"), vals2["AU"] * 3.0)
    assert np.array_equal(c2.codes("DOM"), vals2["DOM"]) and np.array_equal(c2.idx, idx2.astype(np.uint32))
    assert [k for k in bc.MODELS.keys() if k[3] == "AU2"][0][4:] == (m2.shas[m2.file_of("AU2")],)  # a 5th: its file
    assert all(len(k) == 4 for k in bc.MODELS.keys() if k[3] != "AU2")
    reads = bc.MODELS.reads

    def never(*a: Any, **k: Any) -> Any:
        raise AssertionError("the file is read again")

    monkeypatch.setattr(Reader, "pieces", never)
    again = bc.columns(p, m2, ["AU2", "AU", "DOM"])  # the memory serves it
    assert bc.MODELS.reads == reads and again.number("AU") is c2.number("AU")


def test_cache_after_a_field_is_replaced_or_added(bproj) -> None:  # type: ignore[no-untyped-def]
    p = project(bproj)
    store = BlobStore(p.folder)
    model_id = make_model(p, store, grid=PLAIN)
    _idx, vals = sample()
    add_fields(p, store, model_id, [{"name": "X"}], {"X": vals["AU"]})
    first = bc.columns(p, cached(p, model_id), ["X", "AU"])
    assert same(first.number("X"), vals["AU"])
    add_fields(p, store, model_id, [{"name": "X", "replace": True}], {"X": vals["AU"] * 5.0})
    add_fields(p, store, model_id, [{"name": "Y"}], {"Y": vals["AU"] + 1.0})
    m = cached(p, model_id)
    reads = bc.MODELS.reads
    second = bc.columns(p, m, ["X", "AU", "Y"])
    assert same(second.number("X"), vals["AU"] * 5.0) and same(second.number("Y"), vals["AU"] + 1.0)
    assert bc.MODELS.reads - reads == 1  # X and Y from their files; AU and idx were kept
    assert sorted(k[3] for k in bc.MODELS.keys()) == sorted([bc.IDX, "AU", "X", "Y"])  # the older X went


def test_cache_key_rules() -> None:
    cache = bc.ModelCache(limit=10_000)
    a = np.zeros(10)
    cache.put(("p", 1, "c1", "AU"), a)
    cache.put(("p", 1, "c1", "X", "f1"), a)
    cache.put(("p", 1, "c1", "Y", "f2"), a)
    assert len(cache.keys()) == 3
    cache.put(("p", 1, "c1", "X", "f3"), a)  # the field X now lives in another file
    assert sorted(k[3:] for k in cache.keys()) == [("AU",), ("X", "f3"), ("Y", "f2")]
    cache.put(("p", 1, "c1", "X"), a)  # …and now in `cells`
    assert sorted(k[3:] for k in cache.keys()) == [("AU",), ("X",), ("Y", "f2")]
    cache.put(("p", 1, "c2", "AU"), a)  # a new file of cells: all that was of the older one goes
    assert cache.keys() == [("p", 1, "c2", "AU")]


def test_disk_cache(bproj, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    p = project(bproj)
    store = BlobStore(p.folder)
    model_id = make_model(p, store, grid=PLAIN)
    _idx, vals = sample()
    add_fields(p, store, model_id, [{"name": "AU2"}], {"AU2": vals["AU"] * 2.0})
    m = cached(p, model_id)
    first = bc.columns(p, m, ["AU", "DOM", "AU2"], disk=True)
    files = sorted(bc.disk_dir(p).rglob("*.npy"))
    assert len(files) == 4 and not list(bc.disk_dir(p).rglob("*.tmp"))  # idx and three fields
    assert {f.parent.name for f in files} == set(m.shas.values())  # by the SHA-256 of the file of the column
    bc.MODELS.clear()
    reads = bc.MODELS.reads

    def never(*a: Any, **k: Any) -> Any:
        raise AssertionError("the Parquet file is read")

    monkeypatch.setattr(Reader, "pieces", never)
    back = bc.columns(p, m, ["AU", "DOM", "AU2"], disk=True)  # a restart: the memory is empty
    assert bc.MODELS.reads == reads and bc.MODELS.loads == 4
    for name in ("AU", "AU2"):
        assert same(back.number(name), first.number(name)) and not back.number(name).flags.writeable
    assert np.array_equal(back.codes("DOM"), first.codes("DOM")) and back.idx.dtype == np.uint32
    monkeypatch.undo()
    victim = next(f for f in files if f.parent.name == m.sha)  # damaged: another size
    np.save(victim, np.zeros(3))
    bc.MODELS.clear()
    fixed = bc.columns(p, m, ["AU", "DOM"], disk=True)  # read from the model again, the file is made anew
    assert same(fixed.number("AU"), vals["AU"]) and np.array_equal(fixed.codes("DOM"), first.codes("DOM"))
    assert np.load(victim).shape == (350,)
    bc.clear_disk(p)
    assert not bc.disk_dir(p).exists()
    bc.MODELS.clear()
    assert same(bc.columns(p, m, ["AU"], disk=True).number("AU"), vals["AU"])  # a loss of nothing
    bc.MODELS.clear()
    monkeypatch.setattr(bc, "DISK_MIN_ROWS", 10)  # by default a model of 10⁶ rows and more
    bc.clear_disk(p)
    bc.columns(p, m, ["AU"])
    assert list(bc.disk_dir(p).rglob("*.npy"))
    bc.clear_disk(p)
    monkeypatch.setattr(bc, "DISK_MIN_ROWS", 1_000_000)
    bc.MODELS.clear()
    bc.columns(p, m, ["AU"])
    assert not bc.disk_dir(p).exists()


# --- a turn and sub-blocks are read by swath through `swath_sub` (17.10) ------------------------------------------


def test_swath_reads_a_turn_and_subblocks(bproj) -> None:  # type: ignore[no-untyped-def]
    from bf_core.stats import routes_swath

    p = project(bproj)
    store = BlobStore(p.folder)
    turned = make_model(p, store)  # rotation 30°
    sub, *_ = make_sub(p, store)
    plain = make_model(p, store, name="Прямая", grid=PLAIN)
    for model_id in (turned, sub):
        with dialect.read_tx(p.engine) as conn:
            m = bc.model_of(conn, model_id)  # no 422 `blocks_unsupported` any more
        assert m.plain is False and m.field("AU") is not None
        series = routes_swath._blocks(p, 0, {"model_id": model_id, "field": "AU"})
        assert series.error is None and series.sub is not None
    m = cached(p, plain)  # a model without a turn: as before
    assert m.cells == 350 and "rotation" not in m.grid and "div" not in m.grid and m.field("AU") is not None
    assert routes_swath._blocks(p, 0, {"model_id": plain, "field": "AU"}).error is None
    from bf_core.blocks.model import is_plain

    with dialect.read_tx(p.engine) as conn:
        assert not is_plain(bm.block_model_info(conn, turned)) and is_plain(bm.block_model_info(conn, plain))
        assert not is_plain(bm.block_model_info(conn, sub))
