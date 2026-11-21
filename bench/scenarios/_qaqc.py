"""What the benches of QAQC share (ADR 0020 «Замеры», вопрос № 38): `qaqc_calc`, `qaqc_report`, `qaqc_import`.

    meta = build(work, params, data=True)       `prepare`: the CSV of `tools/gen/qaqc.py` (`control` control records —
        30 % CRM of 20 standards × 3 laboratories, 15 % blanks, 15 % each of DUP_F, DUP_C, DUP_P, 10 % UMP, every
        pair with its ORIG parent: ≈ 1.55 × `control` rows; three elements), a project with the settings of the
        generator (its DL and its CRM reference) and — with `data` — the data imported by the job `qaqc.import`
        (not timed); the meta is kept in `work/qaqc.json`
    meta, engine, folder = opened(work)          the project of `build`; the caller disposes the engine
    project = project_of(engine, folder)         what a route takes of `svc.require_current()`
    result = job(folder, kind, params, job_id)   a job as a worker runs it: its handler with a `JobContext`
    result = import_file(project, folder, path, name, job_id)
        `POST /qaqc/data` as functions: the columns and the mapping suggested (`inspect_file`), the request
        checked (`check_submit`), the job `qaqc.import`
    answer, seconds, size = ask(project, section, body)
        `POST /qaqc/calc/{section}` as a function — `routes_calc.compute` (the records through the cache of
        columns, the settings and the reference read, the section) and the answer written as JSON: the time of
        ТЗ is «от запроса до ответа API» (решение 22)
    warm()                                       the kernels of Э13 the sections take (pairs, the scatter, the
        bootstrap) and scipy loaded — or compiled into NUMBA_CACHE_DIR — outside the timing

The volumes: ci — 100 thousand control records (155 thousand rows), full — 1 million (1.55 million rows); the
shares of the types, the standards and the laboratories are the constants below (`tools/gen/qaqc.py`).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from . import _samples

ELEMENT = "AU"  # the active element of the tab
FILE = "контроль.csv"
NAME = "Контрольные пробы"
META = "qaqc.json"
SHARES = {"CRM": 0.30, "BLANK": 0.15, "DUP_F": 0.15, "DUP_C": 0.15, "DUP_P": 0.15, "UMP": 0.10}
LABS, STANDARDS = 3, 20
SPREAD = {"DUP_F": 0.15, "DUP_C": 0.08, "DUP_P": 0.04}  # the relative error of a duplicate (`tools/gen/qaqc.py`)


def build(work: Path, params: dict[str, Any], *, data: bool = True) -> dict[str, Any]:
    """The project of a scenario in `work` (see the module docstring)."""
    from tools.gen.qaqc import generate, write_csv

    from bf_core.db import dialect
    from bf_core.projects.project import create_folder
    from bf_core.qaqc import config

    gen = generate(control=int(params["control"]), crm=STANDARDS, labs=LABS, seed=int(params.get("seed", 12345)))
    rows = write_csv(work / FILE, gen)
    folder = create_folder(work, "Замер QAQC", {})
    meta: dict[str, Any] = {
        "folder": str(folder), "rows": rows, "control": int(params["control"]), "batches": gen.counts["batches"],
        "elements": [name for name, _unit, _column in gen.elements],
        "file_mb": round((work / FILE).stat().st_size / 2**20, 1),
    }  # fmt: skip
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        with dialect.write_tx(engine) as conn:
            config.put_config(conn, {"dl": gen.dl}, 0)
            config.put_crm(conn, gen.crm, 0)
        del gen
        if data:
            result = import_file(project_of(engine, folder), folder, work / FILE, NAME, 1)
            assert result["rows"] == rows, result
            meta["data_id"] = result["data_id"]
    finally:
        engine.dispose()
    (work / META).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def opened(work: Path) -> tuple[dict[str, Any], Any, Path]:
    from bf_core.db import dialect

    meta = json.loads((work / META).read_text(encoding="utf-8"))
    folder = Path(meta["folder"])
    return meta, dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite"))), folder


def project_of(engine: Any, folder: Path) -> Any:
    return _samples.service(engine, folder).require_current()


def job(folder: Path, kind: str, params: dict[str, Any], job_id: int) -> dict[str, Any]:
    from bf_core.jobs.context import JobContext
    from bf_core.jobs.registry import load_handlers

    spec = {"id": job_id, "params": params, "project_folder": str(folder)}
    ctx = JobContext(spec, lambda _message: None, threading.Event())
    try:
        out = load_handlers()[kind].run(ctx)
    finally:
        ctx.close()
    assert out is not None
    return dict(out)


def import_file(project: Any, folder: Path, path: Path, name: str, job_id: int) -> dict[str, Any]:
    from bf_core.qaqc import jobs

    source = {"kind": "file", "path": str(path)}
    seen = jobs.inspect_file(source)
    params = jobs.check_submit(project, {"name": name, "source": source, "mapping": seen["mapping"]})
    return job(folder, jobs.KIND, params, job_id)


def ask(project: Any, section: str, body: dict[str, Any]) -> tuple[dict[str, Any], float, int]:
    from fastapi.responses import JSONResponse

    from bf_core.qaqc import routes_calc

    t0 = time.perf_counter()
    answer = routes_calc.compute(project, section, body)
    size = len(JSONResponse(answer).body)
    return answer, time.perf_counter() - t0, size


def stamp() -> int:
    """A job id of this run: a repeated run (`--repeat`) is a new job."""
    return time.time_ns() // 1_000_000 % 1_000_000_000


def warm() -> None:
    import numpy as np
    import pyarrow.parquet  # noqa: F401
    import scipy.stats  # noqa: F401

    import bf_core.qaqc.calc  # noqa: F401
    import bf_core.qaqc.report  # noqa: F401
    import bf_core.qaqc.routes_calc  # noqa: F401
    from bf_core.qaqc.blanks import carryover
    from bf_core.qaqc.duplicates import scatter_stats
    from bf_core.qaqc.external import paired_t, rma_intervals
    from bf_core.qaqc.thompson import k_factor

    x = np.array([1.0, 2.0, 3.0, 5.0, 8.0])
    y = np.array([1.1, 1.9, 3.2, 5.1, 7.7])
    carryover(x, y)
    scatter_stats(x, y)
    paired_t(x, y)
    rma_intervals(x, y, 10, 1)
    k_factor()
