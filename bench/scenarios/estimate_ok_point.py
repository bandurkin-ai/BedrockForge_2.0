"""Ordinary kriging of a point over the blocks of a model (ТЗ 8.8.1; ADR 0024 «Замеры»: full — 20 млн блоков по 100 тыс.
проб, n_max 24, ≤ 12 мин на 4 ядрах; ci — 300 тыс. блоков по 20 тыс. проб на 2 ядрах).

The same deposit, search and portions as `estimate_idw` (see there); the model of the variogram — two nested structures
(spherical 70 м + exponential 200 м, an anisotropy turned by 20°, a nugget); a point (disc 1×1×1): the covariances C
of the samples and of the point, the system of n + 1 ≤ 25 by LU.
  samples_s, row_s, first_s, run_s, us_per_block, forecast_20m_s, the tallies of the passes — as in `estimate_idw`
What is checked (a failure stops the run): the tallies as in `estimate_idw`; 40 blocks spread over the model against
the brute-force NumPy kriging of `tests/reference/krige3d_numpy.py` (the search of the passes by brute force,
`numpy.linalg.solve`): the same pass and number of samples, EST and KV to 1e−9 relative (`ref_err`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import estimate_common as ec


def prepare(params: dict[str, Any], work: Path) -> None:
    ec.keep(work, ec.Deposit.of(params))


def load() -> None:
    from bf_core.geostat.estimate.warm import warm

    warm()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    return ec.drive(params, work, "ok", variogram=ec.TWO)
