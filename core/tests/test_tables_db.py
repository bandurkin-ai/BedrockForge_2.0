"""The table layer on both dialects (ADR 0003 §4–5): snapshot through the driver (JSON fields by `dialect`),
edits with the journal, undo, tools writing whole columns (`bulk_update`), revisions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from bf_core.db import dialect, migrate
from bf_core.drillholes import actions, tools
from bf_core.drillholes import edit as e
from bf_core.drillholes import store as dh
from bf_core.drillholes.query import page
from bf_core.drillholes.snapshot import build_rows, snapshots, table_key
from tests.dbutil import make_engine

pytestmark = pytest.mark.db


def _project(db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, "Набор")
    data = dh.TableData(
        "Пробы",
        "assay",
        ["D1", "D1", "D2"],
        [0.0, 1.0, 0.0],
        [1.0, 2.0, 3.0],
        [dh.ColumnSpec("AU", "number", "g/t", "component"), dh.ColumnSpec("LITH", "text")],
        [[1.5, None, 0.2], ["гранит", "диорит", None]],
    )
    table_id = dh.write_table(engine, set_id, data, job_id=None, tick=lambda _f, _m: None)["table_id"]
    snapshots.forget()
    return SimpleNamespace(engine=engine, uid=f"test-{db_url[:8]}", folder=tmp_path), set_id, str(table_id)


def _snap(p, set_id: int, table: str):  # type: ignore[no-untyped-def]
    with dialect.read_tx(p.engine) as conn:
        key = table_key(conn, p.uid, set_id, table)
        assert key is not None
        return build_rows(conn, set_id, int(table), key, None)


def test_snapshot_edit_undo_and_tools(db_url: str, tmp_path: Path) -> None:
    p, set_id, table = _project(db_url, tmp_path)
    snap = _snap(p, set_id, table)
    au = next(c.key for c in snap.columns if c.name == "AU")
    lith = next(c.key for c in snap.columns if c.name == "LITH")
    assert snap.data[lith].tolist() == ["гранит", "диорит", None]
    assert np.isnan(snap.data[au][1]) and snap.data[au][0] == 1.5
    rows = page(snap, sort=au, desc=True)["rows"]
    assert [r[au] for r in rows] == [1.5, 0.2, None]

    row_id = int(snap.ids[0])
    out = actions.update_cell(p, set_id, table, row_id, au, "2,5", snap.key[-1])
    assert out["value"] == 2.5 and out["rev"] == snap.key[-1] + 1
    with pytest.raises(Exception, match="изменилась"):
        actions.update_cell(p, set_id, table, row_id, au, "3", snap.key[-1])
    edit_id = actions.history(p, set_id)[0]["id"]
    actions.undo(p, edit_id)
    assert _snap(p, set_id, table).data[au][0] == 1.5

    deleted = int(snap.ids[2])
    actions.delete_row(p, set_id, table, deleted, None)
    actions.undo(p, actions.history(p, set_id)[0]["id"])
    after = _snap(p, set_id, table)
    assert len(after.ids) == 3  # back as a new row (SQLite may give it the freed last id again)
    back = int(np.flatnonzero(after.hole_texts() == "D2")[0])
    assert after.data[au][back] == 0.2 and after.data["to"][back] == 3.0 and after.data[lith][back] is None

    with dialect.write_tx(p.engine) as conn:
        t = e.target(conn, set_id, table)
        new = tools.add_column(conn, t, after, "Метропроцент", "number", expression="(TO - FROM) * AU")
        n = tools.replace(conn, t, after, lith, "гранит", "Гранит-порфир", whole=True)
        e.bump(conn, t, None)
    assert n == 1
    final = _snap(p, set_id, table)
    vals = dict(
        zip(
            final.data["from"].tolist(),
            zip(final.hole_texts().tolist(), final.data[new["key"]].tolist(), strict=True),
            strict=False,
        )
    )
    assert vals[1.0][1] != vals[1.0][1]  # AU missing → missing, never 0
    assert sorted(v for v in final.data[lith].tolist() if v) == ["Гранит-порфир", "диорит"]
    assert any(
        h == "D1" and v == 1.5
        for h, v in zip(final.hole_texts().tolist(), final.data[new["key"]].tolist(), strict=True)
    )
