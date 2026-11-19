"""Where the geometry of a wireframe is: its own blobs or an entry of a pack (ADR 0015 §8; debt of Э8).

A wireframe has the arrays `xyz <f8 N×3`, `tri <i4 M×3` and, when its file had another order, `seq <u4 M`
(`source.py`). A big wireframe keeps them as blobs of its own (roles `xyz`, `tri`, `seq`). The small ones of a
portion of an import or of an operation (`PACK_LIMIT` bytes of arrays at most) share one blob — a pack of the
format of the line layers (`lines/pack.py`, `BFLP`): a wireframe refers to it by the role `pack:<n>`, n — its
entry, whose files are the same arrays byte for byte. Thousands of small wireframes cost a blob a portion, not
three blobs and twelve rows each (3 000 boxes of a DXF: 19 s → see `bench/scenarios/wireframes_many.py`).

The geometry of a wireframe — what its levels of detail are keyed by (`viz.meshes.cache_key`) and what tells
two wireframes of the same geometry — is the pair of roles `xyz`, `tri` or its one `pack:<n>` with their SHA-256
(`geometry`). Every reader goes through `wireframe_files`: the path, the offset and the size of every array.

    blob_roles(conn, id), blob_shas     every blob of its arrays {role: sha256}; those of its geometry
    geometry_shared(conn, shas)         an object of the project still has this geometry (its levels stay)
    geometry(roles)                     {role: sha256} of the geometry among the blobs of a wireframe
    complete(roles)                     the blobs of a geometry are all there (`xyz` and `tri`, or a pack)
    wireframe_files(store, roles)       WireframeFiles: array(role), mapped(role), has(role)
    stage_files(store, staged)          the temp files of wireframes staged without them: packs and own blobs
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import blob_owners
from bf_core.lines import pack

ARRAYS: dict[str, str] = {"xyz": "<f8", "tri": "<i4", "seq": "<u4"}
GEOMETRY = ("xyz", "tri")
#: A wireframe whose arrays take at most this many bytes goes into the pack of its portion (`store.stage_files`).
PACK_LIMIT = 1 << 20
PACK_BYTES = 16 << 20  # a pack is closed when it holds this much: the next small wireframe starts another


def broken() -> ApiError:
    return ApiError(500, "wireframe_broken", "Файлы геометрии каркаса не найдены.", "Загрузите каркас заново.")


def is_pack(role: str) -> bool:
    return pack.entry_of(role) is not None


def owned(role: str) -> bool:
    """A role of the blobs of a wireframe that hold its geometry: an array or an entry of a pack."""
    return role in ARRAYS or is_pack(role)


def geometry(roles: Mapping[str, str]) -> dict[str, str]:
    """The blobs of the geometry {role: sha256} among those of a wireframe (`seq` of its own is no part of it)."""
    return {r: s for r, s in roles.items() if r in GEOMETRY or is_pack(r)}


def complete(roles: Mapping[str, str]) -> bool:
    """The geometry is `xyz` and `tri`, or one entry of a pack, and nothing of the other kind."""
    found = geometry(roles)
    packs = [r for r in found if is_pack(r)]
    return set(found) == set(GEOMETRY) if not packs else len(found) == 1


@dataclass(frozen=True)
class WireframeFiles:
    """Where the arrays of a wireframe are: role → (blob, offset, size); size None — the blob is the array."""

    parts: Mapping[str, tuple[Path, int, int | None]]

    def has(self, role: str) -> bool:
        return role in self.parts

    def count(self, role: str) -> int:
        """Numbers in the array (not rows); a missing file — `broken`."""
        path, _offset, size = self._part(role)
        if size is None:
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise broken() from exc
        item = np.dtype(ARRAYS[role]).itemsize
        if size % item:
            raise broken()
        return size // item

    def _part(self, role: str) -> tuple[Path, int, int | None]:
        part = self.parts.get(role)
        if part is None:
            raise broken()
        return part

    def array(self, role: str, *, first: int = 0, count: int | None = None) -> np.ndarray | None:
        """The numbers [first, first + count) of an array, read (None — the wireframe has no such array): all of
        it by default."""
        if role not in self.parts:
            return None
        path, offset, _size = self.parts[role]
        dtype = np.dtype(ARRAYS[role])
        total = self.count(role)
        n = total - first if count is None else count
        if first < 0 or n < 0 or first + n > total:
            raise broken()
        try:
            arr = np.fromfile(path, dtype=dtype, count=n, offset=offset + first * dtype.itemsize) if n else None
        except OSError as exc:
            raise broken() from exc
        if arr is None:
            return np.empty(0, dtype=dtype)
        if len(arr) != n:
            raise broken()
        return arr

    def mapped(self, role: str) -> np.ndarray:
        """The whole array mapped from its blob (read-only): only the pages a caller touches are read. The
        caller drops its references before the blob may go (Windows keeps a mapped file locked)."""
        path, offset, _size = self._part(role)
        n = self.count(role)
        if n == 0:
            return np.empty(0, dtype=ARRAYS[role])
        try:
            return np.memmap(path, dtype=ARRAYS[role], mode="r", offset=offset, shape=(n,))
        except (OSError, ValueError) as exc:
            raise broken() from exc


def wireframe_files(store: Any, roles: Mapping[str, str]) -> WireframeFiles:
    """The arrays of a wireframe from its blobs {role: sha256} (`store.blob_roles`): its own, or an entry of a
    pack. A blob that is not there, or a damaged pack — `broken`."""
    parts: dict[str, tuple[Path, int, int | None]] = {}
    for role, sha in roles.items():
        entry = pack.entry_of(role)
        if entry is None:
            if role in ARRAYS:
                parts[role] = (store.path_of(sha), 0, None)
            continue
        path = store.path_of(sha)
        try:
            slices = pack.directory(path)[entry]
        except (OSError, pack.PackError, IndexError) as exc:
            raise broken() from exc
        for name, (offset, size) in slices.items():
            if name in ARRAYS:
                parts[name] = (path, offset, size)
    if not all(r in parts for r in GEOMETRY):
        raise broken()
    return WireframeFiles(parts)


# --- the blobs of a wireframe ----------------------------------------------------------------------------

_OWNER = "object"


def blob_roles(conn: sa.Connection, wf_id: int) -> dict[str, str]:
    """Every blob of the arrays of a wireframe {role: sha256}: `xyz`, `tri`, `seq` or `pack:<n>`."""
    rows = conn.execute(
        sa.select(blob_owners.c.role, blob_owners.c.sha256).where(
            blob_owners.c.owner_kind == _OWNER, blob_owners.c.owner_id == wf_id
        )
    ).all()
    return {str(r.role): str(r.sha256) for r in rows if owned(str(r.role))}


def blob_shas(conn: sa.Connection, wf_id: int) -> dict[str, str]:
    """The blobs of the geometry {role: sha256}: `xyz` and `tri`, or its entry of a pack."""
    return geometry(blob_roles(conn, wf_id))


def geometry_shared(conn: sa.Connection, shas: Mapping[str, str]) -> bool:
    """An object of the project owns the geometry of these blobs {role: sha256}: its derived files in
    `cache\\mesh\\` (the levels of detail, keyed by the blobs) are in use and must stay. Of a pack, only its
    entry: the other wireframes of the pack have other geometries."""
    if not shas:
        return False
    same = [(blob_owners.c.sha256 == sha) & (blob_owners.c.role == role) for role, sha in shas.items() if is_pack(role)]
    own = [s for r, s in shas.items() if not is_pack(r)]
    if own:
        same.append(blob_owners.c.sha256.in_(own) & blob_owners.c.role.in_(list(GEOMETRY)))
    query = sa.select(sa.func.count()).select_from(blob_owners).where(sa.or_(*same))
    return bool(conn.execute(query).scalar_one() > 0)


# --- writing ---------------------------------------------------------------------------------------------

Staged = tuple[Path, str, int]


@dataclass
class StagedPack:
    """A pack in its temporary file, shared by the staged wireframes that are its entries."""

    file: Staged
    sha: str | None = None  # set once the file is in place and has its `blobs` row (in the commit transaction)

    def discard(self) -> None:
        self.file[0].unlink(missing_ok=True)


def chunks(arr: np.ndarray, size: int = 1 << 20) -> Iterator[bytes]:
    raw = memoryview(np.ascontiguousarray(arr)).cast("B")
    for start in range(0, len(raw), size):
        yield bytes(raw[start : start + size])


def array_bytes(points: int, triangles: int) -> int:
    """The bytes of the arrays of a wireframe at most: `xyz`, `tri` and a `seq`."""
    return 24 * int(points) + 16 * int(triangles)


def arrays_of(staged: Any) -> dict[str, np.ndarray]:
    """The arrays a staged wireframe stores: `xyz`, `tri` and its `seq` when the order of its file is kept."""
    out = {"xyz": np.ascontiguousarray(staged.xyz, dtype=ARRAYS["xyz"]),
           "tri": np.ascontiguousarray(staged.tri, dtype=ARRAYS["tri"])}  # fmt: skip
    if staged.keep_seq and staged.seq is not None:
        out["seq"] = np.ascontiguousarray(staged.seq, dtype=ARRAYS["seq"])
    return out


def stage_files(
    store: Any,
    staged: Sequence[Any],
    tick: Callable[[int], None] | None = None,
    *,
    pack_limit: int = PACK_LIMIT,
) -> None:
    """The temp files of wireframes staged without them (`store.stage_wireframe(write=False)`; no database
    access): a wireframe of at most `pack_limit` bytes of arrays becomes an entry of a pack shared with the other
    small ones of the call (`pack:<n>` in its `shas`), a bigger one gets its own blobs. `tick(k)` is called
    before wireframe k and may raise to cancel: no temp file of the call is left then."""
    from bf_core.lines import pack as packs

    small: list[tuple[Any, dict[str, bytes]]] = []
    held = 0

    def close_pack() -> None:
        nonlocal held
        if small:
            shared = StagedPack(store.stage_bytes(packs.encode([files for _one, files in small])))
            for entry, (one, _files) in enumerate(small):
                one.pack, one.entry = shared, entry
                one.shas = {packs.role(entry): shared.file[1]}
            small.clear()
        held = 0

    try:
        for k, one in enumerate(staged):
            if tick is not None:
                tick(k)
            if one.files or one.pack is not None:
                continue  # staged with its files already
            arrays = arrays_of(one)
            size = sum(int(a.nbytes) for a in arrays.values())
            if size <= pack_limit:
                if held and held + size > PACK_BYTES:
                    close_pack()
                small.append((one, {role: a.tobytes() for role, a in arrays.items()}))
                held += size
                continue
            for role, arr in arrays.items():
                one.files[role] = store.stage_stream(chunks(arr))
            one.shas = {role: one.files[role][1] for role in GEOMETRY}
        close_pack()
    except BaseException:
        for one in staged:
            one.discard()
        raise
