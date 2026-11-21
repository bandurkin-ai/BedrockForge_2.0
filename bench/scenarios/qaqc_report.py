"""The QAQC report: the summary of every section of three elements and the XLSX book (ТЗ 8.4.3: XLSX ≤ 60 с на
1 млн контрольных записей; ADR 0020 «Замеры»: full — расчёт и XLSX ≤ 60 с, пик ≤ 4 ГБ; ci — ориентир ≤ 8 с).

The data of `_qaqc` in a project with the settings of the generator. `POST /qaqc/reports` as functions: the
snapshot of the inputs and the object (`report.make_inputs`, `report.create`), then the job `qaqc.report` — the
records of the report read from its own copy of the file, every section of AU, CU and AG computed with the
kernels of the tab (the bootstrap of the external control too), the summary kept with the report:
  create_s    the snapshot and the object `building`;
  report_s    the job to its end; `report_compute_s` — the sections (`report.compute`), the rest — the records
              read and the summary written;
  summary_kb  the summary of the report as it is stored.
The step «XLSX» (подшаг 14.14) — `xlsx()` below: the job `XLSX_JOB` with `XLSX_PARAMS` — the book with the sheets
by samples. Until the job is registered the step is skipped (`xlsx_s` — None, `xlsx` says why); once it is, the
step runs by itself and `xlsx_s`, `xlsx_mb`, `total_s` (the calculation and the book: the 60 s of ТЗ) appear —
their budgets go into `limits` of `bench/budgets.json` then.
What is checked (a failure stops the run): the report is ready and not stale; its summary holds the three
elements, each with the sections of the tab — 60 groups of standards, the batches of the generator, the three
types of duplicates and the three pairs of laboratories with their intervals; the hashes of the data and of the
inputs are there; a second report of the same data and settings gives the same summary byte for byte.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import _charts, _qaqc, _samples

STAGES = {"compute": ("compute",)}
XLSX_JOB = "qaqc.report_file"  # ADR 0020 §6, §10: `POST /qaqc/reports/{id}/files {kind: "xlsx"}`
XLSX_PARAMS = {"kind": "xlsx"}  # with `report_id`; no charts — a report without images is always there


def spots() -> _charts.Spots:
    from bf_core.qaqc import report

    return ((report, "compute", "compute"),)


def make(engine: Any, folder: Path, meta: dict[str, Any], name: str, job_id: int, info: dict[str, Any] | None) -> int:
    """A report of every element of the data: the object, then the job `qaqc.report`; timed into `info`."""
    from bf_core.db import dialect
    from bf_core.qaqc import jobs, report
    from bf_core.storage.blobs import BlobStore

    t0 = time.perf_counter()
    with dialect.write_tx(engine) as conn:
        inputs = report.make_inputs(conn, meta["data_id"], None, None, "none")
        report_id = report.create(conn, BlobStore(folder), name, inputs)
    t1 = time.perf_counter()
    with _samples.stages(*spots()) as took:
        result = _qaqc.job(folder, jobs.REPORT, {"report_id": report_id}, job_id)
    assert result["report_id"] == report_id and not result["rebuilt"], result
    if info is not None:
        info["create_s"] = round(t1 - t0, 3)
        info["report_s"] = round(time.perf_counter() - t1, 3)
        info.update(_samples.timings(took, STAGES, "report_"))
    return report_id


def xlsx(engine: Any, folder: Path, report_id: int, job_id: int) -> dict[str, Any]:
    """The step «XLSX»: the job of the book of a report (подшаг 14.14) — or why it is skipped."""
    from bf_core.db import dialect
    from bf_core.jobs.registry import load_handlers
    from bf_core.qaqc import report
    from bf_core.storage.blobs import BlobStore

    if XLSX_JOB not in load_handlers():
        return {"xlsx_s": None, "xlsx": f"нет задания {XLSX_JOB} (подшаг 14.14)"}
    t0 = time.perf_counter()
    _qaqc.job(folder, XLSX_JOB, {"report_id": report_id, **XLSX_PARAMS}, job_id)
    seconds = time.perf_counter() - t0
    with dialect.read_tx(engine) as conn:
        files = {f["kind"]: f["size"] for f in report.info(conn, BlobStore(folder), report_id)["files"]}
    assert files.get("xlsx"), files
    return {"xlsx_s": round(seconds, 3), "xlsx_mb": round(files["xlsx"] / 2**20, 1)}


def check(meta: dict[str, Any], got: dict[str, Any]) -> None:
    assert got["state"] == "ready" and got["stale"] is None, (got["state"], got["stale"])
    assert got["elements"] == meta["elements"] and len(got["data_sha256"]) == len(got["inputs_sha256"]) == 64
    parts = got["summary"]["elements"]
    assert sorted(parts) == sorted(meta["elements"]), list(parts)  # the summary is stored with its keys sorted
    for element, part in parts.items():
        assert {"summary", "standards", "blanks", "external"} <= set(part), (element, list(part))
        assert {f"duplicates:{kind}" for kind in _qaqc.SPREAD} <= set(part), (element, list(part))
        line = part["summary"]
        assert line["counts"]["control"] == meta["control"], line["counts"]
        if "batches" in line:  # the totals of the batches, when the summary of the report keeps them
            assert line["batches"]["total"] == meta["batches"], line["batches"]
        assert len(part["standards"]["groups"]) == _qaqc.STANDARDS * _qaqc.LABS, element
        assert line["standards"]["fail"] > 0 and line["blanks"]["fail"] > 0, (element, line["standards"])
        assert all(part[f"duplicates:{kind}"]["used"] > 0 for kind in _qaqc.SPREAD), element
        groups = part["external"]["groups"]
        assert len(groups) == _qaqc.LABS and all(g["rma"]["b_ci"] and g["rma"]["n_boot"] for g in groups), element


def prepare(params: dict[str, Any], work: Path) -> None:
    _qaqc.build(work, params)
    load()


def load() -> None:
    _qaqc.warm()


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.db import dialect
    from bf_core.qaqc import report
    from bf_core.storage.blobs import BlobStore

    meta, engine, folder = _qaqc.opened(work)
    info: dict[str, Any] = {"rows": meta["rows"], "control": meta["control"], "elements": len(meta["elements"])}
    stamp = _qaqc.stamp()
    try:
        report_id = make(engine, folder, meta, f"Замер {stamp}", stamp, info)
        info.update(xlsx(engine, folder, report_id, stamp + 1))
        if info["xlsx_s"] is not None:
            info["total_s"] = round(info["create_s"] + info["report_s"] + info["xlsx_s"], 3)
        store = BlobStore(folder)
        with dialect.read_tx(engine) as conn:
            got = report.info(conn, store, report_id, summary=True)
        check(meta, got)
        again = make(engine, folder, meta, f"Замер {stamp} (2)", stamp + 2, None)
        with dialect.read_tx(engine) as conn:
            second = report.info(conn, store, again, summary=True)
        same = report.canonical_summary(second["summary"]) == report.canonical_summary(got["summary"])
        assert same and second["data_sha256"] == got["data_sha256"], "the same data and settings — another summary"
    finally:
        engine.dispose()
    info["summary_kb"] = round(got["summary_size"] / 1024.0, 1)
    info["groups"] = sum(len(part["standards"]["groups"]) for part in got["summary"]["elements"].values())
    info["peak_mb"] = _samples.peak_mb()
    return info
