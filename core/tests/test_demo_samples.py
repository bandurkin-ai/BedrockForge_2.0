"""Demo sample processing of Э12 (demo/samples.py; ТЗ 7.5, 8.2, 7.35 Д-26; подшаг 12.32): «Композиты 2 м» of
«Пробы» and «Рудные интервалы» by the bodies of their jobs — ordinary results of «Обработка проб» (fresh, not
stale), the table of intervals with LABEL, ORE_ID / ORE_CLS of the samples, the display «Рудные интервалы»; the
audit of the demo is made after them (not stale); the rest of the demo is as before."""

from __future__ import annotations

from typing import Any

import pytest

from bf_core.demo import samples as ds
from tests.test_composites_jobs import rows_of, tables
from tests.test_import_job import API

C = f"{API}/composites"


@pytest.fixture()
def demo(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr("bf_core.demo.project.SAMPLES", True)  # off for the other tests (conftest)
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    set_id = pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    return pclient, set_id


def column(got: dict[str, Any], name: str) -> list[Any]:
    key = next(c["key"] for c in got["columns"] if c["name"] == name)
    return [r[key] for r in got["rows"]]


def test_composites_and_ore_intervals_of_the_demo(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    tabs = tables(c, set_id)
    assert {"Пробы", ds.COMPOSITES_NAME, ds.ORE_NAME} <= set(tabs)
    samples_key = tabs["Пробы"]["table"]
    res = {r["op"]: r for r in c.get(f"{C}/results?set_id={set_id}").json()["results"]}
    assert set(res) == {"length", "ore"} and not any(r["stale"] for r in res.values())
    assert res["length"]["params"] == ds.composites_body(set_id, samples_key)
    assert res["ore"]["params"] == ds.ore_body(set_id, samples_key)
    assert tabs[ds.COMPOSITES_NAME]["origin"]["result_id"] == res["length"]["id"]

    # «Композиты 2 м»: AU, CU by length 2 m; the metal of the samples kept (Т8.2-4)
    comp = rows_of(c, set_id, tabs[ds.COMPOSITES_NAME]["table"])
    assert len(comp["rows"]) > 3000 and {"AU", "CU", "N_SAMPLES", "FLAG"} <= {x["name"] for x in comp["columns"]}
    summary = res["length"]["report"]["summary"]
    for f in ("AU", "CU"):
        before, after = summary["before"]["fields"][f]["metal"], summary["after"]["fields"][f]["metal"]
        assert abs(after - before) <= 1e-9 * abs(before)

    # «Рудные интервалы»: LABEL of every ore interval, ORE_ID / ORE_CLS of «Пробы» (a gap stays empty)
    ore = rows_of(c, set_id, tabs[ds.ORE_NAME]["table"])
    cls = column(ore, "CLASS")
    labels = column(ore, "LABEL")
    n_ore = cls.count("ore")
    assert 50 < n_ore < 200 and all(bool(lb) == (k == "ore") for k, lb in zip(cls, labels, strict=True))
    samples = rows_of(c, set_id, samples_key)
    ore_cls = column(samples, "ORE_CLS")
    assert ore_cls.count("ore") > 500 and None in ore_cls  # the sample without AU has no class (not waste)
    assert res["ore"]["written"] == [{"table": "пробы", "columns": ["ORE_ID", "ORE_CLS"]}]

    # the display «Рудные интервалы»: its track names the table so that the 3D layer loads it
    chart = next(x for x in c.get(f"{API}/charts?set_id={set_id}").json()["charts"] if x["name"] == ds.ORE_NAME)
    track = chart["settings"]["tracks"][0]
    r = c.get(f"{API}/viz/sets/{set_id}/intervals/0", params={"table": track["table"], "fields": "CLASS,LABEL"})
    assert r.status_code == 200, r.text

    # the audit of the demo is made after them: not stale, its table is «Пробы»
    audit = c.get(f"{API}/audit/sets/{set_id}").json()["result"]
    assert audit["stale"] is False and audit["table_name"] == "Пробы", audit
