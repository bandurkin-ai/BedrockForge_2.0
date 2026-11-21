"""Import of control samples from a file — the job `qaqc.import` (ADR 0020 «Замеры»: CSV → данные QAQC; ci —
100 тыс. контрольных записей, ориентир ≤ 5 с; full — 1 млн, без цели ТЗ, ориентир ≤ 40 с).

The CSV of the generator (`_qaqc`: ≈ 1.55 rows a control record — every duplicate and every sample of the
external control has its ORIG parent; three elements with «<DL» cells) and an empty project with the settings.
`POST /qaqc/data` as functions (`_qaqc.import_file`): the columns and the mapping suggested by the first rows,
the request checked, then the job — the file read, the rows normalized, the records written as Parquet, the
object made in the last transaction:
  import_s    the whole of it; `inspect_s` — the first rows and the check of the request (before the job);
  read_s      the file read into columns (the element columns as text: «<0,01»), with its SHA-256;
  numbers_s   the cells of the elements into numbers with their flags, the dates, SEQ;
  links_s     the rest of the normalization: the text columns as dictionaries, the parent sample of every pair
              and the previous sample of every batch with their values;
  parquet_s   the records written to the store (zstd, groups of 250 thousand rows) with their SHA-256;
  records_mb  the file of the records;  file_mb — the CSV.
What is checked (a failure stops the run): every row of the file is a record; the three elements; every pair
found its parent, no identifier repeats, no date failed; «<DL» cells are counted; the object is in the project
with its laboratories and batches; the records read back hold the columns of the active element.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import _charts, _qaqc, _samples

STAGES = {"read": ("read",), "numbers": ("values", "dates", "seq"), "normalize": ("normalize",),
          "parquet": ("parquet",)}  # fmt: skip


def spots() -> _charts.Spots:
    from bf_core.qaqc import jobs, normalize, service

    return (
        (jobs, "_read", "read"), (normalize, "normalize", "normalize"), (normalize, "parse_values", "values"),
        (normalize, "parse_dates", "dates"), (normalize, "parse_seq", "seq"), (service, "stage", "parquet"),
    )  # fmt: skip


def prepare(params: dict[str, Any], work: Path) -> None:
    _qaqc.build(work, params, data=False)
    load()


def load() -> None:
    import pyarrow.csv  # noqa: F401
    import pyarrow.parquet  # noqa: F401

    import bf_core.formats.registry  # noqa: F401
    import bf_core.qaqc.jobs  # noqa: F401
    import bf_core.qaqc.normalize  # noqa: F401
    import bf_core.qaqc.service  # noqa: F401


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.db import dialect
    from bf_core.qaqc import calc, jobs, records, service
    from bf_core.storage.blobs import BlobStore

    meta, engine, folder = _qaqc.opened(work)
    info: dict[str, Any] = {"rows": meta["rows"], "control": meta["control"], "file_mb": meta["file_mb"]}
    stamp = _qaqc.stamp()
    try:
        project = _qaqc.project_of(engine, folder)
        source = {"kind": "file", "path": str(work / _qaqc.FILE)}
        t0 = time.perf_counter()
        seen = jobs.inspect_file(source)
        body = {"name": f"Замер {stamp}", "source": source, "mapping": seen["mapping"]}
        submitted = jobs.check_submit(project, body)
        t1 = time.perf_counter()
        with _samples.stages(*spots()) as took:
            result = _qaqc.job(folder, jobs.KIND, submitted, stamp)
        info["import_s"] = round(time.perf_counter() - t0, 3)
        info["inspect_s"] = round(t1 - t0, 3)
        info.update(_samples.timings(took, STAGES))
        info["links_s"] = round(info.pop("normalize_s") - info["numbers_s"], 3)

        counts = result["counts"]
        assert result["rows"] == meta["rows"] and not result["replaced"], result["rows"]
        assert counts["no_parent"] == 0 and counts["dup_ids"] == 0 and counts["bad_dates"] == 0, counts
        assert counts["below_dl"] > 0 and sorted(counts["elements"]) == sorted(meta["elements"]), counts
        with dialect.read_tx(engine) as conn:
            data = service.info(conn, result["data_id"])
        assert data["rows"] == meta["rows"] and data["batches"] == meta["batches"], (data["rows"], data["batches"])
        assert data["labs"] == ["EXT", "LAB1", "LAB2", "LAB3"] and len(data["elements"]) == 3, data["labs"]
        recs = service.open_records(project, result["data_id"])
        recs.load(calc.columns_of(recs.schema, _qaqc.ELEMENT))
        assert recs.n == meta["rows"] and int((recs.flags(records.q(_qaqc.ELEMENT)) == records.Q_BELOW).sum()) > 0
        info["records_mb"] = round(BlobStore(folder).path_of(data["sha256"]).stat().st_size / 2**20, 1)
    finally:
        engine.dispose()
    info["below_dl"] = counts["below_dl"]
    info["peak_mb"] = _samples.peak_mb()
    return info
