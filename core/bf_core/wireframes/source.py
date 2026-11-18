"""The source of an imported wireframe: the order of its file and the rows of its file (ADR 0013 §3).

The store keeps the triangles of a wireframe along a Z-order curve (ADR 0004: the parts of the 3D view are runs
of consecutive triangles). The order of the file is the optional blob `seq <u4 M` of the owner `object`:
`seq[k]` — the number in the file of the stored triangle k, so `file[seq] = stored`. A wireframe without the
blob has the order of storage (built in the project, or its file was in the spatial order already). The blob
is not among `StagedWireframe.shas`: the key of the levels of detail does not depend on it.

`objects.meta.source` is `MeshSet.source` — {format, name as the file has it, …, the rows a writer needs to
give the same file back}. Its `as` is the name that stands for the name of the file: the name the object got
at import when the name of the mesh was taken in the project. While the object is still called that, a writer
writes the name of the file (`meshset.name_in_file`); a renamed object is written under its new name.

    stage_mesh(store, mesh)            outside the write transaction: `store.stage_wireframe` and `seq`
    commit_mesh(conn, store, staged, mesh, …)   inside it: the object with colour, attributes, `meta.source`
    read_mesh(conn, store, wf_id)      the wireframe as a writer takes it: the triangles in the order of the file
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.db.schema import blob_owners, objects
from bf_core.formats.meshset import MeshSet
from bf_core.storage.blobs import CHUNK, BlobStore
from bf_core.wireframes import store as ws

SEQ = "seq"  # the role of the blob
SEQ_DTYPE = "<u4"


def _chunks(arr: np.ndarray) -> Iterator[bytes]:
    raw = memoryview(np.ascontiguousarray(arr, dtype=SEQ_DTYPE)).cast("B")
    for start in range(0, len(raw), CHUNK):
        yield bytes(raw[start : start + CHUNK])


def in_order(seq: np.ndarray) -> bool:
    """The permutation changes nothing: the stored triangles are in the order of the file."""
    return len(seq) == 0 or (int(seq[0]) == 0 and bool(np.all(seq[1:] > seq[:-1])))


def stage_mesh(store: BlobStore, mesh: MeshSet, *, progress: ws.Progress = None) -> ws.StagedWireframe:
    """`store.stage_wireframe` of the geometry of a mesh and the temp file of its `seq` (no database access).
    `progress(fraction, message)` may raise to cancel: no temp file stays."""
    staged = ws.stage_wireframe(store, mesh.xyz, mesh.tri, progress=progress)
    try:
        if staged.seq is not None and not in_order(staged.seq):
            staged.files[SEQ] = store.stage_stream(_chunks(staged.seq))
    except BaseException:
        staged.discard()
        raise
    return staged


def source_meta(mesh: MeshSet, given: str) -> dict[str, Any]:
    """`objects.meta.source` of a mesh stored under the name `given`."""
    out = dict(mesh.source)
    if given != mesh.name:
        out["as"] = given
    return out


def commit_mesh(
    conn: sa.Connection,
    store: BlobStore,
    staged: ws.StagedWireframe,
    mesh: MeshSet,
    *,
    name: str | None = None,
    meta: Mapping[str, Any] | None = None,
    **kw: Any,
) -> int:
    """Inside the write transaction: the wireframe of a staged mesh with its colour, attributes and
    `meta.source`. `name` — the name of the object (the caller made it free); None — the name of the mesh, or
    «name (2)» when it is taken. The rest goes to `store.commit_wireframe` (folder, job_id, state, …)."""
    given = name if name is not None else ws.free_name(conn, mesh.name)
    info = dict(meta or {})
    source = source_meta(mesh, given)
    if source:
        info["source"] = source
    return ws.commit_wireframe(conn, store, staged, name=given, color=mesh.color, attrs=mesh.attrs, meta=info, **kw)


# --- reading ---------------------------------------------------------------------------------------------


def read_seq(conn: sa.Connection, store: BlobStore, wf_id: int) -> np.ndarray | None:
    """`seq` of a wireframe (uint32 per stored triangle), or None when it has the order of storage."""
    sha = conn.execute(
        sa.select(blob_owners.c.sha256).where(
            blob_owners.c.owner_kind == ws.OWNER, blob_owners.c.owner_id == wf_id, blob_owners.c.role == SEQ
        )
    ).scalar()
    if sha is None:
        return None
    path = store.path_of(str(sha))
    if not path.is_file():
        raise ws.broken()
    return np.fromfile(path, dtype=SEQ_DTYPE)


def file_order(tri: np.ndarray, seq: np.ndarray | None) -> np.ndarray:
    """The stored triangles in the order of their file (`seq` None — as they are). A `seq` that is not a
    permutation of the triangles is a broken wireframe, never a wrong file."""
    if seq is None:
        return tri
    m = len(tri)
    if len(seq) != m or (m and int(seq.max()) >= m):
        raise ws.broken()
    seen = np.zeros(m, dtype=bool)
    seen[seq] = True
    if not seen.all():
        raise ws.broken()
    out = np.empty_like(tri)
    out[seq] = tri
    return out


def read_mesh(conn: sa.Connection, store: BlobStore, wf_id: int) -> MeshSet:
    """A wireframe of the project as a writer takes it: vertices bit for bit, triangles in the order of its
    file, the name, colour and attributes it has now, its measures and `source`."""
    info = ws.wireframe_info(conn, wf_id)
    xyz, tri = ws.read_wireframe(conn, store, wf_id)
    tri = file_order(tri, read_seq(conn, store, wf_id))
    meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == wf_id)).scalar() or {}
    return MeshSet(
        str(info["name"]),
        xyz,
        tri,
        color=info["color"],
        attrs=info["attrs"],
        source=dict(meta.get("source") or {}),
        stats=dict(info["stats"]) or None,
    )
