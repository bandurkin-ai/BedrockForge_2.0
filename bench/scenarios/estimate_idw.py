"""IDW over the blocks of a model (ТЗ 8.8.1; ADR 0024 «Замеры»: full — 20 млн блоков по 100 тыс. проб, n_max 24, до 3
проходов, ≤ 4 мин на 4 ядрах; ci — 600 тыс. блоков по 20 тыс. проб на 2 ядрах).

The deposit of `estimate_common` (holes with infill, twins, a lognormal grade, an outlier; the model reaches beyond the
samples, so ≈ 85–90 % of the blocks are estimated by the first pass). The row — `kernel.compile_row`, the blocks —
`kernel.run` by portions of 2¹⁶ on `threads` cores, as the job `estimate.run` cuts and feeds them (the model is not
read: the centres come from the grid; the model and the write — `estimate_job`).
  samples_s, row_s   the samples of the row (`samples.prepare`), the row compiled (the tree of the search is built at
                     the first portion);  first_s — the first result;  run_s — all the blocks (the timing of ТЗ)
  us_per_block, forecast_20m_s   the cost of a block and the straight-line forecast for 20 млн (the loop over
                     portions); in `full` the run is 20 млн blocks, so the forecast is the measure
  first_share, late_share, lost_share, by_status, by_pass   the tallies of the passes
What is checked (a failure stops the run): every block is accounted for (the statuses sum to the blocks, ≥ 99 %
estimated, ≥ 80 % in the first pass); 40 blocks spread over the model, as the threaded run gave them, against the
brute-force NumPy IDW (the selection of `tests/reference/krige3d_numpy.py`, w = 1/h_s²): the same pass, 1e−9
(`ref_err`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import estimate_common as ec


def prepare(params: dict[str, Any], work: Path) -> None:
    ec.keep(work, ec.Deposit.of(params))


def load() -> None:
    from bf_core.geostat.estimate.warm import warm

    warm()  # the step `estimate` of the warm-up of the workers: a kernel it misses is compiled inside the timing


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    return ec.drive(params, work, "idw")
