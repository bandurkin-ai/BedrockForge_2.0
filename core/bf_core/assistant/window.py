"""The window of the history of a dialog for a request to Claude (подшаг 23.9; ТЗ 7.32; ADR 0029 decision 29).

The dialog keeps all messages; the request carries a window of them. Tokens are estimated, not counted: characters / 3
(the Russian text and JSON are about three characters a token; the real count comes back in `usage`).

    estimate_tokens(value) -> int                     characters / 3 of a string or of the JSON of a value
    estimate_messages(messages, extra_tokens=0)       the whole request: messages + system + tools (`extra_tokens`)
    cycle_start(messages) -> int                      index of the message that opens the current cycle
    trim_messages(messages, limit=..., extra_tokens=0, force=False) -> Windowed
    shrink_for_retry(messages, extra_tokens=0)        the trim for `model_context_window_exceeded`; None - nothing left
    is_context_exceeded(stop_reason=, error=) -> bool this answer / error means «the context does not fit»

The current cycle is everything from the last message of the user that is not only `tool_result`s (the question) to the
end: its tool results and thoughts are never touched (the API wants the thinking blocks of the running cycle back, and
the answer being built needs the results). Over the limit, the part before it shrinks in the order of the decision:
1. old `tool_result`s become «[результат сокращён]», the oldest first, until the estimate fits;
2. the thinking blocks of the previous questions are dropped;
3. (beyond the ADR: each old question carries its `<workspace>`, up to 4 KB) old `<workspace>` blocks become one line.
The input is never modified. The pairing `tool_use` / `tool_result` stays intact (only the text of a result changes).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

CONTEXT_LIMIT_TOKENS = 150_000  # decision 29: above - trim
CHARS_PER_TOKEN = 3
MAX_RETRIES = 1  # `model_context_window_exceeded`: one more trim and one more call
STOP_CONTEXT_EXCEEDED = "model_context_window_exceeded"
TRIMMED_RESULT = "[результат сокращён]"
TRIMMED_WORKSPACE = "<workspace>[контекст экрана сокращён]</workspace>"
_THINKING = ("thinking", "redacted_thinking")
_ERROR_MARKS = (STOP_CONTEXT_EXCEEDED, "prompt is too long", "context window", "context_length_exceeded")

Message = dict[str, Any]


def estimate_tokens(value: Any) -> int:
    """characters / 3, rounded up: of a string as it is, of anything else as compact JSON (non-ASCII kept)."""
    text = (
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    )
    return -(-len(text) // CHARS_PER_TOKEN)


def estimate_messages(messages: list[Message], extra_tokens: int = 0) -> int:
    """The estimate of a request: its messages plus `extra_tokens` (the system prompt and the tools)."""
    return extra_tokens + sum(estimate_tokens(m) for m in messages)


def _blocks(message: Message) -> list[Any]:
    content = message.get("content")
    return content if isinstance(content, list) else []


def _is_question(message: Message) -> bool:
    """A message of the user that is not only results of tools: it opens a cycle."""
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if isinstance(content, str):
        return True
    return any(not (isinstance(b, dict) and b.get("type") == "tool_result") for b in _blocks(message))


def cycle_start(messages: list[Message]) -> int:
    """Index of the last question of the user; 0 when there is none (the whole list is one cycle)."""
    for i in range(len(messages) - 1, -1, -1):
        if _is_question(messages[i]):
            return i
    return 0


@dataclass(frozen=True)
class Windowed:
    messages: list[Message]  # a new list; the messages that were not touched are the same objects
    tokens: int  # the estimate of the whole request after the trim
    changed: bool  # something was shortened: the loop sends the notice `context_trimmed`
    fits: bool  # the estimate is within the limit


def _replace_results(message: Message) -> Message | None:
    changed = False
    blocks: list[Any] = []
    for b in _blocks(message):
        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("content") != TRIMMED_RESULT:
            if estimate_tokens(b.get("content", "")) > estimate_tokens(TRIMMED_RESULT):
                b = {**b, "content": TRIMMED_RESULT}
                changed = True
        blocks.append(b)
    return {**message, "content": blocks} if changed else None


def _drop_thinking(message: Message) -> Message | None:
    if message.get("role") != "assistant":
        return None
    blocks = _blocks(message)
    kept = [b for b in blocks if not (isinstance(b, dict) and b.get("type") in _THINKING)]
    if len(kept) == len(blocks) or not kept:  # nothing to drop, or the message would be empty
        return None
    return {**message, "content": kept}


def _replace_workspace(message: Message) -> Message | None:
    if message.get("role") != "user":
        return None
    changed = False
    blocks: list[Any] = []
    for b in _blocks(message):
        text = b.get("text") if isinstance(b, dict) and b.get("type") == "text" else None
        if isinstance(text, str) and text.startswith("<workspace>") and text != TRIMMED_WORKSPACE:
            b = {**b, "text": TRIMMED_WORKSPACE}
            changed = True
        blocks.append(b)
    return {**message, "content": blocks} if changed else None


def trim_messages(
    messages: list[Message],
    *,
    limit: int = CONTEXT_LIMIT_TOKENS,
    extra_tokens: int = 0,
    force: bool = False,
) -> Windowed:
    """The window for a request. Within `limit` (estimate of messages + `extra_tokens`) nothing changes; over it the
    part before the current cycle shrinks step by step (see the module text) until it fits. `force` applies every step
    to every old message whatever the estimate (the retry after `model_context_window_exceeded`)."""
    out = list(messages)
    old = cycle_start(out)
    changed = False
    sizes = [estimate_tokens(m) for m in out]  # measured once: a replaced message only moves the running sum
    running = extra_tokens + sum(sizes)

    def apply(step: Any) -> None:
        nonlocal changed, running
        for i in range(old):  # oldest first
            if not force and running <= limit:
                return
            new = step(out[i])
            if new is not None:
                size = estimate_tokens(new)
                running += size - sizes[i]
                sizes[i] = size
                out[i] = new
                changed = True

    if force or running > limit:
        apply(_replace_results)
        apply(_drop_thinking)
        apply(_replace_workspace)
    tokens = running
    return Windowed(out, tokens, changed, tokens <= limit)


def shrink_for_retry(
    messages: list[Message], *, limit: int = CONTEXT_LIMIT_TOKENS, extra_tokens: int = 0
) -> Windowed | None:
    """After `model_context_window_exceeded` (or «prompt is too long»): every old result, thought and screen context
    is shortened at once. None when there is nothing more to shorten - the loop then gives up with `assistant_api_error`
    (retry only `MAX_RETRIES` times)."""
    result = trim_messages(messages, limit=limit, extra_tokens=extra_tokens, force=True)
    return result if result.changed else None


def is_context_exceeded(*, stop_reason: str | None = None, error: BaseException | str | None = None) -> bool:
    """True for the stop reason `model_context_window_exceeded` of an answer, or for an API error that says the
    prompt does not fit («prompt is too long», a context window)."""
    if stop_reason == STOP_CONTEXT_EXCEEDED:
        return True
    if error is None:
        return False
    text = str(error).lower()
    return any(mark in text for mark in _ERROR_MARKS)
