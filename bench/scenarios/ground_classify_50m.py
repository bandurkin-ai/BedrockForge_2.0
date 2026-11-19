"""«Фильтр земли», 50 млн точек «по частям» (ADR 0014 «Замеры», веха; ТЗ 7.35 Д-22: ≤ 6 мин, ≤ 4 ГБ).

The scenario of `ground_classify` under its own name in `bench/budgets.json`: 2 × 2 sites of the scene
«карьер», 25 points/m²; params {"points": 50000000, "block": 1024, "single": false} — four blocks, 4 threads,
without the second run on one thread (the determinism is measured at 1 and 10 million points).
"""

from __future__ import annotations

from .ground_classify import load, prepare, run

__all__ = ["load", "prepare", "run"]
