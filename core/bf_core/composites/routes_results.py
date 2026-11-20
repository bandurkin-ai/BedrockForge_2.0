"""/api/v1/composites/results — the list of results of a set with «устарел», deletion (ADR 0018 §11).

`composites/router.py` includes this router under the prefix `/composites`. Events: `composites.changed
{set_id}` after a deletion, `drillholes.changed {set_id, table, rev}` for every table that lost columns or
was deleted (rev 0).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]


def publish_changed(request: Request, set_id: int) -> None:
    """The tree (rows «Обработка проб») and «Свойства» read the results again."""
    request.app.state.core.events.publish("composites.changed", {"set_id": set_id})


@router.get("/results")
def list_results(svc: Svc, set_id: int) -> dict[str, Any]:
    from . import results

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"results": results.list_results(conn, set_id)}


@router.delete("/results/{result_id}")
def delete_result(svc: Svc, request: Request, result_id: int) -> dict[str, Any]:
    """«Удалить»: the table of the result, or the columns it wrote in place (they can come back by «Отменить»
    in the journal of edits), and the record."""
    from bf_core.drillholes.snapshot import drop_cache_files, snapshots

    from . import results

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        out = results.delete_result(conn, result_id)
    set_id = out["set_id"]
    snapshots.forget(set_id)
    drop_cache_files(project.folder / "cache" / "tables", out["deleted_tables"])
    events = request.app.state.core.events
    for t in out["changed"]:
        events.publish("drillholes.changed", {"set_id": set_id, "table": t["table"], "rev": t["rev"]})
    for tid in out["deleted_tables"]:
        events.publish("drillholes.changed", {"set_id": set_id, "table": str(tid), "rev": 0})
    publish_changed(request, set_id)
    return {"deleted": result_id, **out}
