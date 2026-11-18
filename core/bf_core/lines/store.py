"""Line files and their layers in the project (ADR 0011 §1).

A file is `objects(kind="line_file")`: the format, encoding, source field schema, CRS and warnings in `meta`,
the file as loaded in the `source` blob. Its layers are `objects(kind="line_layer", parent_id=file)` with a
`line_layers` row and the geometry in immutable blobs of the owner `object`: `xyz <f8 N×3`, `offsets <i8`,
`closed u1` (not for points), `seq <u4` (per vertex), `attrs` and `vattrs` (Parquet). Arrays are raw little-endian.

Writing a big layer: `stage_layer` outside the write transaction (temp files with fsync, SHA-256), then
`commit_layer` inside it; `StagedLayer.discard()` drops the temp files when the commit does not happen.
Deleting a file deletes its layers (ON DELETE CASCADE) and their file references.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, line_layers, objects
from bf_core.formats.lineset import AttrField, LineSet
from bf_core.storage.blobs import CHUNK, BlobStore

LINE_FILE = "line_file"
LINE_LAYER = "line_layer"
OWNER = "object"
ARRAY_MEDIA = "application/octet-stream"
PARQUET_MEDIA = "application/vnd.apache.parquet"
#: role → dtype of the raw arrays of a layer
ARRAYS: dict[str, str] = {"xyz": "<f8", "offsets": "<i8", "closed": "u1", "seq": "<u4"}
TABLES = ("attrs", "vattrs")

Staged = tuple[Path, str, int]


def _not_found(what: str = "Слой линий") -> ApiError:
    return ApiError(404, "line_layer_not_found", f"{what} не найден.", "Обновите дерево проекта.")


def _chunks(arr: np.ndarray, tick: Callable[[], None] | None = None) -> Iterator[bytes]:
    raw = memoryview(np.ascontiguousarray(arr)).cast("B")
    for start in range(0, len(raw), CHUNK):
        if tick is not None:
            tick()  # may raise to cancel: the temp files go
        yield bytes(raw[start : start + CHUNK])


def _parquet(table: Any) -> bytes:
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    return buf.getvalue()


@dataclass
class StagedLayer:
    lineset: LineSet
    files: dict[str, Staged] = field(default_factory=dict)

    def discard(self) -> None:
        for tmp, _sha, _size in self.files.values():
            tmp.unlink(missing_ok=True)
        self.files.clear()


def stage_layer(store: BlobStore, ls: LineSet, tick: Callable[[], None] | None = None) -> StagedLayer:
    """The blobs of a layer in temporary files (no database access). `tick()` is called before every megabyte
    of an array: a job reports there and may raise to cancel."""
    staged = StagedLayer(ls)
    arrays: dict[str, np.ndarray | None] = {"xyz": ls.xyz, "offsets": ls.offsets, "closed": ls.closed, "seq": ls.seq}
    try:
        for role, arr in arrays.items():
            if arr is not None:
                staged.files[role] = store.stage_stream(_chunks(np.asarray(arr, dtype=ARRAYS[role]), tick))
        for role, table in (("attrs", ls.attrs), ("vattrs", ls.vattrs)):
            if table is not None:
                staged.files[role] = store.stage_bytes(_parquet(table))
    except BaseException:
        staged.discard()
        raise
    return staged


def _object_values(name: str, kind: str, **extra: Any) -> dict[str, Any]:
    now = dialect.utcnow()
    values: dict[str, Any] = dict(
        kind=kind,
        name=name,
        name_key=dialect.name_key(name),
        sort_key=0.0,
        state="ready",
        version=1,
        meta={},
        created_at=now,
        updated_at=now,
    )
    values.update(extra)
    return values


def create_file(
    conn: sa.Connection,
    store: BlobStore,
    *,
    name: str,
    meta: dict[str, Any],
    source: Path | Staged | None = None,
    source_media: str = ARRAY_MEDIA,
    parent_id: int | None = None,
    folder: str | None = None,
    state: str = "ready",
    job_id: int | None = None,
    crs: str | None = None,
    author: str | None = None,
) -> int:
    """The object of a line file (the parent of its layers); `source` — the file as loaded (path or staged)."""
    values = _object_values(
        name,
        LINE_FILE,
        parent_id=parent_id,
        folder=folder,
        state=state,
        job_id=job_id,
        meta=meta,
        crs=crs,
        author=author,
    )
    file_id = int(conn.execute(sa.insert(objects).values(**values)).inserted_primary_key[0])  # type: ignore[index]
    if source is not None:
        staged = store.stage_file(source) if isinstance(source, Path) else source
        store.commit_staged(conn, staged, media_type=source_media, owner_kind=OWNER, owner_id=file_id, role="source")
    return file_id


def _file_row(conn: sa.Connection, file_id: int) -> sa.Row[Any]:
    row = conn.execute(
        sa.select(objects.c.id, objects.c.state, objects.c.job_id, objects.c.crs, objects.c.extent).where(
            objects.c.id == file_id, objects.c.kind == LINE_FILE
        )
    ).first()
    if row is None:
        raise _not_found("Файл линий")
    return row


def _union(a: Sequence[float | None] | None, b: Sequence[float | None] | None) -> list[float | None] | None:
    if not a:
        return list(b) if b else None
    if not b:
        return list(a)

    def pick(x: float | None, y: float | None, f: Any) -> float | None:
        return y if x is None else x if y is None else f(x, y)

    return [pick(a[i], b[i], min) for i in range(3)] + [pick(a[i], b[i], max) for i in range(3, 6)]


def commit_layer(
    conn: sa.Connection,
    store: BlobStore,
    staged: StagedLayer,
    *,
    file_id: int,
    sort_key: float = 0.0,
    author: str | None = None,
) -> int:
    """Inside the write transaction: the layer object (state and job of its file), its blobs and row."""
    parent = _file_row(conn, file_id)
    ls = staged.lineset
    ext = ls.extent()
    values = _object_values(
        ls.name,
        LINE_LAYER,
        parent_id=file_id,
        state=parent.state,
        job_id=parent.job_id,
        crs=parent.crs,
        extent=ext,
        sort_key=sort_key,
        author=author,
    )
    layer_id = int(conn.execute(sa.insert(objects).values(**values)).inserted_primary_key[0])  # type: ignore[index]
    for role, tmp in list(staged.files.items()):
        media = PARQUET_MEDIA if role in TABLES else ARRAY_MEDIA
        store.commit_staged(conn, tmp, media_type=media, owner_kind=OWNER, owner_id=layer_id, role=role)
        del staged.files[role]  # in place now: a later discard() must not touch it
    st = ls.stats()
    conn.execute(
        sa.insert(line_layers).values(
            object_id=layer_id,
            line_count=st["line_count"],
            point_count=st["point_count"],
            length=st["length"],
            closed_count=st["closed_count"],
            flat=st["flat"],
            color=ls.color,
            attr_schema=ls.attr_schema(),
            points_only=ls.points_only,
            origin=ls.origin(),
        )
    )
    grown = _union(parent.extent, ext)
    if grown != parent.extent:
        conn.execute(sa.update(objects).where(objects.c.id == file_id).values(extent=grown))
    return layer_id


def put_layer(conn: sa.Connection, store: BlobStore, ls: LineSet, *, file_id: int, **kw: Any) -> int:
    """Stage and commit in one go (small layers, tests). The temp files go if the commit fails."""
    staged = stage_layer(store, ls)
    try:
        return commit_layer(conn, store, staged, file_id=file_id, **kw)
    finally:
        staged.discard()


# --- reading -------------------------------------------------------------------------------------------


def layer_blobs(conn: sa.Connection, layer_id: int) -> dict[str, str]:
    rows = conn.execute(
        sa.select(blob_owners.c.role, blob_owners.c.sha256).where(
            blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == layer_id
        )
    ).all()
    return {str(r.role): str(r.sha256) for r in rows}


def layer_info(conn: sa.Connection, layer_id: int) -> dict[str, Any]:
    row = (
        conn.execute(
            sa.select(
                objects.c.id,
                objects.c.parent_id,
                objects.c.name,
                objects.c.state,
                objects.c.version,
                objects.c.extent,
                objects.c.crs,
                line_layers,
            )
            .join(line_layers, line_layers.c.object_id == objects.c.id)
            .where(objects.c.id == layer_id, objects.c.kind == LINE_LAYER)
        )
        .mappings()
        .first()
    )
    if row is None:
        raise _not_found()
    out = dict(row)
    out.pop("object_id", None)
    out["file_id"] = out.pop("parent_id")
    return out


def layer_ids(conn: sa.Connection, file_id: int) -> list[int]:
    return list(
        conn.execute(
            sa.select(objects.c.id)
            .where(objects.c.parent_id == file_id, objects.c.kind == LINE_LAYER)
            .order_by(objects.c.sort_key, objects.c.id)
        ).scalars()
    )


def read_layer(conn: sa.Connection, store: BlobStore, layer_id: int) -> LineSet:
    info = layer_info(conn, layer_id)
    shas = layer_blobs(conn, layer_id)
    if "xyz" not in shas or "seq" not in shas or (not info["points_only"] and "offsets" not in shas):
        raise ApiError(
            500, "line_layer_broken", "Файлы геометрии слоя линий не найдены.", "Загрузите файл линий заново."
        )

    def array(role: str) -> np.ndarray | None:
        sha = shas.get(role)
        return None if sha is None else np.fromfile(store.path_of(sha), dtype=ARRAYS[role])

    def table(role: str) -> Any:
        import pyarrow.parquet as pq

        sha = shas.get(role)
        return None if sha is None else pq.read_table(store.path_of(sha))

    xyz = array("xyz")
    assert xyz is not None
    units = {f.name: f.unit for f in (AttrField.from_dict(d) for d in info["attr_schema"] or []) if f.unit is not None}
    return LineSet(
        info["name"],
        xyz.reshape(-1, 3),
        array("offsets"),
        array("closed"),
        array("seq"),
        table("attrs"),
        table("vattrs"),
        info["color"],
        units,
    )


# --- deleting ------------------------------------------------------------------------------------------


def delete_file(conn: sa.Connection, file_id: int) -> list[int]:
    """The file object, its layers (cascade) and the file references of all of them. Returns the ids gone."""
    _file_row(conn, file_id)
    ids = [file_id, *layer_ids(conn, file_id)]
    from bf_core.drillholes.store import drop_import_owners

    drop_import_owners(conn, ids)  # import_log goes by cascade: its source files must not stay owned
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id.in_(ids)))
    conn.execute(sa.delete(objects).where(objects.c.id == file_id))
    return ids
