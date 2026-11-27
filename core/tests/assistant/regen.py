"""The samples of the contract (`tests/data/assistant/contract_answers.json`) made by a RUNNING core (подшаг 23.11).

`build(api, ...)` drives the core of `test_assistant_api_support.py` (the real routes, the real loop, the real data
tools on the demo project) against the Anthropic mock and returns `{key: sample}`; `normalize` replaces what changes
from run to run (ids, uids, times) by stable values, so the file does not move when nothing changed. The test
`test_the_answers_are_what_the_core_answers` compares `shape(build)` with the file; `BF_REGEN_ASSISTANT=1` writes it.

Keys: `status_none`, `status_key`, `status_account`, `usage`, `prefs_bad`, `key_bad_format`, `key_bad`, `dialogs_list`,
`dialog_full_sample` (with a `pending`), `dialog_temporary`, `stream_text`, `stream_data_tools`, `stream_pending`,
`stream_after_results`, `stream_confirm`, `stream_declined`, `stream_offline`, `stream_cancelled`, `stream_notice`
(`max_tokens`), `stream_temporary`, `stream_interrupted` (a cut stream), `stream_unauthorized` (401), `data_tools`
(the registry), `errors` (the table of `errors.py`). `requests` (the bodies of the requests) is kept as it is.
The key of the samples is `A1B2_KEY` (the mask ends with `a1B2`); the test key of the mock is never written.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.assistant.mock_anthropic import TEST_ACCESS_TOKEN, TEST_REFRESH_TOKEN
from tests.test_assistant_api_support import API, CONTEXT, UI_TOOLS, Api, in_thread, sse_events, wait_for

A1B2_KEY = "sk-ant-regen-00000000000000a1B2"
PROJECT_UID = "a7b1c9d0-0000-4000-8000-000000000001"
BASE_TIME = datetime(2026, 11, 26, 10, 0, 0, tzinfo=UTC)
HOLE_ASK = "Покажи скважину DH-012 на разрезе"
STAMP_IN_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00")  # `ran_at` of an audit in a summary
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
TEMP = re.compile(r"^t-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
ITEM = re.compile(r"^([a-z])-[0-9a-f]{12}$")
SLOW = {
    "match": {},
    "reply": {"content": [{"type": "text", "text": "Медленный ответ " * 8}], "chunk": 4, "delay_ms": 40},
}
LONG = {
    "match": {"last_user_text": "Опиши весь проект"},
    "reply": {
        "content": [{"type": "text", "text": "Самая длинная часть ответа, которая"}],
        "stop_reason": "max_tokens",
        "usage": {"input_tokens": 1200, "output_tokens": 4096, "cache_creation_input_tokens": 9000},
    },
}


KEYS = (
    "status_none", "status_key", "status_account", "usage", "prefs_bad", "key_bad_format", "key_bad", "dialogs_list",
    "dialog_full_sample", "dialog_temporary", "stream_text", "stream_data_tools", "stream_pending",
    "stream_after_results", "stream_confirm", "stream_declined", "stream_offline", "stream_cancelled", "stream_notice",
    "stream_temporary", "stream_interrupted", "stream_unauthorized", "data_tools", "errors",
)  # fmt: skip


def sample(r: Any) -> dict[str, Any]:
    """An answer of a plain route as the file keeps it."""
    return {"status": r.status_code, "body": r.json()}


def stream(api: Api, question: str, dialog_id: str | None = None) -> list[dict[str, Any]]:
    code, events, text = api.ask(question, dialog_id)
    assert code == 200, text
    return events


#: What the window answers for a command: (the title of the card, the text of the result).
WINDOW = {
    "ui_hole_select": ("Выбрать скважину", "Выбрана скважина DH-012"),
    "ui_section_through_hole": ("Разрез через скважину", "Построен разрез через скважину DH-012"),
    "ui_project_delete": ("Удалить проект", "Пользователь отклонил действие"),
}


def answer(api: Api, dialog_id: str, pending: dict[str, Any], ok: bool) -> list[dict[str, Any]]:
    """The results of the window for a `pending` as the window posts them (with the title of each card)."""
    results = [
        {"tool_use_id": t["tool_use_id"], "ok": ok, "text": WINDOW[t["name"]][1], "title": WINDOW[t["name"]][0]}
        for t in pending["tools"]
    ]
    body = {"pending_id": pending["id"], "results": results, "context": CONTEXT, "tools": UI_TOOLS}
    r = api.win.post(f"{API}/dialogs/{dialog_id}/tool_results", json=body)
    assert r.status_code == 200, r.text
    return sse_events(r.text)


def pause(slow: bool) -> None:
    """The time of an item is in seconds: a pause between the steps makes the samples tell the order of the steps."""
    if slow:
        time.sleep(1.05)


def build(api: Api, *, closed_port_url: str, base_url: str, setenv: Any, slow: bool = False) -> dict[str, Any]:
    """Every sample of the file that a core can give; the demo project is opened in the middle of the run."""
    from bf_core.assistant import data_tools, errors
    from bf_core.assistant import usage as usage_mod

    win, shell, mock = api.win, api.shell, api.mock
    mock.api_keys.add(A1B2_KEY)
    out: dict[str, Any] = {}
    # the usage of the past: a fortnight ago (Sonnet), two months ago (Haiku and a model without a price)
    data_dir = api.state.data_dir
    for days, model, tokens in (
        (60, "claude-haiku-5-5", 4000),
        (60, "claude-unknown-9", 700),
        (10, "claude-sonnet-5-5", 9000),
    ):
        usage_mod.record(data_dir, model=model, input=tokens, output=tokens // 10, cache_read=tokens * 3,
                         cache_write=tokens // 2, now=datetime.now(UTC) - timedelta(days=days))  # fmt: skip

    # --- the connection ---
    out["status_none"] = sample(win.get(f"{API}/status"))
    out["key_bad_format"] = sample(win.post(f"{API}/connection/key", json={"key": "hello"}))
    out["key_bad"] = sample(win.post(f"{API}/connection/key", json={"key": "sk-ant-wrong-00000000000000000000"}))
    out["prefs_bad"] = sample(win.put(f"{API}/prefs", json={"model": "bad model!"}))
    api.connect(A1B2_KEY)
    assert win.put(f"{API}/prefs", json={"model": "claude-opus-5-5", "effort": "high"}).status_code == 200
    out["status_key"] = sample(win.get(f"{API}/status"))
    win.put(f"{API}/prefs", json={"effort": "medium"})
    account = {
        "access_token": TEST_ACCESS_TOKEN,
        "refresh_token": TEST_REFRESH_TOKEN,
        "client_id": "00000000-0000-4000-8000-000000000001",
        "organization_id": "org_test0000000000",
        "workspace_id": "wrkspc_test00000000",
        "expires_at": "2026-12-01T00:00:00Z",
    }
    shell.put(f"{API}/connection/secret", json={"kind": "account", "secret": account, "secure_storage": True})
    out["status_account"] = sample(win.get(f"{API}/status"))
    api.connect(A1B2_KEY)

    # --- without a project: a temporary dialog ---
    pause(slow)
    mock.use_scenario("summary")
    out["stream_temporary"] = stream(api, "Сводка по проекту")
    temp_id = out["stream_temporary"][0]["data"]["dialog"]["id"]
    out["dialog_temporary"] = sample(win.get(f"{API}/dialogs/{temp_id}"))

    # --- the demo project ---
    assert win.post("/api/v1/projects/demo").status_code == 200
    ids: dict[str, str] = {}
    pause(slow)
    mock.use_scenario("summary")
    out["stream_text"] = stream(api, "Сводка по проекту")
    ids["text"] = out["stream_text"][-1]["data"]["dialog"]["id"]
    pause(slow)
    mock.use_scenario("data_tools")
    out["stream_data_tools"] = stream(api, "Что не так с данными?")
    ids["data"] = out["stream_data_tools"][-1]["data"]["dialog"]["id"]
    pause(slow)
    mock.use_scenario("project_delete")
    out["stream_confirm"] = stream(api, "Удали проект")
    ids["confirm"] = out["stream_confirm"][-1]["data"]["dialog"]["id"]
    pending = next(e["data"] for e in out["stream_confirm"] if e["event"] == "pending")
    out["stream_declined"] = answer(api, ids["confirm"], pending, ok=False)

    # --- failures ---
    pause(slow)
    mock.use_scenario("drop")
    out["stream_interrupted"] = stream(api, "Расскажи подробно про бурение")
    mock.use_scenario("unauthorized")
    out["stream_unauthorized"] = stream(api, "Привет")
    setenv("BF_ASSISTANT_BASE_URL", closed_port_url)
    out["stream_offline"] = stream(api, "Привет")
    setenv("BF_ASSISTANT_BASE_URL", base_url)

    # --- a stop of the user ---
    mock.use_scenario({"steps": [SLOW]})
    thread, box = in_thread(lambda: api.ask("Расскажи подробно"))
    assert wait_for(lambda: api.svc.segments.busy())
    running = next(iter(api.svc.segments.busy()))
    assert wait_for(lambda: api.svc.segments.get(running).last_id >= 4)
    assert win.post(f"{API}/dialogs/{running}/cancel", json={}).status_code == 204
    thread.join(30)
    out["stream_cancelled"] = box["value"][1]

    # --- one dialog with everything in it: a summary, a cut answer (notice), a failure (error), a command (pending) ---
    pause(slow)
    mock.use_scenario("summary")
    first = stream(api, "Сводка по проекту")
    full = first[-1]["data"]["dialog"]["id"]
    pause(slow)
    mock.use_scenario({"steps": [LONG]})
    out["stream_notice"] = stream(api, "Опиши весь проект", full)
    pause(slow)
    mock.use_scenario({"steps": [{"match": {}, "reply": {"status": 529}}]})
    assert end_state(stream(api, "Ещё вопрос", full)) == "error"
    pause(slow)
    mock.use_scenario("hole_section")
    out["stream_pending"] = stream(api, HOLE_ASK, full)
    pending = next(e["data"] for e in out["stream_pending"] if e["event"] == "pending")
    out["stream_after_results"] = answer(api, full, pending, ok=True)
    pause(slow)
    mock.use_scenario("hole_section")
    again = stream(api, HOLE_ASK, full)  # the dialog that waits for the window
    out["dialog_full_sample"] = sample(win.get(f"{API}/dialogs/{full}"))
    assert out["dialog_full_sample"]["body"]["pending"] is not None and end_state(again) == "pending"

    # --- the list: the summary and the full dialog; the rest of the dialogs of the run is deleted ---
    kept = {ids["text"], full}
    for dialog in win.get(f"{API}/dialogs?limit=200").json()["items"]:
        if dialog["id"] not in kept:
            assert win.delete(f"{API}/dialogs/{dialog['id']}").status_code == 204
    out["dialogs_list"] = sample(win.get(f"{API}/dialogs"))
    out["usage"] = sample(win.get(f"{API}/usage"))
    out["data_tools"] = data_tools.infos()
    out["errors"] = {
        c: {"status": s, "body": {"code": c, "message": m, "hint": h}} for c, (s, m, h) in errors.REFUSALS.items()
    }
    return out


def end_state(events: list[dict[str, Any]]) -> str:
    return str(events[-1]["data"]["state"])


# --- the stable form ------------------------------------------------------------------------------------------------


class Names:
    """A value of a kind -> its number, in the order of the first appearance."""

    def __init__(self) -> None:
        self.maps: dict[str, dict[str, int]] = defaultdict(dict)

    def of(self, kind: str, value: str) -> int:
        table = self.maps[kind]
        return table.setdefault(value, len(table) + 1)


def times_of(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        for v in node.values():
            times_of(v, found)
    elif isinstance(node, list):
        for v in node:
            times_of(v, found)
    elif isinstance(node, str) and ISO.match(node):
        found.add(node)


def normalize(samples: dict[str, Any]) -> dict[str, Any]:
    """The samples with stable ids, uids and times (the real ones change at every run)."""
    found: set[str] = set()
    times_of(samples, found)
    clock = {t: BASE_TIME + timedelta(seconds=n) for n, t in enumerate(sorted(found))}
    names = Names()

    def string(key: str | None, value: str, parent: dict[str, Any] | None) -> str:
        value = STAMP_IN_TEXT.sub("2026-11-26T10:00:00+00:00", value)
        if value in clock:
            return clock[value].strftime("%Y-%m-%dT%H:%M:%SZ")
        if key == "project_uid":
            return PROJECT_UID
        if TEMP.match(value):
            return f"t-0b6f2d4e-8c1a-4d3e-9a5b-{names.of('temp', value):012x}"
        if key == "uid" and parent is not None and "title" in parent:
            return f"3f2a9c5e-1d00-4b00-8000-{names.of('dialog', value):012d}"
        if key in ("id", "dialog_id") and value.isdigit():
            return str(40 + names.of("id", value))
        if value.startswith("seg-"):
            return f"seg-{names.of('seg', value)}"
        if re.match(r"^p-[0-9a-f]{16}$", value):
            return f"p-{names.of('pending', value)}"
        m = ITEM.match(value)
        if m:
            return f"{m.group(1)}-{names.of('item', value)}"
        if value.startswith("toolu_mock"):
            return f"toolu_{names.of('tool', value):03d}"
        return value

    def walk(node: Any, key: str | None = None, parent: dict[str, Any] | None = None) -> Any:
        if isinstance(node, dict):
            return {k: walk(v, k, node) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, key, parent) for v in node]
        if isinstance(node, str):
            return string(key, node, parent)
        return node

    return {k: walk(v) for k, v in samples.items()}


def shape(samples: dict[str, Any]) -> dict[str, Any]:
    """What a re-run must give again: the samples without the times and without the length of a stopped answer."""

    def blank(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: blank(v) for k, v in node.items()}
        if isinstance(node, list):
            return [blank(v) for v in node]
        if isinstance(node, str) and ISO.match(node):
            return "<time>"
        return node

    out = {k: blank(v) for k, v in samples.items()}
    cancelled = out.get("stream_cancelled")
    if isinstance(cancelled, list):  # how many pieces came before the stop depends on the speed of the machine
        out["stream_cancelled"] = [e["event"] for e in cancelled if e["event"] != "text.delta"]
    return out


def raw_events(text: str) -> list[dict[str, Any]]:  # re-exported for the tests
    return sse_events(text)


def first_diff(a: Any, b: Any, path: str = "") -> str | None:
    """Where two samples differ first (a path and the two values), None when they are the same."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}/{k}: only in the {'file' if k in b else 'core'}"
            if (d := first_diff(a[k], b[k], f"{path}/{k}")) is not None:
                return d
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} items from the core, {len(b)} in the file"
        return next(
            (d for i, (x, y) in enumerate(zip(a, b, strict=True)) if (d := first_diff(x, y, f"{path}/{i}"))), None
        )
    return None if a == b and type(a) is type(b) else f"{path}: core {str(a)[:120]!r} != file {str(b)[:120]!r}"
