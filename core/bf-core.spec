# PyInstaller spec for bf-core (onedir, no UPX; ТЗ 6.3, 17.2.7). Build: uv run pyinstaller bf-core.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# bf_core is not an installed package (tool.uv package = false), so collect_submodules("bf_core")
# finds nothing in its isolated subprocess: list the modules from the source tree instead.
# Routers are imported by name (bf_core/api/modules.py) and would be missed by the analysis.
ROOT = Path(SPECPATH)
bf_modules = sorted(
    ".".join(p.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__")
    for p in (ROOT / "bf_core").rglob("*.py")
    if "__pycache__" not in p.parts
)
hidden = collect_submodules("uvicorn") + bf_modules

a = Analysis(
    ["entry.py"],
    pathex=[str(ROOT)],
    hiddenimports=hidden,
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
