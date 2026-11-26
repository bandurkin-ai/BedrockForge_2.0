"""`GET /pit/scenarios/{id}/report?calendar=` — the pit-by-pit report of a stored result (подшаг 21.11; e21-api
§7; ADR 0028 decisions 18, 19). A VIEW of the files `data` and `sidecar:benches`: nothing is written and nothing
is read from the model; the calendar (`?calendar=` or the one of the scenario) is applied here, so a new calendar
is a new answer from the same result (Т8.11-8).

    out = report_json(conn, store, scenario_id, calendar=None)

`table` is the table of the pit-by-pit panel as TEXT of the core: the decimal comma, no-break spaces between the
thousands, a gap — «—» (never 0); the window shows `text` as it is, the chart takes `series`. The columns: № (k),
RF, the blocks, the ore and the waste (t), the stripping ratio W/O, the grade and the recoverable metal of each
element, CF, the NPV of the best and of the worst case. A shell without ore has no stripping ratio and no grade
(null); without Q_р there is no NPV (null, `npv_ready: false`, the warning `no_calendar`).
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any

from . import npv as npv_mod
from . import spec
from . import store as st
from . import store_data as sd
from .report import Report

if TYPE_CHECKING:
    import sqlalchemy as sa

    from bf_core.storage.blobs import BlobStore

    from .spec_parts import Calendar

DASH = "—"
METAL_UNITS = {"g/t": "унц", "ppm": "унц", "ppb": "унц", "%": "т"}


def load_report(conn: sa.Connection, store: BlobStore, scenario_id: int) -> tuple[list[dict[str, Any]], Report]:
    """The shells of the contract (without the NPV) and the `Report` of the two stored tables. 404; 409
    `pit_no_result`, `pit_scenario_broken`."""
    table = st.read_data(conn, store, scenario_id)
    benches = st.read_benches(conn, store, scenario_id)
    elements = sd.elements_of(table)
    shells = sd.table_shells(table, elements)
    rep = Report.from_columns(elements, table.to_pydict(), benches.to_pydict() if benches is not None else {})
    return shells, rep


def _num(x: Any) -> float | None:
    return None if x is None or not math.isfinite(float(x)) else float(x)


def _cell(value: float | None, decimals: int) -> dict[str, Any]:
    from bf_core.resources.forms import format_number

    return {"value": value, "text": DASH if value is None else format_number(value, decimals)}


def _columns(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cols = [("k", "№", "", 0), ("rf", "RF", "", 2), ("blocks", "Блоки", "шт", 0), ("ore_t", "Руда", "т", 0),
            ("waste_t", "Пустая", "т", 0), ("strip", "Вскрыша", "т/т", 2)]  # fmt: skip
    for e in elements:
        cols.append((f"{e['field']}:grade", f"{e['field']}, содержание", e["unit_label"] or "", 2))
        cols.append((f"{e['field']}:rec_metal", f"{e['field']}, извлекаемый металл", e["metal_unit"] or "", 0))
    cols += [("cf", "CF", "$", 0), ("best_npv", "NPV лучший", "$", 0), ("worst_npv", "NPV худший", "$", 0)]
    return [{"key": k, "title": t, "unit": u, "decimals": d} for k, t, u, d in cols]


def _table(shells: list[dict[str, Any]], elements: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for s in shells:
        cells: dict[str, Any] = {"rf": _cell(s["rf"], 2), "blocks": _cell(s["blocks"], 0),
                                 "ore_t": _cell(s["ore_t"], 0), "waste_t": _cell(s["waste_t"], 0),
                                 "strip": _cell(s["strip"], 2)}  # fmt: skip
        for e in elements:
            part = s["elements"].get(e["field"]) or {}
            cells[f"{e['field']}:grade"] = _cell(part.get("grade"), 2)
            cells[f"{e['field']}:rec_metal"] = _cell(part.get("rec_metal"), 0)
        cells["cf"] = _cell(s["cf"], 0)
        cells["best_npv"] = _cell(s["best"]["npv"], 0)
        cells["worst_npv"] = _cell(s["worst"]["npv"], 0)
        rows.append({"k": s["k"], "cells": cells})
    return {"columns": _columns(elements), "rows": rows}


def elements_of(params: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for e in params.get("elements") or []:
        unit = e.get("unit")
        out.append({"field": e["field"], "unit": unit, "unit_label": spec.grade_label(unit),
                    "metal_unit": METAL_UNITS.get(unit or ""), "price_label": spec.price_label(unit)})  # fmt: skip
    return out


def calendar_of(info: dict[str, Any]) -> Calendar:
    params = (info.get("meta") or {}).get("params") or {}
    return spec.parse_params({"calendar": params.get("calendar")}).calendar


def report_json(conn: sa.Connection, store: BlobStore, scenario_id: int, calendar: Calendar | None = None
                ) -> dict[str, Any]:  # fmt: skip
    """The answer of e21-api §7. 404 `pit_scenario_not_found`; 409 `pit_no_result`."""
    t0 = time.perf_counter()
    info = st.info(conn, scenario_id, store)
    if not info["has_result"]:
        raise st.no_result()
    meta = info["meta"]
    cal = calendar or calendar_of(info)
    shells, rep = load_report(conn, store, scenario_id)
    n = len(shells)
    ready = cal.q_ore is not None
    best: list[float | None] = [None] * n
    worst: list[float | None] = [None] * n
    warnings: list[dict[str, Any]] = []
    if ready:
        got = npv_mod.npv(rep, cal)
        best, worst = [_num(x) for x in got.best], [_num(x) for x in got.worst]
    else:
        warnings.append({"code": "no_calendar", "message": "Не задана переработка Q_р: NPV не рассчитан.",
                         "hint": "Задайте календарь в окне «Календарь»."})  # fmt: skip
    for s, b, w in zip(shells, best, worst, strict=True):
        s["best"], s["worst"] = {"npv": b}, {"npv": w}
    elements = elements_of(meta.get("params") or {})
    totals = rep.totals()
    totals["best"], totals["worst"] = {"npv": best[-1] if n else None}, {"npv": worst[-1] if n else None}
    series = {"k": [s["k"] for s in shells], "rf": [s["rf"] for s in shells], "ore_t": [s["ore_t"] for s in shells],
              "waste_t": [s["waste_t"] for s in shells], "cf": [s["cf"] for s in shells], "npv_best": best,
              "npv_worst": worst}  # fmt: skip
    out = {
        "scenario_id": scenario_id, "name": info["name"], "version": info["version"], "state": info["state"],
        "algo": meta.get("algo"), "params_hash": meta.get("params_hash"), "base_rf": 1.0, "model": meta.get("model"),
        "calendar": cal.canonical(), "npv_ready": ready, "elements": elements, "shells": shells, "totals": totals,
        "series": series, "table": _table(shells, elements), "warnings": warnings,
        "seconds": round(time.perf_counter() - t0, 4),
    }  # fmt: skip
    return sd.clean_json(out)  # type: ignore[no-any-return]
