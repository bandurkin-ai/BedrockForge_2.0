"""The answers of the core the window «Вариография» is read against (подшаг 16.23; ADR 0022 §15 «Урок Э14–Э15»,
решение 23; e16-api.md): `job.result` of `variography.experimental` (axes; directions with normal scores), the
failure of a job (`job.error`), `POST /preview` with and without `sampled`, the refusal of the preview (422 with
`counts`); for 16.24–16.25 — `job.result` of `variography.map` (fan, rose, map), `POST /fit` (with `fix`), `/back`
(normal scores), `/curve`, the models (create, list, read, replace with `expected_version`, 409
`name_taken` and `version_conflict`, export — `GET` the text, `POST` the file, 409 `files_exist`, import, `bad_file`,
stale, delete)
and `job.result` of «Перестроить» — all through the real routes and jobs in a `spawn` worker. The file
`tests/data/variography/window_answers.json` is read by vitest (`ui/test/variography-window.test.ts`,
`ui/test/variogram-chart.test.ts`, `ui/test/variography-drag.test.ts`, `ui/test/variography-views.test.ts`).
Regenerate: `BF_REGEN_VARIO_WINDOW=1 uv run pytest -q
tests/test_vario_window_answers.py`. What changes from run to run (the key — of the tokens of the tables, the
seconds of the stages, the times and the paths of the files) is compared by its form only."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_tables
from bf_core.drillholes import store as dh
from bf_core.projects.service import projects_of
from tests.test_import_job import wait_job

API = "/api/v1"
GS = API + "/geostat/variography"
MODULES = ("bf_core.geostat.variogram.jobs", "bf_core.geostat.variogram.jobs_map")
DATA = Path(__file__).parent / "data" / "variography" / "window_answers.json"
REGEN = os.environ.get("BF_REGEN_VARIO_WINDOW") == "1"


def noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def grid_table(engine: Any, set_id: int, name: str, side: int, per_hole: int, gaps: bool, seed: int) -> int:
    """side² vertical holes 20 m apart from (500 000, 6 700 000), `per_hole` samples of 2 m; every 17th value
    missing when `gaps` (counted, never 0)."""
    rng = np.random.Generator(np.random.PCG64(seed))
    names = [f"{name[0]}-{i:03d}" for i in range(side * side)]
    with dialect.write_tx(engine) as conn:
        dh.replace_collars(conn, set_id, [dh.CollarRow(h, x=500_000.0 + 20.0 * (i % side),
                                                       y=6_700_000.0 + 20.0 * (i // side), z=300.0,
                                                       td=2.0 * per_hole, azimuth=0.0, dip=-90.0)
                                          for i, h in enumerate(names)])  # fmt: skip
    holes = [h for h in names for _ in range(per_hole)]
    depth = np.tile(np.arange(per_hole) * 2.0, side * side)
    au = np.exp(np.sin(depth / 37.0) + 0.5 * rng.standard_normal(depth.size)).round(4)
    values: list[float | None] = au.tolist()
    if gaps:
        values = [None if k % 17 == 5 else v for k, v in enumerate(values)]
    rock = ["гранит" if k % 3 else "диорит" for k in range(depth.size)]
    out = dh.write_table(engine, set_id, dh.TableData(name=name, kind="assay", holes=holes, from_m=depth.tolist(),
                                                      to_m=(depth + 2.0).tolist(),
                                                      columns=[dh.ColumnSpec("AU", "number", "г/т", "component"),
                                                               dh.ColumnSpec("ROCK", "text")],
                                                      values=[values, rock]), job_id=None, tick=noop)  # fmt: skip
    return int(out["table_id"])


@pytest.fixture()
def core(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    import importlib

    from bf_core.jobs.registry import HANDLER_MODULES

    extra = [m for m in MODULES if m not in HANDLER_MODULES]
    if extra:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", ",".join(extra))
    for m in MODULES:
        importlib.import_module(m)
    assert pclient.post(API + "/projects", json={"name": "Вариография"}).status_code == 200
    engine = projects_of(pclient.core).require_current().engine
    with dialect.write_tx(engine) as conn:
        small = dh.create_set(conn, "Скважины")
        big = dh.create_set(conn, "Большой набор")
    pclient.small = (small, grid_table(engine, small, "Пробы", side=4, per_hole=30, gaps=True, seed=7))
    pclient.big = (big, grid_table(engine, big, "Композиты", side=10, per_hole=210, gaps=False, seed=3))
    return pclient


def job_of(c: Any, body: dict[str, Any], route: str = "/experimental") -> dict[str, Any]:
    r = c.post(GS + route, json=body)
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"source", "id"}
    return wait_job(c, r.json()["source"], r.json()["id"], timeout=240)  # type: ignore[no-any-return]


def build(c: Any, tmp: Path) -> dict[str, Any]:
    set_id, table = c.small
    base = {"set_id": set_id, "table": str(table), "field": "AU"}
    axes = {**base, "axes": {"ang1": 30, "ang2": 0, "ang3": 0}, "nlag": 6, "min_pairs": 800}
    done = job_of(c, axes)
    assert done["status"] == "done", done
    nscore = {**base, "transform": "nscore", "nlag": 4, "downhole": False,
              "directions": [{"az": 0, "plunge": 0}, {"az": 90, "plunge": 0, "az_tol": 45}]}  # fmt: skip
    ns = job_of(c, nscore)
    assert ns["status"] == "done", ns
    failed = job_of(c, {**base, "domain": {"field": "ROCK", "values": ["нет такой"]}})
    assert failed["status"] == "failed", failed
    pv = c.post(GS + "/preview", json=axes)
    assert pv.status_code == 200, pv.text
    big_set, big_table = c.big
    sampled = c.post(GS + "/preview", json={"set_id": big_set, "table": str(big_table), "field": "AU",
                                            "axes": {"ang1": 0, "ang2": 0, "ang3": 0}, "nlag": 5})  # fmt: skip
    assert sampled.status_code == 200, sampled.text
    refused = c.post(GS + "/preview", json={**base, "domain": {"field": "ROCK", "values": ["нет такой"]}})
    assert refused.status_code == 422, refused.text
    bad_param = c.post(GS + "/preview", json={**base, "nlag": 0})
    assert bad_param.status_code == 422, bad_param.text
    return {
        **models_part(c, base, tmp),
        **{
            "rules": "ответы настоящего ядра: job.result, job.error, POST /preview, 422 (test_vario_window_answers.py)",
            "bodies": {"axes": axes, "nscore": nscore},
            "job_axes": done["result"],
            "job_nscore": ns["result"],
            "job_failed": {"status": failed["status"], "error": failed["error"]},
            "preview": pv.json(),
            "preview_sampled": sampled.json(),
            "preview_refused": {"status": refused.status_code, "body": refused.json()},
            "preview_bad_param": {"status": bad_param.status_code, "body": bad_param.json()},
        },
    }


TURNS = [((0.0, 0.0, 0.0), 30.0), ((0.0, 0.0, 0.0), 150.0), ((30.0, -20.0, 10.0), 45.0), ((120.0, -60.0, -35.0), 100.0),
         ((0.0, -90.0, 0.0), 60.0), ((200.0, 10.0, 170.0), 0.0)]  # fmt: skip
SPH = {"type": "spherical", "c": 0.5, "ang1": 30, "ang2": 0, "ang3": 0, "a1": 60, "a2": 40, "a3": 10}


def answer(r: Any, status: int = 200) -> Any:
    assert r.status_code == status, r.text
    return r.json() if status == 200 else {"status": r.status_code, "body": r.json()}


def models_part(c: Any, base: dict[str, Any], tmp: Path) -> dict[str, Any]:
    """16.24–16.25: the map, the fit, the back-transform, the curve, the models, «Перестроить»."""
    fitcalc = {**base, "axes": {"ang1": 30, "ang2": 0, "ang3": 0}, "nlag": 6, "min_pairs": 5}
    fc = job_of(c, fitcalc)
    assert fc["status"] == "done", fc
    key = fc["result"]["key"]
    model = {"type": "semivariogram", "c0": 0.1, "common_axes": True, "structures": [SPH]}
    fit_body = {"key": key, "type": "semivariogram", "model": model, "weights": "n_h", "fix": ["structures.0.a3"],
                "fix_nugget": False}  # fmt: skip
    fitted = answer(c.post(GS + "/fit", json=fit_body))
    fit_refused = answer(c.post(GS + "/fit", json={**fit_body, "key": "0" * 64}), 404)
    nscalc = {**base, "transform": "nscore", "axes": {"ang1": 30, "ang2": 0, "ang3": 0}, "nlag": 6, "min_pairs": 5}
    nc = job_of(c, nscalc)
    assert nc["status"] == "done", nc
    nmodel = {"type": "nscore", "c0": 0.2, "common_axes": True, "structures": [{**SPH, "c": 0.8}]}
    nfit = answer(c.post(GS + "/fit", json={**fit_body, "key": nc["result"]["key"], "type": "nscore",
                                            "model": nmodel, "fix": []}))  # fmt: skip
    back = answer(c.post(GS + "/back", json={"key": nc["result"]["key"], "model": nfit["model"]}))
    back_refused = answer(c.post(GS + "/back", json={"key": key, "model": nmodel}), 422)
    curve = answer(c.post(GS + "/curve", json={"model": fitted["model"], "n": 5}))
    curve_dirs = answer(c.post(GS + "/curve", json={"model": model, "directions": [{"az": 120, "plunge": 0}],
                                                    "h": [0, 10, 60, 90]}))  # fmt: skip
    mapbody = {**base, "plane": {"ang1": 0, "ang2": 0, "ang3": 0}, "nlag": 6, "min_pairs": 5,
               "fan": {"n": 6, "q": 0.75}, "map": {"nu": 4, "nv": 4, "min_pairs": 2}}  # fmt: skip
    mj = job_of(c, mapbody, "/map")
    assert mj["status"] == "done", mj
    # the models
    fit_meta = {"weights": "n_h", "residual": fitted["residual"], "n": fitted["n"], "fixed": fitted["fixed"]}
    save_body = {"model": fitted["model"], "exp_key": key, "fit": fit_meta}
    created = answer(c.post(GS + "/models", json=save_body))
    second = answer(c.post(GS + "/models", json={**save_body, "fit": None}))
    taken = answer(c.post(GS + "/models", json={**save_body, "name": created["name"]}), 409)
    put_body = {"name": "Вторая", "color": "#f46a1a", "expected_version": version_of(c, second["id"])}
    renamed = answer(c.put(f"{GS}/models/{second['id']}", json=put_body))
    conflict = answer(c.put(f"{GS}/models/{second['id']}", json={**put_body, "name": "Третья"}), 409)
    read = answer(c.get(f"{GS}/models/{created['id']}"))
    exp_json = answer(c.get(f"{GS}/models/{created['id']}/export?format=json"))
    exp_gslib = answer(c.get(f"{GS}/models/{created['id']}/export?format=gslib"))
    target = tmp / "модель.par"
    exp_body = {"format": "gslib", "path": str(target)}
    exp_path = answer(c.post(f"{GS}/models/{created['id']}/export", json=exp_body))
    exp_exists = answer(c.post(f"{GS}/models/{created['id']}/export", json=exp_body), 409)
    exp_replaced = answer(c.post(f"{GS}/models/{created['id']}/export", json={**exp_body, "overwrite": True}))
    imp_json = answer(c.post(GS + "/models/import", json={"text": exp_json["text"]}))
    imp_gslib = answer(c.post(GS + "/models/import", json={"format": "gslib", "text": exp_gslib["text"]}))
    imp_path = answer(c.post(GS + "/models/import", json={"path": str(target)}))
    imp_bad = answer(c.post(GS + "/models/import", json={"format": "gslib", "text": "1 0.1\n1 0,5 0 0 0\n"}), 422)
    deleted = answer(c.delete(f"{GS}/models/{second['id']}"))
    engine = projects_of(c.core).require_current().engine
    with dialect.write_tx(engine) as conn:  # an edit of the table: the model is stale
        conn.execute(sa.update(dh_tables).where(dh_tables.c.id == int(base["table"])).values(rev=dh_tables.c.rev + 1))
    stale = answer(c.get(GS + "/models"))
    r = c.post(f"{GS}/models/{created['id']}/rebuild")
    assert r.status_code == 200 and set(r.json()) == {"source", "id"}, r.text
    rebuilt = wait_job(c, r.json()["source"], r.json()["id"], timeout=240)
    assert rebuilt["status"] == "done", rebuilt
    after = answer(c.get(GS + "/models"))
    from bf_core.geostat.variogram import fan as vfan

    # a click on a ray of the fan / the map: the angles of the plane whose a1 is at θ (`fan.turn`)
    turns = [{"plane": list(p), "theta": t, "angles": list(vfan.turn(p, t))} for p, t in TURNS]
    return {
        "bodies_models": {"fit_calc": fitcalc, "nscore_calc": nscalc, "fit": fit_body, "map": mapbody,
                          "save": save_body},
        "job_fit_calc": fc["result"], "fit": fitted, "fit_refused": fit_refused,
        "job_nscore_axes": nc["result"], "fit_nscore": nfit, "back": back, "back_refused": back_refused,
        "curve": curve, "curve_directions": curve_dirs, "job_map": mj["result"],
        "model_created": created, "model_second": second, "model_taken": taken, "model_renamed": renamed,
        "model_conflict": conflict, "model_read": read, "export_json": exp_json, "export_gslib": exp_gslib,
        "export_path": exp_path, "export_exists": exp_exists, "export_replaced": exp_replaced,
        "import_json": imp_json, "import_gslib": imp_gslib, "import_path": imp_path, "import_bad": imp_bad,
        "model_deleted": deleted, "models_stale": stale, "job_rebuild": rebuilt["result"], "models_rebuilt": after,
        "turns": turns,
    }  # fmt: skip


def version_of(c: Any, model_id: int) -> int:
    return int(c.get(f"{GS}/models/{model_id}").json()["version"])


HEX = set("0123456789abcdef")
VARYING = {"created_at", "updated_at", "token"}  # the times; the token of a table (of the time of its write)


def masked(x: Any, path: str = "") -> Any:
    """A key (64 hex digits anywhere) — its form; the stages — their names; the times, the tokens — their type; a
    path — its file name; the text of a JSON export — parsed; other numbers to 1e−9 relative (threads, libm)."""
    if isinstance(x, dict):
        out: dict[str, Any] = {}
        for k, v in x.items():
            if k == "stages":
                out[k] = sorted(v)
            elif k in VARYING:
                out[k] = type(v).__name__
            elif k == "path" and isinstance(v, str):
                out[k] = Path(v).name
            elif k == "text" and isinstance(v, str) and v.startswith("{"):
                out[k] = masked(json.loads(v), f"{path}.{k}")
            else:
                out[k] = masked(v, f"{path}.{k}")
        return out
    if isinstance(x, list):
        return [masked(v, path) for v in x]
    if isinstance(x, str) and len(x) == 64 and set(x) <= HEX:
        return True
    if isinstance(x, float):
        return float(f"{x:.9g}")
    return x


def test_window_answers_are_what_the_core_answers(core, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    built = json.loads(json.dumps(build(core, tmp_path), ensure_ascii=False))
    if REGEN:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        DATA.write_text(json.dumps(built, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    stored = json.loads(DATA.read_text(encoding="utf-8"))
    assert masked(stored) == masked(built)
    # what the window relies on
    job = stored["job_axes"]
    assert job["fresh"] is False
    assert set(job["stages"]) == {"read", "trajectories", "pairs", "downhole", "write", "total"}
    assert [d["axis"] for d in job["directions"]] == [1, 2, 3] and job["downhole"] is not None
    assert job["sampled"] is None and job["counts"]["missing"] > 0 and job["warnings"] == []
    axis1 = job["directions"][0]
    assert None in axis1["gamma"] and 0 in axis1["n"]  # a lag without pairs: null, never 0
    assert any(f and n > 0 for f, n in zip(axis1["few"], axis1["n"], strict=True)) and not all(axis1["few"])
    assert all((g is None) == (n == 0) for g, n in zip(axis1["gamma"], axis1["n"], strict=True))
    ns = stored["job_nscore"]
    assert ns["transform"] == "nscore" and ns["sigma2"] == 1.0 and ns["sigma2_z"] != 1.0 and ns["downhole"] is None
    assert [d["axis"] for d in ns["directions"]] == [None, None] and "nscore" in ns["stages"]
    assert set(stored["job_failed"]["error"]) >= {"code", "message", "hint"}
    pv = stored["preview"]
    assert pv["key"] is None and pv["sampled"] is None and "fresh" not in pv and "stages" not in pv
    assert pv["directions"] == job["directions"]
    big = stored["preview_sampled"]
    assert big["sampled"] == {"n": 20_000, "of": 21_000, "seed": big["params"]["seed"]} and big["key"] is None
    ref = stored["preview_refused"]["body"]
    assert ref["code"] == "no_values" and ref["counts"]["used"] == 0 and ref["hint"]
    assert stored["preview_bad_param"]["body"]["param"] == "nlag"
    assert all(math.isfinite(v) for v in _numbers(stored))
    check_models_part(stored)


def check_models_part(stored: dict[str, Any]) -> None:
    """What 16.24–16.25 rely on: the mirror of the plane (a2 — west), the rose with and without a range, the empty
    cells of the map, the fit with `order` and `fixed`, the back-transform, the models and «Перестроить»."""
    m = stored["job_map"]
    assert m["plane"] == {"ang1": 0.0, "ang2": 0.0, "ang3": 0.0} and m["fresh"] is False
    assert set(m["stages"]) == {"read", "trajectories", "fan", "map", "write", "total"}
    fan = m["fan"]["directions"]
    assert [(d["theta"], d["az"]) for d in fan][:4] == [(0.0, 0.0), (30.0, 330.0), (60.0, 300.0), (90.0, 270.0)]
    rays = m["rose"]["rays"]
    assert any(r["range"] is None and r["beyond"] for r in rays) and any(r["range"] is not None for r in rays)
    mp = m["map"]
    assert len(mp["u"]) == 2 * mp["nu"] + 1 and len(mp["gamma"]) == 2 * mp["nv"] + 1
    pairs = zip(mp["gamma"], mp["n"], strict=True)
    cells = [(g, n) for row_g, row_n in pairs for g, n in zip(row_g, row_n, strict=True)]
    assert any(g is None for g, _ in cells) and all((g is None) == (n < mp["min_pairs"]) for g, n in cells)
    fit = stored["fit"]
    assert set(fit) == {"model", "residual", "n", "starts", "warnings", "order", "fixed"}
    assert fit["fixed"] == ["structures.0.a3"]
    assert fit["model"]["structures"][0]["a3"] == stored["bodies_models"]["fit"]["model"]["structures"][0]["a3"]
    assert stored["fit_refused"]["body"]["code"] == "result_not_found"
    back = stored["back"]
    assert set(back) == {"model", "share", "warning", "residual", "curves"}
    assert [c["axis"] for c in back["curves"]] == [1, 2, 3]
    assert stored["back_refused"]["body"]["param"] == "key"
    assert [d["axis"] for d in stored["curve"]["directions"]] == [1, 2, 3]
    assert stored["curve_directions"]["directions"][0]["axis"] is None
    assert stored["model_second"]["name"] == stored["model_created"]["name"] + " (2)"
    assert stored["model_taken"]["status"] == 409 and stored["model_taken"]["body"]["code"] == "name_taken"
    assert stored["model_renamed"]["version"] == 2
    assert stored["model_conflict"]["body"]["code"] == "version_conflict" and stored["model_conflict"]["body"]["hint"]
    assert stored["model_conflict"]["body"]["version"] == 2
    assert stored["export_exists"]["status"] == 409 and stored["export_exists"]["body"]["code"] == "files_exist"
    assert stored["export_replaced"]["bytes"] == stored["export_path"]["bytes"]
    assert stored["import_bad"]["body"]["code"] == "bad_file" and stored["import_bad"]["body"]["line"] == 3
    assert stored["import_gslib"]["model"] == stored["import_json"]["model"] == stored["fit"]["model"]
    row = stored["models_stale"]["models"][0]
    assert row["stale"] and row["stale_reason"] and not row["source_lost"]
    rb = stored["job_rebuild"]
    assert rb["model_id"] == stored["model_created"]["id"] and rb["events"][0][0] == "geostat.changed"
    assert not stored["models_rebuilt"]["models"][0]["stale"]


def _numbers(x: Any) -> Any:
    if isinstance(x, dict | list):
        for v in x.values() if isinstance(x, dict) else x:
            yield from _numbers(v)
    elif isinstance(x, int | float) and not isinstance(x, bool):
        yield float(x)
