"""How the built-in assistant reaches Claude: one connection for the whole installation.

Three sources, in this order:
- an API key entered in the application (kept on the server, never sent back to the browser);
- an Anthropic account: an interactive login through the official `ant` CLI (`ant auth login`),
  whose profile the Anthropic SDK reads and refreshes by itself;
- the environment of the server (`ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`), as before.

The connection, the chosen model and effort live in `<DATA_DIR>/assistant/connection.json`
(readable by the server user only), the account profile in `<DATA_DIR>/assistant/anthropic`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic

from app.config import settings

PROFILE = "bedrockforge"
LOGIN_TIMEOUT_S = 600
URL_RE = re.compile(r"https://[^\s\"'<>]+")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,}$")


@dataclass(frozen=True)
class ModelInfo:
    id: str
    title: str
    # USD per million tokens: input, output, cache read; cache write is 1.25 x input
    price_in: float
    price_out: float
    price_cache_read: float
    # whether the API can retry a declined request on a fallback model by itself
    fallbacks: bool
    note: str


MODELS: tuple[ModelInfo, ...] = (
    ModelInfo("claude-opus-5-5", "Claude Opus 5.5", 4.0, 20.0, 0.20, True, "Основная модель: сложный анализ и действия в интерфейсе"),
    ModelInfo("claude-sonnet-5-5", "Claude Sonnet 5.5", 2.0, 10.0, 0.10, True, "Быстрее и вдвое дешевле Opus"),
    ModelInfo("claude-haiku-5-5", "Claude Haiku 5.5", 0.10, 0.50, 0.01, False, "Самая быстрая и дешёвая: простые вопросы"),
    ModelInfo("claude-fable-5-2", "Claude Fable 5.2", 8.0, 40.0, 0.20, True, "Самая сильная модель: долгие сложные задачи, дороже"),
)
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def model_info(model_id: str) -> ModelInfo | None:
    """The model of a response (it may carry a date or be a fallback): matched by its prefix."""
    for m in MODELS:
        if model_id == m.id or model_id.startswith(m.id):
            return m
    return None


def cost_usd(model_id: str, usage: dict[str, int]) -> float | None:
    m = model_info(model_id)
    if m is None:
        return None
    return (
        usage.get("input_tokens", 0) * m.price_in
        + usage.get("output_tokens", 0) * m.price_out
        + usage.get("cache_read_input_tokens", 0) * m.price_cache_read
        + usage.get("cache_creation_input_tokens", 0) * m.price_in * 1.25
    ) / 1_000_000


class ConnectionProblem(Exception):
    """A connection could not be made: the message is for the user."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---- the stored connection

_lock = threading.RLock()
_client: anthropic.Anthropic | None = None
_client_key: tuple | None = None


def _dir() -> Path:
    d = settings.data_dir / "assistant"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_dir() -> Path:
    """Where the `ant` profile of the account login lives (ANTHROPIC_CONFIG_DIR)."""
    d = _dir() / "anthropic"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path() -> Path:
    return _dir() / "connection.json"


def load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: dict[str, Any]) -> None:
    path = _path()
    tmp = path.with_suffix(".tmp")
    # The file holds a key: only the server user may read it.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)
    global _client, _client_key
    _client, _client_key = None, None


def _env_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _profile_exists() -> bool:
    d = config_dir()
    return (d / "configs" / f"{PROFILE}.json").is_file() and (d / "credentials" / f"{PROFILE}.json").is_file()


def mode() -> str | None:
    """"key", "account", "env" or None (not connected)."""
    data = load()
    if data.get("mode") == "key" and data.get("api_key"):
        return "key"
    if data.get("mode") == "account" and _profile_exists():
        return "account"
    if _env_credentials():
        return "env"
    return None


def model() -> str:
    m = load().get("model")
    return m if isinstance(m, str) and model_info(m) else settings.assistant_model


def effort() -> str:
    e = load().get("effort")
    return e if e in EFFORTS else settings.assistant_effort


def masked(key: str) -> str:
    return f"{key[:10]}…{key[-4:]}" if len(key) > 16 else "…"


def client() -> anthropic.Anthropic:
    """The client of the current connection (made again when the connection changes)."""
    global _client, _client_key
    with _lock:
        m = mode()
        data = load()
        key = (m, data.get("api_key"), data.get("connected_at"))
        if _client is not None and _client_key == key:
            return _client
        if m == "key":
            _client = anthropic.Anthropic(api_key=data["api_key"])
        elif m == "account":
            os.environ["ANTHROPIC_CONFIG_DIR"] = str(config_dir())
            try:
                _client = anthropic.Anthropic(profile=PROFILE)
            except anthropic.AnthropicError as e:
                raise ConnectionProblem(f"Профиль входа Anthropic не читается: войдите заново ({e})", 503) from e
        elif m == "env":
            _client = anthropic.Anthropic()
        else:
            raise ConnectionProblem("Claude не подключён: войдите в аккаунт Anthropic или введите API-ключ (окно «Claude: подключение»)", 503)
        _client_key = key
        return _client


def check(c: anthropic.Anthropic | None = None, model_id: str | None = None) -> str:
    """A tiny request: does the connection work and see the model? Returns the model's display name."""
    c = c or client()
    target = model_id or model()
    try:
        info = c.models.retrieve(target)
        return getattr(info, "display_name", None) or target
    except anthropic.AuthenticationError as e:
        raise ConnectionProblem("Anthropic отклонил ключ или вход: подключитесь заново", 401) from e
    except anthropic.PermissionDeniedError as e:
        raise ConnectionProblem(f"У этого подключения нет доступа к модели {target}", 403) from e
    except anthropic.NotFoundError as e:
        raise ConnectionProblem(f"Модель {target} не найдена", 404) from e
    except anthropic.RateLimitError as e:
        raise ConnectionProblem("Превышен лимит запросов Claude API, повторите позже", 429) from e
    except anthropic.APIStatusError as e:
        raise ConnectionProblem(f"Ошибка Claude API ({e.status_code}): {e.message}", 502) from e
    except anthropic.APIConnectionError as e:
        raise ConnectionProblem("Нет связи с api.anthropic.com: проверьте интернет и прокси", 502) from e
    except anthropic.AnthropicError as e:  # the credentials of the profile could not be read or refreshed
        raise ConnectionProblem(f"Вход Anthropic не работает: {e}", 401) from e


def connect_key(api_key: str) -> str:
    key = api_key.strip()
    if not KEY_RE.match(key):
        raise ConnectionProblem("Это не похоже на ключ Claude API: он начинается с sk-ant- (console.anthropic.com → API Keys)")
    name = check(anthropic.Anthropic(api_key=key))
    with _lock:
        data = load()
        data.update(mode="key", api_key=key, connected_at=time.time(), account=None)
        save(data)
    return name


def set_preferences(model_id: str | None, effort_level: str | None) -> None:
    with _lock:
        data = load()
        if model_id is not None:
            if model_info(model_id) is None:
                raise ConnectionProblem(f"Неизвестная модель: {model_id}")
            data["model"] = model_id
        if effort_level is not None:
            if effort_level not in EFFORTS:
                raise ConnectionProblem(f"Неизвестная глубина рассуждений: {effort_level}")
            data["effort"] = effort_level
        save(data)


def disconnect() -> None:
    with _lock:
        data = load()
        was = data.get("mode")
        data.update(mode=None, api_key=None, account=None, connected_at=None)
        save(data)
    if was == "account":
        if ant_path():
            try:
                subprocess.run([ant_path(), "auth", "logout", "--profile", PROFILE], env=_ant_env(), capture_output=True, timeout=30, check=False)
            except (OSError, subprocess.SubprocessError):
                pass
        shutil.rmtree(config_dir(), ignore_errors=True)


def status() -> dict[str, Any]:
    data = load()
    m = mode()
    out: dict[str, Any] = {
        "configured": m is not None, "mode": m, "model": model(), "effort": effort(),
        "models": [{"id": x.id, "title": x.title, "note": x.note, "price_in": x.price_in, "price_out": x.price_out} for x in MODELS],
        "efforts": list(EFFORTS),
        "account_login": ant_path() is not None,
        "key": masked(data["api_key"]) if m == "key" else None,
        "account": data.get("account") if m == "account" else None,
        "connected_at": data.get("connected_at") if m in ("key", "account") else None,
        "login_pending": _login is not None and _login.alive(),
    }
    return out


# ---- the account login: `ant auth login --no-browser`, driven through a pseudo-terminal


def ant_path() -> str | None:
    return shutil.which("ant")


def _ant_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")}
    env["ANTHROPIC_CONFIG_DIR"] = str(config_dir())
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    return env


@dataclass
class _Login:
    id: str
    proc: subprocess.Popen
    master: int
    started: float = field(default_factory=time.time)
    output: str = ""

    def alive(self) -> bool:
        return self.proc.poll() is None and time.time() - self.started < LOGIN_TIMEOUT_S

    def read(self, seconds: float) -> None:
        import select

        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self.master], [], [], max(0.0, end - time.time()))
            if not ready:
                break
            try:
                chunk = os.read(self.master, 4096)
            except OSError:
                break
            if not chunk:
                break
            self.output += ANSI_RE.sub("", chunk.decode("utf-8", "replace"))

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            os.close(self.master)
        except OSError:
            pass


_login: _Login | None = None


def _tail(text: str, lines: int = 6) -> str:
    return "\n".join([x.strip() for x in text.strip().splitlines() if x.strip()][-lines:])


def login_start() -> dict[str, str]:
    """Starts the account login; returns the address the user opens to sign in."""
    global _login
    exe = ant_path()
    if exe is None:
        raise ConnectionProblem("Вход аккаунтом недоступен: в контейнере api нет утилиты ant. Пересоберите api (BedrockForge.bat) или введите API-ключ", 501)
    import pty

    with _lock:
        if _login is not None:
            _login.close()
        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen(
                [exe, "auth", "login", "--no-browser", "--profile", PROFILE],
                stdin=slave, stdout=slave, stderr=slave, env=_ant_env(), close_fds=True, start_new_session=True,
            )
        finally:
            os.close(slave)
        login = _Login(uuid.uuid4().hex, proc, master)
        _login = login
    # The CLI prints the address to open, then waits for the code.
    deadline = time.time() + 20
    url = None
    while time.time() < deadline and login.proc.poll() is None:
        login.read(0.5)
        found = URL_RE.findall(login.output)
        if found:
            login.read(0.3)  # the rest of the prompt
            url = URL_RE.findall(login.output)[0].rstrip(".,)")
            break
    if url is None:
        login.close()
        raise ConnectionProblem(f"ant не выдал адрес для входа: {_tail(login.output) or 'нет ответа'}", 502)
    return {"login_id": login.id, "url": url}


def login_finish(login_id: str, code: str) -> tuple[str, bool]:
    """Passes the code from the sign-in page to the CLI; on success the account is the connection."""
    global _login
    login = _login
    if login is None or login.id != login_id or not login.alive():
        raise ConnectionProblem("Вход устарел: начните заново", 409)
    code = code.strip()
    if not code or len(code) > 4096 or any(ch in code for ch in "\r\n\x00"):
        raise ConnectionProblem("Вставьте код со страницы входа целиком")
    before = len(login.output)
    os.write(login.master, (code + "\n").encode())
    deadline = time.time() + 60
    while time.time() < deadline and login.proc.poll() is None:
        login.read(0.5)
    if login.proc.poll() is None:
        login.close()
        _login = None
        raise ConnectionProblem(f"ant не завершил вход: {_tail(login.output[before:]) or 'нет ответа'}", 502)
    login.read(0.2)
    rc = login.proc.returncode
    message = _tail(login.output[before:])
    login.close()
    _login = None
    if rc != 0 or not _profile_exists():
        raise ConnectionProblem(f"Вход не выполнен: {message or f'код возврата {rc}'}", 400)
    with _lock:
        data = load()
        data.update(mode="account", api_key=None, connected_at=time.time(), account=_account_summary())
        save(data)
    try:
        return f"Вход выполнен. Модель: {check()}", True
    except ConnectionProblem as e:
        # The sign-in itself succeeded: the account stays connected, the user sees why the test request failed.
        return f"Вход выполнен, но проверочный запрос к Claude не прошёл: {e}", False


def login_cancel() -> None:
    global _login
    with _lock:
        if _login is not None:
            _login.close()
            _login = None


def _account_summary() -> dict[str, str] | None:
    """What `ant auth status` says about the profile (organization, workspace), as short lines."""
    exe = ant_path()
    if exe is None:
        return None
    try:
        r = subprocess.run([exe, "auth", "status", "--profile", PROFILE], env=_ant_env(), capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [ANSI_RE.sub("", x).strip() for x in (r.stdout + "\n" + r.stderr).splitlines()]
    out: dict[str, str] = {}
    for line in lines:
        m = re.match(r"^([A-Za-z][A-Za-z _-]{1,40}):\s*(.+)$", line)
        if m and not re.search(r"token|secret|key", m.group(1), re.I):
            out[m.group(1).strip().lower()] = m.group(2).strip()[:200]
    return out or None
