"""Review of Э12 part 2, the core (подшаг 12.34; `docs/progress/e12-review.md`, «Часть 2» № 3, 4, 5, 8–11): the user's
columns of a result table and ORE_* of a replaced table of composites are listed in `lost`; «Перестроить» after
`.bfggis` finds the wireframes by `inputs.meshes`; the thresholds of the top cut by P come from one sort per table
and domain; a table gone during a job — 409 `source_gone`; the snapshot keys by casefold; `X` and `X_SRC` of a
merge never make two columns of one name; no dead branches in `routes_run`."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_results
from tests.test_composites_prep import demo  # noqa: F401 - the fixture
from tests.test_import_job import API

C = f"{API}/composites"


# --- № 11: no dead branches ----------------------------------------------------------------------------------


def test_routes_run_has_no_dead_branches(demo) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.composites import routes_run

    src = Path(routes_run.__file__).read_text(encoding="utf-8")
    assert "TODO(12.2" not in src and "PREVIEW_OPS" not in src
    with pytest.raises(ApiError) as e:  # the previews of the other ops have their own routes, registered first
        routes_run.preview(None, "ore", {})  # type: ignore[arg-type]
    assert e.value.status == 404 and e.value.code == "unknown_operation"
    c, project, set_id, _table = demo
    with dialect.write_tx(project.engine) as conn:  # an op of a newer build: the fallback refusal
        made = conn.execute(sa.insert(dh_results).values(set_id=set_id, table_id=None, op="newop", name="Новая",
                                                         params={}, inputs={}, sha256="0", algo="x", report={},
                                                         created_at=dialect.utcnow()))  # fmt: skip
    rid = made.inserted_primary_key[0]
    r = c.post(f"{C}/results/{rid}/rebuild", json={})
    assert r.status_code == 422 and r.json()["code"] == "not_ready", r.text
    assert "в этой версии" in r.json()["message"] and r.json()["hint"]


# --- № 8: the table is gone while the job runs ---------------------------------------------------------------


def test_a_table_deleted_during_the_job_is_409_source_gone(demo) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.composites import jobs_prep as jp
    from bf_core.db.schema import dh_tables

    c, project, set_id, table = demo

    def tick(_p: float | None = None, m: str | None = None) -> None:
        if m == "Запись полей":  # read and computed: the table goes before the final transaction
            with dialect.write_tx(project.engine) as conn:
                conn.execute(sa.delete(dh_tables).where(dh_tables.c.id == int(table)))

    req = {"set_id": set_id, "table": table, "field": "AU", "percentile": 99}
    with pytest.raises(ApiError) as e:
        jp.run_prep(project.engine, project.folder, {"body": req}, "topcut", job_id=96_001, tick=tick)
    assert e.value.status == 409 and e.value.code == "source_gone", e.value.code
    assert "«Пробы»" in e.value.message and e.value.hint
    assert c.get(f"{C}/results?set_id={set_id}").json()["results"] == []
