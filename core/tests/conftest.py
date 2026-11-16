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
