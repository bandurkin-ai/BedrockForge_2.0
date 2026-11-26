"""25 shells of the pit on a million blocks (ТЗ 8.11.5: ≤ 3 мин и ≤ 2 ГБ; ADR 0028 «Замеры», подшаг 21.14).

ci: 200 thousand nodes, 2 threads; full: a million nodes, 4 threads. The real `shells.shells` (HPF, the bisection of
the factors) on the deposit and the table of `pit_shells_common` (see there: what is timed and what is checked). The
checks: nested, closed (independent numpy check), equal to the solutions from scratch (the first, the middle and the
last shells) and to OR-Tools on C(RF_max) — the same set and the same value. `params`: nodes, threads, shells (25),
scratch (the numbers of the shells solved from scratch), ortools (bool), goal_s / goal_mb (the criterion of the ТЗ).
"""

from __future__ import annotations

from .pit_shells_common import load, prepare, run

__all__ = ["load", "prepare", "run"]
