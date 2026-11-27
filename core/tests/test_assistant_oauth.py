"""The locks and the transport of `MemoryOAuth` (подшаг 23.21б-B): a read of the pair never waits for the network, one
exchange at a time, no redirect of the body with the refresh token; a wrong `ANTHROPIC_API_KEY` is ignored."""

from __future__ import annotations

import http.server
import logging
import threading
import time
from typing import Any

import pytest

from bf_core.assistant.connection import Connection
from bf_core.assistant.oauth import MemoryOAuth, OAuthRefreshError

REFRESH = "sk-ant-ort01-refresh-0000000000000"


class _Response:
    status_code = 200

    def json(self) -> dict[str, Any]:
        return {"access_token": "sk-ant-oat01-new-0000000000000", "refresh_token": REFRESH + "2", "expires_in": 3600}


class _SlowHttp:
    """A token endpoint that waits for `release` (a slow network) and counts the exchanges."""

    def __init__(self) -> None:
        self.entered, self.release, self.calls = threading.Event(), threading.Event(), 0

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls += 1
        self.entered.set()
        assert self.release.wait(10)
        return _Response()

    def close(self) -> None:
        pass


def _oauth(http: Any, **kw: Any) -> MemoryOAuth:
    return MemoryOAuth(
        "sk-ant-oat01-old-00000000000000",
        REFRESH,
        "client",
        int(time.time()) - 5,
        "http://127.0.0.1:1",
        http_client=http,
        **kw,
    )


def test_reads_do_not_wait_for_the_exchange_and_only_one_exchange_runs() -> None:
    http = _SlowHttp()
    p = _oauth(http)
    c = Connection(env={"BF_ASSISTANT_BASE_URL": "http://127.0.0.1:1"})
    c._kind, c._oauth = "account", p  # the account of the window
    workers = [threading.Thread(target=lambda: p(force_refresh=True), daemon=True) for _ in range(2)]
    workers[0].start()
    assert http.entered.wait(5)
    workers[1].start()  # waits for the first exchange, then sees a fresh pair
    started = time.perf_counter()
    assert p.snapshot()["refresh_token"] == REFRESH and p.expired()
    assert c.is_connected() and c.get_secret() is not None and c.scrub("x") == "x" and c.snapshot().state == "connected"
    assert time.perf_counter() - started < 1.0, "a read of the pair waited for the network"
    http.release.set()
    for w in workers:
        w.join(10)
    assert http.calls == 1, "a refresh token is single-use: one exchange"
    assert p.snapshot()["refresh_token"] == REFRESH + "2" and not p.expired()


def test_a_redirect_of_the_token_endpoint_is_an_error_and_is_not_followed() -> None:
    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            hits.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path.startswith("/v1/oauth/token"):
                self.send_response(307)
                self.send_header("Location", "/stolen")
            else:
                self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        p = MemoryOAuth("a" * 20, REFRESH, "client", int(time.time()) - 5, f"http://127.0.0.1:{server.server_port}")
        with pytest.raises(OAuthRefreshError) as e:
            p(force_refresh=True)
        assert e.value.status == 307 and REFRESH not in str(e.value)
        assert hits == ["/v1/oauth/token"], "the body with the refresh token was sent on"
        p.close()
    finally:
        server.shutdown()


def test_a_wrong_env_key_is_ignored_with_one_line_in_the_log(caplog: pytest.LogCaptureFixture) -> None:
    bad = "sk-ant-api03-" + "x" * 30 + "\nX-Evil: 1"
    c = Connection(env={"ANTHROPIC_API_KEY": bad})
    with caplog.at_level(logging.WARNING, logger="bf_core.assistant"):
        assert c.snapshot().state == "none" and not c.is_connected() and c.scrub("t") == "t"
    lines = [r.getMessage() for r in caplog.records if "ANTHROPIC_API_KEY" in r.getMessage()]
    assert len(lines) == 1 and "xxxxxxxx" not in lines[0]
    good = "sk-ant-api03-" + "x" * 30
    assert Connection(env={"ANTHROPIC_API_KEY": good}).snapshot().kind == "env"
