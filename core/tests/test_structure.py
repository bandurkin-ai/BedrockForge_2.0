"""Repository rules checked in tests (ТЗ 6.12, 17.2)."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def test_no_case_only_name_clashes() -> None:
    seen: dict[str, str] = {}
    clashes = []
    for path in _tracked():
        key = path.lower()
        if key in seen and seen[key] != path:
            clashes.append((seen[key], path))
        seen[key] = path
    assert not clashes, clashes


def test_router_discovery_imports() -> None:
    from bf_core.api.app import discover_routers

    assert isinstance(discover_routers(), list)
