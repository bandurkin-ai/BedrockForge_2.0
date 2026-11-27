# ruff: noqa: F811 - fixtures imported from the support module are used as parameters
"""The tools in the loop (подшаг 23.10; ADR 0029 решения 21-23, № 131, № 133; e23-api.md §8-§11): `data_*` run at once
and their results go into the next call; `ui_*` stop the segment as `pending` and go on with the results of the window;
a refusal of `confirm`; a new question over an open `pending`; the limit of 25 calls; the retry after «the context
does not fit». Everything goes to the mock of the Anthropic API."""

from __future__ import annotations

from typing import Any

import pytest

from bf_core.api.errors import ApiError
from bf_core.assistant import errors, loop, prices, schemas_dialog, usage
from bf_core.assistant.loop_events import LoopEnv, Tally
from tests.assistant.mock_anthropic import MockAnthropic
from tests.assistant.mock_scenario import load_scenario
from tests.test_assistant_loop_support import (  # noqa: F401 - fixtures
    UI_TOOLS,
    _forget_effort_cap,
    answer_with,
    ask,
    clean_anthropic_env,
    closed_port_url,
    conn,
    end_of,
    env,
    fake_data_tools,
    finished,
    messages_sent,
    mock_anthropic,
    names,
    of,
    proj,
    results_body,
    say,
    scenario_with,
    text_of,
    turn_body,
)

SELECT, SECTION = "toolu_mock_select", "toolu_mock_section"


def blocks(message: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [b for b in message["content"] if isinstance(b, dict) and b["type"] == kind]


def test_two_rounds_of_data_tools(env: LoopEnv, mock_anthropic: MockAnthropic, fake_data_tools: dict[str, Any]) -> None:
    mock_anthropic.use_scenario("data_tools")
    events = ask(env, "Что не так с данными?")
    assert names(events).count("tool.start") == 2 and names(events).count("tool.end") == 2
    starts = of(events, "tool.start")
    assert [s["item"]["name"] for s in starts] == ["data_summary", "data_audit"]
    assert all(s["item"]["side"] == "data" and s["item"]["status"] == "waiting" for s in starts)
    assert starts[0]["item"]["title"] == "Сводка проекта" and starts[0]["item"]["args"] == {}
    ends = of(events, "tool.end")
    assert [e["status"] for e in ends] == ["ok", "ok"] and ends[0]["item_id"] == starts[0]["item"]["id"]
    assert "holes: 48" in ends[0]["summary"]
    assert fake_data_tools["args"] == [("data_summary", {}), ("data_audit", {})]
    assert fake_data_tools["set_ids"] == [1]  # the set of the screen reaches the tool
    assert "предупреждений" in text_of(events) and len(of(events, "usage")) == 3
    end = end_of(events)
    assert end["state"] == "idle" and end["stop_reason"] == "end_turn" and end["dialog"]["questions"] == 1
    assert end["dialog"]["tokens"]["input"] == 2400 + 2900 + 3300
    assert of(events, "usage")[-1]["dialog"]["tokens"]["output"] == 35 + 40 + 70
    sent = messages_sent(mock_anthropic)
    assert len(sent) == 3
    second = sent[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    (result,) = blocks(second[2], "tool_result")
    assert (
        result["tool_use_id"] == "toolu_mock_summary" and '"holes":48' in result["content"] and "is_error" not in result
    )
    assert [m["role"] for m in sent[2]["messages"]] == ["user", "assistant", "user", "assistant", "user"]
    loaded = env.dialogs.read(end["dialog"]["id"])
    kinds = [i["kind"] for i in loaded.items]
    assert kinds == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
    assert [i["status"] for i in loaded.items if i["kind"] == "tool"] == ["ok", "ok"]
    assert [m["role"] for m in loaded.messages] == ["user", "assistant", "user", "assistant", "user", "assistant"]
    assert loaded.pending is None


def test_a_failing_data_tool_is_a_result_with_is_error(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    from bf_core.assistant import data_tools as dt

    def broken(call: dt.DataCall, args: dict[str, Any]) -> dict[str, Any]:
        raise dt.DataError("Нет активного набора скважин: укажите set_id")

    dt.register(
        "data_summary", {"type": "object", "properties": {}, "additionalProperties": False}, broken, title="Сводка"
    )
    mock_anthropic.use_scenario("data_tools")
    events = ask(env, "Что не так с данными?")
    assert of(events, "tool.end")[0]["status"] == "error"
    (result,) = blocks(messages_sent(mock_anthropic)[1]["messages"][2], "tool_result")
    assert result["is_error"] is True and "Нет активного набора" in result["content"]
    assert end_of(events)["state"] == "idle"  # the model decides what to do with an error, the loop goes on


def test_ui_commands_stop_the_turn_as_pending_and_the_results_go_on(
    env: LoopEnv, mock_anthropic: MockAnthropic
) -> None:
    mock_anthropic.use_scenario("hole_section")
    events = ask(env, "Покажи скважину DH-012 на разрезе")
    assert names(events)[-2:] == ["pending", "turn.end"] and of(events, "thinking") == [
        {"state": "start"},
        {"state": "end"},
    ]
    pending = of(events, "pending")[0]
    assert pending["id"].startswith("p-") and [t["name"] for t in pending["tools"]] == [
        "ui_hole_select",
        "ui_section_through_hole",
    ]
    assert [t["tool_use_id"] for t in pending["tools"]] == [SELECT, SECTION]
    assert pending["tools"][0]["args"] == {"hole_id": "DH-012"} and pending["tools"][0]["command"] is None
    end = end_of(events)
    assert end["state"] == "pending" and end["stop_reason"] == "tool_use" and end["dialog"]["state"] == "pending"
    assert not of(events, "tool.start")  # the cards of the window come from `pending`
    dialog_id = end["dialog"]["id"]
    shown = loop.read_dialog(env, dialog_id)
    cards = [i for i in shown["items"] if i["kind"] == "tool"]
    assert [(c["status"], c["side"], c["id"]) for c in cards] == [
        ("waiting", "ui", t["item_id"]) for t in pending["tools"]
    ]
    assert shown["pending"] == pending
    for item in shown["items"]:
        schemas_dialog.ITEM.validate_python(item)
    schemas_dialog.Dialog.model_validate(shown)

    follow = answer_with(
        env, dialog_id, pending["id"], [(SELECT, True, "Выбрана скважина DH-012"), (SECTION, True, "Разрез построен")]
    )
    assert names(follow)[0] == "turn.start" and "user_item" not in of(follow, "turn.start")[0]
    assert text_of(follow) == "Готово: скважина DH-012 выбрана и показана на разрезе."
    assert (
        end_of(follow)["state"] == "idle" and end_of(follow)["dialog"]["questions"] == 1
    )  # one question, two segments
    last = messages_sent(mock_anthropic)[1]["messages"]
    assert [m["role"] for m in last] == ["user", "assistant", "user"]
    assert [b["type"] for b in last[1]["content"]] == ["thinking", "text", "tool_use", "tool_use"]
    assert last[1]["content"][0]["signature"] == "c2lnLW1vY2s="  # the thoughts come back as they were
    results = blocks(last[2], "tool_result")
    assert [(r["tool_use_id"], r["content"]) for r in results] == [
        (SELECT, "Выбрана скважина DH-012"),
        (SECTION, "Разрез построен"),
    ]
    final = loop.read_dialog(env, dialog_id)
    assert final["pending"] is None and final["head"]["state"] == "idle"
    cards = [i for i in final["items"] if i["kind"] == "tool"]
    assert [(c["status"], c["summary"]) for c in cards] == [
        ("ok", "Выбрана скважина DH-012"),
        ("ok", "Разрез построен"),
    ]
    assert [c["id"] for c in cards] == [t["item_id"] for t in pending["tools"]]  # the same cards, now with results
    assert [i["kind"] for i in final["items"]] == ["user", "assistant", "tool", "tool", "assistant"]


def test_confirm_refused_goes_to_the_api_as_a_tool_result(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("project_delete")
    events = ask(env, "Удали проект")
    pending = of(events, "pending")[0]
    (call,) = pending["tools"]
    assert call["name"] == "ui_project_delete" and end_of(events)["state"] == "pending"
    follow = answer_with(
        env, end_of(events)["dialog"]["id"], pending["id"], [(call["tool_use_id"], False, errors.TEXT_DECLINED)]
    )
    assert text_of(follow) == "Хорошо, проект не удалён."
    (result,) = blocks(messages_sent(mock_anthropic)[1]["messages"][-1], "tool_result")
    assert result["content"] == "Пользователь отклонил действие" and result["is_error"] is True
    final = loop.read_dialog(env, end_of(follow)["dialog"]["id"])
    (card,) = [i for i in final["items"] if i["kind"] == "tool"]
    assert card["status"] == "declined" and card["summary"] == "Пользователь отклонил действие"


def test_a_failed_command_is_an_error_card(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("hole_section")
    events = ask(env, "Покажи скважину DH-012 на разрезе")
    pending = of(events, "pending")[0]
    follow = answer_with(
        env,
        end_of(events)["dialog"]["id"],
        pending["id"],
        [(SELECT, True, "Выбрана"), (SECTION, False, "Нет скважины DH-012")],
    )
    assert text_of(follow).startswith("Не получилось")
    cards = [i for i in loop.read_dialog(env, end_of(follow)["dialog"]["id"])["items"] if i["kind"] == "tool"]
    assert [c["status"] for c in cards] == ["ok", "error"]


def test_a_new_question_closes_the_open_pending(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    second = {"match": {"nth": 1}, "reply": say("Хорошо, спрашивайте.")["reply"]}  # the request after the question
    mock_anthropic.use_scenario({"steps": [second, *load_scenario("hole_section")["steps"]]})
    events = ask(env, "Покажи скважину DH-012 на разрезе")
    dialog_id = end_of(events)["dialog"]["id"]
    new = ask(env, "Нет, лучше сводку", dialog_id)
    assert text_of(new) == "Хорошо, спрашивайте." and end_of(new)["dialog"]["questions"] == 2
    last = messages_sent(mock_anthropic)[1]["messages"]
    assert [m["role"] for m in last] == ["user", "assistant", "user"]
    results = blocks(last[2], "tool_result")
    assert [(r["tool_use_id"], r["content"], r["is_error"]) for r in results] == [
        (SELECT, "Не выполнено: пользователь продолжил диалог", True),
        (SECTION, "Не выполнено: пользователь продолжил диалог", True),
    ]
    assert [b["type"] for b in last[2]["content"]] == ["tool_result", "tool_result", "text", "text"]  # results first
    assert last[2]["content"][-1]["text"] == "Нет, лучше сводку"
    shown = loop.read_dialog(env, dialog_id)
    assert shown["pending"] is None
    assert [c["status"] for c in shown["items"] if c["kind"] == "tool"] == ["declined", "declined"]
    assert [i["kind"] for i in shown["items"]] == ["user", "assistant", "tool", "tool", "user", "assistant"]


def test_results_that_do_not_match_the_pending_are_refused(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("hole_section")
    events = ask(env, "Покажи скважину DH-012 на разрезе")
    dialog_id, pid = end_of(events)["dialog"]["id"], of(events, "pending")[0]["id"]

    def refused(body: Any) -> ApiError:
        with pytest.raises(ApiError) as e:
            loop.start_continue(env, dialog_id, body)
        return e.value

    err = refused(results_body(pid, [(SELECT, True, "ok")]))  # one of two
    assert (
        err.status == 409
        and err.code == "assistant_pending_mismatch"
        and set(err.extra["expected"]) == {SELECT, SECTION}
    )
    assert (
        refused(results_body("p-other", [(SELECT, True, "a"), (SECTION, True, "b")])).code
        == "assistant_pending_mismatch"
    )
    assert (
        refused(results_body(pid, [(SELECT, True, "a"), ("toolu_other", True, "b")])).code
        == "assistant_pending_mismatch"
    )
    assert not env.segments.busy()  # a refusal frees the dialog
    assert loop.read_dialog(env, dialog_id)["pending"]["id"] == pid  # and does not touch it


def test_no_pending_to_continue(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("summary")
    dialog_id = end_of(ask(env, "Сводка по проекту"))["dialog"]["id"]
    with pytest.raises(ApiError) as e:
        loop.start_continue(env, dialog_id, results_body("p-1", [(SELECT, True, "ok")]))
    assert e.value.status == 409 and e.value.code == "assistant_no_pending"
    with pytest.raises(ApiError) as e2:
        loop.start_continue(env, "999", results_body("p-1", [(SELECT, True, "ok")]))
    assert e2.value.status == 404 and not env.segments.busy()


def test_unknown_ui_command_is_answered_by_the_core(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario(
        {
            "steps": [
                {
                    "match": {"tool_result_for": "ui_no_such_thing"},
                    "reply": {"content": [{"type": "text", "text": "Понял."}]},
                },
                {
                    "match": {},
                    "reply": {
                        "content": [{"type": "tool_use", "id": "toolu_x", "name": "ui_no_such_thing", "input": {}}]
                    },
                },
            ]
        }
    )
    events = ask(env, "Сделай что-нибудь")
    assert text_of(events) == "Понял." and end_of(events)["state"] == "idle" and "pending" not in names(events)
    (card,) = [e["item"] for e in of(events, "tool.start")]
    assert card["status"] == "error" and "Нет такой команды" in card["summary"]
    (result,) = blocks(messages_sent(mock_anthropic)[1]["messages"][-1], "tool_result")
    assert result["is_error"] is True and "Нет такой команды" in result["content"]


def test_a_command_missing_from_the_request_tools_is_unknown(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("hole_section")
    only_select = [t for t in UI_TOOLS if t["name"] == "ui_hole_select"]
    events = finished(loop.start_turn(env, turn_body("Покажи скважину DH-012 на разрезе", tools=only_select)))
    assert [t["name"] for t in of(events, "pending")[0]["tools"]] == [
        "ui_hole_select"
    ]  # the section command is not known
    assert [e["item"]["name"] for e in of(events, "tool.start")] == ["ui_section_through_hole"]


def test_data_and_ui_in_one_answer(
    env: LoopEnv, mock_anthropic: MockAnthropic, fake_data_tools: dict[str, Any]
) -> None:
    mock_anthropic.use_scenario(
        {
            "steps": [
                {
                    "match": {"tool_result_for": "data_summary"},
                    "reply": {"content": [{"type": "text", "text": "Готово."}]},
                },
                {
                    "match": {},
                    "reply": {
                        "content": [
                            {"type": "text", "text": "Смотрю и выбираю."},
                            {
                                "type": "tool_use",
                                "id": "toolu_ui",
                                "name": "ui_hole_select",
                                "input": {"hole_id": "DH-001"},
                            },
                            {"type": "tool_use", "id": "toolu_data", "name": "data_summary", "input": {}},
                        ]
                    },
                },
            ]
        }
    )
    events = ask(env, "Сводка и выбери скважину")
    assert [s["item"]["name"] for s in of(events, "tool.start")] == ["data_summary"] and end_of(events)[
        "state"
    ] == "pending"
    assert fake_data_tools["args"] == [
        ("data_summary", {})
    ]  # the data tool ran at once, the command waits for the window
    pending = of(events, "pending")[0]
    assert [t["tool_use_id"] for t in pending["tools"]] == ["toolu_ui"]
    follow = answer_with(env, end_of(events)["dialog"]["id"], pending["id"], [("toolu_ui", True, "Выбрана DH-001")])
    assert text_of(follow) == "Готово."
    results = blocks(messages_sent(mock_anthropic)[1]["messages"][-1], "tool_result")
    assert [r["tool_use_id"] for r in results] == ["toolu_ui", "toolu_data"]  # in the order of the calls
    assert '"holes":48' in results[1]["content"] and results[0]["content"] == "Выбрана DH-001"
    assert fake_data_tools["args"] == [("data_summary", {})]  # not run twice by the continuation


def test_the_limit_of_25_calls_per_question(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    always = {
        "content": [{"type": "text", "text": "Ещё шаг."}, {"type": "tool_use", "name": "data_summary", "input": {}}]
    }
    mock_anthropic.use_scenario({"steps": [{"match": {}, "reply": always}]})
    events = ask(env, "Зациклись")
    assert len(messages_sent(mock_anthropic)) == loop.MAX_CALLS == 25
    notice = of(events, "notice")[-1]["item"]
    assert notice["code"] == "max_rounds" and "предел шагов" in notice["message"]
    end = end_of(events)
    assert end["state"] == "idle" and end["stop_reason"] == "max_rounds" and names(events).count("tool.end") == 25
    loaded = env.dialogs.read(end["dialog"]["id"])
    assert loaded.messages[-1]["role"] == "user" and blocks(
        loaded.messages[-1], "tool_result"
    )  # the history stays valid
    assert len(blocks(loaded.messages[-2], "tool_use")) == len(blocks(loaded.messages[-1], "tool_result")) == 1
    assert end["dialog"]["questions"] == 1
    scenario_with(mock_anthropic, [], say("Продолжаю."))  # the next question has its own 25
    assert text_of(ask(env, "продолжай", end["dialog"]["id"])) == "Продолжаю."


def test_the_count_of_calls_goes_on_after_the_window(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    ui = {"content": [{"type": "tool_use", "id": "toolu_u", "name": "ui_hole_select", "input": {"hole_id": "1"}}]}
    mock_anthropic.use_scenario({"steps": [{"match": {}, "reply": ui}]})
    events = ask(env, "Выбирай бесконечно")
    dialog_id = end_of(events)["dialog"]["id"]
    pending = of(events, "pending")[0]
    for _ in range(loop.MAX_CALLS + 2):
        end = end_of(events)
        if end["state"] != "pending":
            break
        events = answer_with(env, dialog_id, pending["id"], [("toolu_u", True, "ok")])
        pending = of(events, "pending")[0] if of(events, "pending") else pending
    assert end_of(events)["stop_reason"] == "max_rounds" and len(messages_sent(mock_anthropic)) == 25


# --- the window of the history --------------------------------------


def _old_dialog(env: LoopEnv) -> str:
    """A dialog with an old question whose tool result is big (the window can shorten it)."""
    big = "я" * 30_000
    head = env.dialogs.create("Старый")
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "<workspace>{}</workspace>"},
                {"type": "text", "text": "Старый вопрос"},
            ],
        },
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_old", "name": "data_tree", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_old", "content": big}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Старый ответ"}]},
    ]
    env.dialogs.append(head["id"], msgs, [], questions=1)
    return str(head["id"])


@pytest.mark.parametrize("how", ["error", "stop_reason"])
def test_context_exceeded_is_retried_once_with_a_shorter_window(
    env: LoopEnv, mock_anthropic: MockAnthropic, how: str
) -> None:
    dialog_id = _old_dialog(env)
    if how == "error":
        mock_anthropic.fault({"status": 400, "message": "prompt is too long: 250000 tokens > 200000 maximum"}, times=1)
        scenario_with(mock_anthropic, [], say("Ответ после обрезки."))
    else:
        exceeded = {"content": [{"type": "text", "text": ""}], "stop_reason": "model_context_window_exceeded"}
        mock_anthropic.use_scenario(
            {"steps": [{"match": {}, "times": 1, "reply": exceeded}, say("Ответ после обрезки.")]}
        )
    events = ask(env, "Новый вопрос", dialog_id)
    assert text_of(events) == "Ответ после обрезки." and end_of(events)["state"] == "idle"
    sent = messages_sent(mock_anthropic)
    assert len(sent) == 2
    assert "я" * 100 in str(sent[0]["messages"][2]["content"][0]["content"])  # the first request carried everything
    assert sent[1]["messages"][2]["content"][0]["content"] == "[результат сокращён]"
    assert [n["item"]["code"] for n in of(events, "notice")] == ["context_trimmed"]
    stored = env.dialogs.read(dialog_id).messages  # the dialog keeps what it had: the window is only for the request
    assert "я" * 100 in str(stored[2]["content"][0]["content"])
    lines = usage.usage_path(env.connection.data_dir).read_text(encoding="utf-8").splitlines()
    assert len(lines) == (2 if how == "stop_reason" else 1)  # an answer that came and was refused is billed


def test_context_exceeded_again_is_an_error(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    dialog_id = _old_dialog(env)
    mock_anthropic.use_scenario({"steps": [{"match": {}, "reply": {"status": 400, "message": "prompt is too long"}}]})
    events = ask(env, "Новый вопрос", dialog_id)
    assert of(events, "error")[0]["item"]["code"] == "assistant_api_error" and len(messages_sent(mock_anthropic)) == 2


def test_context_exceeded_with_nothing_to_shorten_is_an_error(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario({"steps": [{"match": {}, "reply": {"status": 400, "message": "prompt is too long"}}]})
    events = ask(env, "Новый вопрос")
    assert of(events, "error")[0]["item"]["code"] == "assistant_api_error" and len(messages_sent(mock_anthropic)) == 1


# --- a failed continuation ------------------------------------------


def test_a_failed_continuation_keeps_the_pending_for_another_try(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("hole_section")
    events = ask(env, "Покажи скважину DH-012 на разрезе")
    dialog_id, pending = end_of(events)["dialog"]["id"], of(events, "pending")[0]
    results = [(SELECT, True, "Выбрана"), (SECTION, True, "Разрез")]
    mock_anthropic.fault({"status": 401, "message": "invalid"}, times=1)
    failed = answer_with(env, dialog_id, pending["id"], results)
    assert of(failed, "error")[0]["item"]["code"] == "assistant_bad_key" and end_of(failed)["state"] == "error"
    head = end_of(failed)["dialog"]
    assert head["state"] == "pending"  # the commands still wait for their results
    shown = loop.read_dialog(env, dialog_id)
    assert shown["pending"]["id"] == pending["id"]
    assert [c["status"] for c in shown["items"] if c["kind"] == "tool"] == ["waiting", "waiting"]
    again = answer_with(env, dialog_id, pending["id"], results)  # the same results, once more
    assert end_of(again)["state"] == "idle" and text_of(again).startswith("Готово")
    assert loop.read_dialog(env, dialog_id)["pending"] is None
    assert [m["role"] for m in env.dialogs.read(dialog_id).messages] == ["user", "assistant", "user", "assistant"]


# --- totals ---------------------------------------------------------


def test_a_call_without_a_price_makes_the_cost_unknown_not_zero() -> None:
    t = Tally(questions=1)
    t.add({"input": 100, "output": 10, "cache_read": 0, "cache_write": 0}, prices.cost(prices.DEFAULT_MODEL, 100, 10))
    assert t.dialog()["cost_usd"] is not None and t.segment_usage()["cost_usd"] is not None  # type: ignore[index]
    t.add({"input": 100, "output": 10, "cache_read": 0, "cache_write": 0}, None)
    assert t.dialog()["cost_usd"] is None and t.segment_usage()["cost_usd"] is None  # type: ignore[index]
    assert t.dialog()["tokens"]["input"] == 200 and t.dialog()["questions"] == 1
    assert Tally().segment_usage() is None


# --- the real read tools ------------------------------------------------------------------------------------------


def test_real_data_tools_on_the_demo_project(pclient: Any, mock_anthropic: MockAnthropic, tmp_path: Any) -> None:
    """No replaced registry: `data_summary` and `data_audit` read the demo project and the dialog lands in it."""
    from bf_core.assistant.connection import Connection
    from bf_core.assistant.segments import Segments
    from bf_core.assistant.store import Dialogs
    from bf_core.projects.service import projects_of
    from tests.assistant.mock_anthropic import TEST_API_KEY

    assert pclient.post("/api/v1/projects/demo").status_code == 200
    project = projects_of(pclient.core).require_current()
    conn = Connection(
        tmp_path / "data", env={"BF_ASSISTANT_BASE_URL": mock_anthropic.base_url, "ANTHROPIC_API_KEY": TEST_API_KEY}
    )
    env = LoopEnv(conn, Dialogs(project), Segments())
    mock_anthropic.use_scenario("data_tools")
    events = ask(env, "Что не так с данными?")
    ends = of(events, "tool.end")
    assert [e["status"] for e in ends] == ["ok", "ok"] and end_of(events)["state"] == "idle"
    sent = messages_sent(mock_anthropic)
    first = blocks(sent[1]["messages"][-1], "tool_result")[0]
    audit = blocks(sent[2]["messages"][-1], "tool_result")[0]["content"]
    assert "Скважины 2025" in first["content"] and "is_error" not in first
    assert '"e":6' in audit.replace(" ", "")
    tools = [t["name"] for t in sent[0]["tools"]]
    assert set(tools[:9]) == {
        "data_tree",
        "data_summary",
        "data_holes",
        "data_hole",
        "data_history",
        "data_audit",
        "data_findings",
        "data_table",
        "data_stats",
    }
    head = end_of(events)["dialog"]
    assert head["project_uid"] == project.uid and env.dialogs.list()["total"] == 1
