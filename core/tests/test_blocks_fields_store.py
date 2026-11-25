"""Model v2 and the files of fields (подшаг 17.3; ADR 0023 §2, decision 4; docs/progress/e17-api.md §2–3).

A new field is its own file `fld:<key>`: `cells` keeps its SHA-256. Replacing and deleting a field, 409 on a stale
version (before the work and at the commit), a failure at any point leaves nothing, the round trip of `.bfggis`,
the same on PostgreSQL (`-m db`, BF_TEST_PG)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import fields_store as fs
from bf_core.blocks import meta as bmeta
from bf_core.blocks import schema as bs
from bf_core.blocks import store as bm
from bf_core.db import dialect, migrate
from bf_core.db.schema import blob_owners, blobs, objects
from bf_core.projects.service import projects_of
from bf_core.storage import gc
from bf_core.storage.blobs import BlobStore
from tests.dbutil import make_engine
from tests.test_archive import export, wait_job

API = "/api/v1"
GRID = {"corner": [1000.5, 2000.25, -300.0], "counts": [10, 8, 6], "size": [5.0, 5.0, 2.5], "rotation": 30}
GRID_SUB = {**GRID, "div": [2, 2, 2]}
FIELDS = [
    {"name": "AU", "dtype": "f8", "unit": "г/т", "role": "grade"},
    {"name": "CU", "dtype": "f4", "unit": "%"},
    {"name": "N", "dtype": "i2"},
    {"name": "DOM", "dtype": "cat", "labels": ["руда", "порода", "дайка"], "role": "code"},
]
SRC = {"op": "calc", "params": {"expr": "AU * 2"}, "inputs": ["a" * 64], "algo": "calc-1"}
RG = 100  # a row group of the cells: 350 rows → 100 + 100 + 100 + 50


@pytest.fixture()
def blocks_router(monkeypatch: pytest.MonkeyPatch) -> None:
    import bf_core.api.app as app_module

    if "blocks" not in app_module.ROUTER_MODULES:
        monkeypatch.setattr(app_module, "ROUTER_MODULES", (*app_module.ROUTER_MODULES, "blocks"))


@pytest.fixture()
def bc(blocks_router, pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects", json={"name": "Блоки"}).status_code == 200
    return pclient


def project(c: Any) -> Any:
    return projects_of(c.core).require_current()


def sample(n: int = 350, seed: int = 7) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    rng = np.random.default_rng(seed)
    p = np.sort(rng.choice(10 * 8 * 6, size=n, replace=False)).astype(np.uint64)
    au = rng.lognormal(0.0, 1.0, n)
    au[rng.random(n) < 0.1] = np.nan  # empty stays empty
    cu = rng.random(n)
    cu[rng.random(n) < 0.1] = np.nan
    num = rng.integers(0, 50, n).astype(np.float64)
    num[rng.random(n) < 0.2] = np.nan
    return p, {"AU": au, "CU": cu, "N": num, "DOM": rng.integers(-1, 3, n).astype(np.int32)}


def make_model(p: Any, store: BlobStore, n: int = 350, name: str = "Модель", grid: Any = None) -> int:
    idx, values = sample(n)
    with bm.CellsWriterV2(store, grid or GRID, FIELDS, row_group=RG) as w:
        w.write(idx[:130], {k: v[:130] for k, v in values.items()})
        w.write(idx[130:], {k: v[130:] for k, v in values.items()})
        staged = w.finish()
    try:
        with dialect.write_tx(p.engine) as conn:
            return bm.commit_block_model(conn, store, staged, name=name, source={"format": "dat", "file": "m.dat"})
    finally:
        staged.discard()


def load(p: Any, bm_id: int) -> fs.ModelRef:
    with dialect.read_tx(p.engine) as conn:
        return fs.ModelRef.load(conn, bm_id)


def add_fields(p: Any, store: BlobStore, bm_id: int, adds: list[dict[str, Any]], data: dict[str, np.ndarray],
               version: int | None = None, source: dict[str, Any] | None = None, step: int = 90) -> int:  # fmt: skip
    """The new fields through the writer in portions of `step` rows; → the new version."""
    model = load(p, bm_id)
    with fs.FieldsWriter(store, model, version or model.version) as w:
        for a in adds:
            w.add(**a)
        n = len(next(iter(data.values())))
        for a in range(0, n, step):
            w.write({k: v[a : a + step] for k, v in data.items()})
        staged = w.finish()
    with dialect.write_tx(p.engine) as conn:
        return fs.commit_fields(conn, store, staged, source=source or SRC)


def roles(p: Any, bm_id: int) -> set[str]:
    with dialect.read_tx(p.engine) as conn:
        rows = conn.execute(sa.select(blob_owners.c.role).where(blob_owners.c.owner_id == bm_id)).scalars()
        return set(rows)


def groups_of(store: BlobStore, sha: str) -> list[int]:
    import pyarrow.parquet as pq

    meta = pq.ParquetFile(store.path_of(sha)).metadata
    return [meta.row_group(g).num_rows for g in range(meta.num_row_groups)]


def f4(values: np.ndarray) -> np.ndarray:
    return values.astype(np.float32).astype(np.float64)


def same(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.array_equal(a, b, equal_nan=True))


def tmp_clean(store: BlobStore) -> bool:
    return not store.tmp_dir.exists() or not any(store.tmp_dir.iterdir())


# --- the model v2 --------------------------------------------------------------------------------------------------


def test_model_v2_dtypes_groups_and_reading(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    store = BlobStore(p.folder)
    bm_id = make_model(p, store)
    idx, values = sample()
    with dialect.read_tx(p.engine) as conn:
        info = bm.block_model_info(conn, bm_id)
        sha = bm.cells_sha(conn, bm_id)
        got_idx, got = bm.read_cells(conn, store, bm_id)
        row = conn.execute(sa.select(objects.c.meta, objects.c.extent).where(objects.c.id == bm_id)).one()
    assert row.meta["v"] == 2 and row.meta["rows"] == row.meta["parents"] == 350 and row.meta["sub"] is False
    assert row.meta["groups"] == [{"rows": 100, "k0": g[0], "k1": g[1]} for g in
                                  [(int(idx[a] // 80), int(idx[min(a + 99, 349)] // 80)) for a in (0, 100, 200)]
                                  ] + [{"rows": 50, "k0": int(idx[300] // 80), "k1": int(idx[349] // 80)}]  # fmt: skip
    assert groups_of(store, sha) == [100, 100, 100, 50]
    assert bmeta.upgrade(row.meta) == row.meta  # idempotent: the meta is a valid v2
    assert row.extent == bs.GridV2.of(GRID).extent()
    assert info["meta_v"] == 2 and info["rows"] == 350 and info["cells"] == 350 and info["grid"]["rotation"] == 30.0
    assert [f["dtype"] for f in info["fields"]] == ["f8", "f4", "i2", "cat"]
    assert info["size"] > 0 and all(f["file"] == "cells" for f in info["fields"])
    assert np.array_equal(got_idx, idx)
    assert same(got["AU"], values["AU"]) and same(got["CU"], f4(values["CU"])) and got["CU"].dtype == np.float64
    assert same(got["N"], values["N"]) and got["N"].dtype == np.float64  # i2 with nulls → float64 with NaN
    assert np.array_equal(got["DOM"], values["DOM"]) and got["DOM"].dtype == np.int32  # null → −1
    au = next(f for f in info["fields"] if f["name"] == "AU")["stats"]
    ok = values["AU"][~np.isnan(values["AU"])]
    assert au["count"] == len(ok) and au["missing"] == 350 - len(ok) and au["min"] == ok.min()
    assert au["mean"] == pytest.approx(ok.mean(), rel=1e-12)
    dom = next(f for f in info["fields"] if f["name"] == "DOM")["stats"]
    assert dom["counts"] == [int((values["DOM"] == c).sum()) for c in range(3)]
    assert dom["missing"] == int((values["DOM"] == -1).sum())


def test_model_v2_subblocks_order_and_checks(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    store = BlobStore(p.folder)
    par = np.array([3, 3, 7, 9, 9], np.uint64)
    s = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 0], [0, 1, 0], [1, 1, 0]])
    w = np.array([[1, 2, 2], [1, 2, 2], [2, 2, 2], [2, 1, 2], [1, 1, 2]])
    vals = {"AU": np.array([1.0, 2.0, np.nan, 4.0, 5.0])}

    def writer() -> bm.CellsWriterV2:
        return bm.CellsWriterV2(store, GRID_SUB, [{"name": "AU", "dtype": "f8"}])

    with writer() as wr:
        wr.write(par[:2], {"AU": vals["AU"][:2]}, s[:2], w[:2])
        wr.write(par[2:], {"AU": vals["AU"][2:]}, s[2:], w[2:])  # the order is kept across the portions
        staged = wr.finish()
    assert staged.v2 is not None and staged.v2["parents"] == 3 and staged.cells == 5
    with dialect.write_tx(p.engine) as conn:
        bm_id = bm.commit_block_model(conn, store, staged, name="Субблоки")
    with dialect.read_tx(p.engine) as conn:
        meta = bm.block_model_info(conn, bm_id)
        sha = bm.cells_sha(conn, bm_id)
        idx, got = bm.read_cells(conn, store, bm_id)
    assert meta["sub"] is True and meta["rows"] == 5 and meta["parents"] == 3 and meta["grid"]["levels"] == [1, 1, 1]
    assert np.array_equal(idx, par) and same(got["AU"], vals["AU"])
    import pyarrow.parquet as pq

    table = pq.read_table(store.path_of(sha))
    assert table.column_names == ["idx", "sx", "sy", "sz", "wx", "wy", "wz", "AU"]
    assert table.column("wx").to_pylist() == [1, 1, 2, 2, 1] and str(table.schema.field("sx").type) == "uint8"
    for bad, msg in [
        (dict(p=[5, 4], s=[[0, 0, 0]] * 2, w=[[1, 1, 1]] * 2), "по возрастанию"),
        (dict(p=[5, 5], s=[[1, 0, 0], [0, 0, 0]], w=[[1, 1, 1]] * 2), "по возрастанию"),  # (sz, sy, sx) order
        (dict(p=[5], s=[[1, 0, 0]], w=[[2, 1, 1]]), "пределы родителя"),  # s + w > div
        (dict(p=[5], s=[[0, 0, 0]], w=[[0, 1, 1]]), "пределы родителя"),
        (dict(p=[5], s=None, w=None), "субблок"),
        (dict(p=[480], s=[[0, 0, 0]], w=[[1, 1, 1]]), "вне сетки"),
    ]:
        with pytest.raises(ApiError) as exc, writer() as wr:
            wr.write(bad["p"], {"AU": np.zeros(len(bad["p"]))}, bad["s"], bad["w"])
        assert exc.value.code == "block_model_invalid" and msg in exc.value.message, bad
    with pytest.raises(ApiError) as exc, writer() as wr:  # the previous portion counts: 7 after 9
        wr.write([9], {"AU": [1.0]}, [[0, 0, 0]], [[1, 1, 1]])
        wr.write([7], {"AU": [1.0]}, [[0, 0, 0]], [[1, 1, 1]])
    assert exc.value.code == "block_model_invalid"
    assert tmp_clean(store)
    with pytest.raises(ApiError) as exc:  # sub-blocks without a division
        bm.CellsWriterV2(store, GRID, [], sub=True)
    assert exc.value.code == "block_model_invalid"
    with pytest.raises(ApiError) as exc:  # the column of the sub-blocks is a reserved name
        bm.CellsWriterV2(store, GRID_SUB, [{"name": "sx", "dtype": "f8"}])
    assert exc.value.code == "block_model_invalid"


def test_values_the_type_cannot_keep_are_refused(bc) -> None:  # type: ignore[no-untyped-def]
    store = BlobStore(project(bc).folder)
    for dtype, values in [("i1", [1.0, 200.0]), ("i2", [1.5, 2.0]), ("f4", [1.0, 1e300]), ("f8", ["a", "b"])]:
        col = fs.Column({"name": "X", "dtype": dtype})
        with pytest.raises(ApiError) as exc:
            col.encode(np.asarray(values, dtype=object if dtype == "f8" else np.float64))
        assert exc.value.code == "field_values_invalid", dtype
    cat = fs.Column({"name": "C", "dtype": "cat", "labels": ["a", "b"]})
    for values in ([0, 2], [-2, 0], [0.5, 1.0]):
        with pytest.raises(ApiError) as exc:
            cat.encode(np.asarray(values))
        assert exc.value.code == "field_values_invalid"
    many = {"name": "C", "dtype": "cat", "labels": [str(i) for i in range(bs.MAX_LABELS_V2 + 1)]}
    with pytest.raises(ApiError) as exc:
        fs.Column(many)
    assert exc.value.code == "too_many_labels" and tmp_clean(store)


# --- a new field --------------------------------------------------------------------------------------------------


def test_new_field_is_its_own_file_and_cells_keep_their_sha(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    store = BlobStore(p.folder)
    bm_id = make_model(p, store)
    with dialect.read_tx(p.engine) as conn:
        sha, size_before = bm.cells_sha(conn, bm_id), bm.block_model_info(conn, bm_id)["size"]
    cells_bytes = store.path_of(sha).read_bytes()
    _, values = sample()
    au2 = values["AU"] * 2  # `IF(AU > 1, AU * 2, 0)` of the calculator: NaN stays empty
    grade = np.where(np.isnan(values["AU"]), -1, np.where(values["AU"] > 1, 1, 0)).astype(np.int32)
    new = add_fields(p, store, bm_id,
                     [{"name": "AU2", "dtype": "f8", "unit": "г/т", "role": "density"},
                      {"name": "GRADE", "dtype": "cat", "labels": ["бедная", "богатая"], "role": "class"}],
                     {"AU2": au2, "GRADE": grade})  # fmt: skip
    assert new == 2
    with dialect.read_tx(p.engine) as conn:
        assert bm.cells_sha(conn, bm_id) == sha  # the SHA-256 of `cells` does not change
        info = bm.block_model_info(conn, bm_id)
        idx, got = bm.read_cells(conn, store, bm_id)
        one = bm.read_cells(conn, store, bm_id, ["GRADE", "AU"])[1]
        key = next(f["file"] for f in info["fields"] if f["name"] == "AU2")
        fld_sha = bm.cells_sha(conn, bm_id, key)
        row = conn.execute(sa.select(objects.c.version, objects.c.meta).where(objects.c.id == bm_id)).one()
    assert store.path_of(sha).read_bytes() == cells_bytes and info["size"] > size_before
    assert row.version == 2 and key.startswith("fld:") and bs.FLD_RE.fullmatch(key)
    assert [f["name"] for f in info["fields"]] == ["AU", "CU", "N", "DOM", "AU2", "GRADE"]
    assert {f["file"] for f in info["fields"][4:]} == {key} and roles(p, bm_id) == {"cells", key}
    assert bmeta.files_of(row.meta) == {"cells": ["AU", "CU", "N", "DOM"], key: ["AU2", "GRADE"]}
    assert groups_of(store, fld_sha) == groups_of(store, sha) == [100, 100, 100, 50]  # the same row groups
    new_au2 = info["fields"][4]
    assert new_au2["source"]["op"] == "calc" and new_au2["source"]["inputs"] == ["a" * 64]
    assert new_au2["source"]["params"] == {"expr": "AU * 2"} and new_au2["source"]["at"]
    assert new_au2["role"] == "density"
    assert new_au2["stats"]["count"] == int((~np.isnan(au2)).sum()) and new_au2["stats"]["max"] == np.nanmax(au2)
    assert info["fields"][5]["stats"]["counts"] == [int((grade == 0).sum()), int((grade == 1).sum())]
    assert same(got["AU2"], au2) and np.array_equal(got["GRADE"], grade) and same(got["AU"], values["AU"])
    assert np.array_equal(idx, sample()[0]) and np.array_equal(one["GRADE"], grade)
    # one more file: another calculation, the first stays
    third = add_fields(p, store, bm_id, [{"name": "T", "dtype": "i1"}], {"T": np.arange(350, dtype=np.float64) % 100})
    assert third == 3
    with dialect.read_tx(p.engine) as conn:
        assert bm.cells_sha(conn, bm_id) == sha and len(roles(p, bm_id)) == 3
        assert same(bm.read_cells(conn, store, bm_id, ["T"])[1]["T"], np.arange(350) % 100)


def test_field_exists_without_replace_and_names(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    store = BlobStore(p.folder)
    bm_id = make_model(p, store)
    model = load(p, bm_id)
    with fs.FieldsWriter(store, model, 1) as w:
        with pytest.raises(ApiError) as exc:
            w.add("au", "f8")  # a repeat ignoring the case
        assert exc.value.status == 409 and exc.value.code == "field_exists" and exc.value.extra == {"field": "au"}
        w.add("X", "f8")
        for bad in ("x", "idx", ""):  # "sx" is free in a model without sub-blocks
            with pytest.raises(ApiError) as exc:
                w.add(bad, "f8")
            assert exc.value.code in ("block_model_invalid", "field_exists")
        with pytest.raises(ApiError):
            w.add("R", "f8", role="code")  # a role of the other kind
        w.add("DENS", "f8", role="density")
        w.write({"X": np.zeros(350), "DENS": np.ones(350)})
        with pytest.raises(RuntimeError):
            w.add("LATE", "f8")  # the fields are fixed by the first write
    assert tmp_clean(store)


# --- replacing and deleting ---------------------------------------------------------------------------------------


def test_replace_a_field_of_cells_and_of_a_file(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    store = BlobStore(p.folder)
    bm_id = make_model(p, store)
    _, values = sample()
    with dialect.read_tx(p.engine) as conn:
        sha = bm.cells_sha(conn, bm_id)
    cells_bytes = store.path_of(sha).read_bytes()
    add_fields(p, store, bm_id, [{"name": "AU2", "dtype": "f8"}, {"name": "K", "dtype": "f8"}],
               {"AU2": values["AU"] * 2, "K": np.ones(350)})  # fmt: skip
    old_key = next(f["file"] for f in load(p, bm_id).meta["fields"] if f["name"] == "AU2")
    with dialect.read_tx(p.engine) as conn:
        old_sha = bm.cells_sha(conn, bm_id, old_key)
    # a field of `cells` (the first, in place): the file keeps its bytes, the meta points to the new file
    new_au = values["AU"] + 100
    v = add_fields(p, store, bm_id, [{"name": "AU", "dtype": "f4", "unit": "г/т", "role": "grade", "replace": True}],
                   {"AU": new_au})  # fmt: skip
    assert v == 3
    with dialect.read_tx(p.engine) as conn:
        info = bm.block_model_info(conn, bm_id)
        got = bm.read_cells(conn, store, bm_id)[1]
        assert bm.cells_sha(conn, bm_id) == sha
    assert store.path_of(sha).read_bytes() == cells_bytes
    names = [f["name"] for f in info["fields"]]
    assert names == ["AU", "CU", "N", "DOM", "AU2", "K"]  # the replaced field keeps its place
    au = info["fields"][0]
    assert au["dtype"] == "f4" and au["file"].startswith("fld:") and au["file"] != old_key
    assert same(got["AU"], f4(new_au)) and same(got["AU2"], values["AU"] * 2)
    # the whole file is replaced: its two fields — the old one is dropped and goes with the collection
    v = add_fields(p, store, bm_id, [{"name": "AU2", "dtype": "f8", "replace": True},
                                     {"name": "K", "dtype": "f8", "replace": True}],
                   {"AU2": np.full(350, 7.0), "K": np.full(350, 8.0)})  # fmt: skip
    assert v == 4
    assert old_key not in roles(p, bm_id) and len(roles(p, bm_id)) == 3  # cells, the file of AU, the new file
    assert store.exists(old_sha)
    report = gc.collect(p.engine, store)
    assert report.blobs == 1 and not store.exists(old_sha) and store.exists(sha)
    with dialect.read_tx(p.engine) as conn:
        got = bm.read_cells(conn, store, bm_id)[1]
    assert same(got["AU2"], np.full(350, 7.0)) and same(got["K"], np.full(350, 8.0))
    assert same(got["AU"], f4(new_au)) and same(got["CU"], f4(values["CU"]))


def test_replace_keeps_the_place_and_checks_the_roles_again(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    store = BlobStore(p.folder)
    bm_id = make_model(p, store)
    add_fields(p, store, bm_id, [{"name": "F", "dtype": "f8", "role": "frac"}], {"F": np.ones(350)})
    # a second FRAC is refused at the commit (meta.upgrade), the model stays as it was
    with pytest.raises(ApiError) as exc:
        add_fields(p, store, bm_id, [{"name": "F2", "dtype": "f8", "role": "frac"}], {"F2": np.ones(350)})
    assert exc.value.code == "block_model_invalid" and tmp_clean(store)
    assert load(p, bm_id).version == 2 and len(roles(p, bm_id)) == 2
    # replacing it by a field of the same role is fine, in the same place
    add_fields(p, store, bm_id, [{"name": "f", "dtype": "f4", "role": "frac", "replace": True}], {"f": np.zeros(350)})
    names = [f["name"] for f in load(p, bm_id).meta["fields"]]
    assert names == ["AU", "CU", "N", "DOM", "f"]


def test_delete_fields(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    store = BlobStore(p.folder)
    bm_id = make_model(p, store)
    _, values = sample()
    add_fields(p, store, bm_id, [{"name": "A", "dtype": "f8"}, {"name": "B", "dtype": "f8"}],
               {"A": values["AU"], "B": values["CU"]})  # fmt: skip
    key = next(f["file"] for f in load(p, bm_id).meta["fields"] if f["name"] == "A")
    with dialect.read_tx(p.engine) as conn:
        sha, fld_sha = bm.cells_sha(conn, bm_id), bm.cells_sha(conn, bm_id, key)
    with dialect.write_tx(p.engine) as conn:
        with pytest.raises(ApiError) as exc:
            fs.delete_fields(conn, bm_id, 2, ["A", "NOPE"])
        assert exc.value.status == 404 and exc.value.code == "block_field_not_found"
        assert exc.value.extra == {"param": "names.1"}
        with pytest.raises(ApiError) as exc:
            fs.delete_fields(conn, bm_id, 1, ["A"])
        assert exc.value.status == 409 and exc.value.code == "version_conflict" and exc.value.extra == {"version": 2}
        assert fs.delete_fields(conn, bm_id, 2, ["a"]) == 3  # a name of the file: the other field stays in it
    assert roles(p, bm_id) == {"cells", key}
    with dialect.write_tx(p.engine) as conn:
        assert fs.delete_fields(conn, bm_id, 3, ["B", "AU"]) == 4  # the last of the file and a field of `cells`
    assert roles(p, bm_id) == {"cells"}
    with dialect.read_tx(p.engine) as conn:
        info = bm.block_model_info(conn, bm_id)
        assert bm.cells_sha(conn, bm_id) == sha  # `cells` is immutable: the column stays in the file
        with pytest.raises(ApiError) as exc:
            bm.read_cells(conn, store, bm_id, ["AU"])
        assert exc.value.code == "block_field_not_found"
        got = bm.read_cells(conn, store, bm_id)[1]
    assert [f["name"] for f in info["fields"]] == ["CU", "N", "DOM"] and sorted(got) == ["CU", "DOM", "N"]
    assert store.exists(fld_sha)
    assert gc.collect(p.engine, store).blobs == 1 and not store.exists(fld_sha) and store.exists(sha)
    with dialect.write_tx(p.engine) as conn:  # a name freed by a deletion can be used again
        v = fs.delete_fields(conn, bm_id, 4, ["CU", "N", "DOM"])
    assert v == 5
    assert add_fields(p, store, bm_id, [{"name": "AU", "dtype": "f8"}], {"AU": np.ones(350)}) == 6


# --- 409 and failures --------------------------------------------------------------------------------------------


def test_version_conflict_before_the_work_and_at_the_commit(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    store = BlobStore(p.folder)
    bm_id = make_model(p, store)
    model = load(p, bm_id)
    with pytest.raises(ApiError) as exc:  # a stale version of the client: at once, no file is made
        fs.FieldsWriter(store, model, 7)
    assert exc.value.status == 409 and exc.value.code == "version_conflict" and exc.value.extra == {"version": 1}
    assert tmp_clean(store)
    with fs.FieldsWriter(store, model, 1) as w:  # the model is changed while the job counts
        w.add("X", "f8")
        w.write({"X": np.ones(350)})
        staged = w.finish()
    assert staged.file is not None
    with dialect.write_tx(p.engine) as conn:
        bm.rename_block_model(conn, bm_id, 1, "Другое имя")
    with pytest.raises(ApiError) as exc, dialect.write_tx(p.engine) as conn:
        fs.commit_fields(conn, store, staged, source=SRC)
    assert exc.value.status == 409 and exc.value.extra == {"version": 2}
    assert staged.file is None and tmp_clean(store)  # the temp file went with the refusal
    assert roles(p, bm_id) == {"cells"} and [f["name"] for f in load(p, bm_id).meta["fields"]] == [
        "AU", "CU", "N", "DOM"]  # fmt: skip
    with dialect.read_tx(p.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(blobs)).scalar() == 1
    with pytest.raises(ApiError) as exc, dialect.read_tx(p.engine) as conn:  # the model is not there
        fs.ModelRef.load(conn, 999)
    assert exc.value.status == 404 and exc.value.code == "block_model_not_found"


def test_failure_leaves_nothing(bc, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    store = BlobStore(p.folder)
    bm_id = make_model(p, store)
    model = load(p, bm_id)
    with dialect.read_tx(p.engine) as conn:
        before = conn.execute(sa.select(objects.c.version, objects.c.meta).where(objects.c.id == bm_id)).one()
        n_blobs = conn.execute(sa.select(sa.func.count()).select_from(blobs)).scalar()

    def untouched() -> None:
        with dialect.read_tx(p.engine) as conn:
            now = conn.execute(sa.select(objects.c.version, objects.c.meta).where(objects.c.id == bm_id)).one()
            assert (now.version, now.meta) == (before.version, before.meta)
            assert conn.execute(sa.select(sa.func.count()).select_from(blobs)).scalar() == n_blobs
        assert roles(p, bm_id) == {"cells"} and tmp_clean(store)
        assert not list(store.root.glob("??/??/*")) or len(list(store.root.glob("??/??/*"))) == n_blobs

    # 1. the writer fails in the middle: the temp file goes with it
    with pytest.raises(RuntimeError), fs.FieldsWriter(store, model, 1) as w:
        w.add("X", "f8")
        w.write({"X": np.ones(200)})
        assert not tmp_clean(store)
        raise RuntimeError("boom")
    untouched()
    # 2. a value the type cannot keep, a wrong count of rows: the same
    for bad in ({"X": np.full(350, 1e300)}, {"X": np.ones(349)}):
        with pytest.raises(ApiError), fs.FieldsWriter(store, model, 1) as w:
            w.add("X", "f4")
            w.write(bad)
            w.finish()
        untouched()
    with pytest.raises(ApiError) as exc, fs.FieldsWriter(store, model, 1) as w:
        w.add("X", "f8")
        w.write({"X": np.ones(300)})
        w.write({"X": np.ones(51)})  # more rows than the model has
    assert exc.value.code == "field_rows_mismatch"
    untouched()
    # 3. the commit fails after the file is in place: the transaction, the file and the references go
    with fs.FieldsWriter(store, model, 1) as w:
        w.add("X", "f8")
        w.write({"X": np.arange(350.0)})
        staged = w.finish()

    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("the disk is gone")

    monkeypatch.setattr("bf_core.storage.blobs.add_owner", boom)
    with pytest.raises(RuntimeError), dialect.write_tx(p.engine) as conn:
        fs.commit_fields(conn, store, staged, source=SRC)
    monkeypatch.undo()
    untouched()
    assert staged.file is None


def test_v1_model_gets_fields_and_keeps_its_file(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    store = BlobStore(p.folder)
    v1_grid = {"corner": [0.0, 0.0, 0.0], "counts": [10, 8, 6], "size": [5.0, 5.0, 2.5]}
    v1_fields = [{"name": "Au", "kind": "number", "unit": "g/t"}, {"name": "Порода", "kind": "category",
                                                                    "labels": ["руда", "порода"]}]  # fmt: skip
    idx, values = sample(120)
    v1 = {"Au": values["AU"], "Порода": (values["DOM"] % 2).astype(np.int32)}
    with dialect.write_tx(p.engine) as conn:
        bm_id = bm.put_block_model(conn, store, v1_grid, v1_fields, idx, v1, name="Старая")
        sha = bm.cells_sha(conn, bm_id)
        assert conn.execute(sa.select(objects.c.meta).where(objects.c.id == bm_id)).scalar()["v"] == 1
    n = add_fields(p, store, bm_id, [{"name": "Au2", "dtype": "f8"}], {"Au2": v1["Au"] * 2}, step=50)
    assert n == 2
    with dialect.read_tx(p.engine) as conn:
        meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == bm_id)).scalar()
        info = bm.block_model_info(conn, bm_id)
        got_idx, got = bm.read_cells(conn, store, bm_id)
        assert bm.cells_sha(conn, bm_id) == sha  # the v1 file is not rewritten
    assert meta["v"] == 2 and meta["rows"] == 120 and meta["groups"] is None and info["meta_v"] == 2
    assert [f["file"] for f in info["fields"]] == ["cells", "cells", info["fields"][2]["file"]]
    assert np.array_equal(got_idx, idx) and same(got["Au"], v1["Au"]) and same(got["Au2"], v1["Au"] * 2)
    assert np.array_equal(got["Порода"], v1["Порода"]) and got["Порода"].dtype == np.int32
    key = info["fields"][2]["file"]
    with dialect.read_tx(p.engine) as conn:
        assert groups_of(store, bm.cells_sha(conn, bm_id, key)) == [120]  # one group, as the v1 file has


# --- `.bfggis` ------------------------------------------------------------------------------------------------


def test_bfggis_roundtrip_keeps_the_files_of_fields(bc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    store = BlobStore(p.folder)
    bm_id = make_model(p, store)
    _, values = sample()
    add_fields(p, store, bm_id, [{"name": "AU2", "dtype": "f8", "role": "density"}], {"AU2": values["AU"] * 2})
    add_fields(p, store, bm_id, [{"name": "CLS", "dtype": "cat", "labels": ["a", "b"], "role": "class"}],
               {"CLS": np.where(np.isnan(values["AU"]), -1, 1).astype(np.int32)})  # fmt: skip
    add_fields(p, store, bm_id, [{"name": "AU", "dtype": "f8", "replace": True}], {"AU": values["AU"] + 1})
    with dialect.read_tx(p.engine) as conn:
        before = bm.block_model_info(conn, bm_id)
        want = bm.read_cells(conn, store, bm_id)
        files = {r: bm.cells_sha(conn, bm_id, r) for r in roles(p, bm_id)}
    assert len(files) == 4 and before["version"] == 4
    path = Path(export(bc, tmp_path / "Блоки")["result"]["path"])
    r = bc.post(f"{API}/projects/import", json={"path": str(path), "mode": "copy"}).json()
    job = wait_job(bc, r["source"], r["id"])
    assert job["status"] == "done", job
    res = job["result"]
    bc.post(f"{API}/projects/{res['uid']}/open")
    new_id = int(res["id_maps"]["objects"][str(bm_id)])
    q = project(bc)
    qstore = BlobStore(q.folder)
    after = bc.get(f"{API}/blocks/{new_id}").json()
    keep = ("name", "extent", "grid", "cells", "rows", "parents", "sub", "fields", "groups", "source", "size", "meta_v")
    assert {k: after[k] for k in keep} == {k: before[k] for k in keep}
    with dialect.read_tx(q.engine) as conn:
        again = {r: bm.cells_sha(conn, new_id, r) for r in roles(q, new_id)}
        got = bm.read_cells(conn, qstore, new_id)
    assert again == files  # the same files under the same roles
    assert np.array_equal(got[0], want[0]) and all(same(got[1][k], want[1][k]) for k in want[1])
    assert gc.collect(q.engine, qstore).blobs == 0  # nothing of the model is left unowned


# --- PostgreSQL (BF_TEST_PG; SQLite always) -----------------------------------------------------------------------


@pytest.mark.db
def test_fields_commit_on_both_databases(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    store = BlobStore(tmp_path / "blobs")
    store.tmp_dir.mkdir(parents=True, exist_ok=True)
    idx, values = sample()
    with bm.CellsWriterV2(store, GRID, FIELDS, row_group=RG) as w:
        w.write(idx, values)
        staged = w.finish()
    with dialect.write_tx(engine) as conn:
        bm_id = bm.commit_block_model(conn, store, staged, name="Модель")
        sha = bm.cells_sha(conn, bm_id)
    with dialect.read_tx(engine) as conn:
        model = fs.ModelRef.load(conn, bm_id)
    with fs.FieldsWriter(store, model, 1) as w:
        w.add("AU2", "f8", role="density")
        w.write({"AU2": values["AU"] * 2})
        staged_f = w.finish()
    with dialect.write_tx(engine) as conn:
        assert fs.commit_fields(conn, store, staged_f, source=SRC) == 2
    with dialect.write_tx(engine) as conn:  # the same version again: 409, the row stays as it is
        with pytest.raises(ApiError) as exc:
            fs.delete_fields(conn, bm_id, 1, ["AU2"])
        assert exc.value.status == 409 and exc.value.extra == {"version": 2}
    with dialect.write_tx(engine) as conn:
        assert fs.delete_fields(conn, bm_id, 2, ["AU2"]) == 3
    with dialect.read_tx(engine) as conn:
        assert bm.cells_sha(conn, bm_id) == sha
        got = bm.read_cells(conn, store, bm_id)
        roles_now = set(conn.execute(sa.select(blob_owners.c.role).where(blob_owners.c.owner_id == bm_id)).scalars())
    assert roles_now == {"cells"} and same(got[1]["AU"], values["AU"])
    engine.dispose()
