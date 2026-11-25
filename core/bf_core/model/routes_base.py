"""What the routers of the implicit models share (подшаг 19.16; docs/progress/e19-api.md §1): the dependencies, the
shape of a body, the events `implicit.changed` / `objects.changed`. Light: nothing heavy is imported before it is
needed."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from bf_core.geostat.estimate.routes_common import (  # noqa: F401 - re-exported for the routers of the module
    Payload,
    Sched,
    Svc,
    author,
    body_of,
    common_of,
    plain,
    registered,
    sync,
)

EVENT = "implicit.changed"
KIND = "implicit_model"
KIND_BUILD, KIND_UPDATE = "implicit.build", "implicit.update"
TITLE_BUILD, TITLE_UPDATE = "Построение неявной модели", "Обновление неявной модели"

__all__ = ["EVENT", "KIND", "KIND_BUILD", "KIND_UPDATE", "TITLE_BUILD", "TITLE_UPDATE", "Payload", "Sched", "Svc",
           "author", "body_of", "common_of", "plain", "publish", "publish_wireframe", "registered", "sync"]  # fmt: skip


def publish(request: Request, model_id: int, deleted: bool = False) -> None:
    """`implicit.changed {kind: "implicit_model", id, deleted?}` — after a write and a deletion."""
    data: dict[str, Any] = {"kind": KIND, "id": model_id}
    if deleted:
        data["deleted"] = True
    request.app.state.core.events.publish(EVENT, data)


def publish_wireframe(request: Request, wireframe_id: int, deleted: bool = False) -> None:
    """`objects.changed {kind: "wireframe", id, deleted?}` — an output made, copied or deleted."""
    data: dict[str, Any] = {"kind": "wireframe", "id": wireframe_id}
    if deleted:
        data["deleted"] = True
    request.app.state.core.events.publish("objects.changed", data)
