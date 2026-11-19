"""Line files and their layers in the project (ADR 0011 §1).

A file is `objects(kind="line_file")`: the format, encoding, source field schema, CRS and warnings in `meta`,
the file as loaded in the `source` blob. Its layers are `objects(kind="line_layer", parent_id=file)` with a
`line_layers` row and the geometry in immutable blobs of the owner `object`: `xyz <f8 N×3`, `offsets <i8`,
`closed u1` (not for points), `seq <u4` (per vertex), `attrs` and `vattrs` (Parquet). Arrays are raw little-endian.
Small layers staged together (`stage_layers`: the portions of an import) share one blob instead — a pack
(`pack.py`), the role `pack:<n>` naming the entry of the layer; `layer_files` finds the files of a layer either
way, and every reader goes through it.

Writing: `stage_layer` / `stage_layers` outside the write transaction (temp files with fsync, SHA-256), then
`commit_layer` / `commit_layers` inside it; `StagedLayer.discard()` drops the temp files when the commit does
not happen. Deleting a file deletes its layers (ON DELETE CASCADE) and their file references.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, line_layer_versions, line_layers, objects
from bf_core.formats.lineset import AttrField, LineSet
from bf_core.storage.blobs import CHUNK, BlobStore, add_owners

from . import pack

LINE_FILE = "line_file"
LINE_LAYER = "line_layer"
OWNER = "object"
VERSION_OWNER = "line_version"  # a row of line_layer_versions (ADR 0015 §1)
ARRAY_MEDIA = "application/octet-stream"
PARQUET_MEDIA = "application/vnd.apache.parquet"
#: role → dtype of the raw arrays of a layer
ARRAYS: dict[str, str] = {"xyz": "<f8", "offsets": "<i8", "closed": "u1", "seq": "<u4", "lid": "<u4"}
TABLES = ("attrs", "vattrs")
#: A layer whose files together are at most this many bytes goes into the pack of its portion (`stage_layers`).
PACK_LIMIT = 1 << 20
PACK_BYTES = 16 << 20  # a pack is closed when it holds this much: the next small layer starts another
#: Rows in one Parquet row group of the attribute tables: a page of attributes decodes one group, two at a seam.
ROW_GROUP = 1 << 16

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
    """The Parquet file of a table of attributes, in row groups of `ROW_GROUP` rows: a page of the attributes
    (`LayerFiles.rows`) decodes only the groups it falls into."""
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd", row_group_size=ROW_GROUP)
    return buf.getvalue()


@dataclass
class StagedPack:
    """A pack in its temporary file; shared by the staged layers that are its entries."""

    file: Staged
    sha: str | None = None  # set once the file is in place and has its `blobs` row (in the commit transaction)

    def discard(self) -> None:
        self.file[0].unlink(missing_ok=True)


@dataclass
class StagedLayer:
    lineset: LineSet
    files: dict[str, Staged] = field(default_factory=dict)  # the own blobs of a layer that is not in a pack
    pack: StagedPack | None = None
    entry: int = 0  # the entry of the layer in its pack

    @property
    def packed(self) -> bool:
        return self.pack is not None

    def discard(self) -> None:
        for tmp, _sha, _size in self.files.values():
            tmp.unlink(missing_ok=True)
        self.files.clear()
        if self.pack is not None:
            self.pack.discard()  # a pack already moved into place has no temp file left


def stage_layers(
    store: BlobStore,
    layers: Sequence[LineSet],
    tick: Callable[[int], None] | None = None,
    *,
    pack_limit: int = PACK_LIMIT,
) -> list[StagedLayer]:
    """The blobs of layers in temporary files (no database access). A layer whose files take at most `pack_limit`
    bytes goes into a pack shared with the other small layers of the call; a bigger one gets its own blobs.
    `tick(k)` is called before layer k and before every megabyte of a big array: a job reports there and may
    raise to cancel — nothing is left then."""
    staged: list[StagedLayer] = []
    small: list[tuple[StagedLayer, dict[str, bytes]]] = []
    held = 0

    def close_pack() -> None:
        nonlocal held
        if small:
            shared = StagedPack(store.stage_bytes(pack.encode([files for _layer, files in small])))
            for entry, (layer, _files) in enumerate(small):
                layer.pack, layer.entry = shared, entry
            small.clear()
        held = 0

    try:
        for k, ls in enumerate(layers):
            if tick is not None:
                tick(k)
            one = StagedLayer(ls)
            staged.append(one)
            arrays = {
                role: np.ascontiguousarray(arr, dtype=ARRAYS[role])
                for role, arr in (("xyz", ls.xyz), ("offsets", ls.offsets), ("closed", ls.closed), ("seq", ls.seq))
                if arr is not None
            }
            size = sum(int(a.nbytes) for a in arrays.values())
            tables: dict[str, bytes] = {}
            if size <= pack_limit:  # the tables of a big layer are written after its arrays, one at a time
                tables = {r: _parquet(t) for r, t in (("attrs", ls.attrs), ("vattrs", ls.vattrs)) if t is not None}
                size += sum(len(data) for data in tables.values())
            if size <= pack_limit:
                if held and held + size > PACK_BYTES:
                    close_pack()
                small.append((one, {**{role: a.tobytes() for role, a in arrays.items()}, **tables}))
                held += size
                continue
            step = None if tick is None else partial(tick, k)
            for role, arr in arrays.items():
                one.files[role] = store.stage_stream(_chunks(arr, step))
            for role, table in (("attrs", ls.attrs), ("vattrs", ls.vattrs)):
                if table is not None:
                    one.files[role] = store.stage_bytes(tables.get(role) or _parquet(table))
        close_pack()
    except BaseException:
        for one in staged:
            one.discard()
        raise
    return staged


def stage_layer(store: BlobStore, ls: LineSet, tick: Callable[[], None] | None = None) -> StagedLayer:
    """One layer with its own blobs (`xyz`, `offsets`, …), whatever its size. `tick()` — as in `stage_layers`."""
    step = None if tick is None else lambda _k: tick()
    return stage_layers(store, [ls], step, pack_limit=-1)[0]


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


def commit_layers(
    conn: sa.Connection,
    store: BlobStore,
    staged: Sequence[StagedLayer],
    *,
    file_id: int,
    sort_key: float = 0.0,
    author: str | None = None,
) -> list[int]:
    """Inside the write transaction: the layer objects (state and job of their file; `sort_key`, `sort_key` + 1,
    … in the order given), their blobs and rows. A pack is moved into place once, its layers refer to it."""
    parent = _file_row(conn, file_id)
    grown = parent.extent
    ids: list[int] = []
    rows: list[dict[str, Any]] = []
    owners: list[tuple[str, str, int, str]] = []
    for k, part in enumerate(staged):
        ls = part.lineset
        ext = ls.extent()
        values = _object_values(
            ls.name,
            LINE_LAYER,
            parent_id=file_id,
            state=parent.state,
            job_id=parent.job_id,
            crs=parent.crs,
            extent=ext,
            sort_key=sort_key + k,
            author=author,
        )
        layer_id = int(conn.execute(sa.insert(objects).values(**values)).inserted_primary_key[0])  # type: ignore[index]
        ids.append(layer_id)
        if part.pack is not None:
            if part.pack.sha is None:
                part.pack.sha = store.place(conn, part.pack.file, media_type=ARRAY_MEDIA)
            owners.append((part.pack.sha, OWNER, layer_id, pack.role(part.entry)))
        for role, tmp in list(part.files.items()):
            media = PARQUET_MEDIA if role in TABLES else ARRAY_MEDIA
            store.commit_staged(conn, tmp, media_type=media, owner_kind=OWNER, owner_id=layer_id, role=role)
            del part.files[role]  # in place now: a later discard() must not touch it
        st = ls.stats()
        rows.append(
            dict(
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
        grown = _union(grown, ext)
    add_owners(conn, owners)
    if rows:
        conn.execute(sa.insert(line_layers), rows)
    if grown != parent.extent:
        conn.execute(sa.update(objects).where(objects.c.id == file_id).values(extent=grown))
    return ids


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
    return commit_layers(conn, store, [staged], file_id=file_id, sort_key=sort_key, author=author)[0]


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


def broken() -> ApiError:
    return ApiError(500, "line_layer_broken", "Файлы геометрии слоя линий не найдены.", "Загрузите файл линий заново.")


@dataclass(frozen=True)
class LayerFiles:
    """Where the files of a layer are: role → (blob, offset, size); size None — the blob is the file itself."""

    parts: Mapping[str, tuple[Path, int, int | None]]

    def has(self, role: str) -> bool:
        return role in self.parts

    def array(self, role: str) -> np.ndarray | None:
        part = self.parts.get(role)
        if part is None:
            return None
        path, offset, size = part
        dtype = np.dtype(ARRAYS[role])
        if size is None:
            return np.fromfile(path, dtype=dtype)
        count = size // dtype.itemsize
        arr = np.fromfile(path, dtype=dtype, count=count, offset=offset) if count else np.empty(0, dtype=dtype)
        if size % dtype.itemsize or len(arr) != count:
            raise broken()
        return arr

    def table(self, role: str, columns: Sequence[str] | None = None, *, memory_map: bool = False) -> Any:
        """The Parquet table of a role (None — the layer has none); `columns` — only these."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        part = self.parts.get(role)
        if part is None:
            return None
        path, offset, size = part
        if size is None:
            return pq.read_table(path, columns=columns, memory_map=memory_map)
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read(size)
        if len(data) != size:
            raise broken()
        return pq.read_table(pa.BufferReader(data), columns=columns)

    def rows(self, role: str, offset: int, limit: int, columns: Sequence[str] | None = None) -> Any:
        """Rows [offset, offset + limit) of the Parquet table of a role (None — the layer has none), `columns`
        only. The file is mapped, not read: the footer says which row groups hold the rows, and only they are
        decoded — of a table inside a pack too (its bytes are a slice of the mapped blob). A table written as
        one big group (before `ROW_GROUP`) is still read right, only whole."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        part = self.parts.get(role)
        if part is None:
            return None
        path, start, size = part
        try:
            with pa.memory_map(str(path), "r") as mapped:
                source: Any = mapped
                if size is not None:
                    mapped.seek(start)
                    source = pa.BufferReader(mapped.read_buffer(size))
                    if source.size() != size:
                        raise broken()
                file = pq.ParquetFile(source)
                meta = file.metadata
                groups: list[int] = []
                first = at = 0
                for g in range(meta.num_row_groups):
                    n = meta.row_group(g).num_rows
                    if at + n > offset and at < offset + limit:
                        if not groups:
                            first = at
                        groups.append(g)
                    at += n
                    if at >= offset + limit:
                        break
                if not groups:
                    return file.schema_arrow.empty_table().select(list(columns or file.schema_arrow.names))
                return file.read_row_groups(groups, columns=columns).slice(offset - first, limit)
        except (OSError, pa.ArrowInvalid) as exc:
            raise broken() from exc


def layer_files(store: BlobStore, shas: Mapping[str, str]) -> LayerFiles:
    """The files of a layer from its blobs {role: sha256} (`layer_blobs`): its own, or an entry of a pack."""
    parts: dict[str, tuple[Path, int, int | None]] = {}
    for role, sha in shas.items():
        entry = pack.entry_of(role)
        if entry is None:
            if role in ARRAYS or role in TABLES:
                parts[role] = (store.path_of(sha), 0, None)
            continue
        path = store.path_of(sha)
        try:
            slices = pack.directory(path)[entry]
        except (OSError, pack.PackError, IndexError) as exc:
            raise broken() from exc
        for name, (offset, size) in slices.items():
            if name in ARRAYS or name in TABLES:
                parts[name] = (path, offset, size)
    return LayerFiles(parts)


def read_layer(conn: sa.Connection, store: BlobStore, layer_id: int) -> LineSet:
    info = layer_info(conn, layer_id)
    files = layer_files(store, layer_blobs(conn, layer_id))
    if not files.has("xyz") or not files.has("seq") or (not info["points_only"] and not files.has("offsets")):
        raise broken()
    xyz = files.array("xyz")
    assert xyz is not None
    units = {f.name: f.unit for f in (AttrField.from_dict(d) for d in info["attr_schema"] or []) if f.unit is not None}
    return LineSet(
        info["name"],
        xyz.reshape(-1, 3),
        files.array("offsets"),
        files.array("closed"),
        files.array("seq"),
        files.table("attrs"),
        files.table("vattrs"),
        info["color"],
        units,
    )


# --- deleting ------------------------------------------------------------------------------------------


def drop_version_owners(conn: sa.Connection, layer_ids: Sequence[int]) -> None:
    """Before layers are deleted (their journal goes by cascade): the files of their versions lose that owner, so
    the storage cleanup can remove the ones the current state does not keep."""
    rows = sa.select(line_layer_versions.c.id).where(line_layer_versions.c.layer_id.in_(list(layer_ids)))
    conn.execute(
        sa.delete(blob_owners).where(blob_owners.c.owner_kind == VERSION_OWNER, blob_owners.c.owner_id.in_(rows))
    )


def delete_file(conn: sa.Connection, file_id: int) -> list[int]:
    """The file object, its layers (cascade) and the file references of all of them. Returns the ids gone."""
    _file_row(conn, file_id)
    ids = [file_id, *layer_ids(conn, file_id)]
    from bf_core.drillholes.store import drop_import_owners

    drop_import_owners(conn, ids)  # import_log goes by cascade: its source files must not stay owned
    drop_version_owners(conn, ids)  # so do the versions of the layers (line_layer_versions, ADR 0015 §1)
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id.in_(ids)))
    conn.execute(sa.delete(objects).where(objects.c.id == file_id))
    return ids
