"""The import of a Micromine DAT as a block model whose sub-blocks divide the parent by a number that is not 2^L
(ADR 0030 «Замеры»; подшаг 27.4а, Д-45 А6: the model of the customer — parent 10 × 10 × 10, sub-cells 2, 2, 3.333 m).

The file of `blocks_files.write_dat` with `div` (5, 5, 3) and the parent of 10 m on every axis: 1 million rows × 20
number fields, 25 % of the parents split into eight (the pieces of 2 × 2 × 3.333 m, the offsets 0 and 2 / 0 and 1
sub-cells). Everything is as in `blocks_import_dat`: the roles of the columns, the grid found by the lattice of the
edges (`grid_lattice`: the division is the lcm of the denominators — 5, 5, 3), the rows sorted and checked, the model
written; the checks of `blocks_files.imported` and also the division found: the target is
**≤ 1.1 × `blocks_import_dat`** (the limit of the ci profile 4.65 s with the 3.1 s of the DAT with 2^L).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import blocks_files


def prepare(params: dict[str, Any], work: Path) -> None:
    blocks_files.make(params, work, "dat")


def load() -> None:
    from . import blocks_common as bc

    bc.warm()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    return blocks_files.imported(params, work)
