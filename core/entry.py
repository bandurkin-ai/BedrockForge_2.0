"""PyInstaller entry point for bf-core.exe."""

import multiprocessing

from bf_core.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # workers use spawn (ТЗ 17.2.5)
    raise SystemExit(main())
