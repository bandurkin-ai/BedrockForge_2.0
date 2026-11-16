"""Spikes Э0-а/г: native dependencies compute; numba cache works in a Cyrillic path with spaces."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bf_core.diag.selftest import run_selftest


def test_selftest_in_process() -> None:
    report = run_selftest()
    failed = {k: v.get("error") for k, v in report["checks"].items() if not v["ok"]}
    assert report["ok"], failed


@pytest.mark.windows
def test_numba_cache_in_cyrillic_path(tmp_path: Path) -> None:
    cache = tmp_path / "Пользователь Тестовый" / "кеш numba"
    work = tmp_path / "Мои проекты" / "папка с пробелами"
    work.mkdir(parents=True)
    env = {**os.environ, "NUMBA_CACHE_DIR": str(cache), "PYTHONIOENCODING": "utf-8"}
    core_dir = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = str(core_dir) + os.pathsep + env.get("PYTHONPATH", "")
    runs = []
    for _ in range(2):  # second run must load from the cache
        r = subprocess.run(
            [sys.executable, "-m", "bf_core", "--selftest"], cwd=work, env=env, capture_output=True, timeout=600
        )
        assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
        runs.append(json.loads(r.stdout.decode("utf-8")))
    numba = runs[1]["checks"]["numba"]
    assert numba["cache_dir"] == str(cache)
    assert numba["cache_files"] >= 2
    assert any(cache.rglob("*.nbi"))
