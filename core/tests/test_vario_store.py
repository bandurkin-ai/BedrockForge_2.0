"""Stored variogram models (подшаг 16.13; ADR 0022 §12, §14; e16-api.md «Модели вариограмм»): saving with a copy of
the points of the calculation, the list, reading, renaming and replacing, deleting, 409 `name_taken`, 423 in a
read-only project, «устарел» by the inputs and «источник удалён», the export and import JSON / GSLIB (Т8.6-8 through
the storage, a broken file — `bad_file {line}`, a path with Cyrillic — `-m windows`), the cycle `.bfggis` (ТЗ 10.5
п. 5) with the ids renumbered (`geostat/remap.py`), the store on PostgreSQL (`-m db`)."""

from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect, migrate, schema
from bf_core.db.schema import dh_tables
from bf_core.drillholes import store as dh
from bf_core.geostat import remap
from bf_core.geostat import router as gr
from bf_core.geostat.variogram import exchange, points, spec
from bf_core.geostat.variogram import store as ms
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.dbutil import make_engine
from tests.test_import_job import wait_job
from tests.test_vario_spec import HOLES, LITHO, SAMPLES

API = "/api/v1"
GS = API + "/geostat/variography"
MODEL = {"type": "semivariogram", "c0": 0.2, "common_axes": True,
         "structures": [{"type": "spherical", "c": 1.0, "ang1": 30, "ang2": -10, "ang3": 0, "a1": 120, "a2": 80,
                         "a3": 20}]}  # fmt: skip


def noop(_p: float | None, _m: str | None) -> None:
    pass


@pytest.fixture(autouse=True)
def _roles(monkeypatch: pytest.MonkeyPatch) -> None:
    """The roles `vario`, `nscore` until the line of the orchestrator (`db/schema.py` BLOB_ROLES) is in."""
    if not {ms.VARIO, ms.NSCORE} <= schema.BLOB_ROLES:
        monkeypatch.setattr(schema, "BLOB_ROLES", schema.BLOB_ROLES | {ms.VARIO, ms.NSCORE})


def fill(engine: sa.Engine, name: str = "Скважины") -> tuple[int, int, int]:
    """The set of `test_vario_spec` (three holes with collars, samples, lithology) in a project database."""
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, name)
        dh.replace_collars(conn, set_id, [dh.CollarRow(h, x=x, y=y, z=100.0, td=20.0, azimuth=0.0, dip=-90.0)
                                          for h, x, y in HOLES])  # fmt: skip
    a = dh.write_table(engine, set_id, dh.TableData(
        name="Пробы", kind="assay", holes=[r[0] for r in SAMPLES], from_m=[r[1] for r in SAMPLES],
        to_m=[r[2] for r in SAMPLES],
        columns=[dh.ColumnSpec("AU", "number", "г/т", "component"), dh.ColumnSpec("ROCK", "text"),
                 dh.ColumnSpec("DECL_W", "number")],
        values=[[r[3] for r in SAMPLES], [r[4] for r in SAMPLES], [r[5] for r in SAMPLES]]),
        job_id=None, tick=noop)  # fmt: skip
    b = dh.write_table(engine, set_id, dh.TableData(
        name="Литология", kind="interval", holes=[r[0] for r in LITHO], from_m=[r[1] for r in LITHO],
        to_m=[r[2] for r in LITHO], columns=[dh.ColumnSpec("ZONE", "text"), dh.ColumnSpec("CODE", "number")],
        values=[[r[3] for r in LITHO], [r[4] for r in LITHO]]), job_id=None, tick=noop)  # fmt: skip
    return set_id, int(a["table_id"]), int(b["table_id"])


def nscore_bytes(p: points.Points) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    out = io.BytesIO()
    w = p.weights if p.weights is not None else np.ones(p.n)
    pq.write_table(pa.table({"value": p.z, "score": p.values, "weight": w}), out)
    return out.getvalue()


def calc(project: Any, set_id: int, table: int, **extra: Any) -> tuple[str, dict[str, Any]]:
    """A calculation record as the job of 16.14 leaves it (the points real, the γ of the form made up)."""
    body = {"set_id": set_id, "table": str(table), "field": "AU", **extra}
    with dialect.read_tx(project.engine) as conn:
        data = spec.parse_data(conn, body)
    p = points.load(project, data)
    key = hashlib.sha256(json.dumps([body, p.inputs], sort_keys=True).encode()).hexdigest()
    form = {"key": key, "set_id": set_id, "table": str(table), "field": "AU", "unit": "г/т",
            "transform": data.transform, "sigma2": p.sigma2, "sigma2_z": p.sigma2_z, "origin": p.origin.tolist(),
            "counts": p.counts, "sampled": None, "params": {"lag": 10.0, "nlag": 2, "seed": 12345},
            "directions": [{"axis": 1, "az": 30.0, "plunge": 10.0, "az_tol": 22.5, "dip_tol": 22.5, "band_h": "inf",
                            "band_v": 20.0, "lags": [10.0, 20.0], "h": [9.7, None], "n": [4, 0],
                            "gamma": [0.6, None], "rho": [0.3, None], "pairwise": [0.4, None], "n_pw": [4, 0],
                            "few": [True, True]}],
            "downhole": None, "warnings": []}  # fmt: skip
    source = {k: v for k, v in data.canonical().items() if k != "transform"}
    scores = nscore_bytes(p) if data.transform == "nscore" else None
    ms.write_result(project.folder, key, form, source=source, inputs=p.inputs, nscore=scores)
    return key, form


@pytest.fixture()
def vc(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """A core with an open project and the set; the router is added here until `api/modules.py` names it."""
    if not any(str(getattr(r, "path", "")).startswith(GS) for r in pclient.app.routes):
        pclient.app.include_router(gr.router, prefix=API)
    assert pclient.post(API + "/projects", json={"name": "Вариография"}).status_code == 200
    project = projects_of(pclient.core).require_current()
    draft = fill(project.engine, "Черновик")[
        0
    ]  # deleted: the ids of the set and its tables shift on a `.bfggis` import
    pclient.set_id, pclient.a, pclient.b = fill(project.engine)
    assert pclient.delete(f"{API}/drillholes/sets/{draft}").status_code == 200
    pclient.project = project
    events: list[tuple[str, dict[str, Any]]] = []
    publish = pclient.core.events.publish
    monkeypatch.setattr(pclient.core.events, "publish", lambda e, d: (events.append((e, d)), publish(e, d))[1])
    pclient.events = events
    return pclient


def save(c, key: str, **extra: Any) -> Any:  # type: ignore[no-untyped-def]
    return c.post(GS + "/models", json={"model": MODEL, "exp_key": key, **extra})


def test_save_list_get_with_a_copy_of_the_points(vc) -> None:  # type: ignore[no-untyped-def]
    key, form = calc(vc.project, vc.set_id, vc.a)
    fit = {"weights": "n_g2", "residual": 0.125, "n": 30, "fixed": ["c0"]}
    r = save(vc, key, fit=fit, color="#F46A1A")
    assert r.status_code == 200, r.text
    mid = r.json()["id"]
    assert r.json() == {"id": mid, "name": "AU · Пробы"}  # «‹поле› · ‹таблица›»
    assert vc.events[-1] == ("geostat.changed", {"kind": "variogram_model", "id": mid})
    listed = vc.get(GS + "/models").json()["models"]
    assert [m["id"] for m in listed] == [mid]
    m = listed[0]
    assert {k: m[k] for k in ("name", "set_id", "table", "field", "transform", "type", "color", "structures",
                              "stale", "stale_reason", "source_lost")} == {
        "name": "AU · Пробы", "set_id": vc.set_id, "table": str(vc.a), "field": "AU", "transform": "none",
        "type": "semivariogram", "color": "#f46a1a", "structures": 1, "stale": False, "stale_reason": None,
        "source_lost": False}  # fmt: skip
    assert m["sill"] == 1.2 and m["created_at"] and m["updated_at"]
    assert vc.get(GS + "/models", params={"set_id": vc.set_id + 1000}).json() == {"models": []}
    got = vc.get(f"{GS}/models/{mid}").json()
    meta = got["meta"]
    assert got["points"] == form and got["color"] == "#f46a1a" and not got["stale"]
    assert meta["source"] == {"set_id": vc.set_id, "table": str(vc.a), "field": "AU", "domain": None,
                              "weights": "DECL_W"}  # fmt: skip
    assert (meta["algo"], meta["v"], meta["type"], meta["transform"], meta["exp_key"]) == (
        "vario-1",
        1,
        "semivariogram",
        "none",
        key,
    )
    assert meta["sigma2"] == form["sigma2"] and meta["c0"] == 0.2 and meta["common_axes"] is True
    assert meta["structures"] == [{**MODEL["structures"][0], "ang1": 30.0, "ang2": -10.0, "ang3": 0.0,
                                   "a1": 120.0, "a2": 80.0, "a3": 20.0}]  # fmt: skip
    assert meta["fit"] == fit and meta["back"] is None and len(meta["sha256"]) == 64
    assert set(meta["versions"]) == {"numpy", "scipy", "numba"} and meta["calc"]["params"]["lag"] == 10.0
    assert meta["inputs"]["tables"][0]["id"] == vc.a and meta["inputs"]["trajectories"]
    ms.result_path(vc.project.folder, key).unlink()  # the cache cleared: the model keeps its points
    assert vc.get(f"{GS}/models/{mid}").json()["points"] == form
    assert save(vc, key).json()["code"] == "result_not_found"


def test_names_and_refusals(vc) -> None:  # type: ignore[no-untyped-def]
    key, _ = calc(vc.project, vc.set_id, vc.a)
    first = save(vc, key).json()
    assert save(vc, key).json()["name"] == "AU · Пробы (2)"  # the default name is made free
    r = save(vc, key, name="  au ·  пробы ")
    assert (r.status_code, r.json()["code"], r.json()["param"]) == (409, "name_taken", "name"), r.text
    assert r.json()["hint"]
    for extra, code, param in (({"exp_key": "x"}, "bad_param", "exp_key"),
                               ({"exp_key": "0" * 64}, "result_not_found", "exp_key"),
                               ({"color": "синий"}, "bad_param", "color"),
                               ({"fit": {"weights": "w"}}, "bad_param", "fit.weights"),
                               ({"name": "   ", "exp_key": key}, None, None)):  # fmt: skip
        r = vc.post(GS + "/models", json={"model": MODEL, "exp_key": key, **extra})
        if code is None:
            assert r.status_code == 200  # an empty name — the default one
            continue
        assert (r.json()["code"], r.json()["param"]) == (code, param), r.text
        assert r.status_code in (404, 422) and r.json()["message"] and r.json()["hint"]
    bad = {**MODEL, "structures": [{**MODEL["structures"][0], "a2": -1}]}
    r = vc.post(GS + "/models", json={"model": bad, "exp_key": key})
    assert (r.status_code, r.json()["param"]) == (422, "model.structures.0.a2"), r.text
    assert vc.post(GS + "/models", json=[1]).json()["code"] == "bad_request"
    assert vc.get(f"{GS}/models/{first['id'] + 100}").json()["code"] == "model_not_found"
    tmp = BlobStore(vc.project.folder).tmp_dir
    assert not tmp.exists() or not any(tmp.iterdir())  # refusals leave no staged files


def test_rename_replace_delete(vc) -> None:  # type: ignore[no-untyped-def]
    key, _ = calc(vc.project, vc.set_id, vc.a)
    mid = save(vc, key).json()["id"]
    other = save(vc, key, name="Другая").json()["id"]
    r = vc.put(f"{GS}/models/{mid}", json={"name": "другая", "expected_version": 1})
    assert (r.status_code, r.json()["code"]) == (409, "name_taken")
    r = vc.put(f"{GS}/models/{mid}", json={"name": "Новая"})  # the version is required
    assert (r.status_code, r.json()["param"]) == (422, "expected_version"), r.text
    for v in (True, "1", 0):
        assert vc.put(f"{GS}/models/{mid}", json={"expected_version": v}).json()["param"] == "expected_version"
    model = {
        "type": "correlogram",
        "c0": 0.0,
        "common_axes": False,
        "structures": [
            {"type": "exponential", "c": 0.5, "ang1": 10, "ang2": 0, "ang3": 0, "a1": 50, "a2": 40, "a3": 10},
            {"type": "gaussian", "c": 0.25, "ang1": 20, "ang2": 5, "ang3": 0, "a1": 300, "a2": 200, "a3": 30},
        ],
    }
    before = vc.get(f"{GS}/models/{mid}").json()
    key2, form2 = calc(vc.project, vc.set_id, vc.a, transform="nscore")
    body = {"name": "Золото", "model": model, "exp_key": key2, "color": "#8A99A8", "expected_version": 1}
    r = vc.put(f"{GS}/models/{mid}", json=body)
    assert r.json() == {"id": mid, "name": "Золото", "version": 2}, r.text
    r = vc.put(f"{GS}/models/{mid}", json={**body, "name": "Серебро"})  # another window with the version 1
    assert (r.status_code, r.json()["code"], r.json()["version"]) == (409, "version_conflict", 2), r.text
    assert r.json()["message"] == "Модель вариограммы изменена в другом окне." and r.json()["hint"]
    tmp = BlobStore(vc.project.folder).tmp_dir
    assert not tmp.exists() or not any(tmp.iterdir())  # the conflict is found before the files are staged
    got = vc.get(f"{GS}/models/{mid}").json()
    assert got["meta"]["type"] == "correlogram" and len(got["meta"]["structures"]) == 2
    assert got["meta"]["transform"] == "nscore" and got["points"] == form2 and got["color"] == "#8a99a8"
    assert got["meta"]["sha256"] != before["meta"]["sha256"] and got["version"] == before["version"] + 1
    with dialect.read_tx(vc.project.engine) as conn:
        assert set(ms.owned_files(conn, mid)) == {ms.VARIO, ms.NSCORE}
        path = ms.nscore_file(conn, BlobStore(vc.project.folder), mid)
    assert path is not None and path.read_bytes() == ms.nscore_path(vc.project.folder, key2).read_bytes()
    assert vc.put(f"{GS}/models/{mid}", json={"fit": None, "expected_version": 2}).status_code == 200
    assert vc.delete(f"{GS}/models/{other}").json() == {"deleted": other}
    assert vc.events[-1] == ("geostat.changed", {"kind": "variogram_model", "id": other, "deleted": True})
    assert vc.delete(f"{GS}/models/{other}").status_code == 404
    assert vc.put(f"{GS}/models/{other}", json={"name": "x", "expected_version": 1}).json()["code"] == "model_not_found"
    assert [m["name"] for m in vc.get(GS + "/models").json()["models"]] == ["Золото"]


def test_stale_by_the_inputs_and_source_lost(vc) -> None:  # type: ignore[no-untyped-def]
    key, _ = calc(vc.project, vc.set_id, vc.a, domain={"table": "Литология", "field": "ZONE", "values": ["окисл"]})
    mid = save(vc, key).json()["id"]
    engine = vc.project.engine
    with dialect.read_tx(engine) as conn:  # the key of the trajectories is that `points.load` records
        meta = ms.meta_of(conn, mid)[1]
        assert ms.trajectories_key(conn, vc.set_id) == meta["inputs"]["trajectories"]
    with dialect.write_tx(engine) as conn:  # an edit of the lithology table
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == vc.b).values(rev=dh_tables.c.rev + 1))
    m = vc.get(GS + "/models").json()["models"][0]
    assert (m["stale"], m["stale_reason"], m["source_lost"]) == (True, "изменена таблица «Литология»", False)
    with dialect.write_tx(engine) as conn:
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == vc.b).values(rev=dh_tables.c.rev - 1))
    assert not vc.get(f"{GS}/models/{mid}").json()["stale"]
    dh.write_table(engine, vc.set_id, dh.TableData(name="Ещё", kind="interval", holes=["A-1"], from_m=[0.0],
                   to_m=[1.0], columns=[], values=[]), job_id=None, tick=noop)  # fmt: skip
    got = vc.get(f"{GS}/models/{mid}").json()
    assert (got["stale"], got["stale_reason"]) == (True, "изменены траектории")
    with dialect.read_tx(engine) as conn:
        lost = ms.freshness(conn, {**meta, "source": {**meta["source"], "set_id": vc.set_id + 1000}})
        gone = ms.freshness(conn, {**meta, "inputs": {"tables": [{"id": None, "name_key": "нет"}]}})
        nulls = ms.freshness(conn, remap.model_meta(meta, {}.get, {}.get))
    assert lost == gone == nulls == {"stale": True, "stale_reason": "источник удалён", "source_lost": True}


def gslib_model(rng: np.random.Generator) -> dict[str, Any]:
    n = int(rng.integers(1, 4))
    ang = [float(rng.uniform(0, 359)), float(rng.uniform(-89, 89)), float(rng.uniform(-179, 179))]
    return {
        "type": "semivariogram",
        "c0": float(rng.uniform(0, 1)),
        "common_axes": True,
        "structures": [
            {
                "type": str(rng.choice(["spherical", "exponential", "gaussian"])),
                "c": float(rng.uniform(0, 2)),
                "ang1": ang[0],
                "ang2": ang[1],
                "ang3": ang[2],
                "a1": float(rng.uniform(1, 900)),
                "a2": float(rng.uniform(1, 900)),
                "a3": float(rng.uniform(0.1, 90)),
            }
            for _ in range(n)
        ],
    }


def test_exchange_through_the_storage_bit_for_bit(vc) -> None:  # type: ignore[no-untyped-def]
    """Т8.6-8: a stored model → export → import gives the same numbers bit for bit; a second export — the same
    text byte for byte; JSON keeps the rest of `meta` except `inputs`."""
    key, _ = calc(vc.project, vc.set_id, vc.a)
    rng = np.random.default_rng(816)
    for k in range(40):
        model = gslib_model(rng)
        mid = vc.post(GS + "/models", json={"model": model, "exp_key": key, "name": f"М{k}"}).json()["id"]
        stored = vc.get(f"{GS}/models/{mid}").json()["meta"]
        for fmt in ("gslib", "json"):
            r = vc.get(f"{GS}/models/{mid}/export", params={"format": fmt})
            assert r.status_code == 200, r.text
            out = r.json()
            assert out["format"] == fmt and out["filename"] == f"М{k}{exchange.EXTENSIONS[fmt]}"
            back = vc.post(GS + "/models/import", json={"format": fmt, "text": out["text"]}).json()["model"]
            for a, b in zip(back["structures"], stored["structures"], strict=True):
                assert all(math.copysign(1, a[f]) == math.copysign(1, b[f]) and a[f] == b[f] for f in b if f != "type")
            assert back["c0"] == stored["c0"] and back["common_axes"] is True
            assert vc.get(f"{GS}/models/{mid}/export", params={"format": fmt}).json()["text"] == out["text"]
        doc = json.loads(vc.get(f"{GS}/models/{mid}/export").json()["text"])
        assert doc["kind"] == "variogram_model" and "inputs" not in doc and doc["source"]["field"] == "AU"
    auto = vc.post(GS + "/models/import", json={"text": "1 0.5\n2 1.5 0 0 0\n100 50 10\n"}).json()
    assert auto["format"] == "gslib" and auto["model"]["structures"][0]["type"] == "exponential"


def test_import_and_export_refusals(vc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    key, _ = calc(vc.project, vc.set_id, vc.a)
    mid = save(vc, key).json()["id"]
    r = vc.post(GS + "/models/import", json={"format": "gslib", "text": "1 0.1\n1 1,5 0 0 0\n100 50 10\n"})
    assert (r.status_code, r.json()["code"], r.json()["line"]) == (422, "bad_file", 2), r.text
    assert r.json()["hint"]
    r = vc.post(GS + "/models/import", json={"text": '{"kind": "variogram_model",\n "c0": }'})
    assert (r.json()["code"], r.json()["line"]) == ("bad_file", 2)
    r = vc.post(GS + "/models/import", json={"format": "xls", "text": "1 0"})
    assert (r.json()["code"], r.json()["param"]) == ("bad_param", "format")
    for body, param in (({}, "path"), ({"path": "модель.par"}, "path"), ({"text": 5}, "text"),
                        ({"path": str(tmp_path / "нет.par")}, "path"),
                        ({"text": "1", "path": "/x"}, "path")):  # fmt: skip
        r = vc.post(GS + "/models/import", json=body)
        assert (r.status_code, r.json()["param"]) == (422, param), (body, r.text)
    r = vc.post(GS + "/models/import", json={"text": "1", "path": "/x"})
    assert r.json()["message"] == "Задайте что-то одно: текст или путь к файлу."
    for body in (
        {"format": "gslib", "path": "модель.par"},
        {"format": "gslib", "path": str(tmp_path / "нет" / "м.par")},
        {"format": "gslib"},
    ):
        r = vc.post(f"{GS}/models/{mid}/export", json=body)
        assert (r.status_code, r.json()["code"], r.json()["param"]) == (422, "bad_param", "path"), r.text
    r = vc.post(f"{GS}/models/{mid}/export", json={"format": "csv", "path": str(tmp_path / "м.csv")})
    assert (r.json()["code"], r.json()["param"]) == ("bad_param", "format")
    r = vc.post(f"{GS}/models/{mid}/export", json={"path": str(tmp_path / "м.json"), "overwrite": "да"})
    assert (r.json()["code"], r.json()["param"]) == ("bad_param", "overwrite")
    r = vc.get(f"{GS}/models/{mid}/export", params={"format": "csv"})
    assert (r.json()["code"], r.json()["param"]) == ("bad_param", "format")
    assert vc.get(f"{GS}/models/{mid + 9}/export").json()["code"] == "model_not_found"
    assert vc.post(f"{GS}/models/{mid + 9}/export", json={"path": str(tmp_path / "м.json")}).status_code == 404
    inside = Path(vc.project.folder) / "модель.json"  # the folder of the project is not a place for a file
    r = vc.post(f"{GS}/models/{mid}/export", json={"path": str(inside)})
    assert (r.status_code, r.json()["code"], r.json()["param"]) == (422, "bad_path", "path"), r.text
    assert not inside.exists()


def test_export_writes_by_post_with_the_extension_and_asks_to_replace(vc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    key, _ = calc(vc.project, vc.set_id, vc.a)
    mid = save(vc, key).json()["id"]
    out = tmp_path / "выгрузка"
    out.mkdir()
    r = vc.post(f"{GS}/models/{mid}/export", json={"format": "gslib", "path": str(out / "модель.txt")})
    target = out / "модель.txt.par"  # the extension of the format, whatever the name had
    assert r.status_code == 200 and r.json() == {"format": "gslib", "path": str(target),
                                                 "bytes": target.stat().st_size}, r.text  # fmt: skip
    r = vc.post(f"{GS}/models/{mid}/export", json={"path": str(out / "модель")})
    assert r.json()["path"] == str(out / "модель.json") and (out / "модель.json").is_file()
    (out / "модель.json").write_text("чужой файл", "utf-8")
    r = vc.post(f"{GS}/models/{mid}/export", json={"path": str(out / "модель.json")})
    assert (r.status_code, r.json()["code"], r.json()["files"]) == (409, "files_exist", ["модель.json"])
    assert r.json()["hint"] and (out / "модель.json").read_text("utf-8") == "чужой файл"  # not replaced silently
    r = vc.post(f"{GS}/models/{mid}/export", json={"path": str(out / "модель.json"), "overwrite": True})
    assert r.status_code == 200 and json.loads((out / "модель.json").read_text("utf-8"))["kind"] == "variogram_model"
    got = vc.get(f"{GS}/models/{mid}/export", params={"path": str(out / "x.json")}).json()
    assert set(got) == {"format", "filename", "text"}  # GET reads only: a `path` is not a write
    assert not (out / "x.json").exists()


def test_read_only_project_is_423(vc) -> None:  # type: ignore[no-untyped-def]
    key, _ = calc(vc.project, vc.set_id, vc.a)
    mid = save(vc, key).json()["id"]
    vc.project.read_only = True  # opened in another window (ТЗ 5.7)
    try:
        for r in (save(vc, key, name="Ещё"), vc.put(f"{GS}/models/{mid}", json={"name": "Ещё", "expected_version": 1}),
                  vc.delete(f"{GS}/models/{mid}")):  # fmt: skip
            assert (r.status_code, r.json()["code"]) == (423, "project_read_only") and r.json()["hint"], r.text
        assert vc.get(f"{GS}/models/{mid}").status_code == 200  # reading, export and import go on
        assert vc.get(f"{GS}/models/{mid}/export", params={"format": "gslib"}).status_code == 200
        assert vc.post(GS + "/models/import", json={"text": "1 0\n1 1 0 0 0\n10 10 10\n"}).status_code == 200
    finally:
        vc.project.read_only = False


def test_bfggis_roundtrip_renumbers_and_keeps_models_fresh(vc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from bf_core.archive import importer

    key, form = calc(vc.project, vc.set_id, vc.a, domain={"table": str(vc.b), "field": "ZONE", "values": ["окисл"]},
                     transform="nscore")  # fmt: skip
    mid = save(vc, key, name="Золото").json()["id"]
    before = vc.get(f"{GS}/models/{mid}").json()
    r = vc.post(API + "/projects/export", json={"path": str(tmp_path / "Вариография")}).json()
    job = wait_job(vc, r["source"], r["id"])
    assert job["status"] == "done", job
    r = vc.post(API + "/projects/import", json={"path": job["result"]["path"], "mode": "copy"}).json()
    job = wait_job(vc, r["source"], r["id"])
    assert job["status"] == "done", job
    maps = job["result"]["id_maps"]
    assert vc.post(f"{API}/projects/{job['result']['uid']}/open").status_code == 200
    project = projects_of(vc.core).require_current()
    if not hasattr(importer, "remap_geostat"):  # until the line of the orchestrator in `importer._restore` is in
        objs = {int(k): int(v) for k, v in maps["objects"].items()}
        tabs = {int(k): int(v) for k, v in maps["dh_tables"].items()}
        with dialect.write_tx(project.engine) as conn:
            assert remap.remap_geostat(conn, objs, tabs) == 1
    new_set, new_a = maps["objects"][str(vc.set_id)], str(maps["dh_tables"][str(vc.a)])
    listed = vc.get(GS + "/models").json()["models"]
    assert len(listed) == 1 and listed[0]["name"] == "Золото"
    got = vc.get(f"{GS}/models/{listed[0]['id']}").json()
    assert (got["stale"], got["source_lost"]) == (False, False), got
    src = got["meta"]["source"]
    assert (src["set_id"], src["table"], src["domain"]["table"]) == (new_set, new_a, str(maps["dh_tables"][str(vc.b)]))
    assert [t["id"] for t in got["meta"]["inputs"]["tables"]] == [int(new_a), maps["dh_tables"][str(vc.b)]]
    assert got["points"] == {**form, "set_id": new_set, "table": new_a}
    assert got["meta"]["sha256"] == before["meta"]["sha256"]
    with dialect.read_tx(project.engine) as conn:
        assert set(ms.owned_files(conn, got["id"])) == {ms.VARIO, ms.NSCORE}


@pytest.mark.windows
def test_gslib_to_a_path_with_cyrillic(vc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    key, _ = calc(vc.project, vc.set_id, vc.a)
    model = gslib_model(np.random.default_rng(3))
    mid = vc.post(GS + "/models", json={"model": model, "exp_key": key, "name": "Золото · Пробы"}).json()["id"]
    folder = tmp_path / "Модели вариограмм" / "Месторождение «Северное»"
    folder.mkdir(parents=True)
    target = folder / "Золото · Пробы.par"
    r = vc.post(f"{GS}/models/{mid}/export", json={"format": "gslib", "path": str(target)})
    assert r.status_code == 200, r.text
    raw = target.read_bytes()
    assert r.json() == {"format": "gslib", "path": str(target), "bytes": len(raw)}
    assert b"\r\n" not in raw and raw.decode("ascii")  # «\n» on Windows too, ASCII
    text = vc.get(f"{GS}/models/{mid}/export", params={"format": "gslib"}).json()["text"]
    assert raw == text.encode("ascii")
    back = vc.post(GS + "/models/import", json={"format": "gslib", "path": str(target)}).json()["model"]
    stored = vc.get(f"{GS}/models/{mid}").json()["meta"]
    assert back["c0"] == stored["c0"] and back["structures"] == stored["structures"]
    again = vc.post(f"{GS}/models/{mid}/export", json={"format": "json", "path": str(folder / "м.json")})
    assert again.status_code == 200 and json.loads((folder / "м.json").read_text("utf-8"))["source"]["field"] == "AU"


@pytest.mark.db
def test_store_on_the_database(db_url: str, tmp_path: Path) -> None:
    """The store without the API on SQLite and PostgreSQL: create, the list, rename, «устарел», delete."""
    engine = make_engine(db_url, tmp_path)
    try:
        migrate.upgrade(engine, "project")
        set_id, a, _b = fill(engine)
        project = SimpleNamespace(engine=engine, uid=f"test:{tmp_path}:{db_url}", folder=tmp_path)
        key, form = calc(project, set_id, a)
        store = BlobStore(tmp_path)
        kind, parsed = exchange.api_model(MODEL)
        model = (kind, exchange.model_form(kind, parsed))
        with dialect.write_tx(engine) as conn:
            first = ms.create(conn, store, ms.stage(store, tmp_path, key), model)
            second = ms.create(conn, store, ms.stage(store, tmp_path, key), model, name="Вторая")
        assert first["name"] == "AU · Пробы" and second["name"] == "Вторая"
        with dialect.write_tx(engine) as conn:
            with pytest.raises(Exception) as e:
                ms.update(conn, store, second["id"], expected_version=1, name="au · пробы")
            assert getattr(e.value, "code", None) == "name_taken"
        with dialect.write_tx(engine) as conn:
            ms.update(conn, store, second["id"], expected_version=1, name="Третья", color="#1B222B")
            with pytest.raises(Exception) as e:  # the version 1 again: another window
                ms.update(conn, store, second["id"], expected_version=1, name="Четвёртая")
            assert getattr(e.value, "code", None) == "version_conflict"
        with dialect.read_tx(engine) as conn:
            listed = ms.list_models(conn, set_id)
            got = ms.get(conn, store, first["id"])
        assert [m["name"] for m in listed] == ["AU · Пробы", "Третья"] and not any(m["stale"] for m in listed)
        assert got["points"] == form and listed[1]["color"] == "#1b222b"
        with dialect.write_tx(engine) as conn:
            conn.execute(sa.update(dh_tables).where(dh_tables.c.id == a).values(rev=dh_tables.c.rev + 1))
            ms.delete(conn, second["id"])
        with dialect.read_tx(engine) as conn:
            listed = ms.list_models(conn)
            assert ms.owned_files(conn, second["id"]) == {}
        assert [(m["name"], m["stale_reason"]) for m in listed] == [("AU · Пробы", "изменена таблица «Пробы»")]
    finally:
        engine.dispose()
