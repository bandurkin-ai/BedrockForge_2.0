"""Demo part of Э11 (demo/blocks.py; ТЗ 7.5; ADR 0017 §3): the regular block model 40 × 40 × 20 around the ore
bodies, with AU (gaps stay empty, never 0) and DOMAIN; the same on every installation; seen by `/blocks`."""

from __future__ import annotations

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.demo import blocks as db
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_import_job import API


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    r = pclient.post(f"{API}/projects/demo")
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    return pclient, project, BlobStore(project.folder)


def test_block_model_of_the_demo(demo) -> None:  # type: ignore[no-untyped-def]
    from bf_core.blocks import schema as bs
    from bf_core.blocks import store as bm

    c, project, store = demo
    listed = c.get(f"{API}/blocks").json()["block_models"]
    assert [b["name"] for b in listed] == [db.NAME]
    info = c.get(f"{API}/blocks/{listed[0]['id']}").json()
    n = 40 * 40 * 20
    assert info["cells"] == n and info["folder"] is None and info["source"] is None and info["warnings"] == []
    assert info["grid"] == {"corner": [5000.0, 7950.0, 210.0], "counts": [40, 40, 20], "size": [10.0, 10.0, 10.0],
                            "order": "F", "variable": False}  # fmt: skip
    assert info["extent"] == [5000.0, 7950.0, 210.0, 5400.0, 8350.0, 410.0]
    au, domain = info["fields"]
    assert (au["name"], au["kind"], au["unit"]) == ("AU", "number", "г/т")
    assert (domain["name"], domain["kind"], domain["labels"]) == ("DOMAIN", "category", db.LABELS)
    s = au["stats"]
    assert s["count"] + s["missing"] == n and 0.05 * n < s["missing"] < 0.11 * n
    assert 0 < s["min"] < 0.1 and s["max"] > 3 and 0.1 < s["mean"] < 1.5
    d = domain["stats"]
    assert d["missing"] == 0 and sum(d["counts"]) == n and all(k > 200 for k in d["counts"])

    with dialect.read_tx(project.engine) as conn:
        idx, values = bm.read_cells(conn, store, info["id"])
    assert np.array_equal(idx, np.arange(n, dtype=np.uint64))
    gaps = np.isnan(values["AU"])
    assert int(gaps.sum()) == s["missing"] and not (values["AU"][~gaps] <= 0).any()  # a gap is never 0
    # the ore is where the ore bodies are: mostly in the slab of «Рудное тело 1» and the lens of «Рудное тело 2»
    z = bs.centers(info["grid"], idx)[:, 2]
    ore = values["DOMAIN"] == 0
    assert ore.sum() > 1000 and ((z[ore] > 285) & (z[ore] < 395)).mean() > 0.95
    assert np.nanmean(values["AU"][ore]) > 3 * np.nanmean(values["AU"][values["DOMAIN"] == 1])
    # the same model on every installation, bit for bit
    idx2, again = db.cells()
    assert np.array_equal(idx2, idx) and np.array_equal(again["AU"], values["AU"], equal_nan=True)
    assert np.array_equal(again["DOMAIN"], values["DOMAIN"])
