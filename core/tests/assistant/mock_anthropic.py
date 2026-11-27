"""A mock of the Anthropic HTTP API for the assistant tests and E2E (stage E23, ADR 0029 decision 36).

Standard library only: an HTTP server in a thread on 127.0.0.1. No test and no E2E scenario goes to the internet.
Parts: this file (server, journal, CLI), `mock_scenario.py` (files and matching), `mock_wire.py` (SSE, errors, models).

Endpoints
  POST /v1/messages        a stream (SSE) or a plain answer, chosen by the `stream` flag of the request
  GET  /v1/models/{id}     model info with capabilities; 404 for models in `unavailable_models`
  POST /v1/oauth/token     `refresh_token` grant (the refresh token rotates, a new access token is issued)
Keys: an `x-api-key` in `api_keys` (default: TEST_API_KEY) or a Bearer token issued here / TEST_ACCESS_TOKEN (with the
beta header `oauth-2025-04-20`) is accepted; anything else gets 401 (`check_auth=False` accepts all).
Control (not in the journal; for tests and E2E over HTTP)
  GET/DELETE /__requests   the journal of received requests (secrets are never written) / clear it
  POST /__scenario         {"name": "summary"} | {"names": [...]} | {"steps": [...]} -- replace the scenario
  POST /__fault            {"reply": {"status": 529}, "times": 2, "path": "/v1/messages"} -- override the next answers
  DELETE /__fault          drop the fault;  POST /__reset  scenario, fault, journal and tokens back to the start

A scenario is `{"steps": [{"match": {...}, "reply": {...}, "times"?: n}]}` (docs/progress/e23-api.md section 15).
The first step whose `match` fits (and is not used up: `times`) answers; a fault wins over the scenario.
  match  last_user_text   substring of the text of the last user message (the `<workspace>` block is cut out; a message
                          with tool results has no text for this key -- it is a continuation, not a question)
         tool_result_for  name of a tool whose result is in the last user message
         tool_result_error  true | false: that result is an error or is not
         model  model of the request;   nth  0-based number of the `/v1/messages` request
  reply  content  [{"type": "text", "text"} | {"type": "tool_use", "name", "input", "id"?}
                   | {"type": "thinking", "thinking", "signature"?}]
         stop_reason  default `end_turn` (`tool_use` when the content has a tool_use)
         usage  {"input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"}
                (short names input / output / cache_read / cache_write are accepted)
         stream  accepted and ignored: the `stream` flag of the request decides (SSE or a plain message)
         delay_ms  pause between the events of a stream (cancel tests);  chunk  size of a text piece (default 12)
         status  an error instead of an answer: 401 | 403 | 404 | 429 | 500 | 529 ... (`error_type`, `message`,
                 `headers`; `retry_after_ms` (default 1) -- the SDK waits that long before a retry)
         drop_after_events  the stream ends without its final chunk after n events: the connection is lost
No step fits: a text answer "No scenario step fits this request." and `matched: false` in the journal.

Run from `core/`: `python -m tests.assistant.mock_anthropic --scenario summary --port-file <path>`; the file gets the
port number (plain text); the process lives until SIGTERM / SIGINT.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import signal
import socket
import threading
import time
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from tests.assistant.mock_scenario import SCENARIO_DIR, load_scenario, request_facts, step_fits
from tests.assistant.mock_wire import error_body, message_events, model_info, plain_message

TEST_API_KEY = "sk-ant-test-0000000000000000000000"
TEST_ACCESS_TOKEN = "sk-ant-oat01-test-access-000000000000"
TEST_REFRESH_TOKEN = "sk-ant-ort01-test-refresh-00000000000"
TEST_CLIENT_ID = "00000000-0000-4000-8000-000000000001"
TEST_ORG_ID = "org_test0000000000"
OAUTH_BETA = "oauth-2025-04-20"

_SECRET_KEYS = {"refresh_token", "access_token", "api_key", "client_secret", "key"}
_HIDDEN_HEADERS = {"authorization", "x-api-key", "cookie", "proxy-authorization"}
Scenario = dict[str, Any] | str | os.PathLike[str] | list[str]


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("<redacted>" if k in _SECRET_KEYS and isinstance(v, str) else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


class MockAnthropic:
    """The mock server. A context manager (or `start()` / `stop()`); `base_url` is `http://127.0.0.1:<port>`."""

    def __init__(
        self,
        scenario: Scenario | None = None,
        *,
        port: int = 0,
        api_keys: set[str] | None = None,
        check_auth: bool = True,
        org_id: str = TEST_ORG_ID,
        unavailable_models: set[str] | None = None,
        oauth_expires_in: int = 3600,
        scenario_dir: Path = SCENARIO_DIR,
    ) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._port = port
        self.api_keys = set(api_keys) if api_keys is not None else {TEST_API_KEY}
        self.check_auth = check_auth
        self.org_id = org_id
        self.unavailable_models = set(unavailable_models or ())
        self.oauth_expires_in = oauth_expires_in
        self.scenario_dir = scenario_dir
        self.journal: list[dict[str, Any]] = []
        self.access_tokens: set[str] = set()
        self.refresh_tokens: set[str] = set()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._scenario: dict[str, Any] = {"steps": []}
        self._used: dict[int, int] = {}
        self._fault: dict[str, Any] | None = None
        self._messages = 0
        self._ids = 0
        self.reset()
        if scenario is not None:
            self.use_scenario(scenario)

    # ---- lifecycle ----
    def start(self) -> MockAnthropic:
        if self._server is None:
            server = ThreadingHTTPServer(("127.0.0.1", self._port), _Handler)
            server.daemon_threads = True
            server.mock = self  # type: ignore[attr-defined]
            self._server = server
            self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(5)
            self._thread = None

    def __enter__(self) -> MockAnthropic:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def port(self) -> int:
        assert self._server is not None, "the mock is not started"
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # ---- configuration (over HTTP too: /__scenario, /__fault, /__reset) ----
    def use_scenario(self, scenario: Scenario) -> None:
        """A dict (`{"steps": [...]}`), a file name or path, or a list of names (their steps in that order)."""
        if isinstance(scenario, dict):
            doc = copy.deepcopy(scenario)
        else:
            doc = load_scenario(*(scenario if isinstance(scenario, list) else [scenario]), directory=self.scenario_dir)
        with self._lock:
            self._scenario, self._used = doc, {}

    def fault(self, reply: dict[str, Any], *, times: int | None = None, path: str = "/v1/messages") -> None:
        """The next `times` requests to `path` (all, when None) get `reply` (a scenario reply, e.g. {"status": 529})."""
        with self._lock:
            self._fault = {"reply": copy.deepcopy(reply), "times": times, "path": path}

    def clear_fault(self) -> None:
        with self._lock:
            self._fault = None

    def reset(self) -> None:
        with self._lock:
            self._scenario, self._used, self._fault, self._messages = {"steps": []}, {}, None, 0
            self.journal.clear()
            self.access_tokens = {TEST_ACCESS_TOKEN}
            self.refresh_tokens = {TEST_REFRESH_TOKEN}

    @property
    def requests(self) -> list[dict[str, Any]]:
        """The journal: method, path, headers (no secrets), body (secrets redacted), auth {kind, ok}, status ..."""
        with self._lock:
            return copy.deepcopy(self.journal)

    def requests_to(self, path: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["path"] == path]

    # ---- used by the handler ----
    def next_id(self) -> int:
        with self._lock:
            self._ids += 1
            return self._ids

    def authorize(self, headers: dict[str, str]) -> tuple[dict[str, Any], bool]:
        key, auth = headers.get("x-api-key"), headers.get("authorization", "")
        if key:
            ok = key in self.api_keys
            return {"kind": "api_key", "ok": ok}, ok
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            if token in self.api_keys:  # SDK 2.6.0 sends `api_key=` as `Authorization: Bearer <key>`
                return {"kind": "api_key", "ok": True}, True
            ok = token in self.access_tokens and OAUTH_BETA in headers.get("anthropic-beta", "")
            return {"kind": "bearer", "ok": ok}, ok
        return {"kind": "none", "ok": False}, False

    def pick_reply(self, body: dict[str, Any], path: str) -> tuple[dict[str, Any] | None, int | None, bool]:
        """(reply, step number, is a fault) of a request; None when no step fits."""
        with self._lock:
            f = self._fault
            if f is not None and path.startswith(f["path"]):
                if f["times"] is not None:
                    f["times"] -= 1
                    if f["times"] <= 0:
                        self._fault = None
                return f["reply"], None, True
            nth, self._messages = self._messages, self._messages + 1
            facts = request_facts(body)
            for i, step in enumerate(self._scenario.get("steps", [])):
                limit = step.get("times")
                if limit is not None and self._used.get(i, 0) >= limit:
                    continue
                if step_fits(step.get("match") or {}, facts, nth):
                    self._used[i] = self._used.get(i, 0) + 1
                    return step["reply"], i, False
            return None, None, False


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MockAnthropic/1"

    @property
    def mock(self) -> MockAnthropic:
        return self.server.mock  # type: ignore[attr-defined, no-any-return]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the signature of the base class
        pass

    # ---- output ----
    def _head(self, status: int, headers: dict[str, str]) -> None:
        self.send_response(status)
        self.send_header("request-id", "req_mock" + uuid.uuid4().hex[:12])
        self.send_header("anthropic-organization-id", self.mock.org_id)
        for k, v in headers.items():
            self.send_header(k, v)

    def _send(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        data = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._head(status, {"Content-Type": "application/json", "Content-Length": str(len(data)), **(headers or {})})
        self.end_headers()
        self.wfile.write(data)

    def _error(self, entry: dict[str, Any], status: int, reply: dict[str, Any] | None = None) -> None:
        r = reply or {}
        retry = status in (408, 409, 429) or status >= 500
        headers = {"retry-after-ms": str(int(r.get("retry_after_ms", 1)))} if retry else {}
        entry["status"] = status
        body = error_body(status, r.get("message"), r.get("error_type"))
        self._send(status, body, {**headers, **(r.get("headers") or {})})

    def _chunk(self, data: bytes) -> None:
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def _stream(self, entry: dict[str, Any], events: list[tuple[str, dict[str, Any]]], reply: dict[str, Any]) -> None:
        sse = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "Transfer-Encoding": "chunked"}
        self._head(200, sse)
        self.end_headers()
        entry["status"] = 200
        drop, delay = reply.get("drop_after_events"), float(reply.get("delay_ms") or 0) / 1000
        for n, (name, data) in enumerate(events):
            if drop is not None and n >= int(drop):
                entry["dropped"] = True
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)  # no final chunk: the reader sees a lost connection
                except OSError:
                    pass
                return
            if delay and self.mock._stop.wait(delay):
                return
            self._chunk(f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
        self._chunk(b"")

    # ---- input ----
    def _read_body(self) -> Any:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return None

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        url = urlsplit(self.path)
        body = self._read_body()
        if url.path.startswith("/__"):
            self._control(method, url.path, body)
            return
        headers = {k.lower(): v for k, v in self.headers.items()}
        auth, ok = self.mock.authorize(headers)
        entry: dict[str, Any] = {
            "index": 0, "at": datetime.now(UTC).isoformat(timespec="milliseconds"), "method": method, "path": url.path,
            "query": parse_qs(url.query), "headers": {k: v for k, v in headers.items() if k not in _HIDDEN_HEADERS},
            "auth": auth, "stream": bool(isinstance(body, dict) and body.get("stream")), "body": _redact(body),
            "status": None, "matched": None, "step": None, "fault": False,
        }
        with self.mock._lock:
            entry["index"] = len(self.mock.journal)
            self.mock.journal.append(entry)
        try:
            self._route(method, url.path, body, entry, ok)
        except (BrokenPipeError, ConnectionResetError):
            entry["dropped"] = True
            self.close_connection = True

    def _route(self, method: str, path: str, body: Any, entry: dict[str, Any], ok: bool) -> None:
        mock = self.mock
        if path == "/v1/oauth/token" and method == "POST":
            self._oauth(body, entry)
        elif path.startswith("/v1/models/") and method == "GET":
            model = path[len("/v1/models/") :]
            if mock.check_auth and not ok:
                self._error(entry, 401, {"message": "invalid x-api-key"})
            elif model in mock.unavailable_models:
                self._error(entry, 404, {"message": f"model: {model}"})
            else:
                entry["status"] = 200
                self._send(200, model_info(model))
        elif path == "/v1/messages" and method == "POST":
            self._messages(body, entry, ok)
        else:
            self._error(entry, 404, {"message": f"no route {method} {path}"})

    def _messages(self, body: Any, entry: dict[str, Any], ok: bool) -> None:
        mock = self.mock
        if not isinstance(body, dict):
            self._error(entry, 400, {"message": "the body is not a JSON object"})
            return
        reply, entry["step"], entry["fault"] = mock.pick_reply(body, "/v1/messages")
        if mock.check_auth and not ok and not entry["fault"]:
            self._error(entry, 401, {"message": "invalid x-api-key"})
            return
        if reply is None:
            entry["matched"] = False
            reply = {"content": [{"type": "text", "text": "No scenario step fits this request."}]}
        elif not entry["fault"]:
            entry["matched"] = True
        if reply.get("status"):
            self._error(entry, int(reply["status"]), reply)
            return
        model = str(body.get("model") or "claude-mock")
        if body.get("stream"):
            self._stream(entry, message_events(reply, model, mock.next_id), reply)
        else:
            entry["status"] = 200
            self._send(200, plain_message(reply, model, mock.next_id))

    def _oauth(self, body: Any, entry: dict[str, Any]) -> None:
        mock = self.mock
        if not isinstance(body, dict) or body.get("grant_type") != "refresh_token":
            entry["status"] = 400
            self._send(400, {"error": "unsupported_grant_type"})
            return
        old = str(body.get("refresh_token", ""))
        with mock._lock:
            good = OAUTH_BETA in self.headers.get("anthropic-beta", "") and old in mock.refresh_tokens
            entry["refresh_token_ok"] = good
            n = len(mock.journal)
            access = f"sk-ant-oat01-test-{n:04d}-{uuid.uuid4().hex[:12]}"
            refresh = f"sk-ant-ort01-test-{n:04d}-{uuid.uuid4().hex[:12]}"
            if good:
                mock.refresh_tokens.discard(old)
                mock.refresh_tokens.update({refresh})
                mock.access_tokens.update({access})
        if not good:
            entry["status"] = 400
            self._send(400, {"error": "invalid_grant", "error_description": "bad refresh token or missing beta header"})
            return
        entry["status"] = 200
        issued = {"token_type": "Bearer", "access_token": access, "expires_in": mock.oauth_expires_in}
        self._send(200, {**issued, "refresh_token": refresh})

    def _control(self, method: str, path: str, body: Any) -> None:
        mock = self.mock
        body = body if isinstance(body, dict) else {}
        try:
            if path == "/__requests" and method == "GET":
                self._send(200, {"requests": mock.requests})
            elif path == "/__requests" and method == "DELETE":
                with mock._lock:
                    mock.journal.clear()
                self._send(204, None)
            elif path == "/__scenario" and method == "POST":
                steps = {"steps": body["steps"]} if body.get("steps") else None
                spec = steps or body.get("names") or body.get("name")
                if not spec:
                    raise ValueError("pass `name`, `names` or `steps`")
                mock.use_scenario(spec)
                self._send(200, {"steps": len(mock._scenario["steps"])})
            elif path == "/__fault" and method == "POST":
                mock.fault(body["reply"], times=body.get("times"), path=body.get("path", "/v1/messages"))
                self._send(200, {"ok": True})
            elif path == "/__fault" and method == "DELETE":
                mock.clear_fault()
                self._send(204, None)
            elif path == "/__reset" and method == "POST":
                mock.reset()
                self._send(204, None)
            else:
                self._send(404, error_body(404, f"no control route {method} {path}"))
        except (KeyError, ValueError, FileNotFoundError) as e:
            self._send(400, error_body(400, str(e)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mock of the Anthropic API (stage E23)")
    ap.add_argument("--scenario", action="append", default=[], help="a name in the scenarios folder or a path")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--port-file", help="a file that gets the port number when the server listens")
    ap.add_argument("--key", action="append", help="an accepted API key (default: the test key); repeatable")
    ap.add_argument("--no-auth", action="store_true", help="do not check keys and tokens")
    ap.add_argument("--unavailable", action="append", default=[], help="a model id that answers 404; repeatable")
    args = ap.parse_args(argv)
    mock = MockAnthropic(
        port=args.port, api_keys=set(args.key) if args.key else None, check_auth=not args.no_auth,
        unavailable_models=set(args.unavailable),
    )
    if args.scenario:
        mock.use_scenario(list(args.scenario))
    mock.start()
    if args.port_file:
        tmp = Path(args.port_file + ".tmp")
        tmp.write_text(f"{mock.port}\n", encoding="utf-8")
        os.replace(tmp, args.port_file)
    print(f"mock anthropic listening on {mock.base_url}", flush=True)
    done = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: done.set())
    while not done.is_set():
        time.sleep(0.1)
    mock.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
