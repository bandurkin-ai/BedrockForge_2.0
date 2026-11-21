"""The API of swath plots (ADR 0019 §5, §10; подшаг 13.18): `GET /stats/swath/sources` and `POST /stats/swath
{settings | swath_id}` answer what the kernels give on the demo set and model and on a model of
`tools/gen/blockmodel.py`; the 13th series and 2000+ swaths are refused (Т7.30-1); samples «inside the blocks»
leave out those outside the cells taken (Т7.30-2); a saved swath is recomputed after an edit (Т7.30-5); an
azimuth of 90° is X bit for bit (ТД01-5); a series whose source is gone carries `error`; concurrent requests
read a model once; the columns of a model come from Parquet in a path with Cyrillic (`-m windows`)."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bf_core.blocks import store as bm
from bf_core.db import dialect
from bf_core.projects.service import projects_of
from bf_core.stats import blocks_cache as bc
from bf_core.stats import routes_charts, routes_swath, source
from bf_core.stats import swath as sw
from bf_core.stats import swath_blocks as sb
from bf_core.stats import swath_frame as sf
from bf_core.stats.select import SampleFilter, select_sample
from bf_core.storage.blobs import BlobStore
from tests.test_import_job import API

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gen.blockmodel import generate as gen_bm  # noqa: E402

S = f"{API}/stats"


@pytest.fixture()
def swath_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before the core starts: the stats router has these routes (until `stats/router.py` includes them)."""
    from bf_core.stats import router as stats_router

    paths = {getattr(r, "path", "") for r in stats_router.router.routes}
    if "/stats/swath" not in paths:
        monkeypatch.setattr(stats_router.router, "routes", list(stats_router.router.routes))
        stats_router.router.include_router(routes_swath.router)


@pytest.fixture()
def demo(swath_routes, pclient):  # type: ignore[no-untyped-def]
    c = pclient
    assert c.post(f"{API}/projects/demo").status_code == 200
    bc.MODELS.clear()
    src = c.get(f"{S}/swath/sources").json()
    return c, src["sets"][0]["id"], src["models"][0]["id"]


def blocks(model_id: int | None, **kw: Any) -> dict[str, Any]:
    return {"kind": "blocks", "model_id": model_id, "field": "AU", "weight": "volume", "density": None,
            "filter": None, "color": "#1f6bff", "line": True, "bars": True, **kw}  # fmt: skip


def samples(set_id: int | None, **kw: Any) -> dict[str, Any]:
    return {"kind": "samples", "set_id": set_id, "table": None, "field": "AU", "weight": "length",
            "weight_field": None, "negatives": "keep", "top_cut": None, "within": "box", "within_series": None,
            "color": "#e4572e", "line": True, "bars": False, **kw}  # fmt: skip


def settings(*series: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return {"v": 1, "direction": "x", "azimuth": 0, "dip": 0, "roll": 0, "width": None, "anchor": None,
            "limits": None, "series": list(series), "range": None, "view": {"table": True}, **kw}  # fmt: skip


def post(c: Any, body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    r = c.post(f"{S}/swath", json=body)
    assert r.status_code == status, r.text
    out: dict[str, Any] = r.json()
    return out


def test_sources(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id, model_id = demo
    src = c.get(f"{S}/swath/sources").json()
    assert src["sets"] == [{"id": set_id, "name": "Скважины 2025",
                            "tables": [{"key": "пробы", "name": "Пробы", "kind": "assay", "rows": 4036}]}]  # fmt: skip
    assert src["models"] == [{
        "id": model_id, "name": "Блочная модель Au", "numbers": [{"name": "AU", "unit": "г/т"}],
        "categories": [{"name": "DOMAIN", "labels": ["руда", "порода", "зона разлома"], "more": False}],
        "block": [10.0, 10.0, 10.0], "density": None, "grades": ["AU"],
    }]  # fmt: skip
    info = {"id": 1, "name": "М", "grid": {"size": [5, 5, 2]}, "fields": [
        {"name": "Cu", "kind": "number", "unit": "%"}, {"name": "Плотность", "kind": "number", "unit": "т/м³"},
        {"name": "ZONE", "kind": "category", "labels": [f"z{i}" for i in range(301)]},
    ]}  # fmt: skip
    m = routes_swath.model_source(info)
    assert m["density"] == "Плотность" and m["grades"] == ["Cu"] and m["block"] == [5.0, 5.0, 2.0]
    assert len(m["categories"][0]["labels"]) == 300 and m["categories"][0]["more"] is True


def test_swath_is_the_kernels(demo) -> None:  # type: ignore[no-untyped-def]
    """A model series, samples in the cuboid and samples inside the ore blocks (Т7.30-2) — the kernels on the
    same columns; the answer has the shape of the client (`ui/src/modules/swath/api.ts`)."""
    c, set_id, model_id = demo
    ore = {"field": "DOMAIN", "labels": ["руда"]}
    body = settings(blocks(model_id), samples(set_id), blocks(model_id, weight="none", filter=ore),
                    samples(set_id, within="blocks", within_series=2), samples(set_id, within="blocks"))  # fmt: skip
    got = post(c, {"settings": body})
    assert set(got) == {"direction", "frame", "box", "limits", "custom_limits", "start", "width", "count", "centres",
                        "block", "series", "notes"}  # fmt: skip
    project = projects_of(c.core).require_current()
    with dialect.read_tx(project.engine) as conn:
        idx, cols = bm.read_cells(conn, BlobStore(project.folder), model_id)
        grid = bm.block_model_info(conn, model_id)["grid"]
    f = sf.direction_frame("x")
    keep = sb.keep_of(["руда", "порода", "зона разлома"], ["руда"])
    ext = [sb.block_bounds(f, grid, idx), sb.block_bounds(f, grid, idx, codes=cols["DOMAIN"], keep=keep)]
    cub = sw.cuboid(f, models=ext, block=10.0)
    assert got["frame"] == f.as_dict() and {k: got[k] for k in cub.as_dict()} == cub.as_dict()
    assert got["count"] == 40 and got["width"] == 10.0 and got["direction"] == "x" and got["notes"] == []
    want = [
        sb.block_series(grid, idx, cols["AU"], f, cub),
        sb.block_series(grid, idx, cols["AU"], f, cub, weight="none", codes=cols["DOMAIN"], keep=keep),
    ]
    sc = source.read(project, set_id, None, ["AU", "length"])
    sel = select_sample(sc.number("AU"), SampleFilter(), length=sc.number("length"))
    pts = source.midpoints(project, sc)
    ore_cells = sw.cells_of(grid, sb.selected_idx(idx, cols["DOMAIN"], keep))
    box, inside = sw.sample_series(pts, sel, f, cub), sw.sample_series(pts, sel, f, cub, cells=ore_cells)
    every = sw.sample_series(pts, sel, f, cub, cells=sw.cells_of(grid, idx))
    s = got["series"]
    for x, w in zip(s, [want[0], box, want[1], inside, every], strict=True):
        d = routes_charts._safe(w.as_dict())
        assert x["mean"] == d["mean"] and x["count"] == d["count"] and x["counts"] == d["counts"], x["index"]
        assert x["weights"] == [v if n else None for v, n in zip(d["weight"], d["count"], strict=True)]
        assert x["total"]["mean"] == d["total"]["mean"] and x["error"] is None
    assert [x["within"] for x in s if x["kind"] == "samples"] == ["box", 2, 0]  # null — the first model series
    assert s[3]["counts"]["outside_blocks"] > 0 and s[3]["counts"]["used"] < s[1]["counts"]["used"]  # Т7.30-2
    assert s[4]["counts"]["used"] == s[1]["counts"]["used"]  # every cell of the demo model is occupied
    dev = sw.deviation(box.mean, want[0].mean)
    assert s[1]["deviation"] == [float(v) if np.isfinite(v) else None for v in dev] and "deviation" not in s[0]
    assert s[0]["name"] == "Блочная модель Au · AU" and s[1]["name"] == "Скважины 2025 · AU"
    assert (s[0]["weight_name"], s[0]["weight_unit"], s[0]["unit"]) == ("объём", "м³", "г/т")
    assert (s[1]["weight_name"], s[1]["weight_unit"], s[1]["source_id"]) == ("длина", "м", set_id)
    assert s[2]["weight_name"] == "число блоков" and s[3]["mean"][0] is None  # an empty swath — null, never 0


def test_azimuth_90_is_x_bit_for_bit(demo) -> None:  # type: ignore[no-untyped-def]
    """ТД01-5 through the API: the angles (90; 0; 0) give the swaths of X."""
    c, set_id, model_id = demo
    series = (blocks(model_id), samples(set_id, within="blocks"))
    x = post(c, {"settings": settings(*series)})
    a = post(c, {"settings": settings(*series, direction="angles", azimuth=90.0)})
    assert a.pop("direction") == "angles" and x.pop("direction") == "x" and a == x
    turned = post(c, {"settings": settings(*series, direction="angles", azimuth=30.0, dip=-20.0, roll=10.0)})
    assert turned["frame"]["azimuth"] == 30.0 and turned["series"][0]["total"]["count"] == 29442


def test_refusals(demo) -> None:  # type: ignore[no-untyped-def]
    """Т7.30-1: the 13th series and more than 2000 swaths are refused with their `param`; the other refusals."""
    c, set_id, model_id = demo

    def fail(body: dict[str, Any], status: int = 422) -> dict[str, Any]:
        out = post(c, body, status)
        assert out["message"] and out["hint"]
        return out

    out = fail({"settings": settings(*[samples(set_id)] * 13)})
    assert out["code"] == "too_many_series" and out["param"] == "series"
    out = fail({"settings": settings(blocks(model_id), width=0.1)})
    assert out["code"] == "too_many_swaths" and out["param"] == "width" and "2000" in out["message"]
    assert post(c, {"settings": settings(blocks(model_id), width=0.2)})["count"] == 2000
    assert fail({"settings": settings()})["param"] == "series"
    assert fail({"settings": settings(blocks(model_id), direction="q")})["param"] == "direction"
    assert fail({"settings": settings(blocks(model_id), direction="angles", dip=95)})["param"] == "dip"
    assert fail({"settings": settings(blocks(model_id), width="5")})["param"] == "width"
    assert fail({"settings": settings(blocks(model_id), limits={"u": [1, 0]})})["param"] == "limits"
    assert fail({"settings": settings(blocks(model_id), v=2)})["param"] == "v"
    assert fail({"settings": settings({"kind": "cells"})})["param"] == "series[0].kind"
    assert fail({"settings": settings(blocks(model_id, weight="mass"))})["param"] == "series[0].weight"
    assert fail({"settings": settings(blocks(model_id, weight="tonnage"))})["param"] == "series[0].density"
    assert fail({"settings": settings(samples(set_id, weight="tonnage"))})["param"] == "series[0].weight"
    assert fail({"settings": settings(samples(set_id, weight="field"))})["param"] == "series[0].weight_field"
    assert fail({"settings": settings(samples(set_id, within="blocks"))})["param"] == "series[0].within_series"
    bad = settings(blocks(model_id), samples(set_id, within="blocks", within_series=1))
    assert fail({"settings": bad})["param"] == "series[1].within_series"
    assert fail({"settings": settings(blocks(10**9))}) ["code"] == "no_data"  # fmt: skip
    assert fail({})["param"] == "settings"
    assert fail({"swath_id": "1"})["param"] == "swath_id"
    assert fail({"swath_id": 10**9}, 404)["code"] == "chart_not_found"


def test_a_series_without_its_source_carries_an_error(demo) -> None:  # type: ignore[no-untyped-def]
    """The source of a series is gone (its id null after an import, a deleted set or model, a field renamed):
    the series carries `error`, the others are computed."""
    c, set_id, model_id = demo
    body = settings(blocks(model_id), blocks(None), samples(None), samples(10**9), blocks(10**9),
                    blocks(model_id, field="CU"), samples(set_id, table="Литология"), samples(set_id, field="ZN"),
                    blocks(model_id, filter={"field": "ZONE", "labels": ["a"]}))  # fmt: skip
    s = post(c, {"settings": body})["series"]
    assert s[0]["error"] is None and s[0]["total"]["count"] > 0
    codes = [x["error"]["code"] for x in s[1:]]
    assert codes == ["source_deleted"] * 4 + ["field_not_found", "table_not_found", "field_not_found",
                                              "field_not_found"]  # fmt: skip
    for x in s[1:]:
        assert x["error"]["message"] and x["error"]["hint"] and x["mean"] == [None] * 40 and x["count"] == [0] * 40
        assert x["total"] == {"mean": None, "count": 0, "weight": None, "deviation": None}
    got = post(c, {"settings": settings(blocks(None), samples(set_id, within="blocks"), samples(set_id))})
    inside = got["series"]
    assert inside[1]["error"]["code"] == "within_not_computed" and inside[1]["within"] == 0
    assert (
        inside[2]["error"] is None and inside[2]["deviation"] == [None] * got["count"]
    )  # no first series to compare to
    alone = post(c, {"settings": settings(blocks(None), samples(set_id, within="blocks"))}, 422)
    assert alone["code"] == "no_data" and "удалён" in alone["hint"]  # nothing to build the cuboid around


def test_saved_swath_follows_an_edit(demo) -> None:  # type: ignore[no-untyped-def]
    """Т7.30-5: after an edit of a sample the saved swath is recomputed (by `swath_id` and `/stats/chart/{id}`)."""
    c, set_id, model_id = demo
    body = settings(samples(set_id), samples(set_id, within="blocks", within_series=2), blocks(model_id))
    r = c.post(f"{API}/charts", json={"set_id": None, "kind": "swath", "name": "Swath plot · AU", "settings": body,
                                      "auto_name": True})  # fmt: skip
    assert r.status_code == 200, r.text
    chart = r.json()
    first = post(c, {"swath_id": chart["id"]})
    assert first.pop("swath") == {"id": chart["id"], "name": chart["name"], "version": 1}
    assert first == post(c, {"settings": body})
    saved = c.post(f"{S}/chart/{chart['id']}")
    assert saved.status_code == 200, saved.text
    assert saved.json()["chart"]["kind"] == "swath" and saved.json()["series"] == first["series"]

    # a sample inside the cuboid of the model (in its blocks: every cell of the demo model is occupied)
    project = projects_of(c.core).require_current()
    cols = source.read(project, set_id, None, ["AU"])
    lim = np.array([first["limits"][a] for a in "uvw"])
    row_id = int(cols.row_ids[np.flatnonzero(sw.inside(source.midpoints(project, cols), sf.frame(90.0), lim))[0]])
    tabs = c.get(f"{API}/drillholes/sets/{set_id}/tables").json()["tables"]
    key = next(t["table"] for t in tabs if t["name"] == "Пробы")
    columns = c.get(f"{API}/drillholes/sets/{set_id}/tables/{key}?limit=1").json()["columns"]
    au = next(col["key"] for col in columns if col["name"] == "AU")
    r = c.patch(f"{API}/drillholes/sets/{set_id}/tables/{key}/rows/{row_id}", json={"column": au, "value": 500.0})
    assert r.status_code == 200, r.text
    again = post(c, {"swath_id": chart["id"]})
    assert (
        again.pop("swath")["id"] == chart["id"] and again["series"][2]["mean"] == first["series"][2]["mean"]
    )  # the model
    for k in (0, 1):
        assert again["series"][k]["total"]["mean"] > first["series"][k]["total"]["mean"]
        assert max(m or 0 for m in again["series"][k]["mean"]) > max(m or 0 for m in first["series"][k]["mean"])
    assert c.post(f"{API}/charts", json={"set_id": set_id, "kind": "histogram", "name": "Г",
                                         "settings": {"v": 1, "set_id": set_id}}).status_code == 200  # fmt: skip
    hist = next(x for x in c.get(f"{API}/charts?set_id={set_id}&kind=histogram").json()["charts"])
    assert post(c, {"swath_id": hist["id"]}, 422)["param"] == "swath_id"


def test_concurrent_requests_read_a_model_once(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id, model_id = demo
    project = projects_of(c.core).require_current()
    body = routes_charts.checked("swath", settings(blocks(model_id), blocks(model_id, filter={"field": "DOMAIN",
                                 "labels": ["руда"]}), samples(set_id, within="blocks")))  # fmt: skip
    bc.MODELS.clear()
    reads, out = bc.MODELS.reads, []
    gate = threading.Barrier(6)

    def one() -> None:
        gate.wait()
        out.append(routes_charts.run(lambda: routes_swath.swath_of(project, body)))

    threads = [threading.Thread(target=one) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(out) == 6 and all(x == out[0] for x in out)
    assert bc.MODELS.reads - reads == 1  # idx, AU and DOMAIN in one pass over the file
    assert sorted(k[3] for k in bc.MODELS.keys()) == sorted([bc.IDX, "AU", "DOMAIN"])


def test_the_cache_of_columns() -> None:
    """≤ the limit by bytes, LRU; a new file of a model and another project drop the older columns."""
    cache = bc.ModelCache(limit=3000)
    a = np.zeros(100)  # 800 B
    cache.put(("p", 1, "s1", "AU"), a)
    cache.put(("p", 1, "s1", "CU"), a)
    cache.put(("p", 2, "t1", "AU"), a)
    assert cache.size == 2400 and cache.peek(("p", 1, "s1", "AU")) is a
    cache.put(("p", 2, "t1", "CU"), a)  # over the limit: the least recently used goes
    assert cache.peek(("p", 1, "s1", "CU")) is None and cache.size == 2400
    cache.put(("p", 1, "s2", "AU"), a)  # a new file of model 1
    assert {k[:3] for k in cache.keys()} == {("p", 2, "t1"), ("p", 1, "s2")}
    cache.put(("q", 5, "u", "AU"), np.zeros(10))  # another project
    assert cache.keys() == [("q", 5, "u", "AU")] and cache.size == 80
    cache.put(("q", 5, "u", "BIG"), np.zeros(1000))  # bigger than the limit: not kept
    assert cache.keys() == [("q", 5, "u", "AU")]


# --- a model of tools/gen/blockmodel.py ---------------------------------------------------------------------


def generated(c: Any, tmp_path: Path, n: tuple[int, int, int], name: str) -> tuple[int, Any]:
    """The model of `tools/gen/blockmodel.py` (centres in Morton order) as a regular model of the project, with
    a category «ZONE» (ore above 0.5 g/t)."""
    import pyarrow.parquet as pq

    size = (10.0, 10.0, 5.0)
    path = tmp_path / "модель.parquet"
    gen_bm(path, *n, size=size)
    t = pq.read_table(path)
    corner = [500_000.0, 7_000_000.0, -200.0]
    ijk = [np.floor((t.column(a).to_numpy() - corner[k]) / size[k]).astype(np.int64) for k, a in
           enumerate(("XC", "YC", "ZC"))]  # fmt: skip
    idx = ijk[0] + n[0] * (ijk[1] + n[1] * ijk[2])
    order = np.argsort(idx)
    au, rho = t.column("AU").to_numpy()[order], t.column("DENSITY").to_numpy()[order]
    grid = {"corner": corner, "counts": list(n), "size": list(size)}
    fields = [{"name": "AU", "kind": "number", "unit": "г/т"}, {"name": "DENSITY", "kind": "number", "unit": "т/м³"},
              {"name": "ZONE", "kind": "category", "labels": ["руда", "порода"]}]  # fmt: skip
    project = projects_of(c.core).require_current()
    with dialect.write_tx(project.engine) as conn:
        values = {"AU": au, "DENSITY": rho, "ZONE": np.where(au >= 0.5, 0, 1)}
        bm_id = bm.put_block_model(conn, BlobStore(project.folder), grid, fields, idx[order], values, name=name)
    return bm_id, project


@pytest.mark.windows
def test_model_columns_from_parquet_in_a_cyrillic_path(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """The columns of a model read from its Parquet file under «Проекты» are those of the file (`idx` uint32),
    and the swath of the tonnage of its ore is that of the kernels."""
    c, _set_id, _ = demo
    bm_id, project = generated(c, tmp_path, (60, 50, 40), "Модель руды")
    with dialect.read_tx(project.engine) as conn:
        m = bc.model_of(conn, bm_id)
        idx, cols = bm.read_cells(conn, BlobStore(project.folder), bm_id)
    path = str(BlobStore(project.folder).path_of(m.sha))
    assert "Проекты" in path and m.cells == 120_000
    got = bc.columns(project, m, ["AU", "ZONE", "DENSITY"])
    assert got.idx.dtype == np.uint32 and (got.idx == idx).all() and not got.idx.flags.writeable
    assert got.number("AU").tobytes() == cols["AU"].tobytes() and (got.codes("ZONE") == cols["ZONE"]).all()
    ore = {"field": "ZONE", "labels": ["руда"]}
    out = post(c, {"settings": settings(blocks(bm_id, weight="tonnage", density="DENSITY", filter=ore),
                                        direction="z", azimuth=30.0)})  # fmt: skip
    f, keep = sf.direction_frame("z", 30.0), np.array([True, False])
    grid = m.grid
    cub = sw.cuboid(f, models=[sb.block_bounds(f, grid, idx, codes=cols["ZONE"], keep=keep)], block=5.0)
    want = sb.block_series(grid, idx, cols["AU"], f, cub, weight="tonnage", density=cols["DENSITY"],
                           codes=cols["ZONE"], keep=keep)  # fmt: skip
    s = out["series"][0]
    assert s["mean"] == routes_charts._safe(want.as_dict())["mean"] and s["weight_name"] == "тоннаж"
    assert out["block"] == 5.0 and out["count"] == 40  # along Z: the blocks of 5 m


def test_two_million_cells_first_and_again(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """The point of measure of 13.18: 2 million cells — the first swath (the file read) and again from the cache."""
    c, set_id, _ = demo
    bm_id, _project = generated(c, tmp_path, (200, 100, 100), "Модель 2 млн")
    body = {"settings": settings(blocks(bm_id), blocks(bm_id, weight="none", filter={"field": "ZONE",
                                 "labels": ["руда"]}), direction="angles", azimuth=35.0, dip=-10.0)}  # fmt: skip
    t0 = time.perf_counter()
    first = post(c, body)
    t1 = time.perf_counter()
    again = post(c, body)
    t2 = time.perf_counter()
    print(f"\n2 млн ячеек: первое построение {t1 - t0:.2f} с, повтор {t2 - t1:.3f} с")
    assert first == again and first["series"][0]["total"]["count"] == 2_000_000
    assert t2 - t1 < 1.0  # the criterion is 0.3 s on an idle machine (the report)


def test_routes_import_nothing_heavy() -> None:
    code = (
        "import sys; import bf_core.stats.routes_swath, bf_core.stats.blocks_cache;"
        "heavy=[m for m in ('numba','numpy','pandas','pyarrow','duckdb','scipy') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"imported with the routes: {r.stdout.strip()}"
