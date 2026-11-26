"""Helpers of the tests of the jobs `pit.check` and `pit.run` (подшаг 21.11): the run in the process, «Перестроить»,
the scenario and the model as the project holds them, the counters of the rows, a tick that cancels, a flat terrain."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, lineage, objects
from bf_core.jobs.context import JobCancelled
from bf_core.mine import job as pj
from bf_core.mine import store as st
from bf_core.storage.blobs import BlobStore

SMALL = (9, 8, 6)


def direct(c: Any, sid: int, p: dict[str, Any] | None = None, tick: Any = None, **kw: Any) -> dict[str, Any]:
    """`pit.run` in the process: the current parameters of the scenario (or `p`), its version."""
    with dialect.read_tx(c.project.engine) as conn:
        row = st._row(conn, sid)  # noqa: SLF001
        body = dict(p) if p is not None else st.current_params(conn, sid)
        version = int(row.version)
    job = {"scenario_id": sid, "params": body, "expected_version": version}
    return pj.run_scenario(c.project.engine, c.project.folder, job, tick or pj._noop, **kw)  # noqa: SLF001


def rebuild(c: Any, sid: int, tick: Any = None, **kw: Any) -> dict[str, Any]:
    with dialect.read_tx(c.project.engine) as conn:
        job = st.rebuild_params(conn, sid)
    return pj.run_scenario(c.project.engine, c.project.folder, job, tick or pj._noop, **kw)  # noqa: SLF001


def info(c: Any, sid: int) -> dict[str, Any]:
    with dialect.read_tx(c.project.engine) as conn:
        return st.info(conn, sid, c.store)


def columns(c: Any, mid: int, *names: str) -> dict[str, np.ndarray]:
    with dialect.read_tx(c.project.engine) as conn:
        return Model.open(conn, c.store, mid).columns(list(names))


def field_of(c: Any, mid: int, name: str) -> dict[str, Any] | None:
    with dialect.read_tx(c.project.engine) as conn:
        return next((f for f in Model.open(conn, c.store, mid).fields if f["name"] == name), None)


def sha_of(c: Any, sid: int) -> str | None:
    with dialect.read_tx(c.project.engine) as conn:
        return st.data_sha(conn, sid)


def counts(c: Any) -> tuple[int, int, int]:
    with dialect.read_tx(c.project.engine) as conn:
        return tuple(  # type: ignore[return-value]
            int(conn.execute(sa.select(sa.func.count()).select_from(t)).scalar_one())
            for t in (objects, blob_owners, lineage)
        )


def tmp_files(c: Any) -> list[Path]:
    d = BlobStore(c.project.folder).tmp_dir
    return sorted(d.iterdir()) if d.exists() else []


class Cancel:
    """A tick that cancels when a message of the stage is shown (`at` — the start of the text), or at the n-th call."""

    def __init__(self, at: str | None = None, nth: int | None = None) -> None:
        self.at, self.nth, self.calls = at, nth, 0

    def __call__(self, _p: float | None = None, m: str | None = None) -> None:
        self.calls += 1
        if (self.at is not None and m is not None and m.startswith(self.at)) or self.nth == self.calls:
            raise JobCancelled()


def refused(fn: Any, status: int, code: str) -> ApiError:
    with pytest.raises(ApiError) as e:
        fn()
    assert (e.value.status, e.value.code) == (status, code), e.value
    assert e.value.message and e.value.hint and any("а" <= ch.lower() <= "я" for ch in e.value.message)
    return e.value


def terrain(c: Any, z: float, size: float = 2000.0) -> int:
    """A flat DTM of the plan of the model at the height `z`."""
    from bf_core.wireframes import store as wf

    x0, y0 = 500.0 - size / 2, 700.0 - size / 2
    xyz = np.array([[x0, y0, z], [x0 + 2 * size, y0, z], [x0 + 2 * size, y0 + 2 * size, z], [x0, y0 + 2 * size, z]])
    tri = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
    with dialect.write_tx(c.project.engine) as conn:
        return wf.put_wireframe(conn, c.store, xyz, tri, name="Рельеф", dtm=True)
