"""True thickness and ore intercepts (ТЗ 8.5, ADR 0021).

The ranges of ТЗ 8.5.4 that the kernels check themselves live here — the one place for the kernels (`formulas`,
`directions`, `core_orient`) and for the parameters of a request (`spec.RANGES`). Nothing is imported here:
`spec` stays free of numpy (nothing heavy before BF-READY).
"""

EPS_DEFAULT, EPS_RANGE = 0.1, (0.01, 0.5)  # ε_в: TT / l below it — the hole runs almost along the body
STEP_DEFAULT, STEP_RANGE = 1.0, (0.1, 10.0)  # the part of a hole, m
D_CORE_DEFAULT, D_CORE_RANGE = 10.0, (0.0, 1000.0)  # d_к, m: how far the nearest core measurement may be
LINES = ("boh", "toh")  # the line β_к of a core measurement is counted from: the bottom or the top of the core
