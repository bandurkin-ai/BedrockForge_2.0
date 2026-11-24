"""Export to OMF v1 (ТЗ 7.24; ADR 0017 §4): wireframes and DTMs, line files, regular block models of the project
into one `.omf` that Leapfrog and the import of BF2 read («экспорт → импорт» gives the same objects).

    exportable(conn)       what the window «Экспорт OMF» offers: three lists with `available` and `reason`
    request(conn, …)       checked parameters of the job (404, 422, 409 `files_exist`) — for `POST /omf/export`
    write_objects(path, …) the file itself (also «Экспорт объектов», `exporting/writers.py`)
    export.omf             the job: `write_objects` → `export_log` → {path, counts, warnings}

What goes where (`formats/omf_write.py`): a wireframe — a surface (vertices bit for bit, triangles in the order of
its file, colour, «Описание»; a DTM is marked «BedrockForge: ЦТМ»), a line file — a line set per layer (closed
lines as loops, attributes of the lines on their segments; layers of points and lines with a vertex without Z
are not written — warnings), a block model — a volume (`axis_u/v/w` from its turn about Z, the steps as
tensors, every field densely: an empty cell is NaN or −1; fields of the files `fld:` too — `read_field`). Not
offered: point clouds; block models over `MAX_CELLS` cells; block models with sub-blocks — 422 `omf_subblocks`
«только после регуляризации» (OMF keeps none). The project:
its name and the line «Система координат: ‹имя› (EPSG:…)» of its CRS.

The file is written as «‹имя›.omf.part» and renamed when complete: a cancel or a failure leaves nothing. A file of
that name the save window did not ask about is a 409 `files_exist` (the window asks «Заменить?» and repeats with
`overwrite: true`); the job checks once more before writing. One row of `export_log`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import export_log, line_layers, objects, project_meta, wireframes
from bf_core.formats.omf_write import subblocks_message
from bf_core.jobs.context import JobContext, JobError
from bf_core.jobs.registry import handler

FORMAT = "omf"
SUFFIX = ".omf"
MAX_CELLS = 100_000_000  # ADR 0017 решение 14: a larger model is not offered
MAX_OBJECTS = 5000
KINDS = ("wireframe_ids", "line_file_ids", "block_model_ids")
Progress = Callable[..., None]


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def _reason(message: str, hint: str) -> dict[str, str]:
    return {"message": message, "hint": hint}


TOO_LARGE = _reason(f"Модель больше {_n(MAX_CELLS)} ячеек: в OMF не записывается.", "Разделите модель на части.")
SUBBLOCKS = "omf_subblocks"
CLOUD = _reason("Облако точек в OMF не записывается (точки OMF не переносятся).", "Экспортируйте облако в LAS.")


# --- what can be exported ---------------------------------------------------------------------------------


def exportable(conn: sa.Connection) -> dict[str, list[dict[str, Any]]]:
    """{wireframes: [{id, name, folder, dtm, triangles, available, reason}], line_files: [{id, name, folder, layers,
    points, available, reason}], block_models: [{id, name, folder, cells, available, reason}]} in the order of the
    tree; `reason` {message, hint} — why one is not available."""
    from bf_core.blocks import store as bm

    wf = conn.execute(
        sa.select(objects.c.id, objects.c.name, objects.c.folder, wireframes.c.triangle_count, wireframes.c.dtm)
        .join(wireframes, wireframes.c.object_id == objects.c.id)
        .where(objects.c.kind == "wireframe", objects.c.state == "ready")
        .order_by(objects.c.sort_key, objects.c.name_key, objects.c.id)
    ).all()
    files = conn.execute(
        sa.select(objects.c.id, objects.c.name, objects.c.folder)
        .where(objects.c.kind == "line_file", objects.c.state == "ready")
        .order_by(objects.c.sort_key, objects.c.name_key, objects.c.id)
    ).all()
    layers: dict[int, list[Any]] = {}
    if files:
        for lr in conn.execute(
            sa.select(objects.c.parent_id, objects.c.meta, line_layers.c.point_count)
            .join(line_layers, line_layers.c.object_id == objects.c.id)
            .where(objects.c.parent_id.in_([int(f.id) for f in files]), objects.c.state == "ready")
        ):  # fmt: skip
            layers.setdefault(int(lr.parent_id), []).append(lr)
    out_files = []
    for f in files:
        own = layers.get(int(f.id), [])
        cloud = any(isinstance((lr.meta or {}).get("cloud"), dict) for lr in own)
        points = sum(int(lr.point_count) for lr in own)
        out_files.append({"id": int(f.id), "name": f.name, "folder": f.folder, "layers": len(own), "points": points,
                          "available": not cloud, "reason": CLOUD if cloud else None})  # fmt: skip
    models = []
    for b in bm.list_block_models(conn):
        big = int(b["cells"]) > MAX_CELLS or _grid_cells(b["grid"]) > MAX_CELLS
        sub = bool(b.get("sub"))
        why = _reason(*subblocks_message(str(b["name"]))) if sub else TOO_LARGE if big else None
        models.append({"id": b["id"], "name": b["name"], "folder": b["folder"], "cells": b["cells"],
                       "available": why is None, "reason": why, "code": SUBBLOCKS if sub else None})  # fmt: skip
    return {
        "wireframes": [
            {
                "id": int(w.id),
                "name": w.name,
                "folder": w.folder,
                "dtm": w.dtm is not None,
                "triangles": int(w.triangle_count),
                "available": True,
                "reason": None,
            }
            for w in wf
        ],  # fmt: skip
        "line_files": out_files,
        "block_models": models,
    }


def subblocks_error(name: str) -> ApiError:
    """422 `omf_subblocks`: a model with sub-blocks goes to OMF only after the regularization (`blocks.export` too)."""
    return ApiError(422, SUBBLOCKS, *subblocks_message(name))


def _grid_cells(grid: Any) -> int:
    counts = (grid or {}).get("counts") or [0, 0, 0]
    return int(counts[0]) * int(counts[1]) * int(counts[2])


def target_path(path: str | Path) -> Path:
    """The file the export writes: the chosen name, with `.omf` after it when it has another extension."""
    p = Path(path)
    return p if p.suffix.lower() == SUFFIX else p.with_name(p.name + SUFFIX)


def request(
    conn: sa.Connection,
    *,
    path: str,
    wireframe_ids: Sequence[int] = (),
    line_file_ids: Sequence[int] = (),
    block_model_ids: Sequence[int] = (),
    overwrite: bool = False,
) -> dict[str, Any]:
    """Checked parameters of the job `export.omf`; ApiError otherwise (see the module docstring)."""
    from bf_core.lines.export import check_path
    from bf_core.rasters.export import existing, files_exist

    check_path(path)
    given = {
        "wireframe_ids": list(dict.fromkeys(int(i) for i in wireframe_ids)),
        "line_file_ids": list(dict.fromkeys(int(i) for i in line_file_ids)),
        "block_model_ids": list(dict.fromkeys(int(i) for i in block_model_ids)),
    }
    total = sum(len(v) for v in given.values())
    if not total:
        raise ApiError(422, "nothing_to_export", "Для экспорта в OMF ничего не выбрано.",
                       "Отметьте каркасы, файлы линий или блочные модели.")  # fmt: skip
    if total > MAX_OBJECTS:
        raise ApiError(422, "too_many_objects", f"Объектов для экспорта больше {_n(MAX_OBJECTS)}.",
                       "Экспортируйте их несколькими файлами.")  # fmt: skip
    offered = exportable(conn)
    lists = {"wireframe_ids": offered["wireframes"], "line_file_ids": offered["line_files"],
             "block_model_ids": offered["block_models"]}  # fmt: skip
    weight = 0
    for key, ids in given.items():
        known = {int(o["id"]): o for o in lists[key]}
        for i in ids:
            found = known.get(i)
            if found is None:
                raise ApiError(404, "object_not_found", "Объект для экспорта не найден.",
                               "Обновите дерево проекта и выберите объекты заново.")  # fmt: skip
            if not found["available"]:
                why = found["reason"]
                if found.get("code") == SUBBLOCKS:  # the text already names the model
                    raise ApiError(422, SUBBLOCKS, why["message"], why["hint"])
                raise ApiError(422, "omf_not_exportable", f"«{found['name']}»: {why['message']}", why["hint"])
            weight = max(weight, _weight(key, found))
    target = target_path(path)
    if not overwrite:
        clash = existing([target], Path(path))
        if clash:
            err = files_exist(clash)
            raise ApiError(409, err.code, err.message, err.hint, extra={"files": [p.name for p in clash]})
    return {"path": path, **given, "overwrite": bool(overwrite), "largest": weight}


def _weight(key: str, found: dict[str, Any]) -> int:
    """Bytes of the largest object in memory while it is written."""
    if key == "wireframe_ids":
        return 60 * int(found["triangles"])  # vertices and triangles as <f8 / <i8 and their copies
    if key == "line_file_ids":
        return 400 * int(found["points"])
    return 4_000_000 * 24  # a portion of a dense field and its compressed copy


def _memory(params: dict[str, Any]) -> int:
    return max(512, 256 + int(int(params.get("largest") or 0) / 2**20))


# --- writing ----------------------------------------------------------------------------------------------


def _project(conn: sa.Connection) -> tuple[str, dict[str, Any] | None]:
    """The name of the project and its CRS {name, epsg} for the description of the file."""
    row = conn.execute(sa.select(project_meta.c.name, project_meta.c.crs)).first()
    if row is None:
        return "BedrockForge", None
    crs = None
    if row.crs:
        from bf_core.crs import CrsError, describe

        try:
            info = describe(str(row.crs))
            crs = {"name": info.name, "epsg": info.epsg}
        except CrsError:
            crs = None
    return str(row.name), crs


def write_objects(
    path: str | Path,
    *,
    wireframe_ids: Sequence[int] = (),
    line_file_ids: Sequence[int] = (),
    block_model_ids: Sequence[int] = (),
    engine: sa.Engine,
    store: Any,
    progress: Progress | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Writes the objects into the OMF file `path` (through `path.part`). Returns the counts {wireframes,
    line_layers, block_models, cells, size, files} and the warnings. Objects are read one at a time in short read
    transactions; `progress(fraction, message)` may raise to cancel (nothing is left then). Raises ApiError (an
    object went away), FormatError, OSError."""
    from bf_core.blocks import meta as bmeta
    from bf_core.blocks import schema as bs
    from bf_core.blocks import store as bm
    from bf_core.formats.omf_write import OmfWriter
    from bf_core.lines import store as ls
    from bf_core.wireframes import source as src
    from bf_core.wireframes import store as ws

    tick = progress or (lambda *_a: None)
    path = Path(path)
    with dialect.read_tx(engine) as conn:
        name, crs = _project(conn)
        sizes = _sizes(conn, wireframe_ids, line_file_ids, block_model_ids)
    total = max(sum(sizes), 1)
    counts: dict[str, Any] = {"wireframes": 0, "line_layers": 0, "block_models": 0, "cells": 0}
    label = [""]

    def report(written: int) -> None:
        tick(min(written / total, 0.99), label[0])

    with OmfWriter(path, name, "", crs, tick=report) as w:
        for wf_id in wireframe_ids:
            with dialect.read_tx(engine) as conn:
                mesh = src.read_mesh(conn, store, int(wf_id))
                dtm = ws.wireframe_info(conn, int(wf_id))["dtm"] is not None
            label[0] = f"Каркас «{mesh.name}»"
            w.add_mesh(mesh, dtm=dtm)
            counts["wireframes"] += 1
            del mesh
        for file_id in line_file_ids:
            with dialect.read_tx(engine) as conn:
                ids = ls.layer_ids(conn, int(file_id))
            for layer_id in ids:
                with dialect.read_tx(engine) as conn:
                    layer = ls.read_layer(conn, store, layer_id)
                label[0] = f"Слой «{layer.name}»"
                before = w.element_count
                w.add_lines(layer)  # what is not written stays in `w.warnings`
                counts["line_layers"] += w.element_count - before
        for bm_id in block_model_ids:
            with dialect.read_tx(engine) as conn:
                info = bm.block_model_info(conn, int(bm_id))
                specs = {f["name"]: bmeta.check_field_v2(f) for f in info["fields"]}  # v1 fields too
                shas = {role: bm.cells_sha(conn, int(bm_id), role) for role in {f["file"] for f in specs.values()}}
                shas[bs.ROLE] = bm.cells_sha(conn, int(bm_id))
            label[0] = f"Блочная модель «{info['name']}»"
            if info.get("sub"):
                raise subblocks_error(str(info["name"]))

            def cells(field: str, shas: dict[str, str] = shas, specs: dict[str, Any] = specs) -> Iterator[Any]:
                return read_field(store, shas, specs[field])

            w.add_volume(info["name"], info["grid"], list(specs.values()), cells, sub=bool(info.get("sub")))
            counts["block_models"] += 1
            counts["cells"] += int(info["cells"])
        if not w.element_count:
            from bf_core.formats.errors import FormatError

            raise FormatError("nothing_written", "В OMF нечего записать: у выбранных объектов нет данных для OMF.",
                              "Выберите каркасы, линии с отметками Z или блочные модели.")  # fmt: skip
    counts["size"] = path.stat().st_size
    counts["files"] = [path.name]
    return counts, list(w.warnings)


def read_field(store: Any, shas: dict[str, str], spec: dict[str, Any]) -> Iterator[tuple[Any, Any]]:
    """(idx, values) by row groups of one field of a model (v1, or v2 without sub-blocks: P = idx): from the file
    `cells`, or from its file `fld:` whose row groups are those of `cells` (`blocks/fields_store.py`)."""
    from bf_core.blocks import schema as bs
    from bf_core.blocks import store as bm
    from bf_core.blocks.fields_store import iter_fields

    name, role = spec["name"], spec.get("file") or bs.ROLE
    if role == bs.ROLE:
        for idx, got in bm.iter_cells(store, shas[bs.ROLE], [name], specs={name: spec}):
            yield idx, got[name]
        return
    heads = bm.iter_cells(store, shas[bs.ROLE], [])
    for (idx, _), got in zip(heads, iter_fields(store, shas[role], [spec]), strict=True):
        yield idx, got[name]


def _sizes(conn: sa.Connection, wf: Sequence[int], files: Sequence[int], models: Sequence[int]) -> list[int]:
    """Uncompressed bytes the writer reports for every object (the shares of the progress)."""
    from bf_core.blocks import store as bm

    out: list[int] = []
    if wf:
        out += [
            24 * int(r.point_count) + 24 * int(r.triangle_count)
            for r in conn.execute(sa.select(wireframes.c.point_count, wireframes.c.triangle_count)
                                  .where(wireframes.c.object_id.in_(list(wf))))
        ]  # fmt: skip
    if files:
        points = conn.execute(
            sa.select(sa.func.coalesce(sa.func.sum(line_layers.c.point_count), 0))
            .join(objects, objects.c.id == line_layers.c.object_id)
            .where(objects.c.parent_id.in_(list(files)))
        ).scalar_one()
        out.append(40 * int(points))
    for bm_id in models:
        info = bm.block_model_info(conn, int(bm_id))
        out.append(8 * _grid_cells(info["grid"]) * max(len(info["fields"]), 1))
    return out


# --- the job ----------------------------------------------------------------------------------------------


@handler("export.omf", "Экспорт OMF", queue="heavy", estimate_memory=_memory)
def export_omf(ctx: JobContext) -> dict[str, Any]:
    from bf_core.formats.errors import FormatError
    from bf_core.lines.export import late_tick
    from bf_core.rasters.export import existing, files_exist
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes.export import CLOCK_S, _retry_since

    p = ctx.params
    if ctx.project_folder is None:
        raise RuntimeError("export.omf is a project job")
    chosen = Path(p["path"])
    target = target_path(chosen)
    ids = {k: [int(i) for i in p.get(k) or []] for k in KINDS}
    engine = ctx.engine()
    ctx.tick(0.0, "Подготовка экспорта OMF")
    if not bool(p.get("overwrite")):
        since = _retry_since(p.get("retry_of"))
        clash = [f for f in existing([target], chosen) if since is None or f.lstat().st_mtime < since - CLOCK_S]
        if clash:
            err = files_exist(clash)
            raise JobError(err.code, err.message, err.hint)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        counts, warnings = write_objects(target, **ids, engine=engine, store=BlobStore(ctx.project_folder),
                                         progress=lambda f, m=None: ctx.tick(max(0.0, f), m))  # fmt: skip
    except (FormatError, ApiError) as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    except MemoryError as exc:
        raise JobError("out_of_memory", "Не хватило памяти на экспорт OMF.",
                       "Закройте другие программы или экспортируйте объекты несколькими файлами.") from exc  # fmt: skip
    except OSError as exc:
        raise JobError("file_unwritable", f"Файл не записан: {exc.strerror or exc}.",
                       "Выберите другую папку или закройте файл в другой программе.") from exc  # fmt: skip
    for text in warnings:
        ctx.log(text, "warn")
    ctx.log(
        f"Записан файл «{target.name}» (OMF v1): каркасов {_n(counts['wireframes'])}, слоёв линий "
        f"{_n(counts['line_layers'])}, блочных моделей {_n(counts['block_models'])} ({_n(counts['cells'])} ячеек)"
    )
    every = [i for k in KINDS for i in ids[k]]
    with dialect.write_tx(engine) as conn:
        alive = conn.execute(sa.select(objects.c.id).where(objects.c.id.in_(every[:1]))).scalar()
        conn.execute(
            sa.insert(export_log).values(
                object_id=alive,
                job_id=ctx.id,
                file_name=target.name,
                format=FORMAT,
                settings={**ids, "files": counts["files"]},
                counts={**{k: v for k, v in counts.items() if k != "files"}, "warnings": len(warnings)},
                created_at=dialect.utcnow(),
            )  # fmt: skip
        )
    late_tick(ctx, 1.0, "Готово")
    return {"path": str(target), "format": FORMAT, **counts, "warnings": warnings}
