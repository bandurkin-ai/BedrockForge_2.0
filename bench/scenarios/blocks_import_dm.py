"""The import of a Datamine DM as a block model (ТЗ 5.3, 8.7.1; Д-13; ADR 0023 «Замеры»: ci — 1 млн строк × 20 полей
≤ 30 с, full — 20 млн × 20 **≤ 8 мин, ≤ 4 ГБ**).

The file of `blocks_files.write_dm` (the model of `tools/gen/blockmodel.py` with sub-blocks: IJK, three centres, three
sizes and 20 number fields; the origin and the counts of the grid are implicit fields; pages of 512 words of 8 bytes;
`rows` rows; 25 % of the parents split into eight) in the folder of the scenario (`prepare`, not timed).
`import_model` as in `blocks_import_dat`: IJK is the number of the parent (from 0), the centres and the sizes give
the sub-block; the file is read by windows of 64 MB (a map of the whole file would hold 4.5 GB of memory). The checks
are those of `blocks_files.imported`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import blocks_files


def prepare(params: dict[str, Any], work: Path) -> None:
    blocks_files.make(params, work, "dm")


def load() -> None:
    from . import blocks_common as bc

    bc.warm()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    return blocks_files.imported(params, work)
