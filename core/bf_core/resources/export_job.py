"""Export of a resource report to XLSX / CSV (подшаг 20.5; ТЗ 8.10 «Вывод»; ADR 0027 решения 19, 20, 28, 30;
docs/progress/e20-api.md §5, §6, §10): the job `resources.export` (quick).

    params = request(conn, folder, report_id, body)    # `POST /resources/reports/{id}/export`: the checked parameters
    out = run_export(engine, folder, params, tick=None)  # the body of the job; the job itself is `export_job`

    body    {format: "xlsx" | "csv", path, template?, cutoff?, what?, overwrite?}    (`spec.parse_export` + `what`)
    what    CSV only: `table` (default: every row of the result) | `form` (the form of the template) | `both` (the
            table in the chosen file and «‹имя› · форма.csv»); the XLSX always has all the sheets (`xlsx.py`)
    params  {report_id, format, path, what, template, cutoff, overwrite, record?}   (`path` — with the extension,
            checked; `record` false — the route sets it for a project «только просмотр»: the files are made, the
            project is not written: no `put_xlsx`, no `record_file`, `recorded: false`)
    out     {path, files, format, what, bytes, rows, sheets, form_rows, recorded, seconds, events}

The form (`forms.build`) is made by the core from the rows of the result and the template: the `template` of the body,
else the `template_id` of the report, else a generic one (the groups of the first key in the order of the result,
«Итого»); `cutoff` — the cut-off of the table of the form (null: «без борта», else the lowest). The XLSX shows the
`value` of its cells (Т8.10-5), see `xlsx.py`; the CSV — `csv_out.py`.

A file is written aside («‹имя›.‹случайное›.part» in the folder of the target) and renamed when it is whole, all the
files of one export or none (`composites/ore_report.write_files`): a cancel, a failure and a full disk leave nothing
and the files there before stay as they were. A file that is there — 409 `files_exist {files}` unless `overwrite`
(at the request, at the start of the job and again before the files are put in place: a file may come meanwhile).
Errors `{code, message, hint}`: 404
`resource_report_not_found`, `template_not_found`; 409 `report_building`, `files_exist`, `resource_report_broken`;
422 `bad_param {param}` (`format`, `what`, `path`, `cutoff`), `bad_path` (inside the folder of the project).

After the files are in place (no cancel from there on): the XLSX becomes the last book of the report
(`sidecar:xlsx`, `store.put_xlsx`) and each file goes into `meta.files` (`store.record_file`: {format, path, sha256,
at}) — in one transaction, only if the report has not been rebuilt or deleted meanwhile (`recorded: false` otherwise:
the file is the user's, the report does not list it). The result carries `events` `resources.changed` then.
The export works for a report in any state of the graph (a stale one is exported too: the form shows its numbers).
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks.filters import schema as fs
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.formats.numbers import number_text
from bf_core.jobs.context import JobContext, JobError
from bf_core.jobs.registry import handler
from bf_core.storage.blobs import BlobStore

from . import csv_out, forms, spec, templates
from . import export_cols as ec
from . import store as rst
from . import store_data as sd
from . import xlsx as xl

KIND = "resources.export"
TITLE = "Выгрузка отчёта по ресурсам"
FORMATS = spec.EXPORT_FORMATS
GENERIC_ID = "default"
GENERIC_NAME = "По умолчанию"
LABELS_MAX = templates.MAX_LABELS
Tick = Callable[[float | None, str | None], None]


def _no_tick(_f: float | None, _m: str | None) -> None:
    pass


def _bad(param: str, message: str, hint: str) -> ApiError:
    return spec.bad("bad_param", message, hint, param)


def _comma(x: float | None) -> str:
    return "" if x is None else number_text(float(x)).replace(".", ",")


# --- the request ------------------------------------------------------------------------------------------------------


def target_path(path: Any, fmt: str, folder: Path) -> Path:
    """The file the user chose: full, in a folder that is there, outside the folder of the project, with the
    extension of the format (added when it is missing), not a folder. 422 `bad_param {path}` / `bad_path`."""
    if not isinstance(path, str) or not path or not Path(path).is_absolute():
        raise _bad("path", "Путь к файлу должен быть полным.", "Выберите файл в окне сохранения.")
    target = Path(path)
    if target.suffix.lower() != f".{fmt}":
        target = target.with_name(f"{target.name}.{fmt}")
    if not target.parent.is_dir():
        raise _bad("path", "Папка для файла не найдена.", "Выберите папку заново.")
    if target.resolve().is_relative_to(Path(folder).resolve()):
        raise ApiError(422, "bad_path", "Нельзя сохранить файл внутрь папки проекта.",
                       "Выберите другую папку, например «Документы».", {"param": "path"})  # fmt: skip
    if target.is_dir():
        raise _bad("path", "Под этим именем в папке есть папка.", "Выберите другое имя файла.")
    return target


def files_of(target: Path, fmt: str, what: str) -> list[tuple[Path, str]]:
    """[(file, part)] an export writes: the book (part `book`) or the CSV files (`table` / `form`)."""
    return [(target, "book")] if fmt == "xlsx" else csv_out.files_for(target, what)


def check_clash(files: Sequence[Path], overwrite: bool) -> None:
    """409 `files_exist {files}` when a file is there and `overwrite` is not given."""
    clash = [p for p in files if p.exists() or p.is_symlink()]
    if clash and not overwrite:
        names = ", ".join(f"«{p.name}»" for p in clash)
        raise spec.refuse(
            "files_exist", f"Файл уже есть: {names}. Он не заменяется без подтверждения.",
            "Подтвердите замену или выберите другое имя файла.", files=[str(p) for p in clash],
        )  # fmt: skip


def request(conn: sa.Connection, folder: Path, report_id: int, body: Any) -> dict[str, Any]:
    """The checked parameters of the job `resources.export` (the synchronous part of the route): 404 (report,
    template), 409 `report_building`, `files_exist`, 422 `bad_param`, `bad_path`."""
    req = spec.parse_export(body)
    what = body.get("what") if isinstance(body, Mapping) else None
    if what is None:
        what = "table"
    if what not in csv_out.WHATS:
        raise _bad("what", "Выгрузка: неизвестное значение «что выгружать».",
                   f"Допустимо: {', '.join(csv_out.WHATS)}.")  # fmt: skip
    info = rst.info(conn, int(report_id))
    if info["state"] == "building":
        raise rst.building()
    if req.template is not None:
        templates.get(conn, req.template)
    target = target_path(req.path, req.format, folder)
    check_clash([p for p, _part in files_of(target, req.format, what)], req.overwrite)
    return {"report_id": int(report_id), "format": req.format, "path": str(target), "what": what,
            "template": req.template, "cutoff": req.cutoff, "overwrite": req.overwrite}  # fmt: skip


# --- what the files show ----------------------------------------------------------------------------------------------


def _generic_template(data_path: Path, nkeys: int) -> dict[str, Any]:
    """A template for a report with no template: the labels of the first key in the order of the result (the ones
    over 64 go to «Прочие»), «Итого», tonnes, volume, grade and metal; the metal of g/t in kg."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    labels: list[str] = []
    if nkeys:
        col = pq.read_table(data_path, columns=["g1"]).column("g1")
        labels = [str(v) for v in pc.unique(col).to_pylist() if v is not None][:LABELS_MAX]  # first met first
    body = {"name": GENERIC_NAME, "labels": labels or ["Все блоки"], "subtotals": [], "total": "Итого",
            "columns": ["tonnes", "volume", "grade", "metal"], "balance": False,
            "units": {"tonnes": "t", "volume": "m3", "metal": {"g": "kg", "t": "t"}},
            "decimals": {"tonnes": 0, "volume": 0, "grade": 2, "metal": 1}}  # fmt: skip
    return {"id": GENERIC_ID, **templates.check_body(body), "version": 1}


def _form_rows(data_path: Path, cutoff: float | None) -> list[dict[str, Any]]:
    """The rows the form can use: every row that is not of a cut-off and the rows of the cut-off table (the asked
    cut-off, else the lowest) — a superset of what `forms.build` picks, so a huge table is not turned into dicts."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    kinds = pq.read_table(data_path, columns=["kind", "cutoff"])
    is_cut = pc.equal(kinds.column("kind"), pa.scalar("cutoff"))
    cuts = pc.filter(kinds.column("cutoff"), pc.and_(is_cut, pc.is_valid(kinds.column("cutoff"))))
    if cutoff is None and len(cuts):
        cutoff = float(pc.min(cuts).as_py())
    flt = pc.field("kind") != "cutoff"
    if cutoff is not None:
        tol = 1e-9 * max(1.0, abs(cutoff))
        flt = flt | ((pc.field("cutoff") >= cutoff - tol) & (pc.field("cutoff") <= cutoff + tol))
    return list(pq.read_table(data_path, filters=flt).to_pylist())


def _roles(params: Mapping[str, Any], fields: Sequence[Mapping[str, Any]] | None) -> list[str] | None:
    """The role of each group key (`bench` for a bench); None — the model is not known, the first key is the class."""
    if fields is None:
        return None
    by_name = {str(f["name"]).casefold(): f for f in fields}
    out: list[str] = []
    for g in params.get("groups") or []:
        if isinstance(g, Mapping) and g.get("kind") == "bench":
            out.append("bench")
        else:
            name = str(g.get("field") if isinstance(g, Mapping) else g)
            out.append(str((by_name.get(name.casefold()) or {}).get("role") or ""))
    return out


def _names(conn: sa.Connection, ids: Sequence[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = conn.execute(sa.select(objects.c.id, objects.c.name).where(objects.c.id.in_(list(ids))))
    return {int(r.id): str(r.name) for r in rows}


def _restriction(conn: sa.Connection, params: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """The rows of «Параметры» about the restrictors: the filter of the library and the quick tree (`title_of`)."""
    out: list[tuple[str, Any]] = []
    filter_id, tree = params.get("filter_id"), params.get("restrict")
    ids = [int(filter_id)] if filter_id is not None else []
    if tree:
        ids += fs.object_ids(tree)
    names = _names(conn, ids)
    if filter_id is not None:
        meta = conn.execute(sa.select(objects.c.name, objects.c.meta).where(objects.c.id == int(filter_id))).first()
        if meta is None:
            out.append(("Фильтр библиотеки", f"№ {filter_id} (удалён)"))
        else:
            rules = (meta.meta or {}).get("rules") or {}
            title = fs.title_of(rules, names) if rules else ""
            out.append(("Фильтр библиотеки", f"{meta.name}: {title}" if title else str(meta.name)))
    if tree:
        out.append(("Быстрый ограничитель", fs.title_of(tree, names)))
    if not out:
        out.append(("Ограничитель", "нет: все блоки модели"))
    return out


def _when(v: Any) -> str | None:
    if not v:
        return None
    try:
        t = datetime.fromisoformat(str(v))
    except ValueError:
        return str(v)
    return t.astimezone().strftime("%d.%m.%Y %H:%M")


def _cutoffs_text(values: Sequence[Any]) -> str:
    return "; ".join("без борта" if v is None else _comma(v) for v in values)


def parameter_rows(info: Mapping[str, Any], data: Mapping[str, Any], at: datetime) -> list[tuple[str, Any]]:
    """The rows of the sheet «Параметры»: what the report was made of (ADR 0027 решение 20). `data` — what the job
    read: `restriction` (rows), `template` (the template shown), `cutoff` (the cut-off of the form, text), `rows`."""
    from bf_core import __version__

    p = info.get("params") or {}
    model = info.get("model") or {}
    grid = model.get("grid") or {}
    rows: list[tuple[str, Any]] = [
        ("Отчёт", info.get("name")), ("Версия отчёта", info.get("version")),
        ("Отчёт создан", _when(info.get("created_at"))), ("Отчёт обновлён", _when(info.get("updated_at"))),
        ("Модель", model.get("name")), ("Номер модели", model.get("id")),
        ("Отпечаток ячеек модели (cells_sha)", model.get("cells_sha")),
        ("Сетка: начало", _list_text(grid.get("corner"))), ("Сетка: число ячеек", _list_text(grid.get("counts"))),
        ("Сетка: размер ячейки, м", _list_text(grid.get("size"))), ("Сетка: поворот, °", grid.get("rotation")),
    ]  # fmt: skip
    units = {str(e["field"]).casefold(): e.get("unit") for e in p.get("elements") or [] if isinstance(e, Mapping)}
    for e in p.get("elements") or []:
        rows.append((f"Элемент {e['field']}: единица", ec.unit_label(units[str(e["field"]).casefold()])))
    for name, fp in (info.get("fields") or {}).items():
        rows.append((f"Поле {name}: отпечаток", fp))
    density = p.get("density")
    rows += [
        ("Поле борта", p.get("cutoff_field")),
        ("Борта", _cutoffs_text(p.get("cutoffs") or [])),
        ("Плотность", f"{_comma(density)} т/м³" if isinstance(density, int | float) else (
            f"поле {density}" if density else "не задана")),
    ]  # fmt: skip
    bal = p.get("balance")
    if bal:
        off = bal.get("off_cutoff")
        rows.append(("Баланс", f"балансовые: борт {_comma(bal.get('cutoff'))}; "
                               f"забалансовые: от {_comma(off) if off is not None else '—'}"))  # fmt: skip
    rows += data["restriction"]
    groups = [
        (
            "Уступ"
            if isinstance(g, Mapping) and g.get("kind") == "bench"
            else str(g.get("field") if isinstance(g, Mapping) else g)
        )
        for g in p.get("groups") or []
    ]
    rows.append(("Группы", "; ".join(groups) if groups else "нет"))
    bench = p.get("bench")
    if bench:
        rows.append(("Уступы", f"H = {_comma(bench.get('h'))} м; Z0 = {_comma(bench.get('z0'))}; "
                               f"Z от {_comma(bench.get('z_min'))} до {_comma(bench.get('z_max'))}"))  # fmt: skip
    rows += [
        ("Точек кривой", p.get("curve")), ("Строк в таблице", data["rows"]),
        ("Шаблон формы", data["template"]), ("Борт таблицы «Отчёт»", data["cutoff"]),
        ("Правила (ALGO)", info.get("algo")), ("Хэш параметров", info.get("params_hash")),
        ("Версии библиотек", "; ".join(f"{k} {v}" for k, v in sorted((info.get("versions") or {}).items())) or None),
        ("Выгружено", at.astimezone().strftime("%d.%m.%Y %H:%M")),
        ("Программа", f"BedrockForge 2 {__version__}"),
    ]  # fmt: skip
    return rows


def _list_text(v: Any) -> str | None:
    return None if v is None else "; ".join(_comma(x) if isinstance(x, int | float) else str(x) for x in v)


def _cutoff_note(form: Mapping[str, Any], field: Any) -> str:
    cut = form.get("cutoff") or {}
    name = f"{field} " if field else ""
    if "balance" in cut or "offbalance" in cut:
        bal, off = cut.get("balance"), cut.get("offbalance")
        text = f"балансовые: {name}≥ {_comma(bal)}" if bal is not None else "балансовые: нет строк"
        if off is not None:
            text += f"; забалансовые: {name}от {_comma(off)}"
        return text
    if cut.get("kind") == "cutoff" and cut.get("value") is not None:
        return f"{name}≥ {_comma(cut['value'])}"
    return "без борта (все блоки группы)"


# --- the job ----------------------------------------------------------------------------------------------------------


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _read(engine: sa.Engine, folder: Path, p: Mapping[str, Any]) -> dict[str, Any]:
    """Everything the files show, read in one transaction; 404 / 409 as the module text says."""
    from bf_core.blocks.model import Model

    store, rid = BlobStore(folder), int(p["report_id"])
    with dialect.read_tx(engine) as conn:
        info = rst.info(conn, rid)
        if info["state"] == "building":
            raise rst.building()
        if info["state"] == "error":
            raise rst.broken()
        path = store.path_of(rst.data_sha(conn, rid))
        if not path.is_file():
            raise rst.broken()
        tid = p.get("template") or (info.get("params") or {}).get("template_id")
        template = templates.get(conn, str(tid)) if tid else None
        try:
            model_fields: list[Any] | None = Model.open(conn, store, int(info["model"]["id"])).fields
        except (ApiError, KeyError, TypeError, ValueError):
            model_fields = None  # the model is gone: the first key is taken as the class
        return {"info": info, "path": path, "template": template, "fields": model_fields,
                "curve": rst.read_curve(conn, store, rid), "control": rst.read_control(conn, store, rid),
                "restriction": _restriction(conn, info.get("params") or {})}  # fmt: skip


def _book(got: Mapping[str, Any], p: Mapping[str, Any], at: datetime) -> tuple[xl.BookData, dict[str, Any]]:
    import pyarrow.parquet as pq

    info, path = got["info"], got["path"]
    params = info.get("params") or {}
    schema = pq.ParquetFile(path).schema_arrow.empty_table()
    keys = ec.key_titles(params, sd.keys_of(schema))
    elements = ec.element_names(params, sd.elements_of(schema))
    template = got["template"] or _generic_template(path, len(keys))
    rows = _form_rows(path, p.get("cutoff"))
    roles = _roles(params, got["fields"]) if got["template"] is not None else None
    el_form = [{"name": n, "unit": u} for n, u in elements]
    form = forms.build(rows, template, el_form, roles, p.get("cutoff"))
    if got["template"] is None and not keys:  # no groups: the total row only, not a row of the template with zeros
        for s in form["sections"]:
            s["rows"] = [r for r in s["rows"] if r["type"] != "group"]
    total = int(pq.ParquetFile(path).metadata.num_rows)
    tname = f"{template['name']} (версия {template['version']})" if got["template"] is not None else GENERIC_NAME
    cutoff = _cutoff_note(form, params.get("cutoff_field"))
    notes = [f"Модель: {(info.get('model') or {}).get('name')}", f"Борт: {cutoff}", f"Шаблон: {tname}"]
    data = xl.BookData(str(info["name"]), form, path, keys, elements, got["curve"], got["control"],
                       parameter_rows(info, {"restriction": got["restriction"], "template": tname, "cutoff": cutoff,
                                             "rows": total}, at), notes)  # fmt: skip
    return data, form


def _write_csv(parts: Sequence[Path], files: Sequence[tuple[Path, str]], data: xl.BookData, form: Mapping[str, Any],
               tick: Tick) -> int:  # fmt: skip
    rows = 0
    for part, (_target, kind) in zip(parts, files, strict=True):
        if kind == "form":
            csv_out.write_form(part, form)
        else:
            import pyarrow.parquet as pq

            total = int(pq.ParquetFile(data.data_path).metadata.num_rows)
            head = ec.table_head(data.keys, data.elements)
            rows = csv_out.write_table(part, head, ec.iter_columns(data.data_path), lambda f, m: tick(
                None if f is None else 0.05 + 0.85 * f, m), total)  # fmt: skip
    return rows


def run_export(engine: sa.Engine, folder: Path, params: Mapping[str, Any], *, tick: Tick | None = None,
               at: datetime | None = None) -> dict[str, Any]:  # fmt: skip
    """The body of the job `resources.export` (the handler and the tests). `at` (or `params["at"]`, ISO text) — the time
    written in «Параметры» as «Выгружено»; the same time gives the same bytes."""
    from bf_core.composites import ore_report

    tick, t0 = tick or _no_tick, time.perf_counter()
    fmt, what = str(params["format"]), str(params.get("what") or "table")
    if fmt not in FORMATS:
        raise _bad("format", "Формат выгрузки: неизвестное значение.", f"Допустимо: {', '.join(FORMATS)}.")
    if what not in csv_out.WHATS:
        raise _bad("what", "Выгрузка: неизвестное значение «что выгружать».",
                   f"Допустимо: {', '.join(csv_out.WHATS)}.")  # fmt: skip
    at = at or (datetime.fromisoformat(str(params["at"])) if params.get("at") else datetime.now().astimezone())
    store, rid = BlobStore(folder), int(params["report_id"])
    target = target_path(params.get("path"), fmt, folder)
    files = files_of(target, fmt, what)
    check_clash([f for f, _k in files], bool(params.get("overwrite")))
    tick(0.0, "Чтение отчёта")
    got = _read(engine, folder, params)
    version = int(got["info"]["version"])
    tick(0.02, "Расчёт формы")
    data, form = _book(got, params, at)
    stats: dict[str, int] = {}

    def write(parts: list[Path]) -> None:
        if fmt == "xlsx":
            stats.update(xl.write_book(parts[0], data, tick))
        else:
            stats["rows"] = _write_csv(parts, files, data, form, tick)
        check_clash([f for f, _k in files], bool(params.get("overwrite")))  # a file may come while this one is made

    ore_report.write_files([f for f, _k in files], write)
    # no tick from here on: a cancel after the files are in place would report them as not written
    entries = [{"format": fmt, "path": str(f), "sha256": _sha(f), "at": at.isoformat()} for f, _k in files]
    size = sum(f.stat().st_size for f, _k in files)
    record = bool(params.get("record", True))  # False: a project «только просмотр» — nothing is written into it
    staged = store.stage_file(target) if fmt == "xlsx" and record else None
    recorded = False
    try:
        if record:
            with dialect.write_tx(engine) as conn:
                try:
                    now = rst.info(conn, rid)
                except ApiError:
                    now = None  # deleted meanwhile
                if now is not None and int(now["version"]) == version and now["state"] == "ready":
                    if staged is not None:
                        rst.put_xlsx(conn, store, rid, staged)
                    for e in entries:
                        rst.record_file(conn, rid, e)
                    recorded = True
    finally:
        if staged is not None and not recorded:
            staged[0].unlink(missing_ok=True)
    out: dict[str, Any] = {
        "path": str(target), "files": [str(f) for f, _k in files], "format": fmt, "what": what, "bytes": size,
        "rows": stats.get("rows", 0), "sheets": stats.get("sheets"), "form_rows": len(forms.rows_flat(form)),
        "recorded": recorded, "seconds": round(time.perf_counter() - t0, 3),
    }  # fmt: skip
    if recorded:
        out["events"] = [["resources.changed", {"kind": rst.KIND, "id": rid}]]
    return out


@handler(KIND, TITLE, queue="quick", max_attempts=1, estimate_memory=lambda _p: 512)
def export_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        out = run_export(ctx.engine(), ctx.project_folder, ctx.params, tick=ctx.tick)
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint, retry=False) from exc
    except MemoryError as exc:
        raise JobError("out_of_memory", "Не хватило памяти на выгрузку отчёта.",
                       "Закройте другие программы или выгрузите CSV.") from exc  # fmt: skip
    except OSError as exc:
        raise JobError("file_unwritable", f"Файл не записан: {exc.strerror or exc}.",
                       "Выберите другую папку или закройте файл в другой программе.") from exc  # fmt: skip
    ctx.log(f"Записан файл «{Path(out['path']).name}»: строк таблицы {out['rows']}")
    return out
