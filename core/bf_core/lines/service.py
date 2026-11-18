"""Operations on line files and their layers (ТЗ 7.18; ADR 0011 §1, §3): the tree, properties, attributes by
pages, renaming and colour, deleting a file with its layers.

The version of a layer is `objects.version`: every change raises it (the parts of the 3D view are keyed by it,
`viz/lines.py`), a stale `expected_version` is a 409 with the current layer. Only objects that are `ready` are
seen here: what an import is still building does not exist for the interface.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import line_layers, objects
from bf_core.storage.blobs import BlobStore

from . import store

COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SCOPES = ("feature", "vertex")
_TABLE_OF = {"feature": "attrs", "vertex": "vattrs"}

_LAYER = (
    objects.c.id, objects.c.parent_id, objects.c.name, objects.c.version, objects.c.extent, objects.c.sort_key,
    line_layers.c.line_count, line_layers.c.point_count, line_layers.c.length, line_layers.c.closed_count,
    line_layers.c.flat, line_layers.c.color, line_layers.c.attr_schema, line_layers.c.points_only,
    line_layers.c.origin,
)  # fmt: skip


def _layer_out(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["file_id"] = out.pop("parent_id")
    out.pop("sort_key", None)
    out["attr_schema"] = out["attr_schema"] or []
    # «замкнутость» of the properties: every line is closed, some are, none is (points have no lines)
    lines, closed = int(out["line_count"]), int(out["closed_count"])
    out["closed"] = "none" if closed == 0 else "all" if closed == lines else "some"
    return out


def _layers(conn: sa.Connection, where: sa.ColumnElement[bool]) -> list[dict[str, Any]]:
    rows = conn.execute(
        sa.select(*_LAYER)
        .join(line_layers, line_layers.c.object_id == objects.c.id)
        .where(objects.c.kind == store.LINE_LAYER, objects.c.state == "ready", where)
        .order_by(objects.c.parent_id, objects.c.sort_key, objects.c.id)
    ).mappings()
    return [_layer_out(r) for r in rows]


def get_layer(conn: sa.Connection, layer_id: int) -> dict[str, Any]:
    found = _layers(conn, objects.c.id == layer_id)
    if not found:
        raise store._not_found()
    return found[0]


def _file_out(row: Any, layers: list[dict[str, Any]]) -> dict[str, Any]:
    meta = row["meta"] or {}
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "folder": row["folder"],
        "version": int(row["version"]),
        "extent": row["extent"],
        "crs": row["crs"],
        "crs_name": meta.get("crs_name"),
        "epsg": meta.get("epsg"),
        "format": meta.get("format"),
        "label": meta.get("label"),
        "filename": meta.get("filename"),
        "encoding": meta.get("encoding"),
        "fields": meta.get("fields") or [],
        "warnings": meta.get("warnings") or [],
        "created_at": row["created_at"].isoformat(),
        "layers": layers,
    }


def list_files(conn: sa.Connection, file_id: int | None = None) -> list[dict[str, Any]]:
    """Line files of the project with their layers, in the order of the tree."""
    where = [objects.c.kind == store.LINE_FILE, objects.c.state == "ready"]
    if file_id is not None:
        where.append(objects.c.id == file_id)
    files = conn.execute(sa.select(objects).where(*where).order_by(objects.c.sort_key, objects.c.id)).mappings().all()
    by_file: dict[int, list[dict[str, Any]]] = {int(f["id"]): [] for f in files}
    own = objects.c.parent_id == file_id if file_id is not None else sa.true()
    for layer in _layers(conn, own):
        if layer["file_id"] in by_file:
            by_file[layer["file_id"]].append(layer)
    return [_file_out(f, by_file[int(f["id"])]) for f in files]


def get_file(conn: sa.Connection, file_id: int) -> dict[str, Any]:
    found = list_files(conn, file_id)
    if not found:
        raise store._not_found("Файл линий")
    return found[0]


# --- changing ----------------------------------------------------------------------------------------------


def _conflict(layer: dict[str, Any]) -> ApiError:
    return ApiError(
        409,
        "version_conflict",
        "Слой линий изменили, пока вы его правили.",
        "Свойства слоя обновлены — повторите правку.",
        extra={"layer": layer},
    )


def update_layer(
    conn: sa.Connection, layer_id: int, expected_version: int, changes: Mapping[str, Any]
) -> dict[str, Any]:
    """Rename and recolour. `changes` holds only what is set: `name`, `color` ("#rrggbb"; None — no own colour)."""
    current = get_layer(conn, layer_id)
    if current["version"] != expected_version:
        raise _conflict(current)
    values: dict[str, Any] = {}
    if "name" in changes:
        name = " ".join(str(changes["name"] or "").split())
        if not name:
            raise ApiError(422, "name_empty", "Название слоя не может быть пустым.", "Введите название слоя.")
        key = dialect.name_key(name)
        other = sa.and_(
            objects.c.kind == store.LINE_LAYER,
            objects.c.parent_id == current["file_id"],
            objects.c.name_key == key,
            objects.c.id != layer_id,
        )
        if conn.execute(sa.select(objects.c.id).where(other)).first() is not None:
            raise ApiError(409, "name_taken", f"Слой «{name}» в этом файле уже есть.", "Введите другое название.")
        values.update(name=name, name_key=key)
    if "color" in changes:
        color = changes["color"]
        if color is not None and not COLOR_RE.match(str(color)):
            raise ApiError(422, "bad_color", "Цвет слоя задаётся как #rrggbb.", "Выберите цвет в палитре.")
        conn.execute(
            sa.update(line_layers)
            .where(line_layers.c.object_id == layer_id)
            .values(color=None if color is None else str(color).lower())
        )
    n = conn.execute(
        sa.update(objects)
        .where(objects.c.id == layer_id, objects.c.version == expected_version)
        .values(**values, version=expected_version + 1, updated_at=dialect.utcnow())
    ).rowcount
    if n != 1:
        raise _conflict(get_layer(conn, layer_id))
    return get_layer(conn, layer_id)


def delete_file(conn: sa.Connection, file_id: int) -> list[int]:
    """A file of lines with its layers and their file references. Returns the ids that are gone."""
    row = conn.execute(
        sa.select(objects.c.state).where(objects.c.id == file_id, objects.c.kind == store.LINE_FILE)
    ).first()
    if row is None:
        raise store._not_found("Файл линий")
    if row.state != "ready":
        raise ApiError(
            409, "file_busy", "Файл линий ещё загружается.", "Дождитесь окончания импорта или отмените задание."
        )
    return store.delete_file(conn, file_id)


# --- attributes by pages -----------------------------------------------------------------------------------


def attrs_key(conn: sa.Connection, layer_id: int, scope: str) -> tuple[dict[str, Any], str | None]:
    """The layer and the SHA-256 of its attribute table of a scope (None: the layer has no such attributes)."""
    if scope not in SCOPES:
        raise ApiError(
            422, "bad_scope", "Неизвестный вид атрибутов слоя.", "Укажите feature (по линиям) или vertex (по вершинам)."
        )
    layer = get_layer(conn, layer_id)
    return layer, store.layer_blobs(conn, layer_id).get(_TABLE_OF[scope])


def _cell(v: Any) -> Any:
    return None if isinstance(v, float) and not math.isfinite(v) else v


def attrs_page(
    blobs: BlobStore, layer: Mapping[str, Any], sha: str | None, scope: str, offset: int, limit: int
) -> dict[str, Any]:
    """Rows [offset, offset + limit) of the attributes: one row per line (per point of a point layer), or per
    vertex for `scope="vertex"`. An empty number is null, never 0."""
    fields = [f for f in layer["attr_schema"] if f.get("scope", "feature") == scope]
    if scope == "feature":
        total = int(layer["point_count"] if layer["points_only"] else layer["line_count"])
    else:
        total = int(layer["point_count"])
    out: dict[str, Any] = {
        "layer_id": layer["id"], "version": layer["version"], "scope": scope, "fields": fields, "total": total,
        "offset": offset, "limit": limit, "rows": [],
    }  # fmt: skip
    if sha is None or offset >= total:
        return out
    import pyarrow.parquet as pq

    table = pq.read_table(blobs.path_of(sha), memory_map=True).slice(offset, limit)
    names = [f["name"] for f in fields if f["name"] in table.column_names]
    out["fields"] = [f for f in fields if f["name"] in names]
    columns = [table.column(n).to_pylist() for n in names]
    out["rows"] = [[_cell(c[i]) for c in columns] for i in range(table.num_rows)]
    return out
