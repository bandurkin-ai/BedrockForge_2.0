"""Ordinary kriging of a block 4×4×2 over the blocks of a model (ТЗ 8.8.1; ADR 0024 «Замеры»: full — 20 млн блоков по
100 тыс. проб, 3 прохода, ≤ 25 мин на 4 ядрах, 5 млн ≤ 6 мин; ci — 120 тыс. блоков по 20 тыс. проб на 2 ядрах).

The same deposit, search and portions as `estimate_idw`; two structures of the variogram (as `estimate_ok_point`); a
block of 10 × 10 × 5 м discretized 4 × 4 × 2 = 32 points: the right part C̄_iV of every system is 32 covariances of
every sample (C̄_VV of the block is counted once).
  samples_s, row_s, first_s, run_s, us_per_block, forecast_20m_s, the tallies of the passes — as in `estimate_idw`
  t5m_s   (a model of ≥ 5 млн blocks) the seconds at which the first 5 млн blocks were done: the second target of
          the ADR («5 млн ≤ 6 мин»)
What is checked (a failure stops the run): the tallies as in `estimate_idw`; 40 blocks spread over the model against
the brute-force NumPy block kriging (C̄_iV, C̄_VV by the 32 points, without the nugget between them): the same pass and
number of samples, EST and KV to 1e−9 relative (`ref_err`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import estimate_common as ec

DISC = (4, 4, 2)
MARKS = (5_000_000,)


def prepare(params: dict[str, Any], work: Path) -> None:
    ec.keep(work, ec.Deposit.of(params))


def load() -> None:
    from bf_core.geostat.estimate.warm import warm

    warm()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    return ec.drive(params, work, "ok", variogram=ec.TWO, disc=DISC, marks=MARKS)
