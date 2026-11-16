from __future__ import annotations

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
