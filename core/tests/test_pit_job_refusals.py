"""The jobs `pit.check` and `pit.run`: the cancel at every stage (nothing is left), the model that changed during the
run, the refusals of the job in Russian that settle the scenario, the check of the template (the prototype of the ТЗ:
K = 8, 17 offsets, 1,44°), the same on PostgreSQL (`-m db`, BF_TEST_PG). The whole run, the independent calculation,
«Перестроить» and Т8.11-9 — `test_pit_job.py`."""

# ruff: noqa: F811 - the fixture `pw` is imported, and the tests take it by its name
from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.jobs.context import JobCancelled, JobError
from bf_core.mine import job as pj
from bf_core.mine import store as st
from bf_core.storage.blobs import BlobStore
from tests.pitjob import SMALL, Cancel, columns, counts, direct, field_of, info, rebuild, refused, sha_of, tmp_files
from tests.pitworld import blocks, make_model, new, params, pw  # noqa: F401 - the fixture
from tests.test_blocks_fields_store import add_fields

# --- the cancel, the refusals ---------------------------------------------------------------------------------------


def test_cancel_at_every_stage_leaves_nothing_of_a_new_result(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL, seed=5)
    sid = new(pw, mid)["id"]
    before = counts(pw)
    for tick in (Cancel("Чтение"), Cancel("Экономика"), Cancel("Шаблон"), Cancel("Граф"), Cancel("RF_max"),
                 Cancel("Оболочки"), Cancel("Отчёт"), Cancel("Запись: поля"), Cancel("Запись: запись"),
                 Cancel(nth=2), Cancel(nth=30)):  # fmt: skip
        with pytest.raises(JobCancelled) as e:
            direct(pw, sid, tick=tick, chunk=50)
        assert e.value.events == [["pit.changed", {"kind": "pit_scenario", "id": sid}]]
        got = info(pw, sid)
        assert got["state"] == "ready" and got["has_result"] is False and got["version"] == 1, tick.at
        assert counts(pw) == before and field_of(pw, mid, "PIT") is None, tick.at  # no files, no rows, no fields
        assert tmp_files(pw) == [], tick.at  # the staged files went
    assert direct(pw, sid, chunk=50)["version"] == 2 and info(pw, sid)["has_result"]  # and it can still be made


def test_cancel_of_a_rebuild_leaves_the_result_as_it_was(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL, seed=5)
    sid = new(pw, mid)["id"]
    direct(pw, sid)
    sha, rows, pit = sha_of(pw, sid), counts(pw), np.asarray(columns(pw, mid, "PIT")["PIT"]).tobytes()
    for tick in (Cancel("Экономика"), Cancel("Оболочки"), Cancel("Запись: запись")):
        with pytest.raises(JobCancelled):
            rebuild(pw, sid, tick)
        got = info(pw, sid)
        assert got["state"] == "ready" and got["version"] == 2 and got["has_result"] and sha_of(pw, sid) == sha
        assert counts(pw) == rows and tmp_files(pw) == []
        assert np.asarray(columns(pw, mid, "PIT")["PIT"]).tobytes() == pit


def test_a_model_that_changed_during_the_run_is_refused_and_nothing_stays(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL, seed=5)
    sid = new(pw, mid)["id"]
    before = counts(pw)

    def tick(_p: float | None = None, m: str | None = None) -> None:
        if m and m.startswith("Запись: запись"):
            with dialect.write_tx(pw.project.engine) as conn:
                conn.execute(sa.update(objects).where(objects.c.id == mid).values(version=objects.c.version + 1))

    err = refused(lambda: direct(pw, sid, tick=tick), 409, "model_changed")
    assert "Повторите" in err.hint
    got = info(pw, sid)
    assert got["state"] == "error" and got["error"]["code"] == "model_changed" and not got["has_result"]
    assert counts(pw) == before and field_of(pw, mid, "PIT") is None and tmp_files(pw) == []


def test_refusals_of_the_job_are_in_russian_and_settle_the_scenario(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL, seed=5)
    sid = new(pw, mid)["id"]
    ok_params = params(mid)
    before = counts(pw)

    def go(p: dict[str, Any], status: int, code: str) -> ApiError:
        return refused(lambda: direct(pw, sid, p), status, code)

    nothing = go({**ok_params, "elements": [{"field": "AU", "unit": "g/t"}]}, 422, "pit_not_ready")  # no price
    assert nothing.message.startswith("Сценарий не готов к расчёту")
    assert info(pw, sid)["state"] == "error" and info(pw, sid)["error"]["code"] == "pit_not_ready"
    go({**ok_params, "model_id": None}, 422, "pit_not_ready")
    go({**ok_params, "model_id": 9999}, 404, "block_model_not_found")
    go({**ok_params, "density": "NOPE"}, 404, "block_field_not_found")
    go({**ok_params, "terrain_id": 9999}, 404, "wireframe_not_found")
    go({**ok_params, "filter_id": 9999}, 404, "filter_not_found")
    go({**ok_params, "restrict": {"cond": {"field": "NOFIELD", "op": ">", "value": 1}}}, 422, "filter_field_missing")
    price = {**ok_params, "elements": [{"field": "AU", "unit": "g/t", "price": 0.5, "sales": 0.0}]}
    go(price, 422, "pit_empty")  # nothing pays at the largest RF
    assert not info(pw, sid)["has_result"] and counts(pw) == before and field_of(pw, mid, "PIT") is None
    ok_run = direct(pw, sid, ok_params)  # and after all of it the scenario runs
    assert ok_run["version"] == 2 and info(pw, sid)["state"] == "ready" and info(pw, sid)["error"] is None
    # a running scenario is not started twice; a stale version is a conflict
    with dialect.write_tx(pw.project.engine) as conn:
        st.mark_running(conn, sid, None)
    go(ok_params, 409, "pit_running")
    assert info(pw, sid)["state"] == "running"  # the refusal did not take the mark of the other run away
    with dialect.write_tx(pw.project.engine) as conn:
        st.settle(conn, sid)
    refused(lambda: pj.run_scenario(pw.project.engine, pw.project.folder,
                                    {"scenario_id": sid, "params": ok_params, "expected_version": 1}),
            409, "version_conflict")  # fmt: skip
    refused(lambda: pj.run_scenario(pw.project.engine, pw.project.folder, {"scenario_id": 9999, "params": ok_params}),
            404, "pit_scenario_not_found")  # fmt: skip


def test_the_density_is_asked_for_when_blocks_have_none(pw) -> None:  # type: ignore[no-untyped-def]
    b = blocks(SMALL, seed=5)
    b["SG"][[5, 77, 100]] = np.nan
    from tests.pitworld import FIELDS, grid_of
    from tests.test_blocks_report import put

    mid = put(pw, grid_of(SMALL), FIELDS, b["p"], {k: v for k, v in b.items() if k != "p"}, name="Без плотности")
    sid = new(pw, mid)["id"]
    refused(lambda: direct(pw, sid), 422, "pit_density_missing")
    out = direct(pw, sid, {**params(mid), "default_density": 2.7})
    assert out["control"]["default_density"]["blocks"] == 3
    assert any("нет плотности" in w["message"] for w in out["warnings"])


def test_a_variable_grid_is_refused_with_the_advice_to_regularise(pw) -> None:  # type: ignore[no-untyped-def]
    from tests.test_blocks_report import put

    grid = {"corner": [0.0, 0.0, 0.0], "counts": [2, 1, 1], "tensors": {"x": [10.0, 20.0], "y": [10.0], "z": [10.0]}}
    fields = [{"name": "AU", "dtype": "f8", "unit": "g/t", "role": "grade"}, {"name": "SG", "dtype": "f8"}]
    mid = put(pw, grid, fields, np.arange(2, dtype=np.uint64), {"AU": np.array([1.0, 2.0]), "SG": np.array([2.7, 2.7])})
    sid = new(pw, mid)["id"]
    e = refused(lambda: direct(pw, sid), 422, "variable_grid")
    assert "Регуляризуйте" in e.hint
    refused(lambda: pj.run_check(pw.project.engine, pw.project.folder, {"params": params(mid)}), 422, "variable_grid")


def test_fields_of_another_source_are_never_replaced_and_a_prefix_helps(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL, seed=5)
    n = math.prod(SMALL)
    src = {"op": "calc", "params": {}, "inputs": [], "algo": "calc-1"}
    add_fields(pw.project, pw.store, mid, [{"name": "PIT", "dtype": "f8"}], {"PIT": np.ones(n)}, source=src)
    sid = new(pw, mid)["id"]
    e = refused(lambda: direct(pw, sid), 409, "pit_fields_taken")
    assert "префикс" in e.hint
    assert np.asarray(columns(pw, mid, "PIT")["PIT"]).tolist() == [1.0] * n  # untouched
    out = direct(pw, sid, {**params(mid), "prefix": "A_"})
    assert out["outputs"]["fields"] == ["A_PIT", "A_ORE"] and field_of(pw, mid, "A_PIT")["source"]["scenario_id"] == sid
    again = direct(pw, sid, {**params(mid), "prefix": "A_"})  # its own fields are replaced in place
    assert again["version"] == 3 and np.asarray(columns(pw, mid, "A_PIT")["A_PIT"]).max() == 4


def test_the_handlers_turn_a_refusal_into_a_job_error(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL, seed=5)
    sid = new(pw, mid)["id"]
    ctx = SimpleNamespace(params={"scenario_id": sid, "params": params(mid), "expected_version": 7},
                          project_folder=pw.project.folder, id=5, engine=lambda: pw.project.engine,
                          tick=lambda *_a: None)  # fmt: skip
    with pytest.raises(JobError) as e:
        pj.run_job(ctx)  # type: ignore[arg-type]
    assert e.value.code == "version_conflict" and e.value.message and e.value.hint and not e.value.retry
    assert e.value.events == [["pit.changed", {"kind": "pit_scenario", "id": sid}]]
    ctx2 = SimpleNamespace(params={"params": params(None)}, project_folder=pw.project.folder, id=6,
                           engine=lambda: pw.project.engine, tick=lambda *_a: None)  # fmt: skip
    with pytest.raises(JobError) as e2:
        pj.check_job(ctx2)  # type: ignore[arg-type]
    assert e2.value.code == "bad_param" and "модель" in e2.value.message


# --- pit.check ----------------------------------------------------------------------------------------------------


def test_the_check_of_the_template_is_the_prototype_of_the_spec(pw) -> None:  # type: ignore[no-untyped-def]
    mid = make_model(pw, SMALL, seed=5)
    stages: list[str] = []
    out = pj.run_check(pw.project.engine, pw.project.folder, {"params": {"model_id": mid}},
                       lambda _p, m: m and stages.append(m.split(":")[0]))  # fmt: skip
    assert [x for x in dict.fromkeys(stages)] == ["Шаблон", "Контроль угла"]
    assert out["model"]["id"] == mid and out["model"]["grid"]["counts"] == list(SMALL)
    assert out["k"] == 8 and out["k_auto"] is True and out["tolerance"] == 2.5 and out["levels"] == 8
    d = out["domains"][0]  # ТЗ 8.11.2: 45°, cubes, K = 8 — 17 offsets, 1,44°
    assert d["label"] is None and d["n_offsets"] == len(d["offsets"]) == 17 and d["warning"] is False
    assert d["error"]["max"] == pytest.approx(1.44, abs=0.01) and 0.0 < d["error"]["mean"] <= d["error"]["max"]
    assert d["phi_eff"]["az"] == list(range(360)) and len(d["phi_eff"]["angle"]) == 360
    assert d["table"] == [{"az": 0.0, "angle": 45.0}] and out["warnings"] == [] and out["seconds"] >= 0
    # a larger error than the tolerance is a warning (not a refusal)
    warn = pj.run_check(pw.project.engine, pw.project.folder,
                        {"params": {"model_id": mid, "k": 2, "slopes": {"tolerance": 0.5}}})  # fmt: skip
    assert warn["domains"][0]["warning"] is True and warn["warnings"][0]["code"] == "angle_error"
    assert warn["warnings"][0]["param"] == "slopes" and warn["warnings"][0]["hint"]
    refused(lambda: pj.run_check(pw.project.engine, pw.project.folder, {"params": {"model_id": 9999}}), 404,
            "block_model_not_found")  # fmt: skip
    refused(lambda: pj.run_check(pw.project.engine, pw.project.folder, {"params": {}}), 422, "bad_param")


# --- PostgreSQL (BF_TEST_PG; SQLite always) --------------------------------------------------------------------------


@pytest.mark.db
def test_the_job_on_both_databases(db_url: str, tmp_path: Path) -> None:
    from bf_core.db import migrate
    from tests.dbutil import make_engine
    from tests.pitworld import FIELDS, grid_of
    from tests.test_blocks_report import put

    engine = make_engine(db_url, tmp_path)
    try:
        migrate.upgrade(engine, "project")
        project = SimpleNamespace(engine=engine, uid=f"test:{tmp_path}:{db_url}", folder=tmp_path)
        c = SimpleNamespace(project=project, store=BlobStore(tmp_path))
        b = blocks(SMALL, seed=5)
        mid = put(c, grid_of(SMALL), FIELDS, b["p"], {k: v for k, v in b.items() if k != "p"})
        with dialect.write_tx(engine) as conn:
            sid = st.create(conn, "Сценарий", params(mid))["id"]
        first = direct(c, sid)
        assert first["version"] == 2 and first["shells"][-1]["blocks"] > 0
        sha = sha_of(c, sid)
        again = rebuild(c, sid, chunk=7)
        assert again["version"] == 3 and sha_of(c, sid) == sha and field_of(c, mid, "PIT")["source"]["op"] == "pit"
        with pytest.raises(JobCancelled):
            rebuild(c, sid, Cancel("Запись: запись"))
        assert info(c, sid)["state"] == "ready" and sha_of(c, sid) == sha
    finally:
        engine.dispose()
