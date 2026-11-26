# ruff: noqa: F811 - fixtures imported from the support module are used as parameters
"""The loop of a turn (подшаг 23.10; ТЗ 7.32; ADR 0029 решения 5-7, 14, 21, 24; e23-api.md §8, §10, §12): the text of an
answer, the body of the request (cache, depth, tools), the failures (401, offline, 429, 529, a cut stream), the stop of
the user, the secret that is nowhere, the temporary dialog. Everything goes to the mock of the Anthropic API."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import pytest

from bf_core.assistant import loop, prices, schemas_dialog, store_parts, usage
from bf_core.assistant.connection import Connection
from bf_core.assistant.loop_events import LoopEnv
from bf_core.assistant.segments import Segments
from bf_core.assistant.store import Dialogs
from tests.assistant.mock_anthropic import MockAnthropic
from tests.test_assistant_loop_support import (  # noqa: F401 - fixtures
    CONTEXT,
    TEST_API_KEY,
    _forget_effort_cap,
    ask,
    clean_anthropic_env,
    closed_port_url,
    conn,
    end_of,
    env,
    fake_data_tools,
    finished,
    haystack,
    messages_sent,
    mock_anthropic,
    names,
    of,
    proj,
    say,
    scenario_with,
    text_of,
    turn_body,
    wait_for,
)

SUMMARY = "В проекте «Демо» 48 скважин, 1 240 м бурения и 960 проб."


def test_text_answer_streams_and_is_stored(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("summary")
    events = ask(env, "Сводка по проекту")
    assert names(events)[0] == "turn.start" and names(events)[-1] == "turn.end"
    assert [e.id for e in events] == list(range(1, len(events) + 1))
    start = of(events, "turn.start")[0]
    assert start["dialog"]["state"] == "running" and start["user_item"]["text"] == "Сводка по проекту"
    assert start["model"] == prices.DEFAULT_MODEL and start["effort"] == "medium"
    assert text_of(events).startswith("В проекте «Демо» 48 скважин") and "Аудит не запускался" in text_of(events)
    u = of(events, "usage")[0]
    assert u["call"]["input"] == 2400 and u["call"]["output"] == 85 and u["call"]["cache_write"] == 1800
    assert u["call"]["cost_usd"] == prices.cost(prices.DEFAULT_MODEL, 2400, 85, 0, 1800) and u["call"]["long"] is False
    assert u["dialog"]["questions"] == 1 and u["dialog"]["cost_usd"] == round(u["call"]["cost_usd"], 6)
    end = end_of(events)
    assert end["state"] == "idle" and end["stop_reason"] == "end_turn"
    head = end["dialog"]
    assert head["state"] == "idle" and head["questions"] == 1 and head["title"] == "Сводка по проекту"
    assert head["tokens"] == {"input": 2400, "output": 85, "cache_read": 0, "cache_write": 1800}
    assert head["models"] == [prices.DEFAULT_MODEL] and head["cost_usd"] == u["dialog"]["cost_usd"]
    loaded = env.dialogs.read(head["id"])
    assert [i["kind"] for i in loaded.items] == ["user", "assistant"]
    assert loaded.items[1]["text"] == text_of(events) and loaded.items[1]["model"] == prices.DEFAULT_MODEL
    assert [m["role"] for m in loaded.messages] == ["user", "assistant"]
    assert loaded.pending is None and env.segments.get(head["id"]) is None and not env.segments.busy()


def test_request_body_has_cache_marks_depth_and_tools(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("summary")
    ask(env, "Сводка по проекту")
    (body,) = messages_sent(mock_anthropic)
    assert body["model"] == prices.DEFAULT_MODEL and body["max_tokens"] == 16_000 and body["stream"] is True
    assert body["output_config"] == {"effort": "medium"}
    assert body["thinking"] == {"type": "adaptive", "display": "omitted"}
    assert body["cache_control"] == {"type": "ephemeral"}
    assert (
        body["system"][-1]["cache_control"] == {"type": "ephemeral"}
        and "ассистент геолога" in body["system"][0]["text"]
    )
    tools = [t["name"] for t in body["tools"]]
    assert tools == ["data_audit", "data_summary"] + sorted(
        ["ui_hole_select", "ui_project_delete", "ui_section_through_hole"]
    )
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert all("cache_control" not in t for t in body["tools"][:-1])
    first = body["messages"][0]
    assert first["role"] == "user" and first["content"][0]["text"].startswith("<workspace>")
    assert first["content"][1]["text"] == "Сводка по проекту" and len(body["messages"]) == 1
    assert "p-1" not in first["content"][1]["text"]
    assert TEST_API_KEY not in haystack(body)


def test_depth_and_model_come_from_the_prefs(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    from bf_core.assistant import prefs

    prefs.set_prefs(env.connection.data_dir, "claude-sonnet-5-5", "high")
    mock_anthropic.use_scenario("summary")
    ask(env, "Сводка по проекту")
    (body,) = messages_sent(mock_anthropic)
    assert body["model"] == "claude-sonnet-5-5" and body["output_config"] == {"effort": "high"}


def test_usage_is_recorded_per_call(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("summary")
    events = ask(env, "Сводка по проекту")
    lines = [json.loads(x) for x in usage.usage_path(env.connection.data_dir).read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1 and lines[0]["model"] == prices.DEFAULT_MODEL and lines[0]["cache_write"] == 1800
    head = end_of(events)["dialog"]
    assert lines[0]["dialog_uid"] == head["uid"] and lines[0]["project_uid"] == "p-1"
    assert lines[0]["cost_usd"] == of(events, "usage")[0]["call"]["cost_usd"]


def test_on_saved_gets_the_head_of_every_part(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    heads: list[dict[str, Any]] = []
    env.on_saved = heads.append
    mock_anthropic.use_scenario("summary")
    ask(env, "Сводка по проекту")
    assert len(heads) == 1 and heads[0]["questions"] == 1


def test_second_question_continues_the_history(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    scenario_with(mock_anthropic, ["summary"], say("И ещё ответ."))
    first = end_of(ask(env, "Сводка по проекту"))["dialog"]
    second = ask(env, "А что с аудитом?", first["id"])
    assert text_of(second) == "И ещё ответ." and end_of(second)["dialog"]["questions"] == 2
    body = messages_sent(mock_anthropic)[1]
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert body["messages"][1]["content"][0]["text"].startswith("В проекте")
    assert of(second, "turn.start")[0]["dialog"]["id"] == first["id"]
    loaded = env.dialogs.read(first["id"])
    assert [i["kind"] for i in loaded.items] == ["user", "assistant", "user", "assistant"]


def test_first_delta_comes_fast(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    """23.10: до первого `text.delta` на моке ≤ 0,3 с, with a request as big as the registry (270 tools)."""
    many = [
        {
            "name": f"ui_cmd_{i:03d}",
            "description": "Описание команды. " * 25,
            "input_schema": {
                "type": "object",
                "properties": {f"p{k}": {"type": "string", "description": "Параметр. " * 4} for k in range(3)},
                "required": ["p0"],
                "additionalProperties": False,
            },
        }
        for i in range(270)
    ]
    scenario_with(mock_anthropic, ["summary"], say("Ответ."))
    finished(loop.start_turn(env, turn_body("Сводка по проекту", tools=many)))  # warm: imports, the pool of connections
    best = 9.0
    for _ in range(3):
        t0 = time.perf_counter()
        seg = loop.start_turn(env, turn_body("Сводка по проекту", tools=many))
        for ev in seg.iter():
            if ev is not None and ev.event == "text.delta":
                best = min(best, time.perf_counter() - t0)
                break
        finished(seg)
    assert best <= 0.3, best
    print(f"first text.delta on the mock: {best * 1000:.0f} ms")


def test_temporary_dialog_without_a_project(
    conn: Connection, mock_anthropic: MockAnthropic, fake_data_tools: Any
) -> None:
    env = LoopEnv(conn, Dialogs(None, store_parts.TempDialogs()), Segments())
    mock_anthropic.use_scenario("summary")
    events = ask(env, "Сводка по проекту")
    assert of(events, "notice")[0]["item"]["code"] == "temporary"
    head = end_of(events)["dialog"]
    assert head["temporary"] is True and head["id"].startswith("t-") and head["project_uid"] is None
    assert env.dialogs.read(head["id"]).note == store_parts.NOTE_NO_PROJECT


def test_temporary_dialog_moves_into_the_project_opened_meanwhile(
    conn: Connection, mock_anthropic: MockAnthropic, fake_data_tools: Any, proj: Any
) -> None:
    temp = store_parts.TempDialogs()
    mock_anthropic.use_scenario("summary")
    first = ask(LoopEnv(conn, Dialogs(None, temp), Segments()), "Сводка по проекту")
    tid = end_of(first)["dialog"]["id"]
    env = LoopEnv(conn, Dialogs(proj, temp), Segments())  # a project was opened
    scenario_with(mock_anthropic, ["summary"], say("Дальше."))
    mock_anthropic.use_scenario({"steps": [say("Дальше.")]})
    # the id of a temporary dialog is `t-<hex>`, the contract regex wants `t-<uuid>` (reported to the orchestrator)
    body = turn_body("Продолжим").model_copy(update={"dialog_id": tid})
    events = finished(loop.start_turn(env, body))
    head = end_of(events)["dialog"]
    assert head["temporary"] is False and head["id"] != tid and head["questions"] == 2
    assert env.dialogs.list()["total"] == 1 and temp.get(tid) is None


# --- failures -------------------------------------------------------


def test_cut_stream_keeps_the_partial_text(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("drop")
    events = ask(env, "Расскажи подробно про бурение")
    err = of(events, "error")[0]["item"]
    assert (
        err["code"] == "assistant_interrupted" and err["message"] == "Связь прервалась." and "Повторить" in err["hint"]
    )
    assert end_of(events)["state"] == "error" and "Бурение ведё" in text_of(events)
    head = end_of(events)["dialog"]
    assert head["state"] == "error"
    loaded = env.dialogs.read(head["id"])
    assistant = [i for i in loaded.items if i["kind"] == "assistant"][0]
    assert assistant["interrupted"] is True and assistant["text"] == text_of(events) and assistant["text"] != ""
    assert [i["kind"] for i in loaded.items] == ["user", "assistant", "error"]
    assert loaded.messages[-1] == {"role": "assistant", "content": [{"type": "text", "text": assistant["text"]}]}
    assert len(mock_anthropic.requests_to("/v1/messages")) == 1  # no retry of a stream that began


def test_unauthorized_key_is_reported_and_the_history_stays_clean(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("unauthorized")
    events = ask(env, "Сводка по проекту")
    err = of(events, "error")[0]["item"]
    assert err["code"] == "assistant_bad_key" and "отклонён" in err["message"] and err["hint"]
    end = end_of(events)
    assert end["state"] == "error" and end["dialog"]["state"] == "error" and not of(events, "text.delta")
    loaded = env.dialogs.read(end["dialog"]["id"])
    assert loaded.messages == [] and [i["kind"] for i in loaded.items] == ["user", "error"]
    mock_anthropic.use_scenario("summary")  # the retry: the question again, nothing doubled in the history
    again = ask(env, "Сводка по проекту", end["dialog"]["id"])
    assert end_of(again)["state"] == "idle"
    assert [m["role"] for m in messages_sent(mock_anthropic)[-1]["messages"]] == ["user"]


def test_offline_is_reported(
    conn: Connection, proj: Any, fake_data_tools: Any, closed_port_url: str, tmp_path: Path
) -> None:
    offline = Connection(
        tmp_path / "off", env={"BF_ASSISTANT_BASE_URL": closed_port_url, "ANTHROPIC_API_KEY": TEST_API_KEY}
    )
    env = LoopEnv(offline, Dialogs(proj, store_parts.TempDialogs()), Segments())
    events = ask(env, "Сводка по проекту")
    err = of(events, "error")[0]["item"]
    assert err["code"] == "assistant_offline" and err["message"] == "Ассистент недоступен без интернета."
    assert "интернету" in err["hint"] and end_of(events)["state"] == "error"
    assert offline.snapshot().online is False
    assert env.dialogs.read(end_of(events)["dialog"]["id"]).messages == []


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        ({"status": 429, "message": "slow down"}, "assistant_rate_limited"),
        ({"status": 529, "message": "Overloaded"}, "assistant_overloaded"),
        ({"status": 500, "message": "boom"}, "assistant_overloaded"),
        ({"status": 404, "message": "model: x"}, "assistant_model_unavailable"),
        ({"status": 400, "message": "messages: bad"}, "assistant_api_error"),
        ({"status": 403, "message": "forbidden"}, "assistant_bad_key"),
    ],
)
def test_api_failures_become_the_codes_of_the_contract(
    env: LoopEnv, mock_anthropic: MockAnthropic, reply: dict[str, Any], code: str
) -> None:
    mock_anthropic.use_scenario({"steps": [{"match": {}, "reply": reply}]})
    events = ask(env, "Сводка по проекту")
    assert of(events, "error")[0]["item"]["code"] == code
    assert end_of(events)["state"] == "error" and not of(events, "usage")


def test_overloaded_twice_then_ok_is_retried_by_the_sdk(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("overloaded_then_ok")
    events = ask(env, "Сводка по проекту")
    assert text_of(events) == "Ответ после повторов." and end_of(events)["state"] == "idle"
    assert len(mock_anthropic.requests_to("/v1/messages")) == 3 and env.connection.snapshot().online is True


def test_depth_is_lowered_when_the_model_does_not_support_it(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    from bf_core.assistant import prefs

    prefs.set_prefs(env.connection.data_dir, None, "max")
    scenario_with(mock_anthropic, ["summary"])
    mock_anthropic.fault(
        {"status": 400, "message": "output_config.effort: 'max' is not supported by this model"}, times=1
    )
    events = ask(env, "Сводка по проекту")
    sent = [b["output_config"]["effort"] for b in messages_sent(mock_anthropic)]
    assert sent == ["max", "xhigh"] and end_of(events)["state"] == "idle"
    assert of(events, "turn.start")[0]["effort"] == "max"
    assert [n["item"]["code"] for n in of(events, "notice")] == ["effort_lowered"]
    assert loop.EFFORT_CAP == {prices.DEFAULT_MODEL: "xhigh"}
    mock_anthropic.use_scenario({"steps": [say("Ещё.")]})  # learned: the next question starts with the lower depth
    ask(env, "Ещё", end_of(events)["dialog"]["id"])
    assert messages_sent(mock_anthropic)[-1]["output_config"]["effort"] == "xhigh"


def test_depth_unsupported_down_to_low_is_an_error(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario(
        {"steps": [{"match": {}, "reply": {"status": 400, "message": "effort is not supported"}}]}
    )
    events = ask(env, "Сводка по проекту")
    assert of(events, "error")[0]["item"]["code"] == "assistant_api_error"
    assert [b["output_config"]["effort"] for b in messages_sent(mock_anthropic)] == ["medium", "low"]


@pytest.mark.parametrize(("stop", "code"), [("max_tokens", "max_tokens"), ("refusal", "refusal")])
def test_cut_and_refused_answers_get_a_notice(
    env: LoopEnv, mock_anthropic: MockAnthropic, stop: str, code: str
) -> None:
    mock_anthropic.use_scenario({"steps": [say("Начало ответа", stop_reason=stop)]})
    events = ask(env, "Сводка по проекту")
    notice = of(events, "notice")[-1]["item"]
    assert notice["code"] == code and notice["message"]
    end = end_of(events)
    assert end["state"] == "idle" and end["stop_reason"] == stop
    assert env.dialogs.read(end["dialog"]["id"]).messages[-1]["content"] == [{"type": "text", "text": "Начало ответа"}]
    if code == "max_tokens":
        assert "попросите продолжить" in notice["message"]


# --- stop -----------------------------------------------------------


def test_cancel_stops_the_stream_and_keeps_the_text(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    text = "Очень длинный ответ про бурение. " * 8
    mock_anthropic.use_scenario({"steps": [say(text, delay_ms=60, chunk=6)]})
    seg = loop.start_turn(env, turn_body("Расскажи про бурение"))
    assert wait_for(lambda: any(e.event == "text.delta" for e in seg.events_after(0)))
    t0 = time.perf_counter()
    assert loop.cancel_turn(env, seg.dialog_id) is True
    events = finished(seg)
    assert time.perf_counter() - t0 < 2.0  # the stream is closed, the loop does not wait for the end of the answer
    end = end_of(events)
    assert end["state"] == "cancelled" and end["dialog"]["state"] == "idle"
    got = text_of(events)
    assert 0 < len(got) < len(text)
    loaded = env.dialogs.read(end["dialog"]["id"])
    item = [i for i in loaded.items if i["kind"] == "assistant"][0]
    assert item["stopped"] is True and item["text"] == got and not of(events, "error")
    assert loaded.messages[-1] == {"role": "assistant", "content": [{"type": "text", "text": got}]}
    assert loop.cancel_turn(env, seg.dialog_id) is False  # nothing runs any more: not an error


def test_cancel_without_a_segment_is_quiet(env: LoopEnv) -> None:
    assert loop.cancel_turn(env, "999") is False


def test_a_dialog_answers_one_question_at_a_time(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    from bf_core.api.errors import ApiError

    mock_anthropic.use_scenario({"steps": [say("Долгий ответ. " * 10, delay_ms=40, chunk=5)]})
    seg = loop.start_turn(env, turn_body("Первый"))
    with pytest.raises(ApiError) as e:
        loop.start_turn(env, turn_body("Второй", seg.dialog_id))
    assert e.value.status == 409 and e.value.code == "assistant_busy"
    assert env.dialogs.list(busy=env.segments.busy())["total"] == 0  # not listed until its first part
    loop.cancel_turn(env, seg.dialog_id)
    finished(seg)


def test_reader_catches_up_after_a_gap(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    """`GET …/stream?after=n`: the events above n of the running segment, then the new ones, up to `turn.end`."""
    mock_anthropic.use_scenario({"steps": [say("Ответ частями. " * 6, delay_ms=20, chunk=7)]})
    seg = loop.start_turn(env, turn_body("Вопрос"))
    assert wait_for(lambda: seg.last_id >= 4)
    after = 3
    late = [e for e in seg.iter(after=after) if e is not None]
    assert [e.id for e in late] == list(range(after + 1, seg.last_id + 1)) and late[-1].event == "turn.end"
    assert env.segments.get(seg.dialog_id) is None


# --- the secret -----------------------------------------------------


def test_the_key_is_nowhere(
    env: LoopEnv, mock_anthropic: MockAnthropic, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    caplog.set_level(logging.DEBUG)
    scenario_with(mock_anthropic, ["unauthorized"])
    bad = ask(env, "Сводка по проекту")  # a failed call, too
    mock_anthropic.use_scenario("summary")
    good = ask(env, "Сводка по проекту", end_of(bad)["dialog"]["id"])
    head = end_of(good)["dialog"]
    loaded = env.dialogs.read(head["id"])
    everything = haystack(
        mock_anthropic.requests,
        [(e.event, e.data) for e in bad + good],
        loaded.messages,
        loaded.items,
        loaded.meta,
        head,
        caplog.text,
    )
    assert TEST_API_KEY not in everything and "sk-ant-test" not in everything
    for path in (tmp_path / "data").rglob("*"):
        if path.is_file():
            assert TEST_API_KEY.encode() not in path.read_bytes()
    assert TEST_API_KEY not in env.connection.scrub(TEST_API_KEY)


def test_every_item_and_event_is_of_the_contract(env: LoopEnv, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("summary")
    events = ask(env, "Сводка по проекту")  # `finished` validated every event
    loaded = env.dialogs.read(end_of(events)["dialog"]["id"])
    for item in loaded.items:
        schemas_dialog.ITEM.validate_python(item)
    schemas_dialog.DialogHead.model_validate(loaded.head)
    assert CONTEXT["project"]["name"] == "Демо"
