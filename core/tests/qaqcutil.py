"""Records of QAQC in memory for the tests of the kernels (ТЗ 8.4; ADR 0020): a table of rows → `Records`, the
settings and the reference of a test, hard random data with every kind of record, five filters for Т8.4-8."""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pytest

from bf_core.qaqc import config as qc
from bf_core.qaqc import records as r
from bf_core.qaqc.calc import canonical
from bf_core.qaqc.limits import QaqcError

NAN = math.nan
DAY = 86_400_000.0
BASE = 1_704_067_200_000.0  # 2024-01-01
CONFIG: dict[str, Any] = {"types": dict(qc.DEFAULT_TYPES), "dl": [], "limits": qc.default_limits(),
                          "empty_is_orig": True}  # fmt: skip


def build(rows: list[dict[str, Any]], unit: str | None = "ppm", has_dl: bool = True) -> r.Records:
    """Records in memory of the rows {type, crm, lab, batch, day, seq, hole, au, q, prev, dl, zone, depth, id,
    parent (the value of the parent sample), parent_row (−1 — not found; 0 when a parent value is given),
    parent_lab}."""
    n = len(rows)

    def texts(key: str) -> list[str | None]:
        return [row.get(key) for row in rows]

    def floats(key: str) -> np.ndarray:
        return np.array([NAN if row.get(key) is None else row[key] for row in rows], dtype=np.float64)

    schema = r.Schema((r.Element("AU", unit, has_dl),), (r.Extra("Zone", r.TEXT), r.Extra("Depth", r.NUMBER)))
    day = floats("day")
    cols: dict[str, Any] = {
        r.SAMPLE_ID: [row.get("id", f"S{i:05d}") for i, row in enumerate(rows)],
        r.PARENT_ID: [None] * n, r.HOLE: texts("hole"), r.TYPE_CODE: texts("type"), r.CRM_ID: texts("crm"),
        r.LAB: texts("lab"), r.BATCH: texts("batch"), r.PARENT_LAB: texts("parent_lab"), r.DATE: BASE + day * DAY,
        r.SEQ: floats("seq"), r.PREV_ROW: np.full(n, -1, np.int32),
        r.PARENT_ROW: np.array([row.get("parent_row", 0 if row.get("parent") is not None else -1) for row in rows],
                               dtype=np.int32).reshape(n),
        r.v("AU"): floats("au"), r.q("AU"): np.array([row.get("q", 0) for row in rows], dtype=np.uint8),
        r.p("AU"): floats("parent"), r.b("AU"): floats("prev"), r.x("Zone"): texts("zone"),
        r.x("Depth"): floats("depth"),
    }  # fmt: skip
    if has_dl:
        cols[r.dl("AU")] = floats("dl")
    return r.Records.of(schema, cols)


def crm_row(crm_id: str, mean: float, sd: float, unit: str | None = "ppm", element: str = "AU") -> dict[str, Any]:
    return {"crm_id": crm_id, "element": element, "mean": mean, "sd": sd, "unit": unit, "source": None}


def refused(code: str, param: str | None, call: Any) -> QaqcError:
    with pytest.raises(QaqcError) as e:
        call()
    assert (e.value.code, e.value.param) == (code, param) and e.value.message and e.value.hint
    return e.value


def json_ready(answer: Any) -> None:
    """Only what JSON holds: no numpy numbers, no NaN (`canonical` refuses those)."""
    json.loads(canonical(answer))
    text = json.dumps(answer, allow_nan=False)
    assert json.loads(text) == json.loads(json.dumps(answer))


def hard_records(n: int = 6000, seed: int = 5) -> tuple[r.Records, dict[str, Any], list[dict[str, Any]]]:
    """Random records with every kind of trouble, the settings and the reference for them: codes «STD», «QA»
    (→ DUP_P), «ump», «???» (not recognized) and empty; CRM in two spellings, without a reference and in other
    units; blanks on the border of k_б·DL; pairs without a parent, without a value, negative, zero, below DL."""
    rng = np.random.default_rng(seed)
    pair = np.random.default_rng(seed + 1000)  # the fields of the pairs: the rest stays as it was without them
    spread = {"DUP_F": 0.15, "QA": 0.04, "ump": 0.05}
    kinds = rng.choice(["STD", "CRM", "BLK", "BLANK", "ORIG", None, "DUP_F", "ump", "???", "QA"], n,
                       p=[0.2, 0.15, 0.1, 0.1, 0.15, 0.05, 0.1, 0.05, 0.05, 0.05])  # fmt: skip
    crm = np.array(["G-1", "g-1", "G-2", "OREAS 7", "X-0", None, "Z-9"], dtype=object)
    mu = {"G-1": 1.0, "g-1": 1.0, "G-2": 1.1, "OREAS 7": 4.0, "X-0": 2.0, None: 3.0, "Z-9": 0.5}
    rows = []
    for i in range(n):
        kind = kinds[i]
        is_crm = kind in ("STD", "CRM")
        name = crm[rng.integers(0, len(crm))] if is_crm else None
        if is_crm and rng.random() < 0.03:
            au: float | None = 0.52  # another standard in the bag: «K-5» of the reference
        elif is_crm:
            au = mu[name] * (1 + 0.05 * rng.integers(-8, 9) / 2) + (1e-9 if rng.random() < 0.2 else 0.0)
        elif kind in ("BLK", "BLANK"):
            au = float(rng.choice([0.005, 0.02, 0.05, 0.0500001, 0.06, 0.3]))
        else:
            au = float(np.round(rng.lognormal(0, 1), 3))
        rows.append({
            "id": f"S{i:05d}" if rng.random() > 0.01 else None, "type": kind, "crm": name,
            "lab": rng.choice(["L1", "L2", "l2", None], p=[0.4, 0.3, 0.2, 0.1]),
            "batch": None if rng.random() < 0.05 else f"B{rng.integers(0, 150):03d}",
            "day": None if rng.random() < 0.1 else float(rng.integers(0, 120)) + (0.5 if rng.random() < 0.1 else 0.0),
            "seq": None if rng.random() < 0.1 else float(rng.integers(1, 40)),
            "hole": rng.choice(["DH-1", "DH-10", "RC-7", None]), "au": None if rng.random() < 0.04 else au,
            "q": int(rng.random() < 0.3) if kind in ("BLK", "BLANK") else 0,
            "prev": None if rng.random() < 0.2 else float(np.round(rng.lognormal(0, 1), 3)),
            "dl": float(rng.choice([0.02, 0.01])) if rng.random() < 0.1 else None,
            "zone": rng.choice(["Ox", "Sulf", "Trans", None]),
            "depth": None if rng.random() < 0.1 else float(rng.integers(0, 300)),
        })  # fmt: skip
        if kind in spread:
            row, u = rows[-1], pair.random()
            if u < 0.08:
                continue  # the parent sample was not found
            row["parent_row"] = int(pair.integers(0, n))
            row["parent_lab"] = pair.choice(["L1", "L2", None], p=[0.5, 0.4, 0.1]) if kind == "ump" else row["lab"]
            row["parent"] = None if u < 0.12 else float(np.round(au * (1 + spread[kind] * pair.standard_normal()), 3))
            if u > 0.97:
                row["parent"], row["au"] = (0.0, 0.0) if u > 0.985 else (-0.01, row["au"])
    ref = [
        crm_row("G-1", 1.0, 0.05), crm_row("G-2", 1100.0, 55.0, "ppb"), crm_row("oreas 7", 4.0, 0.2),
        crm_row("X-0", 2.0, 0.1, "oz/t"), crm_row("K-5", 0.52, 0.03), crm_row("G-1", 9.0, 0.1, element="CU"),
    ]  # fmt: skip
    cfg = {**CONFIG, "types": {**CONFIG["types"], "QA": "DUP_P"},
           "dl": [{"element": "AU", "lab": "L1", "value": 0.01, "unit": "ppm"},
                  {"element": "AU", "lab": "L2", "value": 20.0, "unit": "ppb"}]}  # fmt: skip
    return build(rows), cfg, ref


FIVE = [
    {"labs": ["L1"]},
    {"date_from": "2024-01-15", "date_to": "2024-03-20", "types": ["CRM", "BLANK", "ORIG"]},
    {"crm": ["G-1", "G-2"], "where": [{"field": "seq", "op": "le", "value": 30}]},
    {"hole_prefix": "DH", "where": [{"field": "x:Zone", "op": "ne", "value": "Trans"},
                                    {"field": "x:Depth", "op": "between", "value": [20, 250]}]},
    {"batches": [f"B{i:03d}" for i in range(0, 150, 3)], "date_to": "2024-04-01",
     "where": [{"field": "sample_id", "op": "ne", "value": "S00010"},
               {"field": "lab", "op": "in", "value": ["L2", "l1"]}]},
]  # fmt: skip
