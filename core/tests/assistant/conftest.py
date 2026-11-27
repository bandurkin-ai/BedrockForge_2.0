"""Fixtures of the assistant tests (stage E23): the Anthropic mock, an SDK client on it, a fake `ant`.

The fixtures live here, but tests outside `tests/assistant/` (`tests/test_assistant_*.py`) do not see this conftest:
import what is needed, e.g. `from tests.assistant.conftest import anthropic_client, mock_anthropic  # noqa: F401`.
No test goes to the internet: the client has an explicit `base_url` on 127.0.0.1 and explicit credentials.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anthropic
import pytest

from tests.assistant.fake_ant import write_launcher
from tests.assistant.mock_anthropic import TEST_API_KEY, MockAnthropic

__all__ = [
    "TEST_API_KEY",
    "anthropic_client",
    "ant_config_dir",
    "clean_anthropic_env",
    "closed_port_url",
    "fake_ant_path",
    "make_client",
    "mock_anthropic",
]


@pytest.fixture()
def clean_anthropic_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Nothing of the machine reaches the SDK: no ANTHROPIC_* variables, an empty config folder, no proxy."""
    for name in list(os.environ):
        if name.startswith(("ANTHROPIC_", "FAKE_ANT_", "BF_ANT", "BF_ASSISTANT")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "no-anthropic-config"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@pytest.fixture()
def mock_anthropic(clean_anthropic_env: None) -> Iterator[MockAnthropic]:
    """A started mock without a scenario: `mock.use_scenario("summary")`, `mock.fault({"status": 529}, times=2)`."""
    with MockAnthropic() as mock:
        yield mock


def make_client(mock: MockAnthropic | str, *, max_retries: int = 2, **kwargs: Any) -> anthropic.Anthropic:
    """An SDK client on the mock (or on a `base_url`): explicit key and base URL, short timeouts."""
    base_url = mock if isinstance(mock, str) else mock.base_url
    kwargs.setdefault("api_key", TEST_API_KEY)
    kwargs.setdefault("timeout", anthropic.Timeout(10.0, connect=2.0))
    return anthropic.Anthropic(base_url=base_url, max_retries=max_retries, **kwargs)


@pytest.fixture()
def anthropic_client(mock_anthropic: MockAnthropic) -> Iterator[anthropic.Anthropic]:
    with make_client(mock_anthropic) as client:
        yield client


@pytest.fixture()
def closed_port_url() -> str:
    """`http://127.0.0.1:<port>` of a port nothing listens on (the client gets APIConnectionError)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.fixture()
def fake_ant_path(tmp_path: Path, clean_anthropic_env: None) -> Path:
    """The path for `BF_ANT_PATH`: a launcher of `fake_ant.py`; behaviour through FAKE_ANT_* variables."""
    return write_launcher(tmp_path / "bin")


@pytest.fixture()
def ant_config_dir(tmp_path: Path) -> Path:
    """An empty folder for `ANTHROPIC_CONFIG_DIR` of a login (the profile is written there)."""
    d = tmp_path / "ant-config"
    d.mkdir()
    return d
