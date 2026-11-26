"""/api/v1/implicit — the outputs of a model, «В каркас», the deletion (ТЗ 8.9.3; ADR 0026 решения 23–25; e19-api.md §2,
§5; подшаг 19.16):

  * `GET /models/{id}/outputs` → {outputs: [{wireframe_id, kind, group, wing, threshold, name, volume, area,
    triangles}]} — the wireframes the last run wrote (those that are gone are left out); 404 `model_not_found`.
  * `POST /models/{id}/to_wireframe` {wireframe_id, name?} → {wireframe_id, name} — a copy of an output without a
    parent (it stays when the model goes, the levels of detail and the files are shared with the output), with the
    model as its input in the graph; 404 `wireframe_not_found` (not an output of this model), 409 `model_building`,
    423 `project_read_only`.
  * `DELETE /models/{id}` → {deleted, wireframes: [id]} — the model, its outputs, its files and its cache
    `cache/implicit/<id>`; 409 `model_building`, 409 `in_use {usage}` («Где используется» — the objects that refer to an
    output: nothing is deleted); the window asks `lineage.impact` first (askImpact). Publishes `implicit.changed
    {deleted}` and `objects.changed {kind: "wireframe", id, deleted}` of every output.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.storage.blobs import BlobStore

from .routes_base import Payload, Svc, author, body_of, publish, publish_wireframe

router = APIRouter()
COPY_SUFFIX = " (копия)"


@router.get("/models/{model_id}/outputs")
def outputs(svc: Svc, model_id: int) -> dict[str, Any]:
    from . import store as st
    from . import store_base as sb

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        row = sb.stored(conn, model_id)
        return {"outputs": st.outputs_of(conn, dict(row.meta or {}))}


# --- «В каркас» -------------------------------------------------------------------------------------------------------


def copy_output(conn: Any, model_id: int, wireframe_id: Any, name: Any, who: str | None) -> dict[str, Any]:
    """Inside the write transaction: a wireframe of its own with the geometry (the same blobs) of an output; the model
    is its input in the graph. `name` None — «‹имя выхода› (копия)», made free among the wireframes."""
    import sqlalchemy as sa

    from bf_core import lineage
    from bf_core.db.schema import objects, wireframes
    from bf_core.storage.blobs import add_owners
    from bf_core.wireframes import files as wf_files
    from bf_core.wireframes import store as wf

    from . import store_base as sb

    if isinstance(wireframe_id, bool) or not isinstance(wireframe_id, int) or wireframe_id < 1:
        raise sb.bad("wireframe_id", "Не выбран выход модели.", "Выберите поверхность или тело модели.")
    given = None if name is None else " ".join(str(name).split())
    src = conn.execute(sa.select(objects).where(objects.c.id == wireframe_id, objects.c.kind == wf.WIREFRAME,
                                                objects.c.parent_id == model_id)).first()  # fmt: skip
    own = None if src is None else conn.execute(sa.select(wireframes).where(
        wireframes.c.object_id == wireframe_id)).first()  # fmt: skip
    if src is None or own is None:
        raise ApiError(404, "wireframe_not_found", "Выход модели не найден.", "Обновите дерево «Неявные модели».",
                       {"param": "wireframe_id"})  # fmt: skip
    final = wf.free_name(conn, given or f"{src.name}{COPY_SUFFIX}")
    now = dialect.utcnow()
    meta = {k: v for k, v in dict(src.meta or {}).items() if k in ("color",)}
    meta["source"] = {"op": "implicit_copy", "model_id": model_id, "wireframe_id": wireframe_id}
    res = conn.execute(sa.insert(objects).values(
        kind=wf.WIREFRAME, name=final, name_key=dialect.name_key(final), parent_id=None, folder=None, sort_key=0.0,
        state="ready", version=1, meta=meta, extent=src.extent, crs=src.crs, author=who, created_at=now,
        updated_at=now))  # fmt: skip
    new_id = int(res.inserted_primary_key[0])  # type: ignore[index]
    conn.execute(sa.insert(wireframes).values(
        object_id=new_id, point_count=own.point_count, triangle_count=own.triangle_count, origin=own.origin,
        stats=own.stats, render=own.render, attrs=own.attrs, dtm=None))  # fmt: skip
    roles = wf_files.blob_roles(conn, wireframe_id)
    add_owners(conn, [(sha, wf.OWNER, new_id, role) for role, sha in sorted(roles.items())])
    lineage.record(conn, lineage.Node("wireframe", new_id), [lineage.Node(sb.LINEAGE_KIND, model_id)])
    return {"wireframe_id": new_id, "name": final}


@router.post("/models/{model_id}/to_wireframe")
def to_wireframe(svc: Svc, request: Request, model_id: int, body: Payload) -> dict[str, Any]:
    from . import store_base as sb

    raw = body_of(body)
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        row = sb.stored(conn, model_id)
        sb.refuse_building(conn, row)
        out = copy_output(conn, model_id, raw.get("wireframe_id"), raw.get("name"), author())
    publish_wireframe(request, out["wireframe_id"])
    return out


# --- the deletion -----------------------------------------------------------------------------------------------------


def _in_use(err: ApiError) -> ApiError:
    """`wireframe_in_use` of an output → 409 `in_use {usage: [{id, kind, name, role}]}` («Где используется»)."""
    users = [{"id": u["id"], "kind": u["kind"], "name": u["name"], "role": "output"}
             for u in err.extra.get("objects", [])]  # fmt: skip
    names = ", ".join(f"«{u['name']}»" for u in users[:5]) + (" и другие" if len(users) > 5 else "")
    return ApiError(409, "in_use", f"Выходы модели используются: {names}.",
                    "Удалите или измените эти объекты, затем удалите модель.", {"usage": users})  # fmt: skip


@router.delete("/models/{model_id}")
def delete_model(svc: Svc, request: Request, model_id: int) -> dict[str, Any]:
    import sys

    from . import job_write as jw
    from . import store as st

    project = svc.require_current(writable=True)
    try:
        with dialect.write_tx(project.engine) as conn:
            out = st.delete(conn, BlobStore(project.folder), model_id)
    except ApiError as err:
        if err.code == "wireframe_in_use":
            raise _in_use(err) from None
        raise
    jw.drop_levels(project.folder, out["removed"])  # the levels of detail of the geometries nobody has any more
    exact = sys.modules.get("bf_core.wireframes.exact")  # loaded by the first exact query: nothing kept before
    if exact is not None:
        for gone in out["removed"]:
            if gone.get("shas") and not gone.get("shared"):
                from bf_core.viz import meshes

                exact.drop(meshes.cache_key(tuple(sorted(gone["shas"].items())), gone["render"]))
    publish(request, model_id, deleted=True)
    for wid in out["wireframes"]:
        publish_wireframe(request, wid, deleted=True)
    return {"deleted": out["deleted"], "wireframes": out["wireframes"]}
