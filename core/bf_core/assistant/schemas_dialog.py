"""Dialogs and the events of the turn stream (подшаг 23.1; e23-api.md §7, §10): the head and the items of a dialog, the
`pending`, the stored forms, the events of the stream and of the bus `/events`."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, TypeAdapter, field_validator

from .schemas_base import (
    TITLE_MAX,
    TOOL_TEXT_MAX,
    Body,
    Count,
    DialogState,
    Effort,
    Iso,
    Kind,
    Out,
    Side,
    State,
    Tokens,
    ToolStatus,
    TurnState,
)

# --- §7 dialogs -------------------------------------------------------------------------------------------------------


class DialogHead(Out):
    id: str  # "<objects.id>" or "t-<uuid>" (temporary)
    uid: str
    title: str
    created_at: Iso
    last_at: Iso
    questions: Count
    cost_usd: float | None
    tokens: Tokens
    models: list[str]
    state: DialogState
    temporary: bool
    project_uid: str | None


class UserItem(Out):
    kind: Literal["user"] = "user"
    id: str
    at: Iso
    text: str


class AssistantItem(Out):
    kind: Literal["assistant"] = "assistant"
    id: str
    at: Iso
    model: str
    text: str  # Markdown
    stopped: bool = False
    interrupted: bool = False


class ToolItem(Out):
    kind: Literal["tool"] = "tool"
    id: str
    at: Iso
    tool_use_id: str
    name: str
    side: Side
    title: str
    args: dict[str, Any]
    status: ToolStatus
    summary: Annotated[str, StringConstraints(max_length=TOOL_TEXT_MAX)] = ""


class NoticeItem(Out):
    kind: Literal["notice"] = "notice"
    id: str
    at: Iso
    code: str
    message: str


class ErrorItem(Out):
    kind: Literal["error"] = "error"
    id: str
    at: Iso
    code: str
    message: str
    hint: str


Item = Annotated[UserItem | AssistantItem | ToolItem | NoticeItem | ErrorItem, Field(discriminator="kind")]
ITEM: TypeAdapter[Any] = TypeAdapter(Item)


class PendingTool(Out):
    tool_use_id: str
    name: str  # "ui_<command id with _>"; the window finds the command by it (`findByToolName`)
    command: str | None  # the id of the command if the core knows it; the window does not rely on it
    args: dict[str, Any]
    item_id: str


class Pending(Out):
    id: str
    created_at: Iso
    tools: list[PendingTool]


class Dialog(Out):
    head: DialogHead
    items: list[Item]
    pending: Pending | None
    note: str | None


class DialogsPage(Out):
    total: Count
    items: list[DialogHead]


class PatchDialogBody(Body):
    title: str

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= TITLE_MAX:
            raise ValueError("title")
        return value


class DialogMeta(Out):
    """`objects(kind="assistant_dialog").meta`: the head without `id` + the storage (23.6)."""

    uid: str
    title: str
    created_at: Iso
    last_at: Iso
    questions: Count
    cost_usd: float | None
    tokens: Tokens
    models: list[str]
    state: DialogState
    temporary: bool
    project_uid: str | None
    parts: list[str]  # sha256 of the parts, in order
    bytes: Count
    pending: dict[str, Any] | None  # the `Pending` of the window + the results of data tools of the same answer (core)
    prices_version: str


class DialogPart(Out):
    """A part of a dialog (zstd JSON, role `data`): the messages of the API and the items of the ribbon."""

    seq: Annotated[int, Field(ge=1)]
    at: Iso
    messages: list[dict[str, Any]]
    items: list[Item]


# --- §10 events of the stream -----------------------------------------------------------------------------------------


class TurnStart(Out):
    dialog: DialogHead
    segment_id: str
    model: str
    effort: Effort
    user_item: UserItem | None = None  # a new question; a continuation has none


class TextDelta(Out):
    item_id: str
    text: str


class Thinking(Out):
    state: Literal["start", "end"]


class ToolStart(Out):
    item: ToolItem


class ToolEnd(Out):
    item_id: str
    status: Literal["ok", "error"]
    summary: Annotated[str, StringConstraints(max_length=TOOL_TEXT_MAX)]


class CallUsage(Out):
    model: str
    input: Count
    output: Count
    cache_read: Count
    cache_write: Count
    cost_usd: float | None
    long: bool


class DialogUsage(Out):
    cost_usd: float | None
    tokens: Tokens
    questions: Count


class UsageEvent(Out):
    call: CallUsage
    dialog: DialogUsage


class NoticeEvent(Out):
    item: NoticeItem


class ErrorEvent(Out):
    item: ErrorItem


class TurnEnd(Out):
    state: TurnState
    stop_reason: str | None
    dialog: DialogHead


class SseEvent(Out):
    """One event of a stream as the samples keep it: `id` (from 1 in a segment), `event` (the name), `data` (JSON)."""

    id: Annotated[int, Field(ge=1)]
    event: str
    data: dict[str, Any]


class ConnectionEvent(Out):  # the bus `/events`: `assistant.connection` (no secret)
    state: State
    kind: Kind | None
    mask: str | None
    model: str
    online: bool | None
    seal_needed: bool


class DialogsEvent(Out):  # the bus `/events`: `assistant.dialogs`
    project_uid: str | None
    id: str
    deleted: bool = False


EVENT_MODELS: dict[str, type[BaseModel]] = {
    "turn.start": TurnStart,
    "text.delta": TextDelta,
    "thinking": Thinking,
    "tool.start": ToolStart,
    "tool.end": ToolEnd,
    "pending": Pending,
    "usage": UsageEvent,
    "notice": NoticeEvent,
    "error": ErrorEvent,
    "turn.end": TurnEnd,
}
BUS_MODELS: dict[str, type[BaseModel]] = {"assistant.connection": ConnectionEvent, "assistant.dialogs": DialogsEvent}


def parse_event(name: str, data: Any) -> BaseModel:
    """The model of the event `name` of a turn stream or of the bus; ValueError (also pydantic's) if it does not fit -
    for the tests and the readers of the stream."""
    model = EVENT_MODELS.get(name) or BUS_MODELS.get(name)
    if model is None:
        raise ValueError(f"unknown event: {name}")
    return model.model_validate(data)
