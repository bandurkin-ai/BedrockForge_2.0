# ruff: noqa: F811 - fixtures imported from the support module are used as parameters
"""The review of the loop (подшаг 23.21б-A): a turn without an answer is not a question; an open `tool_use` is closed
by the question whatever the meta says; the depth that the model refused is learned only from a call that went
through; a refused try is not one of the 25 calls; the limit of turns at once; the segment is released when the
turn cannot start."""

from __future__ import annotations

from typing import Any

import pytest

from bf_core.api.errors import ApiError
from bf_core.assistant import loop, prefs, prices
from bf_core.assistant.loop_events import LoopEnv
from bf_core.assistant.segments import MAX_RUNNING, Segments
from tests.assistant.mock_anthropic import MockAnthropic
from tests.assistant.mock_scenario import load_scenario
from tests.test_assistant_loop_support import (  # noqa: F401 - fixtures
    _forget_effort_cap,
    ask,
    clean_anthropic_env,
    closed_port_url,
    conn,
    end_of,
    env,
    fake_data_tools,
    messages_sent,
    mock_anthropic,
    of,
    proj,
    say,
    scenario_with,
    text_of,
    turn_body,
)

# --- a turn without an answer ------------------------------------------------------------------------------------


def test_a_failed_question_is_not_counted_and_the_retry_is(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("unauthorized")
    failed = end_of(ask(env, "Сводка по проекту"))["dialog"]
    assert failed["state"] == "error" and failed["questions"] == 0
    mock_anthropic.use_scenario({"steps": [say("Вот сводка.")]})
    again = end_of(ask(env, "Сводка по проекту", failed["id"]))["dialog"]
    assert again["state"] == "idle" and again["questions"] == 1
    mock_anthropic.use_scenario("unauthorized")
    third = end_of(ask(env, "Ещё вопрос", failed["id"]))["dialog"]
    assert third["state"] == "error" and third["questions"] == 1 and third["last_at"] == again["last_at"]


def test_a_cut_stream_leaves_the_history_as_it_was(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("drop")
    cut = end_of(ask(env, "Расскажи подробно про бурение"))["dialog"]
    assert env.dialogs.read(cut["id"]).messages == []
    mock_anthropic.use_scenario({"steps": [say("Бурение ведётся.")]})
    ask(env, "Расскажи подробно про бурение", cut["id"])
    assert [m["role"] for m in messages_sent(mock_anthropic)[-1]["messages"]] == ["user"]  # not user, assistant, user


# --- a question over an open tool_use ----------------------------------------------------------------------------


def test_a_question_closes_a_tool_use_that_the_meta_forgot(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    second = {"match": {"nth": 1}, "reply": say("Хорошо.")["reply"]}
    mock_anthropic.use_scenario({"steps": [second, *load_scenario("hole_section")["steps"]]})
    dialog_id = end_of(ask(env, "Покажи скважину DH-012 на разрезе"))["dialog"]["id"]
    env.dialogs.append(dialog_id, [], [], state="idle", pending=None)  # the meta lost its `pending`
    assert env.dialogs.read(dialog_id).meta.get("pending") is None
    assert text_of(ask(env, "Нет, лучше сводку", dialog_id)) == "Хорошо."
    last = messages_sent(mock_anthropic)[1]["messages"]
    assert [m["role"] for m in last] == ["user", "assistant", "user"]
    done = [b for b in last[2]["content"] if b["type"] == "tool_result"]
    assert len(done) == 2 and all(b["is_error"] and b["content"].startswith("Не выполнено") for b in done)
    assert last[2]["content"][-1]["text"] == "Нет, лучше сводку"


# --- the depth ---------------------------------------------------------------------------------------------------


def test_a_refused_depth_is_remembered_only_after_a_call_went_through(
    env: LoopEnv, mock_anthropic: MockAnthropic
) -> None:
    refusal = {"match": {}, "reply": {"status": 400, "message": "effort is not supported"}}
    mock_anthropic.use_scenario({"steps": [refusal]})
    assert end_of(ask(env, "Сводка"))["state"] == "error"
    assert loop.EFFORT_CAP == {}  # nothing is learned from a model that refused every try
    mock_anthropic.use_scenario({"steps": [say("Ответ.")]})
    ask(env, "Сводка")
    assert messages_sent(mock_anthropic)[-1]["output_config"] == {"effort": "medium"}  # the next turn starts as asked


def test_a_model_without_the_depth_gets_the_request_without_it(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    scenario_with(mock_anthropic, [], say("Ответ."))
    mock_anthropic.fault({"status": 400, "message": "output_config.effort: Extra inputs are not permitted"}, times=1)
    events = ask(env, "Сводка")
    assert end_of(events)["state"] == "idle" and [n["item"]["code"] for n in of(events, "notice")] == ["effort_lowered"]
    assert ["output_config" in b for b in messages_sent(mock_anthropic)] == [True, False]  # no walk down the levels
    assert loop.EFFORT_CAP == {prices.DEFAULT_MODEL: "off"}
    ask(env, "Ещё", end_of(events)["dialog"]["id"])
    assert "output_config" not in messages_sent(mock_anthropic)[-1]


def test_a_refused_try_is_not_one_of_the_calls(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    prefs.set_prefs(env.connection.data_dir, None, "max")
    mock_anthropic.use_scenario("hole_section")
    mock_anthropic.fault({"status": 400, "message": "output_config.effort: 'max' is not supported"}, times=1)
    end = end_of(ask(env, "Покажи скважину DH-012 на разрезе"))
    assert end["state"] == "pending"
    assert len(messages_sent(mock_anthropic)) == 2  # one refused, one answered
    assert env.dialogs.read(end["dialog"]["id"]).meta["pending"]["rounds"] == 1


# --- the limit of turns, the release ----------------------------------------------------------------------------


def test_at_most_eight_turns_at_once() -> None:
    segments = Segments()
    running = [segments.start(f"d{i}") for i in range(MAX_RUNNING)]
    with pytest.raises(ApiError) as e:
        segments.start("one-more")
    assert e.value.status == 409 and e.value.code == "assistant_busy" and "Слишком много" in e.value.message
    with pytest.raises(ApiError):
        segments.ensure_room()
    segments.release(running[0])
    assert segments.start("one-more").dialog_id == "one-more"  # a place is free again


def test_a_full_house_makes_no_dialog(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    for i in range(MAX_RUNNING):
        env.segments.start(f"d{i}")
    with pytest.raises(ApiError) as e:
        loop.start_turn(env, turn_body("Первый"))
    assert e.value.code == "assistant_busy" and env.dialogs.list()["total"] == 0
    assert not mock_anthropic.requests_to("/v1/messages")


def test_the_segment_is_released_when_the_turn_cannot_start(
    env: LoopEnv, mock_anthropic: MockAnthropic, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("no turn")

    monkeypatch.setattr(loop, "_Turn", boom)
    with pytest.raises(RuntimeError):
        loop.start_turn(env, turn_body("Вопрос"))
    assert env.segments.busy() == frozenset()  # the dialog is not left `assistant_busy`
