"""The assistant turn loop with a scripted Claude, the data tools, the UI bridge and the MCP endpoint."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app import ui_bridge
from app.assistant import service, tools
from app.models import Assay, Collar, Component

UI_TOOL = {
    "name": "ui_hole_select",
    "description": "Выбрать скважину",
    "input_schema": {"type": "object", "properties": {"hole_id": {"type": ["string", "null"]}}, "required": ["hole_id"]},
}


def text(value):
    return SimpleNamespace(type="text", text=value)


def tool_use(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def reply(*content, stop_reason="end_turn"):
    return SimpleNamespace(content=list(content), stop_reason=stop_reason)


class ScriptedClaude:
    """Stands in for the Anthropic client: returns prepared responses and records the requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # The message list is mutated after the call: keep what was actually sent.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


@pytest.fixture
def project(db_client, db_engine):
    pid = db_client.post("/api/projects", json={"name": "Assistant"}).json()["id"]
    with Session(db_engine) as s:
        s.add_all([
            Collar(project_id=pid, hole_id="H-1", x=10.0, y=20.0, z=30.0, td=20.0),
            Collar(project_id=pid, hole_id="H-2", x=11.0, y=21.0, z=31.0, td=10.0),
            Assay(project_id=pid, hole_id="H-1", from_m=0.0, to_m=10.0, analytes={"AU": 1.0}),
            Assay(project_id=pid, hole_id="H-1", from_m=10.0, to_m=12.0, analytes={"AU": 7.0}),
            Assay(project_id=pid, hole_id="H-2", from_m=0.0, to_m=10.0, analytes={"AU": None}),
            Component(project_id=pid, name="AU", unit="g/t"),
        ])
        s.commit()
    return db_client, pid


@pytest.fixture
def claude(monkeypatch):
    def install(*responses):
        fake = ScriptedClaude(*responses)
        monkeypatch.setattr(service, "get_client", lambda: fake)
        return fake

    service._conversations.clear()
    return install


def chat(client, pid, **body):
    return client.post(f"/api/projects/{pid}/assistant/chat", json={"ui_tools": [UI_TOOL], **body})


def test_status_reports_missing_credentials(db_client, monkeypatch):
    monkeypatch.setattr(service, "is_configured", lambda: False)

    assert db_client.get("/api/assistant/status").json() == {"configured": False, "model": "claude-opus-5-5"}


def test_chat_without_credentials_is_a_clear_error(project, monkeypatch):
    client, pid = project
    monkeypatch.setattr(service, "is_configured", lambda: False)
    service._conversations.clear()

    response = chat(client, pid, text="привет")

    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]
    assert service._conversations.popitem()[1].messages == []  # the failed turn left nothing behind


def test_data_tool_runs_on_server_and_ui_tool_goes_to_browser(project, claude):
    client, pid = project
    fake = claude(
        reply(text("Смотрю данные."), tool_use("t1", "data_component_stats", {"component": "AU"}), stop_reason="tool_use"),
        reply(tool_use("t2", "data_get_hole", {"hole_id": "H-1"}), tool_use("t3", "ui_hole_select", {"hole_id": "H-1"}), stop_reason="tool_use"),
        reply(text("Готово: выбрана H-1.")),
    )

    first = chat(client, pid, text="Где максимум золота?", context={"selected_hole": None}).json()

    assert first["done"] is False
    assert first["pending"] == [{"id": "t3", "name": "ui_hole_select", "input": {"hole_id": "H-1"}}]
    assert [b["type"] for b in first["blocks"]] == ["text", "tool", "tool"]
    assert first["blocks"][1] == {"type": "tool", "name": "data_component_stats", "title": "Статистика компонента", "input": {"component": "AU"}, "ok": True}
    # The first request carried the question with the workspace, all tools, and the model settings.
    sent = fake.requests[0]
    assert sent["messages"][0]["content"][0] == {"type": "text", "text": "Где максимум золота?"}
    assert sent["messages"][0]["content"][1]["text"] == '<workspace>{"selected_hole": null}</workspace>'
    assert [t["name"] for t in sent["tools"]] == [t.name for t in tools.DATA_TOOLS] + ["ui_hole_select"]
    assert sent["model"] == "claude-opus-5-5" and sent["thinking"] == {"type": "adaptive"}
    # The data tool result went straight back to Claude.
    stats = json.loads(fake.requests[1]["messages"][2]["content"][0]["content"])
    assert stats["count"] == 2 and stats["max"] == 7.0
    assert stats["length_weighted_mean"] == 2.0  # (1*10 + 7*2) / 12

    second = chat(
        client, pid, conversation_id=first["conversation_id"],
        tool_results=[{"tool_use_id": "t3", "content": "Выбрана скважина H-1", "is_error": False}],
    ).json()

    assert second["done"] is True
    assert second["blocks"] == [{"type": "text", "text": "Готово: выбрана H-1."}]
    # Both results of the mixed turn are returned in one user message, in tool-call order.
    results = fake.requests[2]["messages"][4]["content"]
    assert [r["tool_use_id"] for r in results] == ["t2", "t3"]
    assert results[1]["content"] == "Выбрана скважина H-1" and "is_error" not in results[1]


def test_new_message_cancels_unanswered_ui_calls(project, claude):
    client, pid = project
    fake = claude(
        reply(tool_use("t1", "ui_hole_select", {"hole_id": "H-2"}), stop_reason="tool_use"),
        reply(text("Хорошо.")),
    )
    first = chat(client, pid, text="Выбери H-2").json()

    second = chat(client, pid, conversation_id=first["conversation_id"], text="Не надо").json()

    assert second["done"] is True
    content = fake.requests[1]["messages"][2]["content"]
    assert content[0] == {"type": "tool_result", "tool_use_id": "t1", "content": "Отменено пользователем", "is_error": True}
    assert content[1] == {"type": "text", "text": "Не надо"}


def test_tool_errors_are_returned_to_claude_not_raised(project, claude):
    client, pid = project
    fake = claude(
        reply(
            tool_use("t1", "data_get_hole", {"hole_id": "NOPE"}),
            tool_use("t2", "data_query_table", {"kind": "assay", "bogus": 1}),
            tool_use("t3", "ui_not_offered", {}),
            stop_reason="tool_use",
        ),
        reply(text("Такой скважины нет.")),
    )

    body = chat(client, pid, text="Покажи NOPE").json()

    assert body["done"] is True
    assert [b["ok"] for b in body["blocks"] if b["type"] == "tool"] == [False, False]
    results = fake.requests[1]["messages"][2]["content"]
    assert results[0]["content"] == "Ошибка: Скважина не найдена" and results[0]["is_error"] is True
    assert "неизвестные параметры: bogus" in results[1]["content"]
    assert results[2]["content"] == "Инструмента ui_not_offered нет"


def test_refusal_and_unknown_conversation(project, claude):
    client, pid = project
    claude(reply(stop_reason="refusal"))

    refused = chat(client, pid, text="...").json()
    lost = chat(client, pid, conversation_id="missing", text="ещё")

    assert refused["done"] is True and "отклонил" in refused["notice"]
    assert lost.status_code == 409


def test_ui_tools_are_filtered_and_cannot_shadow_data_tools():
    cleaned = service.clean_ui_tools([
        UI_TOOL,
        {"name": "data_get_hole", "description": "x", "input_schema": {"type": "object"}},
        {"name": "ui_bad name", "input_schema": {"type": "object"}},
        {"name": "ui_no_schema"},
        {"name": "ui_a", "description": "a", "input_schema": {"type": "object", "properties": {}}},
    ])

    assert [t["name"] for t in cleaned] == ["ui_a", "ui_hole_select"]


def test_list_holes_tool_filters_and_pages(project, db_engine):
    _, pid = project
    with Session(db_engine) as s:
        found = tools.list_holes(s, pid, search="h-2")
        page = tools.list_holes(s, pid, limit=1, offset=1)

    assert found["total"] == 1 and found["holes"][0]["hole_id"] == "H-2"
    assert page["total"] == 2 and [h["hole_id"] for h in page["holes"]] == ["H-2"]


def test_ui_bridge_round_trip_and_missing_browser():
    async def scenario():
        with pytest.raises(ui_bridge.NoBrowser):
            ui_bridge.list_commands(7)
        session = ui_bridge.open_session("s1", 7)
        assert ui_bridge.set_commands("s1", 7, [{"id": "view.open"}])
        assert not ui_bridge.set_commands("s1", 8, [])  # another project's id does not reach the session

        unknown = await ui_bridge.run_command(7, "nope", {})
        task = asyncio.create_task(ui_bridge.run_command(7, "view.open", {"view": "plan"}))
        call = await session.queue.get()
        assert ui_bridge.resolve("s1", 7, call["call_id"], "Открыт вид «План»", True)
        result = await task

        ui_bridge.close_session("s1")
        return unknown, call, result

    unknown, call, result = asyncio.run(scenario())

    assert unknown[1] is False and "ui_list_commands" in unknown[0]
    assert call["command"] == "view.open" and call["args"] == {"view": "plan"}
    assert result == ("Открыт вид «План»", True)


def test_mcp_endpoint_lists_tools_and_reads_data(project, db_engine, monkeypatch):
    client, pid = project
    from app import db

    monkeypatch.setattr(db, "SessionLocal", lambda: Session(db_engine))
    headers = {"Accept": "application/json, text/event-stream", "Host": "localhost:8000"}

    def rpc(method, params, id_=1):
        return client.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params}, headers=headers)

    with client:  # runs the lifespan that starts the MCP session manager
        init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
        listed = rpc("tools/list", {}, 2)
        holes = rpc("tools/call", {"name": "data_list_holes", "arguments": {"project_id": pid}}, 3)
        no_browser = rpc("tools/call", {"name": "ui_list_commands", "arguments": {"project_id": pid}}, 4)
        foreign = client.post("/mcp", json={}, headers={**headers, "Host": "evil.example"})

    assert init.status_code == 200 and init.json()["result"]["serverInfo"]["name"] == "BedrockForge"
    names = {t["name"] for t in listed.json()["result"]["tools"]}
    assert {t.name for t in tools.DATA_TOOLS} | {"list_projects", "ui_list_commands", "ui_run_command"} == names
    payload = json.loads(holes.json()["result"]["content"][0]["text"])
    assert [h["hole_id"] for h in payload["holes"]] == ["H-1", "H-2"]
    assert "не открыто в браузере" in no_browser.json()["result"]["content"][0]["text"]
    assert foreign.status_code in (400, 403, 421)  # DNS-rebinding protection rejects unknown hosts
