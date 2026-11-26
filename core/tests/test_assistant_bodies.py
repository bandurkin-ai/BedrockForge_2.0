"""The bodies of the requests of the Claude assistant API (подшаг 23.1; docs/progress/e23-api.md §3, §5-§9): the samples
of `contract_answers.json` -> `requests` parse; every refusal is `bad_param {param}` (the first key that failed),
`assistant_bad_key_format` or `assistant_bad_tools {detail}` with a Russian text and a hint; the value of a secret is
never in an error; the limits of the contract (text 20 000, title 200, code 4096, context 4 KB, 400 tools, 256 KB)."""

from __future__ import annotations

import copy
import json
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
DATA: dict[str, Any] = json.loads(PATH.read_text(encoding="utf-8"))
TEST_KEY = "sk-ant-test-0000000000000000000000"
CYRILLIC = re.compile("[а-яё]", re.I)


def roundtrip(model: type[BaseModel], raw: Any) -> BaseModel:
    obj = model.model_validate(raw)
    assert obj.model_dump(mode="json", exclude_unset=True) == raw
    return obj


def refused(model: type[BaseModel], raw: Any, code: str = "bad_param", status: int = 422) -> ApiError:
    with pytest.raises(ApiError) as e:
        sc.parse_body(model, raw)
    assert (e.value.status, e.value.code) == (status, code), (e.value.code, e.value.message)
    assert e.value.message and e.value.hint and CYRILLIC.search(e.value.message + e.value.hint)  # Russian + a hint
    return e.value


REQUESTS = DATA["requests"]


def test_request_samples() -> None:
    assert sc.parse_body(sc.PrefsBody, REQUESTS["prefs"]).effort == "high"
    t = sc.parse_body(sc.TurnBody, REQUESTS["turn"])
    assert t.dialog_id is None and t.context.set is not None and t.context.set.holes == 48
    assert [x.name for x in t.tools] == ["ui_hole_select", "ui_project_delete", "ui_section_through_hole"]
    assert sc.parse_body(sc.TurnBody, REQUESTS["turn_continue"]).dialog_id == "42"
    r = sc.parse_body(sc.ToolResultsBody, REQUESTS["tool_results"])
    assert [x.ok for x in r.results] == [True, True] and r.pending_id == "p-7f3a"
    assert sc.parse_body(sc.ToolResultsBody, REQUESTS["tool_results_declined"]).results[0].text == er.TEXT_DECLINED
    assert sc.parse_body(sd.PatchDialogBody, REQUESTS["rename"]).title == "Разрез DH-012"
    assert sc.parse_body(sc.LoginFinishBody, REQUESTS["login_finish"]).code
    assert sc.parse_body(sc.SealedBody, REQUESTS["sealed"]).revision == 3
    ctx = REQUESTS["turn"]["context"]
    assert len(json.dumps(ctx, ensure_ascii=False).encode()) <= sb.CONTEXT_MAX  # the sample is within 4 KB


def turn(**kw: Any) -> dict[str, Any]:
    return {**copy.deepcopy(REQUESTS["turn"]), **kw}


def results(**kw: Any) -> dict[str, Any]:
    return {**copy.deepcopy(REQUESTS["tool_results"]), **kw}


def param(e: ApiError) -> Any:
    return e.extra.get("param")


def test_turn_body_refusals() -> None:
    assert param(refused(sc.TurnBody, turn(text=""))) == "text"
    assert param(refused(sc.TurnBody, turn(text="   "))) == "text"
    assert param(refused(sc.TurnBody, turn(text="я" * (sb.TEXT_MAX + 1)))) == "text"
    assert sc.parse_body(sc.TurnBody, turn(text="я" * sb.TEXT_MAX)).text
    assert param(refused(sc.TurnBody, turn(text=5))) == "text"  # strict: a number is not a text
    assert param(refused(sc.TurnBody, {k: v for k, v in turn().items() if k != "text"})) == "text"
    assert param(refused(sc.TurnBody, turn(dialog_id="abc"))) == "dialog_id"
    assert param(refused(sc.TurnBody, turn(dialog_id=41))) == "dialog_id"
    assert sc.parse_body(sc.TurnBody, turn(dialog_id="t-0b6f2d4e-8c1a-4d3e-9a5b-7c2e1f0a9b88")).dialog_id
    assert param(refused(sc.TurnBody, turn(context={"windows": ["w"] * 21}))) == "context"
    assert param(refused(sc.TurnBody, turn(context={"project": {"uid": "u", "name": "я" * 201}}))) == "context"
    assert param(refused(sc.TurnBody, turn(context={"panels": ["я" * 300] * 20}))) == "context"  # > 4 KB
    assert (
        param(refused(sc.TurnBody, turn(context={"view": {"mode": "x", "perspective": True, "vexag": 1.0}})))
        == "context"
    )
    assert param(refused(sc.TurnBody, turn(context=None))) == "context"
    assert param(refused(sc.TurnBody, [])) == "body" and param(refused(sc.TurnBody, None)) == "body"
    assert sc.parse_body(sc.TurnBody, turn(context={})).context.project is None  # a new window: an empty context
    assert sc.parse_body(sc.TurnBody, turn(unknown=1)).text  # a key the contract does not know is ignored


def test_tools_refusals() -> None:
    def tool(**kw: Any) -> dict[str, Any]:
        return {**copy.deepcopy(REQUESTS["turn"]["tools"][0]), **kw}

    def bad(tools: Any) -> str:
        e = refused(sc.TurnBody, turn(tools=tools), "assistant_bad_tools")
        assert e.extra["detail"].startswith("tools")
        return str(e.extra["detail"])

    assert sc.parse_body(sc.TurnBody, turn(tools=[])).tools == []
    assert "name" in bad([tool(name="hole_select")]) and "name" in bad([tool(name="ui_" + "a" * 61)])
    assert "name" in bad([tool(name="ui_Hole")])
    assert "repeated" in bad([tool(), tool()])
    assert "description" in bad([tool(description="я" * 2001)]) or "string_too_long" in bad(
        [tool(description="я" * 2001)]
    )
    assert bad([{"name": "ui_x", "input_schema": {}}])  # no description
    assert "input_schema" in bad([tool(input_schema={"type": "array"})])
    assert "additionalProperties" in bad([tool(input_schema={"type": "object", "properties": {}, "required": []})])
    assert "type" in bad(
        [
            tool(
                input_schema={
                    "type": "object",
                    "properties": {"a": {"type": "array"}},
                    "required": [],
                    "additionalProperties": False,
                }
            )
        ]
    )
    assert "required" in bad(
        [tool(input_schema={"type": "object", "properties": {}, "required": ["a"], "additionalProperties": False})]
    )
    many = [tool(name=f"ui_t{i}") for i in range(sb.TOOLS_MAX + 1)]
    assert "more than" in bad(many)
    big = [tool(name=f"ui_b{i}", description="я" * 2000) for i in range(80)]  # > 256 KB of JSON
    assert "256" in bad(big)
    ok400 = [tool(name=f"ui_t{i}") for i in range(sb.TOOLS_MAX)]
    assert len(sc.parse_body(sc.TurnBody, turn(tools=ok400)).tools) == 400
    nullable = tool(
        input_schema={
            "type": "object",
            "properties": {"a": {"type": ["number", "null"], "enum": [1, 2]}},
            "required": ["a"],
            "additionalProperties": False,
        }
    )
    assert sc.parse_body(sc.TurnBody, turn(tools=[nullable])).tools[0].input_schema["required"] == ["a"]
    assert "tool" in bad("nope") or bad("nope")  # not a list: still assistant_bad_tools


def test_tool_results_refusals() -> None:
    assert param(refused(sc.ToolResultsBody, results(results=[]))) == "results"
    assert (
        param(refused(sc.ToolResultsBody, results(results=[REQUESTS["tool_results"]["results"][0]] * 2))) == "results"
    )
    assert param(refused(sc.ToolResultsBody, results(pending_id="x1"))) == "pending_id"
    assert (
        param(refused(sc.ToolResultsBody, results(results=[{"tool_use_id": "toolu 1", "ok": True, "text": "x"}])))
        == "results"
    )
    assert (
        param(refused(sc.ToolResultsBody, results(results=[{"tool_use_id": "toolu_1", "ok": "yes", "text": "x"}])))
        == "results"
    )
    assert refused(sc.ToolResultsBody, results(tools=[{"name": "bad"}]), "assistant_bad_tools")
    cut = sc.parse_body(
        sc.ToolResultsBody, results(results=[{"tool_use_id": "toolu_1", "ok": False, "text": "я" * 5000}])
    )
    assert len(cut.results[0].text) == sb.TOOL_TEXT_MAX and cut.results[0].text.endswith("…")  # §9: a cut with «…»
    assert cut.results[0].ok is False and cut.results[0].title is None
    exact = sc.parse_body(
        sc.ToolResultsBody, results(results=[{"tool_use_id": "toolu_1", "ok": True, "text": "я" * 4000}])
    )
    assert len(exact.results[0].text) == 4000 and not exact.results[0].text.endswith("…")


def test_small_bodies() -> None:
    p = sc.parse_body
    assert (p(sc.PrefsBody, {}).model, p(sc.PrefsBody, {}).effort) == (None, None)
    assert param(refused(sc.PrefsBody, {"effort": "ultra"})) == "effort"
    assert param(refused(sc.PrefsBody, {"model": ""})) == "model"
    assert param(refused(sc.PrefsBody, {"model": 5})) == "model"
    assert p(sc.PrefsBody, {"model": "claude-haiku-5-5", "effort": "max"}).effort == "max"
    assert param(refused(sd.PatchDialogBody, {"title": ""})) == "title"
    assert param(refused(sd.PatchDialogBody, {"title": "   "})) == "title"
    assert param(refused(sd.PatchDialogBody, {"title": "я" * 201})) == "title"
    assert (
        p(sd.PatchDialogBody, {"title": "  Разрез  "}).title == "Разрез"
        and len(p(sd.PatchDialogBody, {"title": "я" * 200}).title) == 200
    )
    for bad_code in ("", "a\nb", "a\rb", "a\0b", "я" * 4097, "   "):
        assert param(refused(sc.LoginFinishBody, {"code": bad_code})) == "code"
    assert p(sc.LoginFinishBody, {"code": "я" * 4096}).code
    assert param(refused(sc.SealedBody, {"revision": -1})) == "revision"
    assert param(refused(sc.SealedBody, {"revision": "3"})) == "revision"


def test_key_format() -> None:
    assert sc.parse_body(sc.KeyBody, {"key": f"  {TEST_KEY}\n"}).key == TEST_KEY  # the pasted spaces go
    for bad_key in (
        "",
        "hello",
        "sk-ant-short",
        "sk-ant-" + "a" * 19,
        "sk-ant-" + "a" * 20 + " b",
        "SK-ANT-" + "a" * 30,
        "sk-ant-" + "я" * 30,
        "sk-ant-" + "a" * 600,
    ):
        e = refused(sc.KeyBody, {"key": bad_key}, "assistant_bad_key_format")
        if len(bad_key) > 12:
            assert bad_key not in e.message + e.hint + json.dumps(e.extra)  # the value is never repeated
    assert param(refused(sc.KeyBody, {"key": 5})) == "key"  # not a string: bad_param
    assert param(refused(sc.KeyBody, {})) == "key"
    assert sc.parse_body(sc.KeyBody, {"key": "sk-ant-api03-" + "aB_-" * 8}).key


def test_secret_bodies() -> None:
    key = {"kind": "api_key", "secret": {"key": TEST_KEY}, "secure_storage": True}
    assert sc.parse_body(sc.SecretPutBody, key).secret == {"key": TEST_KEY}
    acc_secret = {
        "access_token": "at",
        "expires_at": "2026-11-26T11:00:00Z",
        "refresh_token": "rt",
        "client_id": "cid",
        "organization_id": "org_1",
        "workspace_id": None,
    }
    acc = sc.parse_body(sc.SecretPutBody, {"kind": "account", "secret": acc_secret, "secure_storage": False})
    assert acc.secret["access_token"] == "at" and "workspace_id" not in acc.secret and acc.secure_storage is False
    for raw in (
        {**key, "kind": "password"},
        {**key, "secret": {"key": "hello"}},
        {**key, "secret": {}},
        {"kind": "account", "secret": {"access_token": "at"}, "secure_storage": True},
        {"kind": "account", "secret": {**acc_secret, "expires_at": "soon"}, "secure_storage": True},
        {**key, "secure_storage": "yes"},
    ):
        e = refused(sc.SecretPutBody, raw)
        assert param(e) in ("kind", "secret", "secure_storage")
        text = e.message + e.hint + json.dumps(e.extra, ensure_ascii=False)
        assert TEST_KEY not in text  # the value of a secret is never in an error
    roundtrip(sc.SecretPutAnswer, {"ok": True, "kind": "api_key", "mask": "sk-ant-…0000"})
    roundtrip(sc.SecretGetAnswer, {"kind": "api_key", "secret": {"key": "x"}, "revision": 2})
    roundtrip(
        sc.ConnectionTest,
        {
            "ok": True,
            "latency_ms": 412,
            "model": "claude-opus-5-5",
            "model_available": True,
            "effort_supported": ["low", "medium"],
            "organization_id": None,
            "message": "Связь есть.",
        },
    )
    roundtrip(
        sc.LoginStart,
        {"login_id": "l-1", "url": "https://console.anthropic.com/oauth", "expires_at": "2026-11-26T10:10:00Z"},
    )
