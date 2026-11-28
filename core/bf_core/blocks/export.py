"""Export of a block model (подшаг 17.16; ТЗ 8.7.7, 7.35 Д-17; ADR 0023 §12, decision 20; the contract —
`docs/progress/e17-api.md` §11 «Проверка и экспорт»).

    params = request(conn, folder, model_id, body)       # `POST /blocks/{id}/export`: 404, 409 `files_exist`, 422
    out = run_export(engine, folder, params, tick=tick)  # the job `blocks.export` and the registry of Д-17:
                                                         # {path, format, rows, total, bytes, warnings}

Formats (`FORMATS`): `dat` (Micromine DAT, the code page `cp1251` / `bocu1` / `utf-8`), `csv` (a delimiter, a decimal
comma or point, an encoding, the header «plain» or «micromine» — three lines with the grid), `parquet`, `omf`. The
columns of DAT and CSV are X, Y, Z (the centre of the block in the axes of the project — a turned model and every
sub-block are given as they are, one row each), DX, DY, DZ (the size along the axes of the model), then the fields
`fields` (None — all, in the order of the model; a list — in the order asked); Parquet adds `idx` and, for a
model with sub-blocks, `sx…wz`.
A category is written as its label, an empty value — as nothing (`empty` None) or as the number the user chose
(`empty`, DAT and CSV): a gap is never a zero. A real value equal to that code is refused (422 `empty_code_taken`).

Only the blocks that pass are written: the filter `filter_id` of the library and the quick restrictor `restrict`
together (AND, `restrict.combine`; a block passes where its share is > 0, № 65). The model is read by portions of
`ROWS` blocks (`Model.iter`), a portion becomes a portion of the file at once: memory does not depend on the size
of the model (OMF writes the whole grid densely, so it is offered up to 100 million cells, as in `omf_job`; a model
with sub-blocks is refused for it: 422 `omf_subblocks`, «только после регуляризации»; the filter and the fields go
to OMF too — a block that did not pass is an empty cell).

The file is made as «‹имя›.part» and renamed when it is complete: a cancel (`tick` raises), a failure and an empty
result (no block passed: 422 `export_empty`) leave nothing. The same model, options and filter give the same bytes
(DAT, CSV, Parquet). Numbers are the shortest text that gives the same double — a centre comes back to 1e−9 м
(Т8.7-4). It works in a read-only project (nothing of the project is written but the row of the log).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.registry import handler
from bf_core.storage.blobs import BlobStore

from . import calc_job, geometry, restrict
from . import schema as bs
from .filters import schema as fs
from .model import ANY, Chunk, Model, list_unsupported

KIND = "blocks.export"
TITLE = "Экспорт блочной модели"
FORMATS = {"dat": (".dat",), "csv": (".csv", ".txt"), "parquet": (".parquet",), "omf": (".omf",)}
HEADERS = ("plain", "micromine")
DECIMALS = (",", ".")
DEFAULT_ENCODING = {"dat": "cp1251", "csv": "utf-8-sig"}
ROWS = 1 << 18  # blocks in a portion
Tick = Callable[[float | None, str | None], None]
Cancel = Callable[[], None]


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def bad_param(param: str, message: str, hint: str = "Проверьте параметры экспорта.") -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


def empty_result() -> ApiError:
    return ApiError(422, "export_empty", "Ни один блок не прошёл отбор: записывать нечего.",
                    "Ослабьте фильтр или ограничитель, либо отмените их.")  # fmt: skip


# --- the body ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    model_id: int
    format: str
    path: str
    fields: tuple[str, ...] | None
    filter_id: int | None
    restrict: dict[str, Any] | None
    header: str
    encoding: str
    delimiter: str
    decimal: str
    empty: float | None
    overwrite: bool


def parse(model_id: int, body: Mapping[str, Any]) -> Spec:
    """The body of `POST /blocks/{id}/export` checked (the shapes only): 422 `bad_param {param}`."""
    from bf_core.exporting.blocks_writers import CSV_ENCODINGS, DAT_ENCODINGS, DELIMITERS

    fmt = body.get("format")
    if fmt not in FORMATS:
        raise bad_param("format", "Неизвестный формат экспорта.", "Допустимо: DAT, CSV, OMF, Parquet.")
    path = body.get("path")
    if not isinstance(path, str) or not path.strip():
        raise bad_param("path", "Не выбран путь файла.", "Выберите, куда сохранить файл.")
    names = body.get("fields")
    if names is not None and (not isinstance(names, (list, tuple)) or not all(isinstance(x, str) for x in names)):
        raise bad_param("fields", "Поля задаются списком имён.", "Отметьте поля в окне экспорта.")
    if fmt == "omf" and names is not None and not names:
        raise bad_param("fields", "Для OMF выберите хотя бы одно поле.", "Отметьте поля в окне экспорта.")
    fid = body.get("filter_id")
    if fid is not None and (isinstance(fid, bool) or not isinstance(fid, int) or fid < 1):
        raise bad_param("filter_id", "Номер фильтра — целое число от 1.")
    quick = body.get("restrict")
    if quick is not None:
        quick = fs.normalize_rules(quick, "restrict")
    header = body.get("header", "plain")
    if header not in HEADERS:
        raise bad_param("header", "Заголовок: «plain» или «micromine».")
    encoding = body.get("encoding") or DEFAULT_ENCODING.get(str(fmt), "utf-8")
    allowed = DAT_ENCODINGS if fmt == "dat" else CSV_ENCODINGS
    if fmt in ("dat", "csv") and encoding not in allowed:
        raise bad_param("encoding", f"Кодировка «{encoding}» не подходит.", "Допустимо: " + ", ".join(allowed) + ".")
    delimiter = body.get("delimiter", ";")
    if delimiter not in DELIMITERS:
        raise bad_param("delimiter", "Разделитель: «;», «,», табуляция или «|».")
    decimal = body.get("decimal", ",")
    if decimal not in DECIMALS:
        raise bad_param("decimal", "Десятичный знак: запятая или точка.")
    if fmt == "csv" and decimal == delimiter:
        raise bad_param("decimal", "Десятичный знак совпадает с разделителем.", "Выберите «;» или табуляцию.")
    empty = body.get("empty")
    if empty is not None:
        try:
            empty = float(str(empty).replace(",", ".")) if isinstance(empty, str) else float(empty)
        except (TypeError, ValueError):
            raise bad_param("empty", "Код пустого — число.", "Например -99.") from None
        if isinstance(body.get("empty"), bool) or empty != empty or abs(empty) == float("inf"):
            raise bad_param("empty", "Код пустого — конечное число.", "Например -99.")
    overwrite = body.get("overwrite", False)
    if not isinstance(overwrite, bool):
        raise bad_param("overwrite", "Ожидается true или false.")
    return Spec(int(model_id), str(fmt), path, None if names is None else tuple(names), fid, quick, str(header),
                str(encoding), str(delimiter), str(decimal), empty, overwrite)  # fmt: skip


def target_path(path: str | Path, fmt: str) -> Path:
    """The file the export writes: the chosen name, with the extension of the format after it when it has another."""
    p = Path(path)
    return p if p.suffix.lower() in FORMATS[fmt] else p.with_name(p.name + FORMATS[fmt][0])


def check_target(spec: Spec, folder: Path, *, in_project: bool = False) -> Path:
    """The path of a save window: full, in an existing folder, outside the project, not replaced silently.
    `in_project` — the export of the project (the registry of Д-17) chose the folder itself, no check of it."""
    p = Path(spec.path)
    if not p.is_absolute():
        raise bad_param("path", "Путь к файлу должен быть полным.", "Выберите файл в окне сохранения.")
    if not p.parent.is_dir():
        raise bad_param("path", "Папка для файла не найдена.", "Выберите папку заново.")
    target = target_path(p, spec.format)
    if not in_project and target.resolve().is_relative_to(Path(folder).resolve()):
        raise ApiError(422, "bad_path", "Нельзя сохранить файл внутрь папки проекта.",
                       "Выберите другую папку, например «Документы».", {"param": "path"})  # fmt: skip
    if target.is_dir():
        raise bad_param("path", "Под этим именем в папке есть папка.", "Выберите другое имя файла.")
    if not spec.overwrite and (target.is_file() or target.is_symlink()):
        raise ApiError(409, "files_exist", f"Файл «{target.name}» уже есть.",
                       "Подтвердите замену или выберите другое имя файла.", {"files": [target.name]})  # fmt: skip
    return target


# --- the model and the plan -------------------------------------------------------------------------------------------


def _grid_cells(model: Model) -> int:
    c = model.grid.counts
    return int(c[0]) * int(c[1]) * int(c[2])


def selected(model: Model, names: Sequence[str] | None) -> list[dict[str, Any]]:
    """The fields to write: all, or `names` (no case; each once, in the order asked); 404 `block_field_not_found`."""
    if names is None:
        return [dict(f) for f in model.fields]
    return [model.field(n) for n in model.reader.canonical(names)]


def columns_of(model: Model, specs: Sequence[Mapping[str, Any]], fmt: str) -> tuple[list[Any], list[str]]:
    """The columns of the file for the fields: a name the geometry (or `idx`, `sx…`) already has gets «_2»."""
    from bf_core.exporting.blocks_writers import CATEGORY, GEO, NUMBER, Column

    taken = {n.casefold() for n in GEO}
    if fmt == "parquet":
        taken |= {"idx", *(("sx", "sy", "sz", "wx", "wy", "wz") if model.sub else ())}
    out: list[Any] = []
    warnings: list[str] = []
    for f in specs:
        name = base = str(f["name"])
        k = 1
        while name.casefold() in taken:
            k += 1
            name = f"{base}_{k}"
        taken.add(name.casefold())
        if name != base:
            warnings.append(f"Поле «{base}» записано как «{name}»: имя занято столбцом геометрии.")
        category = f.get("kind") == bs.CATEGORY
        out.append(Column(name, CATEGORY if category else NUMBER, tuple(str(x) for x in f.get("labels") or ()),
                          f.get("unit")))  # fmt: skip
    return out, warnings


def check_filter_fields(model: Model, tree: Mapping[str, Any] | None) -> None:
    """422 `filter_field_missing` before any work: the filter reads a field the model has not."""
    if tree is None:
        return
    for name in fs.fields_of(fs.normalize_rules(tree)):
        try:
            model.field(name)
        except ApiError:
            raise restrict.field_missing(name, model.name) from None


def grid_line(model: Model) -> tuple[list[float] | None, str | None]:
    """(x0, dx, y0, dy, z0, dz) of the line of the grid of a CSV Micromine, x0 y0 z0 — the centre of the first parent;
    None and the reason when the line cannot say it (a turn, a variable step)."""
    g = model.grid
    if model.listed:
        return None, "Строка сетки не записана: модель — список блоков."
    if g.rotation != 0.0:
        return None, "Строка сетки не записана: модель повёрнута. При импорте укажите угол поворота."
    if g.tensors is not None or g.size is None:
        return None, "Строка сетки не записана: у модели переменный шаг сетки."
    c, d = g.corner, g.size
    return [c[0] + d[0] / 2, d[0], c[1] + d[1] / 2, d[1], c[2] + d[2] / 2, d[2]], None


def grid_note(model: Model) -> str:
    g = model.grid
    if model.listed:
        return (f"BedrockForge block model (list of blocks): corner {list(g.corner)}, cell {list(g.size or ())}, "
                f"rotation {g.rotation}")
    size = list(g.size) if g.size is not None else "variable"
    return (f"BedrockForge block model: corner {list(g.corner)}, counts {list(g.counts)}, size {size}, "
            f"rotation {g.rotation}, div {list(g.sub.div)}")  # fmt: skip


def request(conn: sa.Connection, folder: Path, model_id: int, body: Mapping[str, Any]) -> dict[str, Any]:
    """The checked parameters of the job `blocks.export` (the synchronous part of `POST /blocks/{id}/export`): 404
    (model, field, filter), 409 `files_exist {files}`, 422 `bad_param {param}`, `bad_path`, `omf_subblocks`,
    `omf_not_exportable`, `filter_field_missing`."""
    from bf_core.exporting import omf_job

    spec = parse(model_id, body)
    target = check_target(spec, folder)
    model = Model.open(conn, BlobStore(folder), model_id, ANY)
    fields = [str(f["name"]) for f in selected(model, spec.fields)]
    check_filter_fields(model, calc_job.rules_tree(conn, spec.filter_id, spec.restrict))
    if spec.format == "omf":
        if model.listed:
            raise list_unsupported(model.name, "Экспорт OMF")
        if model.sub:
            raise omf_job.subblocks_error(model.name)
        if _grid_cells(model) > omf_job.MAX_CELLS:
            raise ApiError(422, "omf_not_exportable", f"«{model.name}»: " + omf_job.TOO_LARGE["message"],
                           omf_job.TOO_LARGE["hint"])  # fmt: skip
    return {"model_id": int(model_id), "format": spec.format, "path": spec.path, "fields": fields,
            "filter_id": spec.filter_id, "restrict": spec.restrict, "header": spec.header,
            "encoding": spec.encoding, "delimiter": spec.delimiter, "decimal": spec.decimal, "empty": spec.empty,
            "overwrite": spec.overwrite, "rows": model.rows, "grid_cells": _grid_cells(model),
            "target": str(target)}  # fmt: skip


# --- the portions -----------------------------------------------------------------------------------------------------


def passes(
    model: Model, r: restrict.Restrictor, tree: Any, names: Sequence[str], chunk: int, cancel: Cancel
) -> Iterator[tuple[Chunk, np.ndarray | None]]:
    """(portion, mask of the blocks that pass); None — all pass."""
    if tree is None:
        for ch in model.iter(names, chunk):
            cancel()
            yield ch, None
        return
    for ch, f in r.iter(model, extra=names, chunk=chunk, cancel=cancel):
        yield ch, f > 0.0


def batch_of(model: Model, specs: Sequence[Mapping[str, Any]], ch: Chunk, mask: np.ndarray | None,
             empty: float | None, clash: dict[str, int]) -> Any:  # fmt: skip
    """The rows of a portion that pass as a `Batch` (None — none of them): the centres in the axes of the project, the
    sizes along the axes of the model, the fields; `empty` replaces an empty number (NaN) by the code, a real value
    equal to the code is counted in `clash`."""
    from bf_core.exporting.blocks_writers import Batch

    if mask is not None:
        kept = int(np.count_nonzero(mask))
        if kept == 0:
            return None
        if kept == ch.n:
            mask = None

    def pick(a: np.ndarray | None) -> np.ndarray | None:
        return a if a is None or mask is None else a[mask]

    p, s, w = pick(ch.p), pick(ch.s), pick(ch.w)
    assert p is not None
    if ch.lc is not None and ch.ld is not None:
        geo = np.concatenate([model.grid.to_project(pick(ch.lc)), pick(ch.ld)], axis=1)
    else:
        geo = np.concatenate([geometry.centres(model.grid, p, s, w), geometry.row_widths(model.grid, p, s, w)], axis=1)
    cols: list[np.ndarray] = []
    for f in specs:
        values = pick(ch.cols[str(f["name"])])
        assert values is not None
        if empty is not None and f.get("kind") != bs.CATEGORY:
            taken = int(np.count_nonzero(values == empty))
            if taken:
                clash[str(f["name"])] = clash.get(str(f["name"]), 0) + taken
            values = np.where(np.isnan(values), empty, values)
        cols.append(values)
    return Batch(int(len(p)), geo, cols, p, s, w)


def _code_taken(clash: Mapping[str, int], empty: float) -> ApiError:
    name, count = next(iter(clash.items()))
    code = f"{empty:g}"
    return ApiError(422, "empty_code_taken", f"В поле «{name}» {_n(count)} значений равны коду пустого {code}: "
                    "после экспорта их нельзя отличить от пропусков.",
                    "Выберите другой код пустого или оставьте пропуски пустыми.", {"field": name})  # fmt: skip


# --- the run ----------------------------------------------------------------------------------------------------------


def run_export(engine: sa.Engine, folder: Any, params: Mapping[str, Any], *, tick: Tick = _noop,
               chunk: int = ROWS, registry: bool = False) -> dict[str, Any]:  # fmt: skip
    """The body of `blocks.export` (the handler, the writers of Д-17 and the tests). `params` — the body of the request
    and `model_id`; the checks of `request` are made again (the file may have appeared meanwhile). `registry` — the
    call of «Экспорт проекта»: a folder inside the project is allowed there, as for the other types."""
    seen = [0.0]

    def go(progress: float | None = None, message: str | None = None) -> None:
        """Progress; with no number — only the check of a cancel (`tick` raises `JobCancelled`)."""
        seen[0] = seen[0] if progress is None else progress
        tick(seen[0], message)

    spec = parse(int(params["model_id"]), params)
    folder = Path(folder)
    target = check_target(spec, folder, in_project=registry)
    store = BlobStore(folder)
    go(0.0, "Подготовка экспорта")
    with dialect.read_tx(engine) as conn:
        model = Model.open(conn, store, spec.model_id, ANY)
        tree = calc_job.rules_tree(conn, spec.filter_id, spec.restrict)
        check_filter_fields(model, tree)
        r = restrict.prepare(model, tree, conn=conn, store=store)
        omf_head = _omf_head(conn) if spec.format == "omf" else None
    if not model.rows:
        raise empty_result()
    specs = selected(model, spec.fields)
    t0 = time.perf_counter()

    cancel = go

    if spec.format == "omf":
        assert omf_head is not None
        done = _run_omf(model, specs, r, tree, target, omf_head, go, cancel)
    else:
        columns, warnings = columns_of(model, specs, spec.format)
        sink = _sink(model, columns, spec, target)
        done = {"warnings": [*warnings, *sink.warnings]}
        clash: dict[str, int] = {}
        rows = 0
        try:
            names = [str(f["name"]) for f in specs]
            for ch, mask in passes(model, r, tree, names, chunk, cancel):
                go(0.02 + 0.96 * (ch.start + ch.n) / model.rows,
                     f"Запись: {_n(ch.start + ch.n)} из {_n(model.rows)}")  # fmt: skip
                batch = batch_of(model, specs, ch, mask, spec.empty if spec.format != "parquet" else None, clash)
                if clash and spec.empty is not None:
                    raise _code_taken(clash, spec.empty)  # at the first portion with a clash, not after the file
                if batch is not None:
                    sink.write(batch)
                    rows += batch.n
            if not rows:
                raise empty_result()
            done["bytes"] = sink.commit()
        except BaseException:
            sink.abort()
            raise
        done["rows"] = rows
    go(1.0, "Готово")
    return {"path": str(target), "format": spec.format, "rows": done["rows"], "total": model.rows,
            "bytes": done["bytes"], "seconds": round(time.perf_counter() - t0, 3),
            "warnings": list(dict.fromkeys([*done["warnings"], *r.warnings]))}  # fmt: skip


def _sink(model: Model, columns: list[Any], spec: Spec, target: Path) -> Any:
    from bf_core.exporting import blocks_writers as bw

    if spec.format == "dat":
        return bw.DatSink(target, columns, spec.encoding, title=model.name, note=grid_note(model))
    if spec.format == "csv":
        line, why = grid_line(model) if spec.header == "micromine" else (None, None)
        sink = bw.CsvSink(target, columns, delimiter=spec.delimiter, decimal=spec.decimal, encoding=spec.encoding,
                          header=spec.header, grid_line=line)  # fmt: skip
        if why:
            sink.warnings.append(why)
        return sink
    return bw.ParquetSink(target, columns, grid=model.grid.to_meta(), sub=model.sub)


# --- OMF --------------------------------------------------------------------------------------------------------------


def _omf_head(conn: sa.Connection) -> tuple[str, dict[str, Any] | None]:
    from bf_core.exporting import omf_job

    return omf_job._project(conn)


def _run_omf(model: Model, specs: Sequence[Mapping[str, Any]], r: restrict.Restrictor, tree: Any, target: Path,
             head: tuple[str, dict[str, Any] | None], tick: Tick, cancel: Cancel) -> dict[str, Any]:  # fmt: skip
    """The model as an OMF volume through `OmfWriter.add_volume`: the fields asked for, the blocks that passed (the
    others are empty cells). The mask of a filter is made once (a byte a row) and shared by the fields."""
    from bf_core.exporting import omf_job
    from bf_core.formats.omf_write import OmfWriter

    model.require_grid("Экспорт OMF")
    if model.sub:
        raise omf_job.subblocks_error(model.name)
    if _grid_cells(model) > omf_job.MAX_CELLS:
        raise ApiError(422, "omf_not_exportable", f"«{model.name}»: " + omf_job.TOO_LARGE["message"],
                       omf_job.TOO_LARGE["hint"])  # fmt: skip
    mask: np.ndarray | None = None
    passed = model.rows
    if tree is not None:
        mask = np.zeros(model.rows, dtype=bool)
        for ch, f in r.iter(model, cancel=cancel):
            mask[ch.start : ch.start + ch.n] = f > 0.0
            tick(0.02 + 0.08 * (ch.start + ch.n) / model.rows, "Отбор блоков")
        passed = int(np.count_nonzero(mask))
        if not passed:
            raise empty_result()
    total = max(8 * _grid_cells(model) * max(len(specs), 1), 1)

    def cells(name: str) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        for ch in model.iter([name], 1 << 20):
            cancel()
            keep = None if mask is None else mask[ch.start : ch.start + ch.n]
            if keep is not None and not keep.any():
                continue
            yield (ch.p, ch.cols[name]) if keep is None else (ch.p[keep], ch.cols[name][keep])

    def report(written: int) -> None:
        tick(0.1 + 0.88 * min(written / total, 1.0), f"Запись OMF: {_n(written >> 20)} МБ")

    name, crs = head
    with OmfWriter(target, name, "", crs, tick=report) as w:
        w.add_volume(model.name, model.grid.to_meta(), [dict(s) for s in specs], cells)
    return {"rows": passed, "bytes": target.stat().st_size, "warnings": list(w.warnings)}


# --- the job ----------------------------------------------------------------------------------------------------------


def memory_mb(params: dict[str, Any]) -> int:
    """A portion of 2¹⁸ rows with the buffers of the sink, a row group of the model; OMF — the grid, densely."""
    if params.get("format") == "omf":
        return max(640, 256 + int(24 * int(params.get("grid_cells") or 0) / 2**20))
    return 640


@handler(KIND, TITLE, queue="heavy", estimate_memory=memory_mb)
def export_job(ctx: Any) -> dict[str, Any]:
    from bf_core.formats.errors import FormatError
    from bf_core.jobs.context import JobError

    assert ctx.project_folder is not None
    try:
        out = run_export(ctx.engine(), ctx.project_folder, ctx.params, tick=ctx.tick)
    except (ApiError, FormatError) as exc:
        raise JobError(exc.code, exc.message, exc.hint, retry=False) from exc
    except MemoryError as exc:
        raise JobError("out_of_memory", "Не хватило памяти на экспорт блочной модели.",
                       "Закройте другие программы или экспортируйте поля по частям.") from exc  # fmt: skip
    except OSError as exc:
        raise JobError("file_unwritable", f"Файл не записан: {exc.strerror or exc}.",
                       "Выберите другую папку или закройте файл в другой программе.") from exc  # fmt: skip
    for text in out["warnings"]:
        ctx.log(text, "warn")
    ctx.log(f"Записан файл «{Path(out['path']).name}»: блоков {_n(out['rows'])} из {_n(out['total'])}")
    _log(ctx, out)
    return out


def _log(ctx: Any, out: Mapping[str, Any]) -> None:
    from bf_core.db.schema import export_log, objects

    p = ctx.params
    with dialect.write_tx(ctx.engine()) as conn:
        alive = conn.execute(sa.select(objects.c.id).where(objects.c.id == int(p["model_id"]))).scalar()
        conn.execute(sa.insert(export_log).values(
            object_id=alive, job_id=ctx.id, file_name=Path(out["path"]).name, format=str(out["format"]),
            settings={k: p.get(k) for k in ("fields", "filter_id", "restrict", "header", "encoding", "delimiter",
                                            "decimal", "empty")},
            counts={"rows": out["rows"], "total": out["total"], "bytes": out["bytes"],
                    "warnings": len(out["warnings"])},
            created_at=dialect.utcnow()))  # fmt: skip
