"""The import of a Micromine DAT as a block model (ТЗ 5.3, 8.7.1; ADR 0023 «Замеры»: ci — 1 млн строк × 20 полей ≤ 30 с,
full — 20 млн × 20 **≤ 8 мин, ≤ 4 ГБ**).

The file of `blocks_files.write_dat` (the model of `tools/gen/blockmodel.py` with sub-blocks: three centres, three
sizes and 20 number fields of 8 bytes — 26 columns; `rows` rows; 25 % of the parents split into eight) in the folder
of the scenario (`prepare`, not timed). `import_model` — the body of the job of the import: the roles of the columns
and the grid found in the first rows, the columns read in portions into a spool, the rows put in the order of the
key, duplicates, overlaps and the volume checked, the file of the model written and committed. The stages are those of
the progress of the job (`stage_<name>_s`). The checks are those of `blocks_files.imported`: the rows, no duplicate,
no overlap, Σ AU and the first 1000 values of AU as they were written.
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
