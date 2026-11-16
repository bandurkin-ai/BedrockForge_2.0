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


def test_no_case_only_module_name_clashes() -> None:
    """`ribbon.ts` next to `Ribbon.tsx`: on Windows `import "./Ribbon"` resolves to the wrong file (ТЗ 17.2.1)."""
    stems: dict[str, str] = {}
    clashes = []
    for path in _tracked():
        if "/" not in path:
            continue
        folder, name = path.rsplit("/", 1)
        stem = name.split(".", 1)[0]
        key = f"{folder.lower()}/{stem.lower()}"
        if key in stems and stems[key] != f"{folder}/{stem}":
            clashes.append((stems[key], path))
        stems.setdefault(key, f"{folder}/{stem}")
    assert not clashes, clashes


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


def test_router_modules_match_source_tree() -> None:
    from bf_core.api.app import discover_routers
    from bf_core.api.modules import ROUTER_MODULES

    pkg = ROOT / "core" / "bf_core"
    on_disk = sorted(p.parent.name for p in pkg.glob("*/router.py") if p.parent.name != "api")
    assert sorted(ROUTER_MODULES) == on_disk, "register the module in bf_core/api/modules.py"
    assert len(discover_routers()) == len(ROUTER_MODULES)
