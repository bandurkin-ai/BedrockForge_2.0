"""`blocks.check` (подшаг 17.41; docs/progress/e17-api.md §11): the checks of a stored model by the job, `meta.checks`
without a new version, the FRAC of another file in the volume, a cancel, a stale version, the route."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import check_job, checks
from bf_core.blocks.model import Model
from bf_core.db import dialect, migrate
from bf_core.db.schema import objects
from bf_core.jobs.context import JobCancelled
from tests import test_blocks_ops_api as api
from tests.dbutil import make_engine
from tests.test_blocks_fields_store import add_fields, make_model
from tests.test_blocks_fill_jobs import P, tmp_clean
from tests.test_blocks_ops import ticker
from tests.test_blocks_ops_api import BL, done, refused, setup_core

CELL = 5.0 * 5.0 * 2.5  # the volume of a block of `test_blocks_fields_store.GRID`


@pytest.fixture()
def pm(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    p = P(engine, tmp_path)
    p.model_id = make_model(p, p.store)  # type: ignore[attr-defined]
    yield p
    engine.dispose()


def opened(p: Any, mid: int | None = None) -> Model:
    with dialect.read_tx(p.engine) as conn:
        return Model.open(conn, p.store, mid or p.model_id)


def frac_values(n: int = 350) -> np.ndarray:
    f = np.round(np.random.default_rng(3).random(n), 3)
    f[::9] = np.nan
    return f


def add_frac(p: Any) -> np.ndarray:
    frac = frac_values()
    add_fields(p, p.store, p.model_id, [{"name": "ZF", "dtype": "f8", "role": "frac"}], {"ZF": frac})
    return frac


def test_the_frac_of_another_file_is_read_for_the_volume(pm: P) -> None:
    frac = add_frac(pm)
    m = opened(pm)
    assert m.field("ZF")["file"] != "cells"  # kept beside the cells, as `blocks.code` with split=false writes it
    report = checks.check_cells(pm.store, m.shas(), m.meta)
    assert report.checks["volume"] is not None
    assert report.checks["volume"] == pytest.approx(CELL * float(np.nansum(frac)), rel=1e-12)
    plain = checks.check_cells(pm.store, m.shas(), {**m.meta, "fields": [f for f in m.fields if f["name"] != "ZF"]})
    assert plain.checks["volume"] == pytest.approx(CELL * m.rows, rel=1e-12)  # no FRAC: the whole blocks


def test_the_job_writes_the_checks_and_keeps_the_version(pm: P) -> None:
    add_frac(pm)
    before = opened(pm)
    assert before.meta["checks"] is None
    ticks: list[Any] = []
    out = check_job.run_check(pm.engine, pm.folder, {"model_id": pm.model_id}, tick=ticker(ticks))
    after = opened(pm)
    assert after.version == before.version and after.meta["checks"] == out["checks"]
    assert out["model_id"] == pm.model_id
    assert out["events"] == [["objects.changed", {"kind": "block_model", "id": pm.model_id}]]
    assert {"at", "duplicates", "overlaps", "empty", "constant", "volume", "examples"} <= set(out["checks"])
    assert out["checks"]["duplicates"] == 0 and out["checks"]["overlaps"]["n"] == 0
    assert any(m and m.startswith("Проверка:") for m in ticks) and ticks[-1] == "Готово"
    again = check_job.run_check(pm.engine, pm.folder, {"model_id": pm.model_id})  # made again, the version stays
    assert opened(pm).version == before.version and again["checks"]["volume"] == out["checks"]["volume"]
    with pytest.raises(ApiError) as e:
        check_job.run_check(pm.engine, pm.folder, {"model_id": 987654})
    assert e.value.code == "block_model_not_found"


def test_a_cancel_writes_nothing_and_a_model_changed_meanwhile_is_a_conflict(pm: P) -> None:
    seen: list[Any] = []
    check_job.run_check(pm.engine, pm.folder, {"model_id": pm.model_id}, tick=ticker(seen))
    with dialect.write_tx(pm.engine) as conn:  # forget the checks: the cancels below must leave them forgotten
        meta = dict(conn.execute(sa.select(objects.c.meta).where(objects.c.id == pm.model_id)).scalar_one())
        conn.execute(sa.update(objects).where(objects.c.id == pm.model_id).values(meta={**meta, "checks": None}))
    for at in range(1, len(seen)):  # the last tick is after the commit
        with pytest.raises(JobCancelled):
            check_job.run_check(pm.engine, pm.folder, {"model_id": pm.model_id}, tick=ticker([], at))
        assert opened(pm).meta["checks"] is None and tmp_clean(pm), at

    def change(_p: float | None = None, m: str | None = None) -> None:
        if m == "Запись":
            add_fields(pm, pm.store, pm.model_id, [{"name": "LATE", "dtype": "f8"}], {"LATE": np.arange(350.0)})

    with pytest.raises(ApiError) as e:
        check_job.run_check(pm.engine, pm.folder, {"model_id": pm.model_id}, tick=change)
    assert e.value.status == 409 and e.value.code == "version_conflict" and e.value.extra["version"] == 2
    assert opened(pm).meta["checks"] is None  # the model of version 1 was checked: nothing is written over version 2


def test_the_route_and_the_job(pclient, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(api, "MODULES", api.MODULES + ",bf_core.blocks.check_job")  # until the orchestrator's line
    oc = setup_core(pclient, monkeypatch, "Проверка")
    version = opened(oc.p, oc.mid).version
    assert opened(oc.p, oc.mid).meta["checks"] is None
    out = done(oc, oc.post(f"{BL}/{oc.mid}/check", json={}))
    assert out["model_id"] == oc.mid and out["checks"]["duplicates"] == 0
    got = opened(oc.p, oc.mid)
    assert got.version == version and got.meta["checks"]["at"] == out["checks"]["at"]
    assert oc.get(f"{BL}/{oc.mid}").json()["checks"]["volume"] == out["checks"]["volume"]
    refused(oc.post(f"{BL}/987654/check", json={}), 404, "block_model_not_found")
    oc.project.read_only = True
    try:
        refused(oc.post(f"{BL}/{oc.mid}/check", json={}), 423, "project_read_only")
    finally:
        oc.project.read_only = False


@pytest.mark.db
def test_the_job_on_both_databases(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    p = P(engine, tmp_path)
    try:
        p.model_id = make_model(p, p.store)  # type: ignore[attr-defined]
        add_frac(p)
        out = check_job.run_check(engine, p.folder, {"model_id": p.model_id})
        m = opened(p)
        assert m.version == 2 and m.meta["checks"] == out["checks"] and out["checks"]["volume"] > 0
        with pytest.raises(JobCancelled):
            check_job.run_check(engine, p.folder, {"model_id": p.model_id}, tick=ticker([], 3))
        assert opened(p).meta["checks"] == out["checks"]
    finally:
        engine.dispose()
