"""Job `import.objects` of the wizard «Импорт файлов» (ТЗ 7.8, 7.18, 7.19, 7.21; ADR 0011 §1, §3, §5; ADR 0013
§3, §5): lines, points, rasters and wireframes. The pairing of the chosen files, their reading and the
composition the wizard shows (`inspect_files`) are in `object_files.py`; the wireframes — in `object_meshes.py`.

Parameters (`ctx.params`): `files` [paths], `options` {path: {encoding, x, y, z, name, split_by, layer_field, cloud,
every, class_column}},
`raster_z` {elevation, shift} — «Уровень растров по Z», `section` {x1, y1, x2, y2, z_top, z_bottom} — where an
image without a georeference stands (the current section line, resolved by the endpoint), `folder` of the tree
and `oda` — the ODA File Converter of «Настройка», for DWG.

File by file: read, stage the blobs, then a short transaction that writes the objects as `building` (a line file
with its layers; a raster with its display copies; every wireframe of a file as an object of its own, taken
from the reader one at a time; a DXF or a DWG gives a line file and wireframes; a LAS, or a table asked for as a
cloud, gives a line file with one layer — the cloud, built in portions by `object_cloud.py`). When every file is in, the
source files are staged and one final transaction attaches them (`source`, `sidecar:<ext>`: to the line file or
the raster, and to the `import_log` row of every wireframe), writes `import_log` and makes everything `ready`.
Until then a cancel or a failure leaves nothing: the scheduler drops what the job was building
(`jobs.store.drop_partial`), the temporary files go here. A file that cannot be read is an error line of the
journal and an entry of `failed`; the job fails only when nothing was loaded. A retry after the final
transaction finds the import done by `import_log.job_id`. The source goes into the storage straight from its
place on the disk: no intermediate copy.

Result: `files` — an entry per file and kind of its objects: {file, format, kind, object_id, layers, lines,
points, vertices | rasters, width, height, … | kind "wireframes", object_ids, wireframes, triangles, mesh_points,
skipped | kind "cloud", cloud_id, points, parts, skipped, every, classes}; `objects` [ids of the line files, rasters
and wireframes], `wireframe_ids` (for `wireframe.check`), `cloud_ids` (the layers that are clouds),
`failed` and `rest` [{file, code, message, hint}], totals.

Ported from BF `importing/objects.py` (`import_lines`, `import_rasters`, the palette of layers without a colour).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import import_log, objects
from bf_core.formats.errors import FormatError
from bf_core.jobs.context import JobContext, JobError
from bf_core.jobs.registry import handler

from . import object_blockfile as obf
from . import object_blocks as ob
from . import object_cloud as oc
from . import object_meshes as om
from .object_files import (
    EMPTY,
    FILE_SETTINGS,
    MAX_NOTES,
    NUMBER_HINT,
    Group,
    Tick,
    _failure,
    _fields,
    _raster_summary,
    _size,
    has_content,
    inspect_files,
    n_text,
    option_key,
    options_by_file,
    parse_level,
    plan,
    read_group,
    summarize,
)

__all__ = ["import_objects", "inspect_files", "plan"]

#: Distinct colours for layers that carry none in the file (BF `PALETTE`).
PALETTE = ("#e8873a", "#4f9dde", "#5fbf6a", "#c06fd8", "#e0c040", "#e05d6f", "#46c7c0", "#9a8f7a")
TARGET = "objects"  # `import_log.target`
PORTION = 25  # layers written by one transaction (about 0.1 s)
LINE_TOTALS = ("layers", "lines", "points", "vertices", "rasters")  # `import_log.counts` of a line file
#: Memory a file needs, by its extension, in sizes of the file: a text file of lines or meshes — its arrays,
#: attributes and the staging (about 6×). A binary file of wireframes is little more than its geometry, and
#: staging a wireframe keeps the mesh, its spatial order and the table of its edges at once: measured 10× the
#: geometry of a wireframe of 620 000 triangles (a TRIDB of 1 million triangles, 23 MB: 150 MB over the start).
MEMORY_FACTOR = {".tridb": 12, ".stl": 12, ".dm": 12}


# --- the job -----------------------------------------------------------------------------------------------


def _memory(params: dict[str, Any]) -> int:
    """Files go one at a time: the biggest of them. Lines — the arrays, their attributes and the staging (about
    6× the file); an image — its decoded pixels and the copies (the size comes from the header)."""
    from bf_core.formats.registry import IMAGE_SUFFIXES

    need = 0.0
    chosen = options_by_file(params.get("options"))
    for raw in params.get("files") or []:
        path = Path(str(raw))
        size = _size(path)
        if path.suffix.lower() in (".las", ".laz") or (chosen.get(option_key(path)) or {}).get("cloud"):
            size = oc.MEMORY_MB * 2**20  # a cloud is built in portions: the same memory whatever its size
        elif path.suffix.lower() == ".omf":
            size = ob.memory(path, size)
        elif obf.is_blockfile(path, chosen.get(option_key(path))):
            size = obf.memory(path, size) * 2**20
        elif path.suffix.lower() in IMAGE_SUFFIXES:
            try:
                from bf_core.formats.raster_image import probe

                info = probe(path)
                size = info.width * info.height * max(info.bands, 3) * max(info.bits, 8) // 8 * 3 // 2
            except Exception:  # noqa: BLE001 - an estimate: the job itself reports a bad image
                size *= 10
        else:
            size *= MEMORY_FACTOR.get(path.suffix.lower(), 6)
        need = max(need, size / 2**20)
    return max(512, int(256 + need))


@dataclass
class _Done:
    """A file whose objects are written as `building`."""

    path: Path
    label: str
    format: str
    media: str
    settings: dict[str, Any]
    object_id: int | None = None  # the line file or the raster; None — the file gave wireframes only
    kind: str = ""  # "lines" | "points" | "raster" of that object
    counts: dict[str, Any] = field(default_factory=dict)
    meshes: om.Written | None = None  # the wireframes of the file
    blocks: ob.Blocks | None = None  # the block models of an OMF file
    companions: dict[str, Path] = field(default_factory=dict)  # suffix → the file kept as `sidecar:<ext>`
    source: tuple[Path, str, int] | None = None
    sidecars: dict[str, tuple[Path, str, int]] = field(default_factory=dict)


def free_name(conn: sa.Connection, kind: str, name: str) -> str:
    """`name`, or «name (2)», «name (3)»… when an object of this kind has it already."""
    name = " ".join(name.split())[:190] or "Без имени"
    same = sa.and_(objects.c.kind == kind, objects.c.state.in_(("ready", "building")))
    taken = {str(k) for k in conn.execute(sa.select(objects.c.name_key).where(same)).scalars()}
    out, n = name, 1
    while dialect.name_key(out) in taken:
        n += 1
        out = f"{name} ({n})"
    return out


def _file_meta(g: Group, read: Any, summary: Mapping[str, Any]) -> dict[str, Any]:
    crs = summary["crs"] or {}
    return {
        "format": read.format, "label": read.label, "source_format": getattr(read.detail, "format", read.format),
        "filename": g.main.name, "size": _size(g.main), "encoding": summary["encoding"],
        "fields": _fields(read.detail), "crs_name": crs.get("name"), "epsg": crs.get("epsg"),
        "sidecars": summary["sidecars"], "warnings": summary["warnings"], "left_out": summary["left_out"],
    }  # fmt: skip


def _settings(g: Group, options: Mapping[str, Any] | None, summary: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """`import_log.mapping` of a file: how it was read."""
    options = options or {}
    out: dict[str, Any] = {"encoding": (summary or {}).get("encoding") or options.get("encoding") or None}
    out["sidecars"] = (summary or {}).get("sidecars") or sorted(p.name for p in g.sidecars.values())
    out.update({k: options[k] for k in FILE_SETTINGS if options.get(k)})
    return out


def _write_lines(ctx: JobContext, engine: sa.Engine, store: Any, g: Group, read: Any, tick: Tick) -> _Done:
    """The file object and its layers as `building`. Layers go in portions — staged, then written by a short
    transaction — so the job reports and can be cancelled between them, however many layers a file has."""
    from bf_core.db.schema import line_layers
    from bf_core.drillholes.edit import author
    from bf_core.lines import store as ls

    layers = [layer for layer in read.layers if layer.point_count]
    summary = summarize(g, read)
    who = author()
    with dialect.write_tx(engine) as conn:
        used = int(conn.execute(sa.select(sa.func.count()).select_from(line_layers)).scalar_one())
        file_id = ls.create_file(
            conn,
            store,
            name=free_name(conn, ls.LINE_FILE, g.main.name),
            meta=_file_meta(g, read, summary),
            folder=ctx.params.get("folder") or None,
            state="building",
            job_id=ctx.id,
            crs=(summary["crs"] or {}).get("wkt"),
            author=who,
        )
    total = len(layers)
    for i, layer in enumerate(layers):
        if layer.color is None:
            layer.color = PALETTE[(used + i) % len(PALETTE)]
    for a in range(0, total, PORTION):

        def step(k: int, a: int = a) -> None:
            tick((a + k) / total, f"слой {a + k + 1} из {total}")

        # small layers of a portion share one blob (`lines/pack.py`): a file of thousands of layers stays fast
        staged = ls.stage_layers(store, layers[a : a + PORTION], step)
        try:
            with dialect.write_tx(engine) as conn:
                ls.commit_layers(conn, store, staged, file_id=file_id, sort_key=float(a), author=who)
        finally:
            for part in staged:
                part.discard()
    tick(1.0, "слои записаны")
    return _Done(
        g.main,
        g.main.name,
        read.format,
        om.MEDIA,
        {"encoding": summary["encoding"], "sidecars": summary["sidecars"]},
        file_id,
        "lines" if summary["kind"] == "omf" else summary["kind"],  # the line sets of an OMF file
        {k: summary["totals"][k] for k in LINE_TOTALS},
    )


def _write_raster(
    ctx: JobContext, engine: sa.Engine, store: Any, g: Group, read: Any, level: tuple[float | None, float]
) -> _Done:
    from bf_core.drillholes.edit import author
    from bf_core.rasters import store as rs

    r = read.rasters[0]
    summary, geo, notes = _raster_summary(r, *level)
    for note in notes:
        ctx.log(f"{g.main.name}: {note}", "warn" if "не применён" in note or note == rs.SECTION_NOTE else "info")
    meta = {
        "format": r.info.format, "filename": g.main.name, "size": _size(g.main), "mode": r.info.mode,
        "bits": r.info.bits, "sidecars": summary["sidecars"], "warnings": [*r.warnings, *notes][:MAX_NOTES],
    }  # fmt: skip
    displays = rs.stage_displays(store, r.displays)
    try:
        with dialect.write_tx(engine) as conn:
            raster_id = rs.create_raster(
                conn, store, name=free_name(conn, rs.RASTER, r.name), width=r.width, height=r.height, georef=geo,
                checks=r.checks, displays=displays, meta=meta, folder=ctx.params.get("folder") or None,
                state="building", job_id=ctx.id, author=author(),
            )  # fmt: skip
    finally:
        displays.discard()
    counts = {
        "rasters": 1, "width": r.width, "height": r.height, "vertical": summary["vertical"],
        "elevation": summary["elevation"], "georef": summary["georef"]["source"],
    }  # fmt: skip
    return _Done(
        g.main, g.main.name, read.format, rs.IMAGE_MEDIA.get(r.info.format, "image/*"),
        {"sidecars": summary["sidecars"], "raster_z": {"elevation": level[0], "shift": level[1]}},
        raster_id, "raster", counts, companions={suffix: Path(p) for suffix, p in r.sidecars.items()},
    )  # fmt: skip


def _write_cloud(
    ctx: JobContext, engine: sa.Engine, store: Any, g: Group, chosen: Any, tick: Tick, k: int, fail: Any
) -> _Done | None:
    """A LAS, or a table loaded as a cloud: one line file with one layer — the cloud (`object_cloud.py`)."""
    try:
        src = oc.open_source(g, chosen)
        written = oc.write_cloud(ctx, engine, store, g, src, tick, color=PALETTE[k % len(PALETTE)])
    except (FormatError, OSError) as exc:
        fail(exc)
        return None
    for w in written.warnings:
        ctx.log(f"{g.main.name}: {w}", "warn")
    c = written.counts
    ctx.log(
        f"{g.main.name}: облако точек — точек {n_text(c['points'])}, частей {n_text(c['parts'])}, "
        f"классов {len(c['classes'])}" + (f", каждая {c['every']}-я запись" if c["every"] > 1 else ""),
        "info",
    )
    return _Done(g.main, g.main.name, src.format, om.MEDIA, written.settings, written.file_id, "cloud", c)


def _span(step: Callable[..., None], low: float, high: float) -> Tick:
    """The part [low, high] of the share of a file in the progress of the job."""
    return lambda fraction, message: step(fraction, message, low, high)


def _chunks(path: Path, tick: Callable[[int], None]) -> Iterator[bytes]:
    from bf_core.storage.blobs import CHUNK

    with path.open("rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                return
            tick(len(chunk))  # may raise JobCancelled: the temporary file goes
            yield chunk


def _result(files: list[dict[str, Any]], failed: list[dict[str, Any]], rest: list[dict[str, str]]) -> dict[str, Any]:
    ids = [i for f in files for i in (f.get("object_ids") or [f["object_id"]])]
    out: dict[str, Any] = {"files": files, "objects": ids, "failed": failed, "rest": rest}
    for key in ("layers", "lines", "points", "vertices", "rasters", "wireframes", "triangles"):
        out[key] = sum(int(f.get(key) or 0) for f in files)
    for key in ("block_models", "cells"):
        out[key] = sum(int(f.get(key) or 0) for f in files if f.get("kind") == ob.BLOCK_MODELS)
    out["wireframe_ids"] = [i for f in files if f.get("kind") == om.WIREFRAMES for i in f.get("object_ids") or []]
    out["block_model_ids"] = [i for f in files if f.get("kind") == ob.BLOCK_MODELS for i in f["object_ids"]]
    out["cloud_ids"] = [int(f["cloud_id"]) for f in files if f.get("cloud_id") is not None]
    return out


def _committed(engine: sa.Engine, job_id: int) -> list[dict[str, Any]] | None:
    """The files an earlier attempt of this job wrote in its final transaction; None — it did not get there."""
    log = import_log.c
    with dialect.read_tx(engine) as conn:
        rows = conn.execute(
            sa.select(log.object_id, log.file_name, log.format, log.counts, log.sha256)
            .where(log.job_id == job_id, log.target == TARGET)
            .order_by(log.id)
        ).all()
    return om.committed(rows) if rows else None


@handler("import.objects", "Импорт файлов", queue="heavy", estimate_memory=_memory)
def import_objects(ctx: JobContext) -> dict[str, Any]:
    from bf_core.formats.registry import SUPPORTED
    from bf_core.rasters import store as rs
    from bf_core.storage.blobs import BlobStore, add_owner

    p = ctx.params
    engine = ctx.engine()
    groups, rest = plan([str(f) for f in p.get("files") or []])
    before = _committed(engine, ctx.id)
    if before is not None:  # a retry after the final transaction: everything is in the project
        ctx.log("Данные уже записаны предыдущей попыткой", "info")
        names = {f["file"] for f in before}
        lost = [
            {"path": str(g.main), "file": g.main.name, "code": "not_loaded", "message": "Файл не загружен.",
             "hint": "Причина — в журнале первой попытки задания."}
            for g in groups if g.main.name not in names
        ]  # fmt: skip
        return _result(before, lost, rest)
    for r in rest:
        ctx.log(f"{r['file']}: {r['message']}", "warn")
    if not groups:
        raise JobError("nothing_to_import", "Среди выбранных файлов нет ни одного, который можно загрузить.", SUPPORTED)
    try:
        level = parse_level(p.get("raster_z"))
    except ValueError as exc:
        raise JobError("not_a_number", str(exc), NUMBER_HINT) from exc
    assert ctx.project_folder is not None
    store = BlobStore(ctx.project_folder)
    options = options_by_file(p.get("options"))  # keys as paths are spelled here, whatever the caller sent
    folder = p.get("folder") or None
    done: list[_Done] = []
    failed: list[dict[str, Any]] = []
    temps: list[Path] = []
    try:
        for k, g in enumerate(groups):
            base, share = 0.9 * k / len(groups), 0.9 / len(groups)
            label = g.main.name

            def step(frac: float, msg: str | None, lo: float = 0.0, hi: float = 0.7, base: float = base,
                     share: float = share, label: str = label) -> None:  # fmt: skip
                at = base + share * (lo + (hi - lo) * min(max(frac, 0.0), 1.0))
                ctx.tick(at, f"{label}: {msg}" if msg else label)

            def fail(exc: BaseException, label: str = label, path: Path = g.main) -> None:
                failure = _failure(path, exc)
                failed.append(failure)
                ctx.log(f"«{label}» не загружен: {failure['message']} {failure['hint']}".strip(), "error")

            # Wireframes that a reader gives one at a time are read while they are stored: their reader reports
            # through the meter; everything else is read by `read_group` — the first 0.7 of the file's share.
            meter = om.Meter()

            def reading(frac: float, msg: str | None, meter: om.Meter = meter, step: Any = step) -> None:
                if meter.lazy:
                    meter.reading(frac, msg)
                else:
                    step(frac, msg)

            step(0.0, None)
            chosen = options.get(option_key(g.main))
            if obf.is_blockfile(g.main, chosen):  # a text block model (ADR 0023 §7): its own reader and writer
                try:
                    block_file = obf.write_blockfile(ctx, engine, store, g, chosen, _span(step, 0.0, 1.0), folder)
                except (FormatError, OSError) as exc:
                    fail(exc)
                    continue
                block_item = _Done(g.main, label, block_file.format, om.MEDIA, _settings(g, chosen))
                block_item.blocks = block_file
                done.append(block_item)
                continue
            if oc.wanted(g, chosen):
                cloud = _write_cloud(ctx, engine, store, g, chosen, _span(step, 0.0, 1.0), len(done), fail)
                if cloud is not None:
                    done.append(cloud)
                continue
            try:
                read = read_group(g, chosen, reading, displays=True, section=p.get("section"), converter=p.get("oda"))
                if not has_content(read):
                    raise FormatError("empty_file", EMPTY, "Проверьте файл.")
            except (FormatError, OSError) as exc:
                fail(exc)
                continue
            for w in read.warnings:
                ctx.log(f"{label}: {w}", "warn")
            item: _Done | None = None
            meshes = om.has_meshes(read)
            # shares of the file: lines → wireframes → block models (an OMF file has all three)
            blocks = 0.9 if read.volumes else 1.0
            lines_top = (0.75 if read.volumes else 0.85) if meshes else blocks
            if read.rasters:
                item = _write_raster(ctx, engine, store, g, read, level)
                ctx.log(f"{label}: растр {item.counts['width']} × {item.counts['height']} пикселей", "info")
            elif any(layer.point_count for layer in read.layers):
                item = _write_lines(ctx, engine, store, g, read, _span(step, 0.7, lines_top))
                c = item.counts
                ctx.log(
                    f"{label}: слоёв — {n_text(c['layers'])}, линий — {n_text(c['lines'])}, "
                    f"вершин — {n_text(c['vertices'])}, точек — {n_text(c['points'])}",
                    "info",
                )
            if meshes:
                lazy = not isinstance(read.meshes, list)
                low = lines_top if item is not None else 0.0 if lazy and not read.volumes else 0.7
                meter.start(_span(step, low, blocks), lazy)
                try:
                    written = om.write_meshes(ctx, engine, store, g, read, meter, folder)
                except om.FileFailed as bad:
                    fail(bad.cause)
                else:
                    if item is None:
                        item = _Done(g.main, label, read.format, om.MEDIA, _settings(g, chosen))
                    item.meshes = written
                    if written.companions:  # the other file of a pair, chosen or found next to the file
                        item.companions.update(written.companions)
                        item.settings["sidecars"] = sorted(path.name for path in written.companions.values())
            if read.volumes:
                found = ob.write_volumes(ctx, engine, store, g, read, _span(step, blocks, 1.0), folder)
                if found.ids:
                    if item is None:
                        item = _Done(g.main, label, read.format, om.MEDIA, _settings(g, chosen))
                    item.blocks = found
            if item is not None:
                done.append(item)
            del read  # keep the memory for the next file
        if not done:
            first = failed[0]
            # every refused file with its `diag`/`choices` stays in the error: the window of the job offers the choice
            message = f"«{first['file']}»: {first['message']}"
            raise JobError(first["code"], message, first["hint"], extra={"failed": failed})

        # The source files — the last step before the final transaction (ТЗ 6.7); still cancellable.
        total = sum(_size(item.path) for item in done) or 1
        copied = 0

        def copy_tick(n: int) -> None:
            nonlocal copied
            copied += n
            ctx.tick(0.9 + 0.08 * min(copied / total, 1.0), "Сохранение исходных файлов в проекте")

        for item in done:
            try:
                item.source = store.stage_stream(_chunks(item.path, copy_tick))
                temps.append(item.source[0])
                for suffix, path in item.companions.items():
                    item.sidecars[suffix] = store.stage_file(path)
                    temps.append(item.sidecars[suffix][0])
            except OSError as exc:  # the file went away or the disk is full: nothing is imported
                raise JobError(
                    "source_not_saved",
                    f"«{item.label}»: исходный файл не сохранён в проекте ({exc.strerror or exc}).",
                    "Проверьте доступ к файлу и свободное место на диске с проектом, затем повторите импорт.",
                ) from exc
        ctx.tick(0.98, "Запись в проект")
        files: list[dict[str, Any]] = []
        with dialect.write_tx(engine) as conn:  # the final transaction: sources, journal and «готово» together
            now = dialect.utcnow()
            for item in done:
                assert item.source is not None
                sha = store.place(conn, item.source, media_type=item.media)
                sides = {
                    f"sidecar:{suffix.lstrip('.').lower()}": store.place(conn, staged, media_type="text/plain")
                    for suffix, staged in item.sidecars.items()
                }
                if item.object_id is not None:  # a line file or a raster owns its files itself
                    add_owner(conn, sha, rs.OWNER, item.object_id, "source")
                    for role, side in sides.items():
                        add_owner(conn, side, rs.OWNER, item.object_id, role)
                    conn.execute(
                        sa.insert(import_log).values(
                            object_id=item.object_id,
                            job_id=ctx.id,
                            file_name=item.label,
                            format=item.format,
                            target=TARGET,
                            mapping=item.settings,
                            counts={"kind": item.kind, **item.counts},
                            sha256=sha,
                            created_at=now,
                        )  # fmt: skip
                    )
                    files.append(
                        {
                            "file": item.label,
                            "format": item.format,
                            "object_id": item.object_id,
                            "kind": item.kind,
                            **item.counts,
                        }  # fmt: skip
                    )
                if item.blocks is not None:  # every block model: its row of the journal, which owns the source
                    files.append(
                        ob.log_blocks(
                            conn,
                            item.blocks,
                            label=item.label,
                            fmt=item.format,
                            settings=item.settings,
                            sha=sha,
                            job_id=ctx.id,
                            now=now,
                        )  # fmt: skip
                    )
                if item.meshes is not None:  # every wireframe: its row of the journal, which owns the files
                    files.append(
                        om.log_meshes(
                            conn,
                            item.meshes,
                            label=item.label,
                            fmt=item.format,
                            settings=item.settings,
                            sha=sha,
                            sides=sides,
                            job_id=ctx.id,
                            now=now,
                        )  # fmt: skip
                    )
            mine = sa.and_(objects.c.job_id == ctx.id, objects.c.state == "building")
            conn.execute(sa.update(objects).where(mine).values(state="ready", job_id=None, updated_at=now))
    finally:
        for tmp in temps:  # moved into place by the final transaction, or left by a cancel or a failure
            tmp.unlink(missing_ok=True)
    return _result(files, failed, rest)
