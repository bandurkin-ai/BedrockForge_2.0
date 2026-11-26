"""25 shells of the pit on five million blocks (ТЗ 8.11.5: ≤ 20 мин и ≤ 4 ГБ; ADR 0028 «Замеры», подшаг 21.14).

Only the full profile (4 threads; by hand in ci). The same scenario as `pit_shells_1m` with 5 million nodes: the real
`shells.shells` and the checks of `pit_shells_common`, except OR-Tools on C(RF_max) (≈ 5,6 ГБ: over the goal; the
million has it) and the solutions from scratch only for the first and the last shell (HPF ≈ 1 min each).
"""

from __future__ import annotations

from .pit_shells_common import load, prepare, run

__all__ = ["load", "prepare", "run"]
