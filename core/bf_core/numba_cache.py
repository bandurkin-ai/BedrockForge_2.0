"""numba on-disk cache inside NUMBA_CACHE_DIR also for the frozen bf-core.exe (spike Э0-г).

In a PyInstaller build `co_filename` is relative and the source file is absent, so numba's
user-provided locator is skipped and the cache lands in the user-wide numba directory.
This locator keeps it in NUMBA_CACHE_DIR; freshness is stamped by the executable (numba does
the same for frozen apps), so a new build invalidates the cache.
"""

from __future__ import annotations

import os
import sys


def install_frozen_cache_locator() -> bool:
    if not getattr(sys, "frozen", False):
        return False
    from numba.core import caching, config

    base_cls = getattr(caching, "UserProvidedCacheLocator", None) or caching._UserProvidedCacheLocator  # type: ignore[attr-defined]
    impl = getattr(caching, "CacheImpl", None) or caching._CacheImpl  # type: ignore[attr-defined]
    root = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))

    class FrozenCacheLocator(base_cls):  # type: ignore[misc, valid-type]
        @classmethod
        def from_function(cls, py_func, py_file):  # type: ignore[no-untyped-def]
            if not config.CACHE_DIR:
                return None
            if not os.path.isabs(py_file):
                py_file = os.path.join(root, py_file)
            self = cls(py_func, py_file)
            try:
                self.ensure_cache_path()
            except OSError:
                return None
            return self

    if not any(c.__name__ == "FrozenCacheLocator" for c in impl._locator_classes):
        impl._locator_classes.insert(0, FrozenCacheLocator)
    return True
