"""The grade–tonnage report of a block model (подшаг 17.28; ТЗ 7.31, 8.7.5; ADR 0023 §10, decision 15).

The tests of BF (a sub-blocked model of 4 m parents: tonnes, grades, metal, a density constant, a category filter,
groups), the cut-off 0 = the full tonnage, `SG` found by itself and the constant `DENSITY` skipped, `*_CUT`, FRAC and
the share f of the restrictor, gaps (never a zero), the units of the metal, the curve, the CSV, the portions of any size
give the same bits, 422 and 404 in Russian, the job over HTTP."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest
from fastapi import APIRouter

from bf_core.api.errors import ApiError
from bf_core.blocks import report as rp
from bf_core.blocks import routes_report
from bf_core.blocks import store as bm
from bf_core.db import dialect
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import store as wf
from tests.test_archive import wait_job

API = "/api/v1"
BL = API + "/blocks"
OZ = rp.TROY_OUNCE_G


@pytest.fixture()
def rc(monkeypatch: pytest.MonkeyPatch, pclient):  # type: ignore[no-untyped-def]
    """A core with an empty project; the job and the route of the report are mounted until the lines of the
    orchestrator name them (`jobs/registry.py`, `blocks/router.py`)."""
    from bf_core.jobs import registry

    if "bf_core.blocks.report" not in registry.HANDLER_MODULES:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", "bf_core.blocks.report")
    registry.load_handlers()
    if not any(getattr(r, "path", "") == f"{BL}/{{bm_id}}/report" for r in pclient.app.routes):
        mount = APIRouter(prefix="/blocks")
        mount.include_router(routes_report.router)
        pclient.app.include_router(mount, prefix=API)
    assert pclient.post(f"{API}/projects", json={"name": "Отчёт"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.store = BlobStore(pclient.project.folder)
    return pclient


def put(c: Any, grid: dict[str, Any], fields: list[dict[str, Any]], p: np.ndarray, values: dict[str, np.ndarray],
        s: np.ndarray | None = None, w: np.ndarray | None = None, name: str = "Модель") -> int:  # fmt: skip
    with bm.CellsWriterV2(c.store, grid, fields, row_group=1000) as wr:
        wr.write(p, values, s, w)
        staged = wr.finish()
    try:
        with dialect.write_tx(c.project.engine) as conn:
            return bm.commit_block_model(conn, c.store, staged, name=name, source={"format": "dat", "file": "m.dat"})
    finally:
        staged.discard()


def run(c: Any, mid: int, chunk: int = 1 << 20, **body: Any) -> dict[str, Any]:
    return rp.run_report(c.project.engine, c.project.folder, {"model_id": mid, **body}, chunk=chunk)


def same(a: Any, b: Any) -> bool:
    """Bit for bit: the JSON of the two answers (a float is written by its shortest exact form)."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def rows_of(out: dict[str, Any], group: str | None = None) -> list[dict[str, Any]]:
    return [r for r in out["rows"] if r["group"] == group]


# --- a sub-blocked model of BF (4 m parents, 1 m sub-blocks) ----------------------------------------------------------

BF_GRID = {"corner": [0.0, 0.0, 0.0], "counts": [4, 3, 2], "size": [4.0, 4.0, 4.0], "div": [4, 4, 4]}
BF_FIELDS = [
    {"name": "AU_CUT", "dtype": "f8", "unit": "g/t", "role": "grade"},
    {"name": "CU_CUT", "dtype": "f8", "unit": "%", "role": "grade"},
    {"name": "AU", "dtype": "f8", "unit": "g/t"},
    {"name": "SG", "dtype": "f8", "role": "density"},
    {"name": "DENSITY", "dtype": "f8", "role": "density"},
    {"name": "ALT", "dtype": "cat", "labels": ["G", "H", "Руда окисл. ёЁ"]},
    {"name": "OXIDE", "dtype": "cat", "labels": ["Ox", "Sulf"]},
]


def bf_blocks(seed: int = 3) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(4):
        for j in range(3):
            for k in range(2):
                if (i + j + k) % 5 == 4:
                    continue  # an empty parent
                p = i + 4 * (j + 3 * k)
                kind = (i * 7 + j * 3 + k) % 4
                if kind == 0:
                    parts = [((0, 0, 0), (4, 4, 4))]
                elif kind == 1:
                    parts = [((0, 0, 0), (1, 4, 4)), ((1, 0, 0), (3, 4, 4))]
                elif kind == 2:
                    parts = [((x, 0, 0), (1, 4, 4)) for x in range(4)]
                else:
                    parts = [((0, 0, 0), (4, 4, 2)), ((0, 0, 2), (4, 4, 1)), ((0, 0, 3), (4, 4, 1))]
                rows += [(p, *s, *w) for s, w in parts]
    a = np.array(rows)
    order = np.lexsort((a[:, 1], a[:, 2], a[:, 3], a[:, 0]))  # P, then sz, sy, sx
    a = a[order]
    n = len(a)
    m: dict[str, Any] = {"p": a[:, 0].astype(np.uint64), "s": a[:, 1:4].astype(np.uint8),
                         "w": a[:, 4:7].astype(np.uint8), "n": n}  # fmt: skip
    m["vol"] = a[:, 4] * a[:, 5] * a[:, 6]
    m["AU_CUT"] = rng.lognormal(0.0, 0.8, n)
    m["CU_CUT"] = rng.random(n) * 2
    m["AU"] = m["AU_CUT"] * 1.5
    m["SG"] = rng.uniform(2.2, 3.1, n)
    m["SG"][1] = np.nan
    m["DENSITY"] = np.full(n, 2.6)
    m["ALT"] = rng.integers(0, 3, n).astype(np.int32)
    m["OXIDE"] = rng.integers(-1, 2, n).astype(np.int32)
    return m


def bf_model(c: Any, m: dict[str, Any]) -> int:
    cols = {k: m[k] for k in ("AU_CUT", "CU_CUT", "AU", "SG", "DENSITY", "ALT", "OXIDE")}
    return put(c, BF_GRID, BF_FIELDS, m["p"], cols, m["s"], m["w"])


def test_bf_report_tonnes_grades_metal_and_defaults(rc) -> None:  # type: ignore[no-untyped-def]
    m = bf_blocks()
    mid = bf_model(rc, m)
    out = run(rc, mid, cutoffs=[0, 0.5, 1])
    # the defaults of 7.31: *_CUT rather than AU, the varying SG rather than the constant DENSITY
    assert out["density"] == {"field": "SG", "value": None} and out["cutoff_field"] == "AU_CUT"
    assert list(out["rows"][0]["grades"]) == ["AU_CUT", "CU_CUT"]
    t = m["vol"] * m["SG"]
    assert out["excluded"] == {"filtered": 0, "no_volume": 0, "no_density": 1, "no_cutoff": 0}
    for row in rows_of(out):
        sel = (m["AU_CUT"] >= row["cutoff"]) & np.isfinite(t)
        assert row["blocks"] == int(sel.sum())
        assert row["tonnes"] == pytest.approx(t[sel].sum(), rel=1e-12)
        assert row["volume"] == pytest.approx(m["vol"][sel].sum(), rel=1e-12)
        au = row["grades"]["AU_CUT"]
        assert au["grade"] == pytest.approx((m["AU_CUT"][sel] * t[sel]).sum() / t[sel].sum(), rel=1e-12)
        assert au["metal"] == pytest.approx((m["AU_CUT"][sel] * t[sel]).sum() / OZ / 1000, rel=1e-12)
        assert (au["unit"], au["metal_unit"], au["missing_tonnes"]) == ("g/t", "тыс. унц", 0.0)
        cu = row["grades"]["CU_CUT"]
        assert cu["metal_unit"] == "т" and cu["metal"] == pytest.approx((m["CU_CUT"][sel] * t[sel]).sum() / 100)
    assert out["units"] == {"volume": "м³", "tonnes": "т"}


def test_the_cutoff_zero_is_the_full_tonnage(rc) -> None:  # type: ignore[no-untyped-def]
    m = bf_blocks()
    mid = bf_model(rc, m)
    t = m["vol"] * m["SG"]
    full = float(t[np.isfinite(t)].sum())
    out = run(rc, mid, cutoffs=[0, 1e9], curve=0)
    assert rows_of(out)[0]["tonnes"] == pytest.approx(full, rel=1e-13)
    assert rows_of(out)[1]["blocks"] == 0 and rows_of(out)[1]["grades"]["AU_CUT"]["grade"] is None
    assert rows_of(out)[1]["grades"]["AU_CUT"]["metal"] is None and out["curve"] is None


def test_a_constant_density_a_category_filter_and_groups(rc) -> None:  # type: ignore[no-untyped-def]
    m = bf_blocks()
    mid = bf_model(rc, m)
    out = run(rc, mid, density=2.7, grades=["AU_CUT"], categories={"field": "ALT", "values": ["G"]}, groups="OXIDE")
    sel = m["ALT"] == 0
    total = rows_of(out)[0]
    assert out["density"] == {"field": None, "value": 2.7}
    assert total["tonnes"] == pytest.approx(m["vol"][sel].sum() * 2.7, rel=1e-12)
    assert out["excluded"]["filtered"] == int((~sel).sum())
    parts = [r for r in out["rows"] if r["group"] is not None]
    assert sum(r["tonnes"] for r in parts) == pytest.approx(total["tonnes"], rel=1e-12)
    assert sum(r["blocks"] for r in parts) == total["blocks"]
    labels = {r["group"] for r in parts}
    assert labels <= {"Ox", "Sulf", rp.EMPTY_GROUP} and (m["OXIDE"][sel] < 0).any() == (rp.EMPTY_GROUP in labels)
    ox = next(r for r in parts if r["group"] == "Ox")
    assert ox["tonnes"] == pytest.approx(m["vol"][sel & (m["OXIDE"] == 0)].sum() * 2.7, rel=1e-12)
    with pytest.raises(ApiError) as e:  # a category is not a density
        run(rc, mid, grades=["AU_CUT"], density="ALT")
    assert e.value.status == 422 and e.value.extra == {"param": "density"}


def test_density_by_default_and_the_constant_one_is_skipped(rc) -> None:  # type: ignore[no-untyped-def]
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [2, 2, 1], "size": [2.0, 2.0, 2.0]}
    fields = [{"name": "AU_CUT", "dtype": "f8", "unit": "ppm"}, {"name": "DENSITY", "dtype": "f8", "role": "density"}]
    p = np.arange(4, dtype=np.uint64)
    mid = put(rc, grid, fields, p, {"AU_CUT": np.array([1.0, 2.0, 3.0, 4.0]), "DENSITY": np.full(4, 2.6)})
    with pytest.raises(ApiError) as e:
        run(rc, mid)
    assert e.value.code == "bad_param" and e.value.extra == {"param": "density"} and "плотност" in e.value.message
    named = run(rc, mid, density="DENSITY", cutoffs=[2], curve=0)  # named — it is used
    assert named["density"]["field"] == "DENSITY"
    assert rows_of(named)[0]["tonnes"] == pytest.approx(3 * 8.0 * 2.6)
    # without *_CUT the fields with the role «содержание» are the grades; the field with the role density, if it varies
    fields = [{"name": "AU", "dtype": "f8", "unit": "g/t", "role": "grade"},
              {"name": "D", "dtype": "f8", "role": "density"}]  # fmt: skip
    mid = put(rc, grid, fields, p, {"AU": np.array([1.0, 2.0, 3.0, 4.0]), "D": np.array([2.0, 2.5, 3.0, 2.0])},
              name="Роли")  # fmt: skip
    out = run(rc, mid, curve=0)
    assert out["density"]["field"] == "D" and list(rows_of(out)[0]["grades"]) == ["AU"]
    assert rows_of(out)[0]["tonnes"] == pytest.approx(8.0 * (2.0 + 2.5 + 3.0 + 2.0))


def test_frac_and_the_share_of_the_restrictor(rc) -> None:  # type: ignore[no-untyped-def]
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [4, 3, 2], "size": [5.0, 5.0, 2.5]}  # 24 blocks of 62.5 m³
    fields = [{"name": "AU_CUT", "dtype": "f8", "unit": "g/t"}, {"name": "SG", "dtype": "f8"},
              {"name": "FRAC", "dtype": "f8", "role": "frac"}]  # fmt: skip
    rng = np.random.default_rng(5)
    au, sg = rng.uniform(0.2, 3.0, 24), rng.uniform(2.0, 3.0, 24)
    frac = rng.uniform(0.3, 1.0, 24)
    frac[4] = np.nan  # an empty FRAC: outside the sum, counted
    mid = put(rc, grid, fields, np.arange(24, dtype=np.uint64), {"AU_CUT": au, "SG": sg, "FRAC": frac})
    out = run(rc, mid, curve=0)
    ok = np.isfinite(frac)
    assert out["excluded"]["no_volume"] == 1
    assert rows_of(out)[0]["tonnes"] == pytest.approx((62.5 * frac * sg)[ok].sum(), rel=1e-12)
    assert rows_of(out)[0]["volume"] == pytest.approx((62.5 * frac)[ok].sum(), rel=1e-12)
    # a surface at z = 1.25 by the share: «above» passes 0.5 of the lower layer and the whole upper one
    xyz = np.array([[-50.0, -50, 1.25], [100, -50, 1.25], [100, 100, 1.25], [-50, 100, 1.25]])
    with dialect.write_tx(rc.project.engine) as conn:
        oid = wf.put_wireframe(conn, rc.store, xyz, np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32), name="П")
    f = np.where(np.arange(24) < 12, 0.5, 1.0)
    cut = {"surface": {"id": oid, "side": "above", "by": "frac"}}
    out = run(rc, mid, restrict=cut, curve=0)
    assert rows_of(out)[0]["tonnes"] == pytest.approx((62.5 * frac * f * sg)[ok].sum(), rel=1e-12)
    assert rows_of(out)[0]["blocks"] == 23 and out["excluded"]["filtered"] == 0
    # a surface at z = 2.5: the lower layer has the share 0 — out (№ 65), counted as filtered
    top = np.array([[-50.0, -50, 2.5], [100, -50, 2.5], [100, 100, 2.5], [-50, 100, 2.5]])
    with dialect.write_tx(rc.project.engine) as conn:
        oid2 = wf.put_wireframe(conn, rc.store, top, np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32), name="В")
    out = run(rc, mid, restrict={"surface": {"id": oid2, "side": "above", "by": "frac"}}, curve=0)
    up = np.arange(24) >= 12
    assert out["excluded"]["filtered"] == 12
    assert rows_of(out)[0]["tonnes"] == pytest.approx((62.5 * frac * sg)[ok & up].sum(), rel=1e-12)


def test_a_gap_in_a_grade_is_not_a_zero(rc) -> None:  # type: ignore[no-untyped-def]
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [2, 2, 1], "size": [1.0, 1.0, 1.0]}
    fields = [{"name": "AU_CUT", "dtype": "f8", "unit": "g/t"}, {"name": "CU_CUT", "dtype": "f8", "unit": "%"},
              {"name": "NI_CUT", "dtype": "f8", "unit": "кг"}]  # fmt: skip
    nan = np.nan
    mid = put(rc, grid, fields, np.arange(4, dtype=np.uint64),
              {"AU_CUT": np.array([2.0, 4.0, nan, 1.0]), "CU_CUT": np.array([1.0, nan, 3.0, nan]),
               "NI_CUT": np.full(4, nan)}, name="Пропуски")  # fmt: skip
    out = run(rc, mid, density=2.0, cutoffs=[0, 3], cutoff_field="AU_CUT", curve=0)
    first, high = rows_of(out)
    assert out["excluded"]["no_cutoff"] == 1  # the block without AU_CUT is above no cut-off, 0 included
    assert (first["blocks"], first["tonnes"]) == (3, 6.0)
    cu = first["grades"]["CU_CUT"]
    assert cu["grade"] == 1.0 and cu["missing_tonnes"] == 4.0 and cu["missing_blocks"] == 2
    assert cu["metal"] == pytest.approx(2.0 * 1.0 / 100)
    ni = first["grades"]["NI_CUT"]  # all gaps: no grade, no metal, an unknown unit
    assert ni["grade"] is None and ni["metal"] is None and ni["metal_unit"] is None and ni["missing_tonnes"] == 6.0
    assert (high["blocks"], high["tonnes"], high["grades"]["AU_CUT"]["grade"]) == (1, 2.0, 4.0)
    assert high["grades"]["CU_CUT"]["grade"] is None  # the only block has no CU_CUT


def test_the_units_of_the_metal() -> None:
    assert rp.metal(1000.0, "g/t") == pytest.approx(1000.0 / OZ / 1000) == rp.metal(1000.0, " г/т ")
    assert rp.metal(1000.0, "ppm") == rp.metal(1000.0, "g/t")
    assert rp.metal(500.0, "%") == 5.0 and rp.metal(2e6, "ppb") == pytest.approx(2.0)
    assert rp.metal(1.0, "кг") is None and rp.metal(1.0, None) is None
    assert [rp.metal_unit(u) for u in ("g/t", "ppm", "%", "ppb", "x")] == ["тыс. унц", "тыс. унц", "т", "кг", None]


def test_cutoffs_in_any_order_and_the_edge_is_above(rc) -> None:  # type: ignore[no-untyped-def]
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [5, 1, 1], "size": [1.0, 1.0, 1.0]}
    fields = [{"name": "AU_CUT", "dtype": "f8", "unit": "g/t"}]
    mid = put(rc, grid, fields, np.arange(5, dtype=np.uint64), {"AU_CUT": np.array([0.5, 1.0, 1.5, 2.0, 2.5])},
              name="Борта")  # fmt: skip
    out = run(rc, mid, density=1.0, cutoffs=["1,5", "0.5", 1.0, 1.5, -1], curve=0)
    assert [r["cutoff"] for r in out["rows"]] == [1.5, 0.5, 1.0, -1.0]  # as asked, each once
    assert [r["blocks"] for r in out["rows"]] == [3, 5, 4, 5]  # «≥»: a block at the cut-off is above it


def test_the_curve_and_the_csv(rc) -> None:  # type: ignore[no-untyped-def]
    m = bf_blocks()
    mid = bf_model(rc, m)
    out = run(rc, mid, grades=["AU_CUT", "CU_CUT"], cutoffs=[0, 1], curve=20, csv=True, groups="OXIDE")
    cv = out["curve"]
    assert cv["field"] == "AU_CUT" and len(cv["cutoffs"]) == len(cv["tonnes"]) == len(cv["grade"]) == 20
    assert cv["cutoffs"][0] == m["AU_CUT"].min() and cv["cutoffs"][-1] == m["AU_CUT"].max()
    assert cv["tonnes"] == sorted(cv["tonnes"], reverse=True) and cv["metal_unit"] == "тыс. унц"
    t = np.where(np.isfinite(m["SG"]), m["vol"] * m["SG"], 0.0)
    assert cv["tonnes"][0] == pytest.approx(t.sum(), rel=1e-12)
    top = m["AU_CUT"] >= m["AU_CUT"].max()
    assert cv["tonnes"][-1] == pytest.approx(t[top].sum(), rel=1e-12)
    assert cv["grade"][-1] == pytest.approx(m["AU_CUT"].max()) or t[top].sum() == 0
    text = out["csv"]
    lines = text.split("\r\n")
    assert lines[0].split(";")[:5] == ["Группа", "Борт", "Блоков", "Объём, м³", "Тоннаж, т"]
    assert "AU_CUT, содержание (g/t)" in lines[0] and "CU_CUT, металл (т)" in lines[0]
    first = lines[1].split(";")
    assert first[0] == "" and first[1] == "0,0" and "." not in lines[1] and "," in first[3]
    assert len(lines) == 1 + len(out["rows"]) + 1 and lines[-1] == ""
    assert run(rc, mid, curve=0)["csv"] is None


def test_the_portions_of_any_size_give_the_same_bits(rc) -> None:  # type: ignore[no-untyped-def]
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [30, 20, 10], "size": [2.5, 2.5, 1.0]}
    fields = [{"name": "AU_CUT", "dtype": "f8", "unit": "g/t"}, {"name": "CU_CUT", "dtype": "f8", "unit": "%"},
              {"name": "SG", "dtype": "f8"}, {"name": "D", "dtype": "cat", "labels": ["a", "b", "c"]}]  # fmt: skip
    rng = np.random.default_rng(11)
    n = 4000
    p = np.sort(rng.choice(30 * 20 * 10, n, replace=False)).astype(np.uint64)
    au = rng.lognormal(0, 1.5, n) * 1e3  # a wide range: the order of the sums matters
    au[rng.random(n) < 0.05] = np.nan
    cu = rng.random(n)
    cu[rng.random(n) < 0.1] = np.nan
    sg = rng.uniform(2, 4, n)
    sg[rng.random(n) < 0.02] = np.nan
    cats = rng.integers(-1, 3, n).astype(np.int32)
    mid = put(rc, grid, fields, p, {"AU_CUT": au, "CU_CUT": cu, "SG": sg, "D": cats})
    body = {"cutoffs": [0, 500, 2000], "groups": "D", "curve": 40, "csv": True, "restrict": {"cond": {
        "field": "CU_CUT", "op": ">", "value": 0.2}}}  # fmt: skip
    base = run(rc, mid, **body)
    for chunk in (1, 7, 333, 1000, 4000, 1 << 20):
        assert same(base, run(rc, mid, chunk=chunk, **body)), chunk
    assert base["excluded"]["filtered"] > 0 and len(base["rows"]) > 3
    ok = np.isfinite(au) & np.isfinite(sg) & (cu > 0.2)
    assert rows_of(base)[0]["tonnes"] == pytest.approx((2.5 * 2.5 * sg)[ok].sum(), rel=1e-12)


# --- 422 and 404 in Russian, the job over HTTP -------------------------------------------------------------------


def asked(c: Any, mid: int, body: Any) -> Any:
    return c.post(f"{BL}/{mid}/report", json=body)


def test_the_job_over_http_returns_the_report(rc) -> None:  # type: ignore[no-untyped-def]
    m = bf_blocks()
    mid = bf_model(rc, m)
    r = asked(rc, mid, {"cutoffs": [0, 1], "grades": ["AU_CUT"], "curve": 10, "csv": True, "groups": "ALT",
                        "categories": {"field": "OXIDE", "values": ["Ox", "Sulf"]}})  # fmt: skip
    assert r.status_code == 200, r.text
    job = wait_job(rc, r.json()["source"], r.json()["id"])
    assert job["status"] == "done", job
    assert same(job["result"], run(rc, mid, cutoffs=[0, 1], grades=["AU_CUT"], curve=10, csv=True, groups="ALT",
                                   categories={"field": "OXIDE", "values": ["Ox", "Sulf"]}))  # fmt: skip
    assert job["result"]["model_id"] == mid and job["result"]["excluded"]["filtered"] == int((m["OXIDE"] < 0).sum())


def test_422_and_404_are_russian_with_a_param(rc) -> None:  # type: ignore[no-untyped-def]
    mid = bf_model(rc, bf_blocks())

    def refused(body: Any, status: int, code: str, param: str | None = None, model: int | None = None) -> None:
        r = asked(rc, model or mid, body)
        assert r.status_code == status, r.text
        e = r.json()
        assert e["code"] == code, e
        assert e["message"] and e["hint"] and any("а" <= ch.lower() <= "я" for ch in e["message"]), e
        if param is not None:
            assert e["param"] == param, e

    refused([], 422, "bad_request")
    refused({"grades": "AU_CUT"}, 422, "bad_param", "grades")
    refused({"grades": ["NOPE"]}, 404, "block_field_not_found", "grades.0")
    refused({"grades": ["ALT"]}, 422, "bad_param", "grades.0")  # a category is not a grade
    refused({"cutoff_field": "ALT"}, 422, "bad_param", "cutoff_field")
    refused({"density": "ALT"}, 422, "bad_param", "density")
    refused({"density": -1}, 422, "bad_param", "density")
    refused({"density": 0}, 422, "bad_param", "density")
    refused({"cutoffs": []}, 422, "bad_param", "cutoffs")
    refused({"cutoffs": [0, "a"]}, 422, "bad_param", "cutoffs.1")
    refused({"cutoffs": [True]}, 422, "bad_param", "cutoffs.0")
    refused({"curve": 1}, 422, "bad_param", "curve")
    refused({"csv": "yes"}, 422, "bad_param", "csv")
    refused({"groups": "AU_CUT"}, 422, "bad_param", "groups")  # a number cannot group
    refused({"categories": {"field": "ALT", "values": ["нет такой"]}}, 422, "bad_param", "categories.values.0")
    refused({"categories": {"field": "ALT", "values": []}}, 422, "bad_param", "categories")
    refused({"categories": {"field": "AU", "values": ["G"]}}, 422, "bad_param", "categories.field")
    refused({"filter_id": 99999}, 404, "filter_not_found")
    refused({"restrict": {"cond": {"field": "ZZZ", "op": ">", "value": 1}}}, 422, "filter_field_missing")
    refused({}, 404, "block_model_not_found", model=99999)
    # the report works in a project «только просмотр»: nothing is written (here: the route needs no writable project)
    assert asked(rc, mid, {"cutoffs": ["0,5"]}).status_code == 200
