"""/api/v1/lines — the string editor (ТЗ 7.22; ADR 0015 §2). Included into `lines/router.py`.

* `POST /lines/layers {name, file_id?, color?}` — `lines.new_layer`: an empty layer (201).
* `GET /lines/layers/{id}/geometry[?lines=]` — the exact geometry, BFA1 `linegeom` (`geometry_io.py`), ETag / 304.
* `PUT /lines/layers/{id}/geometry` — a save: the body BFA1 with the base version; 409 `layer_changed`, 422
  `bad_geometry`, a cloud — 422 `not_editable`.
* `GET /lines/layers/{id}/versions` — the journal; `POST …/versions/{k}/restore {expected_version}` — the return.
* `POST /lines/layers/{id}/move-lines` — `lines.move_selected`: BFA1 (`geometry_io.decode_move`) or JSON
  `{lines, target_id, base_version, target_base}` (lines by `lid`, as stored).

Every change publishes `objects.changed` of the layers (and of the file of a new layer); a project open read-only
answers 423. numpy loads on the first request (tests/test_process.py).
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
IfNoneMatch = Annotated[str | None, Header()]
MAX_BODY = 1 << 30  # a layer of a million vertices is 24 MB; a body this big is not from the editor


def _changed(request: Request, kind: str, object_id: int, **extra: Any) -> None:
    request.app.state.core.events.publish("objects.changed", {"kind": kind, "id": object_id, **extra})


async def _body(request: Request) -> bytes:
    from .geometry_io import bad_body

    data = await request.body()
    if len(data) > MAX_BODY:
        raise bad_body("Тело запроса слишком велико.")
    return data


class NewLayer(BaseModel):
    name: str = Field(max_length=200)
    file_id: int | None = None
    color: str | None = Field(default=None, max_length=16)


@router.post("/layers", status_code=201)
def new_layer(svc: Svc, request: Request, body: NewLayer) -> dict[str, Any]:
    from bf_core.drillholes.edit import author
    from bf_core.storage.blobs import BlobStore

    from . import service, versions

    project = svc.require_current(writable=True)
    if body.color is not None and not service.COLOR_RE.match(body.color):
        from bf_core.api.errors import ApiError

        raise ApiError(422, "bad_color", "Цвет слоя задаётся как #rrggbb.", "Выберите цвет в палитре.")
    with dialect.write_tx(project.engine) as conn:
        layer_id = versions.create_layer(
            conn, BlobStore(project.folder), name=body.name, file_id=body.file_id, color=body.color, author=author()
        )
        out = service.get_layer(conn, layer_id)
    _changed(request, "line_file", out["file_id"])
    _changed(request, "line_layer", layer_id)
    return out


@router.get("/layers/{layer_id}/geometry")
def get_geometry(
    svc: Svc, layer_id: int, lines: Annotated[str | None, Query(max_length=200_000)] = None,
    if_none_match: IfNoneMatch = None,
) -> Response:  # fmt: skip
    from bf_core.storage.blobs import BlobStore
    from bf_core.viz.keys import matches

    from . import geometry_io, store, versions

    project = svc.require_current()
    bs = BlobStore(project.folder)
    wanted = geometry_io.parse_lines(lines)
    with dialect.read_tx(project.engine) as conn:
        head = versions.layer_head(conn, layer_id)
        versions.check_editable(head)
        shas = store.layer_blobs(conn, layer_id)
        key = json.dumps(["linegeom", project.uid, layer_id, head["rev"], sorted(shas.items()), wanted])
        tag = '"' + hashlib.sha256(key.encode()).hexdigest() + '"'
        headers = {"ETag": tag, "Cache-Control": "no-cache"}
        if matches(if_none_match, tag):
            return Response(status_code=304, headers=headers)
        _head, ls, lid = versions.read_current(conn, bs, layer_id)
    data = geometry_io.encode_geometry(layer_id, int(head["rev"]), ls, lid, wanted)
    return Response(data, media_type="application/octet-stream", headers=headers)


def _saved(conn: Any, layer_id: int, version: int) -> dict[str, Any]:
    from . import service

    return {"layer": service.get_layer(conn, layer_id), "version": version}


@router.put("/layers/{layer_id}/geometry")
async def put_geometry(svc: Svc, request: Request, layer_id: int) -> JSONResponse:
    project = svc.require_current(writable=True)
    data = await _body(request)
    return await run_in_threadpool(_put, project, request, layer_id, data)


def _put(project: Any, request: Request, layer_id: int, data: bytes) -> JSONResponse:
    from bf_core.drillholes.edit import author
    from bf_core.storage.blobs import BlobStore

    from . import geometry_io, versions

    base, note, edit = geometry_io.decode_save(data)
    version = versions.save(
        project.engine, BlobStore(project.folder), layer_id, edit, base_version=base, note=note, author=author()
    )
    with dialect.read_tx(project.engine) as conn:
        out = _saved(conn, layer_id, version)
    _changed(request, "line_layer", layer_id)
    return JSONResponse(out)


@router.get("/layers/{layer_id}/versions")
def list_versions(svc: Svc, layer_id: int) -> dict[str, Any]:
    from . import versions

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        head = versions.layer_head(conn, layer_id)
        return {"layer_id": layer_id, "version": int(head["rev"]), "versions": versions.journal(conn, layer_id)}


class RestoreBody(BaseModel):
    expected_version: int = Field(ge=1)


@router.post("/layers/{layer_id}/versions/{version}/restore")
def restore_version(svc: Svc, request: Request, layer_id: int, version: int, body: RestoreBody) -> dict[str, Any]:
    from bf_core.drillholes.edit import author
    from bf_core.storage.blobs import BlobStore

    from . import versions

    project = svc.require_current(writable=True)
    new = versions.restore(
        project.engine, BlobStore(project.folder), layer_id, version, expected_version=body.expected_version,
        author=author(),
    )  # fmt: skip
    with dialect.read_tx(project.engine) as conn:
        out = _saved(conn, layer_id, new)
    _changed(request, "line_layer", layer_id)
    return out


class MoveBody(BaseModel):
    lines: list[int] = Field(min_length=1, max_length=1_000_000)
    target_id: int
    base_version: int = Field(ge=1)
    target_base: int = Field(ge=1)


@router.post("/layers/{layer_id}/move-lines")
async def move_lines(svc: Svc, request: Request, layer_id: int) -> JSONResponse:
    project = svc.require_current(writable=True)
    data = await _body(request)
    json_body = (request.headers.get("content-type") or "").startswith("application/json")
    return await run_in_threadpool(_move, project, request, layer_id, data, json_body)


def _move_json(project: Any, layer_id: int, data: bytes) -> tuple[int, int, int, Any, Any]:
    """Lines named by `lid`, moved as stored: the source keeps the others untouched."""
    import numpy as np
    from pydantic import ValidationError

    from bf_core.api.errors import ApiError
    from bf_core.storage.blobs import BlobStore

    from . import versions
    from .assemble import Edit, as_lines

    try:
        body = MoveBody.model_validate_json(data)
    except ValidationError as exc:
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Выберите линии и слой заново.") from exc
    with dialect.read_tx(project.engine) as conn:
        head, ls, lid = versions.read_current(conn, BlobStore(project.folder), layer_id)
    offsets, closed = as_lines(ls)
    counts = np.diff(offsets)
    chosen = set(body.lines)
    if len(chosen) != len(body.lines):
        from collections import Counter

        twice = min(v for v, k in Counter(body.lines).items() if k > 1)
        raise ApiError(
            422, "bad_geometry", f"Линия {twice} названа для переноса дважды.", "Выберите линии для переноса заново."
        )
    missing = chosen - set(lid.tolist())
    if missing:
        raise ApiError(404, "line_not_found", f"Линии {min(missing)} в слое нет.", "Слой изменился — перечитайте его.")
    keep = np.array([int(v) not in chosen for v in lid.tolist()], dtype=bool)
    order = {int(v): i for i, v in enumerate(lid.tolist())}
    idx = np.array([order[v] for v in body.lines], dtype=np.int64)
    zero = np.zeros((0, 3))
    source = Edit(lid[keep].astype(np.int64), np.zeros(int(keep.sum())), closed[keep], counts[keep], zero)
    moved = Edit(lid[idx].astype(np.int64), np.zeros(len(idx)), closed[idx], counts[idx], zero)
    return body.base_version, body.target_id, body.target_base, source, moved


def _move(project: Any, request: Request, layer_id: int, data: bytes, json_body: bool) -> JSONResponse:
    from bf_core.drillholes.edit import author
    from bf_core.storage.blobs import BlobStore

    from . import geometry_io, versions

    if json_body:
        base, target_id, target_base, source, moved = _move_json(project, layer_id, data)
    else:
        base, target_id, target_base, source, moved = geometry_io.decode_move(data)
    sv, tv = versions.move_lines(
        project.engine, BlobStore(project.folder), layer_id, source, moved, target_id, source_base=base,
        target_base=target_base, author=author(),
    )  # fmt: skip
    with dialect.read_tx(project.engine) as conn:
        out = {"source": _saved(conn, layer_id, sv), "target": _saved(conn, target_id, tv)}
    _changed(request, "line_layer", layer_id)
    _changed(request, "line_layer", target_id)
    return JSONResponse(out)
