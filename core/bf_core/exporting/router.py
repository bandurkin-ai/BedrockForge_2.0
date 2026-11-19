"""/api/v1/export/… — the export of the objects of the project by type (ТЗ 7.35 Д-17; ADR 0017 §5 «API»).

* `GET /export/formats` — the table «тип → формат» of the window «Экспорт проекта › Объекты в файлы»
  (`registry.formats`) and `read_only` with the reason when the project is open only for reading (ТЗ 5.7);
* `POST /export/objects {formats, folder, types?, ids?}` → the job `export.objects` (`job.py`) {source, id};
  423 `project_read_only`; 422 `export_nothing`, `bad_format`, `path_not_absolute`; 404 `folder_not_found`.

The paths are given in full here (no prefix of the router), so other routers can be included into this one:
the catalog of «Менеджер данных» (`projects/catalog_router.py`, `/catalog/…`) is, the router of OMF
(`omf_router.py`, `/omf/…`) can be.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from bf_core.db import dialect
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
READ_ONLY = "Проект открыт только для чтения: экспорт недоступен."


@router.get("/export/formats")
def export_formats(svc: Svc, request: Request) -> dict[str, Any]:
    from bf_core.importing.tools import oda_converter
    from bf_core.storage.blobs import BlobStore

    from . import registry as reg

    project = svc.require_current()
    env = reg.Env(store=BlobStore(project.folder), oda=oda_converter(request.app.state.core))
    with dialect.read_tx(project.engine) as conn:
        out = reg.formats(conn, env)
    out["read_only"] = bool(project.read_only)
    out["reason"] = READ_ONLY if project.read_only else None
    return out


class ObjectsBody(BaseModel):
    formats: dict[str, str] = Field(max_length=32)  # type → format
    folder: str = Field(min_length=1, max_length=1000)
    types: list[str] | None = Field(default=None, max_length=32)
    ids: list[int] | None = Field(default=None, max_length=20_000)


@router.post("/export/objects")
def export_objects(svc: Svc, s: Sched, request: Request, body: ObjectsBody) -> dict[str, Any]:
    """`project.export {formats, folder, types?}` and «Экспорт…» of the data manager (`ids`)."""
    from bf_core.importing.tools import oda_converter
    from bf_core.storage.blobs import BlobStore

    from . import job

    project = svc.require_current(writable=True)  # 423: «только просмотр» (ТЗ 5.7)
    name = str(project.meta()["name"])
    with dialect.read_tx(project.engine) as conn:
        params = job.request(
            conn, folder=body.folder, formats=body.formats, types=body.types, ids=body.ids, project=name,
            oda=oda_converter(request.app.state.core), store=BlobStore(project.folder),
        )  # fmt: skip
    key, job_id = s.submit(job.KIND, params, title="Экспорт объектов проекта")
    return {"source": key, "id": job_id}


from bf_core.projects import catalog_router  # noqa: E402 - /catalog/…: one line of `api/modules.py` for both

router.include_router(catalog_router.router)

from bf_core.exporting import omf_router  # noqa: E402 - the OMF routes (ADR 0017), mounted with this router

router.include_router(omf_router.router)
