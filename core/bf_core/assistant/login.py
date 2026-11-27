"""Sign-in with an Anthropic account through the `ant` utility (подшаг 23.5; ТЗ 7.32; ADR 0029 решение 11; e23-api §3).

    ant_path(env) / ant_available(env)   `BF_ANT_PATH` only (no search in PATH: `ant` there may be Apache Ant)
    LoginManager(connection).start()     `POST /login/start` -> `LoginStart` {login_id, url, expires_at}
    LoginManager.finish(login_id, code)  `POST /login/{id}/finish` -> `Snapshot` (after `connection.set_account`)
    LoginManager.cancel(login_id=None)   `DELETE /login/{id}`
    get_login() / reset_login()          the process-wide manager on `get_connection()`

`ant auth login --no-browser --profile bedrockforge` runs with plain pipes (no pty: Windows has none). Its folder of
the profile (`ANTHROPIC_CONFIG_DIR`) is a NEW temporary folder under `%LOCALAPPDATA%\\BedrockForge\\tmp`; it is removed
in every case (success, wrong code, timeout, cancel, a crash), right after the tokens are read. The tokens go to
`Connection.set_account`; nothing of the profile stays on the disk. The first `https://` address of the output is the
sign-in address (<= 30 s); the code is written to stdin by `finish`. A login lives <= 10 minutes; one at a time.

Concurrency: while a login is being started (waiting for the address) or finished, a second `start` is refused (409
`assistant_login_busy`); a login that only waits for the code is closed by a new `start` ("a new start closes the
previous", e23-api §3). A secret (token, code) is never put in a log, a message or `extra` of an error.
"""

from __future__ import annotations

import codecs
import contextlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bf_core import paths
from bf_core.assistant import errors
from bf_core.assistant.connection import Connection, Snapshot, get_connection, scrub
from bf_core.assistant.schemas import LoginStart
from bf_core.assistant.schemas_base import CODE_MAX

log = logging.getLogger("bf_core.assistant")

ENV_ANT_PATH = "BF_ANT_PATH"
PROFILE = "bedrockforge"
URL_TIMEOUT_S = 30.0  # the address of the sign-in must appear in the output
TOTAL_TIMEOUT_S = 600.0  # the whole login: start -> finish
FINISH_TIMEOUT_S = 60.0  # `ant` after the code: exchange of the code, writing of the profile
CLEANUP_TRIES = 20
OUTPUT_LIMIT = 64 * 1024  # of the output of `ant` only the tail is kept
# The environment of `ant` is an allow-list: nothing else of the core's environment (tokens, keys, paths) reaches it.
CHILD_ENV_NAMES = frozenset(
    {
        "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "LANG", "NO_COLOR",
        "TERM",
    }
)  # fmt: skip
CHILD_ENV_PREFIXES = ("LC_", "SSL_CERT_", "FAKE_ANT_")  # FAKE_ANT_*: the test double of `ant` (tests/assistant)

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_URL_DONE = re.compile(r"https://[^\s<>\"']+(?=\s)")  # the address is complete when whitespace follows
_URL_END = re.compile(r"https://[^\s<>\"']+")  # ... or when the process is over


def ant_path(env: Mapping[str, str] | None = None) -> str | None:
    """The path of `ant` from `BF_ANT_PATH` (the shell sets it to `resources\\ant\\ant.exe`) if the file exists."""
    value = (os.environ if env is None else env).get(ENV_ANT_PATH)
    if value and Path(value).is_file():
        return str(Path(value).resolve())  # absolute: `ant` runs with `cwd` = its profile folder
    return None


def ant_available(env: Mapping[str, str] | None = None) -> bool:
    """`ant_available` of `GET /status`."""
    return ant_path(env) is not None


def _iso(unix: float) -> str:
    return datetime.fromtimestamp(unix, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tail(text: str, lines: int = 4, chars: int = 300) -> str:
    kept = [x.strip() for x in _ANSI.sub("", text).splitlines() if x.strip()]
    return "\n".join(kept[-lines:])[-chars:]


def _remove_tree(folder: Path) -> None:
    """Removes the profile folder; on Windows a just-killed process may hold a file for a moment."""
    for attempt in range(CLEANUP_TRIES):
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            return
        time.sleep(0.05 * (attempt + 1))
    # the next start of the core (or of a login) removes it: `sweep_stale`; the name holds no secret
    log.warning("assistant login: the profile folder %s could not be removed, it will be removed later", folder.name)


class _Session:
    """One run of `ant`: the process, its output and its temporary profile folder."""

    def __init__(self, folder: Path, total_timeout: float) -> None:
        self.id = uuid.uuid4().hex
        self.folder = folder
        self.deadline = time.monotonic() + total_timeout
        self.expires_at = _iso(time.time() + total_timeout)
        self.proc: subprocess.Popen[bytes] | None = None
        self.output = ""
        self.cond = threading.Condition()
        self.readers: list[threading.Thread] = []
        self.timer: threading.Timer | None = None
        self.url: str | None = None
        self.state = "starting"  # starting | waiting | finishing | closed
        self.closed = False

    def _pump(self, stream: Any) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                chunk = stream.read1(4096)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                with self.cond:
                    self.output = (self.output + text)[-OUTPUT_LIMIT:]
                    self.cond.notify_all()
        except (OSError, ValueError):
            pass
        finally:
            with self.cond:
                self.cond.notify_all()

    def spawn(self, argv: list[str], env: dict[str, str]) -> None:
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(  # noqa: S603 - the path is BF_ANT_PATH of the shell, the arguments are fixed
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(self.folder),
            **kwargs,
        )
        for stream in (self.proc.stdout, self.proc.stderr):
            t = threading.Thread(target=self._pump, args=(stream,), name="ant-login-reader", daemon=True)
            t.start()
            self.readers.append(t)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def wait_url(self, timeout: float) -> str | None:
        """The first address of the output, or None (the process is over, the session was closed or time is up)."""
        end = time.monotonic() + timeout
        with self.cond:
            while True:
                found = _URL_DONE.search(_ANSI.sub("", self.output))
                if found is None and not self.alive():
                    found = _URL_END.search(_ANSI.sub("", self.output))
                if found:
                    return found.group(0).rstrip(".,);")
                left = end - time.monotonic()
                if left <= 0 or self.closed or not self.alive():
                    return None
                self.cond.wait(min(left, 0.2))

    def drain(self) -> None:
        for t in self.readers:
            t.join(timeout=2.0)

    def kill(self) -> None:
        proc = self.proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                if sys.platform == "win32":
                    proc.kill()
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                with contextlib.suppress(OSError):
                    proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=5.0)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                with contextlib.suppress(OSError, ValueError):
                    stream.close()

    def close(self) -> None:
        """Kills the process and removes the profile folder. Safe to call twice."""
        with self.cond:
            if self.closed:
                return
            self.closed = True
            self.state = "closed"
            self.cond.notify_all()
        if self.timer is not None:
            self.timer.cancel()
        try:
            self.kill()
        finally:
            self.drain()
            _remove_tree(self.folder)


class LoginManager:
    """The login of one `Connection`; the timeouts are parameters for the tests."""

    def __init__(
        self,
        connection: Connection,
        *,
        env: Mapping[str, str] | None = None,
        base_dir: str | os.PathLike[str] | None = None,
        url_timeout: float = URL_TIMEOUT_S,
        total_timeout: float = TOTAL_TIMEOUT_S,
        finish_timeout: float = FINISH_TIMEOUT_S,
    ) -> None:
        self.connection = connection
        self._env = env
        self._base_dir = Path(base_dir) if base_dir is not None else None
        self.url_timeout, self.total_timeout, self.finish_timeout = url_timeout, total_timeout, finish_timeout
        self._lock = threading.RLock()
        self._session: _Session | None = None
        self._swept = False

    # ---- environment ----
    def _environ(self) -> Mapping[str, str]:
        return os.environ if self._env is None else self._env

    def ant_available(self) -> bool:
        return ant_path(self._environ()) is not None

    def _base(self) -> Path:
        return self._base_dir or paths.local_app_dir() / "tmp"

    def _new_folder(self) -> Path:
        base = self._base()
        base.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="ant-", dir=base))  # 0700 on POSIX; the name is random

    def sweep_stale(self, *, everything: bool = False) -> int:
        """Removes the `ant-*` profile folders left by a killed core or a failed removal (they hold the tokens in
        the clear): all of them at the start of the core (`everything`), else those older than a login can live."""
        try:
            found = [p for p in self._base().glob("ant-*") if p.is_dir()]
        except OSError:
            return 0
        with self._lock:
            live = self._session.folder if self._session is not None else None
        limit = time.time() - self.total_timeout
        removed = 0
        for folder in found:
            try:
                if folder == live or (not everything and folder.stat().st_mtime > limit):
                    continue
            except OSError:
                continue
            _remove_tree(folder)
            removed += not folder.exists()
        if removed:
            log.info("assistant login: %d stale profile folder(s) removed", removed)
        return removed

    def _child_env(self, folder: Path) -> dict[str, str]:
        env = {
            k: v
            for k, v in self._environ().items()
            if k.upper() in CHILD_ENV_NAMES or k.upper().startswith(CHILD_ENV_PREFIXES)
        }
        env["ANTHROPIC_CONFIG_DIR"] = str(folder)
        env.update(NO_COLOR="1", TERM="dumb", PYTHONUNBUFFERED="1")
        return env

    # ---- state ----
    @property
    def active(self) -> bool:
        with self._lock:
            return self._session is not None

    def _release(self, session: _Session) -> None:
        """Closes `session` (always removing its folder) and, if it is the current one, forgets it."""
        try:
            session.close()
        finally:
            with self._lock:
                if self._session is session:
                    self._session = None
                    self.connection.set_login(False)

    def _expire(self, session: _Session) -> None:
        with self._lock:
            if session.state == "finishing":
                return  # `finish` is bounded by the same deadline
        log.info("assistant login: the session expired")
        self._release(session)

    # ---- start ----
    def start(self) -> LoginStart:
        """Runs `ant`, waits <= 30 s for the sign-in address. 501 without `ant`, 409 while another login is busy."""
        exe = ant_path(self._environ())
        if exe is None:
            raise errors.refuse("assistant_no_ant")
        with self._lock:
            current = self._session
            if current is not None and current.state in ("starting", "finishing"):
                raise errors.refuse("assistant_login_busy")
            if not self._swept:
                self._swept = True
                self.sweep_stale()
            folder = self._new_folder()
            session = _Session(folder, self.total_timeout)
            previous, self._session = current, session
            self.connection.set_login(True)
        if previous is not None:  # a new start closes the one that only waited for its code
            previous.close()
        try:
            session.timer = threading.Timer(self.total_timeout, self._expire, args=(session,))
            session.timer.daemon = True
            session.timer.start()
            try:
                session.spawn([exe, "auth", "login", "--no-browser", "--profile", PROFILE], self._child_env(folder))
            except OSError as e:
                raise errors.refuse("assistant_login_failed", detail=f"ant не запустился: {type(e).__name__}") from e
            url = session.wait_url(self.url_timeout)
            if url is None:
                if session.closed:
                    raise errors.refuse("assistant_login_failed", detail="вход отменён")
                if session.alive():
                    detail = "ant не выдал адрес для входа за 30 с"
                else:
                    session.drain()
                    code = session.proc.returncode if session.proc else None
                    detail = f"ant завершился с кодом {code}: {_tail(session.output) or 'нет вывода'}"
                raise errors.refuse("assistant_login_failed", detail=scrub(detail))
            with self._lock:
                if session.closed:
                    raise errors.refuse("assistant_login_failed", detail="вход отменён")
                session.url, session.state = url, "waiting"
        except BaseException:
            self._release(session)
            raise
        log.info("assistant login: started")
        return LoginStart(login_id=session.id, url=url, expires_at=session.expires_at)

    # ---- finish ----
    def finish(self, login_id: str, code: str) -> Snapshot:
        """Writes the code to `ant`, reads the profile, connects the account. The login is over after this call
        (the folder is removed), whatever the result: a wrong code needs a new start."""
        value = code.strip() if isinstance(code, str) else ""
        if not value or len(value) > CODE_MAX or any(c in value for c in "\r\n\0"):
            raise errors.bad_param("code")
        with self._lock:
            session = self._session
            if session is None or session.id != login_id or session.state == "closed":
                raise errors.refuse("assistant_login_gone")
            if session.state != "waiting":
                raise errors.refuse("assistant_login_busy")
            if time.monotonic() >= session.deadline:
                expired = session
            else:
                expired = None
                session.state = "finishing"
        if expired is not None:
            self._release(expired)
            raise errors.refuse("assistant_login_gone")
        try:
            profile = self._run_finish(session, value)
            try:
                self.connection.set_account(**profile)
            finally:
                profile.clear()
        finally:
            self._release(session)
        log.info("assistant login: the account is connected")
        return self.connection.snapshot()  # after the release: `state` is `connected`, not `login`

    def _run_finish(self, session: _Session, code: str) -> dict[str, Any]:
        proc = session.proc
        assert proc is not None and proc.stdin is not None
        try:
            proc.stdin.write((code + "\n").encode("utf-8"))
            proc.stdin.flush()
        except OSError:
            pass  # `ant` is already gone: its exit code tells why
        timeout = max(0.1, min(self.finish_timeout, session.deadline - time.monotonic()))
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise errors.refuse(
                "assistant_login_failed", detail="ant не завершил вход вовремя", message="Вход не завершён вовремя."
            ) from None
        session.drain()
        if rc != 0:
            log.info("assistant login: ant exited with code %s", rc)
            raise errors.refuse("assistant_login_code")
        return self._read_profile(session.folder, session.output, code)

    @staticmethod
    def _read_profile(folder: Path, output: str, code: str) -> dict[str, Any]:
        """`configs/<P>.json` + `credentials/<P>.json` of the `user_oauth` profile -> the arguments of `set_account`."""
        try:
            config = json.loads((folder / "configs" / f"{PROFILE}.json").read_text(encoding="utf-8"))
            creds = json.loads((folder / "credentials" / f"{PROFILE}.json").read_text(encoding="utf-8"))
            auth = config["authentication"]
            ok = (
                auth["type"] == "user_oauth"
                and creds["access_token"]
                and creds["refresh_token"]
                and auth["client_id"]
                and isinstance(creds["access_token"], str)
            )
        except (OSError, ValueError, KeyError, TypeError):
            ok = False
        if not ok:
            detail = scrub(f"ant завершился без профиля входа: {_tail(output) or 'нет вывода'}", code)
            raise errors.refuse("assistant_login_failed", detail=detail)
        return {
            "access_token": creds["access_token"],
            "refresh_token": creds["refresh_token"],
            "client_id": auth["client_id"],
            "expires_at": creds.get("expires_at"),
            "organization_id": config.get("organization_id") or None,
            "workspace_id": config.get("workspace_id") or None,
        }

    # ---- cancel ----
    def cancel(self, login_id: str | None = None) -> bool:
        """`DELETE /login/{id}`: ends the login (kills `ant`, removes the folder). True if there was one."""
        with self._lock:
            session = self._session
            if session is None or (login_id is not None and session.id != login_id):
                return False
        self._release(session)
        return True

    def close(self) -> None:
        """Shutdown of the core / a test: the same as `cancel()`."""
        self.cancel()


_manager: LoginManager | None = None
_manager_lock = threading.Lock()


def get_login() -> LoginManager:
    """The process-wide manager on `get_connection()`."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = LoginManager(get_connection())
            _manager.sweep_stale(everything=True)  # the start of the core: no login of this process exists yet
            _manager._swept = True
        return _manager


def reset_login() -> None:
    """Tests: cancels a running login and forgets the manager."""
    global _manager
    with _manager_lock:
        manager, _manager = _manager, None
    if manager is not None:
        manager.close()


__all__ = ["LoginManager", "ant_available", "ant_path", "get_login", "reset_login"]
