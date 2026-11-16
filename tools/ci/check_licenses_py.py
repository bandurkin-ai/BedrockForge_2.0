"""Fails if a runtime Python dependency of bf-core is GPL/LGPL/AGPL (ТЗ 6.3, 16.6).

Run inside the core environment: uv run python ../tools/ci/check_licenses_py.py [pyproject.toml]
Checks the closure of [project].dependencies (what ships in bf-core.exe). Build and dev tools
(PyInstaller: GPL with a bootloader exception) are not shipped as libraries and are not checked.
"""

from __future__ import annotations

import re
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, distribution, distributions
from pathlib import Path

FORBIDDEN = re.compile(r"\b(A|L)?GPL\b|GNU (Affero |Lesser |Library )?General Public", re.I)
# Dual-licensed packages where a permissive option is taken (document each in docs/adr/0001).
ALLOW: dict[str, str] = {}


def license_text(dist) -> str:  # type: ignore[no-untyped-def]
    md = dist.metadata
    parts = [md.get("License-Expression") or "", (md.get("License") or "")[:300]]
    parts += [c for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
    return " | ".join(p for p in parts if p)


_NAME = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_closure(pyproject: Path) -> list:  # type: ignore[type-arg]
    roots = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["dependencies"]
    seen: dict[str, object] = {}
    todo = [m.group(1) for r in roots if (m := _NAME.match(r))]
    while todo:
        name = _canon(todo.pop())
        if name in seen:
            continue
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            continue
        seen[name] = dist
        for req in dist.requires or []:
            if "extra ==" in req:
                continue
            m = _NAME.match(req)
            if m:
                todo.append(m.group(1))
    return list(seen.values())


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    dists = runtime_closure(Path(args[0])) if args else list(distributions())
    bad, rows = [], []
    for dist in sorted(dists, key=lambda d: (d.metadata["Name"] or "").lower()):
        name = dist.metadata["Name"] or "?"
        text = license_text(dist)
        rows.append(f"{name}=={dist.version}: {text[:120]}")
        if FORBIDDEN.search(text) and name.lower() not in ALLOW:
            bad.append(f"{name}=={dist.version}: {text[:200]}")
    if "-v" in sys.argv:
        print("\n".join(rows))
    if bad:
        print("Forbidden licenses:\n  " + "\n  ".join(bad))
        return 1
    print(f"licenses ok: {len(rows)} distributions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
