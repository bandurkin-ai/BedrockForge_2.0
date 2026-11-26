"""Connection of the assistant to the Claude API (подшаг 23.4; ТЗ 7.32; ADR 0029 решения 6, 8-12; e23-api §1-3, §12).

One process-wide `Connection` (`get_connection()`); a test builds its own `Connection(data_dir, env)`.

    conn.snapshot()            state, kind, mask, organization, workspace, online, checked_at, seal_needed, revision
    conn.status(ant_available=)  `schemas.Status` of `GET /status` (the secret is not in it)
    conn.set_key(key)          `POST /connection/key`: format -> `models.retrieve` BEFORE saving -> memory
    conn.set_account(...)      the result of `ant` login (23.5): `MemoryOAuth` + the same check
    conn.test()                `POST /connection/test` (never raises for an API failure: `ok: false` + message)
    conn.forget()              `DELETE /connection`
    conn.put_secret(...) / get_secret() / mark_sealed(rev)   the exchange with the main process (shell only)
    conn.client(...)           the SDK client: ALWAYS explicit credentials and `base_url`
    conn.api_error(exc, model) an SDK / token failure -> `ApiError` of e23-api.md §12
    conn.note_call(exc)        the loop reports the result of a call: `online`, `checked_at`
    conn.add_listener(fn)      `fn()` after every change of the visible state (the route sends `assistant.connection`)

Sources, in order: the key or login given in the window (kept in memory only) -> `ANTHROPIC_API_KEY` of the
environment, only while the window gave nothing (`kind: "env"`) -> not connected. The SDK's own search of credentials
(`ANTHROPIC_PROFILE`, `ANTHROPIC_AUTH_TOKEN`, profiles in `%APPDATA%\\Anthropic`, `ANTHROPIC_BASE_URL`) is never used:
the client gets `api_key=` or `credentials=` and `base_url=` explicitly. The address is `https://api.anthropic.com`;
`BF_ASSISTANT_BASE_URL` replaces it for 127.0.0.1 only (mocks of tests and E2E). A secret is never put in a log, a
text of an error, `extra` of an error, a status or an event: `scrub()` cuts out what looks like one.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import anthropic

from bf_core.api.errors import ApiError
from bf_core.assistant import errors, prefs, prices
from bf_core.assistant.oauth import MemoryOAuth, OAuthRefreshError
from bf_core.assistant.schemas import Status
from bf_core.assistant.schemas_base import EFFORTS, KEY_RE

log = logging.getLogger("bf_core.assistant")

DEFAULT_BASE_URL = "https://api.anthropic.com"
ENV_API_KEY = "ANTHROPIC_API_KEY"
ENV_BASE_URL = "BF_ASSISTANT_BASE_URL"
ORG_HEADER = "anthropic-organization-id"
WORKSPACE_HEADER = "anthropic-workspace-id"
MAX_RETRIES = 2  # the SDK retries 429, 5xx and a lost connection before the first event (ADR 0029 решение 6)
#: connect 5 s, read 90 s (between the events of a stream), write 30 s (ADR 0029 решение 6)
TIMEOUT = anthropic.Timeout(connect=5.0, read=90.0, write=30.0, pool=30.0)
CHECK_TIMEOUT = anthropic.Timeout(connect=5.0, read=30.0, write=30.0, pool=30.0)  # a check of the key / of the link

Kind = Literal["api_key", "env", "account"]
_SECRET_LIKE = re.compile(r"sk-ant-[A-Za-z0-9_-]{6,}")

# The SDK and its HTTP layer log requests at DEBUG: keep them quiet, whatever the root logger is (ADR 0029 решение 12).
for _name in ("anthropic", "httpx2", "httpcore2", "httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def scrub(text: str, *secrets: str | None) -> str:
    """`text` without a secret: the given values and anything that looks like an Anthropic key or token."""
    for s in secrets:
        if s and len(s) >= 8:
            text = text.replace(s, "…")
    return _SECRET_LIKE.sub("sk-ant-…", text)


def mask_key(key: str) -> str:
    """`sk-ant-…` + the last 4 characters; the key is never shown whole."""
    return f"sk-ant-…{key[-4:]}" if key.startswith("sk-ant-") else f"…{key[-4:]}"


def resolve_base_url(env: Mapping[str, str] | None = None) -> str:
    """`https://api.anthropic.com`, or `BF_ASSISTANT_BASE_URL` when its host is 127.0.0.1 (anything else is ignored)."""
    raw = (os.environ if env is None else env).get(ENV_BASE_URL, "").strip()
    if not raw:
        return DEFAULT_BASE_URL
    try:
        parts = urlsplit(raw)
        port = parts.port
        ok = (
            parts.scheme in ("http", "https")
            and parts.hostname == "127.0.0.1"
            and not (parts.username or parts.password)
        )
    except ValueError:
        ok = False
    if not ok:
        log.warning("assistant: %s is not on 127.0.0.1, the address of the Claude API is used", ENV_BASE_URL)
        return DEFAULT_BASE_URL
    return f"{parts.scheme}://127.0.0.1{f':{port}' if port else ''}{parts.path.rstrip('/')}"


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unix(value: Any) -> int | None:
    """`expires_at` as unix seconds from unix seconds or an ISO 8601 string; None - no expiry known."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


def _iso(unix: int | None) -> str | None:
    return None if unix is None else datetime.fromtimestamp(unix, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Snapshot:
    """What the window may know: no secret."""

    state: Literal["none", "connected", "login"]
    kind: Kind | None
    mask: str | None
    organization: dict[str, Any] | None
    workspace: dict[str, Any] | None
    online: bool | None
    checked_at: str | None
    seal_needed: bool
    revision: int
    secure_storage: bool


class Connection:
    """The connection: the secret lives in this object (memory) only. Thread-safe; listeners run outside locks."""

    def __init__(self, data_dir: str | os.PathLike[str] | None = None, env: Mapping[str, str] | None = None) -> None:
        self.data_dir = data_dir
        self._env = env
        self._lock = threading.RLock()
        self._listeners: list[Callable[[], None]] = []
        self._kind: Literal["api_key", "account"] | None = None  # the connection made in the window
        self._key: str | None = None
        self._oauth: MemoryOAuth | None = None
        self._org: str | None = None
        self._workspace: str | None = None
        self._login = False
        self._online: bool | None = None
        self._checked_at: str | None = None
        self._seal_needed = False
        self._revision = 0
        self._secure_storage = True  # the shell tells otherwise with `PUT /connection/secret`
        self._cached: tuple[Any, anthropic.Anthropic] | None = None

    # ---- listeners ----
    def add_listener(self, fn: Callable[[], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def _notify(self) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn()
            except Exception:  # noqa: BLE001 - a listener must not break a connection change
                log.exception("assistant: a listener of the connection failed")

    # ---- sources ----
    def _environ(self) -> Mapping[str, str]:
        return os.environ if self._env is None else self._env

    def _env_key(self) -> str | None:
        value = self._environ().get(ENV_API_KEY, "").strip()
        return value or None

    def _effective(self) -> tuple[Kind | None, str | None]:
        """(kind, api key) of the connection that is in force: the window first, then the environment."""
        if self._kind == "account" and self._oauth is not None:
            return "account", None
        if self._kind == "api_key" and self._key:
            return "api_key", self._key
        env_key = self._env_key()
        return ("env", env_key) if env_key else (None, None)

    @property
    def base_url(self) -> str:
        return resolve_base_url(self._environ())

    def is_connected(self) -> bool:
        with self._lock:
            return self._effective()[0] is not None

    # ---- the visible state ----
    def snapshot(self) -> Snapshot:
        with self._lock:
            kind, key = self._effective()
            state: Literal["none", "connected", "login"] = "login" if self._login else ("connected" if kind else "none")
            org = {"id": self._org, "name": None} if kind and self._org else None
            ws = {"id": self._workspace, "name": None} if kind == "account" and self._workspace else None
            return Snapshot(
                state=state,
                kind=kind,
                mask=mask_key(key) if key else None,
                organization=org,
                workspace=ws,
                online=self._online if kind else None,
                checked_at=self._checked_at if kind else None,
                seal_needed=self._seal_needed and self._kind is not None,
                revision=self._revision,
                secure_storage=self._secure_storage,
            )

    def status(self, *, ant_available: bool = False) -> Status:
        """`GET /status`: the connection + model and effort (prefs) + the table of prices."""
        s = self.snapshot()
        r = prefs.resolve(self.data_dir, self._environ())
        return Status.model_validate(
            {
                "state": s.state,
                "kind": s.kind,
                "mask": s.mask,
                "organization": s.organization,
                "workspace": s.workspace,
                "online": s.online,
                "checked_at": s.checked_at,
                "model": r.model,
                "model_source": r.model_source,
                "effort": r.effort,
                "effort_source": r.effort_source,
                "ant_available": ant_available,
                "secure_storage": s.secure_storage,
                "seal_needed": s.seal_needed,
                "subscription_note": errors.TEXT_SUBSCRIPTION,
                "models": prices.models_payload(),
                "efforts": prices.efforts_payload(),
                "prices_version": prices.PRICES_VERSION,
            }
        )

    def bus_event(self) -> dict[str, Any]:
        """`assistant.connection` of the bus (e23-api.md §1): no secret."""
        s = self.snapshot()
        model = prefs.resolve(self.data_dir, self._environ()).model
        return {
            "state": s.state,
            "kind": s.kind,
            "mask": s.mask,
            "model": model,
            "online": s.online,
            "seal_needed": s.seal_needed,
        }

    def set_login(self, active: bool) -> None:
        """`login.py` (23.5): a login is in progress (state `login`) or it is over."""
        with self._lock:
            changed, self._login = self._login != active, active
        if changed:
            self._notify()

    def note_call(self, exc: BaseException | None = None) -> None:
        """The result of a call of the API: `online` is false only if the API was not reached."""
        online = not self._unreachable(exc) if exc is not None else True
        with self._lock:
            changed = self._online != online
            self._online, self._checked_at = online, _now_iso()
        if changed:
            self._notify()

    @staticmethod
    def _unreachable(exc: BaseException) -> bool:
        refresh = _find_refresh_error(exc)
        if refresh is not None:
            return refresh.offline
        return isinstance(exc, anthropic.APIConnectionError)

    # ---- the SDK client ----
    def client(
        self, *, timeout: anthropic.Timeout | float | None = None, max_retries: int | None = None
    ) -> anthropic.Anthropic:
        """The SDK client of the connection in force; ALWAYS with explicit credentials and `base_url`.

        A client is kept while the connection and the address are the same (one pool of connections); `timeout` /
        `max_retries` give a copy that shares it. The caller does not close it. Not connected: 409
        `assistant_not_connected`."""
        with self._lock:
            kind, key = self._effective()
            if kind is None:
                raise errors.refuse("assistant_not_connected")
            base_url = self.base_url
            token = (kind, key, id(self._oauth) if kind == "account" else None, base_url)
            if self._cached is None or self._cached[0] != token:
                self._cached = (token, self._build(kind, key, base_url, TIMEOUT, MAX_RETRIES))
            client = self._cached[1]
        if timeout is None and max_retries is None:
            return client
        return client.copy(
            timeout=timeout if timeout is not None else anthropic.NOT_GIVEN,
            max_retries=max_retries if max_retries is not None else anthropic.NOT_GIVEN,
        )

    def _build(
        self,
        kind: Kind,
        key: str | None,
        base_url: str,
        timeout: anthropic.Timeout,
        max_retries: int,
        *,
        oauth: MemoryOAuth | None = None,
        workspace: str | None = None,
    ) -> anthropic.Anthropic:
        if kind == "account":
            provider = oauth or self._oauth
            assert provider is not None
            provider.bind_base_url(base_url)
            workspace = workspace or self._workspace
            headers = {WORKSPACE_HEADER: workspace} if workspace else None
            return anthropic.Anthropic(
                credentials=provider,
                base_url=base_url,
                timeout=timeout,
                max_retries=max_retries,
                default_headers=headers,
            )
        assert key is not None
        return anthropic.Anthropic(api_key=key, base_url=base_url, timeout=timeout, max_retries=max_retries)

    # ---- the check of a connection ----
    def _retrieve(self, client: anthropic.Anthropic, model: str) -> tuple[Any, str | None, int]:
        """`models.retrieve(model)`: (model info, organization id from the header, latency in ms)."""
        t0 = time.perf_counter()
        raw = client.models.with_raw_response.retrieve(model)
        latency = int((time.perf_counter() - t0) * 1000)
        info = raw.parse()
        return info, raw.headers.get(ORG_HEADER) or None, latency

    def _check_new(self, client: anthropic.Anthropic, model: str) -> str | None:
        """Checks a connection that is not accepted yet; returns the organization id. API failures -> ApiError.
        A 404 of the model means the key works (the model is not open to the organization): the key is accepted."""
        try:
            _, org, _ = self._retrieve(client, model)
        except anthropic.NotFoundError as e:
            return e.response.headers.get(ORG_HEADER) or None
        except Exception as e:  # noqa: BLE001 - mapped to ApiError, the text of the SDK error is not passed on
            raise self.api_error(e, model) from None
        return org

    def _model(self, model: str | None) -> str:
        return model or prefs.resolve(self.data_dir, self._environ()).model

    def set_key(self, key: str, *, model: str | None = None) -> Snapshot:
        """`POST /connection/key`. The key is checked with `models.retrieve` BEFORE it is kept: a refusal
        (422 `assistant_bad_key`, 503 `assistant_offline`) leaves the state as it was."""
        key = key.strip()
        if not KEY_RE.match(key):
            raise errors.refuse("assistant_bad_key_format")
        base_url = self.base_url
        with self._build("api_key", key, base_url, CHECK_TIMEOUT, MAX_RETRIES) as probe:
            org = self._check_new(probe, self._model(model))
        with self._lock:
            self._drop_oauth()
            self._kind, self._key, self._org, self._workspace = "api_key", key, org, None
            self._accepted()
        self._notify()
        return self.snapshot()

    def set_account(
        self,
        *,
        access_token: str,
        refresh_token: str,
        client_id: str,
        expires_at: int | str | None,
        organization_id: str | None = None,
        workspace_id: str | None = None,
        model: str | None = None,
    ) -> Snapshot:
        """The result of `ant auth login` (23.5): the tokens go to a `MemoryOAuth`, the link is checked, then they
        are accepted. A failure leaves the state as it was."""
        base_url = self.base_url
        oauth = MemoryOAuth(access_token, refresh_token, client_id, _unix(expires_at), base_url)
        oauth.set_on_rotate(self._rotated)
        try:
            with self._build(
                "account", None, base_url, CHECK_TIMEOUT, MAX_RETRIES, oauth=oauth, workspace=workspace_id
            ) as probe:
                org = self._check_new(probe, self._model(model))
        except BaseException:
            oauth.close()
            raise
        with self._lock:
            self._drop_oauth()
            self._kind, self._key, self._oauth = "account", None, oauth
            self._org, self._workspace = organization_id or org, workspace_id
            self._accepted()
        self._notify()
        return self.snapshot()

    def _accepted(self) -> None:  # under the lock: a connection was made in the window
        self._revision += 1
        self._seal_needed = True
        self._online, self._checked_at = True, _now_iso()
        self._cached = None

    def _drop_oauth(self) -> None:  # under the lock
        if self._oauth is not None:
            self._oauth.close()
        self._oauth = None

    def _rotated(self) -> None:
        """The tokens of the account were exchanged: the encrypted copy of the shell is out of date."""
        with self._lock:
            self._revision += 1
            self._seal_needed = True
            self._online, self._checked_at = True, _now_iso()
        self._notify()

    def forget(self) -> Snapshot:
        """`DELETE /connection`: the secret leaves the memory; `ANTHROPIC_API_KEY` of the environment stays in force."""
        with self._lock:
            self._drop_oauth()
            self._kind = self._key = self._org = self._workspace = None
            self._revision += 1
            self._seal_needed = False
            self._online = self._checked_at = None
            self._cached = None
        self._notify()
        return self.snapshot()

    def test(self, model: str | None = None) -> dict[str, Any]:
        """`POST /connection/test` (`ConnectionTest`): `models.retrieve` of the model. A failure of the API is an
        answer (`ok: false` + a Russian message), not an exception; not connected: 409 `assistant_not_connected`."""
        model = self._model(model)
        client = self.client(timeout=CHECK_TIMEOUT)
        base: dict[str, Any] = {
            "ok": False,
            "latency_ms": 0,
            "model": model,
            "model_available": False,
            "effort_supported": None,
            "organization_id": None,
        }
        try:
            info, org, latency = self._retrieve(client, model)
        except anthropic.NotFoundError as e:
            self.note_call(None)
            msg = errors.model_unavailable(model).message
            return {**base, "ok": True, "organization_id": e.response.headers.get(ORG_HEADER) or None, "message": msg}
        except Exception as e:  # noqa: BLE001
            self.note_call(e)
            err = self.api_error(e, model)
            return {**base, "message": f"{err.message} {err.hint}".strip()}
        self.note_call(None)
        if org:
            with self._lock:
                self._org = org if self._kind != "account" else (self._org or org)
        return {
            **base,
            "ok": True,
            "latency_ms": latency,
            "model_available": True,
            "effort_supported": _efforts(info),
            "organization_id": org,
            "message": f"Связь с Claude есть: модель «{prices.title_of(model)}» доступна ({latency} мс).",
        }

    # ---- the exchange with the main process (shell only) ----
    def put_secret(
        self, kind: Literal["api_key", "account"], secret: Mapping[str, Any], secure_storage: bool
    ) -> str | None:
        """`PUT /connection/secret`: the secret decrypted by the shell at start. No check of the link (offline start);
        the format was checked by `schemas.SecretPutBody`. Returns the mask (None for an account)."""
        key: str | None = None
        oauth: MemoryOAuth | None = None
        if kind == "api_key":
            key = str(secret["key"]).strip()
            if not KEY_RE.match(key):
                raise errors.refuse("assistant_bad_key_format")
        else:
            oauth = MemoryOAuth(
                str(secret["access_token"]),
                str(secret["refresh_token"]),
                str(secret["client_id"]),
                _unix(secret.get("expires_at")),
                self.base_url,
                on_rotate=self._rotated,
            )
        with self._lock:
            self._secure_storage = bool(secure_storage)
            self._drop_oauth()
            self._kind, self._key, self._oauth = kind, key, oauth
            if oauth is None:
                self._org = self._workspace = None
            else:
                self._org, self._workspace = secret.get("organization_id"), secret.get("workspace_id")
            self._revision += 1
            self._seal_needed = False
            self._online = self._checked_at = None
            self._cached = None
        mask = mask_key(key) if key else None
        self._notify()
        return mask

    def get_secret(self) -> tuple[str, dict[str, Any], int] | None:
        """`GET /connection/secret`: (kind, secret, revision) of the connection made in the window; None when there is
        nothing to keep (not connected, or only `ANTHROPIC_API_KEY`). Only the shell may ask for it."""
        with self._lock:
            if self._kind == "api_key" and self._key:
                return "api_key", {"key": self._key}, self._revision
            if self._kind == "account" and self._oauth is not None:
                t = self._oauth.snapshot()
                secret = {
                    "access_token": t["access_token"],
                    "expires_at": _iso(t["expires_at"]),
                    "refresh_token": t["refresh_token"],
                    "client_id": t["client_id"],
                    "organization_id": self._org,
                    "workspace_id": self._workspace,
                }
                return "account", {k: v for k, v in secret.items() if v is not None}, self._revision
            return None

    def mark_sealed(self, revision: int) -> bool:
        """`POST /connection/sealed`: the shell encrypted the copy of `revision`; a newer secret still needs sealing."""
        with self._lock:
            done = revision == self._revision and self._seal_needed
            if done:
                self._seal_needed = False
        if done:
            self._notify()
        return done

    # ---- errors ----
    def scrub(self, text: str) -> str:
        """`text` without the secrets of this connection and without anything that looks like a key."""
        with self._lock:
            known = [self._key, self._env_key()]
            if self._oauth is not None:
                known += [self._oauth.snapshot()["access_token"], self._oauth.snapshot()["refresh_token"]]
        return scrub(text, *known)

    def api_error(self, exc: BaseException, model: str | None = None) -> ApiError:
        """An SDK or token failure as the `ApiError` of e23-api.md §12 (a Russian text with a hint; no secret in it)."""
        refresh = _find_refresh_error(exc)
        if refresh is not None:
            return errors.refuse(
                "assistant_offline"
                if refresh.offline or refresh.status is None
                else ("assistant_bad_key" if refresh.status in (400, 401, 403) else "assistant_overloaded")
            )
        if isinstance(exc, anthropic.APIConnectionError):  # includes APITimeoutError
            return errors.refuse("assistant_offline")
        if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
            return errors.refuse("assistant_bad_key")
        if isinstance(exc, anthropic.NotFoundError):
            return errors.model_unavailable(model or "?")
        if isinstance(exc, anthropic.RateLimitError):
            return errors.refuse("assistant_rate_limited")
        if isinstance(exc, anthropic.APIStatusError):
            if exc.status_code >= 500:
                return errors.refuse("assistant_overloaded")
            return errors.refuse("assistant_api_error", detail=self.scrub(str(exc.message))[:300])
        if isinstance(exc, anthropic.AnthropicError):
            return errors.refuse("assistant_api_error", detail=self.scrub(type(exc).__name__))
        return errors.refuse("internal")


def _find_refresh_error(exc: BaseException | None) -> OAuthRefreshError | None:
    """The failed exchange of a refresh token behind `exc` (the SDK may carry it as the cause)."""
    for _ in range(6):
        if exc is None:
            return None
        if isinstance(exc, OAuthRefreshError):
            return exc
        exc = exc.__cause__ or exc.__context__
    return None


def _efforts(info: Any) -> list[str] | None:
    """The efforts the model supports (`capabilities.effort`); None when the answer does not say."""
    effort = getattr(getattr(info, "capabilities", None), "effort", None)
    if effort is None:
        return None
    if not getattr(effort, "supported", False):
        return []
    return [e for e in EFFORTS if getattr(getattr(effort, e, None), "supported", False)]


_connection: Connection | None = None
_connection_lock = threading.Lock()


def get_connection(data_dir: str | os.PathLike[str] | Path | None = None) -> Connection:
    """The connection of the process (created on the first call; `data_dir` is the `--data` of the core)."""
    global _connection
    with _connection_lock:
        if _connection is None:
            _connection = Connection(data_dir)
        return _connection


def reset_connection() -> None:
    """For tests: forget the process-wide connection."""
    global _connection
    with _connection_lock:
        _connection = None
