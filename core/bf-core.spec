# PyInstaller spec for bf-core (onedir, no UPX; ТЗ 6.3, 17.2.7). Build: uv run pyinstaller bf-core.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

# bf_core is not an installed package (tool.uv package = false), so collect_submodules("bf_core")
# finds nothing in its isolated subprocess: list the modules from the source tree instead.
# Routers are imported by name (bf_core/api/modules.py) and would be missed by the analysis.
ROOT = Path(SPECPATH)
bf_modules = sorted(
    ".".join(p.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__")
    for p in (ROOT / "bf_core").rglob("*.py")
    if "__pycache__" not in p.parts and "migrations" not in p.parts
)
hidden = collect_submodules("uvicorn") + bf_modules
# OR-Tools (the reference and fallback solver of the pit optimiser, ADR 0028 §33): the Windows wheel keeps its DLLs in
# ortools/.libs and `ortools/__init__.py` loads them from there (WinDLL); the analysis does not see them (a plain file
# search), so the extension `max_flow` failed with «DLL load failed» in the .exe (windows CI, 2026-11-26).
ortools_libs = collect_dynamic_libs("ortools")
try:  # the same files by hand, in case the hook skips the hidden folder `.libs` (the Windows wheel): the destination is the one `__init__` reads
    import importlib.util

    _spec = importlib.util.find_spec("ortools")
    _libs = Path(_spec.origin).parent / ".libs" if _spec and _spec.origin else None
    if _libs is not None and _libs.is_dir():
        _have = {Path(dest) / Path(src).name for src, dest in ortools_libs}
        ortools_libs += [(str(f), "ortools/.libs") for f in sorted(_libs.iterdir()) if f.is_file() and Path("ortools/.libs") / f.name not in _have]
except Exception as exc:  # noqa: BLE001 - a build helper: the hook above is the main way
    print(f"bf-core.spec: ortools/.libs not listed by hand: {exc}")
hidden += collect_submodules("ortools.graph")

a = Analysis(
    ["entry.py"],
    pathex=[str(ROOT)],
    binaries=ortools_libs,
    hiddenimports=hidden,
    # Alembic reads env.py and revisions from files: they ship as data, not in the PYZ.
    datas=[(str(ROOT / "bf_core" / "db" / "migrations"), "bf_core/db/migrations")],
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="bf-core",
    console=True,
    upx=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="bf-core")
