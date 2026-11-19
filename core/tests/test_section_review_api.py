"""Э10, 10.28 — the review of the core of the series of sections: what the API must answer 422 with {code, message,
hint, field}, not 500.

* (2) a key of an own window that `str.isdigit` takes but `int` does not («²», 5 000 digits), an integer beyond
  float64 in JSON (`s0`, `p1`, a vertex of a polyline) — 422 `bad_series` with the field;
* (3) ids beyond 64 bits in `?layers=`, `{layers}` of the links, `{wireframe_ids}` of the cache, `contour_layer` —
  422 before any SQL (0 < id < 10¹², as `viewstate/keys._id`).
"""

from __future__ import annotations

from typing import Any

import pytest

from bf_core.sections import geometry, polyline
from bf_core.sections.base import SeriesError
from tests.test_import_job import API

URL = f"{API}/section-series"
BASE = {"type": "vertical", "p1": [1000, 5000, 0], "az": 90, "s0": 0, "s1": 100, "step": 25}
HUGE = 10**400  # an integer of JSON that float() cannot take
BIG_ID = 10**19  # beyond int64


@pytest.fixture()
def series_router(monkeypatch: pytest.MonkeyPatch) -> None:
    import bf_core.api.app as app_module

    if "sections" not in app_module.ROUTER_MODULES:
        monkeypatch.setattr(app_module, "ROUTER_MODULES", (*app_module.ROUTER_MODULES, "sections"))


@pytest.fixture()
def sc(series_router, pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects", json={"name": "Ревью серий"}).status_code == 200
    return pclient


def error_of(r: Any, code: str, field: str | None = None) -> dict[str, Any]:
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == code and body["message"] and body["hint"], body
    if field is not None:
        assert body.get("field") == field, body
    return body  # type: ignore[no-any-return]


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"overrides": {"²": {"away": 5}}}, "overrides"),
        ({"overrides": {"1" * 5000: {"away": 5}}}, "overrides"),
        ({"overrides": {"١": {"away": 5}}}, "overrides"),  # an Arabic-Indic digit: isdecimal, not ASCII
        ({"s0": HUGE}, "s0"),
        ({"s1": -HUGE}, "s1"),
        ({"p1": [HUGE, 5000, 0]}, "p1"),
        ({"az": HUGE}, "az"),
        ({"step": HUGE}, "step"),
        ({"type": "polyline", "polyline": [[0, 0], [HUGE, 0]]}, "polyline"),
        ({"type": "polyline", "polyline": [[0, 0], [10, 0]], "corridor": HUGE}, "polyline"),
    ],
)
def test_bad_numbers_and_keys_are_422_with_the_field(sc, change, field) -> None:  # type: ignore[no-untyped-def]
    error_of(sc.post(URL, json={"params": {**BASE, **change}}), "bad_series", field)
    assert sc.get(URL).json()["series"] == []


def test_a_patch_with_a_bad_key_is_422(sc) -> None:  # type: ignore[no-untyped-def]
    r = sc.post(URL, json={"params": BASE})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    r = sc.patch(f"{URL}/{sid}", json={"expected_version": 1, "params": {"overrides": {"²": {"away": 5}}}})
    error_of(r, "bad_series", "overrides")


@pytest.mark.parametrize(
    "meta",
    [
        {**BASE, "s0": HUGE},
        {**BASE, "p1": [HUGE, 0, 0]},
        {**BASE, "p2": [HUGE, 0], "az": None},
        {**BASE, "overrides": {"²": {"away": 5}}},
        {**BASE, "overrides": {"1" * 5000: {"away": 5}}},
        {"type": "polyline", "polyline": [[0, 0], [HUGE, 0]]},
        {"type": "polyline", "polyline": [[0, 0], [10, 0]], "corridor": HUGE},
    ],
)
def test_the_geometry_of_a_stored_meta_refuses_with_series_error(meta) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SeriesError) as e:
        geometry.series_geometry(meta)
    assert e.value.field is not None and e.value.hint
    if meta["type"] == "polyline":
        with pytest.raises(SeriesError):
            polyline.polyline_section(meta)


def test_ids_beyond_the_limit_are_422(sc) -> None:  # type: ignore[no-untyped-def]
    layer = sc.post(f"{API}/lines/layers", json={"name": "Контуры", "color": "#ff8800"})
    assert layer.status_code == 201, layer.text
    lid = layer.json()["id"]
    r = sc.post(URL, json={"params": {**BASE, "contour_layer": lid}})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    error_of(sc.post(URL, json={"params": {**BASE, "contour_layer": BIG_ID}}), "bad_series", "contour_layer")
    error_of(sc.post(URL, json={"params": {**BASE, "contour_layer": 10**12}}), "bad_series", "contour_layer")
    for text in (str(BIG_ID), f"{lid},{BIG_ID}", str(10**12), "0", "-3"):
        error_of(sc.get(f"{URL}/{sid}/contours", params={"k": "0", "layers": text}), "bad_query")
    for ids in ([BIG_ID], [lid, 10**12], [0]):
        error_of(sc.post(f"{URL}/{sid}/links", json={"layers": ids}), "bad_query")
    for ids in ([BIG_ID], [1, 2**63], [10**12]):
        error_of(sc.post(f"{URL}/{sid}/cache", json={"wireframe_ids": ids}), "bad_wireframes")
    # the ones within the limit still work
    assert sc.get(f"{URL}/{sid}/contours", params={"k": "0", "layers": str(lid)}).status_code == 200
    assert sc.post(f"{URL}/{sid}/links", json={"layers": [lid]}).status_code == 200
