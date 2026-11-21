"""The calculations of QAQC through the API (ADR 0020 §6, §10; подшаг 14.12): the answers are those of the kernels;
Т8.4-1 and Т8.4-2 through `POST /qaqc/calc/{section}`; a change of the dictionary of types changes the answer
without an import; the filter; 100 001 points → `thinned` with every warning and failure kept, the answer
≤ 0,5 s; an unknown section → 404; `field_not_found`; concurrent requests read each column once; `POST /records`."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.projects.service import projects_of
from bf_core.qaqc import calc
from bf_core.qaqc import config as cf
from bf_core.qaqc import normalize as nz
from bf_core.qaqc import records as r
from bf_core.qaqc import service as sv
from bf_core.storage.blobs import BlobStore
from tests.test_import_job import API
from tests.test_qaqc_import_api import qaqc_core  # noqa: F401 - the fixture
from tests.test_qaqc_records import table

Q = f"{API}/qaqc"
MAP = {"sample_id": "ID", "type": "T", "crm_id": "CRM", "lab": "LAB", "batch": "B", "seq": "SEQ", "date": "D",
       "elements": [{"column": "AU_ppm"}]}  # fmt: skip
T841 = [1.11, 1.12, 0.84, 1.16, 0.95] + [1.01] * 9


@pytest.fixture()
def qc(qaqc_core, pclient):  # type: ignore[no-untyped-def]  # noqa: F811
    assert pclient.post(f"{API}/projects", json={"name": "Расчёты"}).status_code == 200
    return pclient


def put_data(c: Any, cols: dict[str, Any], name: str = "Контроль") -> int:
    p = projects_of(c.core).require_current()
    store = BlobStore(p.folder)
    made = nz.normalize(table(**cols), MAP)
    meta = sv.make_meta(made.records, made.counts, made.notes, {"kind": "file", "name": "t.csv", "sha256": "0" * 64},
                        MAP)  # fmt: skip
    staged = sv.stage(store, made.records)
    with dialect.write_tx(p.engine) as conn:
        return sv.create(conn, store, staged, name=name, meta=meta)


def t84(c: Any) -> int:
    """Т8.4-1 (14 STD of one CRM in L1) and Т8.4-2 (three blanks in L2, DL = 0,01 by the settings)."""
    n = len(T841) + 3
    cols = {
        "ID": [f"S{i}" for i in range(n)], "T": ["STD"] * 14 + ["BLK"] * 3, "CRM": ["OREAS-1"] * 14 + [None] * 3,
        "LAB": ["L1"] * 14 + ["L2"] * 3, "B": ["B1"] * 14 + ["B2"] * 3, "SEQ": np.arange(1.0, n + 1),
        "D": [f"2024-01-{i + 1:02d}" for i in range(14)] + ["2024-02-01"] * 3,
        "AU_ppm": [str(v) for v in T841] + ["0.04", "0.05", "0.06"],
    }  # fmt: skip
    data_id = put_data(c, cols)
    crm = [{"crm_id": "OREAS-1", "element": "AU", "mean": 1.0, "sd": 0.05, "unit": "ppm", "source": None}]
    assert c.put(f"{Q}/crm", json={"rows": crm, "expected_version": 0}).status_code == 200
    cfg = c.get(f"{Q}/config").json()
    body = {**cfg, "dl": [{"element": "AU", "lab": None, "value": 0.01, "unit": "ppm"}], "expected_version": 0}
    assert c.put(f"{Q}/config", json=body).status_code == 200
    return data_id


def post(c: Any, section: str, **body: Any) -> Any:
    return c.post(f"{Q}/calc/{section}", json=body)


def test_t841_t842_through_the_api_equal_the_kernels(qc) -> None:  # type: ignore[no-untyped-def]
    did = t84(qc)
    res = post(qc, "standards", data_id=did, element="AU", group={"crm": "OREAS-1", "lab": "L1"})
    assert res.status_code == 200, res.text
    st = res.json()
    s = st["series"]
    assert s["status"] == [1, 2, 2, 2, 0] + [0] * 8 + [1]
    assert s["rule"] == [0, 1, 0, 0, 0] + [0] * 8 + [2]  # a pair; «смещение» on the 9th 1,01
    assert s["x"] == T841 and s["row"] == list(range(14)) and s["batch"] == ["B1"] * 14
    g = st["groups"][0]
    assert (g["crm"], g["lab"], g["n"], g["norm"], g["warn"], g["fail"], g["mu"], g["sd"]) == (
        "OREAS-1", "L1", 14, 9, 2, 3, 1.0, 0.05)  # fmt: skip
    assert st["element"] == "AU" and st["unit"] == "ppm" and st["counts"]["control"] == 17
    assert st["thinned"] == {"shown": 14, "total": 14}
    bl = post(qc, "blanks", data_id=did, element="AU", group={"lab": "L2"}).json()
    assert bl["series"]["status"] == [0, 0, 2] and bl["groups"][0]["norm"] == 2 and bl["groups"][0]["fail"] == 1
    p = projects_of(qc.core).require_current()
    with dialect.read_tx(p.engine) as conn:
        config, crm = cf.get_config(conn), cf.get_crm(conn)["rows"]
    recs = sv.open_records(p, did)
    for section, extra in (("standards", {"group": {"crm": "OREAS-1", "lab": "L1"}, "max_points": 100_000}),
                           ("blanks", {}), ("batches", {"offset": 0, "limit": 10}), ("summary", {})):  # fmt: skip
        api = post(qc, section, data_id=did, element="AU", **extra).json()
        direct = json.loads(calc.canonical(calc.calc(section, recs, config, crm, "AU", None, **extra)))
        assert json.loads(calc.canonical(api)) == direct, section


def test_types_and_filter_change_the_answer(qc) -> None:  # type: ignore[no-untyped-def]
    did = t84(qc)
    before = post(qc, "standards", data_id=did, element="AU").json()
    assert len(before["groups"]) == 1 and before["counts"]["unmapped"] == 0
    cfg = qc.get(f"{Q}/config").json()
    types = {k: v for k, v in cfg["types"].items() if k != "STD"}
    assert qc.put(f"{Q}/config", json={**cfg, "types": types, "expected_version": cfg["version"]}).status_code == 200
    after = post(qc, "standards", data_id=did, element="AU").json()  # no import: the dictionary of the settings
    assert after["groups"] == [] and after["counts"]["unmapped"] == 14
    summary = post(qc, "summary", data_id=did, element="AU").json()
    assert summary["unmapped_codes"] == [{"code": "STD", "n": 14}]
    only = post(qc, "blanks", data_id=did, element="AU", filter={"v": 1, "labs": ["l2"]}).json()
    assert only["counts"]["filtered"] == 3 and len(only["groups"]) == 1
    none = post(qc, "blanks", data_id=did, element="AU", filter={"v": 1, "labs": ["L1"]}).json()
    assert none["groups"] == [] and none["counts"]["filtered"] == 14
    page = post(qc, "batches", data_id=did, element="AU", offset=1, limit=1).json()
    assert page["total"] == 2 and page["offset"] == 1 and [b["batch"] for b in page["batches"]] == ["B2"]


def test_errors(qc) -> None:  # type: ignore[no-untyped-def]
    did = t84(qc)
    res = post(qc, "nothing", data_id=did, element="AU")
    assert res.status_code == 404 and res.json()["code"] == "unknown_section" and res.json()["hint"]
    res = post(qc, "standards", data_id=did, element="CU")
    assert res.status_code == 422 and res.json()["code"] == "field_not_found" and res.json()["param"] == "element"
    res = post(qc, "standards", data_id=did, element="AU", filter={"v": 1, "nope": 1})
    assert res.status_code == 422 and res.json()["code"] == "bad_param"
    res = post(qc, "batches", data_id=did, element="AU", limit=5001)
    assert res.status_code == 422 and res.json()["param"] == "limit"
    res = post(qc, "standards", data_id=did + 100, element="AU")
    assert res.status_code == 404 and res.json()["code"] == "qaqc_data_not_found"
    res = post(qc, "standards", element="AU")
    assert res.status_code == 422 and res.json()["code"] == "bad_param"


def big(c: Any, n: int = 100_001) -> int:
    rng = np.random.default_rng(5)
    x = rng.normal(1.0, 0.05, n)
    x[rng.choice(n, 300, replace=False)] += 0.2  # failures
    cols = {"ID": [f"S{i}" for i in range(n)], "T": ["CRM"] * n, "CRM": ["C"] * n, "LAB": ["L1"] * n,
            "B": [f"B{i // 50}" for i in range(n)], "SEQ": np.arange(n) % 50 + 1.0, "D": [None] * n,
            "AU_ppm": np.round(x, 4)}  # fmt: skip
    did = put_data(c, cols, "Большие")
    rows = [{"crm_id": "C", "element": "AU", "mean": 1.0, "sd": 0.05, "unit": "ppm", "source": None}]
    assert c.put(f"{Q}/crm", json={"rows": rows, "expected_version": 0}).status_code == 200
    return did


def test_thinned_series_keeps_every_violation_and_is_fast(qc) -> None:  # type: ignore[no-untyped-def]
    did = big(qc)
    group = {"crm": "C", "lab": "L1"}
    full = post(qc, "standards", data_id=did, element="AU", group=group, max_points=200_000).json()["series"]
    assert len(full["row"]) == 100_001
    flagged = {row for row, s in zip(full["row"], full["status"], strict=True) if s > 0}
    assert len(flagged) > 300
    t = time.perf_counter()
    res = post(qc, "standards", data_id=did, element="AU", group=group)
    took = time.perf_counter() - t
    body = res.json()
    assert body["thinned"]["total"] == 100_001 and body["thinned"]["shown"] == 100_000
    shown = body["series"]
    where = {row: i for i, row in enumerate(full["row"])}  # the series goes by (DATE, SEQ, row)
    at = [where[row] for row in shown["row"]]
    assert flagged <= set(shown["row"]) and at == sorted(at)  # the order of the series is kept
    assert took <= 0.5, took  # the series of 100 000 points, the columns in the cache (criterion of 14.12)


def test_concurrent_requests_read_each_column_once(qc, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    did = t84(qc)
    r.CACHE.clear()
    reads: list[str] = []
    real = r._read

    def counting(path: Any, schema: Any, names: Any) -> Any:
        reads.extend(names)
        time.sleep(0.05)  # the others arrive while the file is read
        return real(path, schema, names)

    monkeypatch.setattr(r, "_read", counting)
    out: list[Any] = []

    def one() -> None:
        out.append(post(qc, "standards", data_id=did, element="AU").json())

    threads = [threading.Thread(target=one) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(out) == 4 and all(o == out[0] for o in out)
    assert len(reads) == len(set(reads)), reads  # each column once


def test_records_by_rows_and_by_batch(qc) -> None:  # type: ignore[no-untyped-def]
    did = t84(qc)
    got = qc.post(f"{Q}/records", json={"data_id": did, "rows": [15, 0]}).json()
    assert got["total"] == 2 and got["more"] is False
    b, a = got["records"]  # in the order asked
    assert (a["row"], a["sample_id"], a["type_code"], a["type"], a["lab"]) == (0, "S0", "STD", "CRM", "L1")
    assert a["crm_id"] == "OREAS-1" and a["values"]["AU"]["v"] == 1.11 and a["values"]["AU"]["q"] == 0
    assert a["date"] == 1_704_067_200_000 and a["parent_row"] is None and a["prev_row"] is None
    assert (b["row"], b["type"], b["prev_row"], b["values"]["AU"]["b"]) == (15, "BLANK", 14, 0.04)
    batch = qc.post(f"{Q}/records", json={"data_id": did, "batch": "b2", "lab": "L2"}).json()
    assert [x["row"] for x in batch["records"]] == [14, 15, 16] and batch["total"] == 3
    bad = qc.post(f"{Q}/records", json={"data_id": did, "rows": [99]})
    assert bad.status_code == 422 and bad.json()["param"] == "rows"
