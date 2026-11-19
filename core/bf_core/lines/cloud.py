"""A point cloud in the project (ADR 0014 §1): a layer of points stored so that 10–50 million of them are read,
shown and reclassified by slices of files, never as a whole.

A cloud is a `line_layer` (`points_only`, no lines) whose `objects.meta` has `cloud`; the id of the layer is the
`cloud_id` of the commands. Its columns are raw little-endian blobs of the owner `object`, one per role (`ROLES`):

    xyz    <f8 N×3   X, Y, Z of the project: numbers within ±1e9 m, no empty values
    seq    <u4       the number of the record of the source the point was read from
    class  u1        the ASPRS class code 0–255 (0 — never classified)
    ret    u1        the return: its number in the low 4 bits, the number of returns in the high 4 (`pack_returns`)
    h      <f4       after «Фильтр земли»: the height above the ground along its normal, m; NaN — none
    dz     <f4       after a DTM was built from the cloud: Z − DTM, m; NaN — none

Row i of every column is the same point, in the storage order (`cloud_build.py`): squares of the project grid
one after another, cut into **parts** of at most `PART_POINTS` points; inside a part the points go by falling
level, so level j of a part is its first `n[j]` points — one point in every cell of `LEVELS[j]` metres.

`meta.cloud` = {format, levels, parts [{start, n [n0 … n4], bbox, origin}], bbox, classes {code: count},
source {kind, every, …}, ground, dz}; `line_layers` holds the number of points, the colour and `attr_schema`:
the attributes of a point with `store` — the role of the blob they are read from (`attr_column`).

Reading: `open_cloud()` gives a `Cloud`; `Cloud.read(role, start, count)` reads a slice from the disk,
`Cloud.memmap(role)` maps a column for the time of a job (delete the map before the job ends: on Windows a
mapped blob cannot be collected). Changing: blobs are immutable, so new values of `class`, `h`, `dz` are new
blobs — `stage_values()` outside the write transaction, `replace_values()` inside it: `objects.version + 1`,
`meta.cloud.classes / ground / dz`; `xyz`, `seq` and the levels are not touched. A cloud whose blobs are not the
ones the calculation read is not written over: `cloud_changed`.

**The contract of a source** (the readers of LAS and XYZ, the generators of tests): `CloudChunk` — a portion of
points in the order of the records of the source, or any object with the same four arrays (`Points`);
`ChunkSource` — a callable that gives the portions from the start every time it is called (the builder goes
through the source twice).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, line_layers, objects
from bf_core.storage.blobs import BlobStore

from . import store as ls

FORMAT = 1
#: The cells of the levels of a part, m: level 0 — every point. Each is 4 times the one before and a power of
#: two (`geo/cloud_kernels.level_ranks`).
LEVELS: tuple[float, ...] = (0.0, 0.5, 2.0, 8.0, 32.0)
PART_POINTS = 524_288
#: role → dtype of the columns of a cloud; `xyz` has 3 numbers a point.
ROLES: dict[str, str] = {"xyz": "<f8", "seq": "<u4", "class": "u1", "ret": "u1", "h": "<f4", "dz": "<f4"}
BASE_ROLES = ("xyz", "seq", "class", "ret")
VALUE_ROLES = ("class", "h", "dz")  # what `replace_values` writes
CLASS_COUNT = 256

Staged = tuple[Path, str, int]
Box = tuple[float, float, float, float, float, float]

#: name, role of the blob, which half of `ret` (the attributes of a point; ADR 0014 §1 «Атрибуты точки»)
_FIELDS: tuple[tuple[str, str, str | None], ...] = (
    ("Класс", "class", None),
    ("Возврат", "ret", "number"),
    ("Число возвратов", "ret", "count"),
    ("Высота над землёй, м", "h", None),
    ("Отклонение от ЦТМ, м", "dz", None),
)


# --- the contract of a source ----------------------------------------------------------------------------


@dataclass(frozen=True)
class CloudChunk:
    """A portion of the points of a source, in the order of its records.

    `xyz` — float64 n×3 (X, Y, Z of the project; a point with a coordinate that is not a number or is beyond
    ±1e9 m is not stored and is counted in `skipped`); `cls` — uint8 n, the ASPRS code (None — all 0);
    `ret` — uint8 n, `pack_returns(number, count)` (None — all 0); `seq` — uint32 n, the number of the record
    in the source (None — the running number of the point in the stream, from 0)."""

    xyz: np.ndarray
    cls: np.ndarray | None = None
    ret: np.ndarray | None = None
    seq: np.ndarray | None = None


class Points(Protocol):
    """What the builder takes from a portion of a source: a `CloudChunk`, or any object with these four arrays
    under these names (`formats.las.LasChunk`)."""

    @property
    def xyz(self) -> np.ndarray: ...
    @property
    def cls(self) -> np.ndarray | None: ...
    @property
    def ret(self) -> np.ndarray | None: ...
    @property
    def seq(self) -> np.ndarray | None: ...


#: Gives the portions of a source from its start; called twice by the builder, with the same data both times.
ChunkSource = Callable[[], Iterable[Points]]


def pack_returns(number: Any, count: Any) -> np.ndarray:
    """`ret` of points: the return number (low 4 bits) and the number of returns (high 4), each cut at 15."""
    low = np.minimum(np.asarray(number, dtype=np.int64), 15).astype(np.uint8)
    high = np.minimum(np.asarray(count, dtype=np.int64), 15).astype(np.uint8)
    return np.asarray(low | (high << np.uint8(4)), dtype=np.uint8)


def unpack_returns(ret: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The return number and the number of returns of `ret`."""
    ret = np.asarray(ret, dtype=np.uint8)
    return ret & np.uint8(15), ret >> np.uint8(4)


# --- the model -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CloudPart:
    """Points `[start, start + n[0])` of the cloud; level j is the first `n[j]` of them."""

    start: int
    n: tuple[int, ...]
    bbox: Box
    origin: tuple[float, float, float]  # whole metres: the float32 offsets of the display are counted from it

    def as_dict(self) -> dict[str, Any]:
        return {"start": self.start, "n": list(self.n), "bbox": list(self.bbox), "origin": list(self.origin)}

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> CloudPart:
        b, o = d["bbox"], d["origin"]
        return CloudPart(
            int(d["start"]),
            tuple(int(v) for v in d["n"]),
            (float(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5])),
            (float(o[0]), float(o[1]), float(o[2])),
        )


def attr_schema(roles: Iterable[str]) -> list[dict[str, Any]]:
    """`line_layers.attr_schema` of a cloud that has the blobs `roles`: a number per point, read from `store`."""
    have = set(roles)
    out: list[dict[str, Any]] = []
    for name, role, half in _FIELDS:
        if role in have:
            item: dict[str, Any] = {"name": name, "kind": "number", "unit": None, "scope": "feature", "store": role}
            if half is not None:
                item["part"] = half
            out.append(item)
    return out


def not_a_cloud() -> ApiError:
    return ApiError(
        409,
        "not_a_cloud",
        "Слой не является облаком точек.",
        "Выберите облако: слой, загруженный командой «Облако точек».",
    )


def broken() -> ApiError:
    return ApiError(
        500, "cloud_broken", "Файлы облака точек не найдены или повреждены.", "Загрузите облако точек заново."
    )


def changed() -> ApiError:
    return ApiError(
        409,
        "cloud_changed",
        "Облако точек изменилось, пока шёл расчёт.",
        "Повторите команду: расчёт выполнится по текущему облаку.",
    )


def cloud_meta(meta: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """`meta.cloud` of a layer object, None — the layer is not a cloud."""
    found = (meta or {}).get("cloud")
    return dict(found) if isinstance(found, Mapping) else None


@dataclass(frozen=True)
class Cloud:
    """A cloud opened for reading: what `meta.cloud` says and where the columns are."""

    layer_id: int
    file_id: int | None
    name: str
    version: int
    count: int
    levels: tuple[float, ...]
    parts: tuple[CloudPart, ...]
    bbox: Box
    classes: dict[int, int]
    meta: Mapping[str, Any]  # `meta.cloud` as stored: source, ground, dz, …
    shas: Mapping[str, str]  # role → SHA-256 of the columns
    paths: Mapping[str, Path] = field(repr=False, default_factory=dict)

    def has(self, role: str) -> bool:
        return role in self.paths

    def _where(self, role: str) -> tuple[Path, np.dtype[Any], int]:
        if role not in ROLES:
            raise ValueError(f"cloud: unknown column {role!r}")
        path = self.paths.get(role)
        if path is None:
            if role in BASE_ROLES:
                raise broken()
            raise ValueError(f"cloud: the cloud has no column {role!r} yet")
        return path, np.dtype(ROLES[role]), 3 if role == "xyz" else 1

    def read(self, role: str, start: int = 0, count: int | None = None) -> np.ndarray:
        """Rows `[start, start + count)` of a column, read from the disk (`xyz` — n×3)."""
        path, dtype, width = self._where(role)
        count = self.count - start if count is None else count
        if start < 0 or count < 0 or start + count > self.count:
            raise ValueError(f"cloud: rows {start}…{start + count} are out of {self.count}")
        try:
            arr = (
                np.fromfile(path, dtype=dtype, count=count * width, offset=start * width * dtype.itemsize)
                if count
                else np.empty(0, dtype=dtype)
            )
        except OSError as exc:
            raise broken() from exc
        if len(arr) != count * width:
            raise broken()
        return arr.reshape(-1, 3) if width == 3 else arr

    def memmap(self, role: str) -> np.ndarray:
        """A column mapped read-only (for the time of a job): the pages are read as they are touched."""
        path, dtype, width = self._where(role)
        try:
            return np.memmap(path, dtype=dtype, mode="r", shape=(self.count, 3) if width == 3 else (self.count,))
        except (OSError, ValueError) as exc:
            raise broken() from exc

    def part_rows(self, k: int, lod: int = 0) -> tuple[int, int]:
        """(start, count) of the rows of part k at a level."""
        part = self.parts[k]
        return part.start, part.n[lod]


def attr_column(cloud: Cloud, item: Mapping[str, Any], start: int, count: int) -> np.ndarray:
    """Rows of one attribute of `attr_schema` as float64 (NaN — empty): the page of attributes of a cloud."""
    role = str(item["store"])
    values = cloud.read(role, start, count)
    if role == "ret":
        number, total = unpack_returns(values)
        values = total if item.get("part") == "count" else number
    return np.asarray(values, dtype=np.float64)


def open_cloud(conn: sa.Connection, store: BlobStore, layer_id: int, *, ready: bool = True) -> Cloud:
    """The cloud of a layer: its `meta.cloud` and the files of its columns, their sizes checked. `ready` —
    only a layer that is loaded (what an import is still building is not seen)."""
    where = [objects.c.id == layer_id, objects.c.kind == ls.LINE_LAYER]
    if ready:
        where.append(objects.c.state == "ready")
    row = conn.execute(
        sa.select(objects.c.parent_id, objects.c.name, objects.c.version, objects.c.meta, line_layers.c.point_count)
        .join(line_layers, line_layers.c.object_id == objects.c.id)
        .where(*where)
    ).first()
    if row is None:
        raise ls._not_found("Облако точек")
    meta = cloud_meta(row.meta)
    if meta is None:
        raise not_a_cloud()
    shas = {role: sha for role, sha in ls.layer_blobs(conn, layer_id).items() if role in ROLES}
    count = int(row.point_count)
    paths: dict[str, Path] = {}
    try:
        if int(meta["format"]) != FORMAT:
            raise broken()
        for role, sha in shas.items():
            path = store.path_of(sha)
            if path.stat().st_size != count * np.dtype(ROLES[role]).itemsize * (3 if role == "xyz" else 1):
                raise broken()
            paths[role] = path
        if any(role not in paths for role in BASE_ROLES):
            raise broken()
        parts = tuple(CloudPart.from_dict(p) for p in meta["parts"])
        levels = tuple(float(v) for v in meta["levels"])
        b = meta["bbox"]
        bbox: Box = (float(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5]))
        classes = {int(code): int(n) for code, n in (meta.get("classes") or {}).items()}
        if sum(p.n[0] for p in parts) != count or any(len(p.n) != len(levels) for p in parts):
            raise broken()
    except (OSError, KeyError, TypeError, ValueError, IndexError) as exc:
        raise broken() from exc
    return Cloud(
        layer_id,
        None if row.parent_id is None else int(row.parent_id),
        str(row.name),
        int(row.version),
        count,
        levels,
        parts,
        bbox,
        classes,
        meta,
        shas,
        paths,
    )


# --- writing a cloud -------------------------------------------------------------------------------------


@dataclass
class StagedCloud:
    """The columns of a cloud in temporary files (`cloud_build.stage_cloud`), before the write transaction."""

    files: dict[str, Staged]
    count: int
    parts: list[CloudPart]
    bbox: Box
    classes: dict[int, int]
    skipped: int = 0  # points of the source without a place (a coordinate that is not a number, beyond ±1e9 m)

    def discard(self) -> None:
        for tmp, _sha, _size in self.files.values():
            tmp.unlink(missing_ok=True)
        self.files.clear()


def _origin(box: Box) -> list[float]:
    return [float(np.round((box[c] + box[3 + c]) * 0.5)) + 0.0 for c in range(3)]


def commit_cloud(
    conn: sa.Connection,
    store: BlobStore,
    staged: StagedCloud,
    *,
    file_id: int,
    name: str,
    color: str | None = None,
    source: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
    sort_key: float = 0.0,
    author: str | None = None,
) -> int:
    """Inside the write transaction: the layer object of a cloud (state, job and CRS of its line file), its
    blobs and its `line_layers` row. `source` — `meta.cloud.source` ({kind: "las" | "xyz", every, …}); `meta` —
    other keys of `objects.meta` of the layer. Returns the id of the layer — the `cloud_id`."""
    parent = ls._file_row(conn, file_id)
    extent = list(staged.bbox)
    cloud = {
        "format": FORMAT,
        "levels": list(LEVELS),
        "parts": [p.as_dict() for p in staged.parts],
        "bbox": extent,
        "classes": {str(code): int(n) for code, n in sorted(staged.classes.items())},
        "source": dict(source or {}),
        "ground": None,
        "dz": None,
    }
    values = ls._object_values(
        " ".join(str(name).split())[:200] or "Без имени",
        ls.LINE_LAYER,
        parent_id=file_id,
        state=parent.state,
        job_id=parent.job_id,
        crs=parent.crs,
        extent=extent,
        sort_key=sort_key,
        author=author,
        meta={**{k: v for k, v in (meta or {}).items() if k != "cloud"}, "cloud": cloud},
    )
    layer_id = int(conn.execute(sa.insert(objects).values(**values)).inserted_primary_key[0])  # type: ignore[index]
    for role, tmp in list(staged.files.items()):
        store.commit_staged(conn, tmp, media_type=ls.ARRAY_MEDIA, owner_kind=ls.OWNER, owner_id=layer_id, role=role)
        del staged.files[role]  # in place now: a later discard() must not touch it
    conn.execute(
        sa.insert(line_layers).values(
            object_id=layer_id,
            line_count=0,
            point_count=staged.count,
            length=0.0,
            closed_count=0,
            flat=staged.bbox[2] == staged.bbox[5],
            color=None if color is None else str(color).strip().lower() or None,
            attr_schema=attr_schema(BASE_ROLES),
            points_only=True,
            origin=_origin(staged.bbox),
        )
    )
    grown = ls._union(parent.extent, extent)
    if grown != parent.extent:
        conn.execute(sa.update(objects).where(objects.c.id == file_id).values(extent=grown))
    return layer_id


# --- new values of the points ----------------------------------------------------------------------------


@dataclass
class StagedValues:
    """New columns of a cloud in temporary files; `classes` — the points by class when `class` is among them."""

    files: dict[str, Staged]
    classes: dict[int, int] | None = None

    def discard(self) -> None:
        for tmp, _sha, _size in self.files.values():
            tmp.unlink(missing_ok=True)
        self.files.clear()


def class_counts(cls: np.ndarray) -> dict[int, int]:
    counts = np.bincount(np.asarray(cls, dtype=np.uint8), minlength=CLASS_COUNT)
    return {int(code): int(counts[code]) for code in np.flatnonzero(counts)}


def stage_values(
    store: BlobStore, cloud: Cloud, values: Mapping[str, Any], tick: Callable[[], None] | None = None
) -> StagedValues:
    """New `class` (integers 0–255), `h`, `dz` (float32, NaN — none) of every point of the cloud, in the storage
    order, as temporary files (no database access). `tick()` is called before every megabyte and may raise to
    cancel: nothing is left then."""
    staged = StagedValues({})
    try:
        for role, raw in values.items():
            if role not in VALUE_ROLES:
                raise ValueError(f"cloud: the column {role!r} is not replaced")
            arr = np.asarray(raw)
            if arr.shape != (cloud.count,):
                raise ValueError(f"cloud: {role!r} has {arr.shape} values for {cloud.count} points")
            if role == "class":
                if arr.dtype != np.uint8:
                    if arr.dtype.kind not in "iu" or (len(arr) and (int(arr.min()) < 0 or int(arr.max()) > 255)):
                        raise ValueError("cloud: a class is a whole number 0…255")
                    arr = arr.astype(np.uint8)
                staged.classes = class_counts(arr)
            staged.files[role] = store.stage_stream(ls._chunks(np.ascontiguousarray(arr, dtype=ROLES[role]), tick))
    except BaseException:
        staged.discard()
        raise
    return staged


_KEEP: Any = object()


def replace_values(
    conn: sa.Connection,
    store: BlobStore,
    cloud: Cloud,
    staged: StagedValues,
    *,
    ground: Mapping[str, Any] | None = _KEEP,
    dz: Mapping[str, Any] | None = _KEEP,
) -> int:
    """Inside the write transaction: the staged columns become the columns of the cloud, `objects.version + 1`,
    `meta.cloud.classes` follows a new `class`; `ground`, `dz` — new `meta.cloud.ground / dz` (not given — kept).
    `cloud` is the cloud the values were calculated from: when its columns are no longer the columns of the
    layer (another calculation wrote its own, the layer is gone) nothing is written — `cloud_changed`; a new
    name or colour of the layer does not count. Returns the new version."""
    row = conn.execute(
        sa.select(objects.c.version, objects.c.meta).where(
            objects.c.id == cloud.layer_id, objects.c.kind == ls.LINE_LAYER
        )
    ).first()
    meta = None if row is None else cloud_meta(row.meta)
    if row is None or meta is None:
        raise changed()
    # the version first: on PostgreSQL the row is locked from here on, a second writer waits and then fails
    done = conn.execute(
        sa.update(objects)
        .where(objects.c.id == cloud.layer_id, objects.c.version == row.version)
        .values(version=row.version + 1, updated_at=dialect.utcnow())
    ).rowcount
    current = {role: sha for role, sha in ls.layer_blobs(conn, cloud.layer_id).items() if role in ROLES}
    if done != 1 or current != dict(cloud.shas):
        raise changed()
    for role, tmp in list(staged.files.items()):
        conn.execute(
            sa.delete(blob_owners).where(
                blob_owners.c.owner_kind == ls.OWNER,
                blob_owners.c.owner_id == cloud.layer_id,
                blob_owners.c.role == role,
            )
        )
        store.commit_staged(
            conn, tmp, media_type=ls.ARRAY_MEDIA, owner_kind=ls.OWNER, owner_id=cloud.layer_id, role=role
        )
        current[role] = tmp[1]
        del staged.files[role]
    if staged.classes is not None:
        meta["classes"] = {str(code): int(n) for code, n in sorted(staged.classes.items())}
    if ground is not _KEEP:
        meta["ground"] = None if ground is None else dict(ground)
    if dz is not _KEEP:
        meta["dz"] = None if dz is None else dict(dz)
    conn.execute(sa.update(objects).where(objects.c.id == cloud.layer_id).values(meta={**row.meta, "cloud": meta}))
    conn.execute(
        sa.update(line_layers).where(line_layers.c.object_id == cloud.layer_id).values(attr_schema=attr_schema(current))
    )
    return int(row.version) + 1
