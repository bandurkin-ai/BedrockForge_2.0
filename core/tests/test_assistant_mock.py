# ruff: noqa: F811 - fixtures imported from tests.assistant.conftest are used as parameters
"""The Anthropic mock and the fake `ant` (stage E23, 23.2): the real SDK (anthropic 2.6.0) against the mock.

Nothing here goes to the internet: the clients have an explicit `base_url` on 127.0.0.1 and explicit credentials.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.lib.credentials import CredentialsFile
from anthropic.lib.credentials._workload import WorkloadIdentityError

from tests.assistant.conftest import (  # noqa: F401 - fixtures of the assistant tests
    TEST_API_KEY,
    ant_config_dir,
    anthropic_client,
    clean_anthropic_env,
    closed_port_url,
    fake_ant_path,
    make_client,
    mock_anthropic,
)
from tests.assistant.mock_anthropic import TEST_CLIENT_ID, TEST_REFRESH_TOKEN, MockAnthropic
from tests.assistant.mock_scenario import SCENARIO_DIR

MODEL = "claude-opus-5-5"
CORE = Path(__file__).resolve().parents[1]


def ask(client: anthropic.Anthropic, text: str, **kw: Any) -> tuple[list[str], anthropic.types.Message]:
    """One streamed call: the pieces of text and the final message."""
    with client.messages.stream(model=MODEL, max_tokens=1000, messages=[{"role": "user", "content": text}], **kw) as s:
        pieces = list(s.text_stream)
        return pieces, s.get_final_message()


def continue_with(client: anthropic.Anthropic, history: list[dict[str, Any]], results: list[dict[str, Any]]) -> Any:
    """The next call of a tool loop: the results of the tools in one user message."""
    messages = [*history, {"role": "user", "content": results}]
    with client.messages.stream(model=MODEL, max_tokens=1000, messages=messages) as s:
        return s.get_final_message()


def http(base_url: str, method: str, path: str, body: Any = None) -> Any:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        base_url + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - 127.0.0.1
        raw = r.read()
        return json.loads(raw) if raw else None


# ---- text, tool_use, usage, stop_reason ----
def test_stream_text_usage_and_journal(mock_anthropic: MockAnthropic, anthropic_client: anthropic.Anthropic) -> None:
    mock_anthropic.use_scenario("summary")
    pieces, msg = ask(anthropic_client, "Сводка по проекту")
    assert len(pieces) > 3, "the text comes in pieces"
    assert "".join(pieces).startswith("В проекте «Демо» 48 скважин")
    assert msg.stop_reason == "end_turn" and msg.model == MODEL
    u = msg.usage
    assert (u.input_tokens, u.output_tokens, u.cache_creation_input_tokens, u.cache_read_input_tokens) == (
        2400,
        85,
        1800,
        0,
    )
    (req,) = mock_anthropic.requests
    assert req["path"] == "/v1/messages" and req["stream"] is True and req["matched"] is True and req["status"] == 200
    assert req["auth"] == {"kind": "api_key", "ok": True}
    assert req["body"]["model"] == MODEL and req["body"]["messages"][0]["content"] == "Сводка по проекту"
    assert "x-api-key" not in req["headers"] and "authorization" not in req["headers"]
    assert TEST_API_KEY not in json.dumps(mock_anthropic.requests)


def test_tool_use_blocks_and_the_loop(mock_anthropic: MockAnthropic, anthropic_client: anthropic.Anthropic) -> None:
    mock_anthropic.use_scenario("hole_section")
    text = "Покажи скважину DH-012 на разрезе"
    pieces, msg = ask(anthropic_client, text)
    assert "".join(pieces) == "Выбираю скважину и строю разрез."
    assert msg.stop_reason == "tool_use"
    assert [b.type for b in msg.content] == ["thinking", "text", "tool_use", "tool_use"]
    assert msg.content[0].signature == "c2lnLW1vY2s="
    calls = [b for b in msg.content if b.type == "tool_use"]
    assert [(c.id, c.name, c.input) for c in calls] == [
        ("toolu_mock_select", "ui_hole_select", {"hole_id": "DH-012"}),
        ("toolu_mock_section", "ui_section_through_hole", {"hole_id": "DH-012"}),
    ]
    history = [
        {"role": "user", "content": text},
        {"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in msg.content]},
    ]
    ok = [{"type": "tool_result", "tool_use_id": c.id, "content": "Готово"} for c in calls]
    done = continue_with(anthropic_client, history, ok)
    assert done.stop_reason == "end_turn" and done.content[0].text.startswith("Готово: скважина DH-012")
    bad = [{**ok[0]}, {**ok[1], "is_error": True, "content": "Нет разреза"}]
    assert continue_with(anthropic_client, history, bad).content[0].text.startswith("Не получилось")
    third = mock_anthropic.requests[1]
    assert third["body"]["messages"][-1]["content"][0]["type"] == "tool_result"
    assert third["step"] == 1, "a continuation is matched by the tool result, not by the earlier question"


def test_two_data_rounds_then_text(mock_anthropic: MockAnthropic, anthropic_client: anthropic.Anthropic) -> None:
    mock_anthropic.use_scenario("data_tools")
    history: list[dict[str, Any]] = [{"role": "user", "content": "Что не так с данными проекта?"}]
    names = []
    for _ in range(3):
        with anthropic_client.messages.stream(model=MODEL, max_tokens=1000, messages=history) as s:
            msg = s.get_final_message()
        history.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in msg.content]})
        calls = [b for b in msg.content if b.type == "tool_use"]
        if not calls:
            break
        names.append(calls[0].name)
        results = [{"type": "tool_result", "tool_use_id": c.id, "content": "{}"} for c in calls]
        history.append({"role": "user", "content": results})
    assert names == ["data_summary", "data_audit"]
    assert msg.stop_reason == "end_turn" and "DH-012" in msg.content[0].text
    assert [r["step"] for r in mock_anthropic.requests] == [2, 1, 0]


def test_plain_answer_and_extra_parameters_are_journaled(
    mock_anthropic: MockAnthropic, anthropic_client: anthropic.Anthropic
) -> None:
    mock_anthropic.use_scenario("summary")
    msg = anthropic_client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=[{"type": "text", "text": "Ты ассистент", "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "Сводка по проекту"}],
        thinking={"type": "adaptive", "display": "omitted"},
        output_config={"effort": "low"},
        cache_control={"type": "ephemeral"},
        tools=[{"name": "ui_hole_select", "description": "d", "input_schema": {"type": "object", "properties": {}}}],
    )
    assert msg.stop_reason == "end_turn" and msg.usage.output_tokens == 85
    (req,) = mock_anthropic.requests
    assert req["stream"] is False
    body = req["body"]
    assert body["output_config"] == {"effort": "low"} and body["cache_control"] == {"type": "ephemeral"}
    assert body["thinking"] == {"type": "adaptive", "display": "omitted"}
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"} and body["tools"][0]["name"] == "ui_hole_select"


def test_a_request_no_step_fits_is_visible(
    mock_anthropic: MockAnthropic, anthropic_client: anthropic.Anthropic
) -> None:
    mock_anthropic.use_scenario("summary")
    pieces, _ = ask(anthropic_client, "Совсем другой вопрос")
    assert "".join(pieces) == "No scenario step fits this request."
    assert mock_anthropic.requests[0]["matched"] is False and mock_anthropic.requests[0]["step"] is None


def test_workspace_block_and_continuations_are_not_questions(
    mock_anthropic: MockAnthropic, anthropic_client: anthropic.Anthropic
) -> None:
    step = {"match": {"last_user_text": "DH-012"}, "reply": {"content": [{"type": "text", "text": "нашёл"}]}}
    mock_anthropic.use_scenario({"steps": [step]})
    ctx = '<workspace>{"hole": "DH-012"}</workspace>\n'
    assert "".join(ask(anthropic_client, ctx + "Привет")[0]) == "No scenario step fits this request."
    pieces, _ = ask(anthropic_client, ctx + "Скважина DH-012")
    assert "".join(pieces) == "нашёл"
    assert [r["matched"] for r in mock_anthropic.requests] == [False, True]


# ---- errors and retries ----
def test_overloaded_twice_then_ok_after_sdk_retries(
    mock_anthropic: MockAnthropic, anthropic_client: anthropic.Anthropic
) -> None:
    mock_anthropic.use_scenario("overloaded_then_ok")
    pieces, msg = ask(anthropic_client, "привет")
    assert "".join(pieces) == "Ответ после повторов." and msg.stop_reason == "end_turn"
    assert [r["status"] for r in mock_anthropic.requests] == [529, 529, 200]


def test_overloaded_after_the_retries_is_an_error(mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("overloaded_then_ok")
    with make_client(mock_anthropic, max_retries=1) as client, pytest.raises(anthropic.OverloadedError) as e:
        ask(client, "привет")
    assert e.value.status_code == 529
    assert len(mock_anthropic.requests) == 2


def test_rate_limit_fault_and_its_removal(mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("summary")
    mock_anthropic.fault({"status": 429, "message": "slow down"})
    with make_client(mock_anthropic, max_retries=1) as client:
        with pytest.raises(anthropic.RateLimitError) as e:
            ask(client, "Сводка по проекту")
        assert "slow down" in str(e.value) and len(mock_anthropic.requests) == 2
        assert all(r["fault"] for r in mock_anthropic.requests)
        mock_anthropic.clear_fault()
        assert ask(client, "Сводка по проекту")[1].stop_reason == "end_turn"


def test_unauthorized_is_not_retried(mock_anthropic: MockAnthropic, anthropic_client: anthropic.Anthropic) -> None:
    mock_anthropic.use_scenario("unauthorized")
    with pytest.raises(anthropic.AuthenticationError) as e:
        ask(anthropic_client, "привет")
    assert e.value.status_code == 401 and len(mock_anthropic.requests) == 1


def test_a_wrong_key_is_refused_by_the_mock_itself(mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.use_scenario("summary")
    with make_client(mock_anthropic, api_key="sk-ant-wrong-0000000000000000000") as client:
        with pytest.raises(anthropic.AuthenticationError):
            ask(client, "Сводка по проекту")
        with pytest.raises(anthropic.AuthenticationError):
            client.models.retrieve(MODEL)
    assert [r["auth"]["ok"] for r in mock_anthropic.requests] == [False, False]


def test_models_retrieve_capabilities_org_and_unavailable(mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.unavailable_models.add("claude-gone-1")
    with make_client(mock_anthropic) as client:
        raw = client.models.with_raw_response.retrieve(MODEL)
        info = raw.parse()
        assert info.id == MODEL and info.capabilities is not None
        assert info.capabilities.effort.supported and info.capabilities.effort.xhigh is not None
        assert info.capabilities.effort.xhigh.supported
        assert raw.headers["anthropic-organization-id"] == mock_anthropic.org_id
        with pytest.raises(anthropic.NotFoundError):
            client.models.retrieve("claude-gone-1")


def test_closed_port_is_a_connection_error(closed_port_url: str, clean_anthropic_env: None) -> None:
    with make_client(closed_port_url, max_retries=0) as client:
        with pytest.raises(anthropic.APIConnectionError):
            client.models.retrieve(MODEL)
        with pytest.raises(anthropic.APIConnectionError):
            ask(client, "привет")


def test_a_stream_dropped_after_three_events(
    mock_anthropic: MockAnthropic, anthropic_client: anthropic.Anthropic
) -> None:
    mock_anthropic.use_scenario("drop")
    got: list[str] = []
    with pytest.raises(httpx2.RemoteProtocolError):  # not an APIStatusError: the SDK passes the HTTP client error on
        with anthropic_client.messages.stream(
            model=MODEL, max_tokens=500, messages=[{"role": "user", "content": "Расскажи подробно про бурение"}]
        ) as s:
            for piece in s.text_stream:
                got.append(piece)
    assert got == ["Бурение ведё"], "message_start, block start and one piece arrived, then the connection was lost"
    (req,) = mock_anthropic.requests
    assert req["dropped"] is True and len(mock_anthropic.requests) == 1, "no retry after the first event"


def test_delay_between_events(mock_anthropic: MockAnthropic, anthropic_client: anthropic.Anthropic) -> None:
    reply = {"stream": True, "delay_ms": 40, "content": [{"type": "text", "text": "один два три"}], "chunk": 4}
    mock_anthropic.use_scenario({"steps": [{"match": {}, "reply": reply}]})
    t = time.monotonic()
    pieces, _ = ask(anthropic_client, "q")
    assert pieces == ["один", " два", " три"] and time.monotonic() - t >= 0.25


# ---- oauth: the refresh_token grant ----
def login(fake_ant_path: Path, config_dir: Path, code: str, **env: str) -> subprocess.CompletedProcess[str]:
    run_env = {**os.environ, "ANTHROPIC_CONFIG_DIR": str(config_dir), **env}
    cmd = [str(fake_ant_path), "auth", "login", "--no-browser", "--profile", "bedrockforge"]
    return subprocess.run(cmd, input=code + "\n", capture_output=True, text=True, env=run_env, timeout=30)


def test_refresh_token_exchange_with_a_profile_of_the_fake_ant(
    mock_anthropic: MockAnthropic, fake_ant_path: Path, ant_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    done = login(
        fake_ant_path, ant_config_dir, "TEST-CODE-123456", FAKE_ANT_ACCESS_TOKEN="sk-ant-oat01-test-expired-0000000"
    )
    assert done.returncode == 0, done.stderr
    creds_file = ant_config_dir / "credentials" / "bedrockforge.json"
    creds = json.loads(creds_file.read_text())
    creds["expires_at"] = 1  # the access token has expired
    creds_file.write_text(json.dumps(creds))
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(ant_config_dir))
    provider = CredentialsFile("bedrockforge")
    with anthropic.Anthropic(credentials=provider, base_url=mock_anthropic.base_url, max_retries=0) as client:
        assert client.models.retrieve(MODEL).id == MODEL
        mock_anthropic.use_scenario("summary")
        assert ask(client, "Сводка по проекту")[1].stop_reason == "end_turn"
    grant, models, messages = mock_anthropic.requests
    assert (grant["path"], grant["status"], grant["refresh_token_ok"]) == ("/v1/oauth/token", 200, True)
    assert grant["body"] == {"grant_type": "refresh_token", "refresh_token": "<redacted>", "client_id": TEST_CLIENT_ID}
    assert "oauth-2025-04-20" in grant["headers"]["anthropic-beta"]
    assert models["auth"] == {"kind": "bearer", "ok": True} and messages["auth"] == {"kind": "bearer", "ok": True}
    assert "oauth-2025-04-20" in models["headers"]["anthropic-beta"]
    assert len(mock_anthropic.requests) == 3, "one exchange serves the later calls"
    rotated = json.loads(creds_file.read_text())
    assert rotated["refresh_token"] != TEST_REFRESH_TOKEN and rotated["expires_at"] > time.time() + 3000
    assert (
        rotated["refresh_token"] in mock_anthropic.refresh_tokens
        and TEST_REFRESH_TOKEN not in mock_anthropic.refresh_tokens
    )


def test_a_used_refresh_token_is_refused(
    mock_anthropic: MockAnthropic, fake_ant_path: Path, ant_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        login(
            fake_ant_path, ant_config_dir, "TEST-CODE-123456", FAKE_ANT_REFRESH_TOKEN="sk-ant-ort01-unknown-0000000"
        ).returncode
        == 0
    )
    creds_file = ant_config_dir / "credentials" / "bedrockforge.json"
    creds = json.loads(creds_file.read_text())
    creds["expires_at"] = 1
    creds_file.write_text(json.dumps(creds))
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(ant_config_dir))
    with anthropic.Anthropic(
        credentials=CredentialsFile("bedrockforge"), base_url=mock_anthropic.base_url, max_retries=0
    ) as client:
        with pytest.raises(WorkloadIdentityError) as e:
            client.models.retrieve(MODEL)
    assert e.value.status_code == 400 and "invalid_grant" in str(e.value)
    (grant,) = mock_anthropic.requests
    assert grant["refresh_token_ok"] is False
    assert "sk-ant-ort01-unknown-0000000" not in json.dumps(mock_anthropic.requests)


def test_a_bearer_token_without_the_beta_header_is_refused(mock_anthropic: MockAnthropic) -> None:
    req = urllib.request.Request(
        mock_anthropic.base_url + "/v1/models/" + MODEL,
        headers={"Authorization": "Bearer sk-ant-oat01-test-access-000000000000"},
    )
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)  # noqa: S310 - 127.0.0.1
    assert e.value.code == 401


# ---- fake ant ----
def test_fake_ant_right_code_writes_the_profile_the_sdk_reads(fake_ant_path: Path, ant_config_dir: Path) -> None:
    done = login(
        fake_ant_path,
        ant_config_dir,
        "TEST-CODE-123456",
        FAKE_ANT_ORG_ID="org_x1",
        FAKE_ANT_BASE_URL="http://127.0.0.1:9",
    )
    assert done.returncode == 0 and done.stdout.lstrip().split("\n")[0].startswith("Open this address")
    url = next(line for line in done.stdout.splitlines() if line.startswith("https://"))
    assert url.startswith("https://console.anthropic.com/oauth/authorize?")
    config = json.loads((ant_config_dir / "configs" / "bedrockforge.json").read_text())
    assert config["authentication"] == {"type": "user_oauth", "client_id": TEST_CLIENT_ID}
    assert (config["organization_id"], config["base_url"]) == ("org_x1", "http://127.0.0.1:9")
    creds_path = ant_config_dir / "credentials" / "bedrockforge.json"
    creds = json.loads(creds_path.read_text())
    assert creds["type"] == "oauth_token" and creds["access_token"].startswith("sk-ant-oat01-")
    assert creds["refresh_token"] == TEST_REFRESH_TOKEN and creds["expires_at"] > time.time() + 3000
    if sys.platform != "win32":
        assert creds_path.stat().st_mode & 0o077 == 0, "the SDK refuses a credentials file open to the group"


def test_fake_ant_wrong_code_and_failures(fake_ant_path: Path, ant_config_dir: Path) -> None:
    bad = login(fake_ant_path, ant_config_dir, "WRONG")
    assert bad.returncode == 1 and "invalid authorization code" in bad.stderr
    assert not any(ant_config_dir.iterdir()), "a wrong code writes nothing"
    early = login(fake_ant_path, ant_config_dir, "TEST-CODE-123456", FAKE_ANT_MODE="exit_early")
    assert early.returncode == 3 and "https://" not in early.stdout
    custom = login(fake_ant_path, ant_config_dir, "my-code", FAKE_ANT_CODE="my-code")
    assert custom.returncode == 0
    no_dir = subprocess.run(
        [str(fake_ant_path), "auth", "login", "--no-browser"],
        input="x\n",
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "ANTHROPIC_CONFIG_DIR"},
        timeout=30,
    )
    assert no_dir.returncode == 1 and "ANTHROPIC_CONFIG_DIR" in no_dir.stderr
    other = subprocess.run([str(fake_ant_path), "models", "list"], capture_output=True, text=True, timeout=30)
    assert other.returncode == 2


def test_fake_ant_hang_prints_the_address_and_waits_for_a_kill(fake_ant_path: Path, ant_config_dir: Path) -> None:
    env = {**os.environ, "ANTHROPIC_CONFIG_DIR": str(ant_config_dir), "FAKE_ANT_MODE": "hang"}
    p = subprocess.Popen(
        [str(fake_ant_path), "auth", "login", "--no-browser"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert p.stdout is not None
        lines = [p.stdout.readline() for _ in range(3)]
        assert any(line.startswith("https://") for line in lines)
        assert p.poll() is None
    finally:
        p.kill()
        p.wait(10)


# ---- the mock as a process and its control routes ----
def test_the_mock_as_a_process_with_a_port_file(tmp_path: Path, clean_anthropic_env: None) -> None:
    port_file = tmp_path / "port.txt"
    cmd = [
        sys.executable,
        "-m",
        "tests.assistant.mock_anthropic",
        "--scenario",
        "summary",
        "--scenario",
        "drop",
        "--port-file",
        str(port_file),
    ]
    p = subprocess.Popen(cmd, cwd=CORE, stdout=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            if port_file.exists():
                break
            time.sleep(0.1)
        base = f"http://127.0.0.1:{int(port_file.read_text())}"
        with make_client(base) as client:
            pieces, msg = ask(client, "Сводка по проекту")
            assert "".join(pieces).startswith("В проекте") and msg.usage.output_tokens == 85
        assert http(base, "GET", "/__requests")["requests"][0]["body"]["model"] == MODEL
        http(base, "DELETE", "/__requests")
        assert http(base, "GET", "/__requests") == {"requests": []}
        http(base, "POST", "/__scenario", {"name": "unauthorized"})
        with make_client(base, max_retries=0) as client, pytest.raises(anthropic.AuthenticationError):
            ask(client, "привет")
        http(
            base,
            "POST",
            "/__scenario",
            {"steps": [{"match": {}, "reply": {"content": [{"type": "text", "text": "из шагов"}]}}]},
        )
        http(base, "POST", "/__fault", {"reply": {"status": 529}, "times": 1})
        with make_client(base, max_retries=0) as client:
            with pytest.raises(anthropic.OverloadedError):
                ask(client, "привет")
            assert "".join(ask(client, "привет")[0]) == "из шагов", "the fault was used up"
        http(base, "POST", "/__reset")
        assert http(base, "GET", "/__requests") == {"requests": []}
        with pytest.raises(urllib.error.HTTPError) as e:
            http(base, "POST", "/__scenario", {"name": "no-such-scenario"})
        assert e.value.code == 400
    finally:
        p.terminate()
        assert p.wait(15) == 0
        assert p.stdout is not None
        p.stdout.close()


def test_every_scenario_file_is_valid_and_playable() -> None:
    files = sorted(SCENARIO_DIR.glob("*.json"))
    assert {f.stem for f in files} >= {
        "summary",
        "data_tools",
        "hole_section",
        "project_delete",
        "drop",
        "unauthorized",
    }
    for f in files:
        steps = json.loads(f.read_text(encoding="utf-8"))["steps"]
        assert steps and all("match" in s and "reply" in s for s in steps), f.name
        for s in steps:
            blocks = s["reply"].get("content") or []
            assert s["reply"].get("status") or blocks, f.name
            assert all(b["type"] in ("text", "tool_use", "thinking") for b in blocks), f.name
