"""Demo part «Скважины 2025» (ТЗ 7.5; ported from BF demo.py): 48 holes on a 50 m grid, surveys every 30 m,
2 m samples with AU (г/т) and CU (%) over an ore lens, and deliberate errors for the audit (ТЗ 7.12 «на
демо-проекте находятся намеренные ошибки»): a survey below TD, a negative grade overlapping the next sample,
a gap, an empty grade, a repeated sample number and a sharp bend.

Rows are written with the connection of the demo transaction (no nested write transactions).
"""

from __future__ import annotations

import math
import random

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_holes, dh_rows, dh_surveys, dh_tables
from bf_core.drillholes.store import create_set, pack
from bf_core.storage.blobs import BlobStore

SET_NAME = "Скважины 2025"


def add_drillholes(conn: sa.Connection, _store: BlobStore, rng: random.Random) -> None:
    now = dialect.utcnow()
    set_id = create_set(conn, SET_NAME)
    res = conn.execute(
        sa.insert(dh_tables).values(
            set_id=set_id,
            name="Пробы",
            name_key=dialect.name_key("Пробы"),
            kind="assay",
            row_count=0,
            rev=1,
            state="ready",
            created_at=now,
            updated_at=now,
        )
    )
    key = res.inserted_primary_key
    assert key is not None
    table_id = int(key[0])
    conn.execute(
        sa.insert(dh_columns),
        [
            {
                "table_id": table_id,
                "name": "AU",
                "kind": "number",
                "unit": "г/т",
                "slot": 0,
                "role": "component",
                "deleted": False,
                "position": 0,
            },
            {
                "table_id": table_id,
                "name": "CU",
                "kind": "number",
                "unit": "%",
                "slot": 1,
                "role": "component",
                "deleted": False,
                "position": 1,
            },
            {
                "table_id": table_id,
                "name": "Номер пробы",
                "kind": "text",
                "unit": None,
                "slot": 0,
                "role": "sample",
                "deleted": False,
                "position": 2,
            },
        ],
    )
    surveys: list[dict[str, object]] = []
    rows: list[tuple[object, ...]] = []
    for i in range(48):
        row, col = divmod(i, 8)
        name = f"DH-{i + 1:03d}"
        x = 5000 + col * 50 + rng.uniform(-5, 5)
        y = 8000 + row * 50 + rng.uniform(-5, 5)
        td = rng.choice([120, 150, 180, 220])
        az = 90 + rng.uniform(-8, 8)
        dip = -60 + rng.uniform(-5, 5)
        z = 420 + rng.uniform(-6, 6)
        status = "Завершена" if i % 7 else "В бурении"
        hole = conn.execute(
            sa.insert(dh_holes).values(
                set_id=set_id,
                name=name,
                x=x,
                y=y,
                z=z,
                td=float(td),
                azimuth=az,
                dip=dip,
                has_collar=True,
                attrs={"status": status, "stage": "2025"},
            )
        ).inserted_primary_key
        assert hole is not None
        hid = int(hole[0])
        for sd in range(0, td + 1, 30):
            bend = 25.0 if (i == 17 and sd == 60) else 0.0  # a sharp bend (SURVEY_DOGLEG)
            surveys.append(
                {
                    "set_id": set_id,
                    "hole_id": hid,
                    "depth": float(sd),
                    "azimuth": az + sd * 0.02 + bend,
                    "dip": dip + sd * 0.03,
                }
            )
        if i == 5:  # a survey below TD (SURVEY_BEYOND_TD)
            surveys.append({"set_id": set_id, "hole_id": hid, "depth": td + 40.0, "azimuth": az, "dip": dip})
        depth = 0.0
        while depth < td:
            ore = math.exp(-((depth - 90 - col * 6) ** 2) / 900)
            au = round(max(0.005, rng.lognormvariate(-2.2, 0.9) + 4 * ore * rng.random()), 3)
            cu = round(max(0.01, rng.gauss(0.2, 0.08) + 0.6 * ore), 3)
            sample = f"{name}-{int(depth / 2):03d}"
            if i == 20 and depth == 40.0:  # a gap 40–42 m (ASSAY_GAP)
                depth += 2.0
                continue
            if i == 30 and depth == 50.0:  # an empty grade (ASSAY_EMPTY_VALUE)
                au = math.nan
            if i == 40 and depth == 20.0:  # a repeated sample number (ASSAY_DUPLICATE_SAMPLE)
                sample = f"{name}-009"
            rows.append((table_id, hid, depth, depth + 2.0, pack([au, cu]), _txt(sample)))
            depth += 2.0
        if i == 11:  # a negative grade overlapping 10–12 and 12–14 (ASSAY_NEGATIVE_VALUE, ASSAY_OVERLAP)
            rows.append((table_id, hid, 10.0, 13.0, pack([-1.0, None]), _txt(f"{name}-X")))
    conn.execute(sa.insert(dh_surveys), surveys)
    dialect.bulk_insert(conn, dh_rows, ("table_id", "hole_id", "from_m", "to_m", "num", "txt"), rows)
    conn.execute(sa.update(dh_tables).where(dh_tables.c.id == table_id).values(row_count=len(rows)))


def _txt(sample: str) -> str:
    import json

    return json.dumps({"0": sample}, ensure_ascii=False)
