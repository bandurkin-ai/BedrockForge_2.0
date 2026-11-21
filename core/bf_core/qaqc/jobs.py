"""The jobs of QAQC (ADR 0020 §6; ADR 0002): `qaqc.import` — a file (CSV, TXT, DAT, XLSX, XLS) or a table of a
drillhole set → a `qaqc_data` object; `qaqc.report` (quick) — the summary of a report (`run_report`, `report.py`);
`qaqc.report_file` (quick) — its XLSX (`run_report_file`, `report_xlsx.py`).
Registered by an explicit import of this module in `jobs/registry.py`.

Params: {name, source: {kind: "file", path, sheet?, encoding?, delimiter?, header_row?} | {kind: "table", set_id,
table}, mapping, replace_id?}. Stages: «Чтение файла» (or «Чтение таблицы»), «Нормализация», «Запись записей»;
`tick` between them and between the columns — a cancel is answered there. The records are written to a temp file
of the store; the object is made (or replaced) only in the last write transaction, so a cancelled or failed job
leaves no object and no file. Result: {data_id, name, rows, replaced, counts: {no_parent, dup_ids, bad_dates,
below_dl, above_dl, bad_numbers, empty_ids, empty_dates, empty_seq, elements}, notes, events}; the events
(`objects.changed {kind: "qaqc_data", id}`) travel in the result (a worker cannot publish them).

`inspect_file` / `check_submit` — the synchronous parts of `POST /data/inspect` and `POST /data` (the router).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from bf_core.api.errors import ApiError
from bf_core.jobs.context import JobContext, JobError
from bf_core.jobs.registry import handler

KIND = "qaqc.import"
PREVIEW_ROWS = 50
SAMPLES = 5


def options_of(source: dict[str, Any]) -> Any:
    from bf_core.formats.table import ReadOptions

    header = source.get("header_row", 1)
    if isinstance(header, bool) or not isinstance(header, int) or header < 0:
        raise ApiError(422, "bad_param", "Неверный номер строки заголовка.", "Укажите номер строки с именами колонок.",
                       extra={"param": "header_row"})  # fmt: skip
    text = lambda k: str(source[k]) if source.get(k) not in (None, "") else None  # noqa: E731
    return ReadOptions(encoding=text("encoding"), delimiter=text("delimiter"), header_row=header, sheet=text("sheet"))


def _path(source: dict[str, Any]) -> Path:
    raw = source.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise ApiError(422, "bad_param", "Не выбран файл.", "Выберите файл контрольных проб.", extra={"param": "path"})
    path = Path(raw)
    if not path.is_file():
        raise ApiError(404, "file_not_found", f"Файл «{path.name}» не найден.", "Проверьте путь к файлу.",
                       extra={"param": "path"})  # fmt: skip
    return path


def _kind_of(cells: list[str]) -> str:
    from bf_core.formats import numbers as nm

    filled = [c for c in cells if c.strip()]
    if not filled:
        return "text"
    _values, kinds = nm.classify(filled)
    ok = {nm.NUMBER, nm.BELOW, nm.ABOVE, nm.MARKER}
    return "number" if all(int(k) in ok for k in kinds) and any(int(k) != nm.MARKER for k in kinds) else "text"


def inspect_file(source: dict[str, Any]) -> dict[str, Any]:
    """Columns (names as the reader gives them, kind by the first rows, samples), the first rows, the mapping."""
    from bf_core.formats.errors import FormatError
    from bf_core.formats.inspect import inspect_table
    from bf_core.formats.table import unique_names

    from .normalize import auto_mapping

    path, options = _path(source), options_of(source)
    try:
        pv = inspect_table(path, options, limit=options.header_row + PREVIEW_ROWS)
    except FormatError as exc:
        raise ApiError(422, exc.code, exc.message, exc.hint) from exc
    if pv.kind == "dat":
        header, data = [f["name"] for f in pv.fields], pv.rows[:PREVIEW_ROWS]
    else:
        hr = options.header_row
        header = [c.strip() for c in pv.rows[hr - 1]] if hr and len(pv.rows) >= hr else []
        data = pv.rows[hr : hr + PREVIEW_ROWS]
    width = max([len(header), *(len(row) for row in data)], default=0)
    cells = [[row[i] if i < len(row) else "" for row in data] for i in range(width)]
    named = [i < len(header) and bool(header[i]) for i in range(width)]
    keep = [i for i in range(width) if named[i] or any(c.strip() for c in cells[i])]
    names = unique_names([header[i] if named[i] else f"Колонка {i + 1}" for i in keep])
    columns: list[dict[str, Any]] = []
    for name, i in zip(names, keep, strict=True):
        samples = list(dict.fromkeys(c for c in cells[i] if c.strip()))[:SAMPLES]
        columns.append({"name": name, "kind": _kind_of(cells[i]), "samples": samples})
    rows = [[row[i] if i < len(row) else "" for i in keep] for row in data]
    info = pv.info
    return {
        "kind": "file", "columns": columns, "rows": pv.estimated_rows, "rows_exact": pv.kind == "dat",
        "mapping": auto_mapping([(c["name"], c["kind"]) for c in columns]), "preview": rows,
        "info": {"format": pv.kind, "encoding": info.encoding, "delimiter": info.delimiter,
                 "header_row": options.header_row, "sheet": info.sheet, "sheets": info.sheets},
        "summary": pv.summary, "warnings": pv.warnings,
    }  # fmt: skip


def inspect_table(project: Any, source: dict[str, Any]) -> dict[str, Any]:
    from bf_core.db import dialect

    from . import source_table as st
    from .normalize import _UNITS, auto_mapping

    with dialect.read_tx(project.engine) as conn:
        ref = st.resolve(conn, source.get("set_id"), source.get("table"))
        units = {f.name: f.unit for f in st._fields(conn, ref)}
    pv = st.preview(project, ref)
    cols = []
    for i, (name, kind) in enumerate(pv["columns"]):
        samples = list(dict.fromkeys(row[i] for row in pv["rows"] if row[i]))[:SAMPLES]
        cols.append({"name": name, "kind": kind, "samples": samples})
    mapping = auto_mapping(pv["columns"])
    elements = [e for e in mapping["elements"] if e["column"] not in (st.FROM, st.TO)]
    for e in elements:  # the unit of the field of the table, when the name tells none
        unit = units.get(e["column"])
        if e["unit"] is None and unit:
            e["unit"] = _UNITS.get(unit.strip().casefold(), unit.strip())
    mapping["elements"] = elements
    if mapping.get("hole") is None and any(n == st.HOLE for n, _k in pv["columns"]):
        mapping["hole"] = st.HOLE
    return {"kind": "table", "columns": cols, "rows": ref.rows, "rows_exact": True, "mapping": mapping,
            "preview": pv["rows"], "info": {"set_id": ref.set_id, "table_id": ref.table_id, "set_name": ref.set_name,
            "table_name": ref.table_name, "rev": ref.rev}, "summary": f"Таблица «{ref.table_name}» набора «"
            f"{ref.set_name}»: строк {ref.rows}", "warnings": []}  # fmt: skip


def source_kind(source: Any) -> str:
    kind = source.get("kind") if isinstance(source, dict) else None
    if kind not in ("file", "table"):
        raise ApiError(422, "bad_param", "Не выбран источник данных.", "Выберите файл или таблицу набора скважин.",
                       extra={"param": "source"})  # fmt: skip
    return str(kind)


def check_submit(project: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Before the job: the source, the mapping against its columns, the name and the limit. → the job params."""
    from bf_core.db import dialect

    from . import service as sv
    from .normalize import parse_mapping

    source = body.get("source")
    kind = source_kind(source)
    assert isinstance(source, dict)
    replace_id = body.get("replace_id")
    if replace_id is not None and (isinstance(replace_id, bool) or not isinstance(replace_id, int)):
        raise ApiError(422, "bad_param", "Неверные данные для замены.", "Выберите данные QAQC заново.",
                       extra={"param": "replace_id"})  # fmt: skip
    if kind == "file":
        names = [c["name"] for c in inspect_file(source)["columns"]]
        clean_source = {"kind": "file", **{k: source.get(k) for k in ("path", "sheet", "encoding", "delimiter",
                                                                        "header_row") if k in source}}  # fmt: skip
    else:
        from . import source_table as st

        with dialect.read_tx(project.engine) as conn:
            ref = st.resolve(conn, source.get("set_id"), source.get("table"))
            names = [n for n, _k in st.table_columns(conn, ref)]
        clean_source = {"kind": "table", "set_id": ref.set_id, "table": ref.table_id}
    mapping = parse_mapping(body.get("mapping"), names)
    with dialect.read_tx(project.engine) as conn:
        name = sv.check_new(conn, body.get("name") or _default_name(clean_source), replace_id)
    return {"name": name, "source": clean_source, "mapping": mapping.to_json(), "replace_id": replace_id}


def _default_name(source: dict[str, Any]) -> str:
    if source["kind"] == "file":
        return Path(str(source["path"])).stem
    return "Данные QAQC"


def _memory(params: dict[str, Any]) -> int:
    source = params.get("source") or {}
    try:
        size = Path(str(source.get("path"))).stat().st_size if source.get("kind") == "file" else 0
    except OSError:
        size = 0
    return 512 + int(size / 2**20 * 8)


def _sha256(path: Path, ctx: JobContext) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
            ctx.tick()
    return h.hexdigest()


def _names(ctx: JobContext, source: dict[str, Any]) -> list[str]:
    """The columns of the source before it is read (the mapping tells the reader what to keep as text)."""
    if source["kind"] == "file":
        return [c["name"] for c in inspect_file(source)["columns"]]
    from bf_core.db import dialect

    from . import source_table as st

    with dialect.read_tx(ctx.engine()) as conn:
        return [n for n, _k in st.table_columns(conn, st.resolve(conn, int(source["set_id"]), source["table"]))]


def _read(ctx: JobContext, source: dict[str, Any], mapping: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """(columns of the table, meta of the source)."""
    from dataclasses import replace

    from bf_core.formats.errors import FormatError
    from bf_core.formats.registry import read_table

    from .normalize import text_columns

    if source["kind"] == "file":
        path = _path(source)
        options = replace(options_of(source), text_columns=text_columns(mapping))
        ctx.tick(0.0, "Чтение файла")
        try:
            tab = read_table(path, options, lambda f, m: ctx.tick(0.4 * f, m or "Чтение файла"))
        except FormatError as exc:
            raise JobError(exc.code, exc.message, exc.hint) from exc
        for w in tab.warnings:
            ctx.log(w, "warn")
        meta: dict[str, Any] = {
            "kind": "file",
            "name": path.name,
            "path": str(path),
            "sha256": _sha256(path, ctx),
            **{k: source.get(k) for k in ("sheet", "encoding", "delimiter", "header_row") if k in source},
        }
        return {c.name: c for c in tab.columns}, meta  # fmt: skip
    from types import SimpleNamespace

    from . import source_table as st

    ctx.tick(0.0, "Чтение таблицы")
    project = SimpleNamespace(engine=ctx.engine(), folder=ctx.project_folder)
    names = [c for c in [*mapping.roles.values(), *(e.column for e in mapping.elements),
                         *(e.dl_column for e in mapping.elements), *mapping.extra] if c]  # fmt: skip
    cols, ref = st.read_columns(project, int(source["set_id"]), source["table"], names, lambda: ctx.tick())
    return cols, ref.source()


@handler(KIND, "Импорт данных QAQC", queue="heavy", max_attempts=1, estimate_memory=_memory)
def run_import(ctx: JobContext) -> dict[str, Any]:
    from bf_core.db import dialect
    from bf_core.storage.blobs import BlobStore

    from . import service as sv
    from .normalize import normalize, parse_mapping

    p = ctx.params
    staged = None
    try:
        source = dict(p["source"])
        cols, meta_source = _read(ctx, source, parse_mapping(p["mapping"], _names(ctx, source)))
        mapping = parse_mapping(p["mapping"], cols)  # against the columns as read
        made = normalize(cols, mapping, lambda f, m: ctx.tick(0.4 + 0.4 * f, f"Нормализация: {m}"))
        del cols
        ctx.tick(0.8, "Запись записей")
        store = BlobStore(ctx.project_folder)  # type: ignore[arg-type]
        staged = sv.stage(store, made.records, lambda: ctx.tick(None, "Запись записей"))
        meta = sv.make_meta(made.records, made.counts, made.notes, meta_source, mapping.to_json())
        ctx.tick(0.98, "Сохранение")
        replace_id = p.get("replace_id")
        with dialect.write_tx(ctx.engine()) as conn:
            if replace_id is None:
                data_id = sv.create(conn, store, staged, name=p["name"], meta=meta)
            else:
                sv.replace(conn, store, int(replace_id), staged, meta, name=p["name"])
                data_id = int(replace_id)
        staged = None
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    finally:
        sv.discard(staged)
    for note in made.notes:
        ctx.log(note, "warn")
    counts = {k: v for k, v in made.counts.items() if k != "rows"}
    return {"data_id": data_id, "name": p["name"], "rows": made.records.n, "replaced": replace_id is not None,
            "counts": counts, "notes": made.notes,
            "events": [["objects.changed", {"kind": sv.KIND, "id": data_id}]]}  # fmt: skip


REPORT = "qaqc.report"


@handler(REPORT, "Отчёт QAQC", queue="quick", max_attempts=1)
def run_report(ctx: JobContext) -> dict[str, Any]:
    """The summary of a report (`report.compute`) over its records and inputs. A new report is `building` until
    the end (a cancel drops it); a rebuild (`inputs` in the params) changes the report only in the last
    transaction — a cancelled rebuild leaves the report as it was."""
    from bf_core.db import dialect
    from bf_core.storage.blobs import BlobStore

    from . import records as r
    from . import report as rp
    from .limits import QaqcError

    p = ctx.params
    report_id = int(p["report_id"])
    store = BlobStore(ctx.project_folder)  # type: ignore[arg-type]
    if p.get("inputs") is None:  # a new report: its job is this one (a cancel drops it — `drop_partial`)
        with dialect.write_tx(ctx.engine()) as conn:
            rp.set_job(conn, report_id, ctx.id)
    try:
        inputs = p.get("inputs")
        if inputs is None:
            with dialect.read_tx(ctx.engine()) as conn:
                inputs = rp.read_json(conn, store, report_id, rp.INPUTS)
        if inputs is None:
            raise JobError("qaqc_report_broken", "Нет снимка входов отчёта.", "Создайте отчёт заново.")
        ctx.tick(0.0, "Чтение записей")
        path = store.path_of(inputs["data_sha256"])
        if not path.is_file():
            raise JobError("qaqc_report_broken", "Файл записей отчёта не найден.", "Создайте отчёт заново.")
        recs = r.open_file(path)
        summary = rp.compute(recs, inputs, lambda f, m: ctx.tick(0.05 + 0.9 * f, f"Расчёт: {m}"))
        ctx.tick(0.97, "Сохранение")
        with dialect.write_tx(ctx.engine()) as conn:
            version = rp.finish(conn, store, report_id, summary, inputs if p.get("inputs") is not None else None)
    except QaqcError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    return {"report_id": report_id, "version": version, "rebuilt": p.get("inputs") is not None,
            "events": [["objects.changed", {"kind": rp.KIND, "id": report_id}]]}  # fmt: skip


REPORT_FILE = "qaqc.report_file"


def _stage_file(path: Path) -> tuple[Path, str, int]:
    """A finished file of the store's temp folder as a staged blob: fsync, SHA-256, size."""
    import os

    h = hashlib.sha256()
    with open(path, "rb+") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
        os.fsync(fh.fileno())
    return path, h.hexdigest(), path.stat().st_size


@handler(REPORT_FILE, "Файл отчёта QAQC", queue="quick", max_attempts=1)
def run_report_file(ctx: JobContext) -> dict[str, Any]:
    """The XLSX of a ready report (`report_xlsx.write`) into a temp file of the store, then the blob `sidecar:xlsx`
    in the last transaction; a report rebuilt meanwhile (another version) — refused, nothing written."""
    import uuid

    from bf_core.db import dialect
    from bf_core.storage.blobs import BlobStore

    from . import records as r
    from . import report as rp
    from . import report_xlsx as rx
    from .limits import QaqcError

    p = ctx.params
    report_id, version = int(p["report_id"]), int(p["version"])
    store = BlobStore(ctx.project_folder)  # type: ignore[arg-type]
    store.tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = store.tmp_dir / f"{uuid.uuid4().hex}.xlsx"
    try:
        with dialect.read_tx(ctx.engine()) as conn:
            row = conn.execute(sa_report(report_id)).first()
            if row is None or row.state != "ready":
                raise JobError("qaqc_report_not_found", "Отчёт QAQC не найден.", "Обновите раздел «Отчёты».")
            inputs = rp.read_json(conn, store, report_id, rp.INPUTS)
            summary = rp.read_json(conn, store, report_id, rp.SUMMARY)
            pics = rp.images(conn, store, report_id)
            meta = dict(row.meta or {})
        listed = rp.charts_of(summary, meta.get("charts") or "flagged")
        ctx.tick(0.0, "Чтение записей")
        recs = r.open_file(store.path_of(inputs["data_sha256"]))
        limit = int(p.get("limit") or rx.MAX_LINES)
        rx.write(tmp, recs, inputs, meta, pics, listed["charts"], listed["omitted"], lambda f, m: ctx.tick(0.9 * f, m),
                 limit=limit)  # fmt: skip
        ctx.tick(0.95, "Сохранение")
        staged = _stage_file(tmp)
        with dialect.write_tx(ctx.engine()) as conn:
            now = conn.execute(sa_report(report_id)).first()
            if now is None or int(now.version) != version:
                raise JobError("stale", "Отчёт изменили, пока строился файл.", "Сформируйте файл отчёта заново.")
            sha = rp.put_file(conn, store, report_id, staged)
    except QaqcError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    finally:
        tmp.unlink(missing_ok=True)
    return {"report_id": report_id, "kind": "xlsx", "sha256": sha, "size": staged[2],
            "events": [["objects.changed", {"kind": rp.KIND, "id": report_id}]]}  # fmt: skip


def sa_report(report_id: int) -> Any:
    import sqlalchemy as sa

    from bf_core.db.schema import objects

    from . import report as rp

    return sa.select(objects).where(objects.c.id == report_id, objects.c.kind == rp.KIND)
