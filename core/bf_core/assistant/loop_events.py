"""The items, the blocks and the totals of the loop (подшаг 23.10; e23-api.md §7, §10; ADR 0029 решения 5, 19, 21-23).

Plain dicts of the forms of `schemas_dialog.py` (the tests check every event with `schemas.parse_event`); no network, no
store here. `loop.py` runs the cycle, `loop_call.py` reads one stream of the API.

    user_item / assistant_item / tool_item / notice_item / error_item     the `Item`s of the ribbon
    tool_result(tool_use_id, text, is_error)                              a `tool_result` block of the API
    blocks_of(content) / text_only(blocks)                                blocks of an answer as the API takes them
    status_of(ok, text)                                                   `ok` | `error` | `declined`
    merge_items(items)                                                    the same `id` twice: the last wins
    Tally                                                                 totals of the dialog for the `usage` events
"""

from __future__ import annotations

import logging
import math
import os
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from . import data_tools, errors, prefs, prices, tools, usage
from . import store_parts as sp
from .connection import Connection
from .schemas import ToolResult
from .schemas_base import EFFORTS, TOOL_TEXT_MAX
from .segments import SEGMENTS, Segments
from .store import Dialogs, Loaded

CLIP = "…"
NO_EFFORT = "off"  # the depth that is not sent at all (a model that takes none)
log = logging.getLogger("bf_core.assistant")


def new_id(prefix: str) -> str:
    """An id of an item: unique in a dialog (the ids of the API repeat in mocks and are not used for it)."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def clip(text: str, limit: int = TOOL_TEXT_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 1] + CLIP


# --- items ------------------------------------------------------------------------------------------------------------


def user_item(text: str, at: str | None = None, item_id: str | None = None) -> dict[str, Any]:
    return {"kind": "user", "id": item_id or new_id("u"), "at": at or sp.stamp(), "text": text}


def assistant_item(
    item_id: str, model: str, text: str, *, stopped: bool = False, interrupted: bool = False, at: str | None = None
) -> dict[str, Any]:
    return {
        "kind": "assistant",
        "id": item_id,
        "at": at or sp.stamp(),
        "model": model,
        "text": text,
        "stopped": stopped,
        "interrupted": interrupted,
    }


def tool_item(
    *,
    item_id: str,
    tool_use_id: str,
    name: str,
    side: str,
    title: str,
    args: Mapping[str, Any],
    status: str,
    summary: str = "",
    at: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": "tool",
        "id": item_id,
        "at": at or sp.stamp(),
        "tool_use_id": tool_use_id,
        "name": name,
        "side": side,
        "title": title,
        "args": dict(args),
        "status": status,
        "summary": clip(summary),
    }


def notice_item(code: str, at: str | None = None) -> dict[str, Any]:
    return {
        "kind": "notice",
        "id": new_id("n"),
        "at": at or sp.stamp(),
        "code": code,
        "message": errors.notice_message(code),
    }


def error_item(code: str, message: str | None = None, hint: str | None = None, at: str | None = None) -> dict[str, Any]:
    msg, tip = errors.error_fields(code, message, hint)
    return {"kind": "error", "id": new_id("e"), "at": at or sp.stamp(), "code": code, "message": msg, "hint": tip}


def merge_items(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The items of a dialog read from its parts: an item that was written again (a card of a command of the window
    first `waiting`, later with its result) keeps its first place and its last form."""
    out: list[dict[str, Any]] = []
    at: dict[str, int] = {}
    for item in items:
        key = str(item.get("id"))
        if key in at:
            out[at[key]] = dict(item)
        else:
            at[key] = len(out)
            out.append(dict(item))
    return out


def status_of(ok: bool, text: str) -> str:
    """The status of the card of a command of the window by its result: a refusal of the user is `declined`."""
    if ok:
        return "ok"
    return "declined" if text in (errors.TEXT_DECLINED, errors.TEXT_STOPPED, errors.TEXT_SKIPPED) else "error"


# --- blocks of the API ------------------------------------------------------------------------------------------------


def tool_result(tool_use_id: str, text: str, is_error: bool) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": text or "(пусто)"}
    if is_error:
        block["is_error"] = True
    return block


def blocks_of(content: Iterable[Any]) -> list[dict[str, Any]]:
    """The content blocks of an answer of the API as parameters of the next request. The blocks are built field by
    field (the objects of the SDK carry fields the API does not take back); thinking keeps its signature; an empty text
    block is dropped (the API refuses it)."""
    out: list[dict[str, Any]] = []
    for b in content:
        kind = getattr(b, "type", None)
        if kind == "text":
            text = getattr(b, "text", "") or ""
            if text.strip():
                out.append({"type": "text", "text": text})
        elif kind == "thinking":
            out.append(
                {
                    "type": "thinking",
                    "thinking": getattr(b, "thinking", "") or "",
                    "signature": getattr(b, "signature", "") or "",
                }
            )
        elif kind == "redacted_thinking":
            out.append({"type": "redacted_thinking", "data": getattr(b, "data", "") or ""})
        elif kind == "tool_use":
            out.append(
                {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input if isinstance(b.input, dict) else {}}
            )
        elif hasattr(b, "model_dump"):  # a block of another kind (a server tool): as it came
            out.append(b.model_dump(exclude_none=True, mode="json"))
    return out


def text_only(blocks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """What stays of an answer that was stopped, cut or refused: its text. Unfinished `tool_use` and the thinking that
    belongs to them are dropped (решение 7: they would leave a call without a result)."""
    return [dict(b) for b in blocks if b.get("type") == "text"]


def tool_uses(blocks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(b) for b in blocks if b.get("type") == "tool_use"]


# --- totals -----------------------------------------------------------------------------------------------------------


class Tally:
    """The totals of a dialog while a segment runs: what was stored before + the calls of the segment. A call of a model
    without a price makes the cost of the dialog and of the segment unknown (`None`), never 0 (ТЗ 7.32)."""

    def __init__(self, meta: Mapping[str, Any] | None = None, questions: int = 0) -> None:
        meta = meta or sp.new_meta()
        self._base_cost = float(meta.get("cost_usd") or 0.0)
        self._unknown = bool(meta.get("cost_unknown"))
        self._base_tokens = {k: int(meta["tokens"].get(k) or 0) for k in sp.TOKEN_KEYS}
        self._questions = int(meta.get("questions") or 0) + questions
        self.tokens = dict.fromkeys(sp.TOKEN_KEYS, 0)  # of the segment
        self._cost = 0.0
        self._cost_unknown = False
        self.calls = 0

    def add(self, tokens: Mapping[str, int], cost: float | None) -> None:
        self.calls += 1
        for k in sp.TOKEN_KEYS:
            self.tokens[k] += int(tokens.get(k) or 0)
        if cost is None or not math.isfinite(cost):
            self._cost_unknown = True
        else:
            self._cost += cost

    @property
    def dialog_cost(self) -> float | None:
        if self._unknown or self._cost_unknown:
            return None
        return round(self._base_cost + self._cost, 6)

    @property
    def dialog_tokens(self) -> dict[str, int]:
        return {k: self._base_tokens[k] + self.tokens[k] for k in sp.TOKEN_KEYS}

    def dialog(self) -> dict[str, Any]:
        """`dialog` of the `usage` event."""
        return {"cost_usd": self.dialog_cost, "tokens": self.dialog_tokens, "questions": self._questions}

    def segment_usage(self) -> dict[str, Any] | None:
        """`usage` for `Dialogs.append`: the sum over the calls of the segment (None - no call was made)."""
        if not self.calls:
            return None
        return {**self.tokens, "cost_usd": None if self._cost_unknown else round(self._cost, 9)}


# --- what the loop is given -----------------------------------------------------------------------------------------

#: model -> the deepest `effort` that the API accepted (learned from a 400 and a call that went through after it;
#: решение 14, № 127; `NO_EFFORT` - the model takes none). Not kept on disk.
EFFORT_CAP: dict[str, str] = {}


@dataclass
class LoopEnv:
    """What the loop needs from the core: the connection, the dialogs of the open project (or none: temporary dialogs)
    and the registry of segments. `on_saved(head)` is called after every part written (the route sends the event
    `assistant.dialogs` of the bus)."""

    connection: Connection
    dialogs: Dialogs
    segments: Segments = SEGMENTS
    on_saved: Callable[[dict[str, Any]], None] | None = None


@dataclass
class Plan:
    kind: str  # "question" | "continue"
    text: str | None
    results: list[ToolResult]
    context: dict[str, Any]
    api_tools: list[dict[str, Any]]  # with the mark of the cache on the last
    known_ui: frozenset[str]
    model: str
    effort: str
    head: dict[str, Any]
    loaded: Loaded | None
    new_dialog: bool
    pending: dict[str, Any] | None = None  # the stored pending of the dialog (with the results of its data tools)
    set_id: int | None = None
    rounds: int = 0  # calls of the API of this question made before this segment


def last_uses(messages: list[Any]) -> list[dict[str, Any]]:
    """The `tool_use` blocks of the last message of the history if it is an answer of Claude."""
    if not messages or messages[-1].get("role") != "assistant" or not isinstance(messages[-1].get("content"), list):
        return []
    return tool_uses(messages[-1]["content"])


def lower_effort(effort: str, kind: Literal["level", "param"] = "level") -> str | None:
    """The next depth after a refusal of `effort`: one below it (`level`), and after `low` - none at all
    (`NO_EFFORT`); `param` (the model takes no depth) goes to `NO_EFFORT` at once. None - nothing left to try."""
    if effort == NO_EFFORT:
        return None
    if kind == "param":
        return NO_EFFORT
    i = EFFORTS.index(effort) if effort in EFFORTS else 0
    return EFFORTS[i - 1] if i > 0 else NO_EFFORT


def capped(model: str, effort: str) -> str:
    cap = EFFORT_CAP.get(model)
    if cap == NO_EFFORT:
        return NO_EFFORT
    if cap and effort in EFFORTS and cap in EFFORTS and EFFORTS.index(effort) > EFFORTS.index(cap):
        return cap
    return effort


def resolved(conn: Connection) -> prefs.Resolved:
    return conn.resolved()


def tools_of(ui_tools: list[Any]) -> tuple[list[dict[str, Any]], frozenset[str]]:
    api = tools.build_api_tools(ui_tools, data_tools.definitions())
    return tools.with_cache_control(api), tools.known_ui_names(api)


def set_of(context: dict[str, Any]) -> int | None:
    value = (context.get("set") or {}).get("id")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


# --- pieces of the end of a call (kept out of `loop.py`) ------------------------------------------------------------


def record_call(
    res: Any,
    *,
    data_dir: str | os.PathLike[str] | None,
    head: Mapping[str, Any],
    project: Any,
    tally: Tally,
    models: list[str],
) -> dict[str, Any] | None:
    """The cost of a call: the usage file, the totals; returns the data of the event `usage` (None - nothing billed)."""
    u = res.usage
    if not any(u.values()):
        return None
    cost = prices.cost(res.model, u["input"], u["output"], u["cache_read"], u["cache_write"])
    long = prices.is_long(res.model, u["input"], u["cache_read"], u["cache_write"])
    try:
        usage.record(
            data_dir,
            model=res.model,
            input=u["input"],
            output=u["output"],
            cache_read=u["cache_read"],
            cache_write=u["cache_write"],
            dialog_uid=head.get("uid"),
            project_uid=getattr(project, "uid", None),
        )
    except (OSError, ValueError):  # a lost line of the account must not break the turn
        log.warning("assistant: the line of usage was not written", exc_info=True)
    tally.add(u, cost)
    if res.model not in models:
        models.append(res.model)
    return {"call": {"model": res.model, **u, "cost_usd": cost, "long": long}, "dialog": tally.dialog()}


def card_of(
    entry: Mapping[str, Any], status: str, text: str, title: str | None, stored: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """The finished card of a command of the window: the waiting card of the same `item_id` (in `stored`) gives the
    title and the time."""
    old: Mapping[str, Any] = next((i for i in reversed(stored) if i.get("id") == entry["item_id"]), {})
    return tool_item(
        item_id=entry["item_id"],
        tool_use_id=entry["tool_use_id"],
        name=entry["name"],
        side="ui",
        title=title or old.get("title") or entry["name"],
        args=entry.get("args") or {},
        status=status,
        summary=text,
        at=old.get("at"),
    )


def pending_of(
    ui_calls: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]], done: Mapping[str, Any], rounds: int
) -> dict[str, Any]:
    """The `pending` of a dialog: the commands of the window to run, the results of the data tools already got."""
    return {
        "id": "p-" + uuid.uuid4().hex[:16],
        "created_at": sp.stamp(),
        "tools": [
            {
                "tool_use_id": u["id"],
                "name": u["name"],
                "command": None,
                "args": u.get("input") or {},
                "item_id": c["id"],
            }
            for u, c in ui_calls
        ],
        "data_results": list(done.values()),
        "rounds": rounds,
    }
