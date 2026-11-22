"""Importing the geostat routes loads nothing heavy (the core reports BF-READY before numpy, Э16)."""

from __future__ import annotations

import subprocess
import sys

HEAVY = ("numba", "numpy", "pandas", "pyarrow", "duckdb", "scipy")


def test_geostat_router_imports_no_heavy_modules() -> None:
    code = (
        "import sys; import bf_core.geostat.router, bf_core.geostat.trend_store;"
        f"print(','.join(m for m in {HEAVY!r} if m in sys.modules))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"imported by the geostat routes: {r.stdout.strip()}"
