# ruff: noqa: F811 - fixtures imported from the support module are used as parameters
"""The HTTP API of the assistant without the turns (подшаг 23.11; e23-api.md §2–§4, §7, §14): the status, the
preferences, the usage, the connection (the key is checked before it is kept, a secret is nowhere), the routes of the
shell (`Origin` -> 403), the login with an account, the dialogs. The turns (SSE) — `test_assistant_api_turns.py`."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from bf_core.assistant import schemas as sc
from bf_core.assistant import schemas_dialog as sd
from tests.assistant.fake_ant import DEFAULT_CODE
from tests.assistant.mock_anthropic import TEST_ACCESS_TOKEN, TEST_REFRESH_TOKEN
from tests.test_assistant_api_support import (  # noqa: F401 - fixtures
    API,
    APP_ORIGIN,
    KEY_LIKE,
    TEST_API_KEY,
    Api,
    api,
    clean_anthropic_env,
    closed_port_url,
    err,
    fake_ant_path,
    mock_anthropic,
    pclient,
)

# --- §2 the status, the preferences, the usage ---------------------------------------------------------------------


def test_status_of_a_core_that_is_not_connected(api: Api) -> None:
    r = api.win.get(f"{API}/status")
    assert r.status_code == 200
    st = sc.Status.model_validate(r.json())
    assert (st.state, st.kind, st.mask, st.online, st.model, st.model_source) == (
        "none", None, None, None, "claude-opus-5-5", "default",
    )  # fmt: skip
    assert st.ant_available is False and st.secure_storage is True and not st.seal_needed
    assert [m.id for m in st.models][0] == "claude-opus-5-5" and len(st.efforts) == 5


def test_the_key_of_the_environment_is_a_connection(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", TEST_API_KEY)
    st = api.win.get(f"{API}/status").json()
    assert (st["state"], st["kind"], st["seal_needed"]) == ("connected", "env", False)
    assert TEST_API_KEY not in str(st)
    # «Выйти» does not touch the environment
    assert api.win.delete(f"{API}/connection").json()["kind"] == "env"


def test_prefs_are_kept_and_announced(api: Api) -> None:
    r = api.win.put(f"{API}/prefs", json={"model": "claude-sonnet-5-5", "effort": "high"})
    assert r.status_code == 200
    st = r.json()
    assert (st["model"], st["model_source"], st["effort"], st["effort_source"]) == (
        "claude-sonnet-5-5", "prefs", "high", "prefs",
    )  # fmt: skip
    assert api.win.get(f"{API}/status").json()["model"] == "claude-sonnet-5-5"
    assert api.bus("assistant.connection")[-1]["model"] == "claude-sonnet-5-5"
    assert api.win.put(f"{API}/prefs", json={}).json()["effort"] == "high"  # nothing to change


@pytest.mark.parametrize(
    "body, param",
    [
        ({"model": "claude-nope-9"}, "model"),
        ({"model": "bad model!"}, "model"),
        ({"effort": "extreme"}, "effort"),
        ([1], "body"),
    ],
)
def test_prefs_refuse_what_is_not_known(api: Api, body: Any, param: str) -> None:
    e = err(api.win.put(f"{API}/prefs", json=body), 422, "bad_param")
    assert e["param"] == param
    assert api.win.get(f"{API}/status").json()["model_source"] == "default"  # nothing was written


def test_a_broken_body_is_bad_param_and_never_echoed(api: Api) -> None:
    r = api.win.post(
        f"{API}/connection/key",
        content=b'{"key": "sk-ant-leak-1234567890123456789',
        headers={"Content-Type": "application/json"},
    )
    e = err(r, 422, "bad_param")
    assert e["param"] == "body" and "leak" not in r.text


def test_usage_is_the_three_periods(api: Api) -> None:
    r = api.win.get(f"{API}/usage")
    assert r.status_code == 200
    u = sc.Usage.model_validate(r.json())
    assert u.all.requests == 0 and u.all.cost_usd == 0 and u.prices_version == "2026-11"
    api.connect()
    api.mock.use_scenario("summary")
    assert api.ask("Сводка по проекту")[0] == 200
    u = sc.Usage.model_validate(api.win.get(f"{API}/usage").json())
    assert u.today.requests == u.all.requests == 1 and u.all.by_model[0].model == "claude-opus-5-5"


# --- §3 the connection -----------------------------------------------------------------------------------------------


def test_the_key_is_checked_with_the_api_then_kept(api: Api, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    r = api.win.post(f"{API}/connection/key", json={"key": f"  {TEST_API_KEY}  "})
    assert r.status_code == 200
    st = sc.Status.model_validate(r.json())
    assert (st.state, st.kind, st.mask, st.seal_needed) == ("connected", "api_key", "sk-ant-…0000", True)
    assert [x["path"] for x in api.mock.requests] == ["/v1/models/claude-opus-5-5"]  # a check, no turn
    ev = api.bus("assistant.connection")[-1]
    assert (ev["state"], ev["kind"], ev["mask"], ev["seal_needed"]) == ("connected", "api_key", "sk-ant-…0000", True)
    for text in (r.text, str(api.bus("assistant.connection")), caplog.text, api.win.get(f"{API}/status").text):
        assert TEST_API_KEY not in text and not KEY_LIKE.search(text)


def test_a_wrong_key_is_refused_and_not_kept(api: Api) -> None:
    wrong = "sk-ant-wrong-00000000000000000000"
    e = err(api.win.post(f"{API}/connection/key", json={"key": wrong}), 422, "assistant_bad_key")
    assert wrong not in str(e)
    assert api.win.get(f"{API}/status").json()["state"] == "none"
    assert api.svc.connection.get_secret() is None and api.bus("assistant.connection") == []


def test_a_key_of_a_wrong_format_never_reaches_the_api(api: Api) -> None:
    for bad in ("hello", "sk-ant-short", "sk-ant-has space-0000000000000000", ""):
        err(api.win.post(f"{API}/connection/key", json={"key": bad}), 422, "assistant_bad_key_format")
    assert api.win.post(f"{API}/connection/key", json={}).json()["code"] == "bad_param"
    assert api.mock.requests == []


def test_without_a_network_the_key_is_not_accepted(
    api: Api, monkeypatch: pytest.MonkeyPatch, closed_port_url: str
) -> None:
    monkeypatch.setenv("BF_ASSISTANT_BASE_URL", closed_port_url)
    err(api.win.post(f"{API}/connection/key", json={"key": TEST_API_KEY}), 503, "assistant_offline")
    assert api.win.get(f"{API}/status").json()["state"] == "none"


def test_the_link_is_tested_and_the_session_is_left(api: Api) -> None:
    err(api.win.post(f"{API}/connection/test", json={}), 409, "assistant_not_connected")
    api.connect()
    t = sc.ConnectionTest.model_validate(api.win.post(f"{API}/connection/test", json={}).json())
    assert t.ok and t.model_available and t.model == "claude-opus-5-5" and t.latency_ms >= 0
    st = api.win.delete(f"{API}/connection").json()
    assert (st["state"], st["kind"], st["mask"]) == ("none", None, None)
    assert api.bus("assistant.connection")[-1]["state"] == "none"
    err(api.win.post(f"{API}/connection/test", json={}), 409, "assistant_not_connected")


# --- the routes of the shell -----------------------------------------------------------------------------------------

SHELL_ROUTES = [
    ("PUT", "/connection/secret", {"kind": "api_key", "secret": {"key": TEST_API_KEY}, "secure_storage": True}),
    ("GET", "/connection/secret", None),
    ("POST", "/connection/sealed", {"revision": 1}),
]


@pytest.mark.parametrize("method, path, body", SHELL_ROUTES)
def test_the_secret_routes_refuse_the_window(api: Api, method: str, path: str, body: Any) -> None:
    r = api.win.request(method, f"{API}{path}", json=body)
    err(r, 403, "shell_only")
    assert TEST_API_KEY not in r.text
    assert api.win.get(f"{API}/status").json()["state"] == "none"  # nothing was done
    shell = api.shell.request(method, f"{API}{path}", json=body)
    assert shell.status_code in (200, 204), shell.text


def test_the_shell_hands_the_secret_over_and_back(api: Api, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    assert api.shell.get(f"{API}/connection/secret").status_code == 204  # nothing to keep
    api.connect()
    got = api.shell.get(f"{API}/connection/secret")
    assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
    kept = got.json()
    assert kept["kind"] == "api_key" and kept["secret"] == {"key": TEST_API_KEY}
    sealed = api.shell.post(f"{API}/connection/sealed", json={"revision": kept["revision"]})
    assert sealed.status_code == 200 and sealed.json()["seal_needed"] is False
    assert api.bus("assistant.connection")[-1]["seal_needed"] is False
    for text in (caplog.text, str(api.bus("assistant.connection")), sealed.text):
        assert TEST_API_KEY not in text


def test_the_shell_restores_a_key_and_an_account_without_a_check_of_the_link(api: Api) -> None:
    put = api.shell.put(
        f"{API}/connection/secret",
        json={"kind": "api_key", "secret": {"key": TEST_API_KEY}, "secure_storage": False},
    )
    assert put.status_code == 200 and put.json() == {"ok": True, "kind": "api_key", "mask": "sk-ant-…0000"}
    st = api.win.get(f"{API}/status").json()
    assert (st["state"], st["secure_storage"], st["seal_needed"]) == ("connected", False, False)
    assert api.mock.requests == []  # an offline start
    account = {
        "access_token": TEST_ACCESS_TOKEN,
        "refresh_token": TEST_REFRESH_TOKEN,
        "client_id": "00000000-0000-4000-8000-000000000001",
        "organization_id": "org_test0000000000",
        "expires_at": "2026-12-01T00:00:00Z",
    }
    put = api.shell.put(f"{API}/connection/secret", json={"kind": "account", "secret": account, "secure_storage": True})
    assert put.json() == {"ok": True, "kind": "account", "mask": None}
    assert api.win.get(f"{API}/status").json()["kind"] == "account"


def test_a_bad_secret_is_refused_without_its_value(api: Api, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    bad = {"kind": "api_key", "secret": {"key": "sk-ant-oops"}, "secure_storage": True}
    r = api.shell.put(f"{API}/connection/secret", json=bad)
    assert r.status_code == 422 and "oops" not in r.text and "oops" not in caplog.text
    r = api.shell.put(
        f"{API}/connection/secret", json={"kind": "account", "secret": {"access_token": "x"}, "secure_storage": True}
    )
    assert r.status_code == 422 and r.json()["code"] == "bad_param" and r.json()["param"] == "secret"
    err(api.shell.post(f"{API}/connection/sealed", json={"revision": "x"}), 422, "bad_param")


# --- the login with an account ---------------------------------------------------------------------------------------


def test_without_ant_the_login_is_not_in_this_build(api: Api) -> None:
    assert api.win.get(f"{API}/status").json()["ant_available"] is False
    err(api.win.post(f"{API}/login/start", json={}), 501, "assistant_no_ant")


@pytest.fixture()
def ant(api: Api, fake_ant_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BF_ANT_PATH", str(fake_ant_path))
    return fake_ant_path


def test_the_login_goes_from_the_address_to_the_account(api: Api, ant: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    assert api.win.get(f"{API}/status").json()["ant_available"] is True
    r = api.win.post(f"{API}/login/start", json={})
    assert r.status_code == 200
    started = sc.LoginStart.model_validate(r.json())
    assert started.url.startswith("https://console.anthropic.com/") and started.expires_at.endswith("Z")
    assert api.win.get(f"{API}/status").json()["state"] == "login"
    assert api.bus("assistant.connection")[-1]["state"] == "login"
    for bad in ("", "a\nb", "x" * 4097):  # refused before `ant` is asked: the login goes on
        err(api.win.post(f"{API}/login/{started.login_id}/finish", json={"code": bad}), 422, "bad_param")
    wrong = api.win.post(f"{API}/login/{started.login_id}/finish", json={"code": "WRONG-CODE-999999"})
    err(wrong, 422, "assistant_login_code")  # `ant` exits: the login is over, a new one is needed
    assert api.win.get(f"{API}/status").json()["state"] == "none"
    started = sc.LoginStart.model_validate(api.win.post(f"{API}/login/start", json={}).json())
    r = api.win.post(f"{API}/login/{started.login_id}/finish", json={"code": DEFAULT_CODE})
    assert r.status_code == 200, r.text
    st = r.json()
    assert (st["state"], st["kind"], st["mask"], st["seal_needed"]) == ("connected", "account", None, True)
    assert st["organization"]["id"] == "org_test0000000000"
    err(
        api.win.post(f"{API}/login/{started.login_id}/finish", json={"code": DEFAULT_CODE}), 404, "assistant_login_gone"
    )
    for text in (r.text, caplog.text, str(api.bus("assistant.connection"))):
        assert TEST_ACCESS_TOKEN not in text and TEST_REFRESH_TOKEN not in text and DEFAULT_CODE not in text


def test_a_login_is_cancelled_and_forgotten(api: Api, ant: Path) -> None:
    started = api.win.post(f"{API}/login/start", json={}).json()
    assert api.win.delete(f"{API}/login/{started['login_id']}").status_code == 204
    assert api.win.get(f"{API}/status").json()["state"] == "none"
    err(
        api.win.post(f"{API}/login/{started['login_id']}/finish", json={"code": DEFAULT_CODE}),
        404,
        "assistant_login_gone",
    )
    assert api.win.delete(f"{API}/login/nope").status_code == 204  # nothing to cancel is not an error


def test_ant_that_fails_before_the_address_is_a_failed_login(
    api: Api, ant: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ANT_MODE", "exit_early")
    err(api.win.post(f"{API}/login/start", json={}), 502, "assistant_login_failed")
    assert api.win.get(f"{API}/status").json()["state"] == "none"


# --- §7 the dialogs ---------------------------------------------------------------------------------------------------


def test_no_dialogs_in_a_new_core(api: Api) -> None:
    assert api.win.get(f"{API}/dialogs").json() == {"total": 0, "items": []}
    assert api.win.get(f"{API}/dialogs/last").status_code == 204
    err(api.win.get(f"{API}/dialogs/41"), 404, "assistant_dialog_not_found")
    err(api.win.get(f"{API}/dialogs/t-0b6f2d4e-8c1a-4d3e-9a5b-7c2e1f0a9b88"), 404, "assistant_dialog_not_found")
    err(api.win.get(f"{API}/dialogs/nonsense"), 404, "assistant_dialog_not_found")
    err(api.win.patch(f"{API}/dialogs/41", json={"title": "Имя"}), 409, "no_project")  # a saved id, no project
    err(api.win.delete(f"{API}/dialogs/41"), 409, "no_project")


@pytest.mark.parametrize("query", ["offset=-1", "limit=0", "limit=201", "offset=x", "limit=1.5"])
def test_the_page_of_dialogs_is_checked(api: Api, query: str) -> None:
    assert api.win.get(f"{API}/dialogs?{query}").json()["code"] == "bad_param"


def test_a_temporary_dialog_is_listed_read_renamed_and_deleted(api: Api) -> None:
    api.connect()
    api.mock.use_scenario("summary")
    code, events, _ = api.ask("Сводка по проекту")
    head = events[0]["data"]["dialog"]
    assert code == 200 and head["temporary"] is True and head["id"].startswith("t-")
    page = sd.DialogsPage.model_validate(api.win.get(f"{API}/dialogs?limit=10").json())
    assert page.total == 1 and page.items[0].id == head["id"] and page.items[0].state == "idle"
    assert api.win.get(f"{API}/dialogs?offset=1").json() == {"total": 1, "items": []}
    last = api.win.get(f"{API}/dialogs/last")
    assert last.status_code == 200 and last.json()["id"] == head["id"]
    d = sd.Dialog.model_validate(api.win.get(f"{API}/dialogs/{head['id']}").json())
    assert [i.kind for i in d.items] == ["user", "notice", "assistant"] or d.items[0].kind == "user"
    assert d.note == "Диалог не сохранится: проект не открыт" and d.pending is None
    renamed = api.win.patch(f"{API}/dialogs/{head['id']}", json={"title": "  Итоги  "})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Итоги"
    assert api.bus("assistant.dialogs")[-1] == {"project_uid": None, "id": head["id"]}
    for bad in ({"title": ""}, {"title": "x" * 201}, {"title": "   "}, {}):
        assert api.win.patch(f"{API}/dialogs/{head['id']}", json=bad).json()["param"] == "title"
    assert api.win.delete(f"{API}/dialogs/{head['id']}").status_code == 204
    assert api.bus("assistant.dialogs")[-1] == {"project_uid": None, "id": head["id"], "deleted": True}
    assert api.win.get(f"{API}/dialogs").json()["total"] == 0
    err(api.win.delete(f"{API}/dialogs/{head['id']}"), 404, "assistant_dialog_not_found")


def test_the_dialogs_of_the_open_project_are_saved_in_it(api: Api) -> None:
    assert api.win.post("/api/v1/projects/demo").status_code == 200
    puid = api.svc.project_uid()
    assert puid
    api.connect()
    api.mock.use_scenario("summary")
    code, events, _ = api.ask("Сводка по проекту")
    head = events[0]["data"]["dialog"]
    assert code == 200 and head["temporary"] is False and head["project_uid"] == puid and head["id"].isdigit()
    assert api.bus("assistant.dialogs")[-1] == {"project_uid": puid, "id": head["id"]}
    assert api.win.get(f"{API}/dialogs/last").json()["id"] == head["id"]
    assert api.win.patch(f"{API}/dialogs/{head['id']}", json={"title": "Сводка"}).json()["title"] == "Сводка"
    assert api.win.delete(f"{API}/dialogs/{head['id']}").status_code == 204
    assert (
        api.bus("assistant.dialogs")[-1]["deleted"] is True and api.bus("assistant.dialogs")[-1]["project_uid"] == puid
    )
