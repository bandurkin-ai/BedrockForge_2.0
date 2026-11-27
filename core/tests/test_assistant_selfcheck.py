"""The self-check of the assistant's dependencies (подшаг 23.18; ADR 0029 решение 37): what `bf-core --selftest` runs
in the build of the .exe — the chain of the SDK imports, `opentelemetry-api` has its metadata, AES-GCM and `jiter`
work, a stream of the Messages API is parsed through `httpx2.MockTransport`. No network: a socket fails the test."""

from __future__ import annotations

import importlib.metadata
import socket
from typing import Any

import pytest

from bf_core.assistant import selfcheck


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: Any, **_k: Any) -> None:
        raise AssertionError("the self-check must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def test_all_steps_pass_and_report_the_facts() -> None:
    facts = selfcheck.run_checks()
    assert facts["anthropic"] == "2.6.0" and facts["stop_reason"] == "tool_use" and facts["tool"] == "data_summary"
    assert facts["text"] == selfcheck.TEXT and facts["modules"] == len(selfcheck.MODULES)
    assert all(facts[k] for k in ("httpx2", "truststore", "jiter", "opentelemetry_api"))
    assert selfcheck.check_assistant() is None


def test_the_stream_is_the_wire_format_of_the_api() -> None:
    body = selfcheck._stream_body().decode()
    names = [line[7:] for line in body.splitlines() if line.startswith("event: ")]
    assert names[0] == "message_start" and names[-1] == "message_stop"
    assert names.count("content_block_delta") == 3 and names.count("content_block_stop") == 2


def test_lost_metadata_of_opentelemetry_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    real = importlib.metadata.version

    def lost(name: str) -> str:
        if name == "opentelemetry-api":
            raise importlib.metadata.PackageNotFoundError(name)
        return real(name)

    monkeypatch.setattr(importlib.metadata, "version", lost)
    reason = selfcheck.check_assistant()
    assert reason is not None and "PackageNotFoundError" in reason and "opentelemetry-api" in reason


def test_lost_entry_points_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **_k: [])
    reason = selfcheck.check_assistant()
    assert reason is not None and "entry points" in reason


def test_a_module_the_freezing_lost_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(selfcheck, "MODULES", (*selfcheck.MODULES, "anthropic.lib.no_such_module"))
    reason = selfcheck.check_assistant()
    assert reason is not None and "no_such_module" in reason


def test_a_broken_stream_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(selfcheck, "_stream_body", lambda: selfcheck._sse([("message_stop", {"type": "message_stop"})]))
    reason = selfcheck.check_assistant()
    assert reason is not None and len(reason) <= 300
