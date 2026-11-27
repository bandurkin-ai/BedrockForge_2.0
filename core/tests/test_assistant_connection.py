# ruff: noqa: F811 - fixtures imported from tests.assistant.conftest are used as parameters
"""Э23 23.4: connection of the assistant (ADR 0029 решения 6, 8-12; ТЗ 7.32 «Хранение подключения»; e23-api §2-§3, §12).

Everything goes to the mock of the Anthropic API on 127.0.0.1 (23.2); nothing goes to the internet.
"""

from __future__ import annotations

import inspect
import json
import logging
import pickle
import time
from pathlib import Path
from typing import Any

import anthropic
import pytest
from anthropic.lib.credentials import AccessToken, AccessTokenProvider

from bf_core.api.errors import ApiError
from bf_core.assistant import connection as conn_mod
from bf_core.assistant import oauth
from bf_core.assistant.connection import Connection, mask_key, resolve_base_url, scrub
from bf_core.assistant.oauth import MemoryOAuth, OAuthRefreshError
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
from tests.assistant.fake_ant import write_profile
from tests.assistant.mock_anthropic import (
    OAUTH_BETA,
    TEST_ACCESS_TOKEN,
    TEST_CLIENT_ID,
    TEST_ORG_ID,
    TEST_REFRESH_TOKEN,
    MockAnthropic,
)

WRONG_KEY = "sk-ant-api03-" + "A1b2C3d4" * 5
OTHER_KEY = "sk-ant-api03-" + "Z9y8X7w6" * 5
MODEL = "claude-opus-5-5"
ASK: dict[str, Any] = {"model": MODEL, "max_tokens": 16, "messages": [{"role": "user", "content": "Привет"}]}


@pytest.fixture()
def conn(tmp_path: Path, mock_anthropic: MockAnthropic) -> Connection:
    return Connection(tmp_path / "data", env={"BF_ASSISTANT_BASE_URL": mock_anthropic.base_url})


def _env(mock: MockAnthropic, **extra: str) -> dict[str, str]:
    return {"BF_ASSISTANT_BASE_URL": mock.base_url, **extra}


def _refused(call: Any, *args: Any, **kwargs: Any) -> ApiError:
    with pytest.raises(ApiError) as e:
        call(*args, **kwargs)
    return e.value  # type: ignore[no-any-return]


def _text_of(err: ApiError) -> str:
    return json.dumps([err.status, err.code, err.message, err.hint, err.extra], ensure_ascii=False)


def _account(mock: MockAnthropic, c: Connection, **over: Any) -> Any:
    args: dict[str, Any] = {
        "access_token": TEST_ACCESS_TOKEN,
        "refresh_token": TEST_REFRESH_TOKEN,
        "client_id": TEST_CLIENT_ID,
        "expires_at": int(time.time()) + 3600,
        "organization_id": "org_x1",
        "workspace_id": "wrkspc_x1",
    }
    return c.set_account(**{**args, **over})


# ---- the key from the window ----


def test_key_is_checked_then_kept_in_memory(conn: Connection, mock_anthropic: MockAnthropic) -> None:
    assert conn.snapshot().state == "none" and not conn.is_connected()
    snap = conn.set_key(f"  {TEST_API_KEY}\r\n")  # strip at the edges
    assert (snap.state, snap.kind, snap.mask, snap.seal_needed, snap.online) == (
        "connected",
        "api_key",
        "sk-ant-…0000",
        True,
        True,
    )
    assert snap.organization == {"id": TEST_ORG_ID, "name": None} and snap.workspace is None
    assert snap.revision == 1 and snap.checked_at is not None
    (req,) = mock_anthropic.requests
    assert (req["path"], req["auth"]) == (f"/v1/models/{MODEL}", {"kind": "api_key", "ok": True})
    assert conn.get_secret() == ("api_key", {"key": TEST_API_KEY}, 1)


@pytest.mark.parametrize(
    "bad", ["", "sk-ant-short", "sk-ant-" + "a" * 20 + " b", "xx-ant-" + "a" * 30, "sk-ant-" + "ы" * 30]
)
def test_key_format_is_checked_before_any_call(conn: Connection, mock_anthropic: MockAnthropic, bad: str) -> None:
    err = _refused(conn.set_key, bad)
    assert (err.status, err.code) == (422, "assistant_bad_key_format")
    assert mock_anthropic.requests == [] and conn.snapshot().state == "none"
    assert bad.strip() == "" or bad.strip() not in _text_of(err)


def test_a_wrong_key_is_not_accepted_and_the_state_stays(conn: Connection, mock_anthropic: MockAnthropic) -> None:
    err = _refused(conn.set_key, WRONG_KEY)  # the mock answers 401
    assert (err.status, err.code) == (422, "assistant_bad_key")
    assert err.message and err.hint and WRONG_KEY not in _text_of(err)
    snap = conn.snapshot()
    assert (snap.state, snap.kind, snap.mask, snap.seal_needed, snap.revision) == ("none", None, None, False, 0)
    assert conn.get_secret() is None and not conn.is_connected()
    assert mock_anthropic.requests[0]["status"] == 401 and len(mock_anthropic.requests) == 1, "401 is not retried"


def test_a_wrong_key_leaves_the_previous_connection_as_it_was(conn: Connection, mock_anthropic: MockAnthropic) -> None:
    before = conn.set_key(TEST_API_KEY)
    err = _refused(conn.set_key, WRONG_KEY)
    assert err.code == "assistant_bad_key"
    assert conn.snapshot() == before
    assert conn.get_secret() == ("api_key", {"key": TEST_API_KEY}, before.revision)
    assert conn.client().api_key == TEST_API_KEY


def test_no_network_means_offline_and_the_key_is_not_accepted(tmp_path: Path, closed_port_url: str) -> None:
    c = Connection(tmp_path, env={"BF_ASSISTANT_BASE_URL": closed_port_url})
    err = _refused(c.set_key, TEST_API_KEY)
    assert (err.status, err.code) == (503, "assistant_offline")
    assert c.snapshot().state == "none" and c.get_secret() is None


def test_a_model_not_open_to_the_organization_does_not_reject_the_key(
    tmp_path: Path, clean_anthropic_env: None
) -> None:
    with MockAnthropic(unavailable_models={MODEL}) as mock:
        c = Connection(tmp_path, env=_env(mock))
        snap = c.set_key(TEST_API_KEY)  # 404 of the model: the key itself works
        assert snap.state == "connected" and snap.organization == {"id": TEST_ORG_ID, "name": None}
        t = c.test()
        assert (t["ok"], t["model_available"]) == (True, False) and "недоступна" in t["message"]


def test_the_key_is_checked_for_the_chosen_model(
    conn: Connection, mock_anthropic: MockAnthropic, tmp_path: Path
) -> None:
    (tmp_path / "data").mkdir(exist_ok=True)
    conn.set_key(TEST_API_KEY, model="claude-sonnet-5-5")
    assert mock_anthropic.requests[0]["path"] == "/v1/models/claude-sonnet-5-5"


# ---- ANTHROPIC_API_KEY ----


def test_env_key_is_used_only_while_the_window_is_empty(tmp_path: Path, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.api_keys.add(OTHER_KEY)
    c = Connection(tmp_path, env=_env(mock_anthropic, ANTHROPIC_API_KEY=f" {TEST_API_KEY} "))
    snap = c.snapshot()
    assert (snap.state, snap.kind, snap.mask, snap.seal_needed) == ("connected", "env", "sk-ant-…0000", False)
    assert c.get_secret() is None, "nothing of the environment is sealed"
    assert c.client().api_key == TEST_API_KEY
    c.set_key(OTHER_KEY)  # the window wins over the environment
    assert c.snapshot().kind == "api_key" and c.client().api_key == OTHER_KEY
    after = c.forget()  # the secret of the window goes, the environment stays in force
    assert (after.state, after.kind, after.seal_needed) == ("connected", "env", False)
    assert c.client().api_key == TEST_API_KEY


def test_no_env_key_and_no_window_key_is_not_connected(conn: Connection) -> None:
    assert _refused(conn.client).code == "assistant_not_connected"
    assert _refused(conn.test).code == "assistant_not_connected"
    assert Connection(env={"ANTHROPIC_API_KEY": "   "}).snapshot().state == "none"


def test_the_sdk_chain_is_not_used(
    tmp_path: Path,
    mock_anthropic: MockAnthropic,
    monkeypatch: pytest.MonkeyPatch,
    ant_config_dir: Path,
    closed_port_url: str,
) -> None:
    """A profile in the config folder (on Windows `%APPDATA%\\Anthropic`), ANTHROPIC_PROFILE, ANTHROPIC_AUTH_TOKEN and
    ANTHROPIC_BASE_URL of the machine are ignored: the client has its own credentials and address."""
    monkeypatch.setenv("FAKE_ANT_BASE_URL", closed_port_url)
    monkeypatch.setenv("FAKE_ANT_ACCESS_TOKEN", "sk-ant-oat01-planted-profile-0000")
    write_profile(ant_config_dir, "default")
    (ant_config_dir / "active_config").write_text("default", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(ant_config_dir))
    monkeypatch.setenv("ANTHROPIC_PROFILE", "default")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-ant-oat01-env-token-0000000")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", closed_port_url)
    c = Connection(tmp_path, env=_env(mock_anthropic))
    assert c.snapshot().state == "none", "a profile on disk is not a connection"
    assert _refused(c.client).code == "assistant_not_connected"
    c.set_key(TEST_API_KEY)
    client = c.client()
    assert client.credentials is None and client.auth_token is None and client.api_key == TEST_API_KEY
    assert str(client.base_url).rstrip("/") == mock_anthropic.base_url
    assert client.models.retrieve(MODEL).id == MODEL
    assert {r["auth"]["kind"] for r in mock_anthropic.requests} == {"api_key"}
    assert all(r["auth"]["ok"] for r in mock_anthropic.requests)


def test_the_sdk_sends_the_key_as_a_bearer_token(conn: Connection) -> None:
    conn.set_key(TEST_API_KEY)
    client = conn.client()
    assert client.auth_headers == {"Authorization": f"Bearer {TEST_API_KEY}"}
    assert "x-api-key" not in {k.lower() for k in client.default_headers}


# ---- address, time-outs, retries ----


def test_base_url_is_anthropic_unless_the_loopback_is_given(caplog: pytest.LogCaptureFixture) -> None:
    assert resolve_base_url({}) == "https://api.anthropic.com" == anthropic_default()
    assert resolve_base_url({"BF_ASSISTANT_BASE_URL": "http://127.0.0.1:8123/"}) == "http://127.0.0.1:8123"
    assert resolve_base_url({"BF_ASSISTANT_BASE_URL": "http://127.0.0.1"}) == "http://127.0.0.1"
    with caplog.at_level(logging.WARNING, logger="bf_core.assistant"):
        for bad in (
            "https://evil.example.com",
            "http://localhost:8123",
            "http://127.0.0.1.evil.com",
            "ftp://127.0.0.1:1",
            "http://user:pw@127.0.0.1:8123",
            "http://[::1]:8123",
            "http://127.0.0.1:notaport",
        ):
            assert resolve_base_url({"BF_ASSISTANT_BASE_URL": bad}) == "https://api.anthropic.com", bad
    assert "pw@" not in caplog.text and "evil.example.com" not in caplog.text, "the address is not logged"


def anthropic_default() -> str:
    from anthropic.lib.credentials._constants import DEFAULT_BASE_URL

    return DEFAULT_BASE_URL


def test_default_client_goes_to_anthropic_with_the_timeouts_and_retries_of_the_adr(tmp_path: Path) -> None:
    c = Connection(tmp_path, env={"ANTHROPIC_API_KEY": TEST_API_KEY})  # no BF_ASSISTANT_BASE_URL
    client = c.client()
    assert str(client.base_url).rstrip("/") == "https://api.anthropic.com"
    t = client.timeout
    assert (t.connect, t.read, t.write) == (5.0, 90.0, 30.0)
    assert client.max_retries == 2 == conn_mod.MAX_RETRIES
    assert c.client() is client, "one client (one pool) while the connection is the same"
    other = c.client(timeout=anthropic.Timeout(3.0), max_retries=0)
    assert other.max_retries == 0 and other.api_key == TEST_API_KEY and other.timeout.read == 3.0
    assert other.base_url == client.base_url


def test_the_client_is_rebuilt_when_the_connection_changes(conn: Connection, mock_anthropic: MockAnthropic) -> None:
    mock_anthropic.api_keys.add(OTHER_KEY)
    conn.set_key(TEST_API_KEY)
    first = conn.client()
    conn.set_key(OTHER_KEY)
    assert conn.client() is not first and conn.client().api_key == OTHER_KEY


def test_sdk_retries_overloaded_twice(conn: Connection, mock_anthropic: MockAnthropic) -> None:
    conn.set_key(TEST_API_KEY)
    mock_anthropic.fault({"status": 529}, times=2)
    mock_anthropic.use_scenario({"steps": [{"match": {}, "reply": {"content": [{"type": "text", "text": "ok"}]}}]})
    assert conn.client().messages.create(**ASK).stop_reason == "end_turn"
    assert [r["status"] for r in mock_anthropic.requests_to("/v1/messages")] == [529, 529, 200]


# ---- the test of the link ----


def test_link_test_reports_latency_model_efforts_and_organization(conn: Connection) -> None:
    conn.set_key(TEST_API_KEY)
    t = conn.test()
    assert (t["ok"], t["model"], t["model_available"], t["organization_id"]) == (True, MODEL, True, TEST_ORG_ID)
    assert t["latency_ms"] >= 0 and t["effort_supported"] == ["low", "medium", "high", "xhigh", "max"]
    assert "Opus 5.5" in t["message"] and TEST_API_KEY not in json.dumps(t)
    from bf_core.assistant.schemas import ConnectionTest

    ConnectionTest.model_validate(t)
    assert conn.snapshot().online is True


def test_link_test_failures_are_answers_not_exceptions(conn: Connection, mock_anthropic: MockAnthropic) -> None:
    conn.set_key(TEST_API_KEY)
    mock_anthropic.api_keys.clear()  # the key is revoked
    bad = conn.test()
    assert bad["ok"] is False and "отклонён" in bad["message"] and bad["organization_id"] is None
    assert conn.snapshot().online is True, "the API was reached"
    mock_anthropic.api_keys.add(TEST_API_KEY)
    off = Connection(env={"ANTHROPIC_API_KEY": TEST_API_KEY, "BF_ASSISTANT_BASE_URL": "http://127.0.0.1:9"})
    r = off.test()
    assert r["ok"] is False and "без интернета" in r["message"] and off.snapshot().online is False


# ---- secret hygiene ----


def test_the_key_is_not_in_logs_files_statuses_or_errors(
    conn: Connection, mock_anthropic: MockAnthropic, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    assert logging.getLogger("anthropic").level == logging.WARNING and logging.getLogger("httpx2").level >= 30
    seen: list[str] = []
    seen.append(_text_of(_refused(conn.set_key, WRONG_KEY)))
    conn.set_key(TEST_API_KEY)
    mock_anthropic.use_scenario({"steps": [{"match": {}, "reply": {"content": [{"type": "text", "text": "ok"}]}}]})
    conn.client().messages.create(**ASK)
    conn.test()
    seen += [conn.status().model_dump_json(), json.dumps(conn.bus_event()), repr(conn.snapshot()), repr(conn.client())]
    for status, tail in (
        (401, ""),
        (403, ""),
        (404, ""),
        (429, ""),
        (500, ""),
        (529, ""),
        (400, f" key {TEST_API_KEY}"),
    ):
        mock_anthropic.fault({"status": status, "message": f"bad request{tail}"}, times=1)
        try:
            conn.client(max_retries=0).messages.create(**ASK)
        except anthropic.APIError as e:
            seen.append(_text_of(conn.api_error(e, MODEL)))
            conn.note_call(e)
    for blob in [*seen, caplog.text, json.dumps(mock_anthropic.requests)]:
        assert TEST_API_KEY not in blob and WRONG_KEY not in blob
    for blob in seen[1:]:
        assert TEST_API_KEY[-12:] not in blob, "even a long piece of the key is not shown"
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert all(TEST_API_KEY.encode() not in p.read_bytes() for p in files), "no file holds the key"


def test_scrub_cuts_out_keys_and_known_secrets() -> None:
    text = f"key {TEST_API_KEY} token sk-ant-oat01-abcdefgh0000 other secret-value-123456"
    out = scrub(text, "secret-value-123456", None, "")
    assert "sk-ant-test" not in out and "oat01" not in out and "secret-value" not in out
    assert mask_key(TEST_API_KEY) == "sk-ant-…0000" and mask_key("plain-1234") == "…1234"


def test_the_error_of_the_api_never_carries_the_key_in_extra(conn: Connection, mock_anthropic: MockAnthropic) -> None:
    conn.set_key(TEST_API_KEY)
    mock_anthropic.fault({"status": 400, "message": f"invalid x-api-key {TEST_API_KEY} / {WRONG_KEY}"}, times=1)
    with pytest.raises(anthropic.BadRequestError) as e:
        conn.client(max_retries=0).messages.create(**ASK)
    err = conn.api_error(e.value, MODEL)
    assert (err.status, err.code) == (502, "assistant_api_error")
    assert set(err.extra) == {"detail"} and "sk-ant-" not in err.extra["detail"].replace("sk-ant-…", "")
    assert TEST_API_KEY not in _text_of(err) and WRONG_KEY not in _text_of(err)


@pytest.mark.parametrize(
    ("status", "code", "http"),
    [
        (401, "assistant_bad_key", 422),
        (403, "assistant_bad_key", 422),
        (404, "assistant_model_unavailable", 422),
        (429, "assistant_rate_limited", 429),
        (500, "assistant_overloaded", 503),
        (529, "assistant_overloaded", 503),
        (400, "assistant_api_error", 502),
    ],
)
def test_api_failures_map_to_the_codes_of_the_contract(
    conn: Connection, mock_anthropic: MockAnthropic, status: int, code: str, http: int
) -> None:
    conn.set_key(TEST_API_KEY)
    mock_anthropic.fault({"status": status}, times=1)
    with pytest.raises(anthropic.APIStatusError) as e:
        conn.client(max_retries=0).messages.create(**ASK)
    err = conn.api_error(e.value, MODEL)
    assert (err.code, err.status) == (code, http) and err.message and err.hint
    if code == "assistant_model_unavailable":
        assert MODEL in err.message


def test_a_lost_connection_is_offline_and_marks_the_state(tmp_path: Path, closed_port_url: str) -> None:
    c = Connection(tmp_path, env={"ANTHROPIC_API_KEY": TEST_API_KEY, "BF_ASSISTANT_BASE_URL": closed_port_url})
    events: list[Any] = []
    c.add_listener(lambda: events.append(c.snapshot().online))
    with pytest.raises(anthropic.APIConnectionError) as e:
        c.client(max_retries=0).messages.create(**ASK)
    err = c.api_error(e.value)
    assert (err.status, err.code) == (503, "assistant_offline")
    c.note_call(e.value)
    c.note_call(None)
    assert events == [False, True]
    assert c.api_error(RuntimeError("boom")).code == "internal"


# ---- the exchange with the main process ----


def test_put_get_and_seal_of_the_secret(conn: Connection) -> None:
    mask = conn.put_secret("api_key", {"key": TEST_API_KEY}, False)  # the shell restores the key at start
    snap = conn.snapshot()
    assert mask == "sk-ant-…0000" and (snap.state, snap.kind, snap.seal_needed, snap.secure_storage) == (
        "connected",
        "api_key",
        False,
        False,
    )
    assert snap.online is None, "restored without a check of the link"
    kind, secret, rev = conn.get_secret() or ("", {}, -1)
    assert (kind, secret) == ("api_key", {"key": TEST_API_KEY})
    err = _refused(conn.put_secret, "api_key", {"key": "sk-ant-short"}, True)
    assert err.code == "assistant_bad_key_format" and conn.snapshot() == snap
    conn.set_key(TEST_API_KEY)  # a new connection of the window: it needs sealing
    assert conn.snapshot().seal_needed is True
    assert conn.mark_sealed(rev) is False and conn.snapshot().seal_needed is True, "an old revision does not seal"
    assert conn.mark_sealed(conn.snapshot().revision) is True and conn.snapshot().seal_needed is False
    assert conn.mark_sealed(conn.snapshot().revision) is False


def test_forget_clears_the_memory(conn: Connection) -> None:
    conn.set_key(TEST_API_KEY)
    seen: list[str] = []
    conn.add_listener(lambda: seen.append(conn.snapshot().state))
    snap = conn.forget()
    assert (snap.state, snap.kind, snap.mask, snap.seal_needed, snap.online) == ("none", None, None, False, None)
    assert conn.get_secret() is None and _refused(conn.client).code == "assistant_not_connected"
    assert seen == ["none"]
    assert TEST_API_KEY not in json.dumps(vars(conn), default=repr), "no copy of the key stays in the object"


def test_login_state_and_the_event_of_the_bus(conn: Connection) -> None:
    seen: list[str] = []
    conn.add_listener(lambda: seen.append(conn.snapshot().state))
    conn.set_login(True)
    conn.set_login(True)
    assert conn.bus_event() == {
        "state": "login",
        "kind": None,
        "mask": None,
        "model": MODEL,
        "online": None,
        "seal_needed": False,
    }
    conn.set_login(False)
    assert seen == ["login", "none"]
    from bf_core.assistant.schemas_dialog import ConnectionEvent

    conn.set_key(TEST_API_KEY)
    ConnectionEvent.model_validate(conn.bus_event())


def test_status_has_the_shape_of_the_contract(conn: Connection) -> None:
    s = conn.status(ant_available=True)
    assert (s.state, s.kind, s.ant_available, s.secure_storage, s.model, s.model_source) == (
        "none",
        None,
        True,
        True,
        MODEL,
        "default",
    )
    assert [m.id for m in s.models][0] == MODEL and s.prices_version
    conn.set_key(TEST_API_KEY)
    assert conn.status().mask == "sk-ant-…0000" and conn.status().seal_needed is True


# ---- account (MemoryOAuth) ----


def test_account_login_is_checked_and_sends_a_bearer_token(conn: Connection, mock_anthropic: MockAnthropic) -> None:
    snap = _account(mock_anthropic, conn)
    assert (snap.state, snap.kind, snap.mask, snap.seal_needed) == ("connected", "account", None, True)
    assert snap.organization == {"id": "org_x1", "name": None} and snap.workspace == {"id": "wrkspc_x1", "name": None}
    (req,) = mock_anthropic.requests
    assert req["auth"] == {"kind": "bearer", "ok": True} and OAUTH_BETA in req["headers"]["anthropic-beta"]
    assert req["headers"]["anthropic-workspace-id"] == "wrkspc_x1"
    client = conn.client()
    assert client.api_key is None and isinstance(client.credentials, MemoryOAuth)
    assert client.models.retrieve(MODEL).id == MODEL
    assert len(mock_anthropic.requests) == 2, "the token is valid: no exchange"
    assert conn.test()["ok"] is True


def test_account_with_a_wrong_token_is_not_accepted(conn: Connection, mock_anthropic: MockAnthropic) -> None:
    before = conn.set_key(TEST_API_KEY)
    err = _refused(
        _account,
        mock_anthropic,
        conn,
        access_token="sk-ant-oat01-wrong-0000000000000",
        refresh_token="sk-ant-ort01-unknown-0000000",
    )
    assert err.code == "assistant_bad_key" and "oat01-wrong" not in _text_of(err)
    assert conn.snapshot() == before and conn.client().api_key == TEST_API_KEY


def test_a_rejected_access_token_is_replaced_by_the_exchange(conn: Connection, mock_anthropic: MockAnthropic) -> None:
    snap = _account(mock_anthropic, conn, access_token="sk-ant-oat01-revoked-00000000000")  # 401, then the exchange
    assert snap.kind == "account" and snap.seal_needed is True
    grant = mock_anthropic.requests_to("/v1/oauth/token")
    assert len(grant) == 1 and grant[0]["refresh_token_ok"] is True
    assert conn.get_secret()[1]["refresh_token"] != TEST_REFRESH_TOKEN  # type: ignore[index]


def test_an_expired_token_is_exchanged_and_the_rotation_needs_sealing(
    conn: Connection, mock_anthropic: MockAnthropic
) -> None:
    conn.put_secret(
        "account",
        {
            "access_token": "sk-ant-oat01-expired-000000000",
            "expires_at": "2020-01-01T00:00:00Z",
            "refresh_token": TEST_REFRESH_TOKEN,
            "client_id": TEST_CLIENT_ID,
            "organization_id": TEST_ORG_ID,
        },
        True,
    )
    rev0 = conn.snapshot().revision
    assert conn.snapshot().seal_needed is False and conn.get_secret() is not None
    seen: list[bool] = []
    conn.add_listener(lambda: seen.append(conn.snapshot().seal_needed))
    assert conn.client().models.retrieve(MODEL).id == MODEL
    grant, models = mock_anthropic.requests
    assert (grant["path"], grant["status"], grant["refresh_token_ok"]) == ("/v1/oauth/token", 200, True)
    assert grant["body"] == {"grant_type": "refresh_token", "refresh_token": "<redacted>", "client_id": TEST_CLIENT_ID}
    assert (
        OAUTH_BETA in grant["headers"]["anthropic-beta"] and "oidc-federation" not in grant["headers"]["anthropic-beta"]
    )
    assert models["auth"] == {"kind": "bearer", "ok": True}
    snap = conn.snapshot()
    assert snap.seal_needed is True and snap.revision > rev0 and seen and seen[-1] is True
    kind, secret, rev = conn.get_secret() or ("", {}, 0)
    assert kind == "account" and rev == snap.revision
    assert secret["refresh_token"] != TEST_REFRESH_TOKEN and secret["refresh_token"] in mock_anthropic.refresh_tokens
    assert TEST_REFRESH_TOKEN not in mock_anthropic.refresh_tokens
    assert secret["expires_at"] > "2020" and secret["access_token"] in mock_anthropic.access_tokens
    assert conn.mark_sealed(rev) and conn.snapshot().seal_needed is False
    conn.client().models.retrieve(MODEL)
    assert len(mock_anthropic.requests_to("/v1/oauth/token")) == 1, "one exchange serves the later calls"


def test_a_used_refresh_token_is_refused_without_leaking_tokens(
    conn: Connection, mock_anthropic: MockAnthropic
) -> None:
    secret = {
        "access_token": "sk-ant-oat01-expired-000000000",
        "expires_at": 1,
        "refresh_token": "sk-ant-ort01-unknown-0000000",
        "client_id": TEST_CLIENT_ID,
    }
    conn.put_secret("account", {**secret, "expires_at": "2020-01-01T00:00:00Z"}, True)
    with pytest.raises(anthropic.AnthropicError) as e:
        conn.client(max_retries=0).models.retrieve(MODEL)
    refresh = e.value if isinstance(e.value, OAuthRefreshError) else e.value.__cause__
    assert isinstance(refresh, OAuthRefreshError) and refresh.status == 400 and not refresh.offline
    err = conn.api_error(e.value, MODEL)
    assert (err.status, err.code) == (422, "assistant_bad_key")
    assert "ort01" not in _text_of(err) + str(e.value) and "oat01" not in _text_of(err) + str(e.value)
    assert conn.snapshot().seal_needed is False, "nothing changed"


def test_an_unreachable_token_endpoint_is_offline(tmp_path: Path, closed_port_url: str) -> None:
    c = Connection(tmp_path, env={"BF_ASSISTANT_BASE_URL": closed_port_url})
    c.put_secret(
        "account",
        {
            "access_token": "sk-ant-oat01-expired-000000000",
            "expires_at": "2020-01-01T00:00:00Z",
            "refresh_token": TEST_REFRESH_TOKEN,
            "client_id": TEST_CLIENT_ID,
        },
        True,
    )
    with pytest.raises(anthropic.AnthropicError) as e:
        c.client(max_retries=0).models.retrieve(MODEL)
    assert c.api_error(e.value).code == "assistant_offline"
    c.note_call(e.value)
    assert c.snapshot().online is False


def test_memory_oauth_unit(mock_anthropic: MockAnthropic) -> None:
    now = [1_000_000.0]
    rotated: list[int] = []
    p = MemoryOAuth(
        TEST_ACCESS_TOKEN,
        TEST_REFRESH_TOKEN,
        TEST_CLIENT_ID,
        1_000_000 + 3600,
        mock_anthropic.base_url,
        on_rotate=lambda: rotated.append(1),
        clock=lambda: now[0],
    )
    tok = p()
    assert isinstance(tok, AccessToken) and tok.token == TEST_ACCESS_TOKEN and rotated == []
    now[0] += 3600 - 59  # inside the margin of 60 s before the end
    new = p()
    assert new.token != TEST_ACCESS_TOKEN and rotated == [1] and new.expires_at == int(now[0]) + 3600
    assert p().token == new.token and rotated == [1], "valid: no second exchange"
    forced = p(force_refresh=True)
    assert forced.token != new.token and rotated == [1, 1], "a 401 forces the exchange"
    assert p.snapshot()["refresh_token"] in mock_anthropic.refresh_tokens
    assert p.client_id == TEST_CLIENT_ID and not p.expired()
    p.close()


def test_memory_oauth_never_shows_or_copies_its_tokens() -> None:
    p = MemoryOAuth(
        "sk-ant-oat01-secret-token-0000", "sk-ant-ort01-secret-refresh-0000", "cid", None, "http://127.0.0.1:1"
    )
    assert "secret" not in repr(p) and "secret" not in str(p) and repr(p).endswith("expires_at=None)")
    with pytest.raises(TypeError):
        pickle.dumps(p)
    assert p().expires_at is None, "no expiry known: never refreshed"
    assert conn_mod._unix("2026-11-26T10:00:00Z") and conn_mod._unix(None) is None and conn_mod._unix("x") is None
    assert conn_mod._iso(1_700_000_000) == "2023-11-14T22:13:20Z"
    assert oauth.REFRESH_SKEW_S == 60


# ---- guard of the SDK 2.6.0 ----


def test_guard_the_constants_and_the_shape_of_the_sdk_2_6_0() -> None:
    """Fails when the SDK changes what this module copies or relies on (updating `anthropic` is an ADR 0001 matter)."""
    from anthropic import _constants as sdk
    from anthropic.lib.credentials import _constants as c

    assert anthropic.__version__ == "2.6.0"
    assert (
        (oauth.TOKEN_ENDPOINT, oauth.OAUTH_API_BETA_HEADER, oauth.GRANT_TYPE_REFRESH_TOKEN)
        == (
            c.TOKEN_ENDPOINT,
            c.OAUTH_API_BETA_HEADER,
            c.GRANT_TYPE_REFRESH_TOKEN,
        )
        == ("/v1/oauth/token", "oauth-2025-04-20", "refresh_token")
    )
    assert oauth.TOKEN_EXCHANGE_TIMEOUT == c.TOKEN_EXCHANGE_TIMEOUT and conn_mod.DEFAULT_BASE_URL == c.DEFAULT_BASE_URL
    assert c.FEDERATION_BETA_HEADER == "oidc-federation-2026-04-01", "a federation grant is not ours"
    assert oauth.REFRESH_SKEW_S < c.ADVISORY_REFRESH_SECONDS and oauth.REFRESH_SKEW_S >= c.MANDATORY_REFRESH_SECONDS
    assert (c.ENV_API_KEY, c.ENV_PROFILE, c.ENV_BASE_URL, c.ENV_CONFIG_DIR) == (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_PROFILE",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CONFIG_DIR",
    ), "the variables of the chain that the client must not follow"
    assert sdk.DEFAULT_MAX_RETRIES == conn_mod.MAX_RETRIES == 2
    params = inspect.signature(anthropic.Anthropic.__init__).parameters
    for name in ("api_key", "credentials", "base_url", "timeout", "max_retries", "default_headers"):
        assert name in params, name
    assert [f for f in AccessToken.__dataclass_fields__] == ["token", "expires_at"]
    assert "force_refresh" in inspect.signature(AccessTokenProvider.__call__).parameters
    assert "force_refresh" in inspect.signature(MemoryOAuth.__call__).parameters
    assert anthropic.DefaultHttpxClient is not None and anthropic.Timeout is not None
    assert hasattr(anthropic.Anthropic, "copy") and hasattr(anthropic.Anthropic, "__enter__")
