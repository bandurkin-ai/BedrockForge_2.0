"""Account sign-in of the assistant: the access token of an OAuth login kept in memory (ADR 0029 решения 8, 10, 11).

    MemoryOAuth(access_token, refresh_token, client_id, expires_at, base_url, on_rotate=...)
        an `AccessTokenProvider` of the SDK (anthropic 2.6.0): `provider(force_refresh=False) -> AccessToken`.
        The tokens live in this object only; nothing is written to disk (the encrypted copy is the business of the
        main process: `Connection.get_secret()` -> `safeStorage`). When the access token is close to its end
        (or the API answered 401: `force_refresh`), the `refresh_token` is exchanged at `<base_url>/v1/oauth/token`;
        the new pair replaces the old one and `on_rotate` is called (the connection raises `seal_needed`).
    OAuthRefreshError   the exchange failed: `status` (HTTP code or None), `offline` (no connection). Its text never
                        holds a token, a response body or an address with credentials.

The constants below are the ones of the SDK 2.6.0 (`anthropic.lib.credentials._constants`). The module of the SDK is
private, so they are copied here and `tests/test_assistant_connection.py` (the guard) fails when the SDK changes them.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

import anthropic
from anthropic.lib.credentials import AccessToken

TOKEN_ENDPOINT = "/v1/oauth/token"
OAUTH_API_BETA_HEADER = "oauth-2025-04-20"
GRANT_TYPE_REFRESH_TOKEN = "refresh_token"
TOKEN_EXCHANGE_TIMEOUT = 30.0
DEFAULT_EXPIRES_IN = 3600
#: The token is exchanged this many seconds before its end (the cache of the SDK asks the provider from 120 s).
REFRESH_SKEW_S = 60


class OAuthRefreshError(anthropic.AnthropicError):
    """The exchange of the refresh token failed. `status` - HTTP code of the token endpoint (None: no answer),
    `offline` - the endpoint was not reached. A fixed text: no token and no body of the answer is ever put in it."""

    def __init__(self, message: str, *, status: int | None = None, offline: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.offline = offline


def _mask(token: str) -> str:
    return f"...{token[-4:]}" if len(token) >= 12 else "**********"


class MemoryOAuth:
    """Tokens of an account login in memory; callable as an `AccessTokenProvider` (thread-safe)."""

    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        client_id: str,
        expires_at: int | None,
        base_url: str,
        *,
        on_rotate: Callable[[], None] | None = None,
        http_client: Any | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        # `_lock` guards the pair of tokens and is never held over the network; `_exchange_lock` lets one exchange
        # run at a time (a refresh token is single-use), so `snapshot()` / `expired()` never wait for the network.
        self._lock = threading.RLock()
        self._exchange_lock = threading.Lock()
        self._generation = 0  # +1 at every exchange
        self._access = access_token
        self._refresh = refresh_token
        self._client_id = client_id
        self._expires_at = expires_at
        self._base_url = base_url.rstrip("/")
        self._on_rotate = on_rotate
        self._http = http_client
        self._own_http = False
        self._clock = clock

    # ---- the object never shows its tokens ----
    def __repr__(self) -> str:
        return f"MemoryOAuth(access_token='{_mask(self._access)}', expires_at={self._expires_at!r})"

    __str__ = __repr__

    def __reduce__(self) -> Any:  # a copy of the tokens must not travel through pickle (multiprocessing, crash dumps)
        raise TypeError("MemoryOAuth cannot be pickled")

    # ---- state ----
    @property
    def expires_at(self) -> int | None:
        return self._expires_at

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def base_url(self) -> str:
        return self._base_url

    def bind_base_url(self, base_url: str) -> None:
        """The deployment the exchange goes to (the client binds it before the first request)."""
        with self._lock:
            self._base_url = base_url.rstrip("/")

    def set_on_rotate(self, callback: Callable[[], None] | None) -> None:
        self._on_rotate = callback

    def snapshot(self) -> dict[str, Any]:
        """The current pair for the encrypted copy: access_token, refresh_token, client_id, expires_at (unix s)."""
        with self._lock:
            return {
                "access_token": self._access,
                "refresh_token": self._refresh,
                "client_id": self._client_id,
                "expires_at": self._expires_at,
            }

    def expired(self) -> bool:
        with self._lock:
            return self._expires_at is not None and self._clock() >= self._expires_at

    def _fresh(self) -> bool:
        return self._expires_at is None or self._clock() < self._expires_at - REFRESH_SKEW_S

    # ---- AccessTokenProvider ----
    def __call__(self, *, force_refresh: bool = False) -> AccessToken:
        with self._lock:
            if not force_refresh and self._fresh():
                return AccessToken(token=self._access, expires_at=self._expires_at)
            seen = self._generation
        with self._exchange_lock:
            with self._lock:
                if self._generation != seen and self._fresh():  # another thread exchanged while this one waited
                    return AccessToken(token=self._access, expires_at=self._expires_at)
            self._exchange()  # the network: outside `_lock`
            with self._lock:
                result = AccessToken(token=self._access, expires_at=self._expires_at)
        # outside the locks: the callback takes the lock of the connection, which reads this object (snapshot) too
        if self._on_rotate is not None:
            self._on_rotate()
        return result

    def close(self) -> None:
        with self._lock:
            if self._own_http and self._http is not None:
                self._http.close()
            self._http, self._own_http = None, False

    def _client(self) -> Any:
        with self._lock:
            if self._http is None:
                # no redirects: a 307/308 would repeat the body with the refresh token at the `Location`
                self._http = anthropic.DefaultHttpxClient(timeout=TOKEN_EXCHANGE_TIMEOUT, follow_redirects=False)
                self._own_http = True
            return self._http

    def _exchange(self) -> None:
        """`refresh_token` grant; on success the new pair replaces the old one (the caller then calls `on_rotate`)."""
        with self._lock:
            base_url = self._base_url
            body = json.dumps(
                {"grant_type": GRANT_TYPE_REFRESH_TOKEN, "refresh_token": self._refresh, "client_id": self._client_id}
            )
        try:
            resp = self._client().post(
                f"{base_url}{TOKEN_ENDPOINT}",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "anthropic-beta": OAUTH_API_BETA_HEADER,
                    "User-Agent": f"anthropic-python/{anthropic.__version__}",
                },
            )
        except Exception:  # noqa: BLE001 - a transport error text is dropped: it may name an address
            raise OAuthRefreshError("token endpoint is not reachable", offline=True) from None
        if resp.status_code != 200:
            raise OAuthRefreshError(
                f"token endpoint refused the exchange (HTTP {resp.status_code})", status=resp.status_code
            )
        try:
            payload = resp.json()
        except ValueError:
            raise OAuthRefreshError("token endpoint answered not with JSON", status=resp.status_code) from None
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise OAuthRefreshError("token endpoint answer has no access token", status=resp.status_code)
        try:
            expires_in = int(payload.get("expires_in", DEFAULT_EXPIRES_IN))
        except (TypeError, ValueError, OverflowError):
            raise OAuthRefreshError("token endpoint answer has a wrong lifetime", status=resp.status_code) from None
        new_refresh = payload.get("refresh_token")
        with self._lock:
            self._access = token
            self._expires_at = int(self._clock()) + expires_in
            if isinstance(new_refresh, str) and new_refresh:
                self._refresh = new_refresh
            self._generation += 1
        del payload, body
