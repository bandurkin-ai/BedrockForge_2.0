"""Export of a point cloud (ТЗ 7.35 Д-22; ADR 0014 §3 «LAS, запись», «CSV, запись», §6 «Экспорт»): the branch of
the job `export.lines` (`export.py`) for a layer that is a cloud. The cloud goes to the file slice by slice of its
columns (`lines/cloud.py`), never as a layer in memory.

* `las`, `las: "source"` (the default for a cloud loaded from a LAS): the source file kept in the project, byte for
  byte, with the classes of the cloud in its records (`las_write.rewrite_classes`) — colour, GPS time, VLRs and
  the CRS stay as they were; a record the cloud does not have (`every` > 1) keeps its class.
* `las: "new"` (a cloud of a table, or by choice): LAS 1.2 format 0, or 1.4 format 6 for classes above 31 or
  returns above 7 (`las_write.pick_layout`); the scale and the offset of the source LAS — so the integers of the
  records are those of the source and the coordinates read back bit for bit —, otherwise 0,001 and the whole
  metres under the cloud; the CRS label of the layer as GeoTIFF keys (an EPSG code) or a WKT; the storage order.
* `csv`: `X;Y;Z;класс[;h]` (`points_stream.write_cloud_csv`), `h` when «Фильтр земли» gave it; the delimiter,
  decimal sign and encoding of the export window.

A cloud is exported alone: with other layers — `cloud_alone`; to STR, Surpac, DXF or DAT — `cloud_format`; a layer
that is not a cloud to LAS — `las_cloud_only`; «по исходнику» without a LAS source — `no_las_source`.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, export_log, objects
from bf_core.jobs.context import JobContext, JobError

from . import store as ls
from .cloud import cloud_meta, open_cloud
from .export import FORMATS, OPTIONS, late_tick

SLICE = 1 << 20  # points read and written at a time
MODES = ("source", "new")


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def clouds_among(conn: sa.Connection, ids: list[int]) -> list[int]:
    """The layers of `ids` that are point clouds."""
    if not ids:
        return []
    rows = conn.execute(
        sa.select(objects.c.id, objects.c.meta).where(objects.c.id.in_(ids), objects.c.kind == ls.LINE_LAYER)
    ).all()
    return [int(r.id) for r in rows if cloud_meta(r.meta) is not None]


def _has_source(conn: sa.Connection, layer_id: int) -> bool:
    """The cloud was loaded from a LAS and its line file keeps that file."""
    row = conn.execute(sa.select(objects.c.parent_id, objects.c.meta).where(objects.c.id == layer_id)).first()
    meta = None if row is None else cloud_meta(row.meta)
    if meta is None or (meta.get("source") or {}).get("kind") != "las" or row is None or row.parent_id is None:
        return False
    found = conn.execute(
        sa.select(blob_owners.c.sha256).where(
            blob_owners.c.owner_kind == ls.OWNER,
            blob_owners.c.owner_id == row.parent_id,
            blob_owners.c.role == "source",
        )
    ).first()
    return found is not None


def check_cloud(conn: sa.Connection, ids: list[int], fmt: str, las: str | None) -> dict[str, Any]:
    """What the job needs to know of a cloud among the layers to export ({} — there is none): `cloud_id` and, for
    LAS, the mode. ApiError when a cloud cannot be exported so."""
    clouds = clouds_among(conn, ids)
    if not clouds:
        if fmt == "las":
            raise ApiError(
                422,
                "las_cloud_only",
                "В LAS экспортируются только облака точек.",
                "Выберите облако точек или другой формат: CSV, Micromine DAT, DXF.",
            )
        return {}
    if len(ids) != 1:
        raise ApiError(
            422,
            "cloud_alone",
            "Облако точек экспортируется отдельным файлом, без других слоёв.",
            "Выберите одно облако точек.",
        )
    if fmt not in ("las", "csv"):
        raise ApiError(
            422, "cloud_format", f"Облако точек не записывается в {FORMATS[fmt].label}.", "Выберите LAS или CSV."
        )
    out: dict[str, Any] = {"cloud_id": clouds[0]}
    if fmt == "las":
        if las is not None and las not in MODES:
            raise ApiError(
                422, "bad_las_mode", "Неизвестный способ записи LAS.", "Выберите «по исходнику» или «новый файл»."
            )
        can = _has_source(conn, clouds[0])
        if las == "source" and not can:
            raise ApiError(
                422,
                "no_las_source",
                "Облако загружено не из LAS: записать его «по исходнику» нельзя.",
                "Выберите «Новый файл LAS».",
            )
        out["las"] = las or ("source" if can else "new")
    return out


# --- writing -------------------------------------------------------------------------------------------


def _slices(cloud: Any, *roles: str) -> Iterator[list[np.ndarray]]:
    for a in range(0, cloud.count, SLICE):
        k = min(SLICE, cloud.count - a)
        yield [cloud.read(role, a, k) for role in roles]


def _crs_vlrs(wkt: str | None) -> list[Any]:
    """The CRS label of the layer as LAS records: GeoTIFF keys of its EPSG code, or the WKT."""
    from bf_core.formats.las_write import geokey_vlrs, wkt_vlr

    if not wkt:
        return []
    try:
        from bf_core.crs import CrsError, describe

        info = describe(wkt)
    except CrsError:
        return [wkt_vlr(wkt)]
    if info.epsg:
        return geokey_vlrs(info.epsg, geographic=not info.projected, name=info.name)
    return [wkt_vlr(info.wkt)]


def _new_las(cloud: Any, target: Path, wkt: str | None, tick: Any) -> Any:
    from bf_core.formats.las_write import SCALE, LasWriter, pick_layout, return_limit

    top_return = 0
    for (ret,) in _slices(cloud, "ret"):
        top_return = max(top_return, return_limit(ret))
    version, point_format = pick_layout(max(cloud.classes, default=0), top_return)
    las = (cloud.meta.get("source") or {}).get("las") or {}
    scale = las.get("scale") or SCALE
    offset = las.get("offset") or [float(math.floor(v)) for v in cloud.bbox[:3]]
    n = cloud.count
    with LasWriter(
        target, version=version, point_format=point_format, scale=scale, offset=offset, vlrs=_crs_vlrs(wkt)
    ) as writer:
        done = 0
        for xyz, cls, ret in _slices(cloud, "xyz", "class", "ret"):
            tick(done / n, f"Запись точек: {_n(done)} из {_n(n)}")
            writer.write(xyz, cls, ret)
            done += len(xyz)
    tick(1.0, f"Записано точек: {_n(n)}")
    return writer.result


def _csv(cloud: Any, target: Path, options: dict[str, Any], tick: Any) -> Any:
    from bf_core.formats.points_stream import write_cloud_csv

    with_h = cloud.has("h")
    roles = ("xyz", "class", "h") if with_h else ("xyz", "class")

    def portions() -> Iterator[tuple[np.ndarray, np.ndarray, np.ndarray | None]]:
        for cols in _slices(cloud, *roles):
            yield cols[0], cols[1], cols[2] if with_h else None

    opts = {k: v for k, v in options.items() if k in OPTIONS and v is not None}
    return write_cloud_csv(target, portions(), total=cloud.count, with_h=with_h, progress=tick, **opts)


def export_cloud(
    ctx: JobContext,
    engine: sa.Engine,
    store: Any,
    target: Path,
    p: dict[str, Any],
    file_id: int | None,
    sha: str | None,
) -> dict[str, Any]:
    """The cloud `p["cloud_id"]` written to `target` (see the module docstring)."""
    from bf_core.formats.errors import FormatError
    from bf_core.formats.las_write import rewrite_classes

    fmt, mode = str(p["format"]), p.get("las") if p.get("format") == "las" else None
    try:
        with dialect.read_tx(engine) as conn:
            cloud = open_cloud(conn, store, int(p["cloud_id"]))
            wkt = conn.execute(sa.select(objects.c.crs).where(objects.c.id == cloud.layer_id)).scalar()
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc

    def tick(f: float, message: str | None = None) -> None:
        value = 0.05 + 0.95 * max(0.0, min(1.0, f))
        if f >= 1.0:  # the last word of a writer: its file is under its name already
            late_tick(ctx, value, message)
        else:
            ctx.tick(value, message)

    ctx.tick(0.05, "Запись облака точек")
    changed = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "csv":
            written = _csv(cloud, target, p, tick)
        elif mode == "source":
            if sha is None:
                raise FormatError(
                    "no_las_source", "Исходный файл LAS облака не найден в проекте.", "Выберите «Новый файл LAS»."
                )
            written = rewrite_classes(store.path_of(sha), target, cloud.read("seq"), cloud.read("class"), tick)
            changed = written.changed
        else:
            written = _new_las(cloud, target, wkt, tick)
    except FormatError as exc:
        raise JobError(exc.code, exc.message, exc.hint) from exc
    except OSError as exc:
        raise JobError(
            "file_unwritable",
            f"Файл не записан: {exc.strerror or exc}.",
            "Выберите другую папку или закройте файл в другой программе.",
        ) from exc
    warnings = list(written.warnings)
    for w in warnings:
        ctx.log(w, "warn")
    how = {"source": ", по исходному файлу LAS", "new": ", новый файл LAS"}.get(str(mode), "")
    extra = (
        f" (LAS {written.version[0]}.{written.version[1]}, формат точек {written.point_format})" if fmt == "las" else ""
    )
    ctx.log(f"Записан файл «{target.name}»{how}{extra}: точек {_n(cloud.count)}")
    counts = {"layers": 1, "lines": 0, "points": cloud.count, "size": int(written.size)}
    with dialect.write_tx(engine) as conn:
        conn.execute(
            sa.insert(export_log).values(
                object_id=file_id if file_id is not None else cloud.layer_id,
                job_id=ctx.id,
                file_name=target.name,
                format=fmt,
                settings={
                    "layer_ids": [cloud.layer_id],
                    "file_id": p.get("file_id"),
                    "cloud_id": cloud.layer_id,
                    "las": mode,
                    **{k: p.get(k) for k in OPTIONS},
                },
                counts={**counts, "warnings": len(warnings)},
                created_at=dialect.utcnow(),
            )
        )
    late_tick(ctx, 1.0, "Готово")
    out = {"path": str(target), "format": fmt, **counts, "cloud_id": cloud.layer_id, "las": mode}
    out.update(source_layout=mode == "source", changed=changed, warnings=warnings)
    if fmt == "las":
        out.update(version=f"{written.version[0]}.{written.version[1]}", point_format=written.point_format)
    return out
