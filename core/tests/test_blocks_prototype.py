"""The prototype of a block model (подшаг 17.19; ТЗ 8.7.7; ADR 0023 §6, decision 66): an empty grid by the box of
objects or the given one — the origin is a multiple of the block, a pad, a turn about Z, sub-blocks `div`, the limit of
the grid (2·10⁹), a model with 0 rows (the flag «все родители заняты» in the meta, no 20 million rows written), the
round trip through the store and `.bfggis`, 20 million parents in ≤ 2 s, the same on PostgreSQL (`-m db`)."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import meta as bmeta
from bf_core.blocks import prototype as pr
from bf_core.blocks import schema as bs
from bf_core.blocks import store as bm
from bf_core.db import dialect, migrate
from bf_core.db.schema import objects
from bf_core.projects.service import projects_of
from bf_core.storage import gc
from bf_core.storage.blobs import BlobStore
from tests.dbutil import make_engine
from tests.test_archive import export, wait_job

API = "/api/v1"
BOX = [1000.0, 2000.0, -50.0, 1203.0, 2117.0, 31.0]


@pytest.fixture()
def blocks_router(monkeypatch: pytest.MonkeyPatch) -> None:
    import bf_core.api.app as app_module

    if "blocks" not in app_module.ROUTER_MODULES:
        monkeypatch.setattr(app_module, "ROUTER_MODULES", (*app_module.ROUTER_MODULES, "blocks"))


@pytest.fixture()
def bc(blocks_router, pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects", json={"name": "Прототип"}).status_code == 200
    return pclient


def project(c: Any) -> Any:
    return projects_of(c.core).require_current()


def corners(box: list[float]) -> np.ndarray:
    return np.array([[x, y, z] for x in (box[0], box[3]) for y in (box[1], box[4]) for z in (box[2], box[5])])


def covers(grid: bs.GridV2, box: list[float], pad: Any = 0.0) -> bool:
    """The corners of the box are inside the grid with `pad` to spare on every side (the pad is in the local axes)."""
    q = grid.to_local(corners(box))
    pd = np.broadcast_to(np.asarray(pad, dtype=np.float64), 3)
    return bool((q >= pd - 1e-9).all() and (q <= np.array(grid.lengths()) - pd + 1e-9).all())


def rows_of(p: Any, bm_id: int) -> int:
    with dialect.read_tx(p.engine) as conn:
        return int(bm.read_cells(conn, BlobStore(p.folder), bm_id)[0].size)


# --- the grid ------------------------------------------------------------------------------------------------------


def test_empty_grid_origin_is_a_multiple_of_the_block_and_covers_the_box() -> None:
    plan = pr.make_plan([10, 10, 5], BOX)
    g = plan.grid
    assert g.rotation == 0.0 and g.sub.div == (1, 1, 1) and not plan.full
    assert g.corner == (1000.0, 2000.0, -50.0)  # 1000 = 100·10, 2000 = 200·10, −50 = −10·5
    assert g.counts == (21, 12, 17)  # ⌈203/10⌉, ⌈117/10⌉, ⌈81/5⌉
    assert covers(g, BOX) and plan.parents == 21 * 12 * 17
    # a box between nodes: the origin goes down to the node, the far side up
    odd = pr.make_plan([10, 10, 5], [1003.5, 1996.2, -48.0, 1020.0, 2010.0, -41.0])
    assert odd.grid.corner == (1000.0, 1990.0, -50.0) and odd.grid.counts == (2, 2, 2)
    assert covers(odd.grid, [1003.5, 1996.2, -48.0, 1020.0, 2010.0, -41.0])


def test_origin_stays_a_multiple_for_negative_coordinates_and_a_box_on_the_nodes() -> None:
    plan = pr.make_plan([20, 20, 20], [-95.0, -5.0, -100.0, -40.0, 0.0, -80.0])
    assert plan.grid.corner == (-100.0, -20.0, -100.0)
    assert plan.grid.counts == (3, 1, 1)  # the max of Y = 0 lies on a node: no extra layer


def test_pad_widens_every_side_and_may_be_per_axis() -> None:
    plain = pr.make_plan([10, 10, 5], BOX)
    wide = pr.make_plan([10, 10, 5], BOX, pad=20)
    assert wide.grid.corner == (980.0, 1980.0, -70.0)
    assert wide.grid.counts == (plain.grid.counts[0] + 4, plain.grid.counts[1] + 4, plain.grid.counts[2] + 8)
    assert covers(wide.grid, BOX, 20)
    per = pr.make_plan([10, 10, 5], BOX, pad=[10, 0, 5])
    assert per.grid.corner == (990.0, 2000.0, -55.0)
    assert per.params()["pad"] == [10.0, 0.0, 5.0]


@pytest.mark.parametrize("angle", [30.0, -17.5, 90.0, 180.0, -90.0, 450.0])
def test_rotated_grid_is_on_the_lattice_of_its_axes(angle: float) -> None:
    plan = pr.make_plan([10, 8, 5], BOX, rotation=angle, pad=12)
    g = plan.grid
    assert g.rotation == bs.normalize_angle(angle)
    assert covers(g, BOX, 12)
    o = g.to_local(np.zeros((1, 3)))[0]  # the project's origin in the axes of the grid
    for a in range(3):
        k = -o[a] / g.size[a]  # type: ignore[index]
        tol = 0.0 if abs(angle) % 90 == 0 else 1e-9 * max(1.0, abs(k))
        assert abs(k - round(k)) <= tol + 1e-9, (angle, a, k)  # O is a whole number of blocks from the project origin
    if abs(angle) % 90 == 0:
        c = np.array(g.corner[:2]) / np.array(g.size[:2])  # type: ignore[index]
        assert np.array_equal(c, np.round(c)) or angle % 180 != 0  # exact for the multiples of 90°
    assert g.cells == plan.parents and plan.grid.extent() == g.extent()


def test_rotation_90_is_exact_and_swaps_the_axes() -> None:
    plan = pr.make_plan([10, 10, 5], [0.0, 0.0, 0.0, 100.0, 50.0, 10.0], rotation=90)
    g = plan.grid
    assert g.sin_cos() == (1.0, 0.0) and g.counts == (5, 10, 2)
    assert g.corner == (100.0, 0.0, 0.0)  # O′ = (0, −100) in the turned axes, O = R_z(90°)·O′ — exact
    q = g.to_local(corners([0.0, 0.0, 0.0, 100.0, 50.0, 10.0]))
    assert (q >= 0).all() and (q <= np.array(g.lengths())).all()
    assert np.array_equal(plan.frame()[0], [100.0, 0.0, 0.0]) and g.extent() == [0.0, 0.0, 0.0, 100.0, 50.0, 10.0]


def test_levels_make_div_and_sub_blocks_cost_more_disk() -> None:
    flat = pr.make_plan([10, 10, 5], BOX)
    sub = pr.make_plan([10, 10, 5], BOX, levels=[3, 3, 2])
    assert sub.grid.sub.div == (8, 8, 4) and sub.grid.counts == flat.grid.counts and sub.grid.sub.sub
    assert flat.disk == flat.parents * 18 and sub.disk == sub.parents * 24
    assert sub.params()["levels"] == [3, 3, 2]
    for bad in ([8, 0, 0], [-1, 0, 0], [0.5, 0, 0]):
        with pytest.raises(ApiError) as exc:
            pr.make_plan([10, 10, 5], BOX, levels=bad)
        assert exc.value.status == 422 and exc.value.code == "subblock_div", bad
    with pytest.raises(ApiError) as exc:
        pr.make_plan([10, 10, 5], BOX, levels=[1, 2])
    assert exc.value.code == "bad_param" and exc.value.extra == {"param": "levels"}


def test_preview_frame_is_the_turned_box_x_fastest() -> None:
    plan = pr.make_plan([10, 10, 5], [0.0, 0.0, 0.0, 30.0, 20.0, 10.0], rotation=30)
    out = plan.preview()
    g = plan.grid
    assert out["parents"] == g.cells and out["disk"] == plan.disk and out["extent"] == g.extent()
    assert out["grid"] == g.to_meta() and out["grid"]["rotation"] == 30.0 and out["grid"]["div"] == [1, 1, 1]
    lx, ly, lz = g.lengths()
    want = g.to_project([[0, 0, 0], [lx, 0, 0], [0, ly, 0], [lx, ly, 0], [0, 0, lz], [lx, 0, lz], [0, ly, lz],
                         [lx, ly, lz]])  # fmt: skip
    assert len(out["frame"]) == 8 and np.array_equal(np.array(out["frame"]), want)
    box = np.array(out["frame"])
    assert np.allclose([*box.min(axis=0), *box.max(axis=0)], out["extent"])


# --- the limit and the errors -------------------------------------------------------------------------------------


def test_limit_of_the_grid_is_2e9_cells() -> None:
    fits = pr.make_plan([1, 1, 1], [0.0, 0.0, 0.0, 1000.0, 1000.0, 2000.0])  # exactly 2·10⁹
    assert fits.parents == 2_000_000_000 and fits.disk == 36_000_000_000
    for box in ([0.0, 0.0, 0.0, 1000.0, 1000.0, 2001.0], [0.0, 0.0, 0.0, 1e9, 1e9, 1e9]):
        with pytest.raises(ApiError) as exc:
            pr.make_plan([1, 1, 1], box)
        e = exc.value
        assert (e.status, e.code) == (422, "block_model_too_large") and e.hint and "предела" in e.message


@pytest.mark.parametrize(
    ("kwargs", "param"),
    [
        ({"size": [10, 0, 5]}, "size"),
        ({"size": [10, 10]}, "size"),
        ({"size": [10, "a", 5]}, "size.1"),
        ({"size": [10, float("nan"), 5]}, "size.1"),
        ({"size": [10, 10, 5], "pad": -1}, "pad"),
        ({"size": [10, 10, 5], "rotation": float("inf")}, "grid.rotation"),
        ({"size": [10, 10, 5], "rotation": {"x": 5}}, "grid.rotation.x"),
        ({"size": [10, 10, 5], "extent": [0, 0, 0, 1]}, "extent"),
        ({"size": [10, 10, 5], "extent": [5, 0, 0, 1, 1, 1]}, "extent"),
        ({"size": [10, 10, 5], "extent": [0, 0, 0, 1, float("nan"), 1]}, "extent.4"),
        ({"size": [10, 10, 5], "full": 1}, "full"),
    ],
)
def test_errors_are_russian_with_a_param(kwargs: dict[str, Any], param: str) -> None:
    kw = {"extent": BOX, **kwargs}
    with pytest.raises(ApiError) as exc:
        pr.make_plan(kw.pop("size"), kw.pop("extent"), **kw)
    e = exc.value
    assert e.status == 422 and e.code in ("bad_param", "rotation_unsupported") and e.extra["param"] == param
    assert e.message and e.hint and any("а" <= ch <= "я" for ch in e.message.lower())  # Russian text, with a hint


# --- the box of objects --------------------------------------------------------------------------------------------


def make(p: Any, plan: pr.Plan, name: str = "Прототип", **kw: Any) -> dict[str, Any]:
    with dialect.write_tx(p.engine) as conn:
        return pr.create_prototype(conn, BlobStore(p.folder), plan, name=name, **kw)


def test_extent_of_objects_is_their_union_and_the_errors(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    a = make(p, pr.make_plan([10, 10, 5], [0.0, 0.0, 0.0, 100.0, 50.0, 20.0]), "A")["model_id"]
    b = make(p, pr.make_plan([10, 10, 5], [200.0, -40.0, -10.0, 260.0, 10.0, 90.0]), "B")["model_id"]
    with dialect.write_tx(p.engine) as conn:
        conn.execute(sa.update(objects).where(objects.c.id == b).values(extent=None))
    with dialect.read_tx(p.engine) as conn:
        one = pr.resolve_extent(conn, [a])
        assert one == [0.0, 0.0, 0.0, 100.0, 50.0, 20.0]
        assert pr.resolve_extent(conn, None, BOX) == BOX
        with pytest.raises(ApiError) as exc:  # no extent at all
            pr.resolve_extent(conn, [b])
        assert (exc.value.status, exc.value.code) == (422, "no_extent") and exc.value.hint
        with pytest.raises(ApiError) as exc:
            pr.resolve_extent(conn, [a, 987654])
        assert (exc.value.status, exc.value.code) == (404, "object_not_found")
        assert exc.value.extra == {"param": "object_ids.1"}
        for args in ((None, None), ([a], BOX), ([], None)):  # exactly one of the two
            with pytest.raises(ApiError) as exc:
                pr.resolve_extent(conn, *args)
            assert (exc.value.status, exc.value.code) == (422, "bad_param") and exc.value.extra["param"] == "object_ids"


def test_extent_union_of_two_objects(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    a = make(p, pr.make_plan([10, 10, 5], [0.0, 0.0, 0.0, 100.0, 50.0, 20.0]), "A")["model_id"]
    b = make(p, pr.make_plan([10, 10, 5], [200.0, -40.0, -10.0, 260.0, 10.0, 90.0]), "B")["model_id"]
    with dialect.read_tx(p.engine) as conn:
        assert pr.resolve_extent(conn, [a, b]) == [0.0, -40.0, -10.0, 260.0, 50.0, 90.0]


# --- the model: 0 rows, the flag, the round trip -------------------------------------------------------------------


def test_prototype_is_a_model_without_rows_and_the_flag_is_in_the_meta(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    plan = pr.make_plan([10, 10, 5], BOX, pad=20, rotation=30, levels=[2, 2, 1])
    out = make(p, plan, "Прототип")
    assert out["parents"] == plan.parents and out["grid"] == plan.grid.to_meta() and out["name"] == "Прототип"
    assert out["events"] == [["objects.changed", {"kind": "block_model", "id": out["model_id"]}]]
    assert make(p, plan, "Прототип")["name"] == "Прототип (2)"  # a free name, as for any model
    with dialect.read_tx(p.engine) as conn:
        info = bm.block_model_info(conn, out["model_id"])
        raw = conn.execute(sa.select(objects.c.meta, objects.c.extent).where(objects.c.id == out["model_id"])).one()
        idx, cols = bm.read_cells(conn, BlobStore(p.folder), out["model_id"])
    assert idx.size == 0 and cols == {}
    assert info["meta_v"] == 2 and info["rows"] == 0 and info["cells"] == 0 and info["parents"] == 0
    assert info["full"] is False and info["sub"] is True and info["fields"] == [] and info["groups"] == []
    assert info["source"]["op"] == "prototype" and info["source"]["params"] == plan.params()
    assert info["grid"]["div"] == [4, 4, 2] and info["grid"]["rotation"] == 30.0 and info["extent"] == raw.extent
    assert raw.extent == plan.grid.extent() and bs.GridV2.of(raw.meta["grid"]) == plan.grid
    assert bmeta.upgrade(raw.meta) == raw.meta  # a stored meta is already in the checked form


def test_flag_all_parents_occupied_without_writing_a_row(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    plan = pr.make_plan([10, 10, 5], BOX, full=True)
    out = make(p, plan, "Полный")
    with dialect.read_tx(p.engine) as conn:
        info = bm.block_model_info(conn, out["model_id"])
        meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == out["model_id"])).scalar_one()
        size = bm.block_model_info(conn, out["model_id"])["size"]
    assert info["full"] is True and info["rows"] == 0 and meta["full"] is True and meta["parents"] == 0
    assert size < 4096  # the empty file only: no row per parent
    assert out["parents"] == plan.parents == 21 * 12 * 17
    info2 = pr.prototype_info(meta)
    assert info2 is not None and info2["full"] and info2["parents"] == plan.parents and not info2["sub"]
    assert info2["grid"] == plan.grid


def test_prototype_info_tells_prototypes_from_models(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    out = make(p, pr.make_plan([10, 10, 5], BOX))
    with dialect.read_tx(p.engine) as conn:
        meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == out["model_id"])).scalar_one()
    assert pr.prototype_info(meta) is not None
    assert pr.prototype_info({**meta, "source": {"op": "import"}}) is None
    v1 = bs.make_meta(bs.check_grid({"corner": [0, 0, 0], "counts": [2, 2, 2], "size": [1, 1, 1]}), 0, [])
    assert pr.prototype_info(v1) is None


def test_failed_create_leaves_nothing(bc, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    store = BlobStore(p.folder)
    plan = pr.make_plan([10, 10, 5], BOX)

    def boom(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(bm, "commit_block_model", boom)
    with pytest.raises(RuntimeError), dialect.write_tx(p.engine) as conn:
        pr.create_prototype(conn, store, plan)
    assert not store.tmp_dir.exists() or not any(store.tmp_dir.iterdir())
    with dialect.read_tx(p.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(objects)).scalar_one() == 0


def test_bfggis_roundtrip_keeps_the_prototype(bc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    plan = pr.make_plan([10, 10, 5], BOX, pad=10, rotation=-25, levels=[1, 1, 1], full=True)
    bm_id = make(p, plan)["model_id"]
    before = bc.get(f"{API}/blocks/{bm_id}").json()
    path = Path(export(bc, tmp_path / "Прототип")["result"]["path"])
    r = bc.post(f"{API}/projects/import", json={"path": str(path), "mode": "copy"}).json()
    job = wait_job(bc, r["source"], r["id"])
    assert job["status"] == "done", job
    res = job["result"]
    bc.post(f"{API}/projects/{res['uid']}/open")
    new_id = int(res["id_maps"]["objects"][str(bm_id)])
    after = bc.get(f"{API}/blocks/{new_id}").json()
    keep = ("name", "extent", "grid", "rows", "parents", "sub", "full", "fields", "source", "meta_v")
    assert {k: after[k] for k in keep} == {k: before[k] for k in keep}
    assert after["full"] is True and after["rows"] == 0
    q = project(bc)
    assert gc.collect(q.engine, BlobStore(q.folder)).blobs == 0 and rows_of(q, new_id) == 0


# --- 20 million parents ≤ 2 s ------------------------------------------------------------------------------------


def test_prototype_of_20_million_parents_in_two_seconds(bc) -> None:  # type: ignore[no-untyped-def]
    p = project(bc)
    box = [0.0, 0.0, 0.0, 4000.0, 4000.0, 1250.0]  # 400 × 400 × 125 blocks of 10 m
    t0 = time.perf_counter()
    plan = pr.make_plan([10, 10, 10], box, pad=0, rotation=30, levels=[3, 3, 3], full=True)
    prev = plan.preview()
    t_plan = time.perf_counter() - t0
    out = make(p, plan, "20 млн")
    total = time.perf_counter() - t0
    assert plan.parents >= 20_000_000 and prev["parents"] == out["parents"] == plan.parents
    assert total <= 2.0, f"plan {t_plan:.3f} s, total {total:.3f} s"
    print(f"\nprototype {plan.parents:,} parents: plan {t_plan * 1e3:.1f} ms, with the model {total * 1e3:.1f} ms")
    assert rows_of(p, out["model_id"]) == 0


# --- PostgreSQL (BF_TEST_PG; SQLite always) -----------------------------------------------------------------------


@pytest.mark.db
def test_prototype_commit_on_both_databases(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    store = BlobStore(tmp_path / "blobs")
    store.tmp_dir.mkdir(parents=True, exist_ok=True)
    plan = pr.make_plan([10, 10, 5], BOX, pad=10, rotation=30, levels=[2, 2, 2], full=True)
    with dialect.write_tx(engine) as conn:
        out = pr.create_prototype(conn, store, plan, name="Прототип")
    with dialect.read_tx(engine) as conn:
        info = bm.block_model_info(conn, out["model_id"])
        idx, _ = bm.read_cells(conn, store, out["model_id"])
        assert pr.resolve_extent(conn, [out["model_id"]]) == plan.grid.extent()
    assert info["full"] is True and info["rows"] == 0 and info["parents"] == 0 and idx.size == 0
    assert bs.GridV2.of(info["grid"]) == plan.grid and info["source"]["op"] == "prototype"
    assert math.isclose(info["extent"][3], plan.grid.extent()[3])
    engine.dispose()
