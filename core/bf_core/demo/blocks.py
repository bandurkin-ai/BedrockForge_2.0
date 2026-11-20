"""Demo part of Э11 (ТЗ 7.5; ADR 0017 §3): the regular block model «Блочная модель Au» around the ore bodies.

  grid    40 × 40 × 20 cells of 10 × 10 × 10 m from the corner (5000, 7950, 210): X 5000–5400, Y 7950–8350,
          Z 210–410 — under the ground (411–432 m there), around «Рудное тело 1» (Z 291–357), «Рудное тело 2»
          (Z 374–388) and the top of «Рудное тело 3» (Z 207–253); every cell is occupied (32 000);
  AU      number, г/т: the lens of «Рудное тело 1» (a slab about Z 324) and the ellipsoid of «Рудное тело 2»
          over a low background, with a log-normal noise; about 8 % of the cells have no value (empty, NaN —
          never 0);
  DOMAIN  category: «зона разлома» within 10 m of the plane of «Разлом», else «руда» where the grade without
          the noise is ≥ 0.5 g/t, else «порода»; never empty.

Written through `blocks.store.CellsWriter` + `commit_block_model`, as an import writes them. The numbers come from
a generator of its own with a fixed seed: the random stream of the demo is not touched, the other parts stay as
they were.
"""

from __future__ import annotations

import math
import random
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.storage.blobs import BlobStore

from .wireframes import FAULT_DIP, FAULT_TOP, LENS, LENS_AXES, LENS_TURN

NAME = "Блочная модель Au"
CORNER = (5000.0, 7950.0, 210.0)
COUNTS = (40, 40, 20)
SIZE = (10.0, 10.0, 10.0)
GRID: dict[str, Any] = {"corner": list(CORNER), "counts": list(COUNTS), "size": list(SIZE)}
AU, DOMAIN = "AU", "DOMAIN"
LABELS = ["руда", "порода", "зона разлома"]
FIELDS: list[dict[str, Any]] = [
    {"name": AU, "kind": "number", "unit": "г/т"},
    {"name": DOMAIN, "kind": "category", "labels": LABELS},
]
SEED = 11
MISSING = 0.08  # share of the cells without AU
ORE_CUT = 0.5  # g/t
LENS_1 = ((5013.0, 5448.0), (7975.0, 8275.0), 324.0, 22.0)  # x range, y range, z of the middle, half-thickness
FAULT_ZONE = 10.0  # m


def cells() -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Cell numbers (all of them, ascending) and the values of AU and DOMAIN."""
    from bf_core.blocks import schema as bs

    n = math.prod(COUNTS)
    idx = np.arange(n, dtype=np.uint64)
    x, y, z = bs.centers(GRID, idx).T
    (x0, x1), (y0, y1), zc, half = LENS_1
    # «Рудное тело 1»: a slab, fading outside its plan
    inside = np.clip(np.minimum.reduce([x - x0, x1 - x, y - y0, y1 - y]) / 40.0 + 0.5, 0.0, 1.0)
    grade = 2.5 * np.exp(-(((z - zc) / half) ** 2)) * inside
    # «Рудное тело 2»: the turned ellipsoid
    t = math.radians(LENS_TURN)
    dx, dy, dz = x - LENS[0], y - LENS[1], z - LENS[2]
    u, v = dx * math.cos(t) + dy * math.sin(t), -dx * math.sin(t) + dy * math.cos(t)
    a, b, c = LENS_AXES
    grade += 4.0 * np.exp(-((u / a) ** 2 + (v / b) ** 2 + (dz / (2 * c)) ** 2))
    grade += 0.05
    gen = np.random.default_rng(SEED)
    au = grade * np.exp(gen.normal(0.0, 0.35, n))
    au[gen.random(n) < MISSING] = np.nan
    # «Разлом»: x of its plane at the height z (steep to the west from its upper edge)
    fault_x = FAULT_TOP[0] - (FAULT_TOP[1] - z) / math.tan(math.radians(FAULT_DIP))
    domain = np.where(grade >= ORE_CUT, 0, 1).astype(np.int32)
    domain[np.abs(x - fault_x) <= FAULT_ZONE] = 2
    return idx, {AU: au, DOMAIN: domain}


def add_blocks(conn: sa.Connection, store: BlobStore, _rng: random.Random) -> None:
    from bf_core.blocks import store as bm

    idx, values = cells()
    with bm.CellsWriter(store, GRID, FIELDS) as w:
        w.write(idx, values)
        staged = w.finish()
    try:
        bm.commit_block_model(conn, store, staged, name=NAME, sort_key=1.0)
    finally:
        staged.discard()
