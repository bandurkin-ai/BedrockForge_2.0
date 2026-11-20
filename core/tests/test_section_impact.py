"""The warning of ADR 0016 decision 4 (Э11 11.17): how many contours of a series leave the windows of its sections
when its geometry changes — `sections/impact.py`, `POST /section-series/{id}/impact`."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pytest

from bf_core.sections.contours import from_stored, series_contours
from bf_core.sections.geometry import series_geometry
from bf_core.sections.impact import impact_of
from bf_core.sections.names import section_names
from tests.test_import_job import API
from tests.test_lines_edit_api import body, new_layer, save

URL = f"{API}/section-series"
UTM = np.array([512345.678, 6789012.345, 250.0])
# five sections 0, 25, …, 100 along the east from P1, windows of 12,5 m on each side: they abut
SERIES = {
    "type": "vertical",
    "p1": [float(UTM[0]), float(UTM[1]), 0.0],
    "az": 90.0,
    "s0": 0.0,
    "s1": 100.0,
    "step": 25.0,
    "towards": 12.5,
    "away": 12.5,
}


def triangle(centre: np.ndarray, r: float = 0.5) -> np.ndarray:
    return centre + np.array([[0.0, 0.0, 0.0], [r, 0.0, r], [0.0, r, -r]])


def contours_at(geom: Any, ks: list[int]) -> Any:
    """A closed triangle at the origin of each section k (stored form: the first vertex repeated)."""
    parts = [np.vstack([t, t[:1]]) for t in (triangle(geom.origin(k) + [0.0, 0.0, UTM[2]]) for k in ks)]
    offsets = np.concatenate([[0], np.cumsum([len(p) for p in parts])])
    layer = from_stored(7, "Контуры", 1, np.vstack(parts), offsets, np.ones(len(ks)), np.arange(len(ks)))
    return series_contours(geom, [layer])


def test_shifted_series_counts_the_contours_outside_and_moved() -> None:
    geom = series_geometry(SERIES)
    sc = contours_at(geom, [0, 1, 2, 3, 4])
    assert sc.section.tolist() == [0, 1, 2, 3, 4]
    names = section_names(SERIES)
    shifted = series_geometry({**SERIES, "s0": 50.0, "s1": 150.0})  # sections at 50 … 150: 0 and 25 are left out
    out = impact_of(sc, shifted, names)
    assert (out["total"], out["outside"], out["moved"]) == (5, 2, 3)  # 50, 75, 100 — now sections 1, 2, 3 → 0, 1, 2
    assert out["examples"] == [
        f"Разрез {names[0]}: линия 1 слоя «Контуры»",
        f"Разрез {names[1]}: линия 2 слоя «Контуры»",
    ]
    same = impact_of(sc, series_geometry({**SERIES, "s1": 200.0}), names)  # more sections: nobody leaves
    assert (same["outside"], same["moved"]) == (0, 0)
    narrow = impact_of(sc, series_geometry({**SERIES, "towards": 0.2, "away": 0.2, "step": 25.0}), names)
    assert narrow["outside"] == 5  # a triangle 0,5 m deep does not fit a window of 0,4 m
    turned = impact_of(sc, series_geometry({**SERIES, "az": 0.0}), names)
    assert turned["outside"] + turned["moved"] >= 4  # a turned series: the contours are not where they were


def test_contours_in_no_window_now_are_not_counted() -> None:
    geom = series_geometry(SERIES)
    sc = contours_at(geom, [0, 2])
    normal = geom.origin(1) - geom.origin(0)  # one step along the normal of the sections
    far = triangle(geom.origin(4) + [0.0, 0.0, UTM[2]] + 20 * normal)  # beyond the series
    parts = [np.vstack([t, t[:1]]) for t in [*(triangle(geom.origin(k) + [0.0, 0.0, UTM[2]]) for k in (0, 2)), far]]
    offsets = np.concatenate([[0], np.cumsum([len(p) for p in parts])])
    sc = series_contours(geom, [from_stored(7, "К", 1, np.vstack(parts), offsets, np.ones(3), np.arange(3))])
    assert sc.section.tolist() == [0, 2, -1]
    out = impact_of(sc, series_geometry({**SERIES, "s0": 50.0, "s1": 150.0}), section_names(SERIES))
    assert (out["total"], out["outside"], out["moved"]) == (2, 1, 1)
    empty = series_contours(geom, [])
    assert impact_of(empty, geom, []) == {"total": 0, "outside": 0, "moved": 0, "examples": []}


def test_speed_2000_contours() -> None:
    """ADR 0017 §7 / PROGRESS 11.17: 2 000 contours ≤ 0,2 s (the old sections and the check against the new ones)."""
    meta = {**SERIES, "s1": 1975.0}  # 80 sections
    geom = series_geometry(meta)
    rng = np.random.default_rng(1)
    parts, ks = [], rng.integers(0, geom.count, 2000)
    for k in ks:
        ring = geom.origin(int(k)) + [0.0, 0.0, UTM[2]] + rng.normal(0, 3, (20, 3)) * [0.1, 1.0, 1.0]
        parts.append(np.vstack([ring, ring[:1]]))
    offsets = np.concatenate([[0], np.cumsum([len(p) for p in parts])])
    layer = from_stored(7, "К", 1, np.vstack(parts), offsets, np.ones(2000), np.arange(2000))
    names = section_names(meta)
    new = series_geometry({**meta, "s0": 10.0, "s1": 1985.0})
    impact_of(series_contours(geom, [layer]), new, names)  # numba kernels warm
    best = 1e9
    for _ in range(3):
        t0 = time.perf_counter()
        out = impact_of(series_contours(geom, [layer]), new, names)
        best = min(best, time.perf_counter() - t0)
    assert out["total"] == 2000 and out["outside"] + out["moved"] > 0
    assert best <= 0.2, f"{best:.3f} s"


# ---------------------------------------------------------------- over HTTP


@pytest.fixture()
def series_router(monkeypatch: pytest.MonkeyPatch) -> None:
    import bf_core.api.app as app_module

    if "sections" not in app_module.ROUTER_MODULES:
        monkeypatch.setattr(app_module, "ROUTER_MODULES", (*app_module.ROUTER_MODULES, "sections"))


@pytest.fixture()
def sc(series_router, pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects", json={"name": "Влияние"}).status_code == 200
    return pclient


def test_impact_over_http(sc) -> None:  # type: ignore[no-untyped-def]
    plain = sc.post(URL, json={"params": SERIES}).json()
    r = sc.post(f"{URL}/{plain['id']}/impact", json={"params": {"s0": 50.0, "s1": 150.0}})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "series_id": plain["id"],
        "version": 1,
        "layer_id": None,
        "layer": None,
        "total": 0,
        "outside": 0,
        "moved": 0,
        "examples": [],
    }

    layer = new_layer(sc, "Контуры")
    geom = series_geometry(SERIES)
    tris = [triangle(geom.origin(k) + [0.0, 0.0, UTM[2]]) for k in range(5)]
    assert save(sc, layer["id"], 1, body([(-1, True, t) for t in tris])).status_code == 200
    s = sc.post(URL, json={"params": {**SERIES, "contour_layer": layer["id"]}}).json()
    r = sc.post(f"{URL}/{s['id']}/impact", json={"params": {"s0": 50.0, "s1": 150.0}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["layer_id"], out["layer"], out["total"], out["outside"], out["moved"]) == (
        layer["id"],
        "Контуры",
        5,
        2,
        3,
    )
    assert len(out["examples"]) == 2 and out["examples"][0].startswith("Разрез ")
    assert sc.get(f"{URL}/{s['id']}").json()["version"] == 1  # nothing stored

    bad = sc.post(f"{URL}/{s['id']}/impact", json={"params": {"step": -1}})
    assert (
        bad.status_code == 422
        and bad.json()["code"] == "bad_series"
        and bad.json()["field"] == "step"
        and bad.json()["hint"]
    )
    missing = sc.post(f"{URL}/999999/impact", json={"params": {}})
    assert missing.status_code == 404 and missing.json()["code"] == "series_not_found"
