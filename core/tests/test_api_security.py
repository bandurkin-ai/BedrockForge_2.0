"""Spike Э0-в (in-process part): token, Host and Origin checks, CORS for app://."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import TOKEN

H = {"X-BF-Token": TOKEN}


def test_health_with_token(client: TestClient) -> None:
    r = client.get("/api/v1/health", headers=H)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_401_without_token(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "unauthorized" and body["message"]


def test_401_wrong_token(client: TestClient) -> None:
    assert client.get("/api/v1/health", headers={"X-BF-Token": "x"}).status_code == 401


def test_unknown_path_needs_token(client: TestClient) -> None:
    assert client.get("/api/v1/nope").status_code == 401
    assert client.get("/api/v1/nope", headers=H).status_code == 404


def test_foreign_host_rejected(client: TestClient) -> None:
    r = client.get("/api/v1/health", headers={**H, "Host": "evil.example:80"})
    assert r.status_code == 403
    assert r.json()["code"] == "bad_host"


def test_localhost_name_rejected(client: TestClient) -> None:
    assert client.get("/api/v1/health", headers={**H, "Host": "localhost:5555"}).status_code == 403


def test_strict_port_when_known() -> None:
    from bf_core.api.app import CoreState, create_app

    c = TestClient(create_app(CoreState(token=TOKEN, port=5555)), base_url="http://127.0.0.1:5555")
    assert c.get("/api/v1/health", headers=H).status_code == 200
    assert c.get("/api/v1/health", headers={**H, "Host": "127.0.0.1:6666"}).status_code == 403


def test_websocket_refused(client: TestClient) -> None:
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/v1/health", headers=H):
        pass


def test_unexpected_error_is_russian_json_with_cors(state) -> None:  # type: ignore[no-untyped-def]
    from fastapi import APIRouter

    from bf_core.api.app import create_app

    app = create_app(state)
    boom = APIRouter()

    @boom.get("/api/v1/boom")
    def _boom() -> None:
        raise RuntimeError("boom")

    app.include_router(boom)
    c = TestClient(app, base_url="http://127.0.0.1:5555", raise_server_exceptions=False)
    r = c.get("/api/v1/boom", headers={**H, "Origin": "app://bedrockforge"})
    assert r.status_code == 500
    assert r.json()["code"] == "internal"
    assert r.headers["access-control-allow-origin"] == "app://bedrockforge"


def test_foreign_origin_rejected(client: TestClient) -> None:
    r = client.get("/api/v1/health", headers={**H, "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_app_origin_gets_cors(client: TestClient) -> None:
    r = client.get("/api/v1/health", headers={**H, "Origin": "app://bedrockforge"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "app://bedrockforge"


def test_preflight_without_token(client: TestClient) -> None:
    r = client.options(
        "/api/v1/health",
        headers={
            "Origin": "app://bedrockforge",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-bf-token",
        },
    )
    assert r.status_code == 204
    assert "X-BF-Token" in r.headers["access-control-allow-headers"]


def test_shutdown_calls_hook(state, client: TestClient) -> None:  # type: ignore[no-untyped-def]
    called = []
    state.request_shutdown = lambda: called.append(1)
    assert client.post("/api/v1/shutdown", headers=H).status_code == 200
    assert called == [1]


def test_event_stream_ends_on_shutdown() -> None:
    """uvicorn waits for open connections at shutdown: the SSE stream must end by itself."""
    import asyncio

    from bf_core.api.events import EventBus

    async def run() -> list[tuple[int, str, str]]:
        bus = EventBus()
        queue, unsubscribe = bus.subscribe()
        bus.publish("job", {"id": 1})
        bus.close()
        got = [await queue.get(), await queue.get()]
        unsubscribe()
        return got

    got = asyncio.run(run())
    assert got[0][1] == "job" and got[1][0] == 0
