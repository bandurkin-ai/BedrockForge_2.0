"""Wire format of the Anthropic mock: SSE events of a message, a plain message, model info, errors (stage E23)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    413: "request_too_large",
    429: "rate_limit_error",
    500: "api_error",
    529: "overloaded_error",
}
STOP_REASONS = {
    "end_turn", "max_tokens", "tool_use", "pause_turn", "refusal", "model_context_window_exceeded", "stop_sequence",
}
_SHORT_USAGE = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_read": "cache_read_input_tokens",
    "cache_write": "cache_creation_input_tokens",
}

Event = tuple[str, dict[str, Any]]


def error_body(status: int, message: str | None = None, error_type: str | None = None) -> dict[str, Any]:
    kind = error_type or ERROR_TYPES.get(status, "api_error")
    return {
        "type": "error",
        "error": {"type": kind, "message": message or f"mock error {status}"},
        "request_id": "req_mock" + uuid.uuid4().hex[:12],
    }


def usage_of(reply: dict[str, Any]) -> dict[str, int]:
    """API names; the short ones (input, output, cache_read, cache_write) of a scenario are accepted."""
    u = {"input_tokens": 1200, "output_tokens": 60, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    for k, v in (reply.get("usage") or {}).items():
        u[_SHORT_USAGE.get(k, k)] = int(v)
    return u


def _pieces(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def content_blocks(reply: dict[str, Any], next_id: Callable[[], int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in reply.get("content") or []:
        if b["type"] == "tool_use":
            tool_id = b.get("id") or f"toolu_mock{next_id():06d}"
            out.append({"type": "tool_use", "id": tool_id, "name": b["name"], "input": b.get("input") or {}})
        elif b["type"] == "thinking":
            sign = b.get("signature") or "c2ln"
            out.append({"type": "thinking", "thinking": b.get("thinking", ""), "signature": sign})
        else:
            out.append({"type": "text", "text": b.get("text", "")})
    return out


def _stop_reason(reply: dict[str, Any], blocks: list[dict[str, Any]]) -> str:
    stop = reply.get("stop_reason") or ("tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn")
    if stop not in STOP_REASONS:
        raise ValueError(f"unknown stop_reason {stop!r}")
    return str(stop)


def plain_message(reply: dict[str, Any], model: str, next_id: Callable[[], int]) -> dict[str, Any]:
    blocks = content_blocks(reply, next_id)
    return {
        "id": f"msg_mock{next_id():06d}", "type": "message", "role": "assistant", "model": model, "content": blocks,
        "stop_reason": _stop_reason(reply, blocks), "stop_sequence": None, "usage": usage_of(reply),
    }


def message_events(reply: dict[str, Any], model: str, next_id: Callable[[], int]) -> list[Event]:
    """message_start, then per block: start, deltas, stop; message_delta (stop reason, usage), message_stop."""
    blocks = content_blocks(reply, next_id)
    u, size = usage_of(reply), int(reply.get("chunk") or 12)
    msg = {
        "id": f"msg_mock{next_id():06d}", "type": "message", "role": "assistant", "model": model, "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {**u, "output_tokens": 1},
    }
    ev: list[Event] = [("message_start", {"type": "message_start", "message": msg})]

    def delta(i: int, d: dict[str, Any]) -> None:
        ev.append(("content_block_delta", {"type": "content_block_delta", "index": i, "delta": d}))

    for i, b in enumerate(blocks):
        start = {"type": b["type"], **_empty_block(b)}
        ev.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": start}))
        if b["type"] == "text":
            for p in _pieces(b["text"], size):
                delta(i, {"type": "text_delta", "text": p})
        elif b["type"] == "thinking":
            for p in _pieces(b["thinking"], size):
                delta(i, {"type": "thinking_delta", "thinking": p})
            delta(i, {"type": "signature_delta", "signature": b["signature"]})
        else:
            raw = json.dumps(b["input"], ensure_ascii=False)
            cut = max(1, len(raw) // 2)
            for p in (raw[:cut], raw[cut:]):
                if p:
                    delta(i, {"type": "input_json_delta", "partial_json": p})
        ev.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    stop = {"stop_reason": _stop_reason(reply, blocks), "stop_sequence": None}
    ev.append(("message_delta", {"type": "message_delta", "delta": stop, "usage": u}))
    ev.append(("message_stop", {"type": "message_stop"}))
    return ev


def _empty_block(b: dict[str, Any]) -> dict[str, Any]:
    if b["type"] == "text":
        return {"text": ""}
    if b["type"] == "thinking":
        return {"thinking": "", "signature": ""}
    return {"id": b["id"], "name": b["name"], "input": {}}


def model_info(model_id: str) -> dict[str, Any]:
    yes, no = {"supported": True}, {"supported": False}
    return {
        "id": model_id, "type": "model", "display_name": model_id, "created_at": "2025-01-01T00:00:00Z",
        "lifecycle": "active", "max_input_tokens": 200000, "max_tokens": 64000,
        "capabilities": {
            "batch": yes, "citations": yes, "code_execution": yes, "image_input": yes, "pdf_input": yes,
            "structured_outputs": yes, "context_management": {"supported": True},
            "effort": {"supported": True, "low": yes, "medium": yes, "high": yes, "max": yes, "xhigh": yes},
            "server_tools": {"supported": True, "code_execution": yes, "web_search": yes},
            "thinking": {
                "supported": True,
                "types": {"adaptive": yes, "between_tools": no, "disabled": yes, "enabled": yes},
            },
        },
    }
