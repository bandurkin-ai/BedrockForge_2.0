"""The XLSX of a resource report (ТЗ 8.10 «Вывод»: ≤ 10 с на 20 млн; ADR 0027 решение 20 и «Замеры»: форма + 20 000
строк + кривая + параметры, full ≤ 10 с; подшаг 20.5).

The size of a report does not depend on the size of the model (it is the groups × the cut-offs), so the report of the
scenario is a synthetic one that is written straight into a project with a small model: `rows` rows of the table (1000
combinations of two keys × the cut-offs, a total row of each cut-off, three elements, 3 % of the grades missing), the
curve of 100 cut-offs, the control, the parameters of the report. The job `resources.export` (`export_job.run_export`,
the body of the handler: the read of the report, the form of the generic template, the five sheets, the file written
aside and renamed, the copy of the book into the project, the record in `meta.files`) is the timed part:
  info: rows, xlsx_mb, rows_per_s, per_100k_rows_s (the time of 100 000 rows), forecast_20k_rows_s (the goal of the
  ADR: 20 000 rows), forecast_2m_rows_s (a report of 2 000 000 rows — the biggest the limit of the cells allows)
What is checked (a failure stops the run): the answer of the job — the rows, `recorded`; the book is a zip whose sheet
«Таблица» ends with the line `rows + 1` and has no line after it; its first row has the numbers of the first row of
the table (to the last digit); the five sheets are in the workbook; the file of the project is the file written.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from . import blocks_common as bc

KEYS = (50, 20)  # the labels of the two keys: 1000 combinations
CURVE_POINTS = 100
ELEMENTS = ("AU", "CU", "ZN")
AT = datetime(2026, 11, 26, 9, 30, tzinfo=UTC)


def synthetic_columns(rows: int, rng: np.random.Generator) -> tuple[dict[str, Any], int, int]:
    """The columns of `data` for about `rows` rows: per cut-off the total and the 1000 groups. → (columns, rows,
    cut-offs)."""
    groups = KEYS[0] * KEYS[1]
    cutoffs = max(1, rows // (groups + 1))
    n = cutoffs * (groups + 1)
    g1 = np.tile(
        np.array([None, *(f"Домен {i:02d}" for i in range(KEYS[0]) for _ in range(KEYS[1]))], dtype=object), cutoffs
    )
    g2 = np.tile(
        np.array([None, *(f"Класс {j:02d}" for _ in range(KEYS[0]) for j in range(KEYS[1]))], dtype=object), cutoffs
    )
    total = np.zeros(n, dtype=bool)
    total[:: groups + 1] = True
    cut = np.repeat(np.arange(cutoffs, dtype=np.float64) * 0.05, groups + 1)
    blocks = rng.integers(1, 5000, n).astype(np.int64)
    volume = blocks * 500.0
    tonnes = volume * rng.uniform(2.4, 3.1, n)
    cols: dict[str, Any] = {
        "g1": g1,
        "g2": g2,
        "total": total,
        "kind": ["cutoff"] * n,
        "cutoff": cut,
        "cutoff_to": np.full(n, np.nan),
        "blocks": blocks,
        "volume": volume,
        "tonnes": tonnes,
    }
    for e, k in zip(ELEMENTS, (1.0, 0.01, 0.01), strict=True):
        miss = rng.random(n) < 0.03
        wt = np.where(miss, 0.0, tonnes)
        grade = rng.lognormal(0.0, 1.0, n)
        total_g = wt * grade
        cols[f"{e}:wt"] = wt
        cols[f"{e}:sum"] = total_g
        cols[f"{e}:metal"] = total_g * k
        cols[f"{e}:grade"] = np.where(wt > 0, grade, np.nan)  # no tonnes with a grade: a gap, not 0
        cols[f"{e}:missing_t"] = np.where(miss, tonnes, 0.0)
        cols[f"{e}:missing_n"] = miss.astype(np.int64)
    return cols, n, cutoffs


def prepare(params: dict[str, Any], work: Path) -> None:
    from bf_core.blocks.model import Model
    from bf_core.db import dialect
    from bf_core.resources import spec
    from bf_core.resources import store as st
    from bf_core.resources import store_data as sd

    ctx = bc.Ctx.create(work)
    try:
        made = bc.regular_model(ctx, 2000, bc.grade_fields(), bc.grade_values)  # the model is small: the report is not
        cols, n, cutoffs = synthetic_columns(int(params["rows"]), np.random.default_rng(3))
        table = sd.build_table(cols, list(ELEMENTS), 2)
        points = np.linspace(0.1, 5.0, CURVE_POINTS)
        curve = {
            "field": "AU",
            "cutoffs": points.tolist(),
            "blocks": np.arange(CURVE_POINTS, 0, -1).tolist(),
            "volume": np.linspace(9e8, 1e6, CURVE_POINTS).tolist(),
            "tonnes": np.linspace(2.5e9, 2.5e6, CURVE_POINTS).tolist(),
            "elements": {
                e: {
                    "grade": np.linspace(0.5, 3.0, CURVE_POINTS).tolist(),
                    "metal": np.linspace(1e9, 1e6, CURVE_POINTS).tolist(),
                }
                for e in ELEMENTS
            },
        }
        control = {
            "tolerance": 1e-12,
            "no_density": {"blocks": 0, "volume": 0.0},
            "warnings": [],
            "sum_groups": {
                "checked": True,
                "ok": True,
                "tonnes": {"sum": 1.0, "total": 1.0, "rel": 0.0},
                "volume": {"sum": 1.0, "total": 1.0, "rel": 0.0},
                "metal": {},
            },
        }
        with dialect.read_tx(ctx.engine) as conn:
            fields = Model.open(conn, ctx.store, made["model_id"]).fields
        body = {
            "model_id": made["model_id"],
            "elements": [{"field": "AU", "unit": "g/t"}, {"field": "CU", "unit": "%"}],
            "groups": ["DOM"],
            "density": "DENS",
            "cutoffs": [round(i * 0.05, 2) for i in range(min(cutoffs, 200))],
        }
        resolved = spec.parse_params(body).resolved(fields).canonical()
        staged = st.stage(ctx.store, table, sd.curve_table(curve), control)
        built = st.Built(
            resolved,
            {"id": made["model_id"], "name": "Модель замера", "grid": {"counts": made["counts"]}},
            ("AU", "CU", "DOM"),
            None,
            staged,
        )
        with dialect.write_tx(ctx.engine) as conn:
            ident = int(st.create(conn, ctx.store, "Отчёт замера", built)["id"])
        ctx.keep(
            report_id=ident,
            rows=n,
            cutoffs=cutoffs,
            first={"tonnes": float(cols["tonnes"][0]), "au_sum": float(cols["AU:sum"][0])},
        )
    finally:
        ctx.close()


def sheet_xml_of(zf: zipfile.ZipFile, title: str) -> str:
    """The part of the sheet with this title (the workbook lists the sheets in order, the parts are numbered so)."""
    wb = zf.read("xl/workbook.xml").decode("utf-8")
    names = [part.split('name="', 1)[1].split('"', 1)[0] for part in wb.split("<sheet ")[1:]]
    assert title in names, names
    return f"xl/worksheets/sheet{names.index(title) + 1}.xml"


def check_book(path: Path, meta: dict[str, Any]) -> None:
    rows = int(meta["rows"])
    with zipfile.ZipFile(path) as zf:
        wb = zf.read("xl/workbook.xml").decode("utf-8")
        for name in ("Отчёт", "Таблица", "Кривая", "Контроль", "Параметры"):
            assert f'name="{name}"' in wb, name
        part = sheet_xml_of(zf, "Таблица")
        tail = b""
        with zf.open(part) as fh:
            head = fh.read(1 << 16).decode("utf-8")
            while chunk := fh.read(1 << 22):
                tail = (tail + chunk)[-(1 << 16) :]
        tail_text = tail.decode("utf-8", "replace") if tail else head
    assert f'<row r="{rows + 1}">' in tail_text and f'<row r="{rows + 2}">' not in tail_text, rows
    first = head.split('<row r="2">', 1)[1].split("</row>", 1)[0]
    for value in (meta["first"]["tonnes"], meta["first"]["au_sum"]):
        assert f"<v>{value!r}</v>" in first, (value, first[:400])  # to the last digit


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    from bf_core.resources import export_job

    bc.limit_threads(params)
    ctx = bc.Ctx.open(work)
    meta = ctx.meta()
    out = Path(tempfile.mkdtemp(prefix="bf-bench-xlsx-"))
    try:
        target = out / "report.xlsx"
        body = {
            "report_id": meta["report_id"],
            "format": "xlsx",
            "path": str(target),
            "what": "table",
            "overwrite": True,
        }
        t0 = time.perf_counter()
        done = export_job.run_export(ctx.engine, ctx.folder, body, at=AT)
        seconds = time.perf_counter() - t0
        assert done["rows"] == meta["rows"] and done["recorded"] is True and target.is_file(), done
        check_book(target, meta)
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        from bf_core.db import dialect
        from bf_core.resources import store as st

        with dialect.read_tx(ctx.engine) as conn:
            assert st.xlsx_sha(conn, int(meta["report_id"])) == sha  # the book of the project is the file written
        rows = int(meta["rows"])
        return {
            "rows": rows,
            "cutoffs": meta["cutoffs"],
            "xlsx_mb": round(target.stat().st_size / 2**20, 1),
            "export_s": round(seconds, 3),
            "rows_per_s": round(rows / seconds),
            "per_100k_rows_s": round(seconds * 1e5 / rows, 3),
            "forecast_20k_rows_s": round(seconds * 2e4 / rows, 3),
            "forecast_2m_rows_s": round(seconds * 2e6 / rows, 2),
        }
    finally:
        ctx.close()
        shutil.rmtree(out, ignore_errors=True)
