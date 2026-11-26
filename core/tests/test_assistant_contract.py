"""The contract of the Claude assistant API (подшаг 23.1; ТЗ 7.32; ADR 0029; docs/progress/e23-api.md): the form of
`tests/data/assistant/contract_answers.json` equals the schemas of `bf_core/assistant/schemas*.py` (every sample parses
and writes back as it is: no key lost, none added), the texts of the errors are those of `errors.py`, the streams keep
the order of the events of §10, no secret is in the samples. The bodies of the requests - `test_assistant_bodies.py`.
Until 23.11 the samples are hand-made: `BF_REGEN_ASSISTANT=1` is a stub (nothing to write them with yet) and skips."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from bf_core.api.errors import ApiError
from bf_core.assistant import errors as er
from bf_core.assistant import schemas as sc
from bf_core.assistant import schemas_base as sb
from bf_core.assistant import schemas_dialog as sd

PATH = Path(__file__).parent / "data" / "assistant" / "contract_answers.json"
REGEN = os.environ.get("BF_REGEN_ASSISTANT") == "1"
DATA: dict[str, Any] = json.loads(PATH.read_text(encoding="utf-8"))
TEST_KEY = "sk-ant-test-0000000000000000000000"
CYRILLIC = re.compile("[а-яё]", re.I)


def roundtrip(model: type[BaseModel], raw: Any) -> BaseModel:
    """`raw` parses as `model` and writes back exactly as it is (an unset key stays unset)."""
    obj = model.model_validate(raw)
    assert obj.model_dump(mode="json", exclude_unset=True) == raw
    return obj


def body(key: str) -> Any:
    answer = DATA[key]
    assert answer["status"] == 200, key
    return answer["body"]


def refused(model: type[BaseModel], raw: Any, code: str = "bad_param", status: int = 422) -> ApiError:
    with pytest.raises(ApiError) as e:
        sc.parse_body(model, raw)
    assert (e.value.status, e.value.code) == (status, code), (e.value.code, e.value.message)
    assert e.value.message and e.value.hint and CYRILLIC.search(e.value.message + e.value.hint)  # Russian + a hint
    return e.value


def test_regen_is_a_stub() -> None:
    if REGEN:
        pytest.skip(
            "BF_REGEN_ASSISTANT: образцы — вручную до 23.11 (настоящее ядро ещё не отвечает); нечего перезаписывать"
        )
    assert DATA["_note"]


# --- §2 status, §4 usage -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["status_none", "status_key", "status_account"])
def test_status_samples(key: str) -> None:
    st = roundtrip(sc.Status, body(key))
    assert isinstance(st, sc.Status)
    assert [m.id for m in st.models] == ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2"]
    assert [e.id for e in st.efforts] == list(sb.EFFORTS) and sum(m.default for m in st.models) == 1
    assert st.model in {m.id for m in st.models} and st.prices_version == "2026-11"
    assert st.subscription_note == er.TEXT_SUBSCRIPTION
    for m in st.models:  # cache_write = 1,25 x input (ТЗ 7.32, ADR 0029 решение 13)
        assert m.price.cache_write == pytest.approx(1.25 * m.price.input)
        if m.long:
            assert m.long.threshold == 100000 and m.long.cache_write == pytest.approx(1.25 * m.long.input)


def test_status_states() -> None:
    none, key, acc = (sc.Status.model_validate(body(k)) for k in ("status_none", "status_key", "status_account"))
    assert (none.state, none.kind, none.mask, none.online, none.checked_at) == ("none", None, None, None, None)
    assert (key.state, key.kind, key.mask, key.seal_needed) == ("connected", "api_key", "sk-ant-…a1B2", True)
    assert key.model_source == "prefs" and key.effort == "high"
    assert acc.kind == "account" and acc.organization is not None and acc.organization.name == "ООО Рудник"
    prices = {m.id: (m.price.input, m.price.output, m.price.cache_read) for m in none.models}  # ТЗ 7.32
    assert prices == {
        "claude-opus-5-5": (4.0, 20.0, 0.20),
        "claude-sonnet-5-5": (2.0, 10.0, 0.10),
        "claude-haiku-5-5": (0.10, 0.50, 0.01),
        "claude-fable-5-2": (8.0, 40.0, 0.20),
    }


def test_usage_sample() -> None:
    u = roundtrip(sc.Usage, body("usage"))
    assert isinstance(u, sc.Usage)
    for p in (u.today, u.days30, u.all):
        assert p.requests == sum(r.requests for r in p.by_model)
        for k in ("input", "output", "cache_read", "cache_write"):
            assert getattr(p.tokens, k) == sum(getattr(r.tokens, k) for r in p.by_model)
        known = [r.cost_usd for r in p.by_model if r.cost_usd is not None]
        assert p.cost_usd == pytest.approx(sum(known))
    assert u.today.requests <= u.days30.requests <= u.all.requests
    # a gap is null, not 0
    gap = {
        "requests": 1,
        "tokens": dict.fromkeys(("input", "output", "cache_read", "cache_write"), 1),
        "cost_usd": None,
        "unpriced_requests": 1,
        "by_model": [],
    }
    assert sc.Period.model_validate(gap).cost_usd is None


def test_usage_line() -> None:
    line = {
        "at": "2026-11-26T10:00:00Z",
        "model": "claude-opus-5-5",
        "input": 1,
        "output": 2,
        "cache_read": 3,
        "cache_write": 4,
        "cost_usd": None,
        "long": False,
        "dialog_uid": None,
        "project_uid": None,
        "prices_version": "2026-11",
    }
    roundtrip(sc.UsageLine, line)
    refused_line = dict(line, at="2026-11-26 10:00")
    with pytest.raises(ValueError):
        sc.UsageLine.model_validate(refused_line)


# --- §14 errors -----------------------------------------------------------------------------------------------------


def test_error_samples_are_the_texts_of_errors_py() -> None:
    assert DATA["errors"] == json.loads(
        json.dumps(
            {c: {"status": s, "body": {"code": c, "message": m, "hint": h}} for c, (s, m, h) in er.REFUSALS.items()}
        )
    )
    for code, answer in DATA["errors"].items():
        b = answer["body"]
        assert b["code"] == code and b["message"] and b["hint"] and CYRILLIC.search(b["message"]), code
        assert answer["status"] >= 400


def test_contract_codes_exist() -> None:
    table = {  # §14 (HTTP) and §12 (API of Anthropic)
        403: ["shell_only"],
        404: ["assistant_dialog_not_found", "assistant_login_gone"],
        409: [
            "no_project",
            "assistant_not_connected",
            "assistant_busy",
            "assistant_no_pending",
            "assistant_pending_mismatch",
        ],
        422: [
            "bad_param",
            "assistant_bad_key_format",
            "assistant_bad_key",
            "assistant_bad_tools",
            "assistant_login_code",
            "assistant_dialog_full",
        ],
        501: ["assistant_no_ant"],
        502: ["assistant_login_failed"],
        503: ["assistant_offline"],
    }
    for status, codes in table.items():
        for code in codes:
            assert er.REFUSALS[code][0] == status, code
    for code in er.STREAM_ERRORS:  # §10 `error` events
        assert code in er.REFUSALS
    assert {
        "assistant_model_unavailable",
        "assistant_rate_limited",
        "assistant_overloaded",
        "assistant_api_error",
        "assistant_interrupted",
        "internal",
    } <= set(er.REFUSALS)
    assert set(er.NOTICES) == {"effort_lowered", "max_tokens", "refusal", "context_trimmed", "temporary", "max_rounds"}
    assert er.NOTICES["max_tokens"] == "Ответ обрезан: попросите продолжить."


def test_error_helpers() -> None:
    e = er.refuse("assistant_busy")
    assert (e.status, e.code, e.extra) == (409, "assistant_busy", {})
    assert er.refuse("assistant_offline", "Свой текст.").message == "Свой текст."
    assert er.bad_param("title").extra == {"param": "title"}
    assert (
        er.model_unavailable("claude-x").extra == {"model": "claude-x"}
        and "claude-x" in er.model_unavailable("claude-x").message
    )
    assert er.pending_mismatch(["toolu_1"]).extra == {"expected": ["toolu_1"]}
    assert er.error_fields("assistant_offline")[0] == "Ассистент недоступен без интернета."
    assert er.notice_message("temporary") == er.NOTICES["temporary"]


def test_error_samples_of_the_bodies() -> None:
    def answer(e: ApiError) -> dict[str, Any]:
        return {"status": e.status, "body": {**e.extra, "code": e.code, "message": e.message, "hint": e.hint}}

    assert DATA["prefs_bad"] == answer(er.bad_param("model"))
    assert DATA["key_bad_format"] == answer(er.refuse("assistant_bad_key_format"))
    assert DATA["key_bad"] == answer(er.refuse("assistant_bad_key"))
    assert answer(refused(sc.KeyBody, {"key": "hello"}, "assistant_bad_key_format")) == DATA["key_bad_format"]
    assert answer(refused(sc.PrefsBody, {"model": "bad model!"})) == DATA["prefs_bad"]


# --- §7 dialogs -----------------------------------------------------------------------------------------------------


def test_dialog_samples() -> None:
    page = roundtrip(sd.DialogsPage, body("dialogs_list"))
    assert isinstance(page, sd.DialogsPage) and page.total == len(page.items) == 2
    assert page.items[0].last_at >= page.items[1].last_at  # new on top
    for key in ("dialog_full_sample", "dialog_temporary"):
        d = roundtrip(sd.Dialog, body(key))
        assert isinstance(d, sd.Dialog)
        ids = [i.id for i in d.items]
        assert len(ids) == len(set(ids)), key
        assert {i.kind for i in d.items} <= {"user", "assistant", "tool", "notice", "error"}
    full = sd.Dialog.model_validate(body("dialog_full_sample"))
    assert {i.kind for i in full.items} == {"user", "assistant", "tool", "notice", "error"}
    assert full.pending is not None and full.head.state == "pending"
    waiting = {i.tool_use_id: i for i in full.items if i.kind == "tool" and i.status == "waiting"}
    assert [t.tool_use_id for t in full.pending.tools] == list(waiting)  # pending = the waiting ui calls
    for t in full.pending.tools:
        assert (
            t.name.startswith("ui_") and waiting[t.tool_use_id].name == t.name and waiting[t.tool_use_id].side == "ui"
        )
        assert waiting[t.tool_use_id].id == t.item_id
    temp = sd.Dialog.model_validate(body("dialog_temporary"))
    assert (
        temp.head.temporary
        and temp.head.id.startswith("t-")
        and temp.head.project_uid is None
        and temp.note == er.NOTE_TEMPORARY
    )
    assert sb.re.compile(sc.DIALOG_ID_RE.pattern).match(temp.head.id) and sc.DIALOG_ID_RE.match("41")


def test_stored_forms() -> None:
    d = sd.Dialog.model_validate(body("dialog_full_sample"))
    meta = {
        **d.head.model_dump(mode="json", exclude={"id"}),
        "parts": ["a" * 64],
        "bytes": 1200,
        "pending": None,
        "prices_version": "2026-11",
    }
    roundtrip(sd.DialogMeta, meta)
    part = {
        "seq": 1,
        "at": "2026-11-26T10:00:00Z",
        "messages": [{"role": "user", "content": "Привет"}],
        "items": [i.model_dump(mode="json") for i in d.items],
    }
    roundtrip(sd.DialogPart, part)
    with pytest.raises(ValueError):
        sd.DialogPart.model_validate({**part, "seq": 0})


def test_items_by_kind() -> None:
    with pytest.raises(ValueError):
        sd.ITEM.validate_python({"kind": "robot", "id": "i-1", "at": "2026-11-26T10:00:00Z"})
    with pytest.raises(ValueError):  # a status that is not of the contract
        sd.ITEM.validate_python(
            {
                "kind": "tool",
                "id": "i",
                "at": "2026-11-26T10:00:00Z",
                "tool_use_id": "t",
                "name": "n",
                "side": "ui",
                "title": "t",
                "args": {},
                "status": "running",
                "summary": "",
            }
        )
    with pytest.raises(ValueError):  # a summary longer than 4000
        sd.ToolEnd.model_validate({"item_id": "i-2", "status": "ok", "summary": "x" * 4001})


# --- §10 streams ----------------------------------------------------------------------------------------------------

STREAMS = [k for k in DATA if k.startswith("stream_")]


def test_streams_listed() -> None:
    assert {
        "stream_text",
        "stream_data_tools",
        "stream_pending",
        "stream_after_results",
        "stream_confirm",
        "stream_offline",
        "stream_cancelled",
    } <= set(STREAMS)


@pytest.mark.parametrize("key", STREAMS)
def test_stream_samples(key: str) -> None:
    events = DATA[key]
    assert isinstance(events, list) and events
    parsed = []
    for n, e in enumerate(events, 1):
        sse = sc_sse(e)
        assert sse.id == n, key  # `id` from 1 in a segment
        parsed.append((sse.event, sd.parse_event(sse.event, sse.data)))
        assert json.loads(json.dumps(sse.data)) == sse.data
    names = [n for n, _ in parsed]
    assert (
        names[0] == "turn.start"
        and names[-1] == "turn.end"
        and names.count("turn.start") == names.count("turn.end") == 1
    )
    end = parsed[-1][1]
    assert isinstance(end, sd.TurnEnd)
    assert ("pending" in names) == (end.state == "pending")
    if "pending" in names:
        assert names.index("pending") == len(names) - 2  # the last one before `turn.end`
    assert ("error" in names) == (end.state == "error")
    open_tools: set[str] = set()
    for _, ev in parsed:
        if isinstance(ev, sd.ToolStart):
            assert ev.item.side == "data" and ev.item.status == "waiting"
            open_tools.add(ev.item.id)
        if isinstance(ev, sd.ToolEnd):
            assert ev.item_id in open_tools  # `tool.end` closes a `tool.start`
            open_tools.discard(ev.item_id)
        if isinstance(ev, sd.Pending):
            assert ev.tools and all(t.name.startswith("ui_") for t in ev.tools)
    assert not open_tools
    start = parsed[0][1]
    assert isinstance(start, sd.TurnStart)
    assert start.dialog.state == "running" and end.dialog.state in ("idle", "pending", "error")


def sc_sse(raw: Any) -> sd.SseEvent:
    return sd.SseEvent.model_validate(raw)


def test_stream_specifics() -> None:
    new, cont = DATA["stream_pending"][0]["data"], DATA["stream_after_results"][0]["data"]
    assert new["user_item"]["text"] == "Покажи скважину DH-012 на разрезе" and "user_item" not in cont  # a continuation
    pend = sd.Pending.model_validate(DATA["stream_pending"][-2]["data"])
    assert [t.name for t in pend.tools] == ["ui_hole_select", "ui_section_through_hole"]
    conf = sd.Pending.model_validate(DATA["stream_confirm"][-2]["data"])
    assert [t.name for t in conf.tools] == ["ui_project_delete"]
    off = [e for e in DATA["stream_offline"] if e["event"] == "error"][0]["data"]["item"]
    assert off["code"] == "assistant_offline" and off["message"] == er.REFUSALS["assistant_offline"][1]
    assert DATA["stream_cancelled"][-1]["data"]["state"] == "cancelled"
    assert DATA["stream_text"][-1]["data"]["stop_reason"] == "end_turn"
    usage_events = [sd.UsageEvent.model_validate(e["data"]) for e in DATA["stream_data_tools"] if e["event"] == "usage"]
    assert usage_events[-1].dialog.tokens.input == sum(
        u.call.input for u in usage_events
    )  # the dialog total grows by calls


def test_event_models() -> None:
    assert set(sd.EVENT_MODELS) == {
        "turn.start",
        "text.delta",
        "thinking",
        "tool.start",
        "tool.end",
        "pending",
        "usage",
        "notice",
        "error",
        "turn.end",
    }
    with pytest.raises(ValueError):
        sd.parse_event("turn.pause", {})
    with pytest.raises(ValueError):
        sd.parse_event("thinking", {"state": "maybe"})
    with pytest.raises(ValueError):  # an unknown key of an event is refused
        sd.parse_event("text.delta", {"item_id": "i", "text": "x", "extra": 1})
    conn = {
        "state": "connected",
        "kind": "api_key",
        "mask": "sk-ant-…a1B2",
        "model": "claude-opus-5-5",
        "online": None,
        "seal_needed": True,
    }
    assert isinstance(sd.parse_event("assistant.connection", conn), sd.ConnectionEvent)
    with pytest.raises(ValueError):  # no secret in an event of the bus
        sd.parse_event("assistant.connection", {**conn, "secret": "x"})
    assert sd.parse_event("assistant.dialogs", {"project_uid": None, "id": "t-0b6f2d4e-8c1a-4d3e-9a5b-7c2e1f0a9b88"})


# --- §5 tools, §6 context, §8-§9 bodies -----------------------------------------------------------------------------


def test_data_tools_sample() -> None:
    tools = [roundtrip(sc.DataToolInfo, t) for t in DATA["data_tools"]]
    names = [t.name for t in tools]  # type: ignore[attr-defined]
    assert names == [
        "data_tree",
        "data_summary",
        "data_holes",
        "data_hole",
        "data_history",
        "data_audit",
        "data_findings",
        "data_table",
        "data_stats",
    ]
    for t in DATA["data_tools"]:
        s = t["input_schema"]
        assert (
            s["type"] == "object" and s["additionalProperties"] is False and set(s["required"]) <= set(s["properties"])
        )
        assert t["title"] and CYRILLIC.search(t["title"])
    by = {t["name"]: t["input_schema"] for t in DATA["data_tools"]}
    assert by["data_hole"]["required"] == ["hole_id"] and by["data_stats"]["required"] == ["table", "field"]
    assert by["data_findings"]["properties"]["severity"]["enum"] == ["E", "W", "I"]


# --- constants and secrets ------------------------------------------------------------------------------------------


def test_limits_of_the_contract() -> None:
    assert (sb.TEXT_MAX, sb.TITLE_MAX, sb.CODE_MAX, sb.TOOL_TEXT_MAX, sb.CONTEXT_MAX) == (20_000, 200, 4096, 4000, 4096)
    assert (sb.TOOLS_MAX, sb.TOOLS_BYTES_MAX, sb.TOOL_DESCRIPTION_MAX, sb.DATA_TEXT_MAX, sb.PAGE_MAX) == (
        400,
        512 * 1024,
        2000,
        16_000,
        200,
    )
    assert sb.EFFORTS == ("low", "medium", "high", "xhigh", "max")


def test_no_secret_in_the_samples() -> None:
    text = PATH.read_text(encoding="utf-8")
    assert not re.search(r"sk-ant-[A-Za-z0-9_-]{20,}", text)  # no key, not even a test one
    assert "access_token" not in text and "refresh_token" not in text and "secret" not in json.dumps(DATA["status_key"])
    assert text.endswith("}\n") and "\r" not in text
    for key in ("status_none", "status_key", "status_account"):  # the mask of the status is not a key
        mask = DATA[key]["body"]["mask"]
        assert mask is None or "…" in mask


def test_samples_in_russian() -> None:
    for key in ("status_none", "dialog_full_sample"):
        assert CYRILLIC.search(json.dumps(DATA[key], ensure_ascii=False))
    assert DATA["usage"]["body"]["all"]["by_model"][0]["title"] == "Opus 5.5"
