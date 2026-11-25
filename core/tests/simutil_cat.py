"""What the tests of the SIS and the co-SGS add to a world of the simulations (18.32b): a table of the drill holes
with a numeric field of the categories (`LITH`, the codes 1–3 by the position and the depth) whose rows are in a
shuffled order (the samples are put in the canonical order by the job: the codes must follow them by `row_id`), and a
numeric field of the block model that is the secondary variable (`SG`, a smooth function of the centre)."""

from __future__ import annotations

from typing import Any

import numpy as np

from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.drillholes import store as dh
from tests.test_blocks_fields_store import add_fields

LITH, SG = "LITH", "SG"
CODES = (1, 2, 3)


def lith_code(x_rel: float, k: int) -> int:
    """The category of the sample `k` (2 m intervals from the collar) of a hole `x_rel` m from the first one."""
    return 1 + min(2, int((x_rel + 2.0 * k) // 28.0))


def add_lith(engine: Any, set_id: int, holes: list[tuple[str, float]], per_hole: int = 20, seed: int = 5) -> int:
    """The table «Фации» (LITH) of the set: `holes` — [(name, x_rel)]; the rows are shuffled. → the id of the table."""
    names, lo, hi, codes = [], [], [], []
    for h, x in holes:
        for k in range(per_hole):
            names.append(h)
            lo.append(2.0 * k)
            hi.append(2.0 * k + 2.0)
            codes.append(float(lith_code(x, k)))
    order = np.random.default_rng(seed).permutation(len(names))
    got = dh.write_table(engine, set_id, dh.TableData(
        name="Фации", kind="interval", holes=[names[i] for i in order], from_m=[lo[i] for i in order],
        to_m=[hi[i] for i in order], columns=[dh.ColumnSpec(LITH, "number")],
        values=[[codes[i] for i in order]]), job_id=None, tick=lambda _p, _m: None)  # fmt: skip
    return int(got["table_id"])


def add_secondary(project: Any, store: Any, model_id: int, name: str = SG, gaps: bool = False) -> None:
    """A numeric field of the block model — a smooth function of the centre (the density of the rock); `gaps` — NaN
    for every fifth row (a gap is not 0)."""
    with dialect.read_tx(project.engine) as conn:
        c = Model.open(conn, store, model_id).centres()
    sg = 2.6 + 0.3 * np.sin((c[:, 0] - c[:, 0].min()) / 40.0) - 0.002 * (c[:, 2].max() - c[:, 2])
    if gaps:
        sg[::5] = np.nan
    add_fields(project, store, model_id, [{"name": name, "dtype": "f8", "unit": "т/м³", "role": "density"}],
               {name: sg})  # fmt: skip
