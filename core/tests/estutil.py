"""Helpers of the tests of the stored estimation rows and plans (`test_estimate_store*.py`, подшаг 18.11): a project
with the set of Э16, a block model, a variogram model, a trend and a filter (`world`), the body of a row, create /
update / read through the store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks.filters import store as fst
from bf_core.db import dialect, migrate
from bf_core.geostat import trend_store as ts
from bf_core.geostat.estimate import spec as es
from bf_core.geostat.estimate import store as st
from bf_core.geostat.variogram import exchange
from bf_core.geostat.variogram import store as ms
from bf_core.storage.blobs import BlobStore
from tests import test_vario_store as vs
from tests.dbutil import make_engine
from tests.test_blocks_fields_store import make_model
from tests.test_vario_store import MODEL, calc

BACK = {"model": {"type": "semivariogram", "c0": 0.3, "common_axes": True,
                  "structures": [{"type": "exponential", "c": 0.9, "ang1": 10, "ang2": 0, "ang3": 0, "a1": 60,
                                  "a2": 40, "a3": 15}]}, "share": 0.97}  # fmt: skip


@dataclass
class W:
    project: Any
    store: BlobStore
    set_id: int
    a: int  # samples «Пробы»: AU, ROCK, DECL_W
    b: int  # «Литология»: ZONE
    model: int  # block model: AU, CU, N, DOM (руда, порода, дайка)
    vz: int  # variogram model in Z
    trend: int
    flt: int


def vmodel(w: Any, name: str, *, transform: str = "none", back: Any = None, model: Any = MODEL) -> int:
    key, _ = calc(w.project, w.set_id, w.a, transform=transform)
    kind, parsed = exchange.api_model(model)
    with dialect.write_tx(w.project.engine) as conn:
        return int(ms.create(conn, w.store, ms.stage(w.store, w.project.folder, key),
                             (kind, exchange.model_form(kind, parsed)), name=name, back=back)["id"])  # fmt: skip


def fill_world(project: Any, set_id: int, a: int, b: int) -> W:
    store = BlobStore(project.folder)
    w = W(project, store, set_id, a, b, make_model(project, store), 0, 0, 0)
    w.vz = vmodel(w, "Золото Z")
    with dialect.write_tx(project.engine) as conn:
        w.trend = ts.create(conn, {"name": "Тренд СВ", "ang1": 45, "size": 120})["id"]
        w.flt = fst.create(conn, {"name": "Руда", "expr": "AU > 0.1"})
    return w


def world(tmp_path: Path, url: str = "sqlite") -> W:
    engine = make_engine(url, tmp_path)
    migrate.upgrade(engine, "project")
    set_id, a, b = vs.fill(engine)
    project = SimpleNamespace(engine=engine, uid=f"test:{tmp_path}:{url}", folder=tmp_path)
    return fill_world(project, set_id, a, b)


@pytest.fixture()
def w(tmp_path: Path):  # type: ignore[no-untyped-def]
    w = world(tmp_path)
    yield w
    w.project.engine.dispose()


def body(w: W, **over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "model_id": w.model, "target": {"domain_field": "DOM", "label": "руда"},
        "samples": {"set_id": w.set_id, "table": str(w.a), "field": "AU",
                    "domain": {"table": str(w.b), "field": "ZONE", "values": ["окисл"]}},
        "variogram": {"model_id": w.vz}}  # fmt: skip
    out.update(over)
    return out


def make(w: W, **over: Any) -> dict[str, Any]:
    spec = es.parse_estimation(body(w, **over))
    with dialect.write_tx(w.project.engine) as conn:
        return st.create(conn, w.store, w.project, spec)


def put(w: W, ident: int, version: int, **raw: Any) -> dict[str, Any]:
    with dialect.write_tx(w.project.engine) as conn:
        return st.update(conn, w.store, w.project, ident, raw, version)


def get(w: W, ident: int) -> dict[str, Any]:
    with dialect.read_tx(w.project.engine) as conn:
        return st.get(conn, ident)


def refused(fn: Any, status: int, code: str, param: str | None = None) -> ApiError:
    with pytest.raises(ApiError) as e:
        fn()
    err = e.value
    assert (err.status, err.code) == (status, code), (err.code, err.message)
    assert err.message and err.hint  # Russian text with a hint
    if param is not None:
        assert err.extra.get("param") == param
    return err


def mk_refused(w: W, status: int, code: str, param: str | None = None, **over: Any) -> ApiError:
    return refused(lambda: make(w, **over), status, code, param)




def rd(w: W, fn: Any, *args: Any) -> Any:
    """`fn(conn, *args)` in a read transaction of the project."""
    with dialect.read_tx(w.project.engine) as conn:
        return fn(conn, *args)
