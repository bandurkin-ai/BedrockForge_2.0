"""«Каркас по серии» — the operation `stitch_series` through the job `geometry.operation` (ТЗ 8.1.4, Т8.1-4;
ADR 0016 §8, решение 5; 10.9): `ops/run_series.py`, `ops/registry.py`, `ops/schema.py`.

With real `spawn` workers: Т8.1-4 — squares 20 × 20 m on the 5 sections of a series Δ = 25 m apart at UTM
coordinates: 50 000 m³ with the default caps (extend by Δ/2, flat), 40 000 m³ with flat caps, 1e−9 relative;
`meta.built` names the series (number, version) and the layer; two bodies — two wireframes «<серия> — тело N», or
one «одним каркасом»; a failed check makes nothing and names the sections; «Перестроить» after an edit of a contour
gives the new volume. In this process: the refusals of the precheck, the given links, the names in the messages.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.formats.lineset import from_lines
from bf_core.jobs.context import JobError
from bf_core.lines import store as ls_store
from bf_core.lines import versions
from bf_core.lines.assemble import Edit
from bf_core.ops import provenance
from bf_core.ops.run_series import Namer
from bf_core.ops.schema import OPERATIONS
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.opsutil import API, ok, refused
from tests.test_import_job import wait_job
from tests.test_ops_job import checked, numba_cache, ops_env, run_here  # noqa: F401 - fixtures

REL = 1e-9
P1 = (512_000.0, 6_789_000.0, 0.0)
SERIES = {"type": "vertical", "p1": list(P1), "az": 90, "s0": 0, "s1": 100, "step": 25, "towards": 12.5, "away": 12.5}
NAMES = ["6789000N", "6789025N", "6789050N", "6789075N", "6789100N"]


def square(k: int, u0: float = 0.0, w0: float = 100.0, side: float = 20.0, t: float = 0.0) -> np.ndarray:
    """A square on the plane of section k (y = 6 789 000 + 25k): u along x, w = Z."""
    uw = np.array([[u0, w0], [u0 + side, w0], [u0 + side, w0 + side], [u0, w0 + side]], dtype=np.float64)
    return np.column_stack([P1[0] + uw[:, 0], np.full(4, P1[1] + 25.0 * k + t), uw[:, 1]])


@pytest.fixture()
def series_ops(ops_env, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811 - the fixture of test_ops_job
    import bf_core.api.app as app_module

    if "sections" not in app_module.ROUTER_MODULES:
        monkeypatch.setattr(app_module, "ROUTER_MODULES", (*app_module.ROUTER_MODULES, "sections"))


def put_layer(c: Any, rings: list[np.ndarray], name: str = "Контуры") -> int:
    project = projects_of(c.core).require_current()
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        file_id = ls_store.create_file(conn, store, name=f"{name}.str", meta={"format": "micromine_str"})
        return ls_store.put_layer(conn, store, from_lines(name, rings, closed=[True] * len(rings)), file_id=file_id)


def make_series(c: Any, layer_id: int | None, **more: Any) -> dict[str, Any]:
    return ok(c.post(f"{API}/section-series", json={"params": {**SERIES, "contour_layer": layer_id, **more}}))  # type: ignore[no-any-return]


@pytest.fixture()
def sc(series_ops, pclient):  # type: ignore[no-untyped-def]
    ok(pclient.post(f"{API}/projects", json={"name": "Каркас по серии"}))
    project = projects_of(pclient.core).require_current()
    layer = put_layer(pclient, [square(k) for k in range(5)])
    series = make_series(pclient, layer)
    return pclient, project, layer, series


def run(c: Any, url: str, body: dict[str, Any] | None = None, status: str = "done") -> dict[str, Any]:
    started = ok(c.post(url, json=body or {}))
    job: dict[str, Any] = wait_job(c, started["source"], started["id"], timeout=180)
    assert job["status"] == status, job
    job["started"] = started
    return job


def wf(c: Any, wf_id: int) -> dict[str, Any]:
    return ok(c.get(f"{API}/wireframes/{wf_id}"))  # type: ignore[no-any-return]


def names(c: Any) -> list[str]:
    return [w["name"] for w in ok(c.get(f"{API}/wireframes"))["wireframes"]]


# --- real workers -------------------------------------------------------------------------------------------


def test_t81_4_through_the_job(sc) -> None:  # type: ignore[no-untyped-def]
    c, _project, layer, series = sc
    job = run(c, f"{API}/operations", {"op": "stitch_series", "params": {"series_id": series["id"]}})
    assert job["kind"] == "geometry.operation" and job["queue"] == "quick"
    (wf_id,) = job["result"]["wireframe_ids"]
    w = wf(c, wf_id)
    assert w["name"] == "Серия 1 — тело 1" and w["stats"]["closed"] and w["stats"]["orientation"] == "outward"
    assert w["stats"]["volume"] == pytest.approx(50_000.0, rel=REL)
    built = w["built"]
    assert built["op"] == "stitch_series" and built["title"] == "Каркас по серии"
    assert built["params"] == {"series_id": series["id"], "layer_ids": [layer], "links": None, "start_cap": "extend",
                               "end_cap": "extend", "cap_distance": 0.5, "single": False}  # fmt: skip
    assert [(s["role"], s["kind"], s["id"], s["version"]) for s in built["sources"]] == [
        ("series", "section_series", series["id"], 1), ("layers[0]", "line_layer", layer, 1),
    ]  # fmt: skip
    assert built["sources"][0]["number"] == 1 and built["sources"][0]["name"] == "Серия 1"
    report = built["report"]
    assert report["contours"] == 5 and report["links"] == 4 and report["auto_links"] is True
    assert report["bodies"][0]["sections"] == [NAMES[0], NAMES[4]] and report["bodies"][0]["contours"] == 5
    flat = run(c, f"{API}/operations", {"op": "stitch_series", "params": {
        "series_id": series["id"], "start_cap": "flat", "end_cap": "flat"}, "name": "Тело"})  # fmt: skip
    w = wf(c, flat["result"]["wireframe_ids"][0])
    assert w["name"] == "Тело" and w["stats"]["volume"] == pytest.approx(40_000.0, rel=REL)


def test_bodies_single_and_a_failure(sc) -> None:  # type: ignore[no-untyped-def]
    c, project, _layer, _series = sc
    rings = [square(k) for k in range(5)] + [square(k, u0=100.0) for k in range(1, 4)]
    two = put_layer(c, rings, "Две жилы")
    s = make_series(c, two, s1=100)
    job = run(c, f"{API}/operations", {"op": "stitch_series", "params": {"series_id": s["id"]}})
    ids = job["result"]["wireframe_ids"]
    got = [(wf(c, i)["name"], wf(c, i)["stats"]["volume"]) for i in ids]
    assert [g[0] for g in got] == ["Серия 2 — тело 1", "Серия 2 — тело 2"]
    assert got[0][1] == pytest.approx(50_000.0, rel=REL) and got[1][1] == pytest.approx(30_000.0, rel=REL)
    one = run(c, f"{API}/operations", {"op": "stitch_series", "params": {"series_id": s["id"], "single": True}})
    (only,) = one["result"]["wireframe_ids"]
    w = wf(c, only)
    assert w["name"] == "Серия 2" and w["stats"]["volume"] == pytest.approx(80_000.0, rel=REL)
    # a failed check: a bow tie on the middle section — the strips cross; nothing is made, the sections are named
    tie = square(2)
    tie[1:, 2] = [120.0, 100.0, 130.0]
    tie[2, 0] = tie[1, 0]  # (0; 100), (20; 120), (20; 100), (0; 130): the first and the third edges cross
    bad = put_layer(c, [square(1), tie, square(3)], "Бантик")
    s3 = make_series(c, bad)
    before = names(c)
    job = run(c, f"{API}/operations", {"op": "stitch_series", "params": {"series_id": s3["id"]}}, status="failed")
    e = job["error"]
    assert e["code"] == "stitch_self_intersection" and e["hint"], e
    assert e["message"].startswith("Между контурами разрезов 6789025N и 6789050N получаются самопересечения"), e
    assert "(контуры 1 («Бантик») и 2 («Бантик»))" in e["message"], e
    assert names(c) == before
    tmp = BlobStore(project.folder).tmp_dir
    assert not tmp.exists() or not list(tmp.iterdir())


def test_rebuild_after_an_edit_of_a_contour(sc) -> None:  # type: ignore[no-untyped-def]
    c, project, layer, series = sc
    old_id = run(c, f"{API}/operations", {"op": "stitch_series", "params": {"series_id": series["id"]}})[
        "result"]["wireframe_ids"][0]  # fmt: skip
    old = wf(c, old_id)
    bigger = square(4, side=20.0)
    bigger[2:, 2] += 20.0  # the last contour 20 × 40
    with dialect.read_tx(project.engine) as conn:
        base = int(versions.layer_head(conn, layer)["rev"])
    edit = Edit(np.arange(5), [0, 0, 0, 0, 1], [1] * 5, [5] * 5, np.vstack([bigger, bigger[:1]]))
    versions.save(project.engine, BlobStore(project.folder), layer, edit, base_version=base, note="контур")
    job = run(c, f"{API}/wireframes/{old_id}/rebuild")
    new = wf(c, job["result"]["wireframe_ids"][0])
    # the last strip is a frustum-like prism 20 × 20 → 20 × 40 over 25 m (30 × 20 × 25), the cap half of 20 × 40
    expected = 400.0 * (12.5 + 75.0) + 600.0 * 25.0 + 800.0 * 12.5
    assert new["id"] != old_id and new["stats"]["volume"] == pytest.approx(expected, rel=REL)
    assert new["built"]["rebuild_of"] == old_id and new["built"]["sources"][1]["version"] == 2
    assert wf(c, old_id) == old


# --- in this process ----------------------------------------------------------------------------------------


def test_refusals_of_the_precheck(sc) -> None:  # type: ignore[no-untyped-def]
    c, project, layer, series = sc
    check = f"{API}/operations/check"
    s = ok(c.post(check, json={"op": "stitch_series", "params": {"series_id": series["id"]}}))
    assert s["queue"] == "quick" and [x["role"] for x in s["sources"]] == ["series", "layers[0]"]
    none = make_series(c, None)
    e = refused(c.post(check, json={"op": "stitch_series", "params": {"series_id": none["id"]}}), 422, "no_contours")
    assert "«Серия 2»" in e["message"]
    poly = ok(c.post(f"{API}/section-series", json={"params": {
        "type": "polyline", "polyline": [[0, 0], [100, 0], [100, 100]], "p1": [0, 0, 0], "contour_layer": layer}}))
    refused(c.post(check, json={"op": "stitch_series", "params": {"series_id": poly["id"]}}), 422, "series_polyline")
    refused(c.post(check, json={"op": "stitch_series", "params": {"series_id": 999_999}}), 422, "source_missing")
    refused(c.post(check, json={"op": "stitch_series", "params": {"series_id": layer}}), 422, "wrong_kind")
    link = [{"layer_id": layer, "line": 0}, {"layer_id": layer, "line": 9}]
    e = refused(c.post(check, json={"op": "stitch_series", "params": {"series_id": series["id"], "links": [link]}}),
                422, "line_missing")  # fmt: skip
    assert "линии 10" in e["message"] and e["lines"] == [9]
    other = [{"layer_id": layer, "line": 0}, {"layer_id": layer + 1000, "line": 1}]
    refused(c.post(check, json={"op": "stitch_series", "params": {"series_id": series["id"], "links": [other]}}),
            422, "bad_links")  # fmt: skip
    assert OPERATIONS["stitch_series"]["fields"][0]["type"] == "series"


def test_given_links_and_names_in_messages(sc) -> None:  # type: ignore[no-untyped-def]
    c, project, layer, series = sc
    sid = series["id"]
    ends = [[{"layer_id": layer, "line": a}, {"layer_id": layer, "line": a + 1}] for a in range(4)]
    out, _ctx = run_here(project, "stitch_series", {"series_id": sid, "links": ends[:2] + ends[3:]})
    vols = [wf(c, i)["stats"]["volume"] for i in out["wireframe_ids"]]
    assert vols == [pytest.approx(30_000.0, rel=REL), pytest.approx(20_000.0, rel=REL)]  # 0–1–2 and 3–4
    assert out["report"]["auto_links"] is False and out["report"]["links"] == 3
    with pytest.raises(JobError) as info:  # sections 0 and 2 are not neighbours
        run_here(project, "stitch_series", {"series_id": sid, "links": [[ends[0][0], ends[1][1]]]})
    err = info.value
    assert err.code == "stitch_links" and err.hint
    assert err.message.startswith("Связь 1: линия 1 слоя «Контуры» (разрез 6789000N) и линия 3 слоя «Контуры» "
                                  "(разрез 6789050N) лежат не на соседних разрезах")  # fmt: skip
    flat = square(1)
    flat[:, 0] = P1[0] + np.array([0.0, 10.0, 20.0, 5.0])
    flat[:, 2] = 100.0 + 0.5 * (flat[:, 0] - P1[0])  # on the line w = 100 + u/2: zero area
    s = make_series(c, put_layer(c, [square(0), flat], "Плоский"))
    with pytest.raises(JobError) as info:
        run_here(project, "stitch_series", {"series_id": s["id"]})
    assert info.value.code == "degenerate_polygon"
    assert info.value.message.startswith("Разрез 6789025N, линия 2 слоя «Плоский»: контур вырожден")
    namer = Namer(NAMES, ["12 («Контуры»)", "15 («Контуры»)", "3 («А»)"])
    text = ("Между контурами разрезов 2 и 3 получаются самопересечения: поставьте тай-линии (контуры 1, 2 и 3). "
            "Контур 2 на разрезе 4 не связан; Разрезы 1 и 5 идут не по порядку.")  # fmt: skip
    assert namer.text(text) == (
        "Между контурами разрезов 6789025N и 6789050N получаются самопересечения: поставьте тай-линии (контуры "
        "12 («Контуры»), 15 («Контуры») и 3 («А»)). Контур 15 («Контуры») на разрезе 6789075N не связан; "
        "Разрезы 6789000N и 6789100N идут не по порядку."
    )


def test_off_plane_and_skipped_lines(sc) -> None:  # type: ignore[no-untyped-def]
    c, project, _layer, _series = sc
    rings = [square(0), square(1, t=0.003), square(2), square(3, u0=500.0, t=20.0)]
    s = make_series(c, put_layer(c, rings, "Наклон"))
    out, ctx = run_here(project, "stitch_series", {"series_id": s["id"]})
    assert out["report"]["skipped"] == {"open": 0, "short": 0, "outside": 0}  # t = 20 is section 4 (k = 3 + 1)
    warned = [text for level, text in ctx.logs if level == "warn"]
    assert any(w.startswith("Разрез 6789025N, линия 2 слоя «Наклон»: вершины до 0,003 м") for w in warned), warned
    assert len(out["wireframe_ids"]) == 2  # 0–1–2 and the lone square of section 4 (500 m away)
    lone = wf(c, out["wireframe_ids"][1])
    assert lone["stats"]["volume"] == pytest.approx(400.0 * 25.0, rel=REL)  # a slab of Δ: extend by Δ/2 each way


def test_bfggis_renumbers_the_series_and_the_layers_of_a_record() -> None:
    link = [{"layer_id": 3, "line": 0}, {"layer_id": 4, "line": 7}]
    built = provenance.record(
        op="stitch_series", title="Каркас по серии", params={"series_id": 2, "layer_ids": [3, 4], "links": [link]},
        sources=[{"role": "series", "kind": "section_series", "id": 2, "name": "Серия 1", "number": 1}], report={},
    )  # fmt: skip
    out = provenance.remap_built(built, {2: 20, 3: 30}.get)
    assert out["params"] == {"series_id": 20, "layer_ids": [30, None],
                             "links": [[{"layer_id": 30, "line": 0}, {"layer_id": None, "line": 7}]]}  # fmt: skip
    assert out["sources"][0]["id"] == 20 and out["sources"][0]["number"] == 1
