"""The routes of the display of a block model (подшаг 17.27; ТЗ 8.7.6, ADR 0023 §11, decisions 16–19; docs/progress/
e17-api.md §12): BFA1 `blocks` (shell and all blocks, the three colours, the filter and the quick restrictor, LOD above
the limit, `origin`, the order of the faces, `classes`), BFA1 `blocks_slice`, the block under a point and the polygons
of a section — all on the real answers, the same as the kernels give; the columns of the cache, the answer kept, the
overtaking of requests (409 `superseded`), 404 / 422 in Russian.

`tests/data/blocks/viz_answers.json` — the answers of the routes that the interface (17.31, 17.32, 17.34) is read
against; regenerate: `BF_REGEN_BLOCKS_VIZ=1 uv run pytest -q tests/test_blocks_viz.py`."""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi import APIRouter

from bf_core.blocks import geometry, viz_routes
from bf_core.blocks import slice as sl
from bf_core.blocks import store as bm
from bf_core.blocks import viz_data as vd
from bf_core.blocks import viz_spec as sp
from bf_core.blocks.lod import view
from bf_core.blocks.schema import GridV2
from bf_core.blocks.shell_merge import NO_CLASS, break_bins, break_class, scale_bins, scale_class
from bf_core.db import dialect
from bf_core.projects.service import projects_of
from bf_core.stats import blocks_cache as bc
from bf_core.storage.blobs import BlobStore
from bf_core.viz import binary
from tests import golden
from tests.test_blocks_layer_fixture import encode as layer_encode
from tests.test_blocks_layer_fixture import grid_of, same_bfa1, split_model

API = "/api/v1"
VIZ = API + "/viz/blocks"
DATA = Path(__file__).parent / "data" / "blocks" / "viz_answers.json"
REGEN = os.environ.get("BF_REGEN_BLOCKS_VIZ") == "1"
LAYER = Path(__file__).parent / "data" / "blocks" / "layer_fixture.json"
ROCKS = ["руда", "порода", "окисленная", "дайка", "вода"]
FIELDS = [{"name": "AU", "dtype": "f8", "unit": "g/t"}, {"name": "OK", "dtype": "f8"},
          {"name": "ROCK", "dtype": "cat", "labels": ROCKS}]  # fmt: skip
COND = {"cond": {"field": "OK", "op": ">", "value": 0.5}}


@pytest.fixture()
def vc(pclient):  # type: ignore[no-untyped-def]
    """A core with an empty project and the routes of 17.27 (until `viz/router.py` and `blocks/router.py` name them)."""
    if not any(getattr(r, "path", "") == f"{VIZ}/{{bm_id}}/shell" for r in pclient.app.routes):
        viz = APIRouter(prefix="/viz")
        viz.include_router(viz_routes.router)
        pclient.app.include_router(viz, prefix=API)
        blocks = APIRouter(prefix="/blocks")
        blocks.include_router(viz_routes.polygons_router)
        pclient.app.include_router(blocks, prefix=API)
    assert pclient.post(f"{API}/projects", json={"name": "Показ"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.store = BlobStore(pclient.project.folder)
    vd.RESPONSES.clear()
    bc.MODELS.clear()
    return pclient


def put(c: Any, grid: GridV2, p: Any, values: dict[str, Any], s: Any = None, w: Any = None, name: str = "Модель",
        fields: Any = FIELDS) -> int:  # fmt: skip
    meta = {"corner": list(grid.corner), "counts": list(grid.counts), "size": list(grid.regular()),
            "div": list(grid.sub.div), "rotation": grid.rotation, "order": "F"}  # fmt: skip
    with bm.CellsWriterV2(c.store, meta, fields, row_group=1000) as wr:
        wr.write(p, values, s, w)
        staged = wr.finish()
    try:
        with dialect.write_tx(c.project.engine) as conn:
            return bm.commit_block_model(conn, c.store, staged, name=name, source={"format": "dat", "file": "m.dat"})
    finally:
        staged.discard()


def turned(c: Any) -> tuple[int, dict[str, Any]]:
    """The model of the first case of the fixture of the layer (17.30): turned 30°, sub-blocks, 8 % without a value,
    a share of the blocks that the filter drops (`OK`)."""
    grid = grid_of((6, 5, 4), (10.0, 10.0, 5.0), (2, 2, 2), 30.0)
    p, s, w = split_model(5, grid)
    rng = np.random.default_rng(7)
    au = np.exp(rng.normal(0.0, 1.2, len(p)))
    au[rng.random(len(p)) < 0.08] = np.nan
    ok = (rng.random(len(p)) < 0.9).astype(np.float64)
    rock = (np.arange(len(p)) * 7 % 5).astype(np.int32)
    rock[::11] = -1
    mid = put(c, grid, p, {"AU": au, "OK": ok, "ROCK": rock}, s, w, "Повёрнутая")
    return mid, {"grid": grid, "p": p, "s": s, "w": w, "AU": au, "OK": ok, "ROCK": rock}


def plain(c: Any, name: str = "Плоская") -> tuple[int, dict[str, Any]]:
    """A model with no turn and no sub-blocks: 8 × 6 × 4 parents, 10 × 10 × 5 m, a few of them missing."""
    grid = GridV2.of({"corner": [1000.0, 2000.0, 100.0], "counts": [8, 6, 4], "size": [10.0, 10.0, 5.0],
                      "rotation": 0.0, "order": "F"})  # fmt: skip
    rng = np.random.default_rng(11)
    p = np.array([q for q in range(8 * 6 * 4) if q % 13 != 5], dtype=np.uint64)
    au = rng.uniform(0.0, 4.0, len(p))
    au[3] = np.nan
    ok = (rng.random(len(p)) < 0.8).astype(np.float64)
    rock = rng.integers(0, 4, len(p)).astype(np.int32)
    mid = put(c, grid, p, {"AU": au, "OK": ok, "ROCK": rock}, name=name)
    return mid, {"grid": grid, "p": p, "AU": au, "OK": ok, "ROCK": rock}


def shell(c: Any, mid: int, **body: Any) -> Any:
    return c.post(f"{VIZ}/{mid}/shell", json=body)


def decoded(r: Any) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/octet-stream"
    return binary.decode(r.content)


def refused(r: Any, status: int, code: str, param: str | None = None) -> dict[str, Any]:
    e = r.json()
    assert (r.status_code, e["code"]) == (status, code), e
    assert e["message"] and e["hint"] and any("а" <= ch.lower() <= "я" for ch in e["message"]), e
    if param is not None:
        assert e["param"] == param, e
    return e  # type: ignore[no-any-return]


def same_faces(
    a: tuple[dict[str, Any], dict[str, np.ndarray]], b: tuple[dict[str, Any], dict[str, np.ndarray]]
) -> None:
    """Header (but the id and the version of the model and the warnings) and the arrays are equal (floats to the
    tolerance of `tests/golden.py`: the processor rounds the float64 of the rotation in its way)."""
    skip = ("model", "version", "warnings", "arrays")
    head_a = {k: v for k, v in a[0].items() if k not in skip}
    golden.assert_same(head_a, {k: v for k, v in b[0].items() if k not in skip})
    golden.same_arrays({n: a[1][n] for n in ("c", "h", "a", "k")}, {n: b[1][n] for n in ("c", "h", "a", "k")})


# --- BFA1 `blocks`: the same as the kernels and as the fixture of the layer -----------------------


def test_the_shell_is_the_answer_the_layer_was_read_against(vc) -> None:  # type: ignore[no-untyped-def]
    mid, m = turned(vc)
    r = shell(vc, mid, mode="shell", restrict=COND, colour={"field": "AU", "kind": "scale", "from": 0.1, "to": 10,
                                                           "log": True})  # fmt: skip
    got = decoded(r)
    case = next(c for c in json.loads(LAYER.read_text(encoding="utf-8"))["cases"] if c["name"] == "scale_turned")
    want = binary.decode(base64.b64decode(case["bfa1"]))
    same_faces(got, want)
    head = got[0]
    assert (head["kind"], head["format"], head["model"], head["version"]) == ("blocks", 1, mid, 1)
    assert head["corner"] == list(m["grid"].corner) and head["rotation"] == 30.0
    assert head["origin"] == case["origin"] and all(float(x).is_integer() for x in head["origin"])
    assert head["classes"] == 256 and len(head["bins"]) == 256 and head["labels"] is None
    assert (
        head["rows"] == len(m["p"])
        and head["passed"] == int(m["OK"].sum())
        and head["empty"] == head["rows"] - head["passed"]
    )
    assert (head["field"], head["warnings"], head["simplified"], head["lod"]) == ("AU", [], False, 0)
    assert head["faces"] == len(got[1]["a"]) == head["faces"] and head["faces_raw"] >= head["faces"]
    assert got[1]["c"].dtype == np.float32 and got[1]["k"].dtype == np.uint16 and got[1]["a"].dtype == np.uint8
    # the faces go by (a, plane, class, v, u) — the order of the shell
    assert (np.diff(got[1]["a"].astype(int)) >= 0).all()
    assert r.headers["cache-control"] == "no-cache"


def test_the_classes_the_categories_and_no_colour(vc) -> None:  # type: ignore[no-untyped-def]
    mid, m = turned(vc)
    grid, p, s, w = m["grid"], m["p"], m["s"], m["w"]
    passed = m["OK"] > 0.5

    def want(klass: Any, shell_mode: bool = True, **meta: Any) -> Any:
        v = view(grid, p, s, w, passed, klass, shell=shell_mode, kind=meta.pop("kind", "mean"))
        raw, _origin = layer_encode(grid, v, mode="shell" if shell_mode else "all", rows=len(p),
                                    passed=int(passed.sum()), **meta)  # fmt: skip
        return binary.decode(raw)

    br = [0.5, 1.0, 2.0]
    got = decoded(shell(vc, mid, restrict=COND, colour={"field": "AU", "kind": "classes", "breaks": br}))
    same_faces(got, want(break_class(m["AU"], br), field="AU", colour={"kind": "classes", "breaks": br}, classes=4,
                         bins=break_bins(br), labels=None))  # fmt: skip
    assert got[0]["bins"][0] == [None, 0.5] and got[0]["bins"][-1] == [2.0, None]

    codes = np.where(m["ROCK"] < 0, NO_CLASS, m["ROCK"]).astype(np.uint16)
    got = decoded(shell(vc, mid, restrict=COND, colour={"field": "rock", "kind": "categories", "hidden": []}))
    same_faces(got, want(codes, kind="mode", field="ROCK", colour={"kind": "categories", "hidden": []}, classes=5,
                         bins=None, labels=ROCKS))  # fmt: skip
    assert got[0]["labels"] == ROCKS and got[0]["bins"] is None and (got[1]["k"] == NO_CLASS).any()

    got = decoded(shell(vc, mid, restrict=COND, mode="all"))
    same_faces(got, want(None, False, field=None, colour={"kind": "none"}, classes=0, bins=None, labels=None))
    assert got[0]["classes"] == 0 and got[0]["field"] is None and (got[1]["k"] == 0).all()


def test_hidden_categories_leave_the_view(vc) -> None:  # type: ignore[no-untyped-def]
    mid, m = plain(vc)
    c = {"field": "ROCK", "kind": "categories"}
    head, arr = decoded(shell(vc, mid, mode="all", colour=c))
    h2, a2 = decoded(shell(vc, mid, mode="all", colour={**c, "hidden": [1, 3]}))
    shown = int(np.isin(m["ROCK"], [0, 2]).sum())
    assert head["passed"] == len(m["p"]) and h2["passed"] == shown and h2["empty"] == len(m["p"]) - shown
    assert h2["colour"] == {"kind": "categories", "hidden": [1, 3]}
    assert set(np.unique(a2["k"])) <= {0, 2} and h2["faces"] < head["faces"]
    assert h2["classes"] == 5 and head["classes"] == 5  # the labels are five, whatever is hidden


def test_the_filter_of_the_library_and_the_quick_restrictor_together(vc) -> None:  # type: ignore[no-untyped-def]
    mid, m = plain(vc)
    made = vc.post(f"{API}/filters", json={"name": "Хорошие", "rules": COND})
    assert made.status_code == 200, made.text
    fid = made.json()["id"]
    both = shell(vc, mid, mode="all", filter_id=fid, restrict={"cond": {"field": "AU", "op": ">", "value": 2}})
    head = decoded(both)[0]
    assert head["passed"] == int(((m["OK"] > 0.5) & (m["AU"] > 2)).sum())  # the product of the shares: AND
    only = decoded(shell(vc, mid, mode="all", filter_id=fid))[0]
    assert only["passed"] == int((m["OK"] > 0.5).sum()) and only["empty"] == len(m["p"]) - only["passed"]
    nothing = decoded(shell(vc, mid, mode="all", restrict={"cond": {"field": "AU", "op": ">", "value": 99}}))
    assert nothing[0]["faces"] == 0 and nothing[0]["passed"] == 0 and nothing[0]["origin"] == [0.0, 0.0, 0.0]
    assert nothing[1]["c"].size == 0 and nothing[1]["k"].size == 0


def test_a_missing_value_is_no_class_not_zero(vc) -> None:  # type: ignore[no-untyped-def]
    mid, m = plain(vc)
    head, arr = decoded(shell(vc, mid, mode="all", colour={"field": "AU", "kind": "scale"}))
    assert (arr["k"] == NO_CLASS).any()  # the block without a value (AU[3] is NaN)
    assert head["colour"]["from"] == pytest.approx(float(np.nanmin(m["AU"]))) and head["colour"]["log"] is False
    assert head["colour"]["to"] == pytest.approx(float(np.nanmax(m["AU"])))
    assert len(arr["a"]) <= 6 * len(m["p"])  # all blocks: up to six faces each, coplanar ones of a class merge


def test_above_the_limit_the_view_is_simplified(vc) -> None:  # type: ignore[no-untyped-def]
    n = 46  # all blocks: 6·46³ = 584 k faces over the least limit of 500 k
    grid = GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": [n, n, n], "size": [1.0, 1.0, 1.0], "rotation": 0.0,
                      "order": "F"})  # fmt: skip
    p = np.arange(n**3, dtype=np.uint64)
    au = (np.arange(n**3) % 17).astype(np.float64)
    mid = put(vc, grid, p, {"AU": au, "OK": np.ones(n**3), "ROCK": (np.arange(n**3) % 3).astype(np.int32)})
    head, arr = decoded(shell(vc, mid, mode="all", limit=500_000, colour={"field": "AU", "kind": "scale"}))
    assert head["simplified"] is True and head["lod"] >= 1 and head["faces_raw"] == 6 * n**3
    assert head["faces"] <= 500_000 < head["faces_raw"]
    big = decoded(shell(vc, mid, mode="all", limit=10_000_000, colour={"field": "AU", "kind": "scale"}))[0]
    assert big["simplified"] is False and big["lod"] == 0 and big["faces_raw"] == 6 * n**3
    cat = decoded(shell(vc, mid, mode="all", limit=500_000, colour={"field": "ROCK", "kind": "categories"}))[0]
    assert cat["simplified"] is True  # the mode of the categories


def test_the_extent_keeps_the_blocks_whose_centres_are_inside(vc) -> None:  # type: ignore[no-untyped-def]
    mid, m = plain(vc)
    box = [1000.0, 2000.0, 100.0, 1039.9, 2059.9, 119.9]  # 4 × 6 × 4 parents
    head = decoded(shell(vc, mid, mode="all", extent=box))[0]
    c = geometry.centres(m["grid"], m["p"], None, None)
    inside = ((c >= np.array(box[:3])) & (c <= np.array(box[3:]))).all(axis=1)
    assert head["passed"] == int(inside.sum()) < len(m["p"])


# --- the cache of the columns, the answer kept ----------------------------------------------------


def test_a_second_request_reads_nothing_and_the_same_one_comes_from_the_cache(vc) -> None:  # type: ignore[no-untyped-def]
    mid, _m = turned(vc)
    body = {"mode": "shell", "restrict": COND, "colour": {"field": "AU", "kind": "scale", "from": 0.1, "to": 10}}
    first = shell(vc, mid, **body)
    reads = bc.MODELS.reads
    assert reads >= 1 and bc.MODELS.size > 0
    again = shell(vc, mid, **body)
    assert again.content == first.content and vd.RESPONSES.hits == 1  # the answer kept
    other = shell(vc, mid, **{**body, "colour": {"field": "AU", "kind": "scale", "from": 0.2, "to": 5}})
    assert other.status_code == 200 and other.content != first.content
    assert bc.MODELS.reads == reads  # another range of the same field: the columns are in memory
    sw = shell(vc, mid, mode="all", restrict=COND, colour={"field": "ROCK", "kind": "categories"})
    assert sw.status_code == 200 and bc.MODELS.reads == reads + 1  # one more field: one pass over the file
    # the channel and the number of the request are not a part of the key
    ch = shell(vc, mid, **body, channel="scene:1", seq=7)
    assert ch.content == first.content and vd.RESPONSES.hits == 2


def test_a_model_with_sub_blocks_is_read_once_and_comes_back_from_the_disk(vc, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(bc, "DISK_MIN_ROWS", 1)
    mid, _m = turned(vc)
    body = {"mode": "shell", "restrict": COND, "colour": {"field": "AU", "kind": "scale", "from": 0.1, "to": 10}}
    reads = bc.MODELS.reads
    first = shell(vc, mid, **body)
    assert bc.MODELS.reads == reads + 1  # idx, OK, AU, s and w: one pass over the file
    keys = {k[3] for k in bc.MODELS.keys()}
    assert {"\0idx", "\0s", "\0w", "AU", "OK"} <= keys
    bc.MODELS.clear()
    vd.RESPONSES.clear()
    loads = bc.MODELS.loads
    again = shell(vc, mid, **body)
    assert again.content == first.content
    assert bc.MODELS.reads == reads + 1 and bc.MODELS.loads == loads + 5  # all five from `cache/blocks`


# --- the overtaking of requests -------------------------------------------------------------------


def test_a_newer_request_of_the_channel_cancels_the_older_one() -> None:
    g = sp.Overtake()
    a = g.enter("scene:1", 1)
    b = g.enter("scene:1", 2)
    other = g.enter("scene:2", 0)
    assert a.cancelled and not b.cancelled and not other.cancelled
    with pytest.raises(Exception) as late:
        g.enter("scene:1", 1)  # an older number than the running one: refused at once
    assert late.value.status == 409 and late.value.code == "superseded"  # type: ignore[attr-defined]
    with pytest.raises(Exception) as e:
        a.check()
    assert e.value.status == 409 and "устарел" in e.value.message  # type: ignore[attr-defined]
    g.leave(a)
    assert g._live["scene:1"] is b  # the cancelled one does not remove the live one
    g.leave(b)
    g.leave(other)
    assert not g._live
    assert g.enter(None, 0).channel is None and not g._live  # no channel — no overtaking


def test_an_overtaken_request_answers_409_and_the_newer_one_is_served(vc) -> None:  # type: ignore[no-untyped-def]
    from bf_core.stats.routes_charts import SLOTS

    mid, _m = plain(vc)
    results: dict[int, Any] = {}

    def go(seq: int) -> None:
        results[seq] = shell(vc, mid, mode="all", channel="scene:9", seq=seq)

    for _ in range(vd_slots()):
        SLOTS.acquire()  # both HEAVY slots busy: the requests wait for one
    try:
        t1 = threading.Thread(target=go, args=(1,))
        t1.start()
        deadline = time.time() + 10
        while "scene:9" not in sp.GATE._live and time.time() < deadline:
            time.sleep(0.01)
        t2 = threading.Thread(target=go, args=(2,))
        t2.start()
        t1.join(10)
        assert not t1.is_alive()  # the older one stops waiting at once
        refused(results[1], 409, "superseded")
        late = shell(vc, mid, mode="all", channel="scene:9", seq=1)
        refused(late, 409, "superseded")  # an older number than the running one
    finally:
        for _ in range(vd_slots()):
            SLOTS.release()
    t2.join(30)
    assert results[2].status_code == 200 and binary.decode(results[2].content)[0]["kind"] == "blocks"
    assert not sp.GATE._live


def vd_slots() -> int:
    from bf_core.stats.routes_charts import HEAVY

    return int(HEAVY)


# --- the slice ------------------------------------------------------------------------------------


def slice_body(origin: Any, normal: Any, **extra: Any) -> dict[str, Any]:
    return {"plane": {"origin": list(origin), "normal": list(normal)}, **extra}


def test_the_slice_is_the_raster_of_the_kernel(vc) -> None:  # type: ignore[no-untyped-def]
    mid, m = turned(vc)
    grid, p, s, w = m["grid"], m["p"], m["s"], m["w"]
    passed = m["OK"] > 0.5
    c = grid.to_project(np.array([[25.0, 20.0, 7.5]]))[0]
    for normal in ((0, 0, 1), (1, 1, 1), (-1, 0, 0)):
        body = slice_body(c, normal, restrict=COND, field="AU", max_px=512)
        head, arr = decoded(vc.post(f"{VIZ}/{mid}/slice", json=body))
        pl = sl.plane_of(grid, c, normal)
        want = sl.slice_rows(grid, pl, p, s, w, passed, m["AU"], "number", sub=True, max_px=512)
        assert np.array_equal(arr["value"], want.value, equal_nan=True) and np.array_equal(arr["m"], want.m)
        assert (head["kind"], head["format"], head["model"], head["version"]) == ("blocks_slice", 1, mid, 1)
        assert (head["field"], head["field_kind"], head["labels"], head["raster"]) == ("AU", "number", None,
                                                                                         want.layout.kind)  # fmt: skip
        assert head["width"] * head["height"] == arr["m"].size and head["passed"] == want.passed
        assert set(head["frame"]) == {"origin", "u", "v", "du", "dv"} and head["plane"]["normal"] == [
            float(x) / float(np.linalg.norm(normal)) for x in normal]  # fmt: skip
        assert (arr["m"] == 2).any() or normal != (0, 0, 1)  # a block without a value (NaN) is `m` = 2


def test_the_slice_of_categories_hidden_and_without_a_field(vc) -> None:  # type: ignore[no-untyped-def]
    mid, m = plain(vc)
    c = [1035.0, 2025.0, 112.0]  # inside layer k = 2
    head, arr = decoded(vc.post(f"{VIZ}/{mid}/slice", json=slice_body(c, (0, 0, 1), field="ROCK")))
    assert head["field_kind"] == "category" and head["labels"] == ROCKS and head["raster"] == "cells"
    assert (head["width"], head["height"]) == (8, 6)
    k = 2
    expect = np.full(8 * 6, -1.0)
    miss = np.ones(8 * 6, dtype=bool)
    for row, q in enumerate(m["p"]):
        if int(q) // 48 == k:
            expect[int(q) % 48] = m["ROCK"][row]
            miss[int(q) % 48] = False
    assert np.array_equal(np.where(arr["m"] == 0, -1.0, arr["value"]), expect)
    assert (arr["m"][miss] == 0).all() and (arr["m"][~miss] == 1).all()
    h2, a2 = decoded(vc.post(f"{VIZ}/{mid}/slice", json=slice_body(c, (0, 0, 1), field="ROCK", hidden=[0, 1])))
    assert (a2["m"] == 0).sum() > (arr["m"] == 0).sum() and not np.isin(a2["value"][a2["m"] == 1], [0, 1]).any()
    h3, a3 = decoded(vc.post(f"{VIZ}/{mid}/slice", json=slice_body(c, (0, 0, 1))))
    assert h3["field"] is None and h3["field_kind"] is None and np.isnan(a3["value"]).all()
    assert set(np.unique(a3["m"])) == {0, 1}  # no field: every block is `m` = 1, no value
    off = decoded(vc.post(f"{VIZ}/{mid}/slice", json=slice_body([1035.0, 2025.0, 5000.0], (0, 0, 1))))
    assert off[0]["width"] == 0 and off[1]["m"].size == 0  # the plane misses the model


def test_the_polygons_of_a_section(vc) -> None:  # type: ignore[no-untyped-def]
    mid, m = plain(vc)
    c = [1035.0, 2025.0, 112.0]
    r = vc.post(f"{API}/blocks/{mid}/slice/polygons", json=slice_body(c, (0, 0, 1), field="ROCK"))
    assert r.status_code == 200, r.text
    out = r.json()
    layer = [q for q in m["p"] if int(q) // 48 == 2]
    assert out["count"] == len(out["polygons"]) == len(layer) and out["truncated"] is False
    assert out["area"] == pytest.approx(len(layer) * 100.0, rel=1e-12)
    first = out["polygons"][0]
    assert len(first["ring"]) == 4 and first["value"] in ROCKS and all(len(v) == 3 for v in first["ring"])
    assert all(v[2] == pytest.approx(112.0) for v in first["ring"])
    few = vc.post(f"{API}/blocks/{mid}/slice/polygons", json=slice_body(c, (0, 0, 1), limit=5)).json()
    assert len(few["polygons"]) == 5 and few["truncated"] is True and few["count"] == len(layer)
    assert few["area"] == pytest.approx(out["area"])  # the area is that of the whole section


# --- the point ------------------------------------------------------------------------------------


def test_the_point_on_a_face_gives_the_block_that_owns_it(vc) -> None:  # type: ignore[no-untyped-def]
    mid, m = plain(vc)
    head, arr = decoded(shell(vc, mid, mode="shell"))
    o = np.array(head["origin"])
    seen = 0
    for i in range(0, len(arr["a"]), max(1, len(arr["a"]) // 40)):
        a = int(arr["a"][i])
        axis, side = a >> 1, a & 1
        local = arr["c"].reshape(-1, 3)[i].astype(np.float64) + o  # the centre of the face, local axes
        pt = local + np.array(m["grid"].corner)
        normal = np.zeros(3)
        normal[axis] = 1.0 if side else -1.0  # the normal of the face points out of the block
        body = {"point": pt.tolist(), "normal": normal.tolist(), "fields": ["AU", "ROCK"]}
        got = vc.post(f"{VIZ}/{mid}/at", json=body).json()
        assert got["found"] is True, (i, got)
        ijk = got["ijk"]
        # the block lies behind the face: its far edge along the axis is the plane of the face when side = 1
        edge = (ijk[axis] + (1 if side else 0)) * [10.0, 10.0, 5.0][axis]
        assert edge == pytest.approx(local[axis], abs=1e-3)
        row = int(np.flatnonzero(m["p"] == got["p"])[0])
        assert got["row"] == row and got["sub"] is None and got["passed"] is True and got["f"] == 1.0
        want = m["AU"][row]
        assert got["values"]["AU"] == (None if np.isnan(want) else pytest.approx(want))
        assert got["values"]["ROCK"] == ROCKS[m["ROCK"][row]]
        assert got["size"] == [10.0, 10.0, 5.0] and len(got["centre"]) == 3
        seen += 1
    assert seen >= 20


def test_a_missing_value_is_null_and_no_block_is_not_found(vc) -> None:  # type: ignore[no-untyped-def]
    mid, m = plain(vc)
    q = int(m["p"][3])  # AU[3] is NaN
    i, j, k = q % 8, (q // 8) % 6, q // 48
    pt = [1000.0 + 10 * i + 5, 2000.0 + 10 * j + 5, 100.0 + 5 * k + 2.5]
    got = vc.post(f"{VIZ}/{mid}/at", json={"point": pt, "fields": ["AU"]}).json()
    assert got["found"] is True and got["values"] == {"AU": None}
    every = vc.post(f"{VIZ}/{mid}/at", json={"point": pt}).json()
    assert set(every["values"]) == {"AU", "OK", "ROCK"}  # null — all the fields
    gap = int([q for q in range(192) if q % 13 == 5][0])  # a parent that is not in the file
    gi, gj, gk = gap % 8, (gap // 8) % 6, gap // 48
    far = [1000.0 + 10 * gi + 5, 2000.0 + 10 * gj + 5, 100.0 + 5 * gk + 2.5]
    assert vc.post(f"{VIZ}/{mid}/at", json={"point": far}).json() == {"found": False}
    assert vc.post(f"{VIZ}/{mid}/at", json={"point": [0, 0, 0]}).json() == {"found": False}  # outside the grid


def test_the_point_is_the_same_with_and_without_the_cache_and_knows_the_filter(vc) -> None:  # type: ignore[no-untyped-def]
    mid, m = turned(vc)
    grid = m["grid"]
    pt = grid.to_project(np.array([[33.3, 21.7, 6.1]]))[0].tolist()
    body = {"point": pt, "restrict": COND}
    reads = bc.MODELS.reads
    cold = vc.post(f"{VIZ}/{mid}/at", json=body).json()  # nothing in the cache: the row group is read
    assert cold["found"] is True and bc.MODELS.reads == reads and bc.MODELS.size == 0
    shell(vc, mid, restrict=COND, colour={"field": "AU", "kind": "scale"})
    warm = vc.post(f"{VIZ}/{mid}/at", json=body).json()  # the columns are in the cache
    assert warm == cold
    assert cold["sub"] is not None and len(cold["sub"]) == 6 and cold["passed"] == (cold["values"]["OK"] > 0.5)
    assert cold["f"] == (1.0 if cold["passed"] else 0.0)
    wrong = {"point": pt, "restrict": {"cond": {"field": "AU", "op": ">", "value": 1e9}}}
    no = vc.post(f"{VIZ}/{mid}/at", json=wrong).json()
    assert no["found"] is True and no["passed"] is False and no["f"] == 0.0  # found, but not shown


# --- errors ---------------------------------------------------------------------------------------


def test_errors_are_in_russian_with_a_hint(vc) -> None:  # type: ignore[no-untyped-def]
    mid, _m = plain(vc)

    def bad(body: Any, status: int, code: str, param: str | None = None, path: str = "shell") -> None:
        refused(vc.post(f"{VIZ}/{mid}/{path}", json=body), status, code, param)

    bad([], 422, "bad_request")
    bad({"mode": "cut"}, 422, "bad_param", "mode")
    bad({"limit": 10}, 422, "bad_param", "limit")
    bad({"limit": 11_000_000}, 422, "bad_param", "limit")
    bad({"extent": [1, 2, 3]}, 422, "bad_param", "extent")
    bad({"filter_id": "1"}, 422, "bad_param", "filter_id")
    bad({"restrict": []}, 422, "bad_param", "restrict")
    bad({"channel": ""}, 422, "bad_param", "channel")
    bad({"seq": -1, "channel": "a"}, 422, "bad_param", "seq")
    bad({"colour": {"kind": "wave"}}, 422, "bad_param", "colour.kind")
    bad({"colour": {"kind": "scale"}}, 422, "bad_param", "colour.field")
    bad({"colour": {"kind": "scale", "field": "AU", "from": 5, "to": 1}}, 422, "bad_param", "colour.to")
    bad({"colour": {"kind": "scale", "field": "AU", "from": 0, "to": 1, "log": True}}, 422, "bad_param", "colour.from")
    bad({"colour": {"kind": "classes", "field": "AU", "breaks": [2, 1]}}, 422, "bad_param", "colour.breaks")
    bad({"colour": {"kind": "classes", "field": "AU"}}, 422, "bad_param", "colour.breaks")
    bad({"colour": {"kind": "scale", "field": "NOPE"}}, 404, "block_field_not_found", "colour.field")
    bad({"colour": {"kind": "scale", "field": "ROCK"}}, 422, "bad_param", "colour.kind")  # a category is no scale
    bad({"colour": {"kind": "categories", "field": "AU"}}, 422, "bad_param", "colour.kind")
    bad({"filter_id": 99999}, 404, "filter_not_found")
    bad({"restrict": {"cond": {"field": "ZZZ", "op": ">", "value": 1}}}, 422, "filter_field_missing")
    bad({}, 422, "bad_param", "plane", "slice")
    bad(slice_body([0, 0, 0], [0, 0, 0]), 422, "bad_param", "plane.normal", "slice")
    bad(slice_body([0, 0], [0, 0, 1]), 422, "bad_param", "plane.origin", "slice")
    bad(slice_body([0, 0, 0], [0, 0, 1], max_px=10), 422, "bad_param", "max_px", "slice")
    bad(slice_body([0, 0, 0], [0, 0, 1], field="NOPE"), 404, "block_field_not_found", "field", "slice")
    refused(vc.post(f"{VIZ}/{mid}/at", json={"point": [1, 2]}), 422, "bad_param", "point")
    refused(vc.post(f"{VIZ}/{mid}/at", json={"point": [1, 2, 3], "fields": ["NOPE"]}), 404, "block_field_not_found")
    refused(vc.post(f"{VIZ}/{mid}/at", json={}), 422, "bad_param", "point")
    refused(vc.post(f"{API}/blocks/{mid}/slice/polygons", json=slice_body([0, 0, 0], [0, 0, 1], limit=0)), 422,
            "bad_param", "limit")  # fmt: skip
    for path in ("shell", "slice", "at"):
        r = vc.post(f"{VIZ}/99999/{path}", json={"point": [0, 0, 0], **slice_body([0, 0, 0], [0, 0, 1])})
        refused(r, 404, "block_model_not_found")


def test_a_filter_on_a_field_the_model_lacks_is_refused(vc) -> None:  # type: ignore[no-untyped-def]
    mid, _m = plain(vc, "Без")
    made = vc.post(f"{API}/filters", json={"name": "Медь", "rules": {"cond": {"field": "CU", "op": ">", "value": 1}}})
    e = refused(shell(vc, mid, filter_id=made.json()["id"]), 422, "filter_field_missing")
    assert e["field"] == "CU" and e["model"] == "Без"


# --- the answers the interface is read against ----------------------------------------------------


def answers(c: Any) -> dict[str, Any]:
    """The requests and the real answers of the routes on a turned model with sub-blocks and a plain one."""
    cases: list[dict[str, Any]] = []

    def binary_case(name: str, path: str, body: dict[str, Any]) -> None:
        r = c.post(f"{API}{path}", json=body)
        assert r.status_code == 200, r.text
        head, _arrays = binary.decode(r.content)
        cases.append({"name": name, "path": path, "request": body, "status": 200, "header": head,
                      "bfa1": base64.b64encode(r.content).decode("ascii")})  # fmt: skip

    def json_case(name: str, path: str, body: dict[str, Any], status: int = 200) -> None:
        r = c.post(f"{API}{path}", json=body)
        assert r.status_code == status, r.text
        cases.append({"name": name, "path": path, "request": body, "status": status, "json": r.json()})

    t, m = turned(c)
    pl, _q = plain(c)
    grid = m["grid"]
    centre = grid.to_project(np.array([[25.0, 20.0, 7.5]]))[0].tolist()
    scale = {"field": "AU", "kind": "scale", "from": 0.1, "to": 10, "log": True}
    binary_case("shell_scale_turned", f"/viz/blocks/{t}/shell", {"mode": "shell", "restrict": COND, "colour": scale,
                                                               "channel": "scene:1", "seq": 1})  # fmt: skip
    binary_case("shell_classes", f"/viz/blocks/{t}/shell",
                {"mode": "shell", "colour": {"field": "AU", "kind": "classes", "breaks": [0.5, 1, 2]}})  # fmt: skip
    binary_case("shell_categories_hidden", f"/viz/blocks/{t}/shell",
                {"mode": "shell", "colour": {"field": "ROCK", "kind": "categories", "hidden": [1]}})  # fmt: skip
    binary_case("shell_all_none", f"/viz/blocks/{pl}/shell", {"mode": "all"})
    binary_case("shell_scale_default_range", f"/viz/blocks/{pl}/shell",
                {"mode": "shell", "colour": {"field": "AU", "kind": "scale"}})  # fmt: skip
    binary_case("slice_z", f"/viz/blocks/{t}/slice", slice_body(centre, (0, 0, 1), field="AU", restrict=COND,
                                                              max_px=256))  # fmt: skip
    binary_case("slice_oblique", f"/viz/blocks/{t}/slice", slice_body(centre, (1, 1, 1), field="AU", max_px=256))
    binary_case("slice_categories", f"/viz/blocks/{pl}/slice",
                slice_body([1035.0, 2025.0, 112.0], (0, 0, 1), field="ROCK", hidden=[1]))  # fmt: skip
    json_case("at_found", f"/viz/blocks/{pl}/at", {"point": [1035.0, 2025.0, 112.0], "fields": ["AU", "ROCK"]})
    json_case("at_not_found", f"/viz/blocks/{pl}/at", {"point": [0, 0, 0]})
    json_case("at_turned", f"/viz/blocks/{t}/at", {"point": centre, "normal": [0, 0, 1], "restrict": COND})
    json_case("polygons", f"/blocks/{pl}/slice/polygons", slice_body([1035.0, 2025.0, 112.0], (0, 0, 1),
                                                                    field="ROCK", limit=1000))  # fmt: skip
    json_case("bad_param", f"/viz/blocks/{pl}/shell", {"colour": {"kind": "scale"}}, 422)
    return {"note": "BF_REGEN_BLOCKS_VIZ=1 uv run pytest -q tests/test_blocks_viz.py", "cases": cases}


def test_the_answers_kept_for_the_interface_are_the_answers_of_the_routes(vc) -> None:  # type: ignore[no-untyped-def]
    fresh = answers(vc)
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(
            json.dumps(fresh, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8"
        )
        return
    if not DATA.exists():
        pytest.fail("нет tests/data/blocks/viz_answers.json: BF_REGEN_BLOCKS_VIZ=1, см. модуль")
    kept = json.loads(DATA.read_text(encoding="utf-8"))
    assert [c["name"] for c in kept["cases"]] == [c["name"] for c in fresh["cases"]]
    for a, b in zip(kept["cases"], fresh["cases"], strict=True):
        assert a["request"] == b["request"] and a["status"] == b["status"], a["name"]
        assert (a.get("bfa1") is None) == (b.get("bfa1") is None), a["name"]
        if a.get("bfa1") is not None:  # the bytes the interface was read against (or the same to the last float32)
            same_bfa1(a["bfa1"], b["bfa1"], a["name"])
        golden.assert_same(a.get("json"), b.get("json"), f"({a['name']})")


def test_the_faces_of_the_fixture_of_the_layer_have_the_form_of_the_route(vc) -> None:  # type: ignore[no-untyped-def]
    """The shapes of the header of the layer fixture (17.30) are those of the answer of the route: the same keys."""
    mid, _m = turned(vc)
    head = decoded(shell(vc, mid, mode="shell", restrict=COND, colour={"field": "AU", "kind": "scale", "from": 0.1,
                                                                      "to": 10, "log": True}))[0]  # fmt: skip
    case = json.loads(LAYER.read_text(encoding="utf-8"))["cases"][0]
    want = binary.decode(base64.b64decode(case["bfa1"]))[0]
    assert set(head) - set(want) == {"warnings"} and set(want) <= set(head)
    scale = scale_class(np.array([0.1, 10.0]), 0.1, 10.0, log=True)
    assert scale.tolist() == [0, 255] and len(scale_bins(0.1, 10.0, log=True)) == 256
