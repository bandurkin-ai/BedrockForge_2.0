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
        headers={"Origin": "app://bedrockforge", "Access-Control-Request-Method": "GET",
                 "Access-Control-Request-Headers": "x-bf-token"},
    )
    assert r.status_code == 204
    assert "X-BF-Token" in r.headers["access-control-allow-headers"]


def test_shutdown_calls_hook(state, client: TestClient) -> None:  # type: ignore[no-untyped-def]
    called = []
    state.request_shutdown = lambda: called.append(1)
    assert client.post("/api/v1/shutdown", headers=H).status_code == 200
    assert called == [1]
