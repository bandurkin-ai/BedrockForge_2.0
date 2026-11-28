"""The import of a model that lies in no grid as a LIST of blocks (ADR 0030 «Замеры»; подшаг 27.4г, Д-45 А6).

The file of `blocks_files.write_list_dat`: 1 million blocks × 20 number fields, columns of 10 × 10 m cut along Z into
2–8 blocks of random height (no grid holds them: the grid refuses the file, the user chooses «Импортировать списком
блоков», `blocks.layout = "list"`). Everything is as in `blocks_import_dat` — the file read in portions, the roles of
the columns, the model written — but the blocks are not put into parents: the centres go to the cells of the enclosing
grid, the rows are sorted by (idx, cz, cy, cx), the repeated ones left out and the overlaps of the boxes counted
(`list_kernels`). Target: **≤ 1.3 × `blocks_import_dat`** (the limit of the ci profile 4.65 s → 6.05 s). Checked (a
failure stops the run): the rows, no duplicate, no overlap, Σ AU of the model equals that of the generator (1e-9) and
so does the volume of the boxes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import blocks_files


def prepare(params: dict[str, Any], work: Path) -> None:
    blocks_files.make({**params, "layout": "list"}, work, "dat")


def load() -> None:
    from . import blocks_common as bc

    bc.warm()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    return blocks_files.imported({**params, "layout": "list"}, work)
