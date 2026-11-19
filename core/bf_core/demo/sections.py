"""Demo part of Э10 (ТЗ 7.5, 8.1; ADR 0016 §1, §7, решение 2): two series of sections — what «Новая серия»
gives, so that «Каркас по серии», the shadow and the step `]` have something to show at once.

  «Разрезы рудного тела 3»  a vertical series along the section lines of the hole grid Y = 8000 … 8250 (A = 90°,
                            Δ = 50 m, windows 25 / 25 m — they abut): its six sections hold the contours of
                            «Контуры рудного тела 3» (`contours.py`), one section each, and that layer is the
                            contour layer of the series. The contours carry the attributes of decision 2:
                            `SECTION_SET` (the number of the series), `SECTION` (k + 1) and `SECTION_NAME`
                            (8000N …); the tie lines are on no section and have them empty;
  «Планы по рельефу»        a series of plans every 2 m (the interval of the isolines) from the highest isoline of
                            «Рельеф» down to the lowest, windows 1 / 1 m: every plan cuts the relief, and the
                            isolines of its level («Рельеф (изолинии)», both layers) lie on its plane exactly. It
                            has no contour layer: the isolines are open lines in two layers, and with «Основные»
                            (every 10 m) four plans of five would have no contours.

The section of a contour is computed by the same rule as everywhere (`SeriesGeometry.line_sections`): the
attributes are what the series says, not a copy of the coordinates. The body «Рудное тело 3» of Э9 stays as it
was (stitched with ties); «Каркас по серии» on the first series builds the same ore body from the contours.
Nothing is taken from the random stream of the demo.
"""

from __future__ import annotations

import math
import random
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.db.schema import objects
from bf_core.storage.blobs import BlobStore

from .contours import INTERVAL, ISOLINES_FILE, SECTIONS
from .geo import AREA

ORE_SERIES, PLANS = "Разрезы рудного тела 3", "Планы по рельефу"
ORE_STEP = SECTIONS[1] - SECTIONS[0]  # 50 m: the rows of the hole grid
PLAN_STEP = INTERVAL  # 2 m: a plan on every level of the isolines
SET, SECTION, SECTION_NAME = "SECTION_SET", "SECTION", "SECTION_NAME"


def ore_params(layer_id: int | None) -> dict[str, Any]:
    """The vertical series of the contours: P1 on the first section line, v̂ = +Y (A = 90°), names «{Y:.0f}N»."""
    return {
        "type": "vertical", "p1": [AREA[0] + 100.0, SECTIONS[0], 0.0], "az": 90.0, "s0": 0.0,
        "s1": SECTIONS[-1] - SECTIONS[0], "step": ORE_STEP, "towards": ORE_STEP / 2, "away": ORE_STEP / 2,
        "shadow": "prev", "contour_layer": layer_id,
    }  # fmt: skip


def plan_params(top: float, bottom: float, layer_id: int | None = None) -> dict[str, Any]:
    """Plans from `top` down to `bottom` every PLAN_STEP (north up, names «{Z:.0f}»)."""
    return {
        "type": "plan", "p1": [AREA[0], AREA[1], top], "az": 90.0, "s0": 0.0, "s1": top - bottom,
        "step": PLAN_STEP, "towards": PLAN_STEP / 2, "away": PLAN_STEP / 2, "shadow": "none",
        "contour_layer": layer_id,
    }  # fmt: skip


def next_number(conn: sa.Connection) -> int:
    """The number the next series of the project gets (as `sections/store.create` gives it)."""
    from bf_core.sections import store

    return max((int(s["number"]) for s in store.list_series(conn) if s["number"] is not None), default=0) + 1


def section_attrs(lines: list[np.ndarray], closed: list[bool], number: int) -> dict[str, list[Any]]:
    """The columns `SECTION_SET`, `SECTION`, `SECTION_NAME` of the lines of the contour layer: a closed line in
    the window of a section of the ore series gets the series and the section; the others (the ties) — empty."""
    from bf_core.sections.geometry import series_geometry
    from bf_core.sections.names import section_names

    meta = ore_params(None)
    geom = series_geometry(meta)
    names = section_names(meta)
    offsets = np.concatenate([[0], np.cumsum([len(p) for p in lines])])
    found = geom.line_sections(np.vstack(lines), offsets).tolist()
    on = [int(k) if c and k >= 0 else -1 for k, c in zip(found, closed, strict=True)]
    if any(c and k < 0 for k, c in zip(on, closed, strict=True)):
        raise AssertionError("a demo contour is in no window of its series")
    return {
        SET: [float(number) if k >= 0 else math.nan for k in on],
        SECTION: [float(k + 1) if k >= 0 else math.nan for k in on],
        SECTION_NAME: [names[k] if k >= 0 else None for k in on],
    }


def add_ore_series(conn: sa.Connection, layer_id: int, number: int) -> int:
    """The series of «Контуры рудного тела 3»; its number must be the one written into the contours."""
    from bf_core.sections import store

    series = store.create(conn, ore_params(layer_id), name=ORE_SERIES)
    if series["number"] != number:
        raise AssertionError(f"the demo series got the number {series['number']}, the contours say {number}")
    return int(series["id"])


def isoline_levels(conn: sa.Connection, store: BlobStore) -> np.ndarray:
    """The levels (ELEV) of all the isolines of «Рельеф (изолинии)»."""
    from bf_core.geo.contours import ELEV
    from bf_core.lines import store as ls

    file_id = conn.execute(
        sa.select(objects.c.id).where(objects.c.kind == ls.LINE_FILE, objects.c.name == ISOLINES_FILE)
    ).scalar_one()
    parts = [ls.read_layer(conn, store, i).attrs.column(ELEV).to_numpy() for i in ls.layer_ids(conn, int(file_id))]
    return np.concatenate(parts).astype(np.float64)


def add_plans(conn: sa.Connection, store: BlobStore, _rng: random.Random) -> None:
    from bf_core.sections import store as ss

    elev = isoline_levels(conn, store)
    top, bottom = float(elev.max()), float(elev.min())  # every plan cuts the relief: isolines on each
    ss.create(conn, plan_params(top, bottom), name=PLANS)
