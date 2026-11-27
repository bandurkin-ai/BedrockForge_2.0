"""Ids inside the `meta` of a block model after a `.bfggis` round trip (`blocks/remap.py`; ADR 0023 §2, §6–§9; ТЗ 7.4,
7.35 Д-35 «ТД35-6»): `inputs`, `source.params` of a fill and of the fields of the calculator name the objects of the new
project, so a model and a field that were current stay current and «Перестроить» reads the same bodies and filter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.blocks.filters import store as filters
from bf_core.blocks.model import Model
from bf_core.blocks.remap import DEAD_ID, model_meta
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from tests.test_archive import export, wait_job
from tests.test_blocks_fill_jobs import A_BOX, B_BOX, body, prototype, request, run
from tests.test_blocks_ops_api import API, BL, done, setup_core


@pytest.fixture()
def oc(pclient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    return setup_core(pclient, monkeypatch, "Источник")


def test_model_meta_renumbers_the_ids_and_nothing_else() -> None:
    new = {3: 2, 4: 3, 5: 4, 7: 6}.get
    rules = {"op": "and", "items": [{"wireframe": {"id": 4, "side": "inside", "by": "centre", "m": 4}},
                                    {"surface": {"id": 9, "side": "above", "by": "centre"}},
                                    {"expr": "AU > 1"}]}  # fmt: skip
    fill = {"source_id": 5, "wireframes": [{"id": 3, "code": None, "priority": None}, {"id": 8, "code": "x"}],
            "mode": "inside",
            "names": {"wireframes": {"3": "Руда", "8": "Нет"}, "model": {"5": "Прототип"}}}  # fmt: skip
    meta = {
        "v": 2, "rows": 10,
        "inputs": {"wireframes": [{"id": 3, "sha": "a" * 64}, {"id": 8, "sha": "b" * 64}],
                   "model": [{"id": 5, "sha": "c" * 64}]},
        "source": {"op": "fill", "params": fill},
        "fields": [
            {"name": "AU", "source": {"op": "import", "params": {"file": "m.dat", "column": "AU"}}},
            {"name": "CODE", "source": {"op": "fill", "params": fill, "inputs": []}},
            {"name": "AU2", "source": {"op": "calc", "inputs": ["d" * 64],
                                       "params": {"expr": "AU * 2", "filter_id": 7, "restrict": rules}}},
            {"name": "K", "source": {"op": "classify", "params": {
                "filter_id": None, "restrict": None, "overrides": [{"wireframe_id": 4, "label": "A"},
                                                                   {"wireframe_id": 9, "label": "B"}]}}},
            {"name": "KR", "source": {"op": "estimate", "params": {"estimation_ids": [3, 4]}}},
            {"name": "OLD", "source": None},
        ],
    }  # fmt: skip
    out = model_meta(meta, new)
    assert out["inputs"] == {"wireframes": [{"id": 2, "sha": "a" * 64}, {"id": DEAD_ID, "sha": "b" * 64}],
                             "model": [{"id": 4, "sha": "c" * 64}]}  # fmt: skip
    want_fill = {"source_id": 4,
                 "wireframes": [{"id": 2, "code": None, "priority": None}, {"id": DEAD_ID, "code": "x"}],
                 "mode": "inside",
                 "names": {"wireframes": {"2": "Руда", str(DEAD_ID): "Нет"}, "model": {"4": "Прототип"}}}  # fmt: skip
    assert out["source"] == {"op": "fill", "params": want_fill}
    f = {x["name"]: x["source"] for x in out["fields"]}
    assert f["AU"] == meta["fields"][0]["source"] and f["OLD"] is None and f["CODE"]["params"] == want_fill
    assert f["AU2"]["params"]["filter_id"] == 6 and f["AU2"]["inputs"] == ["d" * 64]
    leaves = f["AU2"]["params"]["restrict"]["items"]
    assert leaves[0]["wireframe"]["id"] == 3 and leaves[1]["surface"]["id"] is None and leaves[2] == {"expr": "AU > 1"}
    assert f["K"]["params"] == {"filter_id": None, "restrict": None,
                                "overrides": [{"wireframe_id": 3, "label": "A"},
                                              {"wireframe_id": DEAD_ID, "label": "B"}]}  # fmt: skip
    assert f["KR"] == meta["fields"][4]["source"]  # the ids of an estimate are another module's
    assert meta["inputs"]["wireframes"][0]["id"] == 3 and meta["fields"][2]["source"]["params"]["filter_id"] == 7
    v1 = {"v": 1, "grid": {}, "cells": 3, "fields": [{"name": "Au", "kind": "number"}],
          "source": {"format": "omf", "file": "m.omf", "element": "Блоки"}, "warnings": []}  # fmt: skip
    assert model_meta(v1, new) == v1


def column_of(c: Any, mid: int, name: str) -> np.ndarray:
    q = projects_of(c.core).require_current()
    store = BlobStore(q.folder)
    with dialect.read_tx(q.engine) as conn:
        model = Model.open(conn, store, mid)
    return model.columns([name])[name]


def fields_of(c: Any, mid: int) -> dict[str, Any]:
    r = c.get(f"{BL}/{mid}/fields")
    assert r.status_code == 200, r.text
    return dict(r.json())


def test_bfggis_keeps_a_filled_model_and_a_calculated_field_current(oc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c, p = oc, oc.p
    far = body(p, (500.0, 500.0, 500.0), (520.0, 520.0, 520.0), "Далеко")  # deleted below: the ids after it move
    ore, zone = body(p, *A_BOX, name="Руда"), body(p, *B_BOX, name="Зона")
    proto = prototype(p)
    mid = int(run(p, request(proto, ore))["model_id"])
    with dialect.write_tx(p.engine) as conn:
        in_ore = {"wireframe": {"id": ore, "side": "inside", "by": "centre"}}
        rules = {"op": "or", "items": [{"expr": "AU > 1"}, in_ore]}
        fid = filters.create(conn, {"name": "Богатые в руде", "rules": rules})
    version = c.get(f"{BL}/{c.mid}").json()["version"]
    quick = {"wireframe": {"id": zone, "side": "inside", "by": "centre"}}
    done(c, c.post(f"{BL}/{c.mid}/calc", json={"expected_version": version, "expr": "AU * 2", "field": "AU2",
                                                "filter_id": fid, "restrict": quick}))  # fmt: skip
    assert not fields_of(c, mid)["stale"]
    with dialect.write_tx(p.engine) as conn:
        conn.execute(sa.delete(objects).where(objects.c.id == far))
    path = Path(export(c, tmp_path / "Архив")["result"]["path"])
    r = c.post(f"{API}/projects/import", json={"path": str(path), "mode": "copy"}).json()
    job = wait_job(c, r["source"], r["id"])
    assert job["status"] == "done", job
    c.post(f"{API}/projects/{job['result']['uid']}/open")
    ids = {int(k): int(v) for k, v in job["result"]["id_maps"]["objects"].items()}
    assert ids[ore] != ore and ids[zone] == ore  # the old number of «Руда» is «Зона» in the new project

    got = fields_of(c, ids[mid])
    assert (got["stale"], got["stale_reason"]) == (False, None)
    assert not any(f["stale"] for f in got["fields"])
    full = c.get(f"{BL}/{ids[mid]}").json()
    assert [w["id"] for w in full["inputs"]["wireframes"]] == [ids[ore]]
    params = full["source"]["params"]
    assert params["source_id"] == ids[proto] and [w["id"] for w in params["wireframes"]] == [ids[ore]]
    assert params["names"] == {"wireframes": {str(ids[ore]): "Руда"}}

    au2 = {f["name"]: f for f in fields_of(c, ids[c.mid])["fields"]}["AU2"]
    assert (au2["stale"], au2["stale_reason"]) == (False, None)
    assert au2["source"]["params"]["filter_id"] == ids[fid]
    assert au2["source"]["params"]["restrict"]["wireframe"]["id"] == ids[zone]
    # «Перестроить» reads the same filter and bodies: the same values, the field stays current
    before = column_of(c, ids[c.mid], "AU2")
    version = c.get(f"{BL}/{ids[c.mid]}").json()["version"]
    done(c, c.post(f"{BL}/{ids[c.mid]}/fields/AU2/rebuild", json={"expected_version": version}))
    assert np.array_equal(column_of(c, ids[c.mid], "AU2"), before, equal_nan=True)
    assert not {f["name"]: f for f in fields_of(c, ids[c.mid])["fields"]}["AU2"]["stale"]
