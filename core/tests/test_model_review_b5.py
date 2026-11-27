"""The review of the milestone В5, group 3 (Э19 / Э19б): the inputs of a stored implicit model.

A shell of composites puts its points on the holes of the set the composites belong to (`data_holes.shell_points`:
the middles of the intervals on the trajectories of that set), so the trajectories are an input of the model even when
it has no table of codes: a new survey moves the points while the composites — intervals along the hole — stay bit for
bit what they were."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_results, objects
from tests.estutil import W, world
from tests.test_model_store import (
    _roles,  # noqa: F401 - the autouse fixture: the roles of the files until `db/schema.py` has them
    get,
    make,
    run,
)


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


def composites(w: W) -> int:
    """A `dh_results` row «Композиты» of the set, pointing at its table of assays."""
    with dialect.write_tx(w.project.engine) as conn:
        row = dict(set_id=w.set_id, table_id=w.a, op="length", name="Композиты", params={}, inputs={},
                   sha256="0" * 64, algo="t", report={}, created_at=dt.datetime.now(dt.UTC))  # fmt: skip
        return int(conn.execute(sa.insert(dh_results).values(**row).returning(dh_results.c.id)).scalar_one())


def test_a_model_of_shells_of_composites_is_stale_when_the_trajectories_move(w: W) -> None:
    rid = composites(w)
    shell = {"name": "Au", "type": "shell",
             "params": {"source": "composites", "result_id": rid, "field": "AU", "thresholds": [0.5]}}  # fmt: skip
    ident = make(w, data=None, groups=[shell])["id"]  # no table of codes: the set is named by the composites alone
    run(w, ident)
    assert get(w, ident)["stale"] is False
    kinds = [(i["kind"], i["id"]) for i in get(w, ident)["meta"]["inputs"]]
    assert ("dh_set", w.set_id) in kinds and ("dh_result", rid) in kinds
    with dialect.write_tx(w.project.engine) as conn:  # a new survey: the set moves, the composites do not
        conn.execute(sa.update(objects).where(objects.c.id == w.set_id).values(version=objects.c.version + 1))
    got = get(w, ident)
    assert (got["stale"], got["stale_reason"]) == (True, "изменены траектории")
