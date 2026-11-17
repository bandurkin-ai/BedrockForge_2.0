"""Volume of a closed mesh by signed tetrahedra (numba), warm cache. ТЗ 5.4."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np


def prepare(params: dict[str, Any], work: Path) -> None:
    from tools.gen.mesh import uv_sphere

    v, t = uv_sphere(int(params["triangles"]))
    np.save(work / "v.npy", v)
    np.save(work / "t.npy", t.astype(np.int64))
    from bf_core.diag.kernels import (
        signed_volume,
    )  # compile into the cache outside the timing

    signed_volume(v[:8].copy(), np.array([[0, 1, 2]], dtype=np.int64))


def load() -> None:
    from bf_core.diag.kernels import signed_volume

    signed_volume(np.zeros((3, 3)), np.array([[0, 1, 2]], dtype=np.int64))


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.diag.kernels import signed_volume

    v = np.load(work / "v.npy", mmap_mode="r")
    t = np.load(work / "t.npy", mmap_mode="r")
    vol = signed_volume(np.ascontiguousarray(v), np.ascontiguousarray(t))
    return {"volume": vol, "triangles": int(len(t))}
