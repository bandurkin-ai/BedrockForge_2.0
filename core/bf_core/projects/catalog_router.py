"""/api/v1/catalog/… — the catalog of «Менеджер данных» (ТЗ 7.35 Д-20; ADR 0017 §6 «API»; `catalog.py`).

* `GET /catalog?folder=&kind=&q=&sort=&desc=&offset=&limit=` → `{total, rows}`;
* `GET /catalog/folders` → `{folders: [{folder, count}]}`;
* `POST /catalog/move {ids, folder}` → `{moved, folder}` in one transaction, then `objects.changed {kind, id, ids,
  folder, moved: true}` for each kind of the objects moved; «только просмотр» — 423.

The paths are given in full (no prefix): the router is included by `exporting/router.py`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from bf_core.db import dialect

from .service import projects
from .store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]


@router.get("/catalog")
def catalog_page(
    svc: Svc,
    folder: str | None = Query(default=None, max_length=1000),
    kind: str | None = Query(default=None, max_length=32),
    q: str | None = Query(default=None, max_length=200),
    sort: str = Query(default="name", max_length=16),
    desc: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, Any]:
    from . import catalog

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return catalog.page(conn, folder=folder, kind=kind, q=q, sort=sort, desc=desc, offset=offset, limit=limit)


@router.get("/catalog/folders")
def catalog_folders(svc: Svc) -> dict[str, Any]:
    from . import catalog

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return catalog.folders(conn)


class MoveBody(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=20_000)
    folder: str | None = Field(default=None, max_length=1000)


@router.post("/catalog/move")
def catalog_move(svc: Svc, request: Request, body: MoveBody) -> dict[str, Any]:
    """`dataset.move`: the objects into the folder ("" or null — the root)."""
    from . import catalog

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        by_kind = catalog.move(conn, body.ids, body.folder)
    folder = catalog.norm_folder(body.folder)
    for kind, ids in by_kind.items():
        request.app.state.core.events.publish(
            "objects.changed", {"kind": kind, "id": ids[0], "ids": ids, "folder": folder, "moved": True}
        )
    return {"moved": sum(len(v) for v in by_kind.values()), "folder": folder}
