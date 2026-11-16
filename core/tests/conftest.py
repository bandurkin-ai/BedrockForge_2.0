from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from bf_core.api.app import CoreState, create_app

TOKEN = "test-token-0123456789"


@pytest.fixture()
def state() -> CoreState:
    return CoreState(token=TOKEN)


@pytest.fixture()
def client(state: CoreState) -> TestClient:
    return TestClient(create_app(state), base_url="http://127.0.0.1:5555")


def _db_urls() -> list[object]:
    pg = os.environ.get("BF_TEST_PG")
    return [
        "sqlite",
        pytest.param(pg or "pg", id="pg", marks=pytest.mark.skipif(not pg, reason="BF_TEST_PG is not set")),
    ]


@pytest.fixture(params=_db_urls())
def db_url(request: pytest.FixtureRequest) -> str:
    """Database dialect of a test: SQLite always, PostgreSQL 16 when BF_TEST_PG is set (CI core-pg)."""
    return str(request.param)


@pytest.fixture()
def pclient(tmp_path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """A core with its own profile and projects folder (closed at the end: locks and engines released)."""
    monkeypatch.setenv("BF_LOCAL_DIR", str(tmp_path / "local"))
    st = CoreState(token=TOKEN, projects_dir=str(tmp_path / "Проекты"), data_dir=str(tmp_path / "roaming"))
    with TestClient(create_app(st), base_url="http://127.0.0.1:5555", headers={"X-BF-Token": TOKEN}) as c:
        c.core = st  # type: ignore[attr-defined]
        yield c
