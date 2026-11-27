# ruff: noqa: F811 - fixtures imported from tests.assistant.conftest are used as parameters
"""Helpers and fixtures of the tests of the loop of the assistant (подшаг 23.10): a mock of the Anthropic API, a
connection on it, a project of dialogs, the data tools replaced by two small ones, and readers of a segment.

No test goes to the internet: the client has an explicit `base_url` on 127.0.0.1 (mock) and an explicit test key.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from bf_core.assistant import data_tools as dt
from bf_core.assistant import loop, schemas_dialog, store_parts
from bf_core.assistant.connection import Connection
from bf_core.assistant.loop_events import LoopEnv
from bf_core.assistant.schemas import ToolResultsBody, TurnBody, parse_body
from bf_core.assistant.segments import Event, Segment, Segments
from bf_core.assistant.store import Dialogs
from tests.assistant.conftest import (  # noqa: F401 - fixtures of the assistant tests
    TEST_API_KEY,
    clean_anthropic_env,
    closed_port_url,
    mock_anthropic,
)
from tests.assistant.mock_anthropic import MockAnthropic
from tests.assistant.mock_scenario import load_scenario
from tests.test_assistant_store import project_in

WAIT_S = 20.0


def tool(name: str, description: str = "Команда.", **props: str) -> dict[str, Any]:
    """A tool of the window as `commandTools()` makes it: every property a string, all required."""
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {k: {"type": v, "description": f"Параметр {k}."} for k, v in props.items()},
            "required": list(props),
            "additionalProperties": False,
        },
    }


UI_TOOLS = [
    tool("ui_hole_select", "Выбрать скважину.", hole_id="string"),
    tool("ui_section_through_hole", "Разрез через скважину.", hole_id="string"),
    tool("ui_project_delete", "Удалить проект."),
]
CONTEXT: dict[str, Any] = {"project": {"uid": "p-1", "name": "Демо"}, "set": {"id": 1, "name": "Демо", "holes": 48}}


def turn_body(text: str, dialog_id: str | None = None, tools: list[dict[str, Any]] | None = None) -> TurnBody:
    raw = {"dialog_id": dialog_id, "text": text, "context": CONTEXT, "tools": UI_TOOLS if tools is None else tools}
    return parse_body(TurnBody, raw)


def results_body(
    pending_id: str, results: list[tuple[str, bool, str]], tools: list[dict[str, Any]] | None = None
) -> ToolResultsBody:
    raw = {
        "pending_id": pending_id,
        "results": [{"tool_use_id": t, "ok": ok, "text": text} for t, ok, text in results],
        "context": CONTEXT,
        "tools": UI_TOOLS if tools is None else tools,
    }
    return parse_body(ToolResultsBody, raw)


@pytest.fixture()
def fake_data_tools(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The registry of the data tools replaced by `data_summary` and `data_audit` (the loop does not care what they do);
    the calls are recorded in the returned dict."""
    calls: dict[str, Any] = {"args": [], "set_ids": []}
    monkeypatch.setattr(dt, "DATA_TOOLS", {})
    monkeypatch.setattr(dt, "_loaded", True)

    def summary(call: dt.DataCall, args: dict[str, Any]) -> dict[str, Any]:
        calls["args"].append(("data_summary", args))
        calls["set_ids"].append(call.set_id)
        return {"name": "Демо", "holes": 48}

    def audit(call: dt.DataCall, args: dict[str, Any]) -> dict[str, Any]:
        calls["args"].append(("data_audit", args))
        return {"counts": {"e": 3, "w": 12, "i": 0}, "stale": None}

    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    dt.register("data_summary", schema, summary, title="Сводка проекта", description="Сводка проекта или набора.")
    dt.register("data_audit", schema, audit, title="Итог аудита", description="Итог аудита набора.")
    return calls


@pytest.fixture(autouse=True)
def _forget_effort_cap() -> Any:
    loop.EFFORT_CAP.clear()
    yield
    loop.EFFORT_CAP.clear()


@pytest.fixture()
def conn(tmp_path: Path, mock_anthropic: MockAnthropic) -> Connection:
    """Connected by the key of the environment (the test key), the API is the mock."""
    env = {"BF_ASSISTANT_BASE_URL": mock_anthropic.base_url, "ANTHROPIC_API_KEY": TEST_API_KEY}
    return Connection(tmp_path / "data", env=env)


@pytest.fixture()
def proj(tmp_path: Path) -> Any:
    p = project_in(tmp_path)
    yield p
    p.engine.dispose()


@pytest.fixture()
def env(conn: Connection, proj: Any, fake_data_tools: dict[str, Any]) -> LoopEnv:
    return LoopEnv(conn, Dialogs(proj, store_parts.TempDialogs()), Segments())


def finished(seg: Segment, timeout: float = WAIT_S) -> list[Event]:
    """All the events of a segment once it is over (the test fails if it hangs)."""
    assert seg.wait(timeout), "the segment did not finish"
    events = seg.events_after(0)
    for ev in events:  # every event is of the contract (§10)
        schemas_dialog.parse_event(ev.event, ev.data)
    return events


def names(events: list[Event]) -> list[str]:
    return [e.event for e in events]


def of(events: list[Event], name: str) -> list[dict[str, Any]]:
    return [e.data for e in events if e.event == name]


def text_of(events: list[Event]) -> str:
    return "".join(d["text"] for d in of(events, "text.delta"))


def ask(env: LoopEnv, text: str, dialog_id: str | None = None, **kw: Any) -> list[Event]:
    """A question, to the end of its segment."""
    return finished(loop.start_turn(env, turn_body(text, dialog_id, **kw)))


def answer_with(env: LoopEnv, dialog_id: str, pending_id: str, results: list[tuple[str, bool, str]]) -> list[Event]:
    return finished(loop.start_continue(env, dialog_id, results_body(pending_id, results)))


def end_of(events: list[Event]) -> dict[str, Any]:
    return of(events, "turn.end")[-1]


def messages_sent(mock: MockAnthropic) -> list[dict[str, Any]]:
    """The bodies of the requests of the loop to `/v1/messages`, in order."""
    return [r["body"] for r in mock.requests_to("/v1/messages")]


def scenario_with(mock: MockAnthropic, names_: list[str], *extra: dict[str, Any]) -> None:
    """The steps of the named scenarios, then `extra` steps (a fallback `match: {}`, for instance)."""
    steps = load_scenario(*names_)["steps"] + list(extra)
    mock.use_scenario({"steps": steps})


def say(text: str, **reply: Any) -> dict[str, Any]:
    """A step of a scenario that answers any request with `text`."""
    return {"match": {}, "reply": {"content": [{"type": "text", "text": text}], **reply}}


def haystack(*parts: Any) -> str:
    return "\n".join(json.dumps(p, ensure_ascii=False, default=str) for p in parts)


def wait_for(predicate: Any, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return False
