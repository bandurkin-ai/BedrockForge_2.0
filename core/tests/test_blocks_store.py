"""Regular block models of Э11 (ADR 0017 §3): grid, fields, stats, deleting, `.bfggis` round trip."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import schema as bs
from bf_core.blocks import store as bm
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, objects
from bf_core.projects.service import projects_of
from bf_core.storage import gc
from bf_core.storage.blobs import BlobStore
from tests.test_archive import export, wait_job

API = "/api/v1"
GRID = {"corner": [1000.5, 2000.25, -300.0], "counts": [30, 20, 20], "size": [5.0, 5.0, 2.5]}
FIELDS = [
    {"name": "Au", "kind": "number", "unit": "g/t"},
    {"name": "Порода", "kind": "category", "labels": ["руда", "порода", "дайка"]},
]


@pytest.fixture()
def blocks_router(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before the core starts: its app has the router of the block models (until `api/modules.py` lists it)."""
    import bf_core.api.app as app_module

    if "blocks" not in app_module.ROUTER_MODULES:
        monkeypatch.setattr(app_module, "ROUTER_MODULES", (*app_module.ROUTER_MODULES, "blocks"))


@pytest.fixture()
def bc(blocks_router, pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects", json={"name": "Блоки"}).status_code == 200
    return pclient


def sample(n: int = 10_000, seed: int = 7) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(bs.cell_count(GRID), size=n, replace=False)).astype(np.uint64)
    au = rng.lognormal(0.0, 1.0, n)
    au[rng.random(n) < 0.1] = np.nan  # empty values stay empty
    rock = rng.integers(-1, 3, n).astype(np.int32)
    return idx, {"Au": au, "Порода": rock}


def stage(store: BlobStore, idx: np.ndarray, values: dict[str, np.ndarray], batch: int = 3_000) -> bm.StagedCells:
    with bm.CellsWriter(store, GRID, FIELDS, row_group=4_000) as w:
        for a in range(0, len(idx), batch):
            w.write(idx[a : a + batch], {k: v[a : a + batch] for k, v in values.items()})
        return w.finish()


def project(c: Any) -> Any:
    return projects_of(c.core).require_current()


def test_model_10k_cells_grid_fields_stats(bc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import pyarrow.parquet as pq

    p = project(bc)
    store = BlobStore(p.folder)
    idx, values = sample()
    staged = stage(store, idx, values)
    assert staged.cells == 10_000 and staged.file is not None
    meta = pq.ParquetFile(staged.file[0]).metadata
    assert meta.num_row_groups == 3 and meta.row_group(0).num_rows == 4_000  # 4 000 + 4 000 + 2 000
    with dialect.write_tx(p.engine) as conn:
        bm_id = bm.commit_block_model(conn, store, staged, name="Модель", source={"format": "omf", "file": "a.omf"})
    assert staged.file is None and not any(store.tmp_dir.iterdir())
    with dialect.read_tx(p.engine) as conn:
        info = bm.block_model_info(conn, bm_id)
        got_idx, got = bm.read_cells(conn, store, bm_id)
        only, part = bm.read_cells(conn, store, bm_id, ["Au"])
    assert info["grid"] == {**GRID, "order": "F", "variable": False} and info["cells"] == 10_000
    assert info["extent"] == [1000.5, 2000.25, -300.0, 1150.5, 2100.25, -250.0]
    assert info["source"] == {"format": "omf", "file": "a.omf", "element": None} and info["size"] > 0
    assert got_idx.dtype == np.uint64 and np.array_equal(got_idx, idx) and np.array_equal(only, idx)
    assert np.array_equal(got["Au"], values["Au"], equal_nan=True) and set(part) == {"Au"}
    assert got["Порода"].dtype == np.int32 and np.array_equal(got["Порода"], values["Порода"])
    au, rock = info["fields"]
    ok = values["Au"][np.isfinite(values["Au"])]
    assert au["unit"] == "g/t" and au["stats"]["count"] == len(ok) and au["stats"]["missing"] == 10_000 - len(ok)
    assert au["stats"]["min"] == ok.min() and au["stats"]["max"] == ok.max()
    assert au["stats"]["mean"] == pytest.approx(ok.mean(), rel=1e-12)
    assert au["stats"]["std"] == pytest.approx(ok.std(), rel=1e-10)
    codes = values["Порода"]
    assert rock["labels"] == ["руда", "порода", "дайка"] and rock["unit"] is None
    assert rock["stats"] == {
        "count": int((codes >= 0).sum()), "missing": int((codes == -1).sum()),
        "counts": np.bincount(codes[codes >= 0], minlength=3).tolist(),
    }  # fmt: skip
    # portions of the export: the same cells in batches of a chosen size
    with dialect.read_tx(p.engine) as conn:
        sha = bm.cells_sha(conn, bm_id)
    sizes = [len(i) for i, _ in bm.iter_cells(store, sha, ["Порода"], batch_rows=2_500)]
    assert sum(sizes) == 10_000 and max(sizes) <= 2_500
    xyz = bs.centers(info["grid"], idx[:3])
    i, j, k = bs.ijk_of(idx[:3], GRID["counts"])
    assert np.array_equal(bs.idx_of(i, j, k, GRID["counts"]), idx[:3])
    assert np.allclose(xyz, np.c_[1000.5 + 5 * (i + 0.5), 2000.25 + 5 * (j + 0.5), -300 + 2.5 * (k + 0.5)])


def test_list_get_rename_delete_frees_the_blob(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    store = BlobStore(p.folder)
    idx, values = sample(500)
    with dialect.write_tx(p.engine) as conn:
        bm_id = bm.put_block_model(conn, store, GRID, FIELDS, idx, values, name="Модель")
        twin = bm.put_block_model(conn, store, GRID, FIELDS, idx[:10], {k: v[:10] for k, v in values.items()},
                                  name=" модель ")  # fmt: skip
        sha = bm.cells_sha(conn, bm_id)
    listed = bc.get(f"{API}/blocks").json()["block_models"]
    assert [b["name"] for b in listed] == ["Модель", "модель (2)"]
    assert listed[0]["fields"] == [{"name": "Au", "kind": "number", "unit": "g/t"},
                                   {"name": "Порода", "kind": "category", "unit": None}]  # fmt: skip
    one = bc.get(f"{API}/blocks/{bm_id}").json()
    assert one["fields"][1]["stats"]["count"] + one["fields"][1]["stats"]["missing"] == 500
    assert bc.get(f"{API}/blocks/999").json()["code"] == "block_model_not_found"
    r = bc.patch(f"{API}/blocks/{bm_id}", json={"name": "Модель (2)", "expected_version": 1})
    assert r.status_code == 409 and r.json()["code"] == "name_taken"
    r = bc.patch(f"{API}/blocks/{twin}", json={"name": "Блоки 2026", "expected_version": 1})
    assert r.status_code == 200 and r.json()["version"] == 2
    r = bc.patch(f"{API}/blocks/{twin}", json={"name": "Другое", "expected_version": 1})
    assert r.status_code == 409 and r.json()["current"]["name"] == "Блоки 2026"
    assert bc.delete(f"{API}/blocks/{bm_id}").json() == {"deleted": bm_id}
    names = [(n, json.loads(d)) for _i, n, d in bc.core.events._recent if n == "objects.changed"]
    assert names[-1][1] == {"kind": "block_model", "id": bm_id, "deleted": True}
    with dialect.read_tx(p.engine) as conn:
        assert conn.execute(sa.select(blob_owners).where(blob_owners.c.owner_id == bm_id)).first() is None
    assert store.exists(sha)
    report = gc.collect(p.engine, store)
    assert report.blobs == 1 and not store.exists(sha)
    assert [b["id"] for b in bc.get(f"{API}/blocks").json()["block_models"]] == [twin]


def test_building_model_is_hidden_and_counted_only_when_ready(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    store = BlobStore(p.folder)
    idx, values = sample(100)
    with dialect.write_tx(p.engine) as conn:
        bm_id = bm.put_block_model(conn, store, GRID, FIELDS, idx, values, name="Идёт", state="building", job_id=5)
    assert bc.get(f"{API}/blocks").json()["block_models"] == []
    assert bc.get(f"{API}/blocks/{bm_id}").status_code == 404
    with dialect.write_tx(p.engine) as conn:
        conn.execute(sa.update(objects).where(objects.c.id == bm_id).values(state="ready", job_id=None))
    assert bc.get(f"{API}/projects/current").json()["project"]["summary"]["block_models"] == 1


def test_bfggis_roundtrip_keeps_meta_blob_and_counter(bc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    store = BlobStore(p.folder)
    idx, values = sample()
    tensors = {"corner": [0.0, 0.0, 0.0], "tensors": {"x": [1.0, 2.0] * 15, "y": [3.0] * 20, "z": [0.5] * 20}}
    with dialect.write_tx(p.engine) as conn:
        bm_id = bm.put_block_model(conn, store, GRID, FIELDS, idx, values, name="Модель", folder="Участок 1",
                                   crs="EPSG:32638", warnings=["Повёрнутая модель «Б» не загружена"])  # fmt: skip
        var_id = bm.put_block_model(conn, store, tensors, FIELDS[:1], idx[:5], {"Au": values["Au"][:5]},
                                    name="Переменный шаг")  # fmt: skip
        before = bm.block_model_info(conn, bm_id)
        sha = bm.cells_sha(conn, bm_id)
    job = export(bc, tmp_path / "Блоки")
    path = Path(job["result"]["path"])
    info = bc.post(f"{API}/projects/import/inspect", json={"path": str(path)}).json()
    assert info["summary"]["block_models"] == 2
    r = bc.post(f"{API}/projects/import", json={"path": str(path), "mode": "copy"}).json()
    job = wait_job(bc, r["source"], r["id"])
    assert job["status"] == "done", job
    res = job["result"]
    opened = bc.post(f"{API}/projects/{res['uid']}/open").json()
    assert opened["summary"]["block_models"] == 2
    new_id = int(res["id_maps"]["objects"][str(bm_id)])
    new_var = int(res["id_maps"]["objects"][str(var_id)])
    after = bc.get(f"{API}/blocks/{new_id}").json()
    keep = ("name", "folder", "crs", "extent", "grid", "cells", "fields", "source", "warnings", "size")
    assert {k: after[k] for k in keep} == {k: before[k] for k in keep}
    var = bc.get(f"{API}/blocks/{new_var}").json()
    assert var["grid"]["tensors"]["x"][:2] == [1.0, 2.0] and var["grid"]["variable"] is True
    assert var["extent"] == [0.0, 0.0, 0.0, 45.0, 60.0, 10.0]
    q = project(bc)
    with dialect.read_tx(q.engine) as conn:
        assert bm.cells_sha(conn, new_id) == sha
        got_idx, got = bm.read_cells(conn, BlobStore(q.folder), new_id)
    assert np.array_equal(got_idx, idx) and np.array_equal(got["Au"], values["Au"], equal_nan=True)


@pytest.mark.parametrize(
    ("grid", "code"),
    [
        ({"corner": [0, 0, 0], "counts": [2, 2, 2]}, "block_model_invalid"),  # no step
        ({"corner": [0, 0, 0], "counts": [2, 2, 2], "size": [1, 0, 1]}, "block_model_invalid"),
        ({"corner": [0, 0, float("nan")], "counts": [2, 2, 2], "size": [1, 1, 1]}, "block_model_invalid"),
        ({"corner": [0, 0, 0], "counts": [2, 2.5, 2], "size": [1, 1, 1]}, "block_model_invalid"),
        ({"corner": [0, 0, 0], "counts": [1000, 1000, 201], "size": [1, 1, 1]}, "block_model_too_large"),
        ({"corner": [0, 0], "counts": [2, 2, 2], "size": [1, 1, 1]}, "block_model_invalid"),
        ({"corner": [0, 0, 0], "counts": [3, 1, 1], "tensors": {"x": [1, 1], "y": [1], "z": [1]}},
         "block_model_invalid"),
    ],
)  # fmt: skip
def test_bad_grid_is_refused_in_russian(grid: dict[str, Any], code: str) -> None:
    with pytest.raises(ApiError) as e:
        bs.check_grid(grid)
    assert e.value.code == code and e.value.message and e.value.hint


def test_writer_refuses_bad_cells_and_leaves_no_temp_file(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    cases: list[tuple[Any, dict[str, Any]]] = [
        ([3, 2], {"Au": [1.0, 2.0], "Порода": [0, 1]}),  # not ascending
        ([1, 1], {"Au": [1.0, 2.0], "Порода": [0, 1]}),  # repeated
        ([12_000], {"Au": [1.0], "Порода": [0]}),  # outside the grid
        ([1], {"Au": [1.0], "Порода": [3]}),  # no such label
        ([1], {"Au": [1.0]}),  # a field without values
        ([1, 2], {"Au": [1.0], "Порода": [0, 0]}),  # lengths differ
    ]
    for idx, values in cases:
        with pytest.raises(ApiError) as e, bm.CellsWriter(store, GRID, FIELDS) as w:
            w.write(np.asarray(idx), values)
        assert e.value.code == "block_model_invalid", (idx, values)
    with pytest.raises(ApiError), bm.CellsWriter(store, GRID, FIELDS) as w:
        w.write(np.array([5]), {"Au": [np.inf], "Порода": [0]})
        w.write(np.array([4]), {"Au": [1.0], "Порода": [0]})  # behind the previous portion
    assert not any(store.tmp_dir.iterdir())
    with bm.CellsWriter(store, GRID, FIELDS) as w:
        w.write(np.array([5]), {"Au": [np.inf], "Порода": [-1]})
        staged = w.finish()
    assert staged.fields[0]["stats"] == {"count": 0, "missing": 1, "min": None, "max": None, "mean": None,
                                         "std": None}  # fmt: skip
    staged.discard()
    assert not any(store.tmp_dir.iterdir())


def test_fields_are_checked_and_file_names_made_valid() -> None:
    for bad in ([{"name": ""}], [{"name": "Au"}, {"name": "au"}], [{"name": "IDX"}], [{"name": "a", "kind": "x"}]):
        with pytest.raises(ApiError):
            bs.check_fields(bad)
    assert bs.field_names(["Au", "au", "", "idx", "  Cu  ppm "]) == ["Au", "au (2)", "Поле 3", "idx (2)", "Cu ppm"]
    assert bs.check_fields([{"name": n} for n in bs.field_names(["idx", "Au", "AU"])])
    empty = bm.StagedCells(bs.check_grid(GRID), [], 0, None)
    with pytest.raises(RuntimeError):
        bm.commit_block_model(None, None, empty, name="x")  # type: ignore[arg-type]
