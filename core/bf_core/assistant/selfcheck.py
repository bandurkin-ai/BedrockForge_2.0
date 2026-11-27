"""Self-check of the assistant's dependencies for `bf-core --selftest` (подшаг 23.18; ADR 0029 решение 37).

PyInstaller loses what it cannot see: modules imported by a name, native extensions, package metadata. The chain of
the SDK has all three (`jiter` is a Rust extension, `opentelemetry` reads its entry points through the metadata of
`opentelemetry-api`, the SDK modules are many and lazy). Every step here is what the assistant does at a turn, without
the network and without a key:

    run_checks() -> dict      the facts of the check (versions, the text of the stream); raises on the first failure
                              (what `diag/selftest.py::check_assistant` returns, as the other `check_*` do)
    check_assistant() -> str | None     None - all is well, else one line with the reason (never raises)

The stream of the Messages API is served by `httpx2.MockTransport` in this process: a text block and a `tool_use`
block whose input comes in two `input_json_delta` pieces (the SDK joins them with `jiter`).
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import ssl
from typing import Any

#: Modules of the SDK and of its chain that the freezing may lose (a lazy or a dynamic import).
MODULES = (
    "anthropic",
    "anthropic.types",
    "anthropic.resources.messages",
    "anthropic.lib.streaming",
    "anthropic.lib.streaming._messages",
    "anthropic._tracing",
    "anthropic._data.deprecated_models",
    "httpx2",
    "httpcore2",
    "truststore",
    "jiter",
    "opentelemetry.trace",
    "opentelemetry.context",
    "opentelemetry.context.contextvars_context",
    "opentelemetry.propagate",
)
TEXT = "Проверка связи: ответ разобран."
TOOL_INPUT = {"hole_id": "DH-012", "depth": 12.5}


def _sse(events: list[tuple[str, dict[str, Any]]]) -> bytes:
    return "".join(f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n" for name, data in events).encode()


def _stream_body() -> bytes:
    """A message of the Messages API as the stream sends it: a text, then a call of a tool with split JSON."""
    usage = {"input_tokens": 12, "output_tokens": 1, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    message: dict[str, Any] = {
        "id": "msg_selfcheck", "type": "message", "role": "assistant", "model": "claude-selfcheck", "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": usage,
    }  # fmt: skip
    raw = json.dumps(TOOL_INPUT)
    cut = len(raw) // 2
    block = "content_block_start"
    delta = "content_block_delta"
    events: list[tuple[str, dict[str, Any]]] = [
        ("message_start", {"type": "message_start", "message": message}),
        (block, {"type": block, "index": 0, "content_block": {"type": "text", "text": ""}}),
        (delta, {"type": delta, "index": 0, "delta": {"type": "text_delta", "text": TEXT}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            block,
            {
                "type": block, "index": 1,
                "content_block": {"type": "tool_use", "id": "toolu_selfcheck", "name": "data_summary", "input": {}},
            },
        ),
        (delta, {"type": delta, "index": 1, "delta": {"type": "input_json_delta", "partial_json": raw[:cut]}}),
        (delta, {"type": delta, "index": 1, "delta": {"type": "input_json_delta", "partial_json": raw[cut:]}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        (
            "message_delta",
            {
                "type": "message_delta", "usage": {**usage, "output_tokens": 20},
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]  # fmt: skip
    return _sse(events)


def _check_imports() -> None:
    for name in MODULES:
        importlib.import_module(name)


def _check_opentelemetry() -> str:
    """The entry points of `opentelemetry-api` (its metadata must ship with the build) and a tracer of the SDK."""
    from opentelemetry import trace

    version = importlib.metadata.version("opentelemetry-api")
    found = {e.name for e in importlib.metadata.entry_points(group="opentelemetry_tracer_provider")}
    assert "default_tracer_provider" in found, f"no entry points of opentelemetry-api: {sorted(found)}"
    trace.get_tracer("bf-selfcheck")  # loads the default provider through the entry point
    return version


def _check_aes_gcm() -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key, nonce = os.urandom(32), os.urandom(12)
    box = AESGCM(key).encrypt(nonce, "секрет".encode(), b"selfcheck")
    assert AESGCM(key).decrypt(nonce, box, b"selfcheck").decode() == "секрет"


def _check_tls() -> None:
    import truststore

    truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # the store of the OS (Windows: the certificate store)


def _check_stream() -> dict[str, Any]:
    import anthropic
    import httpx2

    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.path)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_stream_body())

    client = anthropic.Anthropic(
        api_key="selfcheck",  # no real key; the transport below never leaves the process
        base_url="http://selfcheck.invalid",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    try:
        with client.messages.stream(
            model="claude-selfcheck", max_tokens=16, messages=[{"role": "user", "content": "ping"}]
        ) as stream:
            pieces = list(stream.text_stream)
            final = stream.get_final_message()
    finally:
        client.close()
    assert seen == ["/v1/messages"], seen
    assert "".join(pieces) == TEXT, pieces
    tool = [b for b in final.content if b.type == "tool_use"]
    assert len(tool) == 1 and tool[0].input == TOOL_INPUT, [b.type for b in final.content]
    assert final.stop_reason == "tool_use" and final.usage.output_tokens == 20, final.stop_reason
    return {"text": "".join(pieces), "tool": tool[0].name, "stop_reason": final.stop_reason}


def run_checks() -> dict[str, Any]:
    """All the steps; the first failure raises (an `ImportError`, an `AssertionError`, …) with its own text."""
    import anthropic
    import httpx2
    import jiter

    _check_imports()
    otel = _check_opentelemetry()
    _check_aes_gcm()
    _check_tls()
    assert jiter.from_json(b'{"a": [1, 2.5, null]}') == {"a": [1, 2.5, None]}  # the native extension
    stream = _check_stream()
    return {
        "anthropic": anthropic.__version__,
        "httpx2": httpx2.__version__,
        "truststore": importlib.metadata.version("truststore"),
        "jiter": importlib.metadata.version("jiter"),
        "opentelemetry_api": otel,
        "modules": len(MODULES),
        **stream,
    }


def check_assistant() -> str | None:
    """None when the assistant's dependencies work in this build; else a line with the reason."""
    try:
        run_checks()
    except Exception as exc:  # noqa: BLE001 - the answer is the text, whatever failed
        return f"{type(exc).__name__}: {exc}"[:300]
    return None
