"""The FULL (unreduced) cone of the pit template — a reference for the bridges of the graph (review 21.21б).

TEST-ONLY code (never imported by bf_core). The template of `bf_core.mine.template` drops an offset that is a sum of
template offsets (ADR 0028 decision 11); `full_cone` lists EVERY offset of the cone by its definition (the length of
(Δi·dx, Δj·dy) ≤ m·dz/tan φ, m = 1…K, the same relative slack), so a graph with all cells as nodes (the «air» ones with
v′ = 0) and these offsets is the reference of «u needs q» that the reduced graph with bridges must reproduce.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

SLACK = 1e-12


def full_cone(dx: float, dy: float, dz: float, phi: float, k: int) -> NDArray[np.int32]:
    """(n, 3) int32 rows (Δi, Δj, m) of the whole cone of a constant slope angle `phi` (degrees), K levels."""
    reach = dz / math.tan(math.radians(phi))
    w = int(k * reach / min(dx, dy)) + 2
    rows = [
        (a, b, m)
        for m in range(1, k + 1)
        for a in range(-w, w + 1)
        for b in range(-w, w + 1)
        if math.hypot(a * dx, b * dy) <= m * reach * (1.0 + SLACK)
    ]
    return np.array(rows, dtype=np.int32)
