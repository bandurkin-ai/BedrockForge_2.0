"""Spike Э0-в (process part): bf-core prints BF-READY, answers with token, stops on shutdown."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

TOKEN = "proc-token-abcdef"


def _start(*extra: str, cwd: str | None = None, env: dict[str, str] | None = None) -> tuple[subprocess.Popen[str], int]:
    proc = subprocess.Popen(
        [sys.executable, "-m", "bf_core", "--port", "0", "--token", TOKEN, *extra],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        env={**os.environ, **(env or {})},
    )
    assert proc.stdout is not None
    deadline = time.monotonic() + 60
    line = ""
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line.startswith("BF-READY"):
            break
        if proc.poll() is not None:
            pytest.fail("core exited before BF-READY")
    assert line.startswith("BF-READY"), line
    fields = dict(kv.split("=") for kv in line.split()[1:])
    return proc, int(fields["port"])


def _get(port: int, path: str, token: str | None) -> int:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if token:
        req.add_header("X-BF-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def _post(port: int, path: str) -> None:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST", data=b"")
    req.add_header("X-BF-Token", TOKEN)
    urllib.request.urlopen(req, timeout=10).close()


@pytest.mark.windows
def test_ready_token_and_shutdown() -> None:
    proc, port = _start()
    try:
        assert _get(port, "/api/v1/health", TOKEN) == 200
        assert _get(port, "/api/v1/health", None) == 401
        _post(port, "/api/v1/shutdown")
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.mark.windows
def test_exits_when_parent_stdin_closes() -> None:
    proc, port = _start("--watch-stdin")
    try:
        assert _get(port, "/api/v1/health", TOKEN) == 200
        assert proc.stdin is not None
        proc.stdin.close()  # the shell died
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_heavy_libraries_not_imported_before_ready() -> None:
    code = (
        "import sys; from bf_core.api.app import CoreState, create_app; create_app(CoreState(token='t'));"
        "heavy=[m for m in ('numba','numpy','pandas','pyarrow','duckdb','scipy') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"imported before BF-READY: {r.stdout.strip()}"


def test_missing_token_refuses_to_start() -> None:
    r = subprocess.run(
        [sys.executable, "-m", "bf_core", "--port", "0"],
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "BF_TOKEN"},
        timeout=60,
    )
    assert r.returncode == 2
