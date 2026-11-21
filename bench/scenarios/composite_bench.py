"""Composites by benches — the whole job `composite.run`, method "bench" (ADR 0018 «Замеры»; ТЗ 8.2.3: «Композиты
по уступам — ≤ 45 с» на 50 тыс. скважин, 5 млн проб, 10 полей; ci — 5 тыс., 500 тыс.).

The scenario of `composites` under its own name in `bench/budgets.json`: params {"methods": ["bench"], "bench": 10}
— benches of 10 m counted from the highest collar of the set, over holes that curve (a station every 50 m), some
of them nearly flat (FLAG «пологая»), some without a survey or without a collar (no trajectory — no composites,
a line of the journal). Stages `bench_…_s` as there: `bench_traj_s` — the trajectories of the set (minimum
curvature), `bench_kernel_s` — the crossings of the levels and the values, `bench_write_s` — the table and the
final transaction. Checked: Σ LENGTH is the length of the holes that have a collar, within 1e-9 of it.
"""

from __future__ import annotations

from .composites import load, prepare, run

__all__ = ["load", "prepare", "run"]
