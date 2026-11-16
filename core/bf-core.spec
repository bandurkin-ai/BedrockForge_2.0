# PyInstaller spec for bf-core (onedir, no UPX; ТЗ 6.3, 17.2.7). Build: uv run pyinstaller bf-core.spec
from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("uvicorn") + collect_submodules("bf_core")

a = Analysis(
    ["entry.py"],
    pathex=["."],
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
