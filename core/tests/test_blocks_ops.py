"""The jobs of the operations on a block model: `blocks.prototype`, `blocks.regularize` (подшаг 17.29; ТЗ 8.7.3, 8.7.5;
ADR 0023 §6, §9; docs/progress/e17-api.md §6, §9, §15).

A chain on a synthetic model — a prototype, the fill by a body, the regularization — with the totals kept (Т8.7-7),
the stages, the events, the source and «устарел» of the new model, «Перестроить» in place, a cancel at any tick leaves
neither a file nor a row, the same on PostgreSQL (`-m db`, BF_TEST_PG). The jobs of the fields —
`test_blocks_ops_fields.py`, the routes — `test_blocks_ops_api.py`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import jobs as bj
from bf_core.blocks import regularize as rg
from bf_core.blocks.fill import commit, inputs
from bf_core.blocks.fill import jobs as fj
from bf_core.blocks.model import Model
from bf_core.db import dialect, migrate
from bf_core.db.schema import blob_owners, objects
from bf_core.jobs.context import JobCancelled
from tests.dbutil import make_engine
from tests.test_blocks_fill_jobs import (
    P,
    add_field,
    block_models,
    body,
    info,
    model,
    params,
    plain_model,
    prototype,
    request,
    tmp_clean,
)

ORE = ((12.5, 12.5, 7.5), (37.5, 32.5, 27.5))  # on the sub-cells (5 m): 25 × 20 × 20 = 10 000 m³


@pytest.fixture()
def proj(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    yield P(engine, tmp_path)
    engine.dispose()


def ticker(seen: list[Any], cancel_at: int | None = None) -> Any:
    def tick(_p: float | None = None, m: str | None = None) -> None:
        seen.append(m)
        if cancel_at is not None and len(seen) >= cancel_at:
            raise JobCancelled()

    return tick


def stages_of(seen: list[Any]) -> list[str]:
    return [m for k, m in enumerate(seen) if m is not None and m not in seen[:k]]


def filled(p: P) -> int:
    """A model filled by one body: the rows of the fill with sub-blocks (div 2), CODE and FRAC."""
    proto = prototype(p)
    wid = body(p, *ORE, name="Руда")
    return int(fj.run_fill(p.engine, p.folder, params(request(proto, wid)))["model_id"])


def reg_params(model_id: int, size: Any = (20.0, 20.0, 20.0), **kw: Any) -> dict[str, Any]:
    return {"model_id": model_id, "size": list(size), **kw}


def owners(p: P, mid: int) -> set[str]:
    with dialect.read_tx(p.engine) as conn:
        return set(conn.execute(sa.select(blob_owners.c.role).where(blob_owners.c.owner_id == mid)).scalars())


# --- the prototype ----------------------------------------------------------------------------------------------------


def test_the_prototype_job_makes_an_empty_model_with_the_planned_grid(proj: P) -> None:
    req = {"size": [10, 10, "5,0"], "extent": [0, 0, 0, 55, 45, 20], "pad": 5, "rotation": 30, "levels": [1, 1, 0],
           "full": True, "name": "Сетка", "folder": None}  # fmt: skip
    plan = bj.plan_of(req)
    seen: list[Any] = []
    out = bj.run_prototype(proj.engine, proj.folder, req, tick=ticker(seen))
    assert stages_of(seen) == ["Охват", "Запись"] and set(out["stages"]) == {"extent", "write", "total"}
    assert out["name"] == "Сетка" and out["parents"] == plan.parents and out["grid"] == plan.grid.to_meta()
    assert out["events"] == [["objects.changed", {"kind": "block_model", "id": out["model_id"]}]]
    m = model(proj, out["model_id"])
    assert (m.rows, m.parents, m.full, m.meta["source"]["op"]) == (0, 0, True, "prototype")
    assert m.grid.rotation == 30.0 and m.grid.sub.div == (2, 2, 1) and m.grid.cells == plan.parents
    assert m.meta["source"]["params"]["levels"] == [1, 1, 0] and tmp_clean(proj)
    again = bj.run_prototype(proj.engine, proj.folder, {**req, "name": None})  # the default name, free
    assert again["name"] == "Прототип"
    with pytest.raises(ApiError) as e:
        bj.run_prototype(proj.engine, proj.folder, {**req, "size": [0, 1, 1]})
    assert (e.value.status, e.value.code, e.value.extra["param"]) == (422, "bad_param", "size")
    assert block_models(proj) == sorted([out["model_id"], again["model_id"]])


def test_cancel_of_the_prototype_leaves_nothing(proj: P) -> None:
    req = {"size": [10, 10, 10], "extent": [0, 0, 0, 50, 50, 50]}
    for at in (1, 2):
        with pytest.raises(JobCancelled):
            bj.run_prototype(proj.engine, proj.folder, req, tick=ticker([], at))
    assert block_models(proj) == [] and tmp_clean(proj)


# --- the chain: a prototype, the fill, the regularization -------------------------------------------------------------


def test_the_chain_prototype_fill_regularize_keeps_the_volume(proj: P) -> None:
    mid = filled(proj)
    src = model(proj, mid)
    assert src.sub and src.rows > src.parents
    seen: list[Any] = []
    out = bj.run_regularize(proj.engine, proj.folder, reg_params(mid, name="Регулярная"), tick=ticker(seen))
    assert stages_of(seen) == ["Чтение", "Перекрытия", "Запись"]
    assert set(out["stages"]) == {"read", "overlap", "write", "total"}
    assert out["name"] == "Регулярная" and out["warnings"] == []
    assert out["events"] == [["objects.changed", {"kind": "block_model", "id": out["model_id"]}]]
    new = model(proj, out["model_id"])
    assert (new.rows, new.sub, new.version, new.grid.sub.sub) == (out["rows"], False, 1, False)
    assert new.grid.size == (20.0, 20.0, 20.0) and new.grid.rotation == 0.0
    assert new.meta["source"]["op"] == "regularize" and new.meta["algo"] == rg.ALGO
    assert new.meta["source"]["params"]["size"] == [20.0, 20.0, 20.0]
    assert [f["name"] for f in new.fields] == ["CODE", "P_Руда", "FRAC"]
    # the volume of the body survives (Т8.7-7): before = the rows of the fill, after = Σ cell volume · FRAC
    before, after = out["totals"]["before"]["volume"], out["totals"]["after"]["volume"]
    assert before == pytest.approx(10000.0) and after == pytest.approx(before, rel=1e-12)
    vol = new.volume()
    assert float(np.nansum(vol)) == pytest.approx(10000.0, rel=1e-12)
    # the same cells as the kernel gives (the job adds nothing, takes nothing)
    with dialect.read_tx(proj.engine) as conn:
        mm = Model.open(conn, proj.store, mid)
    ref = rg.regularize(mm, proj.store, (20.0, 20.0, 20.0), threads=1)
    try:
        assert ref.rows == new.rows
    finally:
        ref.staged.discard()
    with dialect.read_tx(proj.engine) as conn:
        assert inputs.stale_reason(conn, new.meta["inputs"]) is None
        assert new.meta["inputs"] == {"model": [{"id": mid, "sha": inputs.model_sha(conn, mid)}]}
    assert tmp_clean(proj)


def test_the_regularized_model_is_stale_when_the_source_changes(proj: P) -> None:
    mid = filled(proj)
    out = bj.run_regularize(proj.engine, proj.folder, reg_params(mid))
    rid = out["model_id"]

    def reason() -> str | None:
        with dialect.read_tx(proj.engine) as conn:
            m = Model.open(conn, proj.store, rid)
            return inputs.stale_reason(conn, m.meta["inputs"], inputs.names_in(m.meta))

    assert reason() is None
    add_field(proj, mid, "X", np.zeros(model(proj, mid).rows))  # a field of the source: its files changed
    assert reason() == "изменена модель «Прототип (заполнение)»"
    with dialect.write_tx(proj.engine) as conn:
        conn.execute(sa.delete(objects).where(objects.c.id == mid))
    assert reason() == "удалена модель «Прототип (заполнение)»"  # the name was kept in `source.params.names`


def test_rebuild_of_a_regularized_model_in_place(proj: P) -> None:
    mid = filled(proj)
    first = bj.run_regularize(proj.engine, proj.folder, reg_params(mid, name="Регулярная"))
    rid = first["model_id"]
    ids = block_models(proj)
    add_field(proj, rid, "X", np.zeros(model(proj, rid).rows))  # a field of another maker
    assert model(proj, rid).version == 2
    old = model(proj, rid)
    p = {**reg_params(mid, **{k: old.meta["source"]["params"][k] for k in ("grades", "density", "categories")}),
         "size": old.meta["source"]["params"]["size"], "rebuild": {"model_id": rid, "expected_version": 2}}  # fmt: skip
    out = bj.run_regularize(proj.engine, proj.folder, p)
    new = model(proj, rid)
    assert (out["model_id"], out["version"], out["dropped"], out["name"]) == (rid, 3, ["X"], "Регулярная")
    assert new.version == 3 and "X" not in {f["name"] for f in new.fields} and new.meta["algo"] == rg.ALGO
    assert new.meta["source"]["op"] == "regularize" and new.rows == first["rows"]
    assert block_models(proj) == ids and tmp_clean(proj)
    assert owners(proj, rid) == {"cells"}
    with pytest.raises(ApiError) as e:  # the version moved since
        bj.run_regularize(proj.engine, proj.folder, p)
    assert (e.value.status, e.value.code, e.value.extra) == (409, "version_conflict", {"version": 3})
    with dialect.write_tx(proj.engine) as conn:
        conn.execute(sa.delete(objects).where(objects.c.id == rid))
    with pytest.raises(ApiError) as e2:  # the model to rewrite is gone
        bj.run_regularize(proj.engine, proj.folder, p)
    assert (e2.value.status, e2.value.code) == (409, "source_lost")


def test_the_refusals_of_the_regularization_leave_nothing(proj: P) -> None:
    mid = filled(proj)
    plain = plain_model(proj)
    proto = prototype(proj, counts=(2, 2, 2))
    cases = [
        (reg_params(mid, (0.0, 1.0, 1.0)), 422, "bad_param", "size"),
        (reg_params(mid, grades=[{"field": "NOPE"}]), 404, "block_field_not_found", None),
        (reg_params(mid, grades=[{"field": "FRAC"}]), 422, "bad_param", "grades.0.field"),
        (reg_params(plain, grades=[{"field": "AU", "weight": "tonnes"}]), 422, "bad_param", "density"),
        (reg_params(proto), 422, "block_model_empty", None),
        (reg_params(777_777), 404, "block_model_not_found", None),
    ]
    before = block_models(proj)
    for p, status, code, param in cases:
        with pytest.raises(ApiError) as e:
            bj.run_regularize(proj.engine, proj.folder, p)
        assert (e.value.status, e.value.code) == (status, code), p
        assert param is None or e.value.extra.get("param") == param
    assert block_models(proj) == before and tmp_clean(proj)


def test_cancel_of_the_regularization_at_any_tick_leaves_nothing(proj: P) -> None:
    mid = filled(proj)
    before = block_models(proj)
    files = owners(proj, mid)
    total: list[Any] = []
    bj.run_regularize(proj.engine, proj.folder, reg_params(mid), tick=ticker(total), chunk=64)
    first = block_models(proj)
    assert len(first) == len(before) + 1 and len(total) >= 5
    for at in (1, 2, 3, len(total) // 2, len(total) - 1, len(total)):
        with pytest.raises(JobCancelled):
            bj.run_regularize(proj.engine, proj.folder, reg_params(mid), tick=ticker([], at), chunk=64)
        assert block_models(proj) == first and tmp_clean(proj), at
    assert owners(proj, mid) == files and model(proj, mid).version == 1


def test_the_threads_and_the_portions_do_not_change_the_bits(proj: P) -> None:
    mid = filled(proj)
    a = bj.run_regularize(proj.engine, proj.folder, reg_params(mid, (15.0, 20.0, 10.0), name="a"), threads=1)
    b = bj.run_regularize(proj.engine, proj.folder, reg_params(mid, (15.0, 20.0, 10.0), name="b"), threads=4,
                          chunk=50)  # fmt: skip
    ma, mb = model(proj, a["model_id"]), model(proj, b["model_id"])
    names = [f["name"] for f in ma.fields]
    assert names == [f["name"] for f in mb.fields] and np.array_equal(ma.p(), mb.p())
    ca, cb = ma.columns(names), mb.columns(names)
    assert all(np.array_equal(ca[n], cb[n], equal_nan=True) for n in names)
    assert a["totals"] == b["totals"]


def test_the_memory_estimate_follows_the_rows() -> None:
    assert bj.memory_mb({"rows": 0}) == 768 and bj.memory_mb({"rows": 2_000_000}) == 1018
    assert bj.memory_mb({"rows": 20_000_000}) == 3072 and bj.memory_mb({"rows": 10**9}) == 3072


def test_the_warm_up_regularizes_a_tiny_model_in_a_temporary_project() -> None:
    out = bj.warm()
    assert set(out) == {"regularize"} and out["regularize"] > 0.0


def test_commit_of_a_regularized_model_takes_the_name_of_the_source(proj: P) -> None:
    mid = filled(proj)
    out = bj.run_regularize(proj.engine, proj.folder, reg_params(mid))
    assert out["name"] == "Прототип (заполнение) (регуляризация)"
    assert info(proj, out["model_id"])["source"]["op"] == "regularize"
    assert commit.source_lost("x").code == "source_lost"


@pytest.mark.db
def test_the_chain_on_both_databases(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    p = P(engine, tmp_path / "blobs")
    p.store.tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        proto = bj.run_prototype(engine, p.folder, {"size": [10, 10, 10], "extent": [0, 0, 0, 60, 50, 40],
                                                    "levels": [1, 1, 1]})  # fmt: skip
        wid = body(p, *ORE, name="Руда")
        mid = fj.run_fill(engine, p.folder, params(request(proto["model_id"], wid)))["model_id"]
        out = bj.run_regularize(engine, p.folder, reg_params(mid, name="Регулярная"))
        assert out["totals"]["after"]["volume"] == pytest.approx(10000.0, rel=1e-12)
        add_field(p, mid, "X", np.zeros(model(p, mid).rows))
        with dialect.read_tx(engine) as conn:
            m = Model.open(conn, p.store, out["model_id"])
            assert inputs.stale_reason(conn, m.meta["inputs"]) == "изменена модель «Прототип (заполнение)»"
    finally:
        engine.dispose()
