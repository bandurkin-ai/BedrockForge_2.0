"""Review of Э12 part 2, the core (подшаг 12.34; `docs/progress/e12-review.md`, «Часть 2» № 3, 4, 5, 8–11): the user's
columns of a result table and ORE_* of a replaced table of composites are listed in `lost`; «Перестроить» after
`.bfggis` finds the wireframes by `inputs.meshes`; the thresholds of the top cut by P come from one sort per table
and domain; a table gone during a job — 409 `source_gone`; the snapshot keys by casefold; `X` and `X_SRC` of a
merge never make two columns of one name; no dead branches in `routes_run`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
        rid = conn.execute(sa.insert(dh_results).values(set_id=set_id, table_id=None, op="newop", name="Новая",
                                                        params={}, inputs={}, sha256="0", algo="x", report={},
                                                        created_at=dialect.utcnow())).inserted_primary_key[0]  # fmt: skip
    r = c.post(f"{C}/results/{rid}/rebuild", json={})
    assert r.status_code == 422 and r.json()["code"] == "not_ready", r.text
    assert "в этой версии" in r.json()["message"] and r.json()["hint"]
