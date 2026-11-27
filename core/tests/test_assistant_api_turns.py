# ruff: noqa: F811 - fixtures imported from the support module are used as parameters
"""The turns of the assistant over HTTP (подшаг 23.11; e23-api.md §8–§10, §12, §14): the SSE answer of `POST /turns`
and `POST /dialogs/{id}/tool_results`, the catch-up `GET …/stream?after`, `cancel`, the refusals before the stream and
the failures after it, a dialog that is read while it runs, the shutdown. The mock of the Anthropic API is the other
end; the demo project is a real one."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from bf_core.assistant import schemas_dialog as sd
from bf_core.assistant import store_parts
from tests.test_assistant_api_support import (  # noqa: F401 - fixtures
    API,
    KEY_LIKE,
    TEST_API_KEY,
    UI_TOOLS,
    Api,
    api,
    check_events,
    clean_anthropic_env,
    closed_port_url,
    err,
    fake_ant_path,
    in_thread,
    mock_anthropic,
    names,
    of,
    pclient,
    sse_events,
    text_of,
    wait_for,
)

HOLE_ASK = "Покажи скважину DH-012 на разрезе"
SLOW = {
    "match": {},
    "reply": {"content": [{"type": "text", "text": "Медленный ответ " * 8}], "chunk": 4, "delay_ms": 40},
}


@pytest.fixture()
def on(api: Api) -> Api:
    """Connected by a key (checked on the mock)."""
    api.connect()
    return api


def end_of(events: list[dict[str, Any]]) -> dict[str, Any]:
    return of(events, "turn.end")[-1]


def running(api: Api) -> str:
    """The id of the dialog whose segment is running (the test fails if none starts)."""
    assert wait_for(lambda: api.svc.segments.busy()), "no segment started"
    return next(iter(api.svc.segments.busy()))


# --- the answer of a turn --------------------------------------------------------------------------------------------


def test_a_turn_is_an_sse_answer_of_the_contract(on: Api) -> None:
    on.mock.use_scenario("summary")
    r = on.win.post(f"{API}/turns", json=on.turn_body("Сводка по проекту"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-store"
    assert r.text.startswith("id: 1\nevent: turn.start\ndata: {")
    events = sse_events(r.text)
    check_events(events)
    assert names(events)[0] == "turn.start" and names(events)[-1] == "turn.end"
    assert text_of(events).startswith("В проекте «Демо» 48 скважин")
    start, end = of(events, "turn.start")[0], end_of(events)
    assert start["user_item"]["text"] == "Сводка по проекту" and start["dialog"]["state"] == "running"
    assert end["state"] == "idle" and end["stop_reason"] == "end_turn" and end["dialog"]["questions"] == 1
    assert end["dialog"]["state"] == "idle" and end["dialog"]["cost_usd"] > 0


def test_a_dialog_goes_on_with_the_next_question(on: Api) -> None:
    on.mock.use_scenario("summary")
    _, first, _ = on.ask("Сводка по проекту")
    dialog_id = end_of(first)["dialog"]["id"]
    _, second, _ = on.ask("Сводка по проекту ещё раз", dialog_id)
    assert of(second, "turn.start")[0]["dialog"]["id"] == dialog_id and "user_item" in of(second, "turn.start")[0]
    dialog = on.win.get(f"{API}/dialogs/{dialog_id}").json()
    assert [i["kind"] for i in dialog["items"]].count("user") == 2 and dialog["head"]["questions"] == 2
    assert on.win.get(f"{API}/dialogs").json()["total"] == 1


def test_the_window_runs_the_commands_and_the_turn_goes_on(on: Api) -> None:
    on.mock.use_scenario("hole_section")
    code, events, _ = on.ask(HOLE_ASK)
    check_events(events)
    pending = of(events, "pending")[0]
    assert code == 200 and [t["name"] for t in pending["tools"]] == ["ui_hole_select", "ui_section_through_hole"]
    assert names(events)[-2:] == ["pending", "turn.end"] and end_of(events)["state"] == "pending"
    dialog_id = end_of(events)["dialog"]["id"]
    shown = on.win.get(f"{API}/dialogs/{dialog_id}").json()
    sd.Dialog.model_validate(shown)
    cards = [i for i in shown["items"] if i["kind"] == "tool"]
    assert [c["status"] for c in cards] == ["waiting", "waiting"]  # one card per call, not a pair for each
    assert shown["pending"]["id"] == pending["id"] and shown["head"]["state"] == "pending"
    ids = [t["tool_use_id"] for t in pending["tools"]]
    code, after, _ = on.answer(dialog_id, pending["id"], [(ids[0], True, "Выбрана"), (ids[1], True, "Разрез")])
    check_events(after)
    assert code == 200 and "user_item" not in of(after, "turn.start")[0] and end_of(after)["state"] == "idle"
    assert text_of(after).startswith("Готово")
    done = on.win.get(f"{API}/dialogs/{dialog_id}").json()
    assert done["pending"] is None and [c["status"] for c in done["items"] if c["kind"] == "tool"] == ["ok", "ok"]


def test_the_results_must_be_the_ones_asked_for(on: Api) -> None:
    on.mock.use_scenario("hole_section")
    _, events, _ = on.ask(HOLE_ASK)
    dialog_id, pending = end_of(events)["dialog"]["id"], of(events, "pending")[0]
    ids = [t["tool_use_id"] for t in pending["tools"]]
    bad = on.answer(dialog_id, pending["id"], [(ids[0], True, "Выбрана")])  # one of two
    assert bad[0] == 409
    body = json.loads(bad[2])
    assert body["code"] == "assistant_pending_mismatch" and sorted(body["expected"]) == sorted(ids)
    assert on.answer(dialog_id, "p-other", [(i, True, "x") for i in ids])[0] == 409
    assert on.win.get(f"{API}/dialogs/{dialog_id}").json()["pending"] is not None  # untouched
    assert on.answer(dialog_id, pending["id"], [(i, False, "Пользователь отклонил действие") for i in ids])[0] == 200
    again = on.answer(dialog_id, pending["id"], [(i, True, "x") for i in ids])
    assert again[0] == 409 and json.loads(again[2])["code"] == "assistant_no_pending"


def test_a_command_that_needs_confirmation_and_is_declined(on: Api) -> None:
    on.mock.use_scenario("project_delete")
    _, events, _ = on.ask("Удали проект")
    dialog_id, pending = end_of(events)["dialog"]["id"], of(events, "pending")[0]
    assert [t["name"] for t in pending["tools"]] == ["ui_project_delete"]
    tid = pending["tools"][0]["tool_use_id"]
    _, after, _ = on.answer(dialog_id, pending["id"], [(tid, False, "Пользователь отклонил действие")])
    assert text_of(after) == "Хорошо, проект не удалён." and end_of(after)["state"] == "idle"
    card = [i for i in on.win.get(f"{API}/dialogs/{dialog_id}").json()["items"] if i["kind"] == "tool"][0]
    assert card["status"] == "declined"


def test_the_data_tools_read_the_open_project(on: Api) -> None:
    assert on.win.post("/api/v1/projects/demo").status_code == 200
    on.mock.use_scenario("data_tools")
    _, events, _ = on.ask("Что не так с данными?")
    check_events(events)
    assert [e["status"] for e in of(events, "tool.end")] == ["ok", "ok"] and end_of(events)["state"] == "idle"
    head = end_of(events)["dialog"]
    assert head["project_uid"] == on.svc.project_uid() and head["temporary"] is False
    shown = on.win.get(f"{API}/dialogs/{head['id']}").json()
    assert [c["name"] for c in shown["items"] if c["kind"] == "tool"] == ["data_summary", "data_audit"]


# --- catch-up, cancel, a dialog that is read while it runs ---------------------------------------------------------


def test_a_reloaded_window_catches_up_with_the_running_segment(on: Api) -> None:
    on.mock.use_scenario({"steps": [SLOW]})
    thread, box = in_thread(lambda: on.ask("Расскажи подробно"))
    dialog_id = running(on)
    mid = on.win.get(f"{API}/dialogs/{dialog_id}")  # while it runs: the dialog is there and is `running`
    assert mid.status_code == 200 and mid.json()["head"]["state"] == "running"
    late = on.win.get(f"{API}/dialogs/{dialog_id}/stream?after=3")
    assert late.status_code == 200 and late.headers["content-type"].startswith("text/event-stream")
    thread.join(30)
    code, events, _ = box["value"]
    got = sse_events(late.text)
    assert code == 200 and got and got[0]["id"] == 4 and got == events[3:]  # from the number after `after`
    assert end_of(got)["state"] == "idle"


def test_a_finished_segment_is_204_and_the_dialog_is_the_truth(on: Api) -> None:
    on.mock.use_scenario("summary")
    _, events, _ = on.ask("Сводка по проекту")
    dialog_id = end_of(events)["dialog"]["id"]
    assert on.win.get(f"{API}/dialogs/{dialog_id}/stream?after=0").status_code == 204
    assert on.win.get(f"{API}/dialogs/t-0b6f2d4e-8c1a-4d3e-9a5b-7c2e1f0a9b88/stream").status_code == 204
    assert on.win.get(f"{API}/dialogs/{dialog_id}/stream?after=-1").json()["param"] == "after"
    assert on.win.get(f"{API}/dialogs/{dialog_id}/stream?after=x").json()["code"] == "bad_param"


def test_cancel_stops_the_turn_and_keeps_what_was_said(on: Api) -> None:
    on.mock.use_scenario({"steps": [SLOW]})
    thread, box = in_thread(lambda: on.ask("Расскажи подробно"))
    dialog_id = running(on)
    assert wait_for(lambda: on.svc.segments.get(dialog_id).last_id >= 4)
    assert on.win.post(f"{API}/dialogs/{dialog_id}/cancel", json={}).status_code == 204
    thread.join(30)
    code, events, _ = box["value"]
    check_events(events)
    assert code == 200 and end_of(events)["state"] == "cancelled"
    answer = [i for i in on.win.get(f"{API}/dialogs/{dialog_id}").json()["items"] if i["kind"] == "assistant"][0]
    assert answer["stopped"] is True and answer["text"].startswith("Медленный")
    assert on.win.post(f"{API}/dialogs/{dialog_id}/cancel", json={}).status_code == 204  # nothing runs: still 204
    assert on.win.post(f"{API}/dialogs/nope/cancel", json={}).status_code == 204


def test_a_running_dialog_is_busy(on: Api) -> None:
    on.mock.use_scenario({"steps": [SLOW]})
    thread, box = in_thread(lambda: on.ask("Расскажи подробно"))
    dialog_id = running(on)
    err(on.win.patch(f"{API}/dialogs/{dialog_id}", json={"title": "Другое"}), 409, "assistant_busy")
    err(on.win.delete(f"{API}/dialogs/{dialog_id}"), 409, "assistant_busy")
    again = on.win.post(f"{API}/turns", json=on.turn_body("Ещё вопрос", dialog_id))
    err(again, 409, "assistant_busy")
    assert on.win.get(f"{API}/dialogs").json()["total"] == 0  # not listed until its first part is written
    on.win.post(f"{API}/dialogs/{dialog_id}/cancel", json={})
    thread.join(30)
    assert box["value"][0] == 200 and wait_for(lambda: not on.svc.segments.busy())
    assert on.win.patch(f"{API}/dialogs/{dialog_id}", json={"title": "Другое"}).status_code == 200


def test_the_shutdown_of_the_core_stops_the_running_turns(on: Api) -> None:
    on.mock.use_scenario({"steps": [SLOW]})
    thread, box = in_thread(lambda: on.ask("Расскажи подробно"))
    running(on)
    on.svc.shutdown()
    thread.join(30)
    assert end_of(box["value"][1])["state"] == "cancelled"


# --- the refusals before the stream ---------------------------------------------------------------------------------


def test_not_connected_is_refused_before_a_stream(api: Api) -> None:
    r = api.win.post(f"{API}/turns", json=api.turn_body("Привет"))
    err(r, 409, "assistant_not_connected")
    assert not r.headers["content-type"].startswith("text/event-stream")
    assert api.win.get(f"{API}/dialogs").json()["total"] == 0


@pytest.mark.parametrize(
    "patch, param",
    [
        ({"text": ""}, "text"),
        ({"text": "   "}, "text"),
        ({"text": "я" * 20001}, "text"),
        ({"dialog_id": "no"}, "dialog_id"),
        ({"context": {"panels": ["x" * 201]}}, "context"),
        ({"context": {"windows": ["w"] * 21}}, "context"),
        ({"context": {"unknown": "x" * 5000}}, "context"),
    ],
)
def test_a_bad_question_is_bad_param(on: Api, patch: dict[str, Any], param: str) -> None:
    body = {**on.turn_body("Сводка по проекту"), **patch}
    e = err(on.win.post(f"{API}/turns", json=body), 422, "bad_param")
    assert e["param"] == param
    assert on.mock.requests_to("/v1/messages") == []


@pytest.mark.parametrize(
    "tools",
    [
        [{"name": "hole_select", "description": "x", "input_schema": {"type": "object", "properties": {}}}],
        [UI_TOOLS[0], UI_TOOLS[0]],
        [{"name": "ui_x", "description": "x" * 2001, "input_schema": {"type": "object", "properties": {}}}],
        [{"name": "ui_y", "description": "x", "input_schema": {"type": "array"}}],
    ],
)
def test_bad_tools_are_refused(on: Api, tools: list[Any]) -> None:
    e = err(on.win.post(f"{API}/turns", json={**on.turn_body("Привет"), "tools": tools}), 422, "assistant_bad_tools")
    assert e["detail"]


def test_a_dialog_that_is_not_there(on: Api) -> None:
    err(on.win.post(f"{API}/turns", json=on.turn_body("Привет", "999")), 404, "assistant_dialog_not_found")
    body = {"pending_id": "p-1", "results": [{"tool_use_id": "t", "ok": True, "text": "x"}], "context": {}, "tools": []}
    err(on.win.post(f"{API}/dialogs/999/tool_results", json=body), 404, "assistant_dialog_not_found")
    for bad in ({**body, "results": []}, {**body, "pending_id": "x"}, {**body, "results": body["results"] * 2}):
        assert on.win.post(f"{API}/dialogs/999/tool_results", json=bad).json()["code"] == "bad_param"
    assert on.win.get(f"{API}/dialogs").json()["total"] == 0  # a failed start leaves no empty dialog behind


def test_a_dialog_that_is_full_is_refused(on: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_parts, "MAX_PARTS", 1)
    on.mock.use_scenario("summary")
    _, events, _ = on.ask("Сводка по проекту")
    dialog_id = end_of(events)["dialog"]["id"]
    err(on.win.post(f"{API}/turns", json=on.turn_body("Ещё", dialog_id)), 422, "assistant_dialog_full")


def test_a_dialog_with_a_lost_part_is_broken(on: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    from bf_core.storage.blobs import BlobStore

    assert on.win.post("/api/v1/projects/demo").status_code == 200
    on.mock.use_scenario("summary")
    _, events, _ = on.ask("Сводка по проекту")
    dialog_id = end_of(events)["dialog"]["id"]

    def lost(self: Any, sha: str) -> bytes:
        raise FileNotFoundError(sha)

    monkeypatch.setattr(BlobStore, "read_bytes", lost)
    err(on.win.get(f"{API}/dialogs/{dialog_id}"), 409, "assistant_dialog_broken")
    err(on.win.post(f"{API}/turns", json=on.turn_body("Ещё", dialog_id)), 409, "assistant_dialog_broken")
    assert on.win.delete(f"{API}/dialogs/{dialog_id}").status_code == 204  # it can be removed from the history


# --- the failures after the first event -----------------------------------------------------------------------------


def error_of(events: list[dict[str, Any]]) -> dict[str, Any]:
    assert end_of(events)["state"] == "error"
    return of(events, "error")[0]["item"]  # type: ignore[no-any-return]


def test_a_key_the_api_refuses_is_an_error_event(on: Api) -> None:
    on.mock.use_scenario("unauthorized")
    code, events, _ = on.ask("Привет")
    check_events(events)
    item = error_of(events)
    assert code == 200 and item["code"] == "assistant_bad_key" and item["message"] and item["hint"]
    assert end_of(events)["dialog"]["state"] == "error"


def test_without_a_network_the_turn_says_so(on: Api, monkeypatch: pytest.MonkeyPatch, closed_port_url: str) -> None:
    monkeypatch.setenv("BF_ASSISTANT_BASE_URL", closed_port_url)
    code, events, _ = on.ask("Привет")
    assert code == 200 and error_of(events)["code"] == "assistant_offline"
    assert on.win.get(f"{API}/status").json()["online"] is False
    assert on.bus("assistant.connection")[-1]["online"] is False


def test_a_cut_stream_is_interrupted_and_the_text_is_kept(on: Api) -> None:
    on.mock.use_scenario("drop")
    code, events, _ = on.ask("Расскажи подробно про бурение")
    assert code == 200 and error_of(events)["code"] == "assistant_interrupted"
    dialog_id = end_of(events)["dialog"]["id"]
    kept = [i for i in on.win.get(f"{API}/dialogs/{dialog_id}").json()["items"] if i["kind"] == "assistant"]
    assert kept and kept[0]["interrupted"] is True and kept[0]["text"]


def test_a_busy_service_is_retried_by_the_sdk(on: Api) -> None:
    on.mock.use_scenario("overloaded_then_ok")
    _, events, _ = on.ask("Привет")
    assert end_of(events)["state"] == "idle" and text_of(events) == "Ответ после повторов."


# --- the secret is nowhere ------------------------------------------------------------------------------------------


def test_the_key_is_in_no_answer_event_log_or_dialog(api: Api, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    assert api.win.post("/api/v1/projects/demo").status_code == 200
    api.connect()
    api.mock.use_scenario("summary")
    _, events, raw = api.ask("Сводка по проекту")
    dialog_id = end_of(events)["dialog"]["id"]
    seen = [raw, caplog.text, str(api.bus("assistant.connection")), str(api.bus("assistant.dialogs"))]
    seen += [api.win.get(f"{API}{p}").text for p in ("/status", "/usage", "/dialogs", f"/dialogs/{dialog_id}")]
    seen += [str(api.mock.requests)]  # the journal of the mock holds no key either
    for text in seen:
        assert TEST_API_KEY not in text and not KEY_LIKE.search(text)


# --- the project on another database ------------------------------------------------------------------------------


@pytest.mark.db
def test_the_dialogs_on_both_databases(api: Api, db_url: str, tmp_path: Any) -> None:
    from tests.test_assistant_store import project_in

    proj = project_in(tmp_path, "db", db_url)
    api.state.services["projects"] = SimpleNamespace(current=proj, shutdown=lambda: None)
    try:
        api.connect()
        api.mock.use_scenario("summary")
        _, events, _ = api.ask("Сводка по проекту")
        head = end_of(events)["dialog"]
        assert head["project_uid"] == "db" and head["temporary"] is False
        assert api.win.get(f"{API}/dialogs/{head['id']}").json()["head"]["questions"] == 1
        assert api.win.patch(f"{API}/dialogs/{head['id']}", json={"title": "БД"}).json()["title"] == "БД"
        assert api.win.delete(f"{API}/dialogs/{head['id']}").status_code == 204
        assert api.win.get(f"{API}/dialogs").json()["total"] == 0
    finally:
        proj.engine.dispose()
