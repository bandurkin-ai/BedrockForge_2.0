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


def test_computed_object_kinds_have_a_lineage_provider() -> None:
    """ADR 0025 «Последствия»: a kind of object whose module keeps an `algo` (a computed object) must have a provider
    in `lineage/providers.py`, or its «устарел» stops at its direct inputs. A module names its object kind in a
    top-level `KIND = "…"` (job kinds have a dot: `blocks.calc`)."""
    import re

    from bf_core.lineage import providers

    found: dict[str, str] = {}
    for path in sorted((ROOT / "core" / "bf_core").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "algo" not in text.lower():
            continue
        for kind in re.findall(r'^KIND = "([a-z_]+)"', text, re.MULTILINE):
            found.setdefault(kind, str(path.relative_to(ROOT)))
    # the three kinds with an `algo` at the time of the ADR: a scan that finds fewer is broken, not the project clean
    assert {"block_model", "variogram_model", "qaqc_report"} <= set(found), found
    missing = {k: f for k, f in found.items() if f"object:{k}" not in providers.registered()}
    assert not missing, f"no lineage provider (core/bf_core/lineage/adapt_*.py): {missing}"


def test_job_handler_modules_do_not_load_numba() -> None:
    """The process of the API registers every handler of `HANDLER_MODULES` (`load_handlers`) and must not pay for numba
    with it (the kernels are loaded by the worker): a handler module is thin, its heavy imports are inside the
    functions or under `TYPE_CHECKING` (the pattern of `geostat/estimate/job.py` and `job_run.py`). One fresh process
    per module, so the culprit is named."""
    import os
    import sys
    from concurrent.futures import ThreadPoolExecutor

    from bf_core.jobs.registry import HANDLER_MODULES

    env = {**os.environ, "PYTHONPATH": str(ROOT / "core"), "BF_NO_WARMUP": "1"}

    def loads_numba(module: str) -> str | None:
        code = f"import {module}, sys; raise SystemExit(3 if 'numba' in sys.modules else 0)"
        run = subprocess.run([sys.executable, "-c", code], cwd=ROOT / "core", env=env, capture_output=True, text=True,
                             timeout=120)  # fmt: skip
        if run.returncode == 0:
            return None
        return module if run.returncode == 3 else f"{module} (import failed: {run.stderr.strip()[-200:]})"

    with ThreadPoolExecutor(max_workers=4) as pool:
        bad = [m for m in pool.map(loads_numba, HANDLER_MODULES) if m]
    assert not bad, f"these handler modules load numba at import: {bad}"
