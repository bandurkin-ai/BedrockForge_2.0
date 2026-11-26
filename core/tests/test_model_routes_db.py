"""What the routes of the implicit models do in the database itself, on SQLite and on PostgreSQL (`-m db`; BF_TEST_PG;
подшаг 19.16): «В каркас» (a copy of an output: the row, its part table, the blobs shared with the output, the model as
its input in the graph), the deletion of a model whose output other objects refer to (409 `in_use {usage}`, nothing is
deleted), the deletion that follows (the outputs go, the copy stays with its blobs). The routes themselves —
`test_model_routes*.py`."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from bf_core import lineage
from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, object_refs, objects
from bf_core.model import routes_outputs as ro
from bf_core.model import store as st
from bf_core.model import store_base as sb
from bf_core.model.spec import parse_model
from bf_core.wireframes import store as ws
from tests.meshutil import cube
from tests.test_model_data_load import World, domain

pytestmark = pytest.mark.db
BOX = [0.0, 0.0, 120.0, 420.0, 420.0, 330.0]


@pytest.fixture()
def world(db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    w = World(db_url, tmp_path)
    yield w
    w.engine.dispose()


def model(w: World, name: str) -> int:
    body = {**w.spec([domain(1, "Интрузия", "INT")]).canonical(), "boundary": {"box": BOX}, "h": 8.0, "name": name}
    with dialect.write_tx(w.engine) as conn:
        return int(st.create(conn, w.store, w.project, parse_model(body))["id"])


def output(w: World, mid: int, name: str) -> int:
    v, t = cube(2, 100.0, (0.0, 0.0, -100.0), seed=None)
    with dialect.write_tx(w.engine) as conn:
        return int(ws.put_wireframe(conn, w.store, v, t, name=name, parent_id=mid, meta=st.output_meta(mid, 1)))


def shas(w: World, ident: int) -> dict[str, str]:
    with dialect.read_tx(w.engine) as conn:
        return {str(r.role): str(r.sha256) for r in conn.execute(sa.select(blob_owners).where(
            blob_owners.c.owner_kind == "object", blob_owners.c.owner_id == ident))}  # fmt: skip


def test_the_copy_of_an_output_and_the_deletion(world: World) -> None:
    w = world
    mid, other = model(w, "Модель"), model(w, "Другая")
    out = output(w, mid, "Модель · Интрузия")
    with dialect.write_tx(w.engine) as conn:
        made = ro.copy_output(conn, mid, out, None, "автор")
    assert made["name"] == "Модель · Интрузия (копия)" and made["wireframe_id"] != out
    copy = made["wireframe_id"]
    with dialect.read_tx(w.engine) as conn:
        row = conn.execute(sa.select(objects).where(objects.c.id == copy)).one()
        assert (row.kind, row.parent_id, row.state, row.version, row.author) == ("wireframe", None, "ready", 1, "автор")
        assert row.meta["source"] == {"op": "implicit_copy", "model_id": mid, "wireframe_id": out}
        a, b = ws.wireframe_info(conn, out), ws.wireframe_info(conn, copy)
        assert [a[k] for k in ("point_count", "triangle_count", "stats")] == [b[k] for k in a if k in (
            "point_count", "triangle_count", "stats")]  # fmt: skip
        nodes = lineage.impact(conn, lineage.Node("object:implicit_model", mid))["nodes"]
        assert ("wireframe", copy) in {(n["kind"], n["id"]) for n in nodes}
    assert shas(w, copy) == shas(w, out) and shas(w, copy)  # the same geometry, shared
    with dialect.write_tx(w.engine) as conn:
        again = ro.copy_output(conn, mid, out, "  Моя   копия ", None)
        with pytest.raises(ApiError) as e:
            ro.copy_output(conn, mid, copy, None, None)  # not an output of the model
        assert (e.value.status, e.value.code, e.value.extra["param"]) == (404, "wireframe_not_found", "wireframe_id")
        with pytest.raises(ApiError) as e2:
            ro.copy_output(conn, mid, True, None, None)
        assert (e2.value.status, e2.value.code, e2.value.extra["param"]) == (422, "bad_param", "wireframe_id")
    assert again["name"] == "Моя копия"
    # an output other objects refer to stops the deletion: 409 `in_use` with the usage, nothing is deleted
    with dialect.write_tx(w.engine) as conn:
        conn.execute(sa.insert(object_refs).values(src_id=other, dst_id=out, role="boundary"))
    with pytest.raises(ApiError) as e3, dialect.write_tx(w.engine) as conn:
        st.delete(conn, w.store, mid)
    assert e3.value.code == "wireframe_in_use"
    err = ro._in_use(e3.value)  # noqa: SLF001
    assert (err.status, err.code) == (409, "in_use") and err.message and err.hint
    assert err.extra["usage"] == [{"id": other, "kind": "implicit_model", "name": "Другая", "role": "output"}]
    with dialect.read_tx(w.engine) as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(objects).where(
            objects.c.id.in_([mid, out]))).scalar_one() == 2  # fmt: skip
    with dialect.write_tx(w.engine) as conn:
        conn.execute(sa.delete(object_refs).where(object_refs.c.src_id == other))
        gone = st.delete(conn, w.store, mid)
    assert gone["deleted"] == mid and gone["wireframes"] == [out]
    with dialect.read_tx(w.engine) as conn:
        left = set(conn.execute(sa.select(objects.c.id).where(objects.c.id.in_([mid, out, copy, again["wireframe_id"]])
                                                              )).scalars())  # fmt: skip
    assert left == {copy, again["wireframe_id"]} and shas(w, copy)  # the copies stay with their blobs
    with dialect.read_tx(w.engine) as conn, pytest.raises(ApiError) as e4:
        sb.stored(conn, mid)
    assert e4.value.code == "model_not_found"
