"""Sign-in with an account through `ant` (подшаг 23.5; ADR 0029 решение 11; e23-api §3): `bf_core/assistant/login.py`.

A real process of `tests/assistant/fake_ant.py` (a launcher in `BF_ANT_PATH`) and the Anthropic mock: no network.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from bf_core.api.errors import ApiError
from bf_core.assistant import login as login_mod
from bf_core.assistant.connection import Connection
from bf_core.assistant.login import OUTPUT_LIMIT, LoginManager, _Session, ant_available, ant_path
from tests.assistant import conftest as fx
from tests.assistant.fake_ant import DEFAULT_CODE
from tests.assistant.mock_anthropic import (
    TEST_ACCESS_TOKEN,
    TEST_ORG_ID,
    TEST_REFRESH_TOKEN,
    MockAnthropic,
)

# fixtures of the assistant tests (an assignment: the parameters below must not 'redefine' an import)
clean_anthropic_env = fx.clean_anthropic_env
fake_ant_path = fx.fake_ant_path
mock_anthropic = fx.mock_anthropic

SECRETS = (TEST_ACCESS_TOKEN, TEST_REFRESH_TOKEN, DEFAULT_CODE, "WRONG-CODE-999999")


@pytest.fixture()
def conn(tmp_path: Path, mock_anthropic: MockAnthropic) -> Connection:
    return Connection(tmp_path / "data", env={"BF_ASSISTANT_BASE_URL": mock_anthropic.base_url})


@pytest.fixture()
def ant(fake_ant_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BF_ANT_PATH", str(fake_ant_path))
    return fake_ant_path


@pytest.fixture()
def tmp_base(tmp_path: Path) -> Path:
    return tmp_path / "ant-tmp"


@pytest.fixture()
def manager(conn: Connection, ant: Path, tmp_base: Path) -> LoginManager:
    return LoginManager(conn, base_dir=tmp_base, url_timeout=10.0, finish_timeout=10.0)


def _folders(base: Path) -> list[Path]:
    return sorted(base.iterdir()) if base.exists() else []


def _refused(call: Any, *args: Any) -> ApiError:
    with pytest.raises(ApiError) as e:
        call(*args)
    return e.value  # type: ignore[no-any-return]


def _text_of(err: ApiError) -> str:
    return f"{err.status} {err.code} {err.message} {err.hint} {err.extra}"


def _assert_no_secret(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text


# ---- the way it should go ----


def test_the_address_is_given_and_the_right_code_connects_the_account(
    manager: LoginManager, conn: Connection, tmp_base: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    started = manager.start()
    assert started.url.startswith("https://console.anthropic.com/oauth/authorize?") and started.expires_at.endswith("Z")
    assert len(_folders(tmp_base)) == 1, "the profile of `ant` lives in a temporary folder"
    assert conn.snapshot().state == "login" and manager.active
    snap = manager.finish(started.login_id, "  " + DEFAULT_CODE + " \n".strip())
    assert (snap.state, snap.kind, snap.mask, snap.seal_needed) == ("connected", "account", None, True)
    assert snap.organization == {"id": TEST_ORG_ID, "name": None} and snap.workspace is not None
    assert _folders(tmp_base) == [], "the folder is removed right after the tokens are read"
    assert not manager.active and conn.snapshot().state == "connected"
    secret = conn.get_secret()
    assert secret is not None and secret[0] == "account" and secret[1]["access_token"] == TEST_ACCESS_TOKEN
    _assert_no_secret(caplog.text)


def test_ant_gets_its_own_profile_folder_and_no_credentials_of_the_machine(
    manager: LoginManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", fx.TEST_API_KEY)
    monkeypatch.setenv("ANTHROPIC_PROFILE", "other")
    env = manager._child_env(tmp_path / "x")
    assert env["ANTHROPIC_CONFIG_DIR"] == str(tmp_path / "x")
    assert not {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_BASE_URL"} & set(env)
    assert env["NO_COLOR"] == "1" and env["TERM"] == "dumb"


def test_ant_gets_only_an_allow_list_of_the_environment(manager: LoginManager, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in (("MY_SECRET_TOKEN", "x"), ("GITHUB_TOKEN", "y"), ("BF_CORE_TOKEN", "z"), ("LC_ALL", "C")):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    env = manager._child_env(Path("/x"))
    assert not {"MY_SECRET_TOKEN", "GITHUB_TOKEN", "BF_CORE_TOKEN"} & set(env)
    assert env["LC_ALL"] == "C" and env["HTTPS_PROXY"] == "http://127.0.0.1:1" and "PATH" in env


def test_ant_path_is_absolute(ant: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ant.parent)
    found = ant_path({"BF_ANT_PATH": ant.name})
    assert found is not None and Path(found).is_absolute() and Path(found) == ant.resolve()


def test_the_output_of_ant_is_bounded(tmp_path: Path) -> None:
    session = _Session(tmp_path, 10.0)

    class _Stream:
        def __init__(self) -> None:
            self.chunks = [b"x" * 4096] * 100 + [b"tail", b""]

        def read1(self, n: int) -> bytes:
            return self.chunks.pop(0)

    session._pump(_Stream())
    assert len(session.output) <= OUTPUT_LIMIT and session.output.endswith("tail")


def test_stale_profile_folders_are_swept(conn: Connection, ant: Path, tmp_base: Path) -> None:
    old, fresh, other = tmp_base / "ant-old", tmp_base / "ant-fresh", tmp_base / "keep-me"
    for folder in (old, fresh, other):
        (folder / "credentials").mkdir(parents=True)
        (folder / "credentials" / "bedrockforge.json").write_text('{"refresh_token": "r"}')
    long_ago = time.time() - 3600
    os.utime(old, (long_ago, long_ago))
    m = LoginManager(conn, base_dir=tmp_base, url_timeout=10.0, finish_timeout=10.0)
    started = m.start()  # the first start removes what is older than a login can live
    assert not old.exists() and fresh.exists() and other.exists()
    assert m.sweep_stale(everything=True) == 1 and not fresh.exists() and other.exists()  # not the live folder
    assert len(_folders(tmp_base)) == 2
    m.cancel(started.login_id)


def test_the_default_folder_is_in_the_local_profile(
    conn: Connection, ant: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BF_LOCAL_DIR", str(tmp_path / "local"))
    m = LoginManager(conn)
    started = m.start()
    assert len(_folders(tmp_path / "local" / "tmp")) == 1
    m.cancel(started.login_id)
    assert _folders(tmp_path / "local" / "tmp") == []


# ---- the code ----


def test_a_wrong_code_is_refused_and_nothing_is_kept(
    manager: LoginManager, conn: Connection, tmp_base: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    started = manager.start()
    err = _refused(manager.finish, started.login_id, "WRONG-CODE-999999")
    assert (err.status, err.code) == (422, "assistant_login_code") and err.message and err.hint
    assert conn.snapshot().state == "none" and conn.get_secret() is None
    assert _folders(tmp_base) == [] and not manager.active
    gone = _refused(manager.finish, started.login_id, DEFAULT_CODE)
    assert (gone.status, gone.code) == (404, "assistant_login_gone"), "a wrong code ends the login"
    _assert_no_secret(caplog.text + _text_of(err) + _text_of(gone))


@pytest.mark.parametrize("code", ["", "   ", "a\nb", "a\rb", "a\0b", "x" * 4097])
def test_a_malformed_code_is_422_and_the_login_goes_on(manager: LoginManager, code: str) -> None:
    started = manager.start()
    err = _refused(manager.finish, started.login_id, code)
    assert (err.status, err.code, err.extra) == (422, "bad_param", {"param": "code"})
    assert manager.active
    assert manager.finish(started.login_id, DEFAULT_CODE).kind == "account"


def test_an_account_the_api_does_not_accept_is_not_kept(
    manager: LoginManager,
    conn: Connection,
    tmp_base: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("FAKE_ANT_ACCESS_TOKEN", "sk-ant-oat01-wrong-0000000000000")
    monkeypatch.setenv("FAKE_ANT_REFRESH_TOKEN", "sk-ant-ort01-unknown-0000000")
    started = manager.start()
    err = _refused(manager.finish, started.login_id, DEFAULT_CODE)
    assert err.code == "assistant_bad_key"
    assert conn.snapshot().state == "none" and _folders(tmp_base) == []
    assert "oat01-wrong" not in caplog.text + _text_of(err)


def test_the_folder_is_removed_when_reading_the_profile_fails(
    manager: LoginManager, conn: Connection, tmp_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("crash while reading")

    monkeypatch.setattr(LoginManager, "_read_profile", staticmethod(boom))
    started = manager.start()
    with pytest.raises(RuntimeError):
        manager.finish(started.login_id, DEFAULT_CODE)
    assert _folders(tmp_base) == [] and not manager.active and conn.snapshot().state == "none"


def test_a_missing_profile_after_a_clean_exit_is_login_failed(
    manager: LoginManager, tmp_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = manager.start()
    monkeypatch.setattr(login_mod, "PROFILE", "other-profile")  # `ant` wrote another profile than the one read
    err = _refused(manager.finish, started.login_id, DEFAULT_CODE)
    assert (err.status, err.code) == (502, "assistant_login_failed") and "detail" in err.extra
    assert _folders(tmp_base) == []


# ---- cancel, time ----


def test_cancel_kills_ant_and_removes_the_folder(manager: LoginManager, conn: Connection, tmp_base: Path) -> None:
    started = manager.start()
    proc = manager._session.proc  # type: ignore[union-attr]
    assert proc is not None and proc.poll() is None
    assert manager.cancel("not-this-one") is False and manager.active
    assert manager.cancel(started.login_id) is True
    assert proc.poll() is not None and _folders(tmp_base) == []
    assert conn.snapshot().state == "none" and manager.cancel() is False
    assert _refused(manager.finish, started.login_id, DEFAULT_CODE).code == "assistant_login_gone"


def test_the_whole_login_is_limited_in_time(conn: Connection, ant: Path, tmp_base: Path) -> None:
    m = LoginManager(conn, base_dir=tmp_base, url_timeout=10.0, total_timeout=0.4)
    started = m.start()
    proc = m._session.proc  # type: ignore[union-attr]
    deadline = time.monotonic() + 10
    while m.active and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not m.active and proc is not None and proc.poll() is not None
    assert _folders(tmp_base) == [] and conn.snapshot().state == "none"
    assert _refused(m.finish, started.login_id, DEFAULT_CODE).code == "assistant_login_gone"


def test_a_late_code_after_the_deadline_is_gone(conn: Connection, ant: Path, tmp_base: Path) -> None:
    m = LoginManager(conn, base_dir=tmp_base, total_timeout=0.3)
    started = m.start()
    session = m._session
    assert session is not None
    session.timer.cancel()  # type: ignore[union-attr]  # only the check of `finish` is under test
    time.sleep(0.4)
    assert _refused(m.finish, started.login_id, DEFAULT_CODE).code == "assistant_login_gone"
    assert _folders(tmp_base) == [] and not m.active


def test_no_address_in_time_is_login_failed_and_cleans_up(
    conn: Connection, ant: Path, tmp_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ANT_MODE", "no_url")
    m = LoginManager(conn, base_dir=tmp_base, url_timeout=0.5)
    began = time.monotonic()
    err = _refused(m.start)
    assert time.monotonic() - began < 8
    assert (err.status, err.code) == (502, "assistant_login_failed") and "30" in err.extra["detail"]
    assert _folders(tmp_base) == [] and not m.active and conn.snapshot().state == "none"


def test_ant_that_exits_before_the_address_is_login_failed(
    conn: Connection, ant: Path, tmp_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ANT_MODE", "exit_early")
    m = LoginManager(conn, base_dir=tmp_base, url_timeout=10.0)
    err = _refused(m.start)
    assert err.code == "assistant_login_failed" and "3" in err.extra["detail"] and "cannot start" in err.extra["detail"]
    assert _folders(tmp_base) == [] and conn.snapshot().state == "none"


def test_ant_that_never_reads_the_code_is_login_failed(
    conn: Connection, ant: Path, tmp_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ANT_MODE", "hang")
    m = LoginManager(conn, base_dir=tmp_base, finish_timeout=0.5)
    started = m.start()
    proc = m._session.proc  # type: ignore[union-attr]
    err = _refused(m.finish, started.login_id, DEFAULT_CODE)
    assert (err.status, err.code) == (502, "assistant_login_failed")
    assert proc is not None and proc.poll() is not None and _folders(tmp_base) == [] and not m.active


# ---- no ant ----


def test_without_ant_it_is_501(
    conn: Connection, tmp_base: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("BF_ANT_PATH", raising=False)
    m = LoginManager(conn, base_dir=tmp_base)
    err = _refused(m.start)
    assert (err.status, err.code) == (501, "assistant_no_ant") and err.hint
    assert not m.ant_available() and not ant_available()
    monkeypatch.setenv("BF_ANT_PATH", str(tmp_path / "missing" / "ant"))
    assert _refused(m.start).code == "assistant_no_ant" and ant_path() is None
    assert _folders(tmp_base) == [] and conn.snapshot().state == "none"


def test_ant_is_taken_from_bf_ant_path_only(ant: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert ant_path() == str(ant) and ant_available()
    assert ant_available({"BF_ANT_PATH": str(ant)}) and not ant_available({"PATH": str(ant.parent)})
    monkeypatch.delenv("BF_ANT_PATH")
    monkeypatch.setenv("PATH", str(ant.parent))
    assert not ant_available(), "`ant` in PATH is not used (it may be Apache Ant)"


def test_a_file_that_cannot_be_run_is_login_failed(
    conn: Connection, tmp_base: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad = tmp_path / "ant"
    bad.write_text("not executable", encoding="utf-8")
    bad.chmod(0o644)
    monkeypatch.setenv("BF_ANT_PATH", str(bad))
    m = LoginManager(conn, base_dir=tmp_base)
    err = _refused(m.start)
    assert (err.status, err.code) == (502, "assistant_login_failed")
    assert _folders(tmp_base) == [] and conn.snapshot().state == "none"


# ---- one login at a time ----


def test_a_second_start_while_the_first_waits_for_the_address_is_409(
    conn: Connection, ant: Path, tmp_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ANT_MODE", "no_url")
    m = LoginManager(conn, base_dir=tmp_base, url_timeout=20.0)
    first: dict[str, Any] = {}

    def run() -> None:
        try:
            m.start()
        except ApiError as e:
            first["error"] = e

    t = threading.Thread(target=run)
    t.start()
    deadline = time.monotonic() + 10
    while not m.active and time.monotonic() < deadline:
        time.sleep(0.02)
    err = _refused(m.start)
    assert (err.status, err.code) == (409, "assistant_login_busy") and err.message and err.hint
    assert m.cancel() is True
    t.join(timeout=10)
    assert not t.is_alive() and first["error"].code == "assistant_login_failed"
    assert _folders(tmp_base) == [] and not m.active


def test_a_second_start_while_the_code_is_being_checked_is_409(
    conn: Connection, ant: Path, tmp_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ANT_MODE", "hang")
    m = LoginManager(conn, base_dir=tmp_base, finish_timeout=5.0)
    started = m.start()
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            m.finish(started.login_id, DEFAULT_CODE)
        except ApiError as e:
            result["error"] = e

    t = threading.Thread(target=run)
    t.start()
    deadline = time.monotonic() + 10
    while (m._session is None or m._session.state != "finishing") and time.monotonic() < deadline:
        time.sleep(0.02)
    assert _refused(m.start).code == "assistant_login_busy"
    assert _refused(m.finish, started.login_id, DEFAULT_CODE).code == "assistant_login_busy"
    m.cancel(started.login_id)
    t.join(timeout=10)
    assert not t.is_alive() and _folders(tmp_base) == []


def test_a_new_start_closes_the_login_that_waits_for_its_code(
    manager: LoginManager, conn: Connection, tmp_base: Path
) -> None:
    first = manager.start()
    old = manager._session.proc  # type: ignore[union-attr]
    second = manager.start()
    assert second.login_id != first.login_id
    assert old is not None and old.poll() is not None
    assert len(_folders(tmp_base)) == 1, "the folder of the first login is removed"
    assert _refused(manager.finish, first.login_id, DEFAULT_CODE).code == "assistant_login_gone"
    assert manager.active and conn.snapshot().state == "login"
    assert manager.finish(second.login_id, DEFAULT_CODE).kind == "account"
    assert _folders(tmp_base) == []


def test_an_unknown_login_is_gone(manager: LoginManager) -> None:
    assert _refused(manager.finish, "nope", DEFAULT_CODE).code == "assistant_login_gone"
    started = manager.start()
    assert _refused(manager.finish, "nope", DEFAULT_CODE).code == "assistant_login_gone"
    manager.cancel(started.login_id)


# ---- secrets ----


def test_secrets_are_not_in_logs_errors_or_extra(
    conn: Connection, ant: Path, tmp_base: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    texts: list[str] = []
    m = LoginManager(conn, base_dir=tmp_base, url_timeout=10.0, finish_timeout=1.0)
    started = m.start()
    texts.append(_text_of(_refused(m.finish, started.login_id, "WRONG-CODE-999999")))
    monkeypatch.setenv("FAKE_ANT_MODE", "exit_early")
    texts.append(_text_of(_refused(m.start)))
    monkeypatch.setenv("FAKE_ANT_MODE", "ok")
    started = m.start()
    m.finish(started.login_id, DEFAULT_CODE)
    _assert_no_secret(caplog.text + "\n".join(texts))
    assert conn.get_secret() is not None, "only the connection holds the secret"


def test_the_core_reset_cancels_a_running_login(
    conn: Connection, ant: Path, tmp_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(login_mod, "get_connection", lambda *_: conn)
    monkeypatch.setenv("BF_LOCAL_DIR", str(tmp_base.parent / "local"))
    login_mod.reset_login()
    m = login_mod.get_login()
    assert m is login_mod.get_login() and m.connection is conn
    m.start()
    login_mod.reset_login()
    assert not m.active and _folders(tmp_base.parent / "local" / "tmp") == []
