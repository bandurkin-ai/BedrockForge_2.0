"""The synthetic project of the tests of the routes of the estimation (подшаг 18.13): 25 vertical holes 25 m apart, 20
samples of 2 m each (AU, DECL_W), a table of the lithology by depth (ZONE: окисл 0–12 m, перех 12–20, перв 20–40), a
block model of 10 × 10 × 8 blocks (ZONE by the depth of the centre), a stored variogram model of AU in the units of the
field. Deterministic: the same numbers on every run (the files `core/tests/data/estimate/contract_answers.json`).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from bf_core.blocks import store as bm
from bf_core.db import dialect
from bf_core.drillholes import store as dh
from bf_core.storage.blobs import BlobStore
from tests.estutil import W, vmodel

SIDE, PER_HOLE, STEP = 5, 20, 25.0
X0, Y0, Z0 = 500_000.0, 6_700_000.0, 300.0
MODEL = {"type": "semivariogram", "c0": 0.1, "common_axes": True,
         "structures": [{"type": "spherical", "c": 0.9, "ang1": 0, "ang2": 0, "ang3": 0, "a1": 60, "a2": 60,
                         "a3": 30}]}  # fmt: skip
GRID = {"corner": [X0 - 5.0, Y0 - 5.0, Z0 - 40.0], "counts": [10, 10, 8], "size": [10.0, 10.0, 5.0]}
LABELS = ["окисл", "перех", "перв"]
FIELDS = [{"name": "ZONE", "dtype": "cat", "labels": LABELS, "role": "code"}]


def noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def zone_of(depth: np.ndarray) -> np.ndarray:
    return np.where(depth < 12.0, 0, np.where(depth < 20.0, 1, 2)).astype(np.int32)


def fill(engine: Any, seed: int = 11) -> tuple[int, int, int]:
    """(set id, id of the table of the samples, id of the table of the lithology)."""
    rng = np.random.Generator(np.random.PCG64(seed))
    names = [f"С-{i:02d}" for i in range(SIDE * SIDE)]
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
        dh.replace_collars(conn, set_id, [
            dh.CollarRow(h, x=X0 + STEP * (i % SIDE), y=Y0 + STEP * (i // SIDE), z=Z0, td=2.0 * PER_HOLE,
                         azimuth=0.0, dip=-90.0) for i, h in enumerate(names)])  # fmt: skip
    holes = [h for h in names for _ in range(PER_HOLE)]
    depth = np.tile(np.arange(PER_HOLE) * 2.0, SIDE * SIDE)
    x = np.repeat(np.arange(SIDE * SIDE) % SIDE, PER_HOLE)
    au = np.exp(0.6 * np.sin(depth / 9.0 + x) + 0.4 * rng.standard_normal(depth.size))
    au[rng.random(depth.size) < 0.03] = np.nan  # a gap stays a gap
    w = rng.uniform(0.5, 1.5, depth.size)
    a = dh.write_table(engine, set_id, dh.TableData(
        name="Композиты", kind="assay", holes=holes, from_m=depth.tolist(), to_m=(depth + 2.0).tolist(),
        columns=[dh.ColumnSpec("AU", "number", "г/т", "component"), dh.ColumnSpec("DECL_W", "number")],
        values=[[None if np.isnan(v) else float(v) for v in au], w.tolist()]), job_id=None, tick=noop)  # fmt: skip
    # the lithology: three intervals a hole
    lh = [h for h in names for _ in range(3)]
    lf = [v for _ in names for v in (0.0, 12.0, 20.0)]
    lt = [v for _ in names for v in (12.0, 20.0, 40.0)]
    lz = [v for _ in names for v in LABELS]
    b = dh.write_table(engine, set_id, dh.TableData(
        name="Литология", kind="interval", holes=lh, from_m=lf, to_m=lt,
        columns=[dh.ColumnSpec("ZONE", "text")], values=[lz]), job_id=None, tick=noop)  # fmt: skip
    return set_id, int(a["table_id"]), int(b["table_id"])


def make_block_model(project: Any, store: BlobStore) -> int:
    n = 10 * 10 * 8
    p = np.arange(n, dtype=np.uint64)
    k = np.arange(n) // 100  # i is the fastest: the layer of the cell
    depth = Z0 - (GRID["corner"][2] + 5.0 * (k + 0.5))
    with bm.CellsWriterV2(store, GRID, FIELDS, row_group=400) as w:
        w.write(p, {"ZONE": zone_of(depth)})
        staged = w.finish()
    try:
        with dialect.write_tx(project.engine) as conn:
            return bm.commit_block_model(conn, store, staged, name="Модель оценки",
                                         source={"format": "dat", "file": "m.dat"})  # fmt: skip
    finally:
        staged.discard()


def build(project: Any) -> W:
    """The world in the project of the API (`projects_of(core).require_current()`)."""
    set_id, a, b = fill(project.engine)
    store = BlobStore(project.folder)
    w = W(project, store, set_id, a, b, make_block_model(project, store), 0, 0, 0)
    w.vz = vmodel(w, "AU · окисл", model=MODEL)
    return w


def row_body(w: W, label: str = "окисл", **over: Any) -> dict[str, Any]:
    """The body of a row «Параметры оценки»: OK of AU in the domain `label` of the blocks and of the samples."""
    out: dict[str, Any] = {
        "model_id": w.model, "target": {"domain_field": "ZONE", "label": label}, "disc": [2, 2, 1],
        "samples": {"set_id": w.set_id, "table": str(w.a), "field": "AU",
                    "domain": {"table": str(w.b), "field": "ZONE", "values": [label]}},
        "variogram": {"model_id": w.vz}}  # fmt: skip
    out.update(over)
    return out
