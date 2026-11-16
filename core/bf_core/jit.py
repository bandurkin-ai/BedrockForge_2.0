"""Single import point for numba in bf-core: kernels use `from bf_core.jit import njit`.

numba is heavy (≈ 1 s); it is imported only when a kernel module is, never before BF-READY
(ADR 0002 «Быстрый старт»). In the frozen bf-core.exe the cache locator is installed here.
"""

from __future__ import annotations

from numba import njit

from bf_core.numba_cache import install_frozen_cache_locator

install_frozen_cache_locator()

__all__ = ["njit"]
