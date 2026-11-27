"""One call of the Claude API: the stream of the SDK -> the events of the segment (подшаг 23.10; ADR 0029 решения 5-7;
e23-api.md §10, §12).

    params = request_params(model=, effort=, system=, tools=, messages=)    the body of `messages.stream(...)`
    result = stream_message(client, params, seg, item_id, model)            never raises; see `CallResult`
    classify(exc, started=, cancelled=) -> "cancelled" | "interrupted" | "api"

The text is sent to the window as `text.delta {item_id, text}`: the first piece at once, the next ones joined when they
come faster than `DELTA_EVERY_S` (a long answer stays within the 2000 events of a segment). The thinking of Claude is
not shown (№ 132: `display: omitted`): only `thinking {state}` for «Claude думает…». A stream cut after its first event
is
`interrupted` (the partial text is kept by the loop, no retry); the SDK itself retries what failed before the first
event
(`connection.MAX_RETRIES`). The key and the tokens never appear here: the client carries them.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import anthropic
import httpx2

from .loop_events import NO_EFFORT, blocks_of
from .segments import Segment

log = logging.getLogger("bf_core.assistant")

MAX_TOKENS = 16_000  # решение 5
DELTA_EVERY_S = 0.05
USAGE_KEYS = ("input", "output", "cache_read", "cache_write")


def request_params(
    *,
    model: str,
    effort: str,
    system: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """The arguments of `client.messages.stream` (решение 5, 14): the system prompt and the last tool carry the
    mark of the cache, the whole request a mark of its own (the cache of the history); the depth is
    `output_config.effort` with `thinking: adaptive`, the thoughts are not returned (`omitted`)."""
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "messages": messages,
        "thinking": {"type": "adaptive", "display": "omitted"},
        "cache_control": {"type": "ephemeral"},
    }
    if effort != NO_EFFORT:  # the model that takes no depth at all gets none
        params["output_config"] = {"effort": effort}
    if tools:
        params["tools"] = tools
    return params


@dataclass
class CallResult:
    model: str
    started: bool = False  # the stream gave its first event
    content: list[dict[str, Any]] = field(
        default_factory=list
    )  # blocks of the finished answer, as the API takes them back
    text: str = ""  # what was sent to the window (all of it, also when the stream broke)
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=lambda: dict.fromkeys(USAGE_KEYS, 0))
    cancelled: bool = False
    error: BaseException | None = None
    finished: bool = False  # the final message of the stream was read

    @property
    def tokens(self) -> int:
        return sum(self.usage.values())


def _usage_of(raw: Any) -> dict[str, int]:
    def n(name: str) -> int:
        value = getattr(raw, name, 0)
        return int(value) if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    return {
        "input": n("input_tokens"),
        "output": n("output_tokens"),
        "cache_read": n("cache_read_input_tokens"),
        "cache_write": n("cache_creation_input_tokens"),
    }


class _Deltas:
    """Joins the pieces of text: the first goes out at once, the next ones at most every `DELTA_EVERY_S`."""

    def __init__(self, send: Callable[[str], object]) -> None:
        self._send = send
        self._held: list[str] = []
        self._last = 0.0
        self.all: list[str] = []

    def add(self, text: str) -> None:
        if not text:
            return
        self.all.append(text)
        self._held.append(text)
        now = time.monotonic()
        if now - self._last >= DELTA_EVERY_S:
            self.flush()

    def flush(self) -> None:
        if self._held:
            piece, self._held = "".join(self._held), []
            self._last = time.monotonic()
            self._send(piece)


def stream_message(client: Any, params: dict[str, Any], seg: Segment, item_id: str, model: str) -> CallResult:
    """Runs one call and streams it into `seg`. Never raises: a failure is `result.error` (with `result.started` telling
    whether the stream had begun), a cancel is `result.cancelled`."""
    res = CallResult(model=model)
    deltas = _Deltas(lambda t: seg.emit("text.delta", {"item_id": item_id, "text": t}))
    thinking = False
    stream: Any = None
    try:
        with client.messages.stream(**params) as stream:
            seg.set_closer(stream.close)
            for ev in stream:
                if seg.cancelled:
                    break
                kind = ev.type
                if kind == "message_start":
                    res.started = True
                    res.model = getattr(ev.message, "model", None) or model
                elif kind == "content_block_start":
                    if getattr(ev.content_block, "type", None) in ("thinking", "redacted_thinking"):
                        thinking = True
                        seg.emit("thinking", {"state": "start"})
                elif kind == "content_block_delta":
                    if getattr(ev.delta, "type", None) == "text_delta":
                        deltas.add(ev.delta.text)
                elif kind == "content_block_stop":
                    deltas.flush()
                    if thinking:
                        thinking = False
                        seg.emit("thinking", {"state": "end"})
            if not seg.cancelled:
                final = stream.get_final_message()
                res.model = getattr(final, "model", None) or res.model
                res.stop_reason = getattr(final, "stop_reason", None)
                res.content = blocks_of(final.content)
                res.usage = _usage_of(final.usage)
                res.finished = True
    except Exception as exc:  # noqa: BLE001 - classified by the loop
        res.error = exc
    finally:
        seg.set_closer(None)
        deltas.flush()
        if thinking:
            seg.emit("thinking", {"state": "end"})
    res.text = "".join(deltas.all)
    res.cancelled = seg.cancelled
    if not res.finished and stream is not None:  # a cut stream still tells what the input cost
        try:
            snap = stream.current_message_snapshot
            res.usage = _usage_of(snap.usage)
        except Exception:  # noqa: BLE001 - no snapshot yet
            pass
    return res


def is_transport_error(exc: BaseException) -> bool:
    """A lost connection, a timeout or a protocol failure of the HTTP layer (the SDK lets them out of an open stream
    as they are: `httpx2.RemoteProtocolError` for a cut chunked body)."""
    return isinstance(exc, httpx2.TransportError | anthropic.APIConnectionError)


Kind = Literal["cancelled", "interrupted", "api"]


def classify(res: CallResult) -> Kind:
    """What a failed call is: the user stopped it; the stream broke after it began (partial text is kept, no retry);
    or the API refused / was not reached (mapped by `Connection.api_error`)."""
    if res.cancelled:
        return "cancelled"
    exc = res.error
    if exc is None:
        return "api"
    if res.started and (is_transport_error(exc) or isinstance(exc, anthropic.APIStatusError)):
        return "interrupted"
    return "api"


#: the words of a 400 that refuses the parameter as a whole (not one of its values)
_PARAM_MARKS = ("extra inputs", "not permitted", "unrecognized", "does not support effort",
                "doesn't support effort", "effort parameter")  # fmt: skip


def effort_refusal(exc: BaseException, effort: str) -> Literal["level", "param"] | None:
    """A 400 of the API about the depth (`output_config.effort`, решение 14): "level" - this depth is not supported by
    the model (a lower one may be), "param" - the model takes no depth at all (the parameter goes). None - another
    failure. The level is told by its name in the text («'max' is not supported»); a text that only names the
    parameter is "param"; anything else about effort is taken as a level."""
    if not isinstance(exc, anthropic.BadRequestError):
        return None
    text = str(getattr(exc, "message", exc)).lower()
    if "effort" not in text:
        return None
    named = f"'{effort}'" in text or f'"{effort}"' in text
    if not named and any(mark in text for mark in _PARAM_MARKS):
        return "param"
    return "level"
