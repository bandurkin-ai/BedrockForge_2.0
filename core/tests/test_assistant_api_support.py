# ruff: noqa: F811 - fixtures imported from tests.assistant.conftest are used as parameters
"""Fixtures and helpers of the tests of the HTTP API of the assistant (подшаг 23.11; e23-api.md): a running core with
its own profile and projects folder (`pclient`), the mock of the Anthropic API behind it, the window (a client that
sends `Origin: app://bedrockforge`) and the shell (no `Origin`), readers of the SSE answers.

No test goes to the internet: `BF_ASSISTANT_BASE_URL` is the mock on 127.0.0.1, the key is the test one.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bf_core.api.app import CoreState
from bf_core.assistant import schemas_dialog
from tests.assistant.conftest import (  # noqa: F401 - fixtures of the assistant tests
    TEST_API_KEY,
    clean_anthropic_env,
    closed_port_url,
    fake_ant_path,
    mock_anthropic,
)
from tests.assistant.mock_anthropic import MockAnthropic
from tests.conftest import TOKEN, pclient  # noqa: F401 - fixture

KEY_LIKE = re.compile(r"sk-ant-[A-Za-z0-9_-]{12,}")
APP_ORIGIN = "app://bedrockforge"
API = "/api/v1/assistant"

UI_TOOLS = [
    {
        "name": "ui_hole_select",
        "description": "Выбрать скважину.",
        "input_schema": {
            "type": "object",
            "properties": {"hole_id": {"type": "string", "description": "Номер скважины."}},
            "required": ["hole_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ui_section_through_hole",
        "description": "Разрез через скважину.",
        "input_schema": {
            "type": "object",
            "properties": {"hole_id": {"type": "string", "description": "Номер скважины."}},
            "required": ["hole_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ui_project_delete",
        "description": "Удалить проект.",
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
]
CONTEXT: dict[str, Any] = {"project": {"uid": "p-1", "name": "Демо"}, "set": {"id": 1, "name": "Демо", "holes": 48}}


class Api:
    """A running core with the mock behind it. `win` = the window (with `Origin`), `shell` = the main process."""

    def __init__(self, win: TestClient, shell: TestClient, mock: MockAnthropic, state: CoreState) -> None:
        self.win, self.shell, self.mock, self.state = win, shell, mock, state

    @property
    def svc(self) -> Any:
        from bf_core.assistant.service import assistant_of

        return assistant_of(self.state)

    def bus(self, name: str) -> list[dict[str, Any]]:
        """The events `name` published on the bus of the core so far."""
        return [json.loads(data) for _, event, data in list(self.state.events._recent) if event == name]

    def connect(self, key: str = TEST_API_KEY) -> dict[str, Any]:
        r = self.win.post(f"{API}/connection/key", json={"key": key})
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    def turn_body(self, text: str, dialog_id: str | None = None, **extra: Any) -> dict[str, Any]:
        return {"dialog_id": dialog_id, "text": text, "context": CONTEXT, "tools": UI_TOOLS, **extra}

    def ask(self, text: str, dialog_id: str | None = None, **extra: Any) -> tuple[int, list[dict[str, Any]], str]:
        r = self.win.post(f"{API}/turns", json=self.turn_body(text, dialog_id, **extra))
        return r.status_code, sse_events(r.text) if r.status_code == 200 else [], r.text

    def answer(
        self, dialog_id: str, pending_id: str, results: list[tuple[str, bool, str]]
    ) -> tuple[int, list[dict[str, Any]], str]:
        body = {
            "pending_id": pending_id,
            "results": [{"tool_use_id": t, "ok": ok, "text": text} for t, ok, text in results],
            "context": CONTEXT,
            "tools": UI_TOOLS,
        }
        r = self.win.post(f"{API}/dialogs/{dialog_id}/tool_results", json=body)
        return r.status_code, sse_events(r.text) if r.status_code == 200 else [], r.text


def sse_events(text: str) -> list[dict[str, Any]]:
    """The events of an SSE body as `{id, event, data}`; comments (`: keepalive`) are skipped."""
    out: list[dict[str, Any]] = []
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if not line or line.startswith(":"):
                continue
            name, _, value = line.partition(": ")
            fields[name] = value
        if "event" in fields:
            out.append({"id": int(fields["id"]), "event": fields["event"], "data": json.loads(fields["data"])})
    return out


def check_events(events: list[dict[str, Any]]) -> None:
    """Every event is of the contract (§10) and the numbers run 1, 2, 3 …"""
    assert [e["id"] for e in events] == list(range(1, len(events) + 1))
    for e in events:
        schemas_dialog.parse_event(e["event"], e["data"])


def of(events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e["data"] for e in events if e["event"] == name]


def names(events: list[dict[str, Any]]) -> list[str]:
    return [e["event"] for e in events]


def text_of(events: list[dict[str, Any]]) -> str:
    return "".join(d["text"] for d in of(events, "text.delta"))


def wait_for(predicate: Any, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def in_thread(fn: Any) -> tuple[threading.Thread, dict[str, Any]]:
    """Runs `fn()` in a thread; its result (or error) is in the returned dict under `value` / `error`."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - the test reads it
            box["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, box


@pytest.fixture()
def api(pclient: TestClient, mock_anthropic: MockAnthropic, monkeypatch: pytest.MonkeyPatch) -> Iterator[Api]:
    """The core on the mock, not connected: `ANTHROPIC_API_KEY` and the other variables of the machine are gone."""
    monkeypatch.setenv("BF_ASSISTANT_BASE_URL", mock_anthropic.base_url)
    state: CoreState = pclient.core  # type: ignore[attr-defined]
    pclient.headers["Origin"] = APP_ORIGIN
    shell = TestClient(pclient.app, base_url="http://127.0.0.1:5555", headers={"X-BF-Token": TOKEN})
    try:
        yield Api(pclient, shell, mock_anthropic, state)
    finally:
        shell.close()


def err(r: Any, status: int, code: str) -> dict[str, Any]:
    """The answer is the error `code` of the contract: `{code, message, hint}` in Russian."""
    assert r.status_code == status and r.json()["code"] == code, (r.status_code, r.text)
    body: dict[str, Any] = r.json()
    assert body["message"] and body["hint"] and re.search("[а-яё]", body["message"], re.I)
    return body
