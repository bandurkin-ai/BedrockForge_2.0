"""Connecting Claude from the application: API key, account login through `ant`, preferences, chats, usage."""

import os
import stat
import textwrap

import pytest

from app.assistant import connection, service
from tests.test_assistant import UI_TOOL, ScriptedClaude, reply, text  # noqa: F401  (fixtures below reuse them)
from tests.test_assistant import assistant_dir, claude, project  # noqa: F401

KEY = "sk-ant-api03-" + "x" * 40


@pytest.fixture
def checked(monkeypatch):
    """The test request to Anthropic: answered without the network."""
    calls = []

    def fake_check(c=None, model_id=None):
        calls.append(model_id)
        return "Claude Opus 5.5"

    monkeypatch.setattr(connection, "check", fake_check)
    return calls


def test_key_is_checked_stored_privately_and_never_returned(db_client, assistant_dir, checked):
    bad = db_client.post("/api/assistant/connect/key", json={"api_key": "hello"})
    assert bad.status_code == 400 and "sk-ant-" in bad.json()["detail"]

    ok = db_client.post("/api/assistant/connect/key", json={"api_key": f"  {KEY} "}).json()

    assert ok["configured"] is True and ok["mode"] == "key" and "Opus" in ok["message"]
    assert KEY not in str(ok) and ok["key"].startswith("sk-ant-api") and ok["key"].endswith("xxxx")
    path = assistant_dir / "connection.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert connection.client().api_key == KEY

    gone = db_client.post("/api/assistant/disconnect").json()
    assert gone["configured"] is False and KEY not in path.read_text()


def test_environment_key_still_works(db_client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)

    assert db_client.get("/api/assistant/status").json()["mode"] == "env"


def test_preferences_choose_the_model_and_effort_of_the_chat(project, claude):
    client, pid = project
    fake = claude(reply(text("Да.")))
    assert client.put("/api/assistant/preferences", json={"model": "claude-haiku-5-5", "effort": "low"}).status_code == 200
    assert client.put("/api/assistant/preferences", json={"model": "gpt"}).status_code == 400

    client.post(f"/api/projects/{pid}/assistant/chat", json={"text": "Привет", "ui_tools": [UI_TOOL]})

    sent = fake.requests[0]
    assert sent["model"] == "claude-haiku-5-5" and sent["output_config"] == {"effort": "low"}
    assert "fallbacks" not in sent  # Haiku has no server-side fallback


def test_chats_are_saved_listed_renamed_and_deleted_with_usage(project, claude):
    client, pid = project
    claude(
        reply(text("Сейчас выберу."), tool_use_ui := __import__("tests.test_assistant", fromlist=["tool_use"]).tool_use("t1", "ui_hole_select", {"hole_id": "H-1"}), stop_reason="tool_use"),
        reply(text("Готово.")),
    )
    first = client.post(f"/api/projects/{pid}/assistant/chat", json={"text": "Покажи скважину H-1 крупно", "ui_tools": [UI_TOOL]}).json()
    chat_id = first["conversation_id"]
    assert first["title"] == "Покажи скважину H-1 крупно"
    assert client.get(f"/api/assistant/chats/{chat_id}").json()["waiting"] is True
    client.post(f"/api/projects/{pid}/assistant/chat", json={
        "conversation_id": chat_id, "ui_tools": [UI_TOOL],
        "tool_results": [{"tool_use_id": "t1", "content": "Выбрана H-1", "title": "Выбрать скважину", "detail": "hole_id: H-1"}],
    })

    listed = client.get(f"/api/projects/{pid}/assistant/chats").json()
    assert [c["id"] for c in listed] == [chat_id] and listed[0]["messages"] == 1
    full = client.get(f"/api/assistant/chats/{chat_id}").json()
    assert [i["kind"] for i in full["items"]] == ["user", "assistant", "tool", "assistant"]
    assert full["items"][2] == {"kind": "tool", "title": "Выбрать скважину", "detail": "hole_id: H-1", "ok": True}
    assert full["input_tokens"] == 2000 and full["cost_usd"] == pytest.approx(2 * (1000 * 4 + 100 * 20) / 1e6)

    usage = client.get("/api/assistant/usage").json()
    assert usage["all"]["requests"] == 2 and usage["today"]["cost_usd"] == pytest.approx(0.012)
    assert usage["month"]["by_model"][0]["model"] == "claude-opus-5-5"

    assert client.patch(f"/api/assistant/chats/{chat_id}", json={"title": "H-1"}).json()["title"] == "H-1"
    assert client.delete(f"/api/assistant/chats/{chat_id}").status_code == 204
    assert client.get(f"/api/projects/{pid}/assistant/chats").json() == []
    assert client.get("/api/assistant/usage").json()["all"]["requests"] == 2  # spending stays


def test_cost_of_a_response_by_its_model():
    used = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 0}
    assert connection.cost_usd("claude-sonnet-5-5", used) == pytest.approx(2 + 10 + 0.1)
    assert connection.cost_usd("claude-opus-5-5-20260101", used) == pytest.approx(4 + 20 + 0.2)
    assert connection.cost_usd("other-model", used) is None


FAKE_ANT = textwrap.dedent("""\
    #!/bin/sh
    # A stand-in for the `ant` CLI: the login prints an address and waits for the code.
    if [ "$1 $2" = "auth login" ]; then
      echo "Open this URL in a browser to sign in:"
      echo "  https://console.example.test/oauth/authorize?code=true&state=abc"
      printf "Paste the authorization code: "
      read code
      if [ "$code" = "good-code" ]; then
        mkdir -p "$ANTHROPIC_CONFIG_DIR/credentials" "$ANTHROPIC_CONFIG_DIR/configs"
        echo '{"authentication":{"type":"user_oauth"}}' > "$ANTHROPIC_CONFIG_DIR/configs/bedrockforge.json"
        echo '{"access_token":"t"}' > "$ANTHROPIC_CONFIG_DIR/credentials/bedrockforge.json"
        chmod 600 "$ANTHROPIC_CONFIG_DIR/credentials/bedrockforge.json"
        echo "Logged in."
        exit 0
      fi
      echo "Error: invalid authorization code"
      exit 1
    fi
    if [ "$1 $2" = "auth status" ]; then
      echo "Profile: bedrockforge"
      echo "Organization: Geo Lab"
      echo "Workspace: Default"
      echo "Access token: secret-value"
      exit 0
    fi
    exit 0
""")


@pytest.fixture
def fake_ant(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "ant"
    exe.write_text(FAKE_ANT)
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return exe


def test_account_login_without_ant_explains_what_to_do(db_client, monkeypatch):
    monkeypatch.setattr(connection, "ant_path", lambda: None)

    r = db_client.post("/api/assistant/connect/account/start")

    assert r.status_code == 501 and "ant" in r.json()["detail"]
    assert db_client.get("/api/assistant/status").json()["account_login"] is False


def test_account_login_through_ant(db_client, fake_ant, checked):
    start = db_client.post("/api/assistant/connect/account/start").json()
    assert start["url"] == "https://console.example.test/oauth/authorize?code=true&state=abc"
    wrong = db_client.post("/api/assistant/connect/account/finish", json={"login_id": start["login_id"], "code": "bad"})
    assert wrong.status_code == 400 and "invalid authorization code" in wrong.json()["detail"]

    again = db_client.post("/api/assistant/connect/account/start").json()
    done = db_client.post("/api/assistant/connect/account/finish", json={"login_id": again["login_id"], "code": "good-code"}).json()

    assert done["mode"] == "account" and done["configured"] is True
    assert done["account"] == {"profile": "bedrockforge", "organization": "Geo Lab", "workspace": "Default"}  # no token
    assert connection.client() is not None
    assert db_client.post("/api/assistant/disconnect").json()["configured"] is False
    assert not (connection.config_dir() / "credentials").exists()


def test_unreadable_profile_is_a_clear_error_not_a_crash(db_client, fake_ant, monkeypatch):
    start = db_client.post("/api/assistant/connect/account/start").json()
    db_client.post("/api/assistant/connect/account/finish", json={"login_id": start["login_id"], "code": "good-code"})
    (connection.config_dir() / "credentials" / "bedrockforge.json").chmod(0o644)  # the SDK refuses such a file

    r = db_client.post("/api/assistant/check")

    assert r.status_code in (401, 503) and "Anthropic" in r.json()["detail"]


def test_stale_login_is_refused(db_client, fake_ant):
    start = db_client.post("/api/assistant/connect/account/start").json()
    db_client.post("/api/assistant/connect/account/cancel")

    r = db_client.post("/api/assistant/connect/account/finish", json={"login_id": start["login_id"], "code": "good-code"})

    assert r.status_code == 409
