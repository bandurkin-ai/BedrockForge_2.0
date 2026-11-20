"""Demo sample processing of Э12 (demo/samples.py; ТЗ 7.5, 8.2, 7.35 Д-26; подшаг 12.32): «Композиты 2 м» of
«Пробы» and «Рудные интервалы» by the bodies of their jobs — ordinary results of «Обработка проб» (fresh, not
stale), the table of intervals with LABEL, ORE_ID / ORE_CLS of the samples, the display «Рудные интервалы»; the
audit of the demo is made after them (not stale); the rest of the demo is as before."""

from __future__ import annotations

import time
from typing import Any

import pytest

from bf_core.demo import samples as ds
from tests.test_composites_jobs import rows_of, tables
from tests.test_import_job import API

C = f"{API}/composites"


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    t0 = time.perf_counter()
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    print(f"demo: {time.perf_counter() - t0:.1f} s")
    set_id = pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]
    return pclient, set_id


def column(got: dict[str, Any], name: str) -> list[Any]:
    key = next(c["key"] for c in got["columns"] if c["name"] == name)
    return [r[key] for r in got["rows"]]


def test_composites_and_ore_intervals_of_the_demo(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    tabs = tables(c, set_id)
    assert {"Пробы", ds.COMPOSITES_NAME, ds.ORE_NAME} <= set(tabs)
    res = c.get(f"{C}/results?set_id={set_id}").json()["results"]
    print(res)
    comp = rows_of(c, set_id, tabs[ds.COMPOSITES_NAME]["table"])
    ore = rows_of(c, set_id, tabs[ds.ORE_NAME]["table"])
    samples = rows_of(c, set_id, tabs["Пробы"]["table"])
    print(len(comp["rows"]), [x["name"] for x in comp["columns"]])
    print(len(ore["rows"]), [x["name"] for x in ore["columns"]], column(ore, "LABEL")[:10], column(ore, "CLASS")[:10])
    cls = column(samples, "ORE_CLS")
    print({k: cls.count(k) for k in set(cls)})
