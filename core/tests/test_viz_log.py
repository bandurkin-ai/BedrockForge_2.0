"""The window «Данные по скважинам» (ТЗ 7.17, ADR 0007 §6): up to 30 holes, empty values null (never 0),
events without «до», the depth window, the cut at 20 000 rows, the hole names in the order of the set."""

from __future__ import annotations

import numpy as np
import pytest

from tests.test_import_job import API
from tests.test_viz_holes import open_demo

pytestmark = pytest.mark.windows


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    return open_demo(pclient)


def _log(c, set_id: int, body: dict, status: int = 200) -> dict:  # type: ignore[no-untyped-def, type-arg]
    r = c.post(f"{API}/viz/sets/{set_id}/log", json=body)
    assert r.status_code == status, r.text
    return r.json()  # type: ignore[no-any-return]


def test_hole_names_in_set_order(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    r = c.get(f"{API}/viz/sets/{set_id}/hole-names")
    assert r.status_code == 200 and r.json() == {"names": [*(f"DH-{i:03d}" for i in range(1, 49)), "К-01"]}
    assert c.get(f"{API}/viz/sets/999999/hole-names").json()["code"] == "set_not_found"


def test_31_holes_refused_30_accepted(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    names = [f"DH-{i:03d}" for i in range(1, 32)]
    err = _log(c, set_id, {"holes": names, "tables": [{"table": "пробы"}]}, 422)
    assert err["code"] == "too_many_holes" and "30" in err["message"] and err["hint"]
    out = _log(c, set_id, {"holes": names[:30] + ["DH-001"], "tables": [{"table": "пробы", "fields": ["AU"]}]})
    assert len(out["holes"]) == 30  # a repeated name counts once


def test_values_null_not_zero(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    body = {
        "holes": ["DH-031", "DH-012", "НЕТ-1"],
        "tables": [{"table": "Пробы", "fields": ["au", "CU", "Номер пробы"]}],
    }
    out = _log(c, set_id, body)
    (meta,) = out["tables"]
    assert meta["key"] == "Пробы" and meta["name"] == "Пробы" and meta["kind"] == "assay"
    assert meta["columns"] == [
        {"name": "AU", "kind": "number", "unit": "г/т"},
        {"name": "CU", "kind": "number", "unit": "%"},
        {"name": "Номер пробы", "kind": "text", "unit": None},
    ]
    h31, h12, missing = out["holes"]
    assert missing == {"name": "НЕТ-1", "found": False, "collar_z": None, "td": None, "tables": {}}
    assert h31["found"] and isinstance(h31["collar_z"], float) and h31["td"] in (120.0, 150.0, 180.0, 220.0)
    t31 = h31["tables"]["Пробы"]
    assert t31["truncated"] is False and t31["from"] == sorted(t31["from"]) and len(t31["to"]) == len(t31["from"])
    at50 = t31["from"].index(50.0)
    assert t31["values"]["AU"][at50] is None and isinstance(t31["values"]["CU"][at50], float)  # empty, not 0
    assert all(v is not None for i, v in enumerate(t31["values"]["AU"]) if i != at50)
    t12 = h12["tables"]["Пробы"]
    extra = [i for i, (f, t) in enumerate(zip(t12["from"], t12["to"], strict=True)) if (f, t) == (10.0, 13.0)]
    assert len(extra) == 1 and t12["values"]["AU"][extra[0]] == -1.0 and t12["values"]["CU"][extra[0]] is None
    assert t12["values"]["Номер пробы"][extra[0]] == "DH-012-X"


def test_table_by_id_all_columns_window_and_cut(demo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from bf_core.viz import log

    c, set_id = demo
    tables = c.get(f"{API}/drillholes/sets/{set_id}/tables").json()["tables"]
    assay = next(t for t in tables if t["name"] == "Пробы")
    out = _log(c, set_id, {"holes": ["DH-001"], "tables": [{"table": assay["table"]}], "from": 40, "to": 60})
    assert [col["name"] for col in out["tables"][0]["columns"]] == ["AU", "CU", "Номер пробы"]
    t = out["holes"][0]["tables"][assay["table"]]
    assert t["from"][0] == 38.0 and t["from"][-1] == 60.0  # intervals meeting [40; 60]
    assert all(to >= 40 and f <= 60 for f, to in zip(t["from"], t["to"], strict=True))
    monkeypatch.setattr(log, "MAX_ROWS", 10)
    cut = _log(c, set_id, {"holes": ["DH-001"], "tables": [{"table": "пробы", "fields": ["AU"]}]})
    t = cut["holes"][0]["tables"]["пробы"]
    assert t["truncated"] is True and len(t["from"]) == 10 == len(t["values"]["AU"])


def test_answer_bounded_by_cells(demo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from bf_core.viz import log

    c, set_id = demo
    monkeypatch.setattr(log, "MAX_CELLS", 150)  # 3 columns: 50 rows in all
    out = _log(c, set_id, {"holes": ["DH-001", "DH-002"], "tables": [{"table": "пробы"}]})
    a, b = (h["tables"]["пробы"] for h in out["holes"])
    assert len(a["from"]) == 50 and a["truncated"] is True
    assert len(b["from"]) == 0 and b["truncated"] is True


def test_events_have_null_to(demo) -> None:  # type: ignore[no-untyped-def]
    from bf_core.drillholes.store import ColumnSpec, TableData, write_table

    c, set_id = demo
    project = c.core.services["projects"].require_current()
    data = TableData(
        "Структуры",
        "event",
        ["DH-001", "DH-001", "DH-001"],
        np.array([30.0, 5.0, np.nan]),
        np.full(3, np.nan),
        [ColumnSpec("Тип", "text"), ColumnSpec("Угол", "number")],
        [["трещина", None, "жила"], [45.0, np.nan, 0.0]],
    )
    write_table(project.engine, set_id, data, job_id=None, tick=lambda _f, _m: None)
    out = _log(c, set_id, {"holes": ["DH-001"], "tables": [{"table": "структуры", "fields": ["Угол", "Тип"]}]})
    t = out["holes"][0]["tables"]["структуры"]
    assert t == {
        "from": [5.0, 30.0],  # the event without a depth is left out
        "to": [None, None],
        "values": {"Угол": [None, 45.0], "Тип": [None, "трещина"]},
        "truncated": False,
    }


def test_errors(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    err = _log(c, set_id, {"holes": ["DH-001"], "tables": [{"table": "Литология"}]}, 404)
    assert err["code"] == "table_not_found"
    err = _log(c, set_id, {"holes": ["DH-001"], "tables": [{"table": "Пробы", "fields": ["AG"]}]}, 404)
    assert err["code"] == "field_not_found"
    err = _log(c, set_id, {"holes": ["DH-001"], "tables": [], "from": 50, "to": 10}, 422)
    assert err["code"] == "bad_range" and err["hint"]
    assert _log(c, 999999, {"holes": ["DH-001"], "tables": []}, 404)["code"] == "set_not_found"
