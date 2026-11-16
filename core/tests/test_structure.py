"""Repository rules checked in tests (ТЗ 6.12, 17.2)."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def test_no_case_only_name_clashes() -> None:
    """Files and directories (every path prefix) must not differ only by case (ТЗ 17.2.1)."""
    seen: dict[str, str] = {}
    clashes = set()
    for path in _tracked():
        parts = path.split("/")
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            key = prefix.lower()
            if key in seen and seen[key] != prefix:
                clashes.add((seen[key], prefix))
            seen.setdefault(key, prefix)
    assert not clashes, sorted(clashes)


def test_no_eval_or_raw_html_in_frontend() -> None:
    """ТЗ 5.6 S9: no eval, new Function, dangerouslySetInnerHTML; no window.confirm/prompt/alert (17.4.1)."""
    import re

    bad = re.compile(
        r"\beval\s*\(|new\s+Function\s*\(|dangerouslySetInnerHTML|window\.(confirm|prompt|alert|open)\s*\("
    )
    hits = []
    for path in _tracked():
        if path.startswith(("ui/src/", "app/src/")) and path.endswith((".ts", ".tsx")):
            for n, line in enumerate((ROOT / path).read_text(encoding="utf-8").splitlines(), 1):
                if bad.search(line):
                    hits.append(f"{path}:{n}: {line.strip()}")
    assert not hits, hits


def test_router_discovery_imports() -> None:
    from bf_core.api.app import discover_routers

    assert isinstance(discover_routers(), list)
