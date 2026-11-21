"""The wireframes and the oriented core in the jobs (ADR 0021 §5–6, §8, §10, решения 7–9, 15, 18, 20, 23; подшаг
15.13): Т8.5-13 through the job — the plate of `test_tt_mesh` stored in a project, holes at different angles
through it, TT of the interval of the plate = its thickness (1e−6 m); the stages of the job name the wireframe;
its geometry key is in `inputs` — the plate replaced makes the record «устарел», the rebuild takes the new one; the
preview computes a wireframe of ≤ 2 million triangles and skips a larger one; core measurements of a table
«события» give «керн: замер» (TT = l·sin α_к) and «керн: ближайший»; `POST /orientation/plane` gives the plane of
the plate; the journal warns of a slow d_max. `-m windows`: one hash of TT by the wireframe with 1 and 4 threads;
the job `intercept.run` by the wireframe whole, in a worker."""

from __future__ import annotations

import hashlib
import itertools
import math
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.intercepts import formulas as fm
from bf_core.projects.service import projects_of
from tests.test_composites_jobs import rows_of
from tests.test_import_job import API, wait_job
from tests.test_tt_mesh import ALPHA, ANGLES, CENTRE, DIPDIR, THICK, frame, plate

IC = f"{API}/intercepts"
MODULES = ("bf_core.intercepts.jobs", "bf_core.intercepts.jobs_run")
TT_TOL = 1e-6  # Т8.5-13
WF = "Пластина"
S0 = 70.0  # the depth of the middle plane of the plate along every hole
TD = 150.0
_ids = itertools.count(153_001)
noop = lambda _p=None, _m=None: None  # noqa: E731


def holes_of() -> list[dict[str, Any]]:
    """The holes of ANGLES (zenith, azimuth) through the middle part of the plate: the collar, the direction, the
    depths of the entry and of the exit (by the planes of the faces)."""
    rng = np.random.default_rng(13)
    u, v, n = frame(ALPHA, DIPDIR)
    out = []
    for k, (theta, azimuth) in enumerate(ANGLES):
        through = CENTRE + rng.uniform(-20.0, 20.0) * u + rng.uniform(-20.0, 20.0) * v
        d = fm.hole_direction(theta, azimuth)
        chord = THICK / abs(float(d @ n))
        out.append({"name": f"П-{k + 1}", "collar": through - S0 * d, "theta": theta, "azimuth": azimuth,
                    "in": S0 - 0.5 * chord, "out": S0 + 0.5 * chord})  # fmt: skip
    return out


def put_plate(project: Any, thickness: float = THICK) -> int:
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes import store as ws

    v, t = plate(ALPHA, DIPDIR, thickness, CENTRE)
    with dialect.write_tx(project.engine) as conn:
        return ws.put_wireframe(conn, BlobStore(project.folder), v, t, name=WF)


def write(project: Any, set_id: int, name: str, kind: str, holes: list[str], frm: list[float], to: list[Any],
          cols: list[tuple[str, list[Any]]]) -> str:  # fmt: skip
    from bf_core.drillholes import store as dh

    unit = {"AU": "г/т"}
    data = dh.TableData(name, kind, holes, frm, to, [dh.ColumnSpec(c, "number", unit.get(c)) for c, _v in cols],
                        [v for _c, v in cols])  # fmt: skip
    return str(dh.write_table(project.engine, set_id, data, job_id=None, tick=noop)["table_id"])


@pytest.fixture()
def mj(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """A project with the set «Пластина»: the holes of `holes_of`, the plate as a wireframe; the tables «Пласт»
    (the interval of the plate in every hole), «Пробы» (0 – entry – exit – TD: AU 0,1 / 5 / 0,1), «Метры» (1 m
    from 0 to 100 m; an interval table — a set has one assay table)."""
    import importlib

    from bf_core.drillholes import store as dh
    from bf_core.jobs.registry import HANDLER_MODULES

    extra = [m for m in MODULES if m not in HANDLER_MODULES]
    if extra:
        monkeypatch.setenv("BF_EXTRA_JOB_MODULES", ",".join(extra))
    for m in MODULES:
        importlib.import_module(m)
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    hs = holes_of()
    with dialect.write_tx(project.engine) as conn:
        set_id = dh.create_set(conn, WF)
        dh.replace_collars(conn, set_id, [dh.CollarRow(h["name"], *map(float, h["collar"]), TD, h["azimuth"],
                                                       h["theta"] - 90.0) for h in hs])  # fmt: skip
    wf = put_plate(project)
    names = [h["name"] for h in hs]
    t = {
        "layer": write(project, set_id, "Пласт", "interval", names, [h["in"] for h in hs], [h["out"] for h in hs],
                       [("N", [1.0] * len(hs))]),
        "samples": write(project, set_id, "Пробы", "assay", [n for n in names for _ in range(3)],
                         [x for h in hs for x in (0.0, h["in"], h["out"])],
                         [x for h in hs for x in (h["in"], h["out"], TD)], [("AU", [0.1, 5.0, 0.1] * len(hs))]),
        "metres": write(project, set_id, "Метры", "interval", [n for n in names for _ in range(100)],
                        [float(i) for _ in hs for i in range(100)], [float(i + 1) for _ in hs for i in range(100)],
                        [("AU", [1.0] * 100 * len(hs))]),
    }  # fmt: skip
    return SimpleNamespace(c=pclient, project=project, set_id=set_id, wf=wf, holes=hs, **t)


def mesh(wf: int, **kw: Any) -> dict[str, Any]:
    return {"source": "mesh", "wireframe_ids": [wf], **kw}


def tt_here(project: Any, params: dict[str, Any], said: list[str] | None = None) -> dict[str, Any]:
    from bf_core.intercepts import jobs

    def tick(_p: float | None = None, m: str | None = None) -> None:
        if said is not None and m:
            said.append(m)

    return jobs.run_tt(project.engine, project.folder, params, job_id=next(_ids), tick=tick)


def column(got: dict[str, Any], name: str) -> list[Any]:
    key = next(c["key"] for c in got["columns"] if c["name"] == name)
    return [r[key] for r in got["rows"]]


def by_hole(got: dict[str, Any], name: str) -> dict[str, Any]:
    return {r["hole"]: v for r, v in zip(got["rows"], column(got, name), strict=True)}


def records(c: Any, set_id: int, op: str = "tt") -> list[dict[str, Any]]:
    return [x for x in c.get(f"{IC}/results?set_id={set_id}").json()["results"] if x["op"] == op]


def test_t8_5_13_through_the_job_stages_and_inputs(mj) -> None:  # type: ignore[no-untyped-def]
    said: list[str] = []
    req = {"set_id": mj.set_id, "table": mj.layer, "orientation": [mesh(mj.wf)]}
    out = tt_here(mj.project, {"body": req}, said)
    assert "Пересечения стволов: каркас 1 из 1" in said and "Ближайшие треугольники: каркас 1 из 1" in said
    got = rows_of(mj.c, mj.set_id, mj.layer)
    tt = np.array(column(got, "TT"), dtype=float)
    assert len(tt) == len(ANGLES) and np.abs(tt - THICK).max() <= TT_TOL  # Т8.5-13
    assert set(column(got, "TT_SRC")) == {"каркас: пересечение"} and set(column(got, "TT_FLAG")) == {0.0}
    assert np.allclose(column(got, "ALPHA"), ALPHA, rtol=0.0, atol=1e-8)
    assert np.allclose(column(got, "DIPDIR"), DIPDIR, rtol=0.0, atol=1e-8)
    theta = by_hole(got, "HOLE_THETA")
    for h in mj.holes:
        assert math.isclose(theta[h["name"]], h["theta"], abs_tol=1e-8), h
    rep = out["summary"]
    assert rep["absent"] == [] and rep["counts"]["src"] == {"каркас: пересечение": len(ANGLES)}
    rec = records(mj.c, mj.set_id)[0]
    assert [m["name"] for m in rec["inputs"]["meshes"]] == [WF] and rec["inputs"]["meshes"][0]["key"]
    assert rec["params"]["orientation"] == [{"source": "mesh", "wireframe_ids": [mj.wf], "d_max": 50.0, "r_n": 0.0}]


def test_replaced_wireframe_makes_the_record_stale_and_the_rebuild_takes_it(mj) -> None:  # type: ignore[no-untyped-def]
    from bf_core.wireframes import store as ws

    c = mj.c
    first = tt_here(mj.project, {"body": {"set_id": mj.set_id, "table": mj.layer, "orientation": [mesh(mj.wf)]}})
    assert records(c, mj.set_id)[0]["stale"] is False
    with dialect.write_tx(mj.project.engine) as conn:
        ws.delete_wireframe(conn, mj.wf)
    rec = records(c, mj.set_id)[0]
    assert rec["stale"] is True and rec["reason"] == f"удалён каркас «{WF}»", rec
    wf2 = put_plate(mj.project, thickness=9.0)  # the same name, another geometry and id
    rec = records(c, mj.set_id)[0]
    assert rec["stale"] is True and rec["reason"] == f"изменён каркас «{WF}»", rec
    r = c.post(f"{IC}/results/{first['result_id']}/rebuild")
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=240)
    assert job["status"] == "done", job
    rec = records(c, mj.set_id)[0]
    assert rec["stale"] is False and rec["params"]["orientation"][0]["wireframe_ids"] == [wf2]
    assert job["result"]["fresh"] is False
    got = rows_of(c, mj.set_id, mj.layer)
    tt = np.array(column(got, "TT"), dtype=float)
    length = np.array([r["to"] - r["from"] for r in got["rows"]])
    assert np.abs(tt - THICK).max() <= TT_TOL and (length > THICK).all()  # the interval of the old plate: whole


def test_preview_computes_a_small_wireframe_and_skips_a_large_one(mj, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from bf_core.intercepts import routes_preview
    from tests.test_intercepts_tt_job import hole_id_of

    c = mj.c
    hid = hole_id_of(c, mj.set_id, mj.holes[3]["name"])
    req = {"set_id": mj.set_id, "table": mj.metres, "orientation": [mesh(mj.wf), {"source": "global", "alpha": 10,
           "dipdir": 0}]}  # fmt: skip
    p = c.post(f"{IC}/tt/preview", json={"params": req, "hole_id": hid}).json()
    assert p["mesh_skipped"] is False and {"каркас: пересечение", "каркас: ближайшая", "глобальная"} >= set(
        p["rows"]["TT_SRC"]) >= {"каркас: пересечение", "каркас: ближайшая"}  # fmt: skip
    inside = [tt for tt, s in zip(p["rows"]["TT"], p["rows"]["TT_SRC"], strict=True) if s == "каркас: пересечение"]
    assert inside and math.fsum(inside) > 0
    monkeypatch.setattr(routes_preview, "MESH_TRIANGLES", 10)
    p = c.post(f"{IC}/tt/preview", json={"params": req, "hole_id": hid}).json()
    assert p["mesh_skipped"] is True and set(p["rows"]["TT_SRC"]) == {"глобальная"}
    assert routes_preview.MESH_SKIPPED in p["notes"]
    run = {"set_id": mj.set_id, "table": mj.samples, "field": "AU", "cutoff": 1, "l_min": 0, "min_by": "tt",
           "orientation": [mesh(mj.wf)]}  # fmt: skip
    p = c.post(f"{IC}/run/preview", json={"params": run, "hole_id": hid}).json()
    assert p["mesh_skipped"] is True and p["candidates"][0]["tt"] is None  # no source: TT empty, by the length
    monkeypatch.setattr(routes_preview, "MESH_TRIANGLES", 2_000_000)
    p = c.post(f"{IC}/run/preview", json={"params": run, "hole_id": hid}).json()
    assert p["mesh_skipped"] is False and abs(p["candidates"][0]["tt"] - THICK) <= TT_TOL
    p = c.post(f"{IC}/run/preview", json={"params": {**run, "l_min": THICK + 0.01}, "hole_id": hid}).json()
    assert [(x["accepted"], x["reason"]) for x in p["candidates"]] == [(False, "ИМ меньше m_min")]


def test_core_of_an_events_table(mj) -> None:  # type: ignore[no-untyped-def]
    """α_к, β_к of a table «события»: an interval with a measurement — TT = l·sin α_к («керн: замер»); one within
    d_к of a measurement — its normal («керн: ближайший»; a straight hole — l·sin α_к too); farther — «нет»."""
    c, hs = mj.c, mj.holes
    names = [h["name"] for h in hs]
    alpha_k = [20.0 + 5.0 * k for k in range(len(hs))]
    events = write(mj.project, mj.set_id, "Керн", "event", [n for n in names for _ in range(2)],
                   [x for _ in hs for x in (10.5, 30.5)], [None] * 2 * len(hs),
                   [("ALPHA_K", [a for a in alpha_k for _ in range(2)]), ("BETA_K", [40.0] * 2 * len(hs))])  # fmt: skip
    tens = write(mj.project, mj.set_id, "Десятки", "interval", [n for n in names for _ in range(6)],
                 [float(10 * i) for _ in hs for i in range(6)], [float(10 * i + 10) for _ in hs for i in range(6)],
                 [("N", [1.0] * 6 * len(hs))])  # fmt: skip
    core = {"source": "core", "table": events, "alpha": "ALPHA_K", "beta": "BETA_K", "line": "boh", "d_core": 10}
    tt_here(mj.project, {"body": {"set_id": mj.set_id, "table": tens, "orientation": [core]}})
    got = rows_of(c, mj.set_id, tens)
    want = {(0.0): "керн: ближайший", 10.0: "керн: замер", 20.0: "керн: ближайший", 30.0: "керн: замер",
            40.0: "керн: ближайший", 50.0: "нет"}  # fmt: skip
    for row, tt, src, flag in zip(got["rows"], column(got, "TT"), column(got, "TT_SRC"), column(got, "TT_FLAG"),
                                  strict=True):  # fmt: skip
        k = names.index(row["hole"])
        if hs[k]["theta"] < 1.0:
            continue  # a vertical hole: the measurement is not turned (bit 4)
        assert src == want[row["from"]], (row, src)
        if src == "нет":
            assert tt is None and flag == 2.0
        else:
            assert math.isclose(tt, 10.0 * math.sin(math.radians(alpha_k[k])), rel_tol=1e-9), (row, tt)
    rec = records(c, mj.set_id)
    assert [t["name"] for t in rec[0]["inputs"]["tables"]] == ["Десятки", "Керн"]


def test_orientation_plane_of_the_plate(mj) -> None:  # type: ignore[no-untyped-def]
    c = mj.c
    r = c.post(f"{IC}/orientation/plane", json={"wireframe_ids": [mj.wf]})
    assert r.status_code == 200, r.text
    p = r.json()
    assert math.isclose(p["alpha"], ALPHA, abs_tol=1e-8) and math.isclose(p["dipdir"], DIPDIR, abs_tol=1e-8)
    faces, edges = 2 * 300.0 * 300.0, 4 * 300.0 * THICK  # the faces lie in the plane, the edges across it
    assert math.isclose(p["area"], faces + edges, rel_tol=1e-9)
    assert math.isclose(p["spread"], 90.0 * edges / (faces + edges), rel_tol=1e-6)
    assert p["triangles"] > 0
    for raw, code in (([], "bad_param"), (list(range(1, 18)), "bad_param"), ([10**6], "wireframe_not_found")):
        r = c.post(f"{IC}/orientation/plane", json={"wireframe_ids": raw})
        assert (r.status_code, r.json()["code"], r.json()["param"]) == (422, code, "wireframe_ids"), r.text
        assert r.json()["hint"]


def test_slow_d_max_is_estimated_in_the_journal_and_the_memory() -> None:
    from bf_core.intercepts import jobs

    def spec(d_max: float, triangles: int) -> Any:
        state = SimpleNamespace(triangles=triangles)
        return SimpleNamespace(orientation=[SimpleNamespace(kind="mesh", d_max=d_max, meshes=[(state, None)])])

    assert jobs.mesh_notes(spec(50.0, 10_000_000), 5_000_000) == []  # the default d_max: no warning
    assert jobs.mesh_notes(spec(1000.0, 1000), 100) == []  # quick
    (line,) = jobs.mesh_notes(spec(200.0, 10_000_000), 5_000_000, threads=4)
    assert line.startswith("Каркас: d_max = 200 м на каркасах в 10 000 000 треугольников") and "мкс" in line
    assert jobs.mesh_triangles(spec(200.0, 10_000_000)) == 10_000_000
    base = jobs.memory_mb({"rows": 5_000_000})
    assert jobs.memory_mb({"rows": 5_000_000, "triangles": 10_000_000}) - base == pytest.approx(954, abs=2)


# --- Windows CI (решение 23) -------------------------------------------------------------------------------------


def _digest(t: Any) -> str:
    h = hashlib.sha256()
    for a in (t.tt, t.flag, t.src, t.alpha, t.dipdir, t.theta, t.azimuth):
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


@pytest.mark.windows
def test_one_hash_of_tt_by_the_wireframe_with_1_and_4_threads(mj) -> None:  # type: ignore[no-untyped-def]
    from bf_core.composites import source
    from bf_core.intercepts import jobs, orient_mesh

    req = {"set_id": mj.set_id, "table": mj.metres, "orientation": [mesh(mj.wf, r_n=5)]}
    with dialect.read_tx(mj.project.engine) as conn:
        spec = jobs.parse(conn, req)
    lines, tables = jobs.read(mj.project.engine, spec)
    st = source.trajectories(mj.project, mj.set_id)
    made = [jobs.compute(spec, lines, tables, st, mj.project, mesh=orient_mesh.project_source(mj.project, keep=k),
                         threads=n) for n, k in ((1, True), (4, False))]  # fmt: skip
    assert _digest(made[0]) == _digest(made[1])
    src = set(made[0].src_text())
    assert {"каркас: пересечение", "каркас: ближайшая"} <= src, src


@pytest.mark.windows
def test_intercept_run_by_the_wireframe_in_a_worker(mj) -> None:  # type: ignore[no-untyped-def]
    c = mj.c
    req = {"set_id": mj.set_id, "table": mj.samples, "field": "AU", "cutoff": 1, "l_min": THICK - 0.1, "min_by": "tt",
           "orientation": [mesh(mj.wf)]}  # fmt: skip
    r = c.post(f"{IC}/run", json=req)
    assert r.status_code == 200, r.text
    job = wait_job(c, r.json()["source"], r.json()["id"], timeout=240)
    assert job["status"] == "done", job
    res = job["result"]
    assert res["rows"] == len(ANGLES) and res["summary"]["intercepts"] == len(ANGLES)
    got = rows_of(c, mj.set_id, str(res["table_id"]))
    tt = np.array(column(got, "TT"), dtype=float)
    assert np.abs(tt - THICK).max() <= TT_TOL and set(column(got, "TT_SRC")) == {"каркас: пересечение"}
    assert all(label.endswith(f"(ИМ {THICK:.1f} м)".replace(".", ",")) for label in column(got, "LABEL"))
    rec = records(c, mj.set_id, "intercepts")[0]
    assert rec["stale"] is False and [m["name"] for m in rec["inputs"]["meshes"]] == [WF]
