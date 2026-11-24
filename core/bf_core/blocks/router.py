"""/api/v1/blocks — regular block models of the project (ADR 0017 §3; contract — `blocks/schema.py`).

  * `GET /blocks` → {block_models: [{id, name, folder, version, extent, crs, grid {corner, counts, size?,
    order, variable}, cells, fields [{name, kind, unit}], source, warnings, size, created_at, updated_at}]} —
    the tree «Блочные модели»; `variable` — the grid has a variable step (its `tensors` only in the details);
    `size` — bytes of the file of cells;
  * `GET /blocks/{id}` — «Свойства»: the same with `grid.tensors` and the fields with `labels` and `stats`;
  * `PATCH /blocks/{id}` {name, expected_version} — «Переименовать» (`object.rename`); a stale version — 409
    `version_conflict` {current}, a name in use — 409 `name_taken`;
  * `DELETE /blocks/{id}` — «Удалить» (`object.delete`); the file goes with the next storage collection.
Changes publish `objects.changed {kind: "block_model", id[, deleted]}`; a read-only project — 423. The folder of
a model is changed by the catalog (`POST /catalog/move`), its cells are written by the import (`import.objects`,
format `omf`) and read by `export.omf` through `blocks/store.py`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from .routes_calc import router as calc_router
from .routes_report import router as report_router
from .routes_schemes import router as schemes_router

# numpy, pyarrow and the store load on first use, not at the start of the core (tests/test_process.py).

router = APIRouter(prefix="/blocks")
router.include_router(calc_router)
router.include_router(schemes_router)  # before the routes of `/{bm_id}`: `GET /blocks/schemes` is not a model
router.include_router(report_router)
Svc = Annotated[ProjectService, Depends(projects)]
KIND = "block_model"


def _changed(request: Request, bm_id: int, **extra: Any) -> None:
    request.app.state.core.events.publish("objects.changed", {"kind": KIND, "id": bm_id, **extra})


@router.get("")
def list_block_models(svc: Svc) -> dict[str, Any]:
    from . import store

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"block_models": store.list_block_models(conn)}


@router.get("/{bm_id}")
def get_block_model(svc: Svc, bm_id: int) -> dict[str, Any]:
    from . import store

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return store.block_model_info(conn, bm_id)


class BlockModelPatch(BaseModel):
    name: Annotated[str, Field(max_length=400)]
    expected_version: int = Field(ge=1)


@router.patch("/{bm_id}")
def patch_block_model(svc: Svc, request: Request, bm_id: int, body: BlockModelPatch) -> dict[str, Any]:
    from . import store

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        out = store.rename_block_model(conn, bm_id, body.expected_version, body.name)
    _changed(request, bm_id)
    return out


@router.delete("/{bm_id}")
def delete_block_model(svc: Svc, request: Request, bm_id: int) -> dict[str, Any]:
    from . import store

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        store.delete_block_model(conn, bm_id)
    _changed(request, bm_id, deleted=True)
    return {"deleted": bm_id}
