"""/api/v1/assistant — the dialogs and the turns (подшаг 23.11; ТЗ 7.32; ADR 0029 решения 5, 21, 24, 28–32;
docs/progress/e23-api.md §7–§10). The paths are relative to `/assistant` (`router.py`):

  * `GET /dialogs?offset&limit` → {total, items}; `GET /dialogs/last` → the head or 204; `GET /dialogs/{id}`;
    `PATCH /dialogs/{id} {title}` → the head; `DELETE /dialogs/{id}` → 204. 404 `assistant_dialog_not_found`;
    409 `assistant_busy` (a segment runs), `no_project`, `assistant_dialog_broken`; 423 `project_read_only`.
  * `POST /turns` and `POST /dialogs/{id}/tool_results` answer with the events of a segment (SSE): `id: n`,
    `event: name`, `data: JSON`, `: keepalive` after 15 s of silence. The refusals that come before the first event are
    plain JSON (409 `assistant_not_connected` / `assistant_busy` / `assistant_no_pending` /
    `assistant_pending_mismatch`, 404, 422).
  * `GET /dialogs/{id}/stream?after=n` — the events of the running segment above `n` (a window that was reloaded);
    no segment — 204. `POST /dialogs/{id}/cancel` → 204 always.

The loop runs in a thread of its own and finishes the segment even when nobody reads the answer (решение 24): a closed
connection stops only the reading.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

from anyio import to_thread
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from .errors import bad_param, refuse
from .routes import Svc, blocking, read_json
from .schemas import ToolResultsBody, TurnBody, parse_body
from .schemas_dialog import PatchDialogBody

if TYPE_CHECKING:
    from .segments import Event, Segment

router = APIRouter()
KEEPALIVE_S = 15.0
_END = object()  # the iteration of a segment is over


def number(raw: str, param: str, low: int, high: int | None = None) -> int:
    """An integer of a query string between the limits; anything else is 422 `bad_param {param}`."""
    text = raw.strip()
    if not (text.isascii() and text.isdecimal()) or len(text) > 12:
        raise bad_param(param)
    value = int(text)
    if value < low or (high is not None and value > high):
        raise bad_param(param)
    return value


def frame(event: Event) -> bytes:
    data = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.id}\nevent: {event.event}\ndata: {data}\n\n".encode()


def stream_of(segment: Segment, after: int = 0) -> StreamingResponse:
    """The events of a segment as SSE. The wait for the next event is in a thread that is let go when the reader
    leaves, so a closed window does not hold the answer (and the shutdown of the core) for the keepalive interval."""
    events: Iterator[Event | None] = segment.iter(after, keepalive=KEEPALIVE_S)

    def pull() -> Any:
        return next(events, _END)

    async def body() -> AsyncIterator[bytes]:
        while True:
            item = await to_thread.run_sync(pull, abandon_on_cancel=True)
            if item is _END:
                return
            yield b": keepalive\n\n" if item is None else frame(item)

    return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


# --- the turns -------------------------------------------------------------------------------------------------------


@router.post("/turns")
async def post_turn(request: Request, svc: Svc) -> StreamingResponse:
    from . import loop

    body = parse_body(TurnBody, await read_json(request))
    segment = await blocking(loop.start_turn, svc.env(), body)
    return stream_of(segment)


@router.post("/dialogs/{dialog_id}/tool_results")
async def post_tool_results(dialog_id: str, request: Request, svc: Svc) -> StreamingResponse:
    from . import loop

    body = parse_body(ToolResultsBody, await read_json(request))
    segment = await blocking(loop.start_continue, svc.env(), dialog_id, body)
    return stream_of(segment)


@router.get("/dialogs/{dialog_id}/stream", response_model=None)
def get_stream(dialog_id: str, svc: Svc, after: str = "0") -> Response:
    start = number(after, "after", 0)
    segment = svc.segments.get(dialog_id)
    if segment is None:
        return Response(status_code=204)
    return stream_of(segment, start)


@router.post("/dialogs/{dialog_id}/cancel")
def post_cancel(dialog_id: str, svc: Svc) -> Response:
    from . import loop

    loop.cancel_turn(svc.env(), dialog_id)
    return Response(status_code=204)


# --- the dialogs -----------------------------------------------------------------------------------------------------


@router.get("/dialogs")
def get_dialogs(svc: Svc, offset: str = "0", limit: str = "50") -> dict[str, Any]:
    first, size = number(offset, "offset", 0), number(limit, "limit", 1, 200)
    return svc.dialogs.list(first, size, busy=svc.segments.busy())  # type: ignore[no-any-return]


@router.get("/dialogs/last", response_model=None)
def get_last_dialog(svc: Svc) -> Any:
    head = svc.dialogs.last(svc.segments.busy())
    return Response(status_code=204) if head is None else head


@router.get("/dialogs/{dialog_id}")
def get_dialog(dialog_id: str, svc: Svc) -> dict[str, Any]:
    from . import loop

    return loop.read_dialog(svc.env(), dialog_id)


@router.patch("/dialogs/{dialog_id}")
async def patch_dialog(dialog_id: str, request: Request, svc: Svc) -> dict[str, Any]:
    body = parse_body(PatchDialogBody, await read_json(request))
    if dialog_id in svc.segments.busy():
        raise refuse("assistant_busy")
    head = await blocking(svc.dialogs.rename, dialog_id, body.title)
    svc.publish_dialog(head)
    return head  # type: ignore[no-any-return]


@router.delete("/dialogs/{dialog_id}")
async def delete_dialog(dialog_id: str, svc: Svc) -> Response:
    if dialog_id in svc.segments.busy():
        raise refuse("assistant_busy")
    saved_in = None if dialog_id.startswith("t-") else svc.project_uid()
    await blocking(svc.dialogs.delete, dialog_id)
    svc.publish_dialog({"id": dialog_id, "project_uid": saved_in}, deleted=True)
    return Response(status_code=204)
